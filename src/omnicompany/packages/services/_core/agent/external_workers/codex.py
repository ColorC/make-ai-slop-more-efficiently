# [OMNI] origin=codex domain=services/agent ts=2026-05-09 type=infrastructure
"""Codex CLI external worker adapter."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator

from omnicompany.packages.services._core.agent.external_workers.base import (
    ExternalAgentEvent,
    ExternalAgentPermissionMode,
    ExternalAgentResult,
    ExternalAgentRunSpec,
    ExternalAgentStatus,
    ExternalAgentWorker,
    _headless_process_kwargs,
)
from omnicompany.packages.services._core.agent.external_workers.trace import (
    _summarize_args,
    _summarize_result,
)


_CODEX_SANDBOX_BY_PERMISSION: dict[ExternalAgentPermissionMode, str] = {
    ExternalAgentPermissionMode.READONLY: "read-only",
    ExternalAgentPermissionMode.WORKSPACE_WRITE: "workspace-write",
    ExternalAgentPermissionMode.TRUSTED_BYPASS: "danger-full-access",
}

_THE_COMPANY_CODEX_CONFIG: dict[str, str] = {
    "model_provider": json.dumps("the_company"),
    "model_providers.the_company.name": json.dumps("the_company proxy"),
    "model_providers.the_company.base_url": json.dumps(
        "https://internal-llm-proxy.example.com/v1"
    ),
    "model_providers.the_company.env_key": json.dumps("THE_COMPANY_API_KEY"),
    "model_providers.the_company.wire_api": json.dumps("responses"),
    "model_providers.the_company.supports_websockets": "false",
}

_STREAM_READ_CHUNK_SIZE = 64 * 1024
_POST_EXIT_STREAM_DRAIN_TIMEOUT_S = 1.0
_STREAM_CLEANUP_TIMEOUT_S = 1.0
_PROCESS_WAIT_TIMEOUT_S = 5.0


class CodexExecWorker(ExternalAgentWorker):
    """Run Codex through `codex exec --json`.

    This is intentionally an external-agent path, not an LLMClient provider.
    """

    provider_name = "codex"
    handles_timeout = True

    def __init__(
        self,
        *,
        bus: Any | None = None,
        codex_executable: str = "codex",
        provider_name: str | None = None,
        codex_home: str | None = None,
    ):
        super().__init__(bus=bus)
        self.codex_executable = codex_executable
        if provider_name:
            self.provider_name = provider_name
        # Codex has no account switch of its own: "another account" IS another
        # CODEX_HOME (authority: dashboard/ccdaemon/provider_identity.codex_home_for,
        # not importable from this layer). codex-2 runs the same CLI against the
        # second official account's home so its quota, rollouts and auth stay
        # separate from the primary account.
        self.codex_home = codex_home

    def build_command(
        self,
        spec: ExternalAgentRunSpec,
        *,
        last_message_path: Path,
        runtime_cwd: Path | None = None,
        output_schema_path: Path | None = None,
    ) -> list[str]:
        permission_mode = spec.normalized_permission_mode()
        cmd = [
            _resolve_executable_for_subprocess(self.codex_executable),
            "exec",
            # Controlled workers have their own audit envelope. Project/user
            # lifecycle hooks would double-inject context and register this
            # ephemeral subprocess as an interactive native session.
            "--disable",
            "hooks",
            "--ephemeral",
            "--json",
            "--cd",
            str(runtime_cwd or spec.normalized_cwd()),
            "--sandbox",
            _CODEX_SANDBOX_BY_PERMISSION[permission_mode],
            "--output-last-message",
            str(last_message_path),
        ]
        if _metadata_flag(spec.metadata, "minimal_context"):
            cmd.extend(
                [
                    "--ignore-user-config",
                    "--ignore-rules",
                    "--skip-git-repo-check",
                    "--disable",
                    "memories",
                    "--disable",
                    "plugins",
                    "--disable",
                    "apps",
                    "--disable",
                    "multi_agent",
                ]
            )
        if spec.model:
            cmd.extend(["--model", spec.model])
        if spec.profile:
            cmd.extend(["--profile", spec.profile])
        image_paths = spec.normalized_image_paths()
        if image_paths:
            cmd.extend(["--image", *(str(path) for path in image_paths)])
        schema_path = output_schema_path or spec.output_schema_path
        if schema_path:
            cmd.extend(["--output-schema", str(Path(schema_path).expanduser().resolve())])
        # model_provider 是统一 ExternalAgentRunSpec 的公开字段；the_company 通过
        # 进程局部 Codex 配置接入，不能要求 CLI 调用者另造嵌套 metadata。
        # 显式 codex_config 仍可追加/覆盖(如 research 开 tools.web_search)。
        codex_config = _codex_config_overrides(spec)
        for key, value in codex_config.items():
            cmd.extend(["-c", f"{key}={value}"])
        # ``codex exec`` is non-interactive. With the current CLI, an omitted
        # approval policy defaults workspace-write tool calls to approval-
        # required and then silently rejects them because no approver exists.
        # The sandbox remains the authority boundary; ``never`` only prevents a
        # valid in-sandbox write from deadlocking or being misreported as done.
        cmd.extend(["-c", 'approval_policy="never"'])
        cmd.append("-")
        return cmd

    async def _run_impl(self, spec: ExternalAgentRunSpec) -> ExternalAgentResult:
        cwd = spec.normalized_cwd()
        # The parent worker, not Codex, writes this audit file. Create it before
        # the readonly baseline so its live appends are not misclassified as a
        # child permission violation. Callers should point it at an untracked
        # run artifact, never a tracked source file.
        event_log_path = _resolve_event_log_path(spec)
        if event_log_path is not None:
            event_log_path.parent.mkdir(parents=True, exist_ok=True)
            event_log_path.touch(exist_ok=True)
        before_diff = _git_diff_stat(cwd)
        before_changed = set(_git_changed_files(cwd))
        watch_paths = spec.normalized_watch_paths()
        before_watch = _snapshot_watch_paths(cwd, watch_paths)
        started = time.monotonic()
        with _codex_runtime_cwd(cwd) as runtime_cwd, tempfile.TemporaryDirectory(prefix="omni-codex-") as tmp:
            last_message_path = Path(tmp) / "last_message.md"
            output_schema_path = None
            if spec.output_schema_path:
                try:
                    output_schema_path = _materialize_codex_output_schema(
                        Path(spec.output_schema_path),
                        Path(tmp) / "output-schema.strict.json",
                    )
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    return ExternalAgentResult(
                        run_id=spec.run_id,
                        provider=self.provider_name,
                        status=ExternalAgentStatus.FAILED,
                        error=f"invalid Codex output schema: {exc}",
                    )
            if output_schema_path is None:
                cmd = self.build_command(
                    spec,
                    last_message_path=last_message_path,
                    runtime_cwd=runtime_cwd,
                )
            else:
                cmd = self.build_command(
                    spec,
                    last_message_path=last_message_path,
                    runtime_cwd=runtime_cwd,
                    output_schema_path=output_schema_path,
                )
            events: list[ExternalAgentEvent] = [
                ExternalAgentEvent(
                    type="command",
                    message="codex exec command built",
                    payload={"argv": _redact_argv(cmd)},
                )
            ]
            env = _build_env(spec.env)
            if self.codex_home:
                env["CODEX_HOME"] = self.codex_home

            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=str(runtime_cwd),
                    env=env,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    **_headless_process_kwargs(),
                )
            except FileNotFoundError:
                return ExternalAgentResult(
                    run_id=spec.run_id,
                    provider=self.provider_name,
                    status=ExternalAgentStatus.FAILED,
                    events=events,
                    error=f"codex executable not found: {self.codex_executable}",
                )

            stdout, stderr, timed_out, timeout_reason, saw_error = await self._communicate_streaming(
                spec,
                proc,
                events,
                started=started,
            )

            final_text = ""
            if last_message_path.exists():
                final_text = last_message_path.read_text(encoding="utf-8", errors="replace")
            if not final_text:
                final_text = _last_text_from_events(events) or stdout[-4000:]
            structured_output = _parse_structured_final_text(final_text)

            after_diff = _git_diff_stat(cwd)
            after_changed = set(_git_changed_files(cwd))
            after_watch = _snapshot_watch_paths(cwd, watch_paths)
            watched_path_changes = _diff_watch_snapshots(before_watch, after_watch)
            if timed_out:
                status = ExternalAgentStatus.TIMED_OUT
            elif proc.returncode == 0 and not saw_error:
                status = ExternalAgentStatus.SUCCEEDED
            else:
                status = ExternalAgentStatus.FAILED
            changed_files = sorted(after_changed - before_changed)
            diff_summary = _format_diff_summary(
                after_diff if after_diff != before_diff else "",
                changed_files,
            )
            diff_summary = _append_watched_path_summary(diff_summary, watched_path_changes)
            completion_contract_error = ""
            if status == ExternalAgentStatus.SUCCEEDED:
                completion_contract_error = _completion_contract_error(
                    spec,
                    cwd=cwd,
                    watched_path_changes=watched_path_changes,
                )
                if completion_contract_error:
                    status = ExternalAgentStatus.FAILED
            readonly_rollback: dict[str, list[str]] | None = None
            if (
                spec.normalized_permission_mode() == ExternalAgentPermissionMode.READONLY
                and (
                    after_diff != before_diff
                    or changed_files
                    or watched_path_changes["has_changes"]
                )
            ):
                rollback_paths = sorted(
                    set(changed_files + list(watched_path_changes["created"])),
                    key=lambda path: path.count("/"),
                    reverse=True,
                )
                readonly_rollback = _rollback_new_changes(cwd, rollback_paths)
                diff_summary = _append_rollback_summary(diff_summary, readonly_rollback)
                # Codex already runs inside its preventive read-only sandbox.
                # A repository-wide time-window delta can come from any of the
                # other interactive agents sharing this worktree, so it remains
                # an unattributed audit warning. Explicit watch-path changes are
                # scoped closely enough to fail the run conservatively.
                if watched_path_changes["has_changes"]:
                    status = ExternalAgentStatus.PERMISSION_VIOLATION

            error = ""
            if status == ExternalAgentStatus.TIMED_OUT:
                error = f"codex timed out: {timeout_reason}"
            elif status == ExternalAgentStatus.PERMISSION_VIOLATION:
                error = (
                    "readonly workspace changed during worker window; ownership is unproven "
                    "and detected changes were preserved"
                )
                if readonly_rollback and readonly_rollback["failed"]:
                    error += "; rollback failed for " + ", ".join(readonly_rollback["failed"])
            elif completion_contract_error:
                error = completion_contract_error
            elif status != ExternalAgentStatus.SUCCEEDED:
                error = stderr[-4000:]

            return ExternalAgentResult(
                run_id=spec.run_id,
                provider=self.provider_name,
                status=status,
                final_text=final_text,
                structured_output=structured_output,
                events=events,
                exit_code=proc.returncode,
                changed_files=changed_files,
                diff_summary=diff_summary,
                error=error,
                raw={
                    "stdout_tail": stdout[-4000:],
                    "stderr_tail": stderr[-4000:],
                    "preexisting_changed_files_count": len(before_changed),
                    "after_changed_files_count": len(after_changed),
                    "after_rollback_changed_files_count": len(_git_changed_files(cwd)),
                    "watch_paths": _relative_watch_paths(cwd, watch_paths),
                    "watched_path_changes": watched_path_changes,
                    "readonly_rollback": readonly_rollback,
                    "completion_contract_error": completion_contract_error,
                    "unattributed_workspace_changes": bool(
                        spec.normalized_permission_mode()
                        == ExternalAgentPermissionMode.READONLY
                        and (after_diff != before_diff or changed_files)
                    ),
                    "runtime_cwd": str(runtime_cwd),
                    "timeout_policy": {
                        "total_timeout_s": spec.timeout_s,
                        "idle_timeout_s": _optional_positive_float(
                            (spec.metadata or {}).get("idle_timeout_s")
                        ),
                        "default_total_timeout": None,
                    },
                },
            )

    async def _communicate_streaming(
        self,
        spec: ExternalAgentRunSpec,
        proc: asyncio.subprocess.Process,
        events: list[ExternalAgentEvent],
        *,
        started: float,
    ) -> tuple[str, str, bool, str, bool]:
        """Consume Codex JSONL as it arrives and publish pure materials."""

        assert proc.stdin is not None
        proc.stdin.write(spec.full_prompt().encode("utf-8"))
        startup_timeout = _optional_positive_float(
            (spec.metadata or {}).get("startup_timeout_s")
        ) or 30.0
        try:
            await asyncio.wait_for(proc.stdin.drain(), timeout=startup_timeout)
        except asyncio.TimeoutError:
            proc.stdin.close()
            await _terminate_process_tree(proc)
            return (
                "",
                "",
                True,
                f"Agent stdin startup stalled for {startup_timeout:g}s",
                False,
            )
        proc.stdin.close()

        queue: asyncio.Queue[tuple[str, str, object | None]] = asyncio.Queue()

        async def pump(name: str, stream: asyncio.StreamReader | None) -> None:
            buffer = bytearray()
            try:
                if stream is None:
                    return
                while True:
                    # readline() enforces StreamReader's 64 KiB transport
                    # limit. Chunk reads let valid JSONL records be larger.
                    chunk = await stream.read(_STREAM_READ_CHUNK_SIZE)
                    if not chunk:
                        break
                    buffer.extend(chunk)
                    while True:
                        newline = buffer.find(b"\n")
                        if newline < 0:
                            break
                        line = bytes(buffer[: newline + 1])
                        del buffer[: newline + 1]
                        queue.put_nowait(("line", name, line))
                if buffer:
                    # A final JSONL record may omit its newline.
                    queue.put_nowait(("line", name, bytes(buffer)))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if buffer:
                    # Preserve a valid trailing record even when the next
                    # transport read fails before EOF.
                    queue.put_nowait(("line", name, bytes(buffer)))
                # Keep reader failures in-band; otherwise a dead pump looks
                # like a process timeout to the coordinator.
                queue.put_nowait(("error", name, exc))
            finally:
                # Exactly one EOF notification per pump, including failures.
                queue.put_nowait(("eof", name, None))

        async def watch_process() -> None:
            try:
                returncode = await proc.wait()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                queue.put_nowait(("process_error", "process", exc))
            else:
                queue.put_nowait(("process_exit", "process", returncode))

        pumps = [
            asyncio.create_task(pump("stdout", proc.stdout)),
            asyncio.create_task(pump("stderr", proc.stderr)),
        ]
        process_waiter = asyncio.create_task(watch_process())
        stream_names = {"stdout", "stderr"}
        eof_streams: set[str] = set()
        process_exited = proc.returncode is not None
        post_exit_deadline = (
            time.monotonic()
            + (
                _optional_positive_float(
                    (spec.metadata or {}).get("post_exit_drain_timeout_s")
                )
                or _POST_EXIT_STREAM_DRAIN_TIMEOUT_S
            )
            if process_exited
            else None
        )
        post_exit_drain_timeout = (
            _optional_positive_float(
                (spec.metadata or {}).get("post_exit_drain_timeout_s")
            )
            or _POST_EXIT_STREAM_DRAIN_TIMEOUT_S
        )
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        tool_parent_by_id: dict[str, str] = {}
        idle_timeout = _optional_positive_float((spec.metadata or {}).get("idle_timeout_s"))
        total_deadline = started + spec.timeout_s if spec.timeout_s is not None else None
        last_meaningful_progress = started
        diagnostic_counts: dict[str, int] = {}
        timed_out = False
        timeout_reason = ""
        saw_error = False
        event_log_path = _resolve_event_log_path(spec)
        event_log = None
        if event_log_path is not None:
            event_log_path.parent.mkdir(parents=True, exist_ok=True)
            event_log = event_log_path.open("a", encoding="utf-8")

        def persist_event(event: ExternalAgentEvent) -> None:
            if event_log is None:
                return
            message = event.message
            payload = event.payload
            if _metadata_flag(spec.metadata, "event_log_redact_tool_output"):
                message, payload = _redacted_event_log_content(event)
            event_log.write(json.dumps({
                "ts": event.ts,
                "type": event.type,
                "message": message,
                "payload": payload,
            }, ensure_ascii=False, default=str) + "\n")
            event_log.flush()

        def record_runtime_error(source: str, exc: object) -> None:
            nonlocal saw_error
            saw_error = True
            message = f"{source} error: {type(exc).__name__}: {exc}"
            stderr_lines.append(message)
            event = ExternalAgentEvent(
                type="stream.error",
                message=message,
                payload={"source": source, "error_type": type(exc).__name__},
            )
            events.append(event)
            persist_event(event)

        async def cancel_tasks_bounded(tasks: list[asyncio.Task[object]]) -> None:
            for task in tasks:
                if not task.done():
                    task.cancel()
            done, pending = await asyncio.wait(
                tasks,
                timeout=_STREAM_CLEANUP_TIMEOUT_S,
            )
            for task in done:
                if task.cancelled():
                    continue
                try:
                    task.result()
                except Exception as exc:
                    record_runtime_error("stream task", exc)
            # Do not wait indefinitely for an uncooperative reader after the
            # bounded cleanup window has elapsed.
            for task in pending:
                task.cancel()

        try:
            while eof_streams != stream_names:
                if not process_exited and proc.returncode is not None:
                    process_exited = True
                    post_exit_deadline = (
                        time.monotonic() + post_exit_drain_timeout
                    )
                if process_exited and post_exit_deadline is None:
                    post_exit_deadline = (
                        time.monotonic() + post_exit_drain_timeout
                    )

                if process_exited:
                    remaining = post_exit_deadline - time.monotonic()
                    if remaining <= 0:
                        record_runtime_error(
                            "process exit stream drain",
                            TimeoutError(
                                "stdout/stderr did not reach EOF after process exit"
                            ),
                        )
                        break
                    candidates = [remaining]
                else:
                    remaining_total = (
                        total_deadline - time.monotonic()
                        if total_deadline is not None
                        else None
                    )
                    if remaining_total is not None and remaining_total <= 0:
                        timed_out = True
                        timeout_reason = f"total deadline reached after {spec.timeout_s:g}s"
                        await _terminate_process_tree(proc)
                        break
                    remaining_idle = (
                        idle_timeout - (time.monotonic() - last_meaningful_progress)
                        if idle_timeout is not None
                        else None
                    )
                    if remaining_idle is not None and remaining_idle <= 0:
                        timed_out = True
                        timeout_reason = f"no meaningful Agent output for {idle_timeout:g}s"
                        await _terminate_process_tree(proc)
                        break
                    candidates = [
                        value
                        for value in (remaining_idle, remaining_total)
                        if value is not None
                    ]
                try:
                    stream_kind, stream_name, raw_value = await asyncio.wait_for(
                        queue.get(), timeout=min(candidates) if candidates else None
                    )
                except asyncio.TimeoutError:
                    if not process_exited and proc.returncode is not None:
                        # The process may have exited after the pre-wait
                        # check, while its waiter notification is still
                        # pending.  Reclassify this boundary as transport
                        # draining, not a model deadline.
                        process_exited = True
                        post_exit_deadline = (
                            time.monotonic() + post_exit_drain_timeout
                        )
                        continue
                    if process_exited:
                        record_runtime_error(
                            "process exit stream drain",
                            TimeoutError(
                                "stdout/stderr did not reach EOF after process exit"
                            ),
                        )
                    else:
                        timed_out = True
                        now = time.monotonic()
                        if total_deadline is not None and (
                            idle_timeout is None or now >= total_deadline
                        ):
                            timeout_reason = f"total deadline reached after {spec.timeout_s:g}s"
                        elif idle_timeout is not None:
                            timeout_reason = (
                                f"no meaningful Agent output for {idle_timeout:g}s"
                            )
                        else:
                            timeout_reason = "stream wait deadline reached"
                        await _terminate_process_tree(proc)
                    break

                if stream_kind == "eof":
                    if stream_name in stream_names:
                        eof_streams.add(stream_name)
                    continue
                if stream_kind == "process_exit":
                    process_exited = True
                    post_exit_deadline = (
                        time.monotonic() + post_exit_drain_timeout
                    )
                    continue
                if stream_kind == "process_error":
                    record_runtime_error("process wait", raw_value)
                    process_exited = True
                    post_exit_deadline = (
                        time.monotonic() + post_exit_drain_timeout
                    )
                    continue
                if stream_kind == "error":
                    record_runtime_error(f"{stream_name} reader", raw_value)
                    continue
                if stream_kind != "line":
                    record_runtime_error(
                        "stream coordinator",
                        RuntimeError(f"unknown stream message kind: {stream_kind}"),
                    )
                    continue
                if not isinstance(raw_value, bytes):
                    record_runtime_error(
                        f"{stream_name} reader",
                        TypeError("stream pump returned non-bytes data"),
                    )
                    continue
                line = raw_value.decode("utf-8", errors="replace").rstrip("\r\n")
                if stream_name == "stderr":
                    if line.strip():
                        diagnostic_key = _diagnostic_key(line)
                        repeat_count = diagnostic_counts.get(diagnostic_key, 0) + 1
                        diagnostic_counts[diagnostic_key] = repeat_count
                        # Codex can emit thousands of timestamped copies of the
                        # same queue warning while otherwise making no progress.
                        # Keep a logarithmic audit trail without letting those
                        # diagnostics defeat the idle timeout or flood memory.
                        if repeat_count not in {1, 10, 100, 1000, 10_000}:
                            continue
                        diagnostic_line = line[-4000:]
                        if repeat_count > 1:
                            diagnostic_line += f" (同类诊断累计 {repeat_count} 次)"
                        stderr_lines.append(diagnostic_line)
                        diagnostic_event = ExternalAgentEvent(
                            type="stderr", message=diagnostic_line
                        )
                        events.append(diagnostic_event)
                        persist_event(diagnostic_event)
                        await self._emit_material(
                            spec,
                            "agent.diagnostic",
                            {
                                "material_kind": "diagnostic",
                                "stream": "stderr",
                                "text": diagnostic_line,
                            },
                        )
                    continue

                last_meaningful_progress = time.monotonic()
                stdout_lines.append(line)
                parsed = _parse_json_lines(line)
                event = parsed[0] if parsed else ExternalAgentEvent(type="stdout", message=line)
                events.append(event)
                persist_event(event)
                emitted_error = await self._publish_provider_event(
                    spec,
                    event,
                    tool_parent_by_id=tool_parent_by_id,
                )
                saw_error = saw_error or emitted_error
        except asyncio.CancelledError:
            await _terminate_process_tree(proc)
            raise
        finally:
            await cancel_tasks_bounded([*pumps, process_waiter])
            if event_log is not None:
                event_log.close()

        if proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=_PROCESS_WAIT_TIMEOUT_S)
            except asyncio.TimeoutError:
                await _terminate_process_tree(proc)
        return (
            "\n".join(stdout_lines),
            "\n".join(stderr_lines),
            timed_out,
            timeout_reason,
            saw_error,
        )

    async def _emit_material(
        self,
        spec: ExternalAgentRunSpec,
        event_type: str,
        payload: dict[str, Any],
        *,
        parent_id: str | None = None,
        tags: list[str] | None = None,
    ) -> str | None:
        material_kind = str(payload.get("material_kind") or event_type)
        return await self._emit(
            spec,
            event_type,
            {"run_id": spec.run_id, "provider": self.provider_name, **payload},
            parent_id=parent_id,
            tags=[f"material:{material_kind}", *(tags or [])],
        )

    async def _publish_provider_event(
        self,
        spec: ExternalAgentRunSpec,
        event: ExternalAgentEvent,
        *,
        tool_parent_by_id: dict[str, str],
    ) -> bool:
        payload = event.payload
        kind = event.type
        item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
        item_type = str(item.get("type") or "")
        item_id = str(item.get("id") or "")

        if kind == "thread.started":
            await self._emit_material(
                spec,
                "agent.session.started",
                {"material_kind": "session-start", "session_id": payload.get("thread_id")},
            )
            return False
        if kind == "turn.started":
            await self._emit_material(spec, "agent.step.started", {"material_kind": "step-start"})
            return False
        if kind == "turn.completed":
            await self._emit_material(
                spec,
                "agent.step.finished",
                {"material_kind": "step-finish", "usage": payload.get("usage")},
            )
            return False
        if kind in {"error", "turn.failed"}:
            await self._emit_material(
                spec,
                "agent.error",
                {"material_kind": "error", "error": _summarize_result(payload.get("error") or event.message)},
            )
            return kind == "turn.failed" or not _is_transient_reconnect_error(
                event.message
            )
        if kind not in {"item.started", "item.completed"}:
            await self._emit_material(
                spec,
                "agent.provider.event",
                {"material_kind": "provider-event", "provider_event_type": kind},
            )
            return False

        if item_type in {"command_execution", "file_change"}:
            tool_name = "shell" if item_type == "command_execution" else "file_change"
            if kind == "item.started":
                args = (
                    {"command": item.get("command")}
                    if item_type == "command_execution"
                    else {"changes": item.get("changes")}
                )
                parent = await self._emit_material(
                    spec,
                    "agent.tool.call",
                    {
                        "material_kind": "tool-call",
                        "tool": tool_name,
                        "tool_use_id": item_id or None,
                        "args": _summarize_args(args),
                    },
                    tags=[f"tool:{tool_name}"],
                )
                if item_id and parent:
                    tool_parent_by_id[item_id] = parent
                return False
            status = str(item.get("status") or "")
            failed = status == "failed" or (
                item_type == "command_execution"
                and item.get("exit_code") not in (None, 0)
            )
            result = (
                item.get("aggregated_output")
                if item_type == "command_execution"
                else item.get("changes")
            )
            await self._emit_material(
                spec,
                "agent.tool.result",
                {
                    "material_kind": "tool-result",
                    "tool": tool_name,
                    "tool_use_id": item_id or None,
                    "verdict": "error" if failed else "ok",
                    "result": _summarize_result(result),
                },
                parent_id=tool_parent_by_id.get(item_id),
                tags=[f"tool:{tool_name}"],
            )
            # A non-zero tool result is observable material, not necessarily a
            # failed Agent run: Codex can inspect the error and recover later in
            # the same turn. Only a top-level `error`/`turn.failed` is terminal.
            return False
        if item_type == "agent_message" and kind == "item.completed":
            text = str(item.get("text") or "")
            await self._emit_material(
                spec,
                "agent.text.output",
                {"material_kind": "text-output", "text": text},
            )
            return False
        if item_type == "reasoning" and kind == "item.completed":
            await self._emit_material(
                spec,
                "agent.reasoning.completed",
                {"material_kind": "reasoning-metadata", "chars": len(str(item.get("text") or ""))},
            )
            return False
        if item_type == "error":
            await self._emit_material(
                spec,
                "agent.diagnostic",
                {
                    "material_kind": "diagnostic",
                    "provider_event_type": "item.error",
                    "text": _summarize_result(item.get("message")),
                },
            )
            return False
        await self._emit_material(
            spec,
            "agent.provider.event",
            {
                "material_kind": "provider-event",
                "provider_event_type": f"{kind}:{item_type or 'unknown'}",
            },
        )
        return False


def _optional_positive_float(value: Any) -> float | None:
    """Parse an explicitly configured timeout without inventing a default."""

    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"timeout must be a positive number, got {value!r}") from exc
    if parsed <= 0:
        raise ValueError(f"timeout must be positive, got {value!r}")
    return parsed


def _completion_contract_error(
    spec: ExternalAgentRunSpec,
    *,
    cwd: Path,
    watched_path_changes: dict[str, Any],
) -> str:
    """Fail a nominally green worker that omitted caller-declared deliverables."""

    metadata = spec.metadata or {}
    errors: list[str] = []
    raw_paths = metadata.get("required_output_paths")
    if isinstance(raw_paths, str):
        required_paths = [item.strip() for item in raw_paths.split("|") if item.strip()]
    elif isinstance(raw_paths, (list, tuple)):
        required_paths = [str(item).strip() for item in raw_paths if str(item).strip()]
    else:
        required_paths = []
    root = cwd.resolve()
    for raw_path in required_paths:
        path = Path(raw_path).expanduser()
        path = (path if path.is_absolute() else root / path).resolve()
        if not _is_relative_to(path, root):
            errors.append(f"required output escapes worker cwd: {raw_path}")
        elif not path.is_file() or path.stat().st_size <= 0:
            errors.append(f"required output is missing or empty: {raw_path}")
    if _metadata_flag(metadata, "require_watch_change") and not watched_path_changes.get(
        "has_changes"
    ):
        errors.append("required watch-path change was not observed")
    return "; ".join(errors)


def _is_transient_reconnect_error(message: str) -> bool:
    """Distinguish Codex transport retries from terminal worker failures.

    Codex can emit several ``type=error`` reconnect notices and then complete
    the same turn successfully with exit code zero. The later ``turn.failed``
    event or process exit code remains authoritative for terminal failures.
    """

    return bool(
        re.match(
            r"^\s*Reconnecting\.\.\.\s+\d+/\d+\s+\(",
            str(message or ""),
            flags=re.IGNORECASE,
        )
    )


@contextmanager
def _codex_runtime_cwd(cwd: Path) -> Iterator[Path]:
    """Yield a Codex-safe cwd while preserving the requested workspace.

    On Windows, Codex's PowerShell command wrapper can treat square brackets in
    a literal path as wildcard syntax. A temporary directory junction provides
    an equivalent path with no wildcard characters. The alias must live beside
    the workspace (rather than in the user temp directory): Codex's restricted
    Windows token cannot start a subprocess from a private temp junction. It is
    removed on every exit path and never changes the logical audit workspace.
    """

    resolved = cwd.expanduser().resolve()
    if os.name != "nt" or not any(char in str(resolved) for char in "[]*?"):
        yield resolved
        return

    alias_parent = _codex_alias_parent(resolved)
    alias = alias_parent / f".omni-codex-cwd-{os.getpid()}-{time.time_ns()}"
    comspec = os.environ.get("COMSPEC") or r"C:\Windows\System32\cmd.exe"
    linked = subprocess.run(
        [comspec, "/d", "/c", "mklink", "/J", str(alias), str(resolved)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_headless_process_kwargs(),
    )
    if linked.returncode != 0 or not alias.exists():
        if alias.exists():
            alias.rmdir()
        detail = (linked.stderr or linked.stdout or "junction creation failed").strip()
        raise RuntimeError(f"failed to create Codex-safe cwd alias: {detail}")
    try:
        yield alias
    finally:
        # rmdir removes the junction itself without touching its target.
        try:
            alias.rmdir()
        except FileNotFoundError:
            pass


def _codex_alias_parent(cwd: Path) -> Path:
    """Choose an invisible repo control dir, then a shell-safe ancestor."""

    # A junction under `.git` is invisible to git status, so permission audits
    # never mistake the alias and its target contents for newly created files.
    for ancestor in (cwd, *cwd.parents):
        marker = ancestor / ".git"
        git_dir: Path | None = None
        if marker.is_dir():
            git_dir = marker
        elif marker.is_file():
            try:
                header = marker.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                header = ""
            if header.lower().startswith("gitdir:"):
                raw = Path(header.split(":", 1)[1].strip())
                git_dir = (marker.parent / raw).resolve() if not raw.is_absolute() else raw.resolve()
        if (
            git_dir is not None
            and git_dir.exists()
            and git_dir.is_dir()
            and not any(char in str(git_dir) for char in "[]*?")
        ):
            return git_dir

    candidate = cwd.parent
    while any(char in str(candidate) for char in "[]*?"):
        if candidate.parent == candidate:
            raise RuntimeError(f"no wildcard-free ancestor available for Codex cwd: {cwd}")
        candidate = candidate.parent
    if not candidate.exists() or not candidate.is_dir():
        raise RuntimeError(f"Codex cwd alias parent is unavailable: {candidate}")
    return candidate


def _resolve_event_log_path(spec: ExternalAgentRunSpec) -> Path | None:
    """Resolve an optional live JSONL event log, constrained to the worker cwd."""

    raw = (spec.metadata or {}).get("event_log_path")
    if not raw:
        return None
    cwd = spec.normalized_cwd()
    path = Path(str(raw)).expanduser()
    path = (path if path.is_absolute() else cwd / path).resolve()
    try:
        path.relative_to(cwd)
    except ValueError as exc:
        raise ValueError(f"event_log_path must stay under cwd: {path}") from exc
    return path


def _diagnostic_key(line: str) -> str:
    """Collapse timestamped copies of the same Codex diagnostic."""

    if (
        "dropping in-process server notification (queue full)" in line
        or "dropping in-process app-server event because consumer queue is full"
        in line
    ):
        return "codex_app_server_queue_full"
    return line


def _codex_config_overrides(spec: ExternalAgentRunSpec) -> dict[str, str]:
    """Resolve process-local Codex config from the provider-neutral run spec."""

    overrides: dict[str, str] = {}
    if (spec.model_provider or "").strip().lower() == "the_company":
        overrides.update(_THE_COMPANY_CODEX_CONFIG)
    catalog_path = (spec.metadata or {}).get("model_catalog_path")
    if catalog_path:
        catalog = Path(str(catalog_path)).expanduser().resolve().as_posix()
        overrides["model_catalog_json"] = json.dumps(catalog, ensure_ascii=False)
    explicit = (spec.metadata or {}).get("codex_config")
    if isinstance(explicit, dict):
        overrides.update({str(key): str(value) for key, value in explicit.items()})
    return overrides


async def _terminate_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Terminate the whole external Agent tree and wait for process reaping."""

    if proc.returncode is not None:
        return
    if os.name == "nt":
        killer = None
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill.exe",
                "/PID",
                str(proc.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                **_headless_process_kwargs(),
            )
            await asyncio.wait_for(killer.wait(), timeout=_PROCESS_WAIT_TIMEOUT_S)
        except (OSError, asyncio.TimeoutError):
            if killer is not None and killer.returncode is None:
                killer.kill()
    else:
        proc.kill()
    try:
        await asyncio.wait_for(proc.wait(), timeout=_PROCESS_WAIT_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), timeout=_STREAM_CLEANUP_TIMEOUT_S)
        except asyncio.TimeoutError:
            # The caller must still be able to return a bounded result when a
            # broken child/pipe refuses to finish reaping.
            pass


