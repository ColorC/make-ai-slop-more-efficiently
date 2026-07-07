# [OMNI] origin=codex domain=services/agent ts=2026-05-09 type=infrastructure
"""Claude Code SDK external worker adapter."""

from __future__ import annotations

import dataclasses
import os
import re
import time
from contextlib import contextmanager
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
    _rollback_new_changes,
    _snapshot_watch_paths,
)
from omnicompany.packages.services._core.agent.external_workers.trace import ExternalWorkerTraceMirror


_CLAUDE_PERMISSION_BY_MODE: dict[ExternalAgentPermissionMode, str] = {
    # Readonly runs stay in normal answer mode so the worker can return source
    # code as final text, but only read tools are exposed below.
    ExternalAgentPermissionMode.READONLY: "default",
    # Workspace-write runs are intended to be long-running implementation
    # workers. Claude's default mode still asks for per-edit approval, which
    # deadlocks non-interactive `omni worker run`; acceptEdits allows file edits
    # while keeping trusted bypass as a separate explicit mode.
    ExternalAgentPermissionMode.WORKSPACE_WRITE: "acceptEdits",
    ExternalAgentPermissionMode.TRUSTED_BYPASS: "bypassPermissions",
}

_READONLY_DISALLOWED_TOOLS = ["Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"]
_READONLY_ALLOWED_TOOLS = {"Read", "Grep", "Glob", "LS", "NotebookRead", "WebFetch"}
# headless 外部 worker 不能用交互/委托类工具:Plan-mode 要批准、AskUserQuestion 要用户、Task 子委托
# 在 SDK headless 下会中断(实测:worker 用 Task 去"thorough 探索"时在写文件前被 "[Request interrupted by user]"
# 打断)。外部 worker 本就是确定性自动化单元,移除这些工具 = 强制它直接 Read/Grep/Write,不靠 prompt 求它别用。
_HEADLESS_INCOMPATIBLE_TOOLS = ["Task", "EnterPlanMode", "ExitPlanMode", "AskUserQuestion"]


@dataclasses.dataclass
class _PermissionFallback:
    behavior: str
    message: str = ""
    interrupt: bool = False


async def _readonly_can_use_tool(tool_name: str, tool_input: dict[str, Any], context: Any) -> Any:
    del tool_input, context
    if tool_name in _READONLY_ALLOWED_TOOLS:
        return _permission_allow()
    return _permission_deny(
        message=f"readonly external worker may not use tool: {tool_name}",
        interrupt=True,
    )


async def _workspace_write_can_use_tool(tool_name: str, tool_input: dict[str, Any], context: Any) -> Any:
    del context
    if tool_name != "Bash":
        return _permission_allow()
    command = str(tool_input.get("command") or "").strip()
    if _is_workspace_write_safe_bash(command):
        return _permission_allow()
    return _permission_deny(
        message=(
            "workspace-write external worker may only run narrow validation/search "
            "Bash commands (rg / git status|diff|ls-files / read-only probes). "
            "This command is blocked — do NOT retry it. Verify by reading source files "
            "(Read/Grep/Glob) instead of running shell commands."
        ),
        # 只拒绝这一次工具调用, 不掐断整个会话(interrupt=True 会让 worker 一试被挡的 Bash
        # 就整体 "[Request interrupted by user]" 死掉 —— headless 自动化 worker 不该这么脆)。
        interrupt=False,
    )


def _permission_allow() -> Any:
    try:
        from claude_agent_sdk.types import PermissionResultAllow  # type: ignore
    except Exception:
        return _PermissionFallback(behavior="allow")
    return PermissionResultAllow()


def _permission_deny(*, message: str, interrupt: bool) -> Any:
    try:
        from claude_agent_sdk.types import PermissionResultDeny  # type: ignore
    except Exception:
        return _PermissionFallback(behavior="deny", message=message, interrupt=interrupt)
    return PermissionResultDeny(message=message, interrupt=interrupt)


