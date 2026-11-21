# [OMNI] origin=codex domain=services/session-companion ts=2026-08-11 type=service
# [OMNI] material_id="material:services.session-companion.audit.py"
"""Deterministic health classification for companion execution receipts.

Queue state answers whether execution finished.  This module separately answers
whether the requested maintenance outcome was actually closed, degraded, or
left for an operator.  It derives only from the immutable event, validated
proposal, provider audit and deterministic executor receipts.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from .models import stable_digest
from .policy import explicit_review_requested

AUDIT_STATUSES = ("healthy", "degraded", "action_required", "failed")
def _finding(code: str, message: str, *, source: str = "") -> dict[str, str]:
    value = {"code": code, "message": str(message)[:1000]}
    if source:
        value["source"] = source
    return value


def execution_digest(record: Mapping[str, Any]) -> str:
    """Seal the evidence-bearing execution fields of one attempt."""

    evidence = (
        record.get("event") or {},
        str(record.get("proposal_digest") or ""),
        record.get("agent") or {},
        str(record.get("receipts_digest") or ""),
        str(record.get("state") or ""),
        str(record.get("last_error") or ""),
    )
    version = str(record.get("execution_digest_version") or "1")
    if version == "1":
        return stable_digest(*evidence, size=32)
    return stable_digest(
        f"execution-digest-v{version}",
        *evidence,
        int(record.get("attempt") or 0),
        str(record.get("queued_at") or ""),
        str(record.get("started_at") or ""),
        str(record.get("finished_at") or ""),
        record.get("attempt_history") or [],
        size=32,
    )


def audit_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Classify one queue record without mutating it.

    ``done`` is deliberately not treated as synonymous with success: skipped
    writes and unresolved user feedback require an operator even when provider
    execution and queue movement completed normally.
    """

    state = str(record.get("state") or "")
    event = record.get("event") if isinstance(record.get("event"), Mapping) else {}
    receipts = [row for row in record.get("receipts") or [] if isinstance(row, Mapping)]
    agent = record.get("agent") if isinstance(record.get("agent"), Mapping) else {}
    counts: Counter[str] = Counter()
    action_required: list[dict[str, str]] = []
    degraded: list[dict[str, str]] = []

    for receipt in receipts:
        action = str(receipt.get("action") or "unknown")
        status = str(receipt.get("status") or "unknown")
        counts[f"{action}:{status}"] += 1
        if action == "unresolved":
            action_required.append(
                _finding("unresolved", str(receipt.get("message") or "unresolved maintenance item"), source=action)
            )
        elif status == "skipped":
            reason = str(receipt.get("reason") or "executor skipped the action")
            action_required.append(_finding(f"{action}_skipped", reason, source=action))
        for warning in receipt.get("warnings") or []:
            degraded.append(_finding(f"{action}_warning", str(warning), source=action))

    fallback_errors = [str(item) for item in agent.get("fallback_errors") or [] if item]
    for error in fallback_errors:
        degraded.append(_finding("provider_fallback", error, source="agent"))
    if bool(agent.get("validation_repaired")):
        degraded.append(
            _finding("proposal_repaired", "initial provider output required JSON/schema repair", source="agent")
        )
    for search in agent.get("searches") or []:
        if not isinstance(search, Mapping):
            continue
        exit_code = search.get("exit_code")
        error = str(search.get("error") or "")
        if error or (exit_code is not None and int(exit_code) != 0):
            degraded.append(
                _finding(
                    "search_failed",
                    f"query={search.get('query')!s}: {error or f'exit_code={exit_code}'}",
                    source="search",
                )
            )

    proposal = record.get("proposal") if isinstance(record.get("proposal"), Mapping) else {}
    if proposal:
        calculated_proposal_digest = stable_digest(proposal, size=32)
        stored_proposal_digest = str(record.get("proposal_digest") or "")
        agent_proposal_digest = str(agent.get("proposal_digest") or "")
        if stored_proposal_digest and stored_proposal_digest != calculated_proposal_digest:
            action_required.append(
                _finding("proposal_digest_mismatch", "stored proposal digest does not match proposal", source="integrity")
            )
        if agent_proposal_digest and agent_proposal_digest != calculated_proposal_digest:
            action_required.append(
                _finding("agent_proposal_digest_mismatch", "agent proposal digest does not match executed proposal", source="integrity")
            )
    stored_receipts_digest = str(record.get("receipts_digest") or "")
    if stored_receipts_digest and stored_receipts_digest != stable_digest(receipts, size=32):
        action_required.append(
            _finding("receipts_digest_mismatch", "stored receipts digest does not match executor receipts", source="integrity")
        )
    stored_execution_digest = str(record.get("execution_digest") or "")
    if stored_execution_digest and stored_execution_digest != execution_digest(record):
        action_required.append(
            _finding(
                "execution_digest_mismatch",
                "sealed event/proposal/agent/receipts execution evidence does not match",
                source="integrity",
            )
        )
    elif state in {"done", "failed"} and not stored_execution_digest:
        action_required.append(
            _finding(
                "execution_digest_missing",
                "terminal record predates or is missing an execution evidence seal",
                source="integrity",
            )
        )

    material_closed = any(
        str(row.get("action") or "") == "material"
        and str(row.get("status") or "") in {"applied", "duplicate"}
        for row in receipts
    )
    if state == "done" and explicit_review_requested(str(event.get("user_input") or "")) and not material_closed:
        action_required.append(
            _finding(
                "explicit_review_not_submitted",
                "user explicitly requested review but no material was applied or deduplicated",
                source="event",
            )
        )

    if state == "failed":
        status = "failed"
        failure = str(record.get("last_error") or "provider/executor failure")
        action_required.insert(0, _finding("execution_failed", failure, source="queue"))
    elif action_required:
        status = "action_required"
    elif degraded:
        status = "degraded"
    elif state == "done" and receipts:
        status = "healthy"
    elif state in {"queued", "running"}:
        status = state
    else:
        status = "degraded"
        degraded.append(_finding("missing_receipts", "completed record has no executor receipts", source="queue"))

    findings = [*action_required, *degraded]
    integrity_failed = any(str(row.get("source") or "") == "integrity" for row in findings)
    return {
        "status": status,
        "findings": findings,
        "finding_count": len(findings),
        "action_counts": dict(sorted(counts.items())),
        "explicit_review_requested": explicit_review_requested(str(event.get("user_input") or "")),
        "proposal_recorded": bool(proposal),
        "integrity": "failed" if integrity_failed else "sealed" if stored_execution_digest else "unsealed",
        "replay_allowed": status == "action_required" and bool(stored_execution_digest) and not integrity_failed,
        "retry_allowed": state == "failed" and bool(stored_execution_digest) and not integrity_failed,
    }


