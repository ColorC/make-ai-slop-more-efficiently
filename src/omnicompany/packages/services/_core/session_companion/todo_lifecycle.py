"""Deterministic session TODO lifecycle maintenance.

This module deliberately writes through the existing plan-groups ``_todo``
store.  It is a small, fail-closed policy layer around that store: lifecycle
signals may advance an owned open item, but cannot resurrect a terminal item or
replace a human decision/title.  Missing or ambiguous evidence is retained as
``unknown``/``needs_classification`` in the item's legacy metadata.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping

from omnicompany.packages.services._core.lifecycle import plan_groups_store as pgs

from .models import CompanionEvent, stable_digest

TODO_GROUP = "_todo"
LIFECYCLE_SCHEMA = "session-todo-lifecycle/v1"
OPEN_STATUSES = {"pending", "in_progress", "deferred", "review", "blocked"}
TERMINAL_STATUSES = {"done", "cancelled"}

# These are intentionally narrow.  A normal request such as “帮我完成这个”
# is not a completion acknowledgement, while “已完成/不做了” is.
_DONE_RE = re.compile(
    r"(?:已完成|已经完成|完成了?|做完了?|搞定了?|解决了?|all\s+done|completed|done)",
    re.IGNORECASE,
)
_DROP_RE = re.compile(
    r"(?:不做了|不要了|放弃了?|取消了?|作罢了?|cancel(?:led)?|drop(?:ped)?)",
    re.IGNORECASE,
)
_AMBIGUOUS_RE = re.compile(
    r"(?:先不|暂时不|现在不|以后再说|稍后再说|回头再说|not\s+now|later|maybe\s+later)",
    re.IGNORECASE,
)
_ASSISTANT_DONE_RE = re.compile(
    r"(?:all\s+done|task\s+complete|completed|已完成|已经完成|做完了|搞定了|解决了)",
    re.IGNORECASE,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def classify_user_intent(text: str) -> str:
    """Return ``completed``, ``dropped``, ``unknown`` or ``none``.

    ``unknown`` is only returned when a message looks like a deferral.  All
    other ordinary prompts are ``none`` and do not change a terminal state.
    """

    value = _text(text)
    if not value:
        return "none"
    # Explicit acknowledgements outrank a surrounding explanatory sentence.
    request_to_complete = re.search(r"(?:帮我|请|需要|要|想要)\s*(?:尽快)?完成", value, re.IGNORECASE)
    if _DONE_RE.search(value) and not request_to_complete:
        return "completed"
    if _DROP_RE.search(value):
        return "dropped"
    if _AMBIGUOUS_RE.search(value):
        return "unknown"
    return "none"


def is_agent_completion_declaration(text: str) -> bool:
    """Whether the current assistant output explicitly declares completion."""

    return bool(_ASSISTANT_DONE_RE.search(_text(text)))


def classify_close(
    *,
    close_reason: str = "",
    close_actor: str = "",
    close_evidence: Mapping[str, Any] | None = None,
) -> str:
    """Classify a close signal without treating process death as user intent."""

    reason = _text(close_reason).casefold()
    actor = _text(close_actor).casefold()
    evidence = close_evidence or {}
    if reason in {"user", "human", "manual"}:
        return "human"
    if reason == "kill" and any(_text(evidence.get(key)) for key in ("requester_ip", "requester_user_agent", "requester_referer")):
        return "human"
    if actor and actor not in {"system", "daemon", "runtime", "unknown"} and reason not in {
        "pty-exit",
        "process-exit",
        "cleanup",
        "exception",
        "crash",
    }:
        return "human"
    if reason in {
        "pty-exit",
        "process-exit",
        "process_exit",
        "cleanup",
        "exception",
        "crash",
        "host-missing",
        "host_missing",
        "idle-ttl",
        "agent-idle-ttl",
        "provider-idle",
        "exit",
    }:
        return "process_exit"
    return "unknown"


def _entry_trace(entry: Mapping[str, Any]) -> str:
    legacy = entry.get("legacy") or {}
    description = _text(entry.get("description") or legacy.get("description"))
    pty_match = re.search(r"PTY\s*[:：=]\s*([A-Za-z0-9_-]{8,})", description, re.IGNORECASE)
    return _text(
        legacy.get("session_trace_id")
        or legacy.get("trace_id")
        or legacy.get("session_id")
        or legacy.get("pty_id")
        or legacy.get("session_pty_id")
        or legacy.get("provider_session_id")
        or (pty_match.group(1) if pty_match else "")
        or entry.get("session_trace_id")
    )


def _entry_identifiers(entry: Mapping[str, Any]) -> set[str]:
    legacy = entry.get("legacy") or {}
    identifiers: set[str] = set()
    for record in (entry, legacy):
        for key in (
            "session_trace_id",
            "trace_id",
            "session_id",
            "pty_id",
            "session_pty_id",
            "provider_session_id",
            "claude_session_id",
        ):
            value = _text(record.get(key))
            if value:
                identifiers.add(value)
    description = _text(entry.get("description") or legacy.get("description"))
    pty_match = re.search(r"PTY\s*[:：=]\s*([A-Za-z0-9_-]{8,})", description, re.IGNORECASE)
    if pty_match:
        identifiers.add(pty_match.group(1))
    return identifiers


def _event_meta_candidates(event: CompanionEvent) -> list[tuple[str, dict[str, Any]]]:
    """Read existing PTY metadata to bridge native-session and PTY identities."""

    wanted = {
        value
        for value in (
            _text(event.trace_id),
            _text(event.session_id),
            f"{_text(event.provider)}_{_text(event.session_id)}".strip("_"),
        )
        if value
    }
    if not wanted:
        return []
    try:
        from omnicompany.dashboard.ccdaemon.api import read_meta_store_with_archive

        store = read_meta_store_with_archive()
    except Exception:  # noqa: BLE001 - identity enrichment is fail-open
        return []
    candidates: list[tuple[str, dict[str, Any]]] = []
    for key, raw_meta in store.items():
        meta = dict(raw_meta)
        identifiers = {
            _text(key),
            *(
                _text(meta.get(field))
                for field in (
                    "id",
                    "trace_id",
                    "session_id",
                    "provider_session_id",
                    "claude_session_id",
                )
            ),
        }
        if wanted & {value for value in identifiers if value}:
            candidates.append((_text(key), meta))
    return candidates


def _event_identifiers(event: CompanionEvent) -> set[str]:
    identifiers = {
        value
        for value in (
            _text(event.trace_id),
            _text(event.session_id),
            f"{_text(event.provider)}_{_text(event.session_id)}".strip("_"),
        )
        if value
    }
    for key, meta in _event_meta_candidates(event):
        identifiers.add(key)
        identifiers.update(
            _text(meta.get(field))
            for field in (
                "id",
                "trace_id",
                "session_id",
                "provider_session_id",
                "claude_session_id",
            )
            if _text(meta.get(field))
        )
    return identifiers


def _event_pty_id(event: CompanionEvent) -> str:
    candidates = _event_meta_candidates(event)
    if len(candidates) != 1:
        return ""
    key, meta = candidates[0]
    return _text(meta.get("pty_id") or meta.get("id") or key)


def _linked_entries(identifiers: set[str]) -> list[dict[str, Any]]:
    payload = pgs.load_group(TODO_GROUP) or {}
    return [
        entry
        for entry in payload.get("tasks") or []
        if _entry_identifiers(entry) & identifiers and not bool(entry.get("archived"))
    ]


def _select_entry(entries: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None]:
    if not entries:
        return None, "unlinked"
    active = [entry for entry in entries if _text(entry.get("status")) in {"pending", "in_progress"}]
    if len(active) == 1:
        return active[0], None
    if len(active) > 1:
        return None, "ambiguous_multiple_active_todos"
    # A single paused/review item is still useful evidence, but lifecycle code
    # must not silently revive it.  Multiple historical items are ambiguous.
    if len(entries) == 1:
        return entries[0], None
    return None, "ambiguous_multiple_todos"


def _event_key(trace_id: str, provider: str, session_id: str, user_input: str) -> str:
    return f"turn:{stable_digest(trace_id, provider, session_id, _text(user_input))}"


def _merge_metadata(
    entry: Mapping[str, Any],
    *,
    trace_id: str,
    provider: str,
    session_id: str,
    event_id: str,
    user_input: str,
    trigger: str,
    close_kind: str | None,
    classification: str,
    needs_classification: bool,
) -> dict[str, Any]:
    old = dict(entry.get("legacy") or {})
    old_lifecycle = dict(old.get("session_lifecycle") or {})
    current_key = _event_key(trace_id, provider, session_id, user_input)
    old_lifecycle.update(
        {
            "schema": LIFECYCLE_SCHEMA,
            "source": "session_companion",
            "trace_id": trace_id,
            "provider": provider,
            "session_id": session_id,
            "last_event_id": event_id,
            "last_event_at": _now_iso(),
            "last_trigger": trigger,
            "current_turn_key": current_key if trigger == "user_prompt" else old_lifecycle.get("current_turn_key"),
            "classification": classification,
            "close_kind": close_kind,
            "needs_classification": bool(needs_classification),
        }
    )
    old["session_lifecycle"] = old_lifecycle
    return old_lifecycle


def _status_for_classification(classification: str) -> str | None:
    return {"completed": "done", "dropped": "cancelled"}.get(classification)


def _patch(
    entry: Mapping[str, Any],
    *,
    status: str | None,
    metadata: dict[str, Any],
    association: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {"session_lifecycle": metadata}
    if association:
        fields.update({key: value for key, value in association.items() if value})
    if status is not None:
        fields["status"] = status
    return pgs.patch_task(str(entry["id"]), **fields)


def maintain_event(
    event: CompanionEvent,
    *,
    close_reason: str = "",
    close_actor: str = "",
    close_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply one companion event to the existing session TODO, fail-closed."""

    close_reason = _text(close_reason) or _text(event.close_reason)
    close_actor = _text(close_actor) or _text(event.close_actor)
    close_evidence = close_evidence or event.close_evidence or {}
    trace_id = _text(event.trace_id)
    provider = _text(event.provider) or "unknown"
    session_id = _text(event.session_id)
    if not trace_id:
        return {"state": "unknown", "reason": "missing_trace_id"}
    entries = _linked_entries(_event_identifiers(event))
    entry, selection_reason = _select_entry(entries)
    if entry is None:
        return {
            "state": "unknown",
            "reason": selection_reason or "unlinked",
            "trace_id": trace_id,
            "needs_classification": True,
        }

    event_id = _text(event.event_id) or f"event:{stable_digest(trace_id, event.trigger, event.user_input)}"
    lifecycle = dict(entry.get("legacy") or {}).get("session_lifecycle") or {}
    if _text(lifecycle.get("last_event_id")) == event_id:
        return {"state": "idempotent", "todo_id": entry.get("id"), "event_id": event_id}

    classification = classify_user_intent(event.user_input)
    close_kind: str | None = None
    needs_classification = classification == "unknown"
    next_status: str | None = None
    current_status = _text(entry.get("status"))
    legacy = dict(entry.get("legacy") or {})
    human_status_owned = bool(
        legacy.get("manual_status")
        or _text(legacy.get("status_source")).casefold() == "human"
    )

    if event.trigger == "user_prompt":
        explicit_status = _status_for_classification(classification)
        if explicit_status and current_status not in TERMINAL_STATUSES:
            next_status = explicit_status
        elif classification == "unknown":
            next_status = None
        elif current_status == "pending" and not human_status_owned:
            # Pending is the unstarted state. Paused/review/blocked and all
            # terminal states are human-owned until explicitly changed.
            next_status = "in_progress"
    elif event.trigger in {"stop", "manual"}:
        evidence = dict(close_evidence or {})
        close_kind = classify_close(
            close_reason=close_reason,
            close_actor=close_actor,
            close_evidence=evidence,
        )
        if close_kind == "human" and current_status not in TERMINAL_STATUSES:
            explicit_status = _status_for_classification(classification)
            if explicit_status:
                next_status = explicit_status
            elif classification == "unknown":
                needs_classification = True
            else:
                current_key = _event_key(trace_id, provider, session_id, event.user_input)
                declared_key = _text(lifecycle.get("current_turn_key"))
                if (
                    declared_key
                    and declared_key == current_key
                    and is_agent_completion_declaration(event.assistant_output)
                ):
                    next_status = "done"
                else:
                    next_status = "deferred"
        elif close_kind == "unknown":
            needs_classification = True
        # process_exit deliberately has no status transition; recovery owns
        # the next decision and the open TODO remains recoverable.

    metadata = _merge_metadata(
        entry,
        trace_id=trace_id,
        provider=provider,
        session_id=session_id,
        event_id=event_id,
        user_input=event.user_input,
        trigger=event.trigger,
        close_kind=close_kind,
        classification=classification,
        needs_classification=needs_classification,
    )
    legacy = dict(entry.get("legacy") or {})
    association = {
        # Inventory-created rows were intentionally no-session rows. Once a
        # real hook event supplies the exact trace, promote that same row to a
        # formal session association instead of creating another TODO.
        "session_trace_id": trace_id if not _text(legacy.get("session_trace_id")) else None,
        "provider": provider if not _text(legacy.get("provider")) else None,
        "session_id": session_id if not _text(legacy.get("session_id")) else None,
        "pty_id": _event_pty_id(event) if not _text(legacy.get("pty_id")) else None,
    }
    updated = _patch(entry, status=next_status, metadata=metadata, association=association)
    return {
        "state": "updated" if next_status or metadata != dict(entry.get("legacy") or {}) else "observed",
        "todo_id": updated.get("id"),
        "status": updated.get("status"),
        "previous_status": current_status,
        "transition": next_status,
        "classification": classification,
        "close_kind": close_kind,
        "needs_classification": needs_classification,
        "event_id": event_id,
    }