# WORK-LIFECYCLE: 安全的 omni 生命周期子命令(只读 / task·review 状态管理), headless worker 可自闭环。
# 只白名单这些精确 "<group> <sub>" 对; 不放开任意 omni / 任意 shell。
_SAFE_OMNI_LIFECYCLE = (
    "review submit", "review list", "review annotate",
    "task complete", "task status", "task start", "task next", "task show",
    "task list", "task assign", "task board", "task bindings", "task update",
    "plan gate", "plan show", "plan current", "plan list", "plan report",
    "notes ls", "notes read", "human inbox",
)


def _is_safe_omni_lifecycle_bash(command: str) -> bool:
    c = f" {command.strip()} "
    if "omni " not in c and "omnicompany.cli.main" not in c:
        return False
    return any(f" {pair} " in c or f" {pair}" in c for pair in _SAFE_OMNI_LIFECYCLE)


def _is_workspace_write_safe_bash(command: str) -> bool:
    normalized = command.replace("\\", "/").lower()
    if not normalized:
        return False
    normalized_without_null_stderr = (
        normalized.replace("2>/dev/null", "")
        .replace("2> /dev/null", "")
        .replace("2>&1", "")
        .replace("2> &1", "")
    )
    forbidden = [
        "remove-item",
        " del ",
        "erase ",
        " rm ",
        " rmdir",
        "git reset",
        "git checkout",
        "git clean",
        "p4 submit",
        "p4 revert",
        "p4 delete",
        ">",
        ">>",
        "| set-content",
        "| out-file",
    ]
    padded = f" {normalized_without_null_stderr} "
    if any(item in padded for item in forbidden):
        return False
    if normalized.startswith(("rg ", "git status", "git diff", "git ls-files")):
        return True
    # WORK-LIFECYCLE: 放行少数**安全的 omni 生命周期命令**, 让 headless worker 能自己闭环
    # (提交物料审阅 / 自报 task 完成), 而非放开任意 shell。forbidden 列表(>, rm, git reset…)
    # 仍先拦截; 这里只白名单 review/task/plan/notes 的只读或状态管理子命令。
    if _is_safe_omni_lifecycle_bash(normalized_without_null_stderr):
        return True
    if normalized.startswith("python ") and "/app/tool/prefab-workstation/scripts/" in normalized:
        return True
    if (
        normalized.startswith("cd ")
        and "app/tool/prefab-workstation" in normalized
        and "&& python scripts/" in normalized
    ):
        return _pipeline_tail_is_readonly(normalized_without_null_stderr)
    if normalized.startswith("cd ") and "app/tool/prefab-workstation" in normalized and " python " in normalized:
        return "/scripts/" in normalized
    if _is_readonly_shell_probe(normalized_without_null_stderr):
        return True
    return False


def _is_readonly_shell_probe(command: str) -> bool:
    """Allow small evidence-gathering shell snippets without edit capability.

    Claude Code naturally reaches for `ls`, `wc -l`, and short `for` loops while
    inspecting a corpus. Keeping those blocked makes non-interactive workers
    abort before producing useful output, but allowing arbitrary Bash would
    defeat the worker boundary. This parser intentionally accepts only simple
    read-only commands and loop scaffolding after redirection/destructive tokens
    have already been filtered by `_is_workspace_write_safe_bash`.
    """

    stripped_strings = re.sub(r'"[^"]*"|\'[^\']*\'', " ", command)
    stripped_strings = re.sub(r"\$[a-zA-Z_][a-zA-Z0-9_]*", " ", stripped_strings)
    if any(token in stripped_strings for token in ("`", "$(", ">", "<")):
        return False
    allowed_commands = {
        "cat",
        "echo",
        "find",
        "grep",
        "head",
        "ls",
        "pwd",
        "rg",
        "sed",
        "tail",
        "test",
        "wc",
    }
    allowed_scaffolding = {"do", "done", "then", "fi", "else"}
    segments = [segment.strip() for segment in re.split(r"&&|\|\||\||[;\n]+", stripped_strings)]
    meaningful_segments = [segment for segment in segments if segment]
    if not meaningful_segments:
        return False
    for segment in meaningful_segments:
        if segment in allowed_scaffolding:
            continue
        if segment.startswith("for ") and " in " in f" {segment} ":
            continue
        if segment.startswith("if "):
            condition = segment[3:].strip()
            if condition.startswith(("test ", "[ ")) or condition in {"true", "false"}:
                continue
            return False
        command_name = segment.split()[0]
        if command_name not in allowed_commands:
            return False
    return True


