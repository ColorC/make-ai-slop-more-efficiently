# [OMNI] origin=codex domain=dashboard/ccdaemon ts=2026-07-10 type=infra status=active
"""Install Omnicompany lifecycle hooks for native Codex sessions.

Codex loads project/user hooks from ``.codex/hooks.json``.  This installer is
deliberately separate from the Claude settings installer because the config
locations, trust model, and supported event behavior differ.  Both installers
reuse the same hook implementations; the command line carries an explicit
``--provider codex`` marker so hook payloads are attributed correctly.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Scope = Literal["project", "user"]

_OWNED_COMMAND_FRAGMENT = "omnicompany.dashboard.ccdaemon.hooks."
_PROVIDER_ARGS = "--provider codex"


def _project_root() -> Path:
    from omnicompany.core.config import omni_workspace_root

    return omni_workspace_root()


def hooks_path(scope: Scope = "project") -> Path:
    if scope == "user":
        return Path.home() / ".codex" / "hooks.json"
    return _project_root() / ".codex" / "hooks.json"


def _python_cmd() -> str:
    return sys.executable.replace("\\", "/")


def _hook_command(module: str) -> str:
    return f'"{_python_cmd()}" -m {module} {_PROVIDER_ARGS}'


def _hook_command_windows(module: str) -> str:
    # Codex executes commandWindows through PowerShell.  A quoted executable at
    # the start of a PowerShell statement is only a string expression, so use
    # the call operator and single-quote the path for literal interpretation.
    python = _python_cmd().replace("'", "''")
    return f"& '{python}' -m {module} {_PROVIDER_ARGS}"


def _hook_block(module: str, *, matcher: str | None = None, status: str | None = None) -> dict:
    handler: dict[str, object] = {
        "type": "command",
        "command": _hook_command(module),
        "commandWindows": _hook_command_windows(module),
        "timeout": 30,
    }
    if status:
        handler["statusMessage"] = status
    block: dict[str, object] = {"hooks": [handler]}
    if matcher is not None:
        block["matcher"] = matcher
    return block


def _desired_hooks() -> dict[str, list[dict]]:
    base = "omnicompany.dashboard.ccdaemon.hooks"
    # Full PreToolUse/PostToolUse tracing is intentionally not part of the
    # default profile.  It starts a Python process on both sides of nearly
    # every local tool call; keep the trace module available for explicit
    # diagnostics while the default installation stays lightweight.
    return {
        "SessionStart": [
            _hook_block(
                f"{base}.session_start",
                matcher="startup|resume|clear|compact",
                status="Loading Omnicompany session context",
            ),
        ],
        "PreToolUse": [
            _hook_block(
                f"{base}.lock_pretooluse",
                matcher="Bash|apply_patch|Edit|Write",
                status="Checking Omnicompany write policy",
            ),
        ],
        "UserPromptSubmit": [
            _hook_block(f"{base}.user_prompt_submit"),
        ],
        "Stop": [
            _hook_block(f"{base}.trace"),
        ],
    }


def _is_owned_block(block: object) -> bool:
    if not isinstance(block, dict):
        return False
    for handler in block.get("hooks") or []:
        if not isinstance(handler, dict):
            continue
        command = str(handler.get("command") or "")
        if _OWNED_COMMAND_FRAGMENT in command and _PROVIDER_ARGS in command:
            return True
    return False


def _read_config(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Codex hooks config is malformed: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Codex hooks config must contain a JSON object: {path}")
    return data


def _backup(path: Path) -> Path | None:
    if not path.is_file():
        return None
    target = path.with_suffix(path.suffix + f".bak.{int(time.time())}")
    shutil.copy2(path, target)
    return target


@dataclass(frozen=True)
class InstallReport:
    settings_path: str
    backup: str | None
    hooks_added: list[str]
    hooks_unchanged: list[str]
    note: str
    requires_trust: bool = True


def install(scope: Scope = "project") -> InstallReport:
    """Merge Omnicompany hooks without replacing unrelated Codex hook config."""

    path = hooks_path(scope)
    before = _read_config(path)
    after = dict(before)
    existing_hooks = before.get("hooks") or {}
    if not isinstance(existing_hooks, dict):
        raise ValueError(f"Codex hooks field must be an object: {path}")

    desired = _desired_hooks()
    merged: dict[str, list] = {}
    all_events = set(existing_hooks) | set(desired)
    hooks_added: list[str] = []
    hooks_unchanged: list[str] = []
    for event in sorted(all_events):
        current = existing_hooks.get(event) or []
        if not isinstance(current, list):
            raise ValueError(f"Codex hooks.{event} must be an array: {path}")
        kept = [block for block in current if not _is_owned_block(block)]
        final = kept + desired.get(event, [])
        if final:
            merged[event] = final
        if event in desired:
            (hooks_unchanged if final == current else hooks_added).append(event)

    after["hooks"] = merged
    backup = None
    if after != before:
        path.parent.mkdir(parents=True, exist_ok=True)
        backup = _backup(path)
        path.write_text(json.dumps(after, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return InstallReport(
        settings_path=str(path),
        backup=str(backup) if backup else None,
        hooks_added=hooks_added,
        hooks_unchanged=hooks_unchanged,
        note=(
            f"installed Codex hooks at scope={scope}: {path}. "
            "Start Codex in this scope, open `/hooks`, review the definitions, and trust them."
        ),
    )


def uninstall(scope: Scope = "project") -> dict:
    """Remove only Omnicompany's Codex hook blocks."""

    path = hooks_path(scope)
    if not path.is_file():
        return {"settings_path": str(path), "removed": False, "backup": None}
    before = _read_config(path)
    existing_hooks = before.get("hooks") or {}
    if not isinstance(existing_hooks, dict):
        raise ValueError(f"Codex hooks field must be an object: {path}")

    merged: dict[str, list] = {}
    for event, blocks in existing_hooks.items():
        if not isinstance(blocks, list):
            raise ValueError(f"Codex hooks.{event} must be an array: {path}")
        kept = [block for block in blocks if not _is_owned_block(block)]
        if kept:
            merged[event] = kept

    after = dict(before)
    if merged:
        after["hooks"] = merged
    else:
        after.pop("hooks", None)
    if after == before:
        return {"settings_path": str(path), "removed": False, "backup": None}

    backup = _backup(path)
    if after:
        path.write_text(json.dumps(after, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        path.unlink()
    return {
        "settings_path": str(path),
        "removed": True,
        "backup": str(backup) if backup else None,
    }


def status(scope: Scope = "project") -> dict:
    path = hooks_path(scope)
    try:
        config = _read_config(path)
    except ValueError as exc:
        return {
            "settings_path": str(path),
            "installed": False,
            "hook_events": [],
            "requires_trust": True,
            "note": str(exc),
        }
    installed_events: list[str] = []
    hooks = config.get("hooks") or {}
    if isinstance(hooks, dict):
        for event, blocks in hooks.items():
            if isinstance(blocks, list) and any(_is_owned_block(block) for block in blocks):
                installed_events.append(event)
    desired_events = set(_desired_hooks())
    return {
        "settings_path": str(path),
        "installed": desired_events.issubset(installed_events),
        "hook_events": sorted(installed_events),
        "requires_trust": bool(installed_events),
        "trust_command": "/hooks" if installed_events else None,
        "provider": "codex",
    }


__all__ = ["InstallReport", "hooks_path", "install", "uninstall", "status"]
