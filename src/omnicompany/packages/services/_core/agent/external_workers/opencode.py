# [OMNI] origin=codex domain=services/agent ts=2026-07-16 type=infrastructure
# [OMNI] material_id="material:core.agent.external_worker.opencode_adapter.py"
"""OpenCode CLI adapter with line-streamed EventBus materials.

OpenCode's ``run --format json`` contract is newline-delimited JSON.  The
adapter consumes one line at a time and publishes one semantic material at a
time; it never waits for the full process output before exposing progress.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.agent.external_workers.base import (
    ExternalAgentEvent,
    ExternalAgentPermissionMode,
    ExternalAgentResult,
    ExternalAgentRunSpec,
    ExternalAgentStatus,
    ExternalAgentWorker,
    _headless_process_kwargs,
)
from omnicompany.packages.services._core.agent.external_workers.codex import (
    _append_rollback_summary,
    _append_watched_path_summary,
    _build_env,
    _diff_watch_snapshots,
    _format_diff_summary,
    _git_changed_files,
    _git_diff_stat,
    _relative_watch_paths,
    _resolve_executable_for_subprocess,
    _rollback_new_changes,
    _snapshot_watch_paths,
    _terminate_process_tree,
)
from omnicompany.packages.services._core.agent.external_workers.trace import (
    _summarize_args,
    _summarize_result,
)


_READONLY_PERMISSION = {
    "*": "deny",
    "read": "allow",
    "glob": "allow",
    "grep": "allow",
    "list": "allow",
    "lsp": "allow",
    "skill": "allow",
    "edit": "deny",
    "bash": "deny",
    "task": "deny",
    "external_directory": "deny",
    "webfetch": "deny",
    "websearch": "deny",
    "question": "deny",
}

DEFAULT_OPENCODE_MODEL = os.environ.get(
    "OPENCODE_DEFAULT_MODEL",
    "opencode-go/deepseek-v4-pro",
)

_USAGE_TOKEN_FIELDS = ("input", "output", "cache_read", "cache_write", "reasoning")


def _empty_opencode_usage() -> dict[str, int | float | None]:
    """Return the raw usage shape without treating absent telemetry as zero."""

    return {field: None for field in (*_USAGE_TOKEN_FIELDS, "cost")}


def _numeric_usage_value(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _accumulate_step_finish_usage(
    payload: dict[str, Any],
    usage: dict[str, int | float | None],
    seen_steps: set[str],
) -> bool:
    """Accumulate one OpenCode step_finish event exactly once.

    OpenCode reports per-step values.  A retried/duplicated event is identified
    by its event or part id; fixtures without ids use the canonical event body.
    Missing fields are left as ``None`` until a later step supplies them.
    """

    part = payload.get("part") if isinstance(payload.get("part"), dict) else {}
    session_id = str(payload.get("sessionID") or "")
    event_id = (
        payload.get("id")
        or payload.get("eventID")
        or part.get("id")
    )
    if event_id:
        step_key = f"{session_id}:{event_id}"
    else:
        step_key = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if step_key in seen_steps:
        return False
    seen_steps.add(step_key)

    tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
    cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
    values = {
        "input": tokens.get("input"),
        "output": tokens.get("output"),
        "cache_read": cache.get("read", tokens.get("cache_read")),
        "cache_write": cache.get("write", tokens.get("cache_write")),
        "reasoning": tokens.get("reasoning"),
        "cost": part.get("cost"),
    }
    for field, raw_value in values.items():
        value = _numeric_usage_value(raw_value)
        if value is None:
            continue
        prior = usage[field]
        usage[field] = value if prior is None else prior + value
    return True


class OpenCodeRunWorker(ExternalAgentWorker):
    """Run OpenCode as an audited, streaming external Agent."""

    provider_name = "opencode"
    # There is deliberately no implicit 60-second or total runtime cutoff.
    # Callers may opt into an inactivity limit with metadata.idle_timeout_s.
    handles_timeout = True

    def __init__(self, *, bus: Any | None = None, opencode_executable: str = "opencode"):
        super().__init__(bus=bus)
        self.opencode_executable = opencode_executable

    def build_command(self, spec: ExternalAgentRunSpec) -> list[str]:
        if spec.output_schema_path:
            raise ValueError(
                "OpenCode worker does not emulate schemas in prompts; use a native tool call "
                "or let the Agent write a real file and lint it"
            )
        if spec.normalized_permission_mode() == ExternalAgentPermissionMode.TRUSTED_BYPASS:
            raise ValueError(
                "OpenCode has no verified trusted-bypass CLI contract; choose readonly "
                "or workspace-write so explicit deny rules remain enforceable"
            )
        model = spec.model or DEFAULT_OPENCODE_MODEL
        if "/" not in model:
            raise ValueError(
                "OpenCode model must be a full provider/model id; "
                f"got {model!r}"
            )
        cmd = [
            _resolve_executable_for_subprocess(self.opencode_executable),
        ]
        metadata = spec.metadata or {}
        minimal_context = _metadata_flag(metadata, "minimal_context")
        if _metadata_flag(metadata, "opencode_pure") or minimal_context:
            cmd.append("--pure")
        cmd.extend([
            "run",
            "--format",
            "json",
            "--dir",
            str(spec.normalized_cwd()),
            "--auto",
            "--model",
            model,
        ])
        if spec.profile:
            cmd.extend(["--agent", spec.profile])
        session_id = str((spec.metadata or {}).get("session_id") or "").strip()
        if session_id:
            cmd.extend(["--session", session_id])
        # The prompt is supplied on stdin. This avoids Windows command-line
        # limits and keeps the command event free of user material.
        return cmd

    async def _run_impl(self, spec: ExternalAgentRunSpec) -> ExternalAgentResult:
        cwd = spec.normalized_cwd()
        before_diff = _git_diff_stat(cwd)
        before_changed = set(_git_changed_files(cwd))
        watch_paths = spec.normalized_watch_paths()
        before_watch = _snapshot_watch_paths(cwd, watch_paths)
        started = time.monotonic()
        events: list[ExternalAgentEvent] = []
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        final_parts: list[str] = []
        final_parts_by_message: dict[str, list[str]] = {}
        final_message_order: list[str] = []
        tool_parent_by_id: dict[str, str] = {}
        session_id = ""
        usage = _empty_opencode_usage()
        seen_usage_steps: set[str] = set()
        saw_fatal_error = False
        timed_out = False
        # Mojibake is recorded on its own channel rather than mixed into the
        # child's stderr: the final error is built from stderr, so appending the
        # complaint there used to overwrite the real reason a run ended.
        encoding_violations: list[dict[str, str]] = []
        text_integrity = str(
            (spec.metadata or {}).get("text_integrity") or "strict"
        ).strip().lower()

        cmd = self.build_command(spec)
        env = self._build_opencode_env(spec)
        events.append(
            ExternalAgentEvent(
                type="command",
                message="opencode run command built",
                payload={"argv": _redact_argv(cmd)},
            )
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(cwd),
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
                raw={"usage": usage},
                error=f"opencode executable not found: {self.opencode_executable}",
            )

        queue: asyncio.Queue[tuple[str, bytes | None, BaseException | None]] = asyncio.Queue()

        async def pump(name: str, stream: asyncio.StreamReader | None) -> None:
            pending = bytearray()
            try:
                if stream is not None:
                    while True:
                        chunk = await stream.read(64 * 1024)
                        if not chunk:
                            break
                        pending.extend(chunk)
                        while True:
                            newline = pending.find(b"\n")
                            if newline < 0:
                                break
                            line = bytes(pending[: newline + 1])
                            del pending[: newline + 1]
                            await queue.put((name, line, None))
                    if pending:
                        await queue.put((name, bytes(pending), None))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Wake the consumer immediately so a broken pipe reader cannot
                # masquerade as a provider idle timeout.
                await queue.put((name, None, exc))
                return
            # Each normally completed stream contributes exactly one EOF.
            await queue.put((name, None, None))

        pumps = [
            asyncio.create_task(pump("stdout", proc.stdout)),
            asyncio.create_task(pump("stderr", proc.stderr)),
        ]
        events.append(
            ExternalAgentEvent(
                type="phase",
                message="stream pumps active; awaiting provider output",
                payload={"phase": "awaiting_provider_output"},
            )
        )
        assert proc.stdin is not None
        proc.stdin.write(spec.full_prompt().encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

        active_streams = 2
        stream_error = ""
        idle_timeout_raw = (spec.metadata or {}).get("idle_timeout_s")
        idle_timeout = float(idle_timeout_raw) if idle_timeout_raw is not None else None
        total_deadline = started + spec.timeout_s if spec.timeout_s is not None else None
        timeout_reason = ""
        last_observable_at = time.monotonic()

        def _remaining_timeout() -> float | None:
            candidates: list[float] = []
            if idle_timeout is not None:
                candidates.append(idle_timeout - (time.monotonic() - last_observable_at))
            if total_deadline is not None:
                candidates.append(total_deadline - time.monotonic())
            return min(candidates) if candidates else None

        async def _await_bounded(awaitable: Any) -> Any:
            remaining = _remaining_timeout()
            if remaining is None:
                return await awaitable
            if remaining <= 0:
                raise asyncio.TimeoutError
            return await asyncio.wait_for(awaitable, timeout=remaining)

        try:
            while active_streams:
                try:
                    remaining_total = (
                        total_deadline - time.monotonic()
                        if total_deadline is not None
                        else None
                    )
                    if remaining_total is not None and remaining_total <= 0:
                        timeout_reason = f"total deadline reached after {spec.timeout_s:g}s"
                        raise asyncio.TimeoutError
                    wait_timeout_candidates = [
                        value
                        for value in (
                            idle_timeout - (time.monotonic() - last_observable_at)
                            if idle_timeout is not None
                            else None,
                            remaining_total,
                        )
                        if value is not None
                    ]
                    if not wait_timeout_candidates:
                        stream_name, raw_line, read_error = await queue.get()
                    else:
                        stream_name, raw_line, read_error = await asyncio.wait_for(
                            queue.get(),
                            timeout=min(wait_timeout_candidates),
                        )
                except asyncio.TimeoutError:
                    timed_out = True
                    if not timeout_reason:
                        if total_deadline is not None and time.monotonic() >= total_deadline:
                            timeout_reason = f"total deadline reached after {spec.timeout_s:g}s"
                        else:
                            timeout_reason = (
                                f"no observable output for {idle_timeout:g}s"
                                if idle_timeout is not None
                                else "total deadline reached"
                            )
                    await _terminate_process_tree(proc)
                    for pump_task in pumps:
                        pump_task.cancel()
                    break
                if read_error is not None:
                    stream_error = (
                        f"failed reading opencode {stream_name}: "
                        f"{type(read_error).__name__}: {read_error}"
                    )
                    await _terminate_process_tree(proc)
                    for pump_task in pumps:
                        pump_task.cancel()
                    break
                if raw_line is None:
                    active_streams -= 1
                    continue
                if stream_name == "stdout":
                    last_observable_at = time.monotonic()
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if "\ufffd" in line:
                    if len(encoding_violations) < 20:
                        offset = line.find("\ufffd")
                        encoding_violations.append(
                            {
                                "stream": stream_name,
                                "context": line[max(0, offset - 120):offset + 120],
                            }
                        )
                    # Under "strict" the producer owes us UTF-8 and a breach voids the
                    # run. A worker that only streams telemetry cannot make that
                    # promise: it shells out to Windows tools that emit the local code
                    # page, and one such byte should not void hours of work.
                    if text_integrity != "lenient":
                        saw_fatal_error = True
                if stream_name == "stderr":
                    stderr_lines.append(line)
                    if line.strip():
                        await _await_bounded(
                            self._emit_material(
                                spec,
                                "agent.diagnostic",
                                {"material_kind": "diagnostic", "stream": "stderr", "text": line[-4000:]},
                            )
                        )
                    continue

                stdout_lines.append(line)
                event, payload = _parse_json_line(line)
                events.append(event)
                if payload is None:
                    await _await_bounded(
                        self._emit_material(
                            spec,
                            "agent.diagnostic",
                            {"material_kind": "diagnostic", "stream": "stdout", "text": line[-4000:]},
                        )
                    )
                    continue
                session_id = str(payload.get("sessionID") or session_id)
                if str(payload.get("type") or "") == "step_finish":
                    _accumulate_step_finish_usage(payload, usage, seen_usage_steps)
                emitted_fatal_error, text = await _await_bounded(
                    self._publish_provider_event(
                        spec,
                        payload,
                        tool_parent_by_id=tool_parent_by_id,
                    )
                )
                saw_fatal_error = saw_fatal_error or emitted_fatal_error
                if text:
                    part = payload.get("part") if isinstance(payload.get("part"), dict) else {}
                    message_id = str(part.get("messageID") or "").strip()
                    if message_id:
                        if message_id not in final_parts_by_message:
                            final_parts_by_message[message_id] = []
                            final_message_order.append(message_id)
                        final_parts_by_message[message_id].append(text)
                    else:
                        final_parts.append(text)
        except asyncio.TimeoutError:
            timed_out = True
            if not timeout_reason:
                if total_deadline is not None and time.monotonic() >= total_deadline:
                    timeout_reason = f"total deadline reached after {spec.timeout_s:g}s"
                else:
                    timeout_reason = (
                        f"no observable output for {idle_timeout:g}s"
                        if idle_timeout is not None
                        else "total deadline reached"
                    )
            await _terminate_process_tree(proc)
            for pump_task in pumps:
                pump_task.cancel()
        except asyncio.CancelledError:
            for pump_task in pumps:
                pump_task.cancel()
            await _terminate_process_tree(proc)
            raise
        finally:
            await asyncio.gather(*pumps, return_exceptions=True)
        if proc.returncode is None:
            # EOF on both pipes does not guarantee that the process itself has
            # exited (a detached child can keep the handle alive). Never let
            # final reaping bypass the same timeout contract as stream drain.
            reap_timeout = _remaining_timeout()
            if reap_timeout is None:
                reap_timeout = 5.0
            try:
                await asyncio.wait_for(proc.wait(), timeout=max(0.05, reap_timeout))
            except asyncio.TimeoutError:
                timed_out = True
                timeout_reason = timeout_reason or "process did not exit after stream EOF"
                await _terminate_process_tree(proc)

        after_diff = _git_diff_stat(cwd)
        after_changed = set(_git_changed_files(cwd))
        after_watch = _snapshot_watch_paths(cwd, watch_paths)
        watched_path_changes = _diff_watch_snapshots(before_watch, after_watch)
        changed_files = sorted(after_changed - before_changed)
        # Do not echo the repository's pre-existing dirty diff into every run
        # result. Only summarize git diff text when this run changed it; ignored
        # or untracked watch roots are reported separately below.
        diff_summary = _format_diff_summary(
            after_diff if after_diff != before_diff else "",
            changed_files,
        )
        diff_summary = _append_watched_path_summary(diff_summary, watched_path_changes)
        readonly_rollback: dict[str, list[str]] | None = None

        encoding_note = ""
        if encoding_violations:
            encoding_note = (
                f"output carried Unicode replacement characters on "
                f"{len(encoding_violations)} line(s); the UTF-8 producer/decoder "
                f"contract is invalid"
            )

        if timed_out:
            status = ExternalAgentStatus.TIMED_OUT
            error = f"opencode timed out: {timeout_reason}"
        elif stream_error:
            status = ExternalAgentStatus.FAILED
            error = stream_error
        elif proc.returncode == 0 and not saw_fatal_error:
            status = ExternalAgentStatus.SUCCEEDED
            error = ""
        else:
            status = ExternalAgentStatus.FAILED
            # Say why the process ended before saying the bytes were malformed:
            # the exit reason is what a reader needs, and a mojibake line is not
            # a substitute for it.
            reasons = [
                "\n".join(stderr_lines)[-4000:],
                f"opencode exited {proc.returncode}" if proc.returncode else "",
                encoding_note if saw_fatal_error else "",
            ]
            error = "; ".join(part for part in reasons if part) or (
                "OpenCode returned an error event"
            )
        if encoding_note and not saw_fatal_error:
            events.append(
                ExternalAgentEvent(
                    type="diagnostic",
                    message=encoding_note,
                    payload={"encoding_violations": encoding_violations[:5]},
                )
            )

        if (
            spec.normalized_permission_mode() == ExternalAgentPermissionMode.READONLY
            and (after_diff != before_diff or changed_files or watched_path_changes["has_changes"])
        ):
            status = ExternalAgentStatus.PERMISSION_VIOLATION
            rollback_paths = sorted(
                set(changed_files + list(watched_path_changes["created"])),
                key=lambda path: path.count("/"),
                reverse=True,
            )
            readonly_rollback = _rollback_new_changes(cwd, rollback_paths)
            diff_summary = _append_rollback_summary(diff_summary, readonly_rollback)
            error = (
                "readonly workspace changed during worker window; ownership is unproven "
                "and detected changes were preserved"
            )
            if readonly_rollback["failed"]:
                error += "; rollback failed for " + ", ".join(readonly_rollback["failed"])

        return ExternalAgentResult(
            run_id=spec.run_id,
            provider=self.provider_name,
            status=status,
            final_text=_select_final_text(
                final_parts_by_message,
                final_message_order,
                fallback_parts=final_parts,
            ),
            events=events,
            exit_code=proc.returncode,
            changed_files=changed_files,
            diff_summary=diff_summary,
            error=error,
            duration_ms=(time.monotonic() - started) * 1000,
            raw={
                "session_id": session_id,
                "usage": usage,
                "stdout_tail": "\n".join(stdout_lines)[-4000:],
                "stderr_tail": "\n".join(stderr_lines)[-4000:],
                "text_integrity": text_integrity,
                "encoding_violations": encoding_violations,
                "preexisting_changed_files_count": len(before_changed),
                "after_changed_files_count": len(after_changed),
                "after_rollback_changed_files_count": len(_git_changed_files(cwd)),
                "watch_paths": _relative_watch_paths(cwd, watch_paths),
                "watched_path_changes": watched_path_changes,
                "readonly_rollback": readonly_rollback,
                "timeout_policy": {
                    "total_timeout_s": spec.timeout_s,
                    "idle_timeout_s": idle_timeout,
                    "default_total_timeout": None,
                },
            },
        )

    def _build_opencode_env(self, spec: ExternalAgentRunSpec) -> dict[str, str]:
        env = _build_env(spec.env)
        metadata = spec.metadata or {}
        if _metadata_flag(metadata, "minimal_context"):
            # OpenCode's --pure disables external plugins, but not the global
            # Claude/Agents skill and prompt compatibility layers.  A caller
            # selecting minimal context must get the narrow context it asked
            # for even if the parent process exported contrary values.
            env["OPENCODE_DISABLE_EXTERNAL_SKILLS"] = "1"
            env["OPENCODE_DISABLE_CLAUDE_CODE"] = "1"
        config = str((spec.metadata or {}).get("opencode_config") or env.get("OPENCODE_CONFIG") or "").strip()
        if not config:
            default = default_opencode_config_path()
            if default.exists():
                config = str(default)
        if config:
            config_path = Path(config).expanduser().resolve()
            if not config_path.is_file():
                raise ValueError(f"OpenCode config does not exist: {config_path}")
            env["OPENCODE_CONFIG"] = str(config_path)
        else:
            raise ValueError(
                "OpenCode provider config is required; no OPENCODE_CONFIG "
                "or repository config/agents/opencode.jsonc was found"
            )
        if spec.normalized_permission_mode() == ExternalAgentPermissionMode.READONLY:
            env["OPENCODE_PERMISSION"] = json.dumps(_READONLY_PERMISSION, separators=(",", ":"))
        return env

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
            {
                "run_id": spec.run_id,
                "provider": self.provider_name,
                **payload,
            },
            parent_id=parent_id,
            tags=[f"material:{material_kind}", *(tags or [])],
        )

    async def _publish_provider_event(
        self,
        spec: ExternalAgentRunSpec,
        payload: dict[str, Any],
        *,
        tool_parent_by_id: dict[str, str],
    ) -> tuple[bool, str]:
        kind = str(payload.get("type") or "unknown")
        part = payload.get("part") if isinstance(payload.get("part"), dict) else {}
        session_id = str(payload.get("sessionID") or "")
        common = {"session_id": session_id}

        if kind == "step_start":
            await self._emit_material(
                spec,
                "agent.step.started",
                {
                    "material_kind": "step-start",
                    **common,
                    "step_id": str(part.get("id") or part.get("messageID") or ""),
                },
            )
            return False, ""
        if kind == "step_finish":
            await self._emit_material(
                spec,
                "agent.step.finished",
                {
                    "material_kind": "step-finish",
                    **common,
                    "reason": part.get("reason"),
                    "cost": part.get("cost"),
                    "tokens": part.get("tokens"),
                },
            )
            return False, ""
        if kind == "text":
            text = str(part.get("text") or "").strip()
            if text:
                await self._emit_material(
                    spec,
                    "agent.text.output",
                    {"material_kind": "text-output", **common, "text": text},
                )
            return False, text
        if kind == "reasoning":
            # Preserve observability without persisting hidden chain-of-thought.
            await self._emit_material(
                spec,
                "agent.reasoning.completed",
                {"material_kind": "reasoning-metadata", **common, "chars": len(str(part.get("text") or ""))},
            )
            return False, ""
        if kind == "tool_use":
            tool_id = str(part.get("id") or "")
            tool_name = str(part.get("tool") or "?")
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            args = state.get("input") if isinstance(state.get("input"), dict) else {}
            call_id = await self._emit_material(
                spec,
                "agent.tool.call",
                {
                    "material_kind": "tool-call",
                    **common,
                    "tool": tool_name,
                    "tool_use_id": tool_id or None,
                    "args": _summarize_args(args),
                },
                tags=[f"tool:{tool_name}"],
            )
            if tool_id and call_id:
                tool_parent_by_id[tool_id] = call_id
            state_status = str(state.get("status") or "")
            metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
            exit_code = metadata.get("exit")
            tool_failed = state_status == "error" or (
                isinstance(exit_code, int) and not isinstance(exit_code, bool) and exit_code != 0
            )
            raw_result = state.get("error") if state_status == "error" else state.get("output")
            await self._emit_material(
                spec,
                "agent.tool.result",
                {
                    "material_kind": "tool-result",
                    **common,
                    "tool": tool_name,
                    "tool_use_id": tool_id or None,
                    "verdict": "error" if tool_failed else "ok",
                    "result": _summarize_result(raw_result),
                },
                parent_id=tool_parent_by_id.get(tool_id),
                tags=[f"tool:{tool_name}"],
            )
            # A failed tool call (including a completed shell tool with a
            # non-zero process exit) is an observable Agent event, not a terminal
            # provider failure. OpenCode may recover in a later step and still
            # finish normally. Only a top-level provider `error` event is fatal.
            return False, ""
        if kind == "error":
            await self._emit_material(
                spec,
                "agent.error",
                {"material_kind": "error", **common, "error": _summarize_result(payload.get("error"))},
            )
            return True, ""

        await self._emit_material(
            spec,
            "agent.provider.event",
            {"material_kind": "provider-event", **common, "provider_event_type": kind},
        )
        return False, ""


def _parse_json_line(line: str) -> tuple[ExternalAgentEvent, dict[str, Any] | None]:
    stripped = line.strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return ExternalAgentEvent(type="stdout", message=stripped), None
    if not isinstance(payload, dict):
        return ExternalAgentEvent(type="json", payload={"value": payload}), None
    kind = str(payload.get("type") or "json")
    part = payload.get("part") if isinstance(payload.get("part"), dict) else {}
    message = str(part.get("text") or "")
    return ExternalAgentEvent(type=kind, message=message, payload=payload), payload


def _dedupe_parts(parts: list[str]) -> str:
    out: list[str] = []
    for part in parts:
        text = part.strip()
        if text and text not in out:
            out.append(text)
    return "\n\n".join(out)


def _select_final_text(
    parts_by_message: dict[str, list[str]],
    message_order: list[str],
    *,
    fallback_parts: list[str],
) -> str:
    """Return only the last assistant message, not pre-tool progress prose."""
    for message_id in reversed(message_order):
        text = _dedupe_parts(parts_by_message.get(message_id, []))
        if text:
            return text
    return _dedupe_parts(fallback_parts)


def default_opencode_config_path() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "config" / "agents" / "opencode-the_company.jsonc"
        if candidate.exists():
            return candidate
    return Path("config/agents/opencode-the_company.jsonc")


def _metadata_flag(metadata: dict[str, Any], key: str) -> bool:
    value = metadata.get(key)
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _redact_argv(argv: list[str]) -> list[str]:
    redacted = list(argv)
    for index, item in enumerate(redacted[:-1]):
        if item == "--session":
            redacted[index + 1] = "<session-id>"
    return redacted


__all__ = [
    "DEFAULT_OPENCODE_MODEL",
    "OpenCodeRunWorker",
    "default_opencode_config_path",
]