def _pipeline_tail_is_readonly(command: str) -> bool:
    if "|" not in command:
        return True
    allowed_pipe_commands = {"cat", "grep", "head", "sed", "tail", "wc"}
    for segment in command.split("|")[1:]:
        command_name = segment.strip().split(maxsplit=1)[0] if segment.strip() else ""
        if command_name not in allowed_pipe_commands:
            return False
    return True


class ClaudeCodeSdkWorker(ExternalAgentWorker):
    """Run Claude Code through `claude-agent-sdk`.

    This intentionally does not reuse dashboard chat's bypass default. Worker
    permissions are driven by `ExternalAgentRunSpec.permission_mode`.
    """

    provider_name = "claude-code"

    def __init__(self, *, bus: Any | None = None, sdk_module: Any | None = None):
        super().__init__(bus=bus)
        self._sdk_module = sdk_module

    def build_options_kwargs(self, spec: ExternalAgentRunSpec) -> dict[str, Any]:
        is_readonly = spec.normalized_permission_mode() == ExternalAgentPermissionMode.READONLY
        kwargs = {
            "system_prompt": {"type": "preset", "preset": "claude_code"},
            "tools": sorted(_READONLY_ALLOWED_TOOLS - {"NotebookRead", "WebFetch"})
            if is_readonly
            else {"type": "preset", "preset": "claude_code"},
            # External workers should be deterministic automation units. Avoid
            # user/project/local Claude hooks here; Omnicompany supplies its own
            # trace/audit envelope around the run.
            "setting_sources": [],
            "permission_mode": _CLAUDE_PERMISSION_BY_MODE[spec.normalized_permission_mode()],
            "cwd": str(spec.normalized_cwd()),
            "model": spec.model,
        }
        if is_readonly:
            kwargs["disallowed_tools"] = list(_READONLY_DISALLOWED_TOOLS) + list(_HEADLESS_INCOMPATIBLE_TOOLS)
            kwargs["can_use_tool"] = _readonly_can_use_tool
        elif spec.normalized_permission_mode() == ExternalAgentPermissionMode.WORKSPACE_WRITE:
            kwargs["can_use_tool"] = _workspace_write_can_use_tool
            kwargs["disallowed_tools"] = list(_HEADLESS_INCOMPATIBLE_TOOLS)
        return kwargs

    def build_prompt(self, spec: ExternalAgentRunSpec) -> str:
        prompt = spec.full_prompt()
        if spec.normalized_permission_mode() == ExternalAgentPermissionMode.READONLY:
            return (
                "Readonly external-worker run. Do not edit, write, delete, move, "
                "or generate files. Use inspection and reasoning only.\n\n"
                f"{prompt}"
            )
        return prompt

    async def _run_impl(self, spec: ExternalAgentRunSpec) -> ExternalAgentResult:
        cwd = spec.normalized_cwd()
        before_diff = _git_diff_stat(cwd)
        before_changed = set(_git_changed_files(cwd))
        watch_paths = spec.normalized_watch_paths()
        before_watch = _snapshot_watch_paths(cwd, watch_paths)
        before_session_state = _snapshot_claude_session_state(cwd)
        casdk = self._sdk_module or _import_claude_agent_sdk()
        events: list[ExternalAgentEvent] = []
        final_parts: list[str] = []
        status = ExternalAgentStatus.SUCCEEDED
        error = ""
        worker_env = _build_env(spec.env)
        started = time.time()
        trace_mirror = ExternalWorkerTraceMirror(spec)
        trace_mirror.emit_started()

        with _temporary_env(worker_env):
            opts = casdk.ClaudeAgentOptions(**self.build_options_kwargs(spec))
            client = casdk.ClaudeSDKClient(options=opts)

            try:
                await client.connect()
                await client.query(self.build_prompt(spec), session_id=spec.run_id)
                async for msg in client.receive_response():
                    event = _message_to_event(msg)
                    events.append(event)
                    trace_mirror.mirror_claude_sdk_event(event)
                    if event.type == "assistant.text" and event.message:
                        final_parts.append(event.message)
                    result_error = _result_event_error(event.payload) if event.type == "result" else ""
                    if result_error:
                        status = ExternalAgentStatus.FAILED
                        error = result_error
                        if event.payload.get("result"):
                            final_parts.append(str(event.payload.get("result")))
                        break
                    if event.type == "result" and event.payload.get("result"):
                        final_parts.append(str(event.payload.get("result")))
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    pass

        after_diff = _git_diff_stat(cwd)
        after_changed = set(_git_changed_files(cwd))
        after_watch = _snapshot_watch_paths(cwd, watch_paths)
        watched_path_changes = _diff_watch_snapshots(before_watch, after_watch)
        changed_files = sorted(after_changed - before_changed)
        diff_summary = _format_diff_summary(after_diff, changed_files)
        diff_summary = _append_watched_path_summary(diff_summary, watched_path_changes)
        readonly_rollback: dict[str, list[str]] | None = None
        readonly_ignored_session_cleanup: dict[str, list[str]] | None = None
        allowed_changed_files: list[str] = []
        if spec.normalized_permission_mode() == ExternalAgentPermissionMode.READONLY:
            if after_diff != before_diff or changed_files or watched_path_changes["has_changes"]:
                allowed_changed_files = [path for path in changed_files if _is_claude_session_artifact(path)]
                blocking_changed_files = [
                    path for path in changed_files if path not in set(allowed_changed_files)
                ]
                rollback_paths = sorted(
                    set(changed_files + list(watched_path_changes["created"])),
                    key=lambda path: path.count("/"),
                    reverse=True,
                )
                readonly_rollback = _rollback_new_changes(cwd, rollback_paths)
                diff_summary = _append_rollback_summary(diff_summary, readonly_rollback)
                if (
                    blocking_changed_files
                    or watched_path_changes["has_changes"]
                    or readonly_rollback["failed"]
                ):
                    status = ExternalAgentStatus.PERMISSION_VIOLATION
                    error = "readonly permission violation: external worker changed files"
                if readonly_rollback["failed"]:
                    error += "; rollback failed for " + ", ".join(readonly_rollback["failed"])
                if allowed_changed_files:
                    diff_summary += (
                        "\n\nAllowed Claude metadata files detected and rolled back:\n"
                        + "\n".join(f"- {path}" for path in allowed_changed_files)
                    )
            readonly_ignored_session_cleanup = _cleanup_ignored_claude_session_artifacts(
                cwd,
                before_session_state,
            )
            cleaned = (
                readonly_ignored_session_cleanup["removed"]
                + readonly_ignored_session_cleanup["restored"]
            )
            if cleaned:
                diff_summary += (
                    "\n\nIgnored Claude metadata files cleaned after readonly run:\n"
                    + "\n".join(f"- {path}" for path in cleaned)
                )

        final_text = _dedupe_text_parts(final_parts)
        raw = {
            "preexisting_changed_files_count": len(before_changed),
            "after_changed_files_count": len(after_changed),
            "after_rollback_changed_files_count": len(_git_changed_files(cwd)),
            "watch_paths": _relative_watch_paths(cwd, watch_paths),
            "watched_path_changes": watched_path_changes,
            "readonly_rollback": readonly_rollback,
            "readonly_allowed_changed_files": allowed_changed_files,
            "readonly_ignored_session_cleanup": readonly_ignored_session_cleanup,
            "external_worker_trace": {
                "trace_id": trace_mirror.trace_id,
                "db_path": str(trace_mirror.db_path),
            },
            "worker_env_keys": sorted(
                key for key in worker_env if key.startswith(("PYTHON", "NO_COLOR", "FORCE_COLOR", "OMNI_"))
            ),
        }
        duration_ms = (time.time() - started) * 1000
        trace_mirror.emit_completed(
            status=status.value,
            error=error,
            changed_files=changed_files,
            diff_summary=diff_summary,
            duration_ms=duration_ms,
            raw=raw,
        )
        return ExternalAgentResult(
            run_id=spec.run_id,
            provider=self.provider_name,
            status=status,
            final_text=final_text,
            events=events,
            changed_files=changed_files,
            diff_summary=diff_summary,
            error=error,
            raw=raw,
        )


