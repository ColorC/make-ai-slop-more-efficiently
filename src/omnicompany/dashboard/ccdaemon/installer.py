# [OMNI] origin=claude-code ts=2026-05-02 type=infra
# [OMNI] material_id="material:dashboard.cc_wrapper.settings_installer.config_writer.py"
"""Idempotent installer that wires omnicompany MCP server + hooks into Claude Code's
settings.json. Single source of truth — both the `omni cc` CLI and the dashboard's
install button call the functions in this module.

We default to **project-scoped** install (`<repo>/.claude/settings.json`) so the
integration only activates when claude is run from inside the omnicompany repo.
Pass `scope="user"` to install into `~/.claude/settings.json` instead (less safe).

A backup is always written before any change: `<settings>.bak.<timestamp>`.
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

OMNI_MCP_KEY = "omnicompany"
PYTHON_CMD_KEY = "_omni_python_cmd"  # comment-like marker we set in our managed slice

# These names tag every entry we own so `uninstall` can remove just our stuff.
_HOOK_MARK = "[omnicompany]"


def _python_cmd() -> str:
    """Best-effort path to the venv python (so `python` resolves correctly when
    settings.json is consumed by claude in any cwd)."""
    return sys.executable.replace("\\", "/")


def _project_root() -> Path:
    from omnicompany.core.config import omni_workspace_root
    return omni_workspace_root()


def settings_path(scope: Scope = "project") -> Path:
    """Path to the hooks-bearing settings.json.

    NOTE: MCP servers are NOT registered here in current Claude Code (v2.1+).
    See `mcp_path()` for the canonical MCP location (`.mcp.json` / `~/.claude.json`).
    """
    if scope == "user":
        return Path.home() / ".claude" / "settings.json"
    return _project_root() / ".claude" / "settings.json"


def mcp_path(scope: Scope = "project") -> Path:
    """Canonical MCP server registry file.

    Project scope writes to `.mcp.json` at repo root (the file `claude mcp add -s project`
    writes to). User scope writes to `~/.claude.json` (where `claude mcp add -s user` writes).
    """
    if scope == "user":
        return Path.home() / ".claude.json"
    return _project_root() / ".mcp.json"


def _hook_command(module_path: str) -> str:
    """Build a portable `python -m <module>` invocation. Claude expands `${PWD}` etc.
    but a frozen path to our venv python is more reliable."""
    return f'"{_python_cmd()}" -m {module_path}'


@dataclass(frozen=True)
class InstallReport:
    settings_path: str
    backup: str | None
    mcp_added: bool
    hooks_added: list[str]
    hooks_unchanged: list[str]
    note: str


def _hook_block(event: str, module: str, matcher: str | None = None) -> dict:
    block: dict = {
        "hooks": [{
            "type": "command",
            "command": _hook_command(module),
            "_omnicompany": _HOOK_MARK,  # ownership marker
        }],
    }
    if matcher is not None:
        block["matcher"] = matcher
    return block


def _desired_mcp_entry() -> dict:
    """The omnicompany MCP server entry, in `.mcp.json` shape."""
    return {
        "type": "stdio",
        "command": _python_cmd(),
        "args": ["-m", "omnicompany.dashboard.ccdaemon.mcp_server"],
        "env": {},
        "_omnicompany": _HOOK_MARK,
    }


def _desired_settings_slice() -> dict:
    """The fragments we own in settings.json (HOOKS only — MCP lives in .mcp.json)."""
    return {
        "hooks": {
            "SessionStart": [_hook_block("SessionStart", "omnicompany.dashboard.ccdaemon.hooks.session_start")],
            "PreCompact":   [_hook_block("PreCompact",   "omnicompany.dashboard.ccdaemon.hooks.compact")],
            "PreToolUse":   [
                _hook_block("PreToolUse",  "omnicompany.dashboard.ccdaemon.hooks.lock_pretooluse",
                            matcher="Bash|Edit|Write|MultiEdit|NotebookEdit"),
            ],
            "UserPromptSubmit": [
                _hook_block("UserPromptSubmit", "omnicompany.dashboard.ccdaemon.hooks.user_prompt_submit"),
            ],
            "Stop":         [_hook_block("Stop",        "omnicompany.dashboard.ccdaemon.hooks.trace")],
        },
    }


def _is_omni_block(block) -> bool:
    if not isinstance(block, dict):
        return False
    if block.get("_omnicompany") == _HOOK_MARK:
        return True
    hooks = block.get("hooks") or []
    return any(isinstance(h, dict) and h.get("_omnicompany") == _HOOK_MARK for h in hooks)


def _install_mcp(scope: Scope) -> tuple[bool, str | None]:
    """Write our MCP server entry to `.mcp.json` (project) or `~/.claude.json` (user).
    Idempotent. Returns (changed, mcp_path)."""
    p = mcp_path(scope)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if p.is_file():
        try:
            existing = json.loads(p.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            existing = {}
    desired = _desired_mcp_entry()
    mcp_servers = existing.get("mcpServers") or {}
    changed = mcp_servers.get(OMNI_MCP_KEY) != desired
    mcp_servers[OMNI_MCP_KEY] = desired
    existing["mcpServers"] = mcp_servers
    if changed:
        p.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    return changed, str(p)


def install(scope: Scope = "project") -> InstallReport:
    """Idempotent merge — hooks into settings.json, MCP server into .mcp.json.

    Both files preserve any pre-existing user keys; we only add/update the
    fragments tagged with our `_omnicompany` marker.
    """
    p = settings_path(scope)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    backup: Path | None = None
    if p.is_file():
        try:
            existing = json.loads(p.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            existing = {}
        backup = p.with_suffix(p.suffix + f".bak.{int(time.time())}")
        shutil.copy2(p, backup)
    else:
        backup = None

    desired = _desired_settings_slice()
    hooks_added: list[str] = []
    hooks_unchanged: list[str] = []

    # Strip any obsolete `mcpServers.omnicompany` entry from settings.json (we used to
    # write here in earlier versions; current Claude Code reads MCP from .mcp.json only).
    legacy_mcp = (existing.get("mcpServers") or {})
    if OMNI_MCP_KEY in legacy_mcp:
        legacy_mcp.pop(OMNI_MCP_KEY)
        if not legacy_mcp:
            existing.pop("mcpServers", None)
        else:
            existing["mcpServers"] = legacy_mcp

    # Hooks merge — drop every previously owned block, including events removed
    # from the current profile, then add the fresh minimal defaults.
    hooks_existing = existing.get("hooks") or {}
    merged_hooks: dict[str, list] = {}
    all_events = set(hooks_existing) | set(desired["hooks"])
    for event in sorted(all_events):
        cur_list = hooks_existing.get(event) or []
        kept = [b for b in cur_list if not _is_omni_block(b)]
        new_list = kept + desired["hooks"].get(event, [])
        if new_list:
            merged_hooks[event] = new_list
        if event in desired["hooks"]:
            if new_list != cur_list:
                hooks_added.append(event)
            else:
                hooks_unchanged.append(event)
    if merged_hooks:
        existing["hooks"] = merged_hooks
    else:
        existing.pop("hooks", None)

    p.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

    # And install the MCP server entry to its canonical location.
    mcp_changed, mcp_pth = _install_mcp(scope)

    return InstallReport(
        settings_path=str(p),
        backup=str(backup) if backup else None,
        mcp_added=bool(mcp_changed),
        hooks_added=hooks_added,
        hooks_unchanged=hooks_unchanged,
        note=(f"installed to scope={scope}; hooks→{p}; mcp→{mcp_pth}; "
              f"equivalent CLI: `omni cc install --scope {scope}`"),
    )


def _uninstall_mcp(scope: Scope) -> bool:
    """Remove our MCP server entry from .mcp.json. Returns True if changed."""
    p = mcp_path(scope)
    if not p.is_file():
        return False
    try:
        cur = json.loads(p.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return False
    mcp = cur.get("mcpServers") or {}
    if OMNI_MCP_KEY not in mcp:
        return False
    mcp.pop(OMNI_MCP_KEY)
    if mcp:
        cur["mcpServers"] = mcp
    else:
        cur.pop("mcpServers", None)
    if cur:
        p.write_text(json.dumps(cur, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        # If the file is now empty (no other top-level keys), delete it.
        try:
            p.unlink()
        except OSError:
            p.write_text("{}\n", encoding="utf-8")
    return True


def uninstall(scope: Scope = "project") -> dict:
    """Remove only the entries we own (hooks in settings.json + MCP in .mcp.json)."""
    p = settings_path(scope)
    backup: str | None = None
    settings_changed = False
    if p.is_file():
        try:
            existing = json.loads(p.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            existing = None
        if existing is not None:
            backup_p = p.with_suffix(p.suffix + f".bak.{int(time.time())}")
            shutil.copy2(p, backup_p)
            backup = str(backup_p)

            # legacy mcpServers cleanup (we used to write here)
            mcp = existing.get("mcpServers") or {}
            if mcp.pop(OMNI_MCP_KEY, None) is not None:
                settings_changed = True
                if not mcp:
                    existing.pop("mcpServers", None)
                else:
                    existing["mcpServers"] = mcp

            hooks = existing.get("hooks") or {}
            for event in list(hooks.keys()):
                kept = [b for b in (hooks.get(event) or []) if not _is_omni_block(b)]
                if kept != (hooks.get(event) or []):
                    settings_changed = True
                if kept:
                    hooks[event] = kept
                else:
                    hooks.pop(event)
            if hooks:
                existing["hooks"] = hooks
            else:
                existing.pop("hooks", None)
            p.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

    mcp_changed = _uninstall_mcp(scope)
    return {
        "settings_path": str(p),
        "mcp_path": str(mcp_path(scope)),
        "removed": settings_changed or mcp_changed,
        "backup": backup,
        "note": f"settings_changed={settings_changed}, mcp_changed={mcp_changed}",
    }


def status(scope: Scope = "project") -> dict:
    p = settings_path(scope)
    mp = mcp_path(scope)
    # MCP status (canonical: .mcp.json)
    mcp_command: str | None = None
    if mp.is_file():
        try:
            mcp_cfg = json.loads(mp.read_text(encoding="utf-8") or "{}")
            entry = (mcp_cfg.get("mcpServers") or {}).get(OMNI_MCP_KEY)
            if entry:
                mcp_command = entry.get("command")
        except json.JSONDecodeError:
            pass

    if not p.is_file():
        return {
            "settings_path": str(p), "mcp_path": str(mp),
            "installed": bool(mcp_command),
            "mcp_command": mcp_command, "hook_events": [],
        }
    try:
        cur = json.loads(p.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return {"settings_path": str(p), "mcp_path": str(mp),
                "installed": bool(mcp_command), "mcp_command": mcp_command,
                "hook_events": [], "note": "settings.json malformed"}
    hook_events: list[str] = []
    for event, blocks in (cur.get("hooks") or {}).items():
        for b in (blocks or []):
            if _is_omni_block(b):
                hook_events.append(event)
                break
    return {
        "settings_path": str(p),
        "mcp_path": str(mp),
        "installed": bool(mcp_command) and bool(hook_events),
        "mcp_command": mcp_command,
        "hook_events": sorted(set(hook_events)),
    }