def _build_env(extra: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    # A worker is a new audited Codex invocation. Inheriting the interactive
    # parent's thread id makes the nested CLI treat itself as part of the
    # parent's approval/sandbox session and can collapse workspace-write to
    # read-only.
    env.pop("CODEX_THREAD_ID", None)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("NO_COLOR", "1")
    env.setdefault("FORCE_COLOR", "0")
    env.update({str(k): str(v) for k, v in extra.items()})
    return env


def _resolve_executable_for_subprocess(executable: str) -> str:
    """Resolve command shims that Windows CreateProcess will not find by basename."""

    candidate = Path(executable)
    if candidate.is_absolute() or candidate.parent != Path("."):
        return executable
    resolved = shutil.which(executable)
    if resolved:
        return resolved
    if os.name == "nt" and not candidate.suffix:
        for suffix in (".cmd", ".exe", ".bat", ".ps1"):
            resolved = shutil.which(executable + suffix)
            if resolved:
                return resolved
    return executable


def _parse_structured_final_text(text: str) -> dict[str, Any] | None:
    stripped = (text or "").strip()
    if not stripped:
        return None
    candidates = [stripped]
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].strip().startswith("```"):
            candidates.append("\n".join(lines[1:-1]).strip())
    match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _parse_json_lines(stdout: str) -> list[ExternalAgentEvent]:
    events: list[ExternalAgentEvent] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            events.append(ExternalAgentEvent(type="stdout", message=stripped))
            continue
        event_type = str(payload.get("type") or payload.get("event") or "json")
        message = str(payload.get("message") or payload.get("text") or "")
        events.append(ExternalAgentEvent(type=event_type, message=message, payload=payload))
    return events