def _import_claude_agent_sdk() -> Any:
    try:
        import claude_agent_sdk as casdk  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "claude-agent-sdk is not installed; cannot run claude-code external worker"
        ) from exc
    return casdk


@contextmanager
def _temporary_env(env: dict[str, str]):
    previous: dict[str, str | None] = {key: os.environ.get(key) for key in env}
    try:
        os.environ.update(env)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _message_to_event(msg: Any) -> ExternalAgentEvent:
    payload = _message_payload(msg)
    kind = payload.get("kind") or type(msg).__name__
    content = payload.get("content")
    if isinstance(content, list):
        text = "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
        if text:
            return ExternalAgentEvent(type="assistant.text", message=text, payload=payload)
    if kind == "ResultMessage" or str(kind).lower() == "result":
        return ExternalAgentEvent(type="result", payload=payload)
    return ExternalAgentEvent(type=str(kind), payload=payload)


def _message_payload(msg: Any) -> dict[str, Any]:
    if dataclasses.is_dataclass(msg):
        return dataclasses.asdict(msg)
    if hasattr(msg, "model_dump"):
        return msg.model_dump()
    if hasattr(msg, "__dict__"):
        return dict(vars(msg))
    return {"message": str(msg)}


def _result_event_error(payload: dict[str, Any]) -> str:
    error = payload.get("error")
    if error:
        return str(error)
    if payload.get("is_error") is True:
        result = str(payload.get("result") or "").strip()
        return result or "claude-code result reported is_error=true"
    subtype = str(payload.get("subtype") or "").strip().lower()
    if subtype in {"error", "failed", "failure", "error_during_execution"}:
        result = str(payload.get("result") or payload.get("message") or "").strip()
        return result or f"claude-code result subtype={subtype}"
    if str(payload.get("stop_reason") or "").strip().lower() == "tool_use" and not payload.get("result"):
        return "claude-code stopped during tool use without a final result"
    return ""


