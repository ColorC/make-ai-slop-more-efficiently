# [OMNI] origin=kimi domain=services/agent ts=2026-07-18 type=infrastructure
# [OMNI] material_id="material:core.agent.external_worker.kimi_adapter.py"
"""Kimi CLI adapter with line-streamed EventBus materials.

Kimi's ``-p --output-format stream-json`` contract is newline-delimited JSON.
The adapter consumes one line at a time and publishes one semantic material at
a time; it never waits for the full process output before exposing progress.

Two Kimi-specific constraints shape this adapter:

- The prompt must be passed inline via ``-p`` (stdin is ignored in prompt
  mode), so the command event redacts the prompt value and over-long prompts
  are rejected up front instead of hitting Windows command-line limits.
- Kimi CLI has no readonly/sandbox flag in ``-p`` mode (it always runs in auto
  permission mode, and ``--yolo``/``--auto``/``--plan`` are mutually exclusive
  with ``-p``). READONLY is therefore enforced the same way as the post-hoc
  audit in the other adapters: the run completes, git diff / watch-path
  snapshots are compared, and any change is preserved and reported as
  PERMISSION_VIOLATION because the time-window delta does not prove which
  process owned it. This is a detective control, not a preventive one.
"""

from __future__ import annotations

import asyncio
import json
import time
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


# Kimi takes the prompt inline via -p. Windows CreateProcess command lines cap
# out near 32k characters, so reject prompts that would come anywhere close.
_MAX_INLINE_PROMPT_CHARS = 30000


class KimiExecWorker(ExternalAgentWorker):
    """Run Kimi CLI as an audited, streaming external Agent."""

    provider_name = "kimi"
    # There is deliberately no implicit 60-second or total runtime cutoff.
    # Callers may opt into an inactivity limit with metadata.idle_timeout_s.
    handles_timeout = True

    def __init__(self, *, bus: Any | None = None, kimi_executable: str = "kimi"):
        super().__init__(bus=bus)
        self.kimi_executable = kimi_executable

    def build_command(self, spec: ExternalAgentRunSpec) -> list[str]:
        if spec.output_schema_path:
            raise ValueError(
                "Kimi worker does not emulate schemas in prompts; use a native tool call "
                "or let the Agent write a real file and lint it"
            )
        prompt = spec.full_prompt()
        if len(prompt) > _MAX_INLINE_PROMPT_CHARS:
            raise ValueError(
                f"kimi worker passes the prompt inline via -p; prompt is {len(prompt)} chars, "
                f"over the {_MAX_INLINE_PROMPT_CHARS} char Windows command-line safety limit; "
                "shorten the prompt or attached context"
            )
        cmd = [
            _resolve_executable_for_subprocess(self.kimi_executable),
            "-p",
            prompt,
            "--output-format",
            "stream-json",
        ]
        if spec.model:
            cmd.extend(["-m", spec.model])
        session_id = str((spec.metadata or {}).get("session_id") or "").strip()
        if session_id:
            cmd.extend(["--session", session_id])
        # There is deliberately no permission flag: kimi -p always runs in auto
        # permission mode. READONLY is enforced by the post-run audit below.
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
        tool_parent_by_id: dict[str, str] = {}
        tool_name_by_id: dict[str, str] = {}
        session_id = ""
        timed_out = False

        cmd = self.build_command(spec)
        env = _build_env(spec.env)
        events.append(
            ExternalAgentEvent(
                type="command",
                message="kimi -p command built",
                payload={"argv": _redact_argv(cmd)},
            )
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(cwd),
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return ExternalAgentResult(
                run_id=spec.run_id,
                provider=self.provider_name,
                status=ExternalAgentStatus.FAILED,
                events=events,
                error=f"kimi executable not found: {self.kimi_executable}",
            )

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
            session_id = str(payload.get("session_id") or session_id)
            text = await self._publish_provider_event(
                spec,
                payload,
                tool_parent_by_id=tool_parent_by_id,
                tool_name_by_id=tool_name_by_id,
            )
            if text:
                final_parts.append(text)

        await asyncio.gather(*pumps, return_exceptions=True)
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
            error = f"kimi timed out: {timeout_reason}"
        elif proc.returncode == 0:
            status = ExternalAgentStatus.SUCCEEDED
            error = ""
        else:
            status = ExternalAgentStatus.FAILED
            error = "\n".join(stderr_lines)[-4000:] or f"kimi exited with code {proc.returncode}"

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
            final_text=_dedupe_parts(final_parts),
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
        tool_name_by_id: dict[str, str],
    ) -> str:
        role = str(payload.get("role") or "")

        if role == "assistant":
            text_out = ""
            content = payload.get("content")
            if isinstance(content, str) and content.strip():
                text = content.strip()
                await self._emit_material(
                    spec,
                    "agent.text.output",
                    {"material_kind": "text-output", "text": text},
                )
                text_out = text
            tool_calls = payload.get("tool_calls")
            if isinstance(tool_calls, list):
                for call in tool_calls:
                    if not isinstance(call, dict):
                        continue
                    tool_id = str(call.get("id") or "")
                    function = call.get("function") if isinstance(call.get("function"), dict) else {}
                    tool_name = str(function.get("name") or "?")
                    args = _parse_tool_arguments(function.get("arguments"))
                    call_id = await self._emit_material(
                        spec,
                        "agent.tool.call",
                        {
                            "material_kind": "tool-call",
                            "tool": tool_name,
                            "tool_use_id": tool_id or None,
                            "args": _summarize_args(args),
                        },
                        tags=[f"tool:{tool_name}"],
                    )
                    if tool_id:
                        tool_name_by_id[tool_id] = tool_name
                        if call_id:
                            tool_parent_by_id[tool_id] = call_id
            return text_out
        if role == "tool":
            # A tool result is an observable Agent event, not a terminal
            # provider failure; kimi may recover in a later step. The stream
            # carries no error flag, so verdict stays "ok"/"empty" and the
            # process exit code remains the fatal signal.
            tool_id = str(payload.get("tool_call_id") or "")
            tool_name = tool_name_by_id.get(tool_id, "?")
            content = payload.get("content")
            await self._emit_material(
                spec,
                "agent.tool.result",
                {
                    "material_kind": "tool-result",
                    "tool": tool_name,
                    "tool_use_id": tool_id or None,
                    "verdict": "ok" if content else "empty",
                    "result": _summarize_result(content),
                },
                parent_id=tool_parent_by_id.get(tool_id),
                tags=[f"tool:{tool_name}"] if tool_name != "?" else None,
            )
            return ""
        if role == "meta":
            # e.g. type=session.resume_hint carries the resumable session_id;
            # the run loop already captured it into raw["session_id"].
            await self._emit_material(
                spec,
                "agent.provider.event",
                {
                    "material_kind": "provider-event",
                    "provider_event_type": str(payload.get("type") or "meta"),
                    "session_id": payload.get("session_id") or None,
                },
            )
            return ""

        await self._emit_material(
            spec,
            "agent.provider.event",
            {"material_kind": "provider-event", "provider_event_type": role or "unknown"},
        )
        return ""