def _redacted_event_log_content(
    event: ExternalAgentEvent,
) -> tuple[str, dict[str, Any]]:
    """Keep live Agent observability without copying retrieved source bodies."""

    message = event.message
    payload = dict(event.payload)
    if event.type == "stdout" and message:
        message = _redacted_text_marker(message)
    item = payload.get("item")
    if isinstance(item, dict) and item.get("type") == "command_execution":
        item = dict(item)
        output = item.get("aggregated_output")
        if isinstance(output, str) and output:
            item["aggregated_output"] = _redacted_text_marker(output)
        payload["item"] = item
    return message, payload


def _redacted_text_marker(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()
    return f"<redacted chars={len(value)} sha256={digest}>"


def _last_text_from_events(events: list[ExternalAgentEvent]) -> str:
    for event in reversed(events):
        if event.message:
            return event.message
        for key in ("text", "message", "content"):
            value = event.payload.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _git_diff_stat(cwd: Path) -> str:
    # The pathspec is load-bearing, not cosmetic.  A worker cwd is not always a
    # repository root: session_companion hands each lane an empty scratch dir
    # under data/_runtime/session_companion/workspaces/, and a bare `git diff`
    # there walks *up* until it finds the enclosing omnicompany repo, then
    # diffs that entire work tree.  Measured on this machine: 9-13GB RSS per
    # call, re-spawned every few seconds, driving system RAM from 73% to 93%
    # in a sawtooth -- while reporting changes the worker never made, because
    # the diff described a repository it had never touched.
    #
    # Restricting to `-- .` keeps the intended meaning ("what did this worker
    # change here") in every case: at a repository root it still covers the
    # whole repo, in a subdirectory it scopes to that subdirectory, and in a
    # non-repository scratch dir it correctly reports nothing (6MB / 0.1s).
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), "diff", "--stat", "--", "."],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            **_headless_process_kwargs(),
        )
    except Exception:
        return ""
    return proc.stdout.strip()