def _is_claude_session_artifact(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return normalized.startswith(".omni/sessions/") and (
        normalized.endswith(".json") or normalized == ".omni/sessions/_current.txt"
    )


def _snapshot_claude_session_state(cwd: Path) -> dict[str, Any]:
    session_dir = cwd / ".omni" / "sessions"
    current_path = session_dir / "_current.txt"
    json_files: set[str] = set()
    if session_dir.exists():
        json_files = {
            _relative_posix(path, cwd)
            for path in session_dir.glob("*.json")
            if path.is_file()
        }
    return {
        "json_files": json_files,
        "current_exists": current_path.exists(),
        "current_text": current_path.read_text(encoding="utf-8") if current_path.exists() else "",
    }


def _cleanup_ignored_claude_session_artifacts(
    cwd: Path,
    before_state: dict[str, Any],
) -> dict[str, list[str]]:
    session_dir = cwd / ".omni" / "sessions"
    removed: list[str] = []
    restored: list[str] = []
    failed: list[str] = []
    before_json = set(before_state.get("json_files") or set())

    if session_dir.exists():
        for path in session_dir.glob("*.json"):
            if not path.is_file():
                continue
            rel_path = _relative_posix(path, cwd)
            if rel_path in before_json:
                continue
            try:
                path.unlink()
                removed.append(rel_path)
            except Exception:
                failed.append(rel_path)

    current_path = session_dir / "_current.txt"
    current_rel = ".omni/sessions/_current.txt"
    try:
        if before_state.get("current_exists"):
            previous = str(before_state.get("current_text") or "")
            if not current_path.exists() or current_path.read_text(encoding="utf-8") != previous:
                current_path.parent.mkdir(parents=True, exist_ok=True)
                current_path.write_text(previous, encoding="utf-8")
                restored.append(current_rel)
        elif current_path.exists():
            current_path.unlink()
            removed.append(current_rel)
    except Exception:
        failed.append(current_rel)

    return {"removed": sorted(removed), "restored": sorted(restored), "failed": sorted(failed)}


def _relative_posix(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _dedupe_text_parts(parts: list[str]) -> str:
    seen: set[str] = set()
    deduped: list[str] = []
    for part in parts:
        text = part.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return "\n".join(deduped)


__all__ = ["ClaudeCodeSdkWorker"]
