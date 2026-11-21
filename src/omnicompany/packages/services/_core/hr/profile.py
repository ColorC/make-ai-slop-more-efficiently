# [OMNI] material_id="material:core.hr.profile.py" origin=codex ts=2026-08-18 type=module status=active agent=codex
# [OMNI] summary="按 actor 汇合客观事件、用户反馈与去重互评，保留证据并拒绝生成跨域总分"
# [OMNI] tags=hr,actor-profile,feedback,peer-review,evidence
"""Evidence-first actor views for the HR attribution system."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from omnicompany.packages.services._core.agent.resource_control import (
    REVIEW_SCHEMA,
    deduplicate_peer_reviews,
)

from .blame import DEFAULT_MINIMUM_SAMPLES, compute_defect_rates, sessions_touching
from .note import HrNote, summarize_notes
from .schema import HrEvent


def read_peer_reviews(path: Path) -> list[dict[str, Any]]:
    """Read valid review rows without mutating or repairing the source ledger."""
    if not Path(path).is_file():
        return []
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("schema") == REVIEW_SCHEMA:
                rows.append(row)
    return rows


def _parse_actor_id(actor_id: str) -> tuple[str, str, str]:
    parts = actor_id.split(":", 2)
    if len(parts) != 3 or not all(parts):
        raise ValueError("actor_id must be 'provider:model:session_id'")
    return parts[0], parts[1], parts[2]


def _event_actor_id(event: HrEvent) -> str:
    return f"{event.provider or 'unknown'}:{event.model or 'unknown'}:{event.session_id or 'unknown'}"


def _event_evidence(event: HrEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "operation_id": event.operation_id,
        "ts": event.ts,
        "kind": event.kind,
        "target": event.target,
        "verdict": event.verdict,
        "evidence_ref": event.evidence_ref,
    }


def _objective_evidence(actor_id: str, events: list[HrEvent]) -> dict[str, Any]:
    matching = [event for event in events if _event_actor_id(event) == actor_id]
    channels = {
        "writes": [
            _event_evidence(event)
            for event in matching
            if event.kind == "write" and event.verdict == "applied"
        ],
        "denied": [
            _event_evidence(event)
            for event in matching
            if event.kind == "denial" or event.verdict == "denied"
        ],
        "stray": [_event_evidence(event) for event in matching if event.kind == "stray"],
        "errors": [_event_evidence(event) for event in matching if event.verdict == "error"],
    }

    by_target: dict[str, list[HrEvent]] = {}
    for event in events:
        if event.kind == "write" and event.verdict == "applied" and event.target:
            by_target.setdefault(event.target, []).append(event)
    takeovers: list[dict[str, Any]] = []
    for target, target_events in by_target.items():
        previous_actor = ""
        for event in sorted(target_events, key=lambda item: item.ts):
            current_actor = _event_actor_id(event)
            if previous_actor and current_actor != previous_actor and current_actor == actor_id:
                takeovers.append({
                    "target": target,
                    "previous_actor_id": previous_actor,
                    "actor_id": current_actor,
                    "ts": event.ts,
                    "event_id": event.event_id,
                    "evidence_ref": event.evidence_ref,
                    "interpretation": "cross_actor_takeover_proxy_not_proof_of_defect",
                })
            previous_actor = current_actor
    channels["cross_actor_takeovers"] = takeovers
    return channels


def build_actor_profile(
    actor_id: str,
    *,
    events: Iterable[HrEvent],
    notes: Iterable[HrNote] = (),
    peer_reviews: Iterable[dict[str, Any]] = (),
    minimum_samples: int = DEFAULT_MINIMUM_SAMPLES,
) -> dict[str, Any]:
    """Join separate evidence channels for one actor without inventing a score."""
    provider, model, session_id = _parse_actor_id(actor_id)
    event_rows = list(events)
    note_rows = list(notes)
    raw_reviews = list(peer_reviews)
    unique_reviews, duplicate_rows_ignored = deduplicate_peer_reviews(raw_reviews)

    matching_reviews = [
        row
        for row in unique_reviews
        if str(row.get("subject_session") or "") == session_id
        and str(row.get("subject_model") or "") == model
    ]
    peer_counts = {"pass": 0, "imperfect": 0, "severe": 0}
    for row in matching_reviews:
        severity = str(row.get("severity") or "")
        if severity in peer_counts:
            peer_counts[severity] += 1

    note_summary = summarize_notes(note_rows)
    feedback_row = note_summary["by_actor"].get(actor_id, {
        "total": 0,
        "pass": 0,
        "imperfect": 0,
        "severe": 0,
        "by_source": {"user": 0, "agent": 0},
        "by_attribution_tier": {},
        "note_ids": [],
    })
    matching_notes = [item.to_dict() for item in note_rows if item.actor_id == actor_id]
    explicit_user_feedback = [row for row in matching_notes if row.get("source") == "user"]
    objective = compute_defect_rates(event_rows, minimum_samples=minimum_samples).get(actor_id)
    objective_evidence = _objective_evidence(actor_id, event_rows)
    targets = sessions_touching(actor_id, events=event_rows)

    return {
        "actor_id": actor_id,
        "identity": {"provider": provider, "model": model, "session_id": session_id},
        "objective": objective,
        "objective_evidence": objective_evidence,
        "targets": {"count": len(targets), "paths": targets},
        "evidence_policy": {
            "primary": "user_feedback",
            "auxiliary": "peer_review",
            "peer_review_can_override_user": False,
        },
        "feedback": {
            "role": "primary",
            "summary": feedback_row,
            "rows": matching_notes,
            "explicit_user_rows": explicit_user_feedback,
        },
        "peer_reviews": {
            "role": "auxiliary",
            "suppressed_for_routing_by_explicit_user_feedback": bool(explicit_user_feedback),
            "summary": {**peer_counts, "total": len(matching_reviews)},
            "rows": matching_reviews,
            "duplicate_rows_ignored_globally": duplicate_rows_ignored,
        },
        "quality_score": None,
        "ranking": None,
        "caveats": [
            "Objective counts, user feedback and peer reviews are separate evidence channels.",
            "Explicit user feedback is primary; peer review is auxiliary and cannot override it.",
            "cross-actor file takeover is a handoff/rework proxy, not proof of a defect.",
            "No cross-provider or cross-task quality ranking is produced by this view.",
        ],
    }


__all__ = ["build_actor_profile", "read_peer_reviews"]
