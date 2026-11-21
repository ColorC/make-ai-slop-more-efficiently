# [OMNI] origin=claude-code ts=2026-05-02 type=infra
# [OMNI] material_id="material:dashboard.cc_wrapper.hooks.plan_todo_bidirectional_sync.implementation.py"
"""Legacy PostToolUse compatibility shim.

This hook used to copy TodoWrite state into plan.md and inject the plan's first
20 checkboxes after edits. Both behaviors created competing progress sources and
repeated stale context. New installations do not register this module; old
configurations may still call it, so it remains deliberately silent and
read-only until the next install removes the block.
"""

from __future__ import annotations

import sys

from . import _shared as sh


def main() -> int:
    stdin = sh.read_stdin_json()
    tool_name = stdin.get("tool_name") or stdin.get("toolName") or ""
    sh.append_audit("todos.legacy_ignored", {
        "tool": tool_name,
        "provider": sh.binding_provider(stdin),
        "session_id": sh.provider_session_id(stdin),
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
