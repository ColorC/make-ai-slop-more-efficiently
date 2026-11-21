# [OMNI] origin=claude-code domain=omnicompany/core ts=2026-10-17T12:00:00+08:00 type=infra status=active
# [OMNI] summary="复现 Codex hook 信任哈希并写入 config.toml 的 [hooks.state]，让 hooks.json 新增的 hook 不必进 TUI /hooks 手动信任。"
# [OMNI] why="2026-10-17 长跑诊断补 hook 覆盖时发现：Codex 只运行已信任的 hook，安装器写进 hooks.json 的新条目会被静默跳过。算法取自 openai/codex codex-rs/hooks/src/engine/discovery.rs hook_hash + config/src/fingerprint.rs version_for_toml，已对本机 8 个已信任条目逐一核对一致。"
"""Codex hook trust hashes, reproduced exactly (stdlib only).

``trusted_hash`` = ``sha256:`` + hex(sha256(compact JSON with sorted keys of the
normalized identity)).  The identity is the event key label, the matcher and one
normalized command handler: on Windows ``commandWindows`` replaces ``command``,
``timeout`` defaults to 600, ``async`` is always present, ``statusMessage`` only
when set.  The config key is ``<hooks.json path>:<event label>:<group>:<handler>``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

EVENT_LABELS = {
    "PreToolUse": "pre_tool_use",
    "PostToolUse": "post_tool_use",
    "SessionStart": "session_start",
    "UserPromptSubmit": "user_prompt_submit",
    "Stop": "stop",
    "SubagentStop": "subagent_stop",
    "SubagentStart": "subagent_start",
    "PreCompact": "pre_compact",
    "PostCompact": "post_compact",
    "SessionEnd": "session_end",
    "PermissionRequest": "permission_request",
}


def hook_hash(event: str, matcher: str | None, handler: dict[str, Any], *, windows: bool | None = None) -> str:
    if windows is None:
        windows = os.name == "nt"
    if handler.get("type", "command") != "command":
        raise ValueError("only command hooks are supported")
    command = handler.get("commandWindows") if windows and handler.get("commandWindows") else handler["command"]
    normalized: dict[str, Any] = {
        "type": "command",
        "command": command,
        "timeout": max(int(handler.get("timeout") or 600), 1),
        "async": bool(handler.get("async", False)),
    }
    if handler.get("statusMessage") is not None:
        normalized["statusMessage"] = handler["statusMessage"]
    identity: dict[str, Any] = {"event_name": EVENT_LABELS[event], "hooks": [normalized]}
    if matcher is not None:
        identity["matcher"] = matcher
    text = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def hook_keys(hooks_json: Path) -> list[tuple[str, str]]:
    """Return ``(config key, trusted hash)`` for every command handler in a hooks.json."""
    data = json.loads(hooks_json.read_bytes().decode("utf-8-sig"))
    out: list[tuple[str, str]] = []
    for event, groups in (data.get("hooks") or {}).items():
        if event not in EVENT_LABELS:
            continue
        for gi, group in enumerate(groups):
            for hi, handler in enumerate(group.get("hooks") or []):
                if handler.get("type", "command") != "command":
                    continue
                key = f"{hooks_json}:{EVENT_LABELS[event]}:{gi}:{hi}"
                out.append((key, hook_hash(event, group.get("matcher"), handler)))
    return out


_STATE_HEADER = re.compile(r"^\[hooks\.state\.'(?P<key>[^']+)'\]\s*$")


def write_trust(config_toml: Path, entries: list[tuple[str, str]]) -> list[dict[str, str]]:
    """Add or update ``[hooks.state.'<key>'] trusted_hash`` tables; returns the changes made.

    Edits are textual so comments and unrelated tables survive untouched.  Keys
    containing a single quote cannot be written as TOML literal keys and are refused.
    """
    text = config_toml.read_text(encoding="utf-8") if config_toml.exists() else ""
    lines = text.splitlines()
    present: dict[str, int] = {}
    for index, line in enumerate(lines):
        match = _STATE_HEADER.match(line.strip())
        if match:
            present[match.group("key")] = index
    changes: list[dict[str, str]] = []
    appended: list[str] = []
    for key, digest in entries:
        if "'" in key:
            raise ValueError(f"cannot write hook key containing a quote: {key}")
        if key in present:
            index = present[key]
            for j in range(index + 1, len(lines)):
                stripped = lines[j].strip()
                if stripped.startswith("["):
                    lines.insert(j, f'trusted_hash = "{digest}"')
                    changes.append({"key": key, "action": "added-hash", "hash": digest})
                    break
                if stripped.startswith("trusted_hash"):
                    if stripped != f'trusted_hash = "{digest}"':
                        lines[j] = f'trusted_hash = "{digest}"'
                        changes.append({"key": key, "action": "updated", "hash": digest})
                    break
            else:
                lines.append(f'trusted_hash = "{digest}"')
                changes.append({"key": key, "action": "added-hash", "hash": digest})
            continue
        appended += ["", f"[hooks.state.'{key}']", f'trusted_hash = "{digest}"']
        changes.append({"key": key, "action": "added", "hash": digest})
    if changes:
        body = "\n".join(lines + appended).rstrip("\n") + "\n"
        tmp = config_toml.with_name(config_toml.name + ".tmp-trust")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, config_toml)
    return changes


def trust_matching(hooks_json: Path, config_toml: Path, needles: list[str]) -> list[dict[str, str]]:
    """Trust only handlers whose marker or command contains one of ``needles``.

    Never trusts a whole file blindly: hooks someone else added stay for the user to review.
    """
    data = json.loads(hooks_json.read_bytes().decode("utf-8-sig"))
    entries = []
    for event, groups in (data.get("hooks") or {}).items():
        if event not in EVENT_LABELS:
            continue
        for gi, group in enumerate(groups):
            for hi, handler in enumerate(group.get("hooks") or []):
                text = " ".join(str(handler.get(k) or "") for k in ("_omnicompany", "command", "commandWindows"))
                if handler.get("type", "command") == "command" and any(n in text for n in needles):
                    entries.append((f"{hooks_json}:{EVENT_LABELS[event]}:{gi}:{hi}",
                                    hook_hash(event, group.get("matcher"), handler)))
    return write_trust(config_toml, entries)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="codex_hook_trust",
                                     description="Write Codex trusted_hash entries for selected hooks.json handlers.")
    parser.add_argument("--hooks-json", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True, help="the config.toml Codex reads trust from")
    parser.add_argument("--match", action="append", required=True,
                        help="substring of the handler marker/command to trust (repeatable)")
    args = parser.parse_args(argv)
    changes = trust_matching(args.hooks_json.absolute(), args.config, args.match)
    sys.stdout.write(json.dumps({"config": str(args.config.absolute()), "changes": changes},
                                ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