def _parse_json_line(line: str) -> tuple[ExternalAgentEvent, dict[str, Any] | None]:
    stripped = line.strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return ExternalAgentEvent(type="stdout", message=stripped), None
    if not isinstance(payload, dict):
        return ExternalAgentEvent(type="json", payload={"value": payload}), None
    role = str(payload.get("role") or "json")
    meta_type = str(payload.get("type") or "") if role == "meta" else ""
    kind = f"{role}.{meta_type}" if meta_type else role
    content = payload.get("content")
    message = content if isinstance(content, str) else ""
    return ExternalAgentEvent(type=kind, message=message, payload=payload), payload


def _parse_tool_arguments(raw: Any) -> dict[str, Any]:
    """Kimi emits tool_call function.arguments as a JSON string, not an object."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw_arguments": raw[:1500]}
        if isinstance(parsed, dict):
            return parsed
        return {"_raw_arguments": raw[:1500]}
    return {}


def _dedupe_parts(parts: list[str]) -> str:
    out: list[str] = []
    for part in parts:
        text = part.strip()
        if text and text not in out:
            out.append(text)
    return "\n\n".join(out)


def _redact_argv(argv: list[str]) -> list[str]:
    redacted = list(argv)
    for index, item in enumerate(redacted[:-1]):
        if item == "-p":
            redacted[index + 1] = f"<prompt:{len(redacted[index + 1])} chars>"
        elif item == "--session":
            redacted[index + 1] = "<session-id>"
    return redacted


__all__ = ["KimiExecWorker"]
