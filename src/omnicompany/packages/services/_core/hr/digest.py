# [OMNI] material_id="material:core.hr.digest.py" origin=codex ts=2026-08-18 type=module status=active agent=codex
# [OMNI] summary="HR 证据 digest：构造有界 actor/Material 证据包、校验 LLM 引用并幂等保存结果"
# [OMNI] tags=hr,digest,evidence,material,llm
"""Deterministic evidence packets and persistence for LLM-authored HR digests."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from .note import HrNote
from .profile import build_actor_profile
from .schema import HrEvent


DIGESTS_FILENAME = "digests.jsonl"
DIGEST_SCHEMA = "omnicompany.hr-digest.v1"
MAX_EVIDENCE_REFS = 80
MAX_EVIDENCE_PER_CHANNEL = 8
MAX_DETAIL_ROWS = 12


class HrDigestRejected(ValueError):
    """The packet or semantic digest violates the evidence-only contract."""


@dataclass
class HrDigestRecord:
    id: str
    created_at: str
    actor_id: str
    task_type: str
    model: str
    packet_fingerprint: str
    digest: dict[str, Any]
    evidence_refs: list[str]
    schema: str = DIGEST_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _event_refs(profile: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for rows in profile.get("objective_evidence", {}).values():
        selected = rows[-MAX_EVIDENCE_PER_CHANNEL:] if isinstance(rows, list) else []
        for row in selected:
            ref = str(row.get("evidence_ref") or "").strip()
            if ref:
                refs.append(ref)
    return refs


def _matching_materials(
    paths: Iterable[str], *, material_index: Any | None, project_root: Path | None
) -> list[dict[str, str]]:
    if material_index is None:
        return []
    root = Path(project_root or Path.cwd())
    wanted = {_path_key(path) for path in paths}
    rows: list[dict[str, str]] = []
    for entry in material_index.list_all():
        raw = Path(str(getattr(entry, "file_path", "") or ""))
        resolved = raw if raw.is_absolute() else root / raw
        if _path_key(str(resolved)) not in wanted:
            continue
        rows.append({
            "material_id": str(getattr(entry, "material_id", "") or ""),
            "file_path": str(resolved.resolve(strict=False)),
            "kind": str(getattr(entry, "kind", "") or ""),
        })
    return sorted(rows, key=lambda row: row["material_id"])


def _packet_fingerprint(packet: dict[str, Any]) -> str:
    payload = {key: value for key, value in packet.items() if key != "packet_fingerprint"}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "digest_packet_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_digest_packet(
    actor_id: str,
    *,
    events: Iterable[HrEvent],
    notes: Iterable[HrNote] = (),
    peer_reviews: Iterable[dict[str, Any]] = (),
    task_type: str | None = None,
    material_index: Any | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Build one bounded evidence packet; no model-generated claims are added here."""
    event_rows = list(events)
    note_rows = list(notes)
    review_rows = list(peer_reviews)
    profile = build_actor_profile(
        actor_id, events=event_rows, notes=note_rows, peer_reviews=review_rows
    )
    reviews = profile["peer_reviews"]["rows"]
    if task_type:
        reviews = [row for row in reviews if str(row.get("task_type") or "general") == task_type]

    matching_notes = profile["feedback"]["rows"]
    evidence_refs = _event_refs(profile)
    for row in matching_notes:
        evidence_refs.extend(str(ref).strip() for ref in row.get("evidence_refs") or [] if str(ref).strip())
        source_ref = str(row.get("source_ref") or "").strip()
        if source_ref:
            evidence_refs.append(source_ref)
    for row in reviews:
        evidence_refs.extend(str(ref).strip() for ref in row.get("evidence") or [] if str(ref).strip())
    evidence_refs = list(dict.fromkeys(evidence_refs))[:MAX_EVIDENCE_REFS]

    objective_channels = profile["objective_evidence"]
    event_count = sum(len(rows) for rows in objective_channels.values())
    user_feedback_count = sum(1 for row in matching_notes if row.get("source") == "user")
    materials = _matching_materials(
        profile["targets"]["paths"], material_index=material_index, project_root=project_root
    )
    takeovers = objective_channels.get("cross_actor_takeovers") or []
    review_reminders = [
        {
            "reason": "cross_actor_takeover_requires_human_or_agent_review",
            "target": row.get("target"),
            "previous_actor_id": row.get("previous_actor_id"),
            "evidence_refs": [row.get("evidence_ref")] if row.get("evidence_ref") in evidence_refs else [],
        }
        for row in takeovers[:MAX_DETAIL_ROWS]
    ]

    packet: dict[str, Any] = {
        "schema": "omnicompany.hr-digest-packet.v1",
        "actor_id": actor_id,
        "identity": profile["identity"],
        "task_type": task_type or "all_observed_task_types",
        "objective": profile["objective"],
        "objective_counts": {name: len(rows) for name, rows in objective_channels.items()},
        "feedback_summary": profile["feedback"]["summary"],
        "feedback_rows": matching_notes[-MAX_DETAIL_ROWS:],
        "evidence_policy": profile["evidence_policy"],
        "peer_review_summary": {
            "total": len(reviews),
            "pass": sum(row.get("severity") == "pass" for row in reviews),
            "imperfect": sum(row.get("severity") == "imperfect" for row in reviews),
            "severe": sum(row.get("severity") == "severe" for row in reviews),
            "duplicate_rows_ignored_globally": profile["peer_reviews"]["duplicate_rows_ignored_globally"],
        },
        "peer_review_rows": reviews[-MAX_DETAIL_ROWS:],
        "materials": materials[:MAX_DETAIL_ROWS],
        "review_reminders": review_reminders,
        "speed_evidence": {
            "status": "insufficient",
            "reason": "events do not separate active work from queueing, pauses, or external waits",
        },
        "signal_completeness": {
            "objective_event_rows": event_count,
            "user_feedback_rows": user_feedback_count,
            "peer_review_rows": len(reviews),
            "material_links": len(materials),
            "comparable_cohort_available": False,
        },
        "allowed_evidence_refs": evidence_refs,
        "constraints": [
            "Treat explicit user feedback as primary evidence and peer review as auxiliary.",
            "Peer review cannot override explicit user feedback.",
            "Do not create a cross-provider or cross-task ranking.",
            "Do not convert missing feedback into a pass.",
            "Every strength, risk, speed or review-reminder claim must cite allowed_evidence_refs.",
            "A cross-actor takeover is a review reminder, not proof of a defect.",
        ],
    }
    packet["packet_fingerprint"] = _packet_fingerprint(packet)
    return packet