def _format_diff_summary(diff_stat: str, newly_changed_files: list[str]) -> str:
    parts: list[str] = []
    if diff_stat:
        parts.append(diff_stat)
    if newly_changed_files:
        parts.append(
            "Newly changed files detected during external worker window "
            "(ownership unproven):\n"
            + "\n".join(f"- {path}" for path in newly_changed_files)
        )
    return "\n\n".join(parts)


def _append_watched_path_summary(diff_summary: str, changes: dict[str, Any]) -> str:
    if not changes.get("has_changes"):
        return diff_summary
    parts = [diff_summary] if diff_summary else []
    lines = ["Watched path changes detected outside git-status reliance:"]
    for key, label in (
        ("created", "Created"),
        ("modified", "Modified"),
        ("deleted", "Deleted"),
    ):
        values = list(changes.get(key) or [])
        if not values:
            continue
        shown = values[:100]
        lines.append(f"{label}:")
        lines.extend(f"- {path}" for path in shown)
        if len(values) > len(shown):
            lines.append(f"- ... {len(values) - len(shown)} more")
    parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _append_rollback_summary(diff_summary: str, rollback: dict[str, list[str]]) -> str:
    parts = [diff_summary] if diff_summary else []
    if rollback["rolled_back"]:
        parts.append(
            "Readonly rollback completed for:\n"
            + "\n".join(f"- {path}" for path in rollback["rolled_back"])
        )
    if rollback["failed"]:
        parts.append(
            "Readonly rollback failed for:\n"
            + "\n".join(f"- {path}" for path in rollback["failed"])
        )
    if rollback.get("preserved"):
        parts.append(
            "Readonly guard preserved detected changes because process ownership "
            "was not proven:\n"
            + "\n".join(f"- {path}" for path in rollback["preserved"])
        )
    return "\n\n".join(parts)


