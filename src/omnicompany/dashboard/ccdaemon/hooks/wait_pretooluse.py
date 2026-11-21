# [OMNI] origin=claude-code ts=2026-10-03 type=infra status=active agent=claude-code
# [OMNI] summary="PreToolUse 等待纪律提醒：同会话独立 write_stdin / 短 cell wait / 纯 sleep 轮询 ≥ 2 次就注入一次提醒，不阻断"
# [OMNI] why="demogame CONFIG-RECORDING-EFFICIENCY 计划 1.10：基线 write_stdin 3,147 次 / 27.3 小时；等待要在同一次调用里等到结束或走后台完成事件"
# [OMNI] tags=hook,pretooluse,wait,polling,1.10
"""PreToolUse wait-discipline reminder (plan 1.10).

A *poll* is a tool call whose only purpose is to wait a little and look again:

* Codex ``write_stdin`` as its own tool call, a ``wait`` on a notebook cell (``cell_id`` +
  ``yield_time_ms`` below the sanctioned long wait) or a unified ``exec`` script that calls
  ``tools.write_stdin`` without looping on it (the loop form
  ``while (r.session_id) { r = await tools.write_stdin({... yield_time_ms: 300000}) }`` is the
  sanctioned way to wait and is not counted);
* a shell command that is nothing but ``sleep`` / ``Start-Sleep`` / ``timeout`` (optionally followed by a
  status peek).

The second poll of a session, and every fifth after that, gets one ``additionalContext`` line pointing
at the two sanctioned waits (same-call loop, or background + completion event).  Nothing is ever
blocked; the counter lives under ``<workspace>/.omni/runtime/wait-reminder/<session>.json`` and the
hook is fail-open.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from . import _shared as sh

POLL_TOOLS = frozenset({"write_stdin"})
WAIT_TOOLS = frozenset({"wait"})          # Codex cell wait: {"cell_id": "16", "yield_time_ms": 30000}
LONG_WAIT_MS = 300_000                    # a wait at least this long in one call is the sanctioned form
EXEC_TOOLS = frozenset({"exec", "exec_command", "Bash", "Shell", "shell", "bash"})
REMIND_AT = 2            # the second independent poll of a session
REMIND_EVERY = 5         # ... then every fifth
STATE_DIR = Path(".omni") / "runtime" / "wait-reminder"

_WRITE_STDIN_RX = re.compile(r"\bwrite_stdin\s*\(")
_LOOP_RX = re.compile(r"\b(while|for|do)\b")
_SLEEP_ONLY_RX = re.compile(
    r"^\s*(?:(?:sleep|timeout|Start-Sleep(?:\s+-s(?:econds)?)?)\s+\d+(?:\.\d+)?\s*;?\s*)"
    r"(?:(?:&&|;|\|\|)?\s*(?:tail|cat|Get-Content|ls|dir|p4\s+opened|git\s+status|omni\s+\S+\s+status|type)\b[^\n]*)?\s*$",
    re.IGNORECASE,
)

REMINDER = (
    "[OMNI-WAIT] 这是本会话第 {n} 次独立轮询式等待（write_stdin / 短 wait / 纯 sleep）。等待要在同一次调用里等到结束，"
    "不要每隔几十秒开一个新回合：Codex 用 "
    "`while (r.session_id) { r = await tools.write_stdin({session_id: r.session_id, chars: \"\", yield_time_ms: 300000}) }`，"
    "cell 用一次 `wait({cell_id, yield_time_ms: 300000})`；"
    "Claude 用 Bash run_in_background + 完成事件。超过预计耗时才查一次进度；查到卡住就处理（停掉 / 缩范围 / 换做法）。"
    "规范：demogame-auto-config SKILL.md「开工三条」第 2 条。"
)


def _tool_text(tool_input: Any) -> str:
    if isinstance(tool_input, str):
        return tool_input
    if not isinstance(tool_input, dict):
        return ""
    for key in ("command", "cmd", "script", "input", "code", "chars"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value
    try:
        return json.dumps(tool_input, ensure_ascii=False)
    except (TypeError, ValueError):
        return ""


def classify(tool_name: str, tool_input: Any) -> str | None:
    """``"write_stdin"`` / ``"exec-write_stdin"`` / ``"sleep"`` when the call is a poll, else None."""
    name = str(tool_name or "")
    if name in POLL_TOOLS:
        return "write_stdin"
    if name in WAIT_TOOLS:
        return _cell_wait(tool_input)
    if name not in EXEC_TOOLS:
        return None
    text = _tool_text(tool_input)
    if not text:
        return None
    if _WRITE_STDIN_RX.search(text):
        return None if _LOOP_RX.search(text) else "exec-write_stdin"
    if _SLEEP_ONLY_RX.match(text):
        return "sleep"
    return None


def _cell_wait(tool_input: Any) -> str | None:
    """A short ``wait`` on a running cell is a poll; one that yields for the long form is not."""
    if not isinstance(tool_input, dict) or "cell_id" not in tool_input:
        return None
    try:
        yield_ms = int(tool_input.get("yield_time_ms") or 0)
    except (TypeError, ValueError):
        yield_ms = 0
    return None if yield_ms >= LONG_WAIT_MS else "cell-wait"


def _state_path(root: Path, session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "no-session")[:120]
    return root / STATE_DIR / f"{safe}.json"


def bump(root: Path, session_id: str, kind: str) -> int:
    """Increment and return this session's poll count (per-session JSON file)."""
    path = _state_path(root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    state: dict[str, Any] = {"count": 0, "kinds": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    state["count"] = int(state.get("count") or 0) + 1
    kinds = state.get("kinds") if isinstance(state.get("kinds"), dict) else {}
    kinds[kind] = int(kinds.get(kind) or 0) + 1
    state["kinds"] = kinds
    state["last_ts"] = time.time()
    try:
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return int(state["count"])


def should_remind(count: int) -> bool:
    return count == REMIND_AT or (count > REMIND_AT and (count - REMIND_AT) % REMIND_EVERY == 0)


def handle(payload: dict[str, Any], *, root: Path | None = None) -> str | None:
    """Return the reminder text to inject, or None.  Never raises."""
    try:
        tool_name = payload.get("tool_name") or payload.get("toolName") or ""
        tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
        kind = classify(str(tool_name), tool_input)
        if kind is None:
            return None
        session_id = sh.provider_session_id(payload) or str(payload.get("cwd") or "")
        count = bump(root or sh.repo_root(), session_id, kind)
        sh.append_audit("wait_poll", {"session_id": session_id, "kind": kind, "count": count,
                                      "tool_name": str(tool_name)})
        if not should_remind(count):
            return None
        return REMINDER.replace("{n}", str(count))   # the text carries JS braces: no str.format
    except Exception as exc:  # fail-open: a reminder hook must never break a tool call
        try:
            sh.append_audit("wait_poll_error", {"error": f"{type(exc).__name__}: {exc}"})
        except Exception:
            pass
        return None


def main() -> int:
    payload = sh.read_stdin_json()
    text = handle(payload)
    if text:
        sh.emit_context(text, payload, "PreToolUse")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
