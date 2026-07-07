# [OMNI] origin=codex domain=services/agent ts=2026-05-09 type=infrastructure
"""Codex CLI external worker adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
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


_CODEX_SANDBOX_BY_PERMISSION: dict[ExternalAgentPermissionMode, str] = {
    ExternalAgentPermissionMode.READONLY: "read-only",
    ExternalAgentPermissionMode.WORKSPACE_WRITE: "workspace-write",
    ExternalAgentPermissionMode.TRUSTED_BYPASS: "danger-full-access",
}


class CodexExecWorker(ExternalAgentWorker):
    """Run Codex through `codex exec --json`.

    This is intentionally an external-agent path, not an LLMClient provider.
    """

    provider_name = "codex"
    handles_timeout = True

    def __init__(self, *, bus: Any | None = None, codex_executable: str = "codex"):
        super().__init__(bus=bus)
        self.codex_executable = codex_executable

    def build_command(
        self,
        spec: ExternalAgentRunSpec,
        *,
        last_message_path: Path,
    ) -> list[str]:
        permission_mode = spec.normalized_permission_mode()
        cmd = [
            _resolve_executable_for_subprocess(self.codex_executable),
            "exec",
            "--ephemeral",
            "--json",
            "--cd",
            str(spec.normalized_cwd()),
            "--sandbox",
            _CODEX_SANDBOX_BY_PERMISSION[permission_mode],
            "--output-last-message",
            str(last_message_path),
        ]
        if spec.model:
            cmd.extend(["--model", spec.model])
        if spec.profile:
            cmd.extend(["--profile", spec.profile])
        if spec.output_schema_path:
            cmd.extend(["--output-schema", str(Path(spec.output_schema_path).expanduser().resolve())])
        # 调用方可经 metadata["codex_config"] 传 `-c key=value` 配置覆盖(如 research 开 tools.web_search)。
        codex_config = (spec.metadata or {}).get("codex_config")
        if isinstance(codex_config, dict):
            for key, value in codex_config.items():
                cmd.extend(["-c", f"{key}={value}"])
        cmd.append("-")
        return cmd

    async def _run_impl(self, spec: ExternalAgentRunSpec) -> ExternalAgentResult:
        cwd = spec.normalized_cwd()
        before_diff = _git_diff_stat(cwd)
        before_changed = set(_git_changed_files(cwd))
        watch_paths = spec.normalized_watch_paths()
        before_watch = _snapshot_watch_paths(cwd, watch_paths)
        with tempfile.TemporaryDirectory(prefix="omni-codex-") as tmp:
            last_message_path = Path(tmp) / "last_message.md"
            cmd = self.build_command(spec, last_message_path=last_message_path)
            events: list[ExternalAgentEvent] = [
                ExternalAgentEvent(
                    type="command",
                    message="codex exec command built",
                    payload={"argv": _redact_argv(cmd)},
                )
            ]
            env = _build_env(spec.env)

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
                    error=f"codex executable not found: {self.codex_executable}",
                )

            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(spec.full_prompt().encode("utf-8")),
                    timeout=spec.timeout_s,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return ExternalAgentResult(
                    run_id=spec.run_id,
                    provider=self.provider_name,
                    status=ExternalAgentStatus.TIMED_OUT,
                    events=events,
                    error=f"codex exec timed out after {spec.timeout_s:g}s",
                )

            stdout = stdout_b.decode("utf-8", errors="replace")
            stderr = stderr_b.decode("utf-8", errors="replace")
            events.extend(_parse_json_lines(stdout))
            if stderr.strip():
                events.append(ExternalAgentEvent(type="stderr", message=stderr[-4000:]))

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
            status = ExternalAgentStatus.SUCCEEDED if proc.returncode == 0 else ExternalAgentStatus.FAILED
            changed_files = sorted(after_changed - before_changed)
            diff_summary = _format_diff_summary(after_diff, changed_files)
            diff_summary = _append_watched_path_summary(diff_summary, watched_path_changes)
            readonly_rollback: dict[str, list[str]] | None = None
            if (
                spec.normalized_permission_mode() == ExternalAgentPermissionMode.READONLY
                and (
                    after_diff != before_diff
                    or changed_files
                    or watched_path_changes["has_changes"]
                )
            ):
                status = ExternalAgentStatus.PERMISSION_VIOLATION
                rollback_paths = sorted(
                    set(changed_files + list(watched_path_changes["created"])),
                    key=lambda path: path.count("/"),
                    reverse=True,
                )
                readonly_rollback = _rollback_new_changes(cwd, rollback_paths)
                diff_summary = _append_rollback_summary(diff_summary, readonly_rollback)

            error = ""
            if status == ExternalAgentStatus.PERMISSION_VIOLATION:
                error = "readonly permission violation: external worker changed files"
                if readonly_rollback and readonly_rollback["failed"]:
                    error += "; rollback failed for " + ", ".join(readonly_rollback["failed"])
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
                },
            )


def _build_env(extra: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
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
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), "diff", "--stat"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
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
            "Newly changed files detected by external worker:\n"
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
    return "\n\n".join(parts)


def _git_changed_files(cwd: Path) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), "status", "--porcelain=v1", "--untracked-files=all"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
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
    root = cwd.resolve()
    rolled_back: list[str] = []
    failed: list[str] = []
    for rel_path in paths:
        target = (root / rel_path).resolve()
        if not _is_relative_to(target, root):
            failed.append(rel_path)
            continue
        try:
            if _git_path_is_tracked(root, rel_path):
                proc = subprocess.run(
                    ["git", "-C", str(root), "restore", "--", rel_path],
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10,
                )
                if proc.returncode == 0:
                    rolled_back.append(rel_path)
                else:
                    failed.append(rel_path)
                continue
            if target.is_dir():
                shutil.rmtree(target)
                rolled_back.append(rel_path)
            elif target.exists():
                target.unlink()
                rolled_back.append(rel_path)
            else:
                rolled_back.append(rel_path)
        except Exception:
            failed.append(rel_path)
    return {"rolled_back": rolled_back, "failed": failed}


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