def _git_changed_files(cwd: Path) -> list[str]:
    # Same trap as _git_diff_stat: a session_companion scratch dir is not a repository, so
    # an unscoped `git status --untracked-files=all` walks up and scans the whole omnicompany
    # work tree.  That scan can outlive the 10s timeout, and a git killed mid-refresh leaves a
    # 0-byte .git/index.lock (2026-10-12: three such locks in 45 minutes with only this command
    # running against the repo, every session's commits blocked); it also counts other
    # sessions' edits as this worker's changes.
    # `-- .` scopes the answer to the worker's own directory; --no-optional-locks keeps a
    # read-only query from taking the index lock at all.
    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(cwd), "status", "--porcelain=v1", "--untracked-files=all", "--", "."],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            **_headless_process_kwargs(),
        )
    except Exception:
        return []
    return _parse_git_status_paths(proc.stdout)


def _relative_watch_paths(cwd: Path, watch_paths: list[Path]) -> list[str]:
    return [_relative_posix(path, cwd) for path in watch_paths]


def _snapshot_watch_paths(cwd: Path, watch_paths: list[Path]) -> dict[str, Any]:
    entries: dict[str, dict[str, Any]] = {}
    roots = _relative_watch_paths(cwd, watch_paths)
    for root in watch_paths:
        rel_root = _relative_posix(root, cwd)
        if not root.exists():
            entries[rel_root] = {"kind": "missing"}
            continue
        if root.is_file():
            entries[rel_root] = _file_fingerprint(root)
            continue
        if root.is_dir():
            entries[rel_root] = {"kind": "dir"}
            for path in sorted(root.rglob("*")):
                if path.is_dir():
                    if path.name in {".git"}:
                        continue
                    entries[_relative_posix(path, cwd)] = {"kind": "dir"}
                    continue
                if path.is_file():
                    entries[_relative_posix(path, cwd)] = _file_fingerprint(path)
    return {"roots": roots, "entries": entries}


