"""Install the small destructive-operation guard for Claude Code and Codex.

This module deliberately owns only one hook: ``PreToolUse``.  It is separate
from the lifecycle installers because repairing a provider's safety hook must
not rewrite the provider's MCP, trust, or feature configuration.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Literal

Provider = Literal["claude_code", "codex"]
Scope = Literal["user", "project"]

_OWNED_COMMAND_FRAGMENT = "omnicompany.dashboard.ccdaemon.hooks."
_SAFETY_SCRIPT = "safety_pretooluse.py"
_KNOWN_OLD_HOOKS = (
    "guard_destructive_ops",
    "l2_boundary_check",
    "guardian_pretool_defense",
)
_WORKSPACE_ROOT = "C:/workspace/"
_OLD_HOOK_PATHS = frozenset(
    f"{_WORKSPACE_ROOT}/.claude/hooks/{name}{suffix}"
    for name in _KNOWN_OLD_HOOKS
    for suffix in ("", ".py")
)
_MARKER = "[omnicompany safety]"
_MATCHER = ".*"


def _absolute(path: str | Path) -> Path:
    """Return an absolute path without requiring the target to exist."""

    return Path(path).expanduser().absolute()


def _config_path(provider: Provider, scope: Scope) -> Path:
    from omnicompany.core.config import omni_workspace_root

    root = Path.home() if scope == "user" else omni_workspace_root()
    if provider == "claude_code":
        return _absolute(root / ".claude" / "settings.json")
    return _absolute(root / ".codex" / "hooks.json")


def _script_path(script_path: str | Path | None) -> Path:
    if script_path is None:
        script_path = (
            Path(__file__).resolve().parent.parent
            / "dashboard" / "ccdaemon" / "hooks" / _SAFETY_SCRIPT
        )
    return _absolute(script_path)


def _validate_script(path: Path) -> None:
    """Refuse to install a hook that is missing, non-regular, or a link."""

    try:
        if path.is_symlink() or not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode):
            raise ValueError(f"safety hook must be an existing regular file, not a link: {path}")
    except OSError as exc:
        raise ValueError(f"safety hook must be an existing regular file: {path}") from exc


def _python_path() -> Path:
    return _absolute(sys.executable)


def _quote_windows(value: Path) -> str:
    # PowerShell single quoted strings escape a literal quote by doubling it.
    return str(value).replace("'", "''")


def _commands(provider: Provider, script_path: Path) -> tuple[str, str | None]:
    python = _python_path()
    script = _absolute(script_path)
    # The direct command is also kept in the native command field for Codex;
    # commandWindows is the PowerShell form Codex uses on Windows.
    command = f'"{python}" -I -S "{script}"'
    if provider == "codex":
        # PowerShell -Command maps an unpropagated native nonzero status to 1.
        # Codex treats 2 as a denial but 1 as a hook failure (and continues).
        command_windows = f"& '{_quote_windows(python)}' -I -S '{_quote_windows(script)}'; exit $LASTEXITCODE"
        return command, command_windows
    return command, None


def _handler(provider: Provider, script_path: Path) -> dict[str, Any]:
    command, command_windows = _commands(provider, script_path)
    result: dict[str, Any] = {
        "type": "command",
        "command": command,
        "timeout": 3,
        "_omnicompany": _MARKER,
    }
    if command_windows is not None:
        result["commandWindows"] = command_windows
    return result


def _desired_block(provider: Provider, script_path: Path) -> dict[str, Any]:
    return {"matcher": _MATCHER, "hooks": [_handler(provider, script_path)]}


def _command_values(handler: Any) -> list[str]:
    if not isinstance(handler, dict):
        return []
    return [
        value
        for key in ("command", "commandWindows")
        if isinstance(value := handler.get(key), str)
    ]


def _contains_exact_path(command: str, path: str) -> bool:
    start = command.find(path)
    while start >= 0:
        end = start + len(path)
        before_ok = start == 0 or command[start - 1] in " \t\"'"
        after_ok = end == len(command) or command[end] in " \t\"'"
        if before_ok and after_ok:
            return True
        start = command.find(path, start + 1)
    return False


def _is_owned_handler(handler: Any, canonical_script: Path | None = None) -> bool:
    """Recognize only hooks this repair entry is allowed to replace."""

    if isinstance(handler, dict) and handler.get("_omnicompany") == _MARKER:
        return True
    commands = _command_values(handler)
    normalized = [command.replace("\\", "/").lower() for command in commands]
    if any(_OWNED_COMMAND_FRAGMENT in command for command in normalized):
        return True
    canonical = _absolute(canonical_script) if canonical_script else _script_path(None)
    canonical_text = str(canonical).replace("\\", "/").lower()
    if any(canonical_text in command for command in normalized):
        return True
    # These are the only three legacy Omni files eligible for cleanup.  A
    # similarly named script elsewhere (including other/safety_pretooluse.py)
    # remains user-owned.
    return any(
        any(_contains_exact_path(command, old_path) for old_path in _OLD_HOOK_PATHS)
        for command in normalized
    )


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = path.read_text(encoding="utf-8-sig")
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"hook configuration is malformed or unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"hook configuration must contain a JSON object: {path}")
    return value


def _merge_hooks(
    existing: Any,
    provider: Provider,
    script_path: Path,
    *,
    add_hook: bool = True,
) -> tuple[dict[str, list[Any]], int, int]:
    if existing is None:
        existing = {}
    if not isinstance(existing, dict):
        raise ValueError("hook configuration field 'hooks' must be an object")

    merged: dict[str, list[Any]] = {}
    removed = 0
    preserved = 0
    for event, blocks in existing.items():
        if not isinstance(event, str) or not isinstance(blocks, list):
            raise ValueError(f"hook configuration event {event!r} must contain an array")
        final_blocks: list[Any] = []
        for block in blocks:
            if not isinstance(block, dict) or "hooks" not in block:
                final_blocks.append(block)
                continue
            handlers = block.get("hooks")
            if not isinstance(handlers, list):
                raise ValueError(f"hook configuration event {event!r} contains a malformed hook block")
            kept = []
            for handler in handlers:
                if _is_owned_handler(handler):
                    removed += 1
                else:
                    kept.append(handler)
                    preserved += 1
            if kept:
                if len(kept) != len(handlers):
                    retained_block = dict(block)
                    retained_block["hooks"] = kept
                    final_blocks.append(retained_block)
                else:
                    final_blocks.append(block)
            # A block containing only owned handlers disappears.  A mixed block
            # remains with its unrelated handlers intact.
        if final_blocks:
            merged[event] = final_blocks

    if add_hook:
        desired = _desired_block(provider, script_path)
        merged.setdefault("PreToolUse", []).append(desired)
    return merged, removed, preserved


def _backup(path: Path) -> Path:
    stamp = time.time_ns()
    target = path.with_name(path.name + f".bak.{stamp}")
    while target.exists():
        stamp += 1
        target = path.with_name(path.name + f".bak.{stamp}")
    shutil.copy2(path, target)
    return target


def _atomic_write(path: Path, payload: str, *, bom: bool) -> None:
    """Write beside the destination and replace it only after a full write."""

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        encoding = "utf-8-sig" if bom else "utf-8"
        with os.fdopen(fd, "w", encoding=encoding, newline="") as stream:
            fd = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        if fd != -1:
            os.close(fd)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _resolve_injected_path(
    path: str | Path | None,
    config_path: str | Path | None,
    settings_path: str | Path | None,
    hooks_path: str | Path | None,
) -> Path | None:
    supplied = [value for value in (path, config_path, settings_path, hooks_path) if value is not None]
    if len(supplied) > 1:
        raise ValueError("provide only one of path, config_path, settings_path, or hooks_path")
    return _absolute(supplied[0]) if supplied else None


def install(
    provider: Provider = "claude_code",
    scope: Scope = "user",
    *,
    path: str | Path | None = None,
    config_path: str | Path | None = None,
    settings_path: str | Path | None = None,
    hooks_path: str | Path | None = None,
    script_path: str | Path | None = None,
    add_hook: bool = True,
) -> dict[str, Any]:
    """Install the safety profile and return a JSON-safe receipt.

    ``path`` (and its provider-specific aliases) exists for fixture/repair
    callers.  It scopes one explicit config file; this function never scans
    sibling projects or the filesystem.
    """

    if provider not in ("claude_code", "codex"):
        raise ValueError(f"unsupported provider: {provider!r}")
    if scope not in ("user", "project"):
        raise ValueError(f"unsupported scope: {scope!r}")
    target = _resolve_injected_path(path, config_path, settings_path, hooks_path)
    target = target or _config_path(provider, scope)
    script = _script_path(script_path)
    if add_hook:
        _validate_script(script)

    before = _read(target)
    after = dict(before)
    merged_hooks, removed, preserved = _merge_hooks(
        before.get("hooks"), provider, script, add_hook=add_hook
    )
    # An existing non-hook top-level key, including MCP and provider metadata,
    # is retained byte-for-byte in the logical JSON structure.
    if merged_hooks:
        after["hooks"] = merged_hooks
    else:
        after.pop("hooks", None)

    changed = after != before
    backup_path: Path | None = None
    if changed:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            backup_path = _backup(target)
        bom = target.is_file() and target.read_bytes().startswith(b"\xef\xbb\xbf")
        _atomic_write(
            target,
            json.dumps(after, indent=2, ensure_ascii=False) + "\n",
            bom=bom,
        )

    files = [{"path": str(target), "purpose": f"{provider} {scope} hook configuration"}]
    if backup_path is not None:
        files.append({"path": str(backup_path), "purpose": "pre-install recovery backup"})
    return {
        "provider": provider,
        "scope": scope,
        "config_path": str(target),
        "settings_path": str(target),
        "changed": changed,
        "installed": bool(add_hook),
        "backup_path": str(backup_path) if backup_path else None,
        "removed_owned_handlers": removed,
        "preserved_unowned_handlers": preserved,
        "files": files,
        "note": (
            (
                "Safety PreToolUse profile written; start a new provider session to load it."
                if add_hook
                else "Known Omni safety hooks removed; no new hook was installed."
            )
            + " This receipt does not claim that a session has reloaded or trusted the hook."
        ),
    }


__all__ = ["install"]
