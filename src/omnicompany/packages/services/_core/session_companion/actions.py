# [OMNI] origin=codex domain=services/session-companion ts=2026-08-11 type=service
# [OMNI] material_id="material:services.session-companion.actions.py"
"""Strict normalization for model-proposed maintenance actions."""

from __future__ import annotations

from typing import Any, Mapping

from .models import CompanionEvent, stable_digest
from .policy import explicit_decision_quote, material_submission_allowed

ALLOWED_MATERIAL_KINDS = {"markdown"}
ALLOWED_MATERIAL_TIERS = {"mandatory", "important"}
ALLOWED_FEEDBACK_KINDS = {"accept", "reject", "comment", "update"}


class ActionValidationError(ValueError):
    """Model output is malformed or cites evidence outside the event."""


def _string(value: Any, *, limit: int = 4000) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ActionValidationError(f"{field} must be an object")
    return dict(value)


def _list(value: Any, field: str, *, limit: int) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ActionValidationError(f"{field} must be an array")
    return value[:limit]


def _evidence(value: Any, event: CompanionEvent) -> list[str]:
    refs = [_string(ref, limit=500) for ref in _list(value, "evidence_refs", limit=12)]
    refs = list(dict.fromkeys(ref for ref in refs if ref))
    allowed = set(event.evidence_refs)
    unknown = [ref for ref in refs if ref not in allowed]
    if unknown:
        raise ActionValidationError(f"unknown evidence refs: {unknown}")
    return refs