def associate_inventory_todo(event: CompanionEvent, *, association_event_id: str = "") -> dict[str, Any]:
    """Formally associate one already-audited TODO without changing its status.

    ``audit_sessions`` deliberately creates no-session rows because native
    identity is only safe after the process/native metadata join.  This
    one-shot bridge records that join in the same ``_todo`` row and leaves
    human/receipt-derived status and title untouched.
    """

    trace_id = _text(event.trace_id)
    if not trace_id:
        return {"state": "unknown", "reason": "missing_trace_id"}
    entries = _linked_entries(_event_identifiers(event))
    entry, selection_reason = _select_entry(entries)
    if entry is None:
        return {
            "state": "unknown",
            "reason": selection_reason or "unlinked",
            "trace_id": trace_id,
            "needs_classification": True,
        }
    legacy = dict(entry.get("legacy") or {})
    existing_trace = _text(legacy.get("session_trace_id"))
    if existing_trace and existing_trace != trace_id:
        return {
            "state": "unknown",
            "reason": "conflicting_session_association",
            "todo_id": entry.get("id"),
            "trace_id": trace_id,
            "existing_trace_id": existing_trace,
            "needs_classification": True,
        }
    association_id = _text(association_event_id) or f"association:{stable_digest(trace_id, event.session_id)}"
    old_lifecycle = dict(legacy.get("session_lifecycle") or {})
    if _text(old_lifecycle.get("association_event_id")) == association_id:
        return {"state": "idempotent", "todo_id": entry.get("id"), "event_id": association_id}
    lifecycle = {
        **old_lifecycle,
        "schema": LIFECYCLE_SCHEMA,
        "source": "session_inventory",
        "association_event_id": association_id,
        "association_at": _now_iso(),
        "association_trace_id": trace_id,
        "association_provider": _text(event.provider) or "unknown",
        "association_session_id": _text(event.session_id),
    }
    association = {
        "session_trace_id": trace_id if not existing_trace else None,
        "provider": _text(event.provider) or "unknown" if not _text(legacy.get("provider")) else None,
        "session_id": _text(event.session_id) if not _text(legacy.get("session_id")) else None,
        "pty_id": _event_pty_id(event) if not _text(legacy.get("pty_id")) else None,
    }
    updated = _patch(entry, status=None, metadata=lifecycle, association=association)
    return {
        "state": "updated",
        "todo_id": updated.get("id"),
        "status": updated.get("status"),
        "event_id": association_id,
    }


