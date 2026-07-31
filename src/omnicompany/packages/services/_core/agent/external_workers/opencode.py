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
    "the_company/gpt-5.6-terra",
)


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
        if _metadata_flag(spec.metadata or {}, "opencode_pure"):
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
        saw_fatal_error = False
        timed_out = False

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
            )
        except FileNotFoundError:
            return ExternalAgentResult(
                run_id=spec.run_id,
                provider=self.provider_name,
                status=ExternalAgentStatus.FAILED,
                events=events,
                error=f"opencode executable not found: {self.opencode_executable}",
            )

        assert proc.stdin is not None
        proc.stdin.write(spec.full_prompt().encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

        queue: asyncio.Queue[tuple[str, bytes | None]] = asyncio.Queue()

        async def pump(name: str, stream: asyncio.StreamReader | None) -> None:
            if stream is None:
                await queue.put((name, None))
                return
            while True:
                line = await stream.readline()
                if not line:
                    await queue.put((name, None))
                    return
                await queue.put((name, line))

        pumps = [
            asyncio.create_task(pump("stdout", proc.stdout)),
            asyncio.create_task(pump("stderr", proc.stderr)),
        ]
        active_streams = 2
        idle_timeout_raw = (spec.metadata or {}).get("idle_timeout_s")
        idle_timeout = float(idle_timeout_raw) if idle_timeout_raw is not None else None
        total_deadline = started + spec.timeout_s if spec.timeout_s is not None else None
        timeout_reason = ""

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
                    value for value in (idle_timeout, remaining_total) if value is not None
                ]
                if not wait_timeout_candidates:
                    stream_name, raw_line = await queue.get()
                else:
                    stream_name, raw_line = await asyncio.wait_for(
                        queue.get(),
                        timeout=min(wait_timeout_candidates),
                    )
            except asyncio.TimeoutError:
                timed_out = True
                if not timeout_reason:
                    if total_deadline is not None and time.monotonic() >= total_deadline:
                        timeout_reason = f"total deadline reached after {spec.timeout_s:g}s"
                    else:
                        timeout_reason = f"no observable output for {idle_timeout:g}s"
                proc.kill()
                break
            if raw_line is None:
                active_streams -= 1
                continue
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if "\ufffd" in line:
                saw_fatal_error = True
                stderr_lines.append(
                    "OpenCode output contains Unicode replacement characters; "
                    "the UTF-8 producer/decoder contract is invalid"
                )
            if stream_name == "stderr":
                stderr_lines.append(line)
                if line.strip():
                    await self._emit_material(
                        spec,
                        "agent.diagnostic",
                        {"material_kind": "diagnostic", "stream": "stderr", "text": line[-4000:]},
                    )
                continue

            stdout_lines.append(line)
            event, payload = _parse_json_line(line)
            events.append(event)
            if payload is None:
                await self._emit_material(
                    spec,
                    "agent.diagnostic",
                    {"material_kind": "diagnostic", "stream": "stdout", "text": line[-4000:]},
                )
                continue
            session_id = str(payload.get("sessionID") or session_id)
            emitted_fatal_error, text = await self._publish_provider_event(
                spec,
                payload,
                tool_parent_by_id=tool_parent_by_id,
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

        await asyncio.gather(*pumps, return_exceptions=True)
        if timed_out:
            await proc.wait()
        else:
            await proc.wait()

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

        if timed_out:
            status = ExternalAgentStatus.TIMED_OUT
            error = f"opencode timed out: {timeout_reason}"
        elif proc.returncode == 0 and not saw_fatal_error:
            status = ExternalAgentStatus.SUCCEEDED
            error = ""
        else:
            status = ExternalAgentStatus.FAILED
            error = "\n".join(stderr_lines)[-4000:] or "OpenCode returned an error event"

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
                "stdout_tail": "\n".join(stdout_lines)[-4000:],
                "stderr_tail": "\n".join(stderr_lines)[-4000:],
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