def validate_actions(payload: Mapping[str, Any], event: CompanionEvent) -> dict[str, Any]:
    """Return the small, executable subset of a companion response."""

    if not isinstance(payload, Mapping):
        raise ActionValidationError("companion output must be a JSON object")
    root = dict(payload)
    queries = [
        _string(item, limit=120)
        for item in _list(root.get("search_queries"), "search_queries", limit=3)
        if _string(item, limit=120)
    ]

    raw_digest = _mapping(root.get("digest"), "digest")
    digest = {
        "project": _string(raw_digest.get("project"), limit=60),
        "workstream": _string(raw_digest.get("workstream"), limit=80),
        "plan": _string(raw_digest.get("plan"), limit=160),
        "title": _string(raw_digest.get("title"), limit=80),
        "last_step": _string(raw_digest.get("last_step"), limit=180),
        "summary": _string(raw_digest.get("summary"), limit=1200),
    }

    scopes: list[dict[str, Any]] = []
    for index, value in enumerate(_list(root.get("scope_segments"), "scope_segments", limit=4)):
        row = _mapping(value, f"scope_segments[{index}]")
        evidence_refs = _evidence(row.get("evidence_refs"), event)
        scope = {
            "segment_id": _string(row.get("segment_id"), limit=120),
            "project": _string(row.get("project"), limit=100),
            "workstream": _string(row.get("workstream"), limit=160),
            "plan": _string(row.get("plan"), limit=240),
            "rationale": _string(row.get("rationale"), limit=500),
            "evidence_refs": evidence_refs,
            "make_default": bool(row.get("make_default", False)),
            "confidence": min(1.0, max(0.0, float(row.get("confidence") or 0.0))),
        }
        if not scope["segment_id"]:
            scope["segment_id"] = f"companion-{stable_digest(event.event_id, index)}"
        if any(scope[key] for key in ("project", "workstream", "plan")) and evidence_refs:
            scopes.append(scope)

    decisions: list[dict[str, Any]] = []
    for index, value in enumerate(_list(root.get("decisions"), "decisions", limit=2)):
        row = _mapping(value, f"decisions[{index}]")
        evidence_refs = _evidence(row.get("evidence_refs"), event)
        evidence_quote = _string(row.get("evidence_quote"), limit=1200)

        def _decision_options(field: str) -> list[dict[str, str]]:
            options: list[dict[str, str]] = []
            for option_index, option_value in enumerate(_list(row.get(field), field, limit=4)):
                option = _mapping(option_value, f"decisions[{index}].{field}[{option_index}]")
                label = _string(option.get("option"), limit=300)
                why = _string(option.get("why"), limit=800)
                if label:
                    options.append({"option": label, "why": why})
            return options

        decision = {
            "semantic_key": _string(row.get("semantic_key"), limit=160),
            "statement": _string(row.get("statement"), limit=500),
            "rationale": _string(row.get("rationale"), limit=2000),
            "boundary": _string(row.get("boundary"), limit=1000),
            "applies_to": _string(row.get("applies_to"), limit=500),
            "project": _string(row.get("project") or event.project, limit=100),
            "plan": _string(row.get("plan") or event.active_plan, limit=240),
            "chosen": _decision_options("chosen"),
            "rejected": _decision_options("rejected"),
            "evidence_quote": evidence_quote,
            "evidence_refs": evidence_refs,
        }
        if not decision["semantic_key"]:
            decision["semantic_key"] = "decision:" + stable_digest(
                decision["project"],
                decision["statement"],
            )
        if (
            decision["statement"]
            and evidence_refs
            and explicit_decision_quote(event.user_input, evidence_quote)
        ):
            decisions.append(decision)

    # Deterministic hard gate: ReviewStage is deny-by-default. A material may be
    # created only when the current turn is an explicit, direct submission
    # request. Anything the model proposes off ordinary functional discussion is
    # dropped here — regardless of how well-formed it looks — so an over-eager or
    # misbehaving proposer can never turn conversation into a review material.
    materials: list[dict[str, Any]] = []
    material_rows = _list(root.get("materials"), "materials", limit=1)
    if material_rows and not material_submission_allowed(event.user_input):
        material_rows = []
    for index, value in enumerate(material_rows):
        row = _mapping(value, f"materials[{index}]")
        evidence_refs = _evidence(row.get("evidence_refs"), event)
        kind = _string(row.get("kind") or "markdown", limit=40)
        tier = _string(row.get("tier") or "important", limit=40)
        if kind not in ALLOWED_MATERIAL_KINDS:
            raise ActionValidationError(f"unsupported material kind: {kind}")
        if tier not in ALLOWED_MATERIAL_TIERS:
            raise ActionValidationError(f"unsupported material tier: {tier}")
        material = {
            "semantic_key": _string(row.get("semantic_key"), limit=160),
            "title": _string(row.get("title"), limit=200),
            "kind": kind,
            "tier": tier,
            "project": _string(row.get("project") or event.project, limit=100),
            "plan": _string(row.get("plan") or event.active_plan, limit=240),
            "track": _string(row.get("track") or "工作报告", limit=100),
            "content": _string(row.get("content"), limit=60_000),
            "rationale": _string(row.get("rationale"), limit=800),
            "evidence_refs": evidence_refs,
        }
        if not material["semantic_key"]:
            material["semantic_key"] = "material:" + stable_digest(
                event.trace_id, material["project"], material["title"], material["content"]
            )
        if material["title"] and material["content"] and material["rationale"] and evidence_refs:
            materials.append(material)

    feedback: list[dict[str, Any]] = []
    for index, value in enumerate(_list(root.get("feedback"), "feedback", limit=10)):
        row = _mapping(value, f"feedback[{index}]")
        kind = _string(row.get("kind"), limit=30)
        if kind not in ALLOWED_FEEDBACK_KINDS:
            raise ActionValidationError(f"unsupported feedback kind: {kind}")
        evidence_refs = _evidence(row.get("evidence_refs"), event)
        action = {
            "semantic_key": _string(row.get("semantic_key"), limit=160),
            "material_id": _string(row.get("material_id"), limit=120),
            "kind": kind,
            "content": _string(row.get("content"), limit=8000),
            "reason": _string(row.get("reason"), limit=2000),
            "explicit": bool(row.get("explicit", False)),
            "evidence_refs": evidence_refs,
        }
        if not action["semantic_key"]:
            action["semantic_key"] = "feedback:" + stable_digest(
                event.trace_id,
                action["material_id"],
                action["kind"],
                action["content"],
                action["reason"],
            )
        if action["material_id"] and evidence_refs:
            feedback.append(action)

    unresolved = [
        _string(item, limit=1000)
        for item in _list(root.get("unresolved"), "unresolved", limit=12)
        if _string(item, limit=1000)
    ]
    return {
        "search_queries": queries,
        "digest": digest,
        "scope_segments": scopes,
        "decisions": decisions,
        "materials": materials,
        "feedback": feedback,
        "unresolved": unresolved,
    }


__all__ = ["ActionValidationError", "validate_actions"]
