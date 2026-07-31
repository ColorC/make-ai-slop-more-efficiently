# [OMNI] origin=claude-code ts=2026-05-02 type=infra
# [OMNI] material_id="material:dashboard.cc_wrapper.hooks.session_initializer.implementation.py"
"""SessionStart hook for native Claude Code and Codex sessions.

Output: `additionalContext` JSON containing only workspace/plan pointers.
Side effect: emits `task.intent` event so the cc_session shows up in dashboard's
Trace list immediately on session start.

The model-visible text is capped at 800 characters. Plan prose, checklists,
standards and exit criteria stay on disk and are opened only when needed.
"""

from __future__ import annotations

import json
import os
import sys

# Windows: 默认 stdout encoding cp936 (GBK), plan.md 含 ↔ 等 unicode → encode fail.
# 强制 utf-8 输出 (跟 cli/main.py 同 pattern).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

from . import _shared as sh


def main() -> int:
    payload = sh.read_stdin_json()
    cwd = payload.get("cwd") or os.getcwd()
    session_id = sh.provider_session_id(payload)
    provider = sh.binding_provider(payload)

    plan = sh.detect_active_plan(
        hint_cwd=cwd,
        provider=provider,
        session_id=session_id or None,
    )
    plan_id = sh.plan_id_of(plan) if plan else None

    plan_meta: dict = {}
    if plan:
        plan_md = plan / "plan.md"
        if plan_md.is_file():
            try:
                from omnicompany.dashboard.controlplane.plans import parse_plan_frontmatter

                plan_meta = parse_plan_frontmatter(plan_md) or {}
            except Exception:
                plan_meta = {}

    text = sh.build_minimal_context(
        cwd=str(cwd),
        plan=plan,
        payload=payload,
        plan_meta=plan_meta,
    )

    # Audit so we can see what we injected (for debugging via tail data/cc_hooks_audit.jsonl)
    sh.append_audit("session_start", {
        "session_id": session_id, "provider": provider, "cwd": cwd, "plan_id": plan_id,
        "context_chars": len(text),
    })

    # Mirror to event bus so the session appears as a trace immediately.
    sh.emit_event(
        trace_id=sh.trace_id_for(payload),
        event_type="task.intent",
        payload={
            "session_id": session_id,
            "provider_session_id": session_id,
            "claude_session_id": session_id if provider == "claude_code" else None,
            "provider": provider,
            "cwd": cwd,
            "active_plan": plan_id,
            "instruction": f"{provider} session started (plan={plan_id or 'none'})",
        },
        tags=["cc_session", f"provider:{provider}"],
    )

    # If we know our PTY id (set by PtyManager via OMNI_CC_PTY_ID env), write the
    # provider's durable conversation id back into cc_sessions.json so the
    # dashboard can correlate + offer resume after a backend restart.
    pty_id = os.environ.get("OMNI_CC_PTY_ID")
    if pty_id and session_id:
        try:
            from omnicompany.dashboard.ccdaemon.pty import update_meta_field
            update_meta_field(
                pty_id,
                provider_session_id=session_id,
                claude_session_id=session_id if provider == "claude_code" else None,
                active_plan=plan_id,
            )
        except Exception as e:
            print(f"[ccdaemon] meta link failed: {e}", file=sys.stderr)

    # Record active session to cc_session_active.json so CLI / non-hook callers can
    # resolve the same trace_id (shared identity across hook + CLI + web — all three
    # go through omnicompany.packages.services._core.identity.record_active_session).
    # 有真身份才记(否则 trace_id 会退化成 cc_unknown, 多个匿名会话撞成一条)。
    if session_id or pty_id:
        try:
            from omnicompany.packages.services._core.identity import record_active_session
            record_active_session(
                trace_id=sh.trace_id_for(payload),
                session_id=session_id or None,
                claude_session_id=session_id if provider == "claude_code" else None,
                pty_id=pty_id,
                active_plan=plan_id,
                # project 取 plan frontmatter(权威); 没绑 plan 时留空, 让 agent_registry 去推测.
                project=(plan_meta.get("project") if plan_meta else None),
                provider=provider,
                cwd=cwd,
                source="hook",
            )
            if plan_id:
                from omnicompany.packages.services._core.identity import update_session_binding

                update_session_binding(
                    sh.trace_id_for(payload),
                    last_plan_inject_id=plan_id,
                )
        except Exception as e:
            print(f"[ccdaemon] record_active_session failed: {e}", file=sys.stderr)

    # Both native runtimes accept this event-scoped additionalContext envelope;
    # Kimi Code CLI gets plain stdout text instead (see _shared.emit_context).
    sh.emit_context(text, payload, "SessionStart")
    return 0


if __name__ == "__main__":
    sys.exit(main())