def resolve_close_evidence(event: CompanionEvent) -> dict[str, Any]:
    """Read-only best-effort join with the existing PTY metadata store.

    The PTY manager writes the kill/exit tombstone before its fallback Stop
    event.  Reading it here avoids changing the old worker's claimed PTY file
    while still distinguishing an HTTP human kill from process exit.
    """

    if event.trigger not in {"stop", "manual"}:
        return {}
    candidates = [meta for _, meta in _event_meta_candidates(event)]
    if len(candidates) != 1:
        return {}
    meta = candidates[0]
    return {
        "close_reason": _text(meta.get("close_reason") or meta.get("exit_reason")),
        "close_actor": _text(meta.get("close_actor")),
        "close_evidence": {
            "requester_ip": meta.get("kill_requester_ip"),
            "requester_user_agent": meta.get("kill_requester_ua"),
            "requester_referer": meta.get("kill_requester_referer"),
        },
    }


def maintain_event_from_queue(event: CompanionEvent) -> dict[str, Any]:
    evidence = resolve_close_evidence(event)
    return maintain_event(
        event,
        close_reason=_text(event.close_reason) or _text(evidence.get("close_reason")),
        close_actor=_text(event.close_actor) or _text(evidence.get("close_actor")),
        close_evidence=event.close_evidence or evidence.get("close_evidence") or {},
    )


__all__ = [
    "LIFECYCLE_SCHEMA",
    "TODO_GROUP",
    "classify_close",
    "classify_user_intent",
    "is_agent_completion_declaration",
    "associate_inventory_todo",
    "maintain_event",
    "maintain_event_from_queue",
    "resolve_close_evidence",
]
