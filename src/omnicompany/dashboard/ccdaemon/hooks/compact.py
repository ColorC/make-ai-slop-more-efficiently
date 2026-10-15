# [OMNI] origin=claude-code ts=2026-05-02 type=infra
# [OMNI] material_id="material:dashboard.cc_wrapper.hooks.compact_context_preserver.implementation.py"
"""PreCompact hook — preserve only the current workspace/plan pointers.

Conversation history remains the runtime's responsibility. This hook neither
writes per-compact files nor tries to reconstruct a manual summary checklist.
"""

from __future__ import annotations

import os
import sys

from . import _shared as sh
from ..assistant import ASSISTANT_COMPACT_CONTEXT, ASSISTANT_TOPIC


def main() -> int:
    payload = sh.read_stdin_json()
    cwd = payload.get("cwd") or os.getcwd()
    session_id = sh.provider_session_id(payload)
    provider = sh.binding_provider(payload)
    trigger = (payload.get("trigger") or "auto").lower()  # 'manual' | 'auto'

    plan = sh.detect_active_plan(
        hint_cwd=cwd,
        provider=provider,
        session_id=session_id or None,
    )
    plan_id = sh.plan_id_of(plan) if plan else None
    audit = {
        "trigger": trigger,
        "session_id": session_id,
        "provider": provider,
        "cwd": cwd,
        "active_plan": plan_id,
    }

    # Emit a trace event so the dashboard sees the compact event in real time.
    sh.emit_event(
        trace_id=sh.trace_id_for(payload),
        event_type="agent.state.change",
        payload={"from_state": "active", "to_state": "compacting", **audit},
        tags=["cc_session", "compact"],
    )
    text = sh.build_minimal_context(
        cwd=str(cwd),
        plan=plan,
        payload=payload,
        reason="after compact",
    )
    # Reassert the dedicated assistant contract after native history compaction.
    # Binding lookup is best-effort so ordinary sessions and standalone hooks
    # retain the existing minimal context behavior.
    try:
        from omnicompany.packages.services._core.identity import binding_for_session

        binding = binding_for_session(
            trace_id=sh.trace_id_for(payload),
            provider=sh.binding_provider(payload),
            session_id=sh.provider_session_id(payload),
            pty_id=os.environ.get("OMNI_CC_PTY_ID"),
        ) or {}
    except Exception:
        binding = {}
    if binding.get("topic") == ASSISTANT_TOPIC:
        text = f"{text}\n\n{ASSISTANT_COMPACT_CONTEXT}"[:800]
    audit["context_chars"] = len(text)
    sh.append_audit("compact", audit)

    # Non-blocking lifecycle maintenance: enqueue only; the detached companion
    # worker performs all model and store work after this hook returns.
    try:
        from .session_companion import enqueue_from_payload

        enqueue_from_payload(payload, trigger="pre_compact")
    except Exception:
        pass

    # Claude/Codex 走 additionalContext JSON；Kimi 走纯文本 stdout（见 _shared.emit_context）。
    sh.emit_context(text, payload, "PreCompact")
    return 0


if __name__ == "__main__":
    sys.exit(main())
