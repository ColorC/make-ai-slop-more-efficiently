# [OMNI] origin=claude-code ts=2026-05-02 type=infra
# [OMNI] material_id="material:dashboard.cc_wrapper.hooks.tool_call_trace_emitter.implementation.py"
"""Convert native Claude Code/Codex tool calls into event-bus trace events.

Wired as PreToolUse + PostToolUse + Stop hooks (one script, dispatch on
`hook_event_name`). Output is silent (no additionalContext) — pure observation.

Trace correlation:
  trace_id = `cc_<session_id>`  → cc_session entity in dashboard
  event ids: PreToolUse mints UUID, PostToolUse correlates by `tool_use_id`.

This is the integration that makes claude-code runs first-class in the
dashboard's Trace DAG / Timeline / Tree views (round 13).
"""

from __future__ import annotations

import json
import sys
import uuid

from . import _shared as sh

# in-process map, used only when stdin payload doesn't carry a tool_use_id
# (won't survive across hook invocations — Claude provides correlation ids,
# this is a defensive fallback).
_FALLBACK_PAIRING: dict[str, str] = {}


def _trace_id_of(stdin: dict) -> str:
    return sh.trace_id_for(stdin)


def _tags(stdin: dict, *extra: str) -> list[str]:
    return ["cc_session", f"provider:{sh.binding_provider(stdin)}", *extra]


def _summarize_args(args: dict) -> dict:
    """Trim potentially huge tool args (file content, etc.) before logging."""
    out: dict = {}
    if not isinstance(args, dict):
        return {"_raw_repr": repr(args)[:200]}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 1500:
            out[k] = v[:1500] + f"... [truncated, total {len(v)} chars]"
        elif isinstance(v, (list, dict)):
            j = json.dumps(v, ensure_ascii=False, default=str)
            if len(j) > 1500:
                out[k] = j[:1500] + "... [truncated]"
            else:
                out[k] = v
        else:
            out[k] = v
    return out


def _summarize_result(result) -> str:
    """Truncate tool result text for storage."""
    if result is None:
        return ""
    if isinstance(result, (dict, list)):
        s = json.dumps(result, ensure_ascii=False, default=str)
    else:
        s = str(result)
    if len(s) > 4000:
        return s[:4000] + f"... [truncated, total {len(s)} chars]"
    return s


def handle_pre_tool_use(stdin: dict) -> int:
    tool_use_id = stdin.get("tool_use_id") or stdin.get("toolUseId")
    tool_name = stdin.get("tool_name") or stdin.get("toolName") or "?"
    tool_input = stdin.get("tool_input") or stdin.get("toolInput") or {}

    eid = uuid.uuid4().hex
    if tool_use_id:
        _FALLBACK_PAIRING[tool_use_id] = eid

    sh.emit_event(
        trace_id=_trace_id_of(stdin),
        event_type="agent.tool.call",
        payload={
            "tool": tool_name,
            "args": _summarize_args(tool_input),
            "tool_use_id": tool_use_id,
        },
        tags=_tags(stdin, f"tool:{tool_name}"),
    )
    return 0


def handle_post_tool_use(stdin: dict) -> int:
    tool_use_id = stdin.get("tool_use_id") or stdin.get("toolUseId")
    tool_name = stdin.get("tool_name") or stdin.get("toolName") or "?"
    result = stdin.get("tool_response") or stdin.get("toolResponse")

    parent = _FALLBACK_PAIRING.pop(tool_use_id, None) if tool_use_id else None
    sh.emit_event(
        trace_id=_trace_id_of(stdin),
        event_type="agent.tool.result",
        payload={
            "tool": tool_name,
            "result": _summarize_result(result),
            "tool_use_id": tool_use_id,
            "verdict": "ok" if result else "empty",
        },
        parent_id=parent,
        tags=_tags(stdin, f"tool:{tool_name}"),
    )
    return 0


def handle_stop(stdin: dict) -> int:
    sh.emit_event(
        trace_id=_trace_id_of(stdin),
        event_type="task.finish",
        payload={
            "session_id": stdin.get("session_id") or stdin.get("sessionId"),
            "provider": sh.binding_provider(stdin),
            "result": "turn ended",
        },
        tags=_tags(stdin),
    )
    # Codex Stop requires JSON (plain text is invalid); an empty object means
    # success/continue and remains harmless for Claude Code.
    if sh.hook_provider(stdin) == "codex":
        json.dump({}, sys.stdout)
        sys.stdout.write("\n")
    return 0


def main() -> int:
    stdin = sh.read_stdin_json()
    event = (stdin.get("hook_event_name") or stdin.get("hookEventName") or "").strip()
    try:
        if event == "PreToolUse":
            return handle_pre_tool_use(stdin)
        if event == "PostToolUse":
            return handle_post_tool_use(stdin)
        if event == "Stop":
            return handle_stop(stdin)
    except Exception as e:
        # never block the native agent on observability failure
        print(f"[cc_wrapper trace hook] {event} failed: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