def validate_digest_output(output: dict[str, Any], packet: dict[str, Any]) -> None:
    if not isinstance(output, dict):
        raise HrDigestRejected("digest output must be an object")
    allowed = set(packet.get("allowed_evidence_refs") or [])
    for channel in ("strength_signals", "risk_signals", "review_reminders"):
        for row in output.get(channel) or []:
            refs = [str(ref) for ref in row.get("evidence_refs") or []]
            unknown = [ref for ref in refs if ref not in allowed]
            if unknown:
                raise HrDigestRejected(f"{channel} cites evidence outside the packet: {unknown}")
    speed = output.get("speed_assessment") or {}
    speed_refs = [str(ref) for ref in speed.get("evidence_refs") or []]
    unknown_speed = [ref for ref in speed_refs if ref not in allowed]
    if unknown_speed:
        raise HrDigestRejected(f"speed_assessment cites evidence outside the packet: {unknown_speed}")
    if packet.get("speed_evidence", {}).get("status") == "insufficient" and speed.get("status") != "insufficient":
        raise HrDigestRejected("speed assessment must remain insufficient without active-work timing evidence")
    if output.get("overall_conclusion") not in {"evidence_summary_only", "insufficient_for_comparison"}:
        raise HrDigestRejected("overall_conclusion cannot imply a ranking or unsupported performance verdict")


def _digests_path(data_dir: Path) -> Path:
    return Path(data_dir) / DIGESTS_FILENAME


def read_digests(*, data_dir: Path) -> list[HrDigestRecord]:
    path = _digests_path(data_dir)
    if not path.is_file():
        return []
    known = set(HrDigestRecord.__dataclass_fields__)
    rows: list[HrDigestRecord] = []
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict) or raw.get("schema") != DIGEST_SCHEMA:
                continue
            try:
                rows.append(HrDigestRecord(**{key: value for key, value in raw.items() if key in known}))
            except TypeError:
                continue
    return rows


def cached_digest(*, data_dir: Path, packet_fingerprint: str, model: str) -> HrDigestRecord | None:
    return next(
        (
            row for row in reversed(read_digests(data_dir=data_dir))
            if row.packet_fingerprint == packet_fingerprint and row.model == model
        ),
        None,
    )


def append_digest(
    *, data_dir: Path, packet: dict[str, Any], model: str, digest: dict[str, Any]
) -> HrDigestRecord:
    validate_digest_output(digest, packet)
    record = HrDigestRecord(
        id=f"digest_{uuid4().hex}",
        created_at=_now_iso(),
        actor_id=str(packet.get("actor_id") or ""),
        task_type=str(packet.get("task_type") or ""),
        model=model,
        packet_fingerprint=str(packet.get("packet_fingerprint") or ""),
        digest=digest,
        evidence_refs=list(packet.get("allowed_evidence_refs") or []),
    )
    path = _digests_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    descriptor = os.open(str(path), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(descriptor, raw)
    finally:
        os.close(descriptor)
    return record


__all__ = [
    "DIGESTS_FILENAME", "DIGEST_SCHEMA", "HrDigestRejected", "HrDigestRecord",
    "build_digest_packet", "validate_digest_output", "read_digests", "cached_digest",
    "append_digest",
]