def _diff_watch_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_entries = dict(before.get("entries") or {})
    after_entries = dict(after.get("entries") or {})
    before_keys = set(before_entries)
    after_keys = set(after_entries)
    created_from_missing = {
        path
        for path in before_keys & after_keys
        if before_entries.get(path, {}).get("kind") == "missing"
        and after_entries.get(path, {}).get("kind") != "missing"
    }
    deleted_to_missing = {
        path
        for path in before_keys & after_keys
        if before_entries.get(path, {}).get("kind") != "missing"
        and after_entries.get(path, {}).get("kind") == "missing"
    }
    created = sorted((after_keys - before_keys) | created_from_missing)
    deleted = sorted((before_keys - after_keys) | deleted_to_missing)
    modified = sorted(
        path
        for path in before_keys & after_keys
        if path not in created_from_missing
        and path not in deleted_to_missing
        if before_entries.get(path) != after_entries.get(path)
    )
    return {
        "roots": list(after.get("roots") or before.get("roots") or []),
        "created": created,
        "modified": modified,
        "deleted": deleted,
        "has_changes": bool(created or modified or deleted),
        "before_entry_count": len(before_entries),
        "after_entry_count": len(after_entries),
    }


def _file_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "kind": "file",
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": _sha256_file(path),
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rollback_new_changes(cwd: Path, paths: list[str]) -> dict[str, list[str]]:
    """Compatibility shim that preserves shared-worktree changes.

    A before/after git-status delta proves only that a path changed during the
    worker window. It does not prove which process owned the change. External
    workers often run beside interactive agents, so restoring tracked paths or
    deleting untracked paths here can destroy unrelated work. Keep the legacy
    result keys for callers while making the safe default preserve-and-report.
    """

    root = cwd.resolve()
    preserved: list[str] = []
    failed: list[str] = []
    for rel_path in paths:
        target = (root / rel_path).resolve()
        if not _is_relative_to(target, root):
            failed.append(rel_path)
            continue
        preserved.append(rel_path)
    return {"rolled_back": [], "failed": failed, "preserved": preserved}