def audit_record_with_effects(record: Mapping[str, Any]) -> dict[str, Any]:
    """Augment receipt audit by checking that referenced canonical records exist."""

    audit = dict(audit_record(record))
    findings = list(audit.get("findings") or [])
    checks: list[dict[str, Any]] = []
    event = record.get("event") if isinstance(record.get("event"), Mapping) else {}
    try:
        from omnicompany.packages.services.boss_sight.authored.store import get_authored_store
        from omnicompany.packages.services.boss_sight.services.agent_digest import get_digest
        from omnicompany.packages.domains.decisions import library as decisions_library
        from omnicompany.packages.services._core import review_store
        from omnicompany.packages.services._core.identity import session_scope_segments

        authored_store = get_authored_store()
        scope_ids = {
            str(row.get("segment_id") or "")
            for row in session_scope_segments(str(event.get("trace_id") or ""))
            if isinstance(row, Mapping)
        }
        for receipt in record.get("receipts") or []:
            if not isinstance(receipt, Mapping) or str(receipt.get("status") or "") not in {"applied", "duplicate"}:
                continue
            action = str(receipt.get("action") or "")
            targets: list[tuple[str, str, bool]] = []
            material_id = str(receipt.get("material_id") or "")
            decision_id = str(receipt.get("decision_id") or "")
            note_id = str(receipt.get("note_id") or "")
            segment_id = str(receipt.get("segment_id") or "")
            if material_id:
                targets.append(("material", material_id, review_store.get_material(material_id) is not None))
            if decision_id:
                targets.append(("decision", decision_id, decisions_library.get(decision_id) is not None))
            if note_id:
                targets.append(("authored_note", note_id, authored_store.get(note_id) is not None))
            if segment_id:
                targets.append(("scope_segment", segment_id, segment_id in scope_ids))
            if action == "digest":
                provider = str(event.get("provider") or "")
                session_id = str(event.get("session_id") or "")
                targets.append(("digest", f"{provider}:{session_id}", bool(get_digest(provider, session_id))))
            for target_kind, target_id, exists in targets:
                checks.append({
                    "action": action,
                    "target_kind": target_kind,
                    "target_id": target_id,
                    "exists": exists,
                })
                if not exists:
                    findings.append(
                        _finding(
                            "effect_missing",
                            f"{action} receipt references missing {target_kind} {target_id}",
                            source="effect_verification",
                        )
                    )
    except Exception as exc:  # noqa: BLE001 - audit remains queryable if one truth source is offline
        findings.append(
            _finding(
                "effect_verification_failed",
                f"{type(exc).__name__}: {exc}",
                source="effect_verification",
            )
        )

    effect_missing = any(row.get("code") == "effect_missing" for row in findings)
    verify_failed = any(row.get("code") == "effect_verification_failed" for row in findings)
    if audit.get("status") != "failed" and effect_missing:
        audit["status"] = "action_required"
    elif audit.get("status") == "healthy" and verify_failed:
        audit["status"] = "degraded"
    audit["findings"] = findings
    audit["finding_count"] = len(findings)
    audit["effect_checks"] = checks
    return audit


__all__ = [
    "AUDIT_STATUSES",
    "audit_record",
    "audit_record_with_effects",
    "execution_digest",
    "explicit_review_requested",
]
