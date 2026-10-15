# [OMNI] origin=codex domain=dashboard/ccdaemon/hooks ts=2026-08-11 type=hook
# [OMNI] material_id="material:dashboard.ccdaemon.hooks.session_companion.py"
"""Fail-open bridge from provider hooks to the session companion queue."""

from __future__ import annotations

import os
from typing import Any

from . import _shared as sh


def _disabled_during_tests() -> bool:
    return bool(
        os.environ.get("PYTEST_CURRENT_TEST")
        and not os.environ.get("OMNI_TEST_ALLOW_SESSION_COMPANION")
    )


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()
    if isinstance(value, dict):
        return _content_text(value.get("content") or value.get("text") or value.get("message"))
    return ""


def _first_text(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        text = _content_text(payload.get(key))
        if text:
            return text
    return ""


def _canonical_trace_for_payload(payload: dict[str, Any]) -> str:
    """Join provider accounts to the existing conversation binding identity."""

    trace_id = sh.trace_id_for(payload)
    try:
        from omnicompany.packages.services._core.identity import binding_for_session

        binding = binding_for_session(
            trace_id=trace_id,
            provider=sh.binding_provider(payload),
            session_id=sh.provider_session_id(payload),
            pty_id=os.environ.get("OMNI_CC_PTY_ID"),
        )
        return str(binding.get("trace_id") or trace_id)
    except Exception:  # noqa: BLE001 - identity enrichment is fail-open
        return trace_id


def capture_user_prompt(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist the exact user turn before any injected hook context is added."""

    if _disabled_during_tests():
        return {}
    text = _first_text(payload, ("prompt", "user_prompt", "userPrompt", "input", "message"))
    if not text:
        return {}
    from omnicompany.core.quiet_coordination import is_agent_envelope

    if is_agent_envelope(text):
        try:
            from omnicompany.core.parent_action_delivery import acknowledge_parent_action_prompt

            acknowledge_parent_action_prompt(payload, text)
        except Exception:
            pass  # No receipt is safer than a false acknowledgement.
        return {}  # Bound-parent action remains agent context, never human goal/TODO.
    try:
        from omnicompany.packages.services._core.session_companion import CompanionStore

        trace_id = _canonical_trace_for_payload(payload)
        result = CompanionStore().capture_user_turn(trace_id, text)
        try:
            from omnicompany.packages.services._core.session_companion.models import stable_digest
            from omnicompany.packages.services._core.session_companion.todo_lifecycle import maintain_event
            from omnicompany.packages.services._core.session_companion.models import build_event

            lifecycle_event = build_event(
                trace_id=trace_id,
                provider=sh.binding_provider(payload),
                session_id=sh.provider_session_id(payload),
                cwd=str(payload.get("cwd") or os.getcwd()),
                trigger="user_prompt",
                user_input=text,
                generation=f"user-prompt:{stable_digest(trace_id, text)}",
            )
            todo = maintain_event(lifecycle_event, close_reason="")
            result = {**result, "todo_lifecycle": todo}
        except Exception as exc:  # noqa: BLE001 - prompt capture remains fail-open
            sh.append_audit("session_todo_lifecycle_failed", {"error": f"{type(exc).__name__}: {exc}"})
        return result
    except Exception as exc:  # noqa: BLE001 - hook must never block a user turn
        sh.append_audit("session_companion_capture_failed", {"error": f"{type(exc).__name__}: {exc}"})
        return {}


def enqueue_from_payload(
    payload: dict[str, Any],
    *,
    trigger: str,
    compact_summary: str = "",
    assistant_output: str = "",
) -> dict[str, Any]:
    """Capture one immutable event and return immediately after queue/kick."""

    if _disabled_during_tests():
        return {"created": False, "reason": "disabled during tests"}
    trace_id = _canonical_trace_for_payload(payload)
    session_id = sh.provider_session_id(payload)
    if not trace_id or trace_id.endswith("unknown"):
        return {"created": False, "reason": "session-unresolved"}
    try:
        from omnicompany.packages.services._core.identity import binding_for_session
        from omnicompany.packages.services._core.session_companion import CompanionStore
        from omnicompany.packages.services._core.session_companion.service import enqueue_lifecycle_event

        store = CompanionStore()
        turn = store.user_turn(trace_id)
        binding = binding_for_session(
            trace_id=trace_id,
            provider=sh.binding_provider(payload),
            session_id=session_id,
            pty_id=os.environ.get("OMNI_CC_PTY_ID"),
        )
        assistant = assistant_output or _first_text(
            payload,
            (
                "last_assistant_message",
                "lastAssistantMessage",
                "assistant_output",
                "assistantOutput",
                "response",
                "output",
            ),
        )
        summary = compact_summary or _first_text(
            payload,
            ("compact_summary", "compactSummary", "summary", "compacted_context"),
        )
        generation = str(
            payload.get("generation")
            or payload.get("compact_generation")
            or payload.get("turn_id")
            or payload.get("turnId")
            or ""
        )
        result = enqueue_lifecycle_event(
            trigger=trigger,
            trace_id=trace_id,
            provider=sh.binding_provider(payload),
            session_id=session_id,
            cwd=str(payload.get("cwd") or binding.get("cwd") or os.getcwd()),
            first_user_input=str(turn.get("first_user_input") or ""),
            user_input=str(turn.get("latest_user_input") or ""),
            assistant_output=assistant,
            compact_summary=summary,
            project=str(binding.get("project") or ""),
            workstream=str(binding.get("workstream") or ""),
            active_plan=str(binding.get("active_plan") or ""),
            # Transcript discovery can scan several provider roots. Keep it
            # out of the lifecycle hook: the captured event text is already
            # sufficient, and digest upsert accepts a zero/unknown mtime.
            transcript_mtime=0.0,
            generation=generation,
            close_reason=str(payload.get("close_reason") or payload.get("closeReason") or ""),
            close_actor=str(payload.get("close_actor") or payload.get("closeActor") or ""),
            close_evidence=payload.get("close_evidence") or payload.get("closeEvidence") or {},
            store=store,
        )
        sh.append_audit("session_companion_enqueue", {"trigger": trigger, **result})
        return result
    except Exception as exc:  # noqa: BLE001 - maintenance failure cannot affect main Agent
        sh.append_audit(
            "session_companion_enqueue_failed",
            {"trigger": trigger, "trace_id": trace_id, "error": f"{type(exc).__name__}: {exc}"},
        )
        return {"created": False, "reason": str(exc)}


def is_post_compact_start(payload: dict[str, Any]) -> bool:
    """Codex exposes compact as a SessionStart source instead of PreCompact."""

    values = (
        payload.get("source"),
        payload.get("trigger"),
        payload.get("reason"),
        payload.get("session_start_source"),
        payload.get("sessionStartSource"),
    )
    return any("compact" in str(value or "").casefold() for value in values)


def enqueue_native_compact(messages: list[dict[str, Any]], summary: str) -> dict[str, Any]:
    """Bridge the in-process omni-native compact result into the same queue."""

    try:
        from omnicompany.packages.services._core.identity import current_session_meta

        meta = current_session_meta()
        user = _last_role_text(messages, "user")
        assistant = _last_role_text(messages, "assistant")
        payload = {
            "session_id": meta.get("session_id") or meta.get("claude_session_id"),
            "provider": meta.get("provider") or "omni-native",
            "cwd": meta.get("cwd") or os.getcwd(),
            "compact_summary": summary,
            "assistant_output": assistant,
        }
        store_result = capture_user_prompt({**payload, "prompt": user}) if user else {}
        _ = store_result
        return enqueue_from_payload(
            payload,
            trigger="native_compact",
            compact_summary=summary,
            assistant_output=assistant,
        )
    except Exception:
        return {"created": False, "reason": "native compact bridge failed"}


def _last_role_text(messages: list[dict[str, Any]], role: str) -> str:
    for message in reversed(messages):
        if message.get("role") == role:
            text = _content_text(message.get("content"))
            if text:
                return text
    return ""


__all__ = [
    "capture_user_prompt",
    "enqueue_from_payload",
    "enqueue_native_compact",
    "is_post_compact_start",
]