def _materialize_codex_output_schema(source: Path, target: Path) -> Path:
    """Write a temporary OpenAI-strict schema without mutating its source."""

    payload = json.loads(source.expanduser().resolve().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("output schema root must be an object")
    strict = _strictify_codex_output_schema(payload)
    target.write_text(
        json.dumps(strict, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def _metadata_flag(metadata: dict[str, Any] | None, key: str) -> bool:
    value = (metadata or {}).get(key)
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _strictify_codex_output_schema(node: Any) -> Any:
    """Convert Pydantic JSON Schema objects to the Codex strict subset."""

    if isinstance(node, list):
        return [_strictify_codex_output_schema(item) for item in node]
    if not isinstance(node, dict):
        return node

    is_schema_node = any(
        key in node
        for key in (
            "type",
            "properties",
            "$ref",
            "anyOf",
            "oneOf",
            "allOf",
            "items",
            "enum",
            "const",
        )
    )
    result: dict[str, Any] = {}
    for key, value in node.items():
        if key == "default" and is_schema_node:
            continue
        if key in {"properties", "$defs", "definitions"} and isinstance(value, dict):
            result[key] = {
                name: _strictify_codex_output_schema(child)
                for name, child in value.items()
            }
        else:
            result[key] = _strictify_codex_output_schema(value)

    properties = result.get("properties")
    if isinstance(properties, dict):
        result["required"] = list(properties)
        result["additionalProperties"] = False
    return result


def _git_path_is_tracked(cwd: Path, rel_path: str) -> bool:
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), "ls-files", "--error-unmatch", "--", rel_path],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            **_headless_process_kwargs(),
        )
    except Exception:
        return False
    return proc.returncode == 0


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _relative_posix(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _parse_git_status_paths(status_output: str) -> list[str]:
    paths: list[str] = []
    for line in status_output.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        # Porcelain v1 rename/copy format is "old -> new"; the new path is the
        # file the external worker left in the workspace.
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        if path:
            paths.append(path.strip('"'))
    return paths


def _redact_argv(argv: list[str]) -> list[str]:
    redacted: list[str] = []
    skip_next = False
    for item in argv:
        if skip_next:
            redacted.append("<redacted>")
            skip_next = False
            continue
        redacted.append(item)
        if item in {"--output-last-message", "--output-schema"}:
            skip_next = True
    if redacted:
        redacted[-1] = f"<prompt chars={len(argv[-1])}>"
    return redacted


__all__ = ["CodexExecWorker"]
