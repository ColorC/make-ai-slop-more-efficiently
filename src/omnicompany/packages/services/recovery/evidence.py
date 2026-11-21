# [OMNI] origin=claude-code created_by=omni-recover intent=freeze-byte-safe-recovery-plans
"""Normalized, byte-preserving evidence and conservative recovery planning.

The model intentionally separates *what bytes were observed* from *whether the
observation is allowed to become a workspace winner*. A Read can rescue a
missing file when no mutation post-image exists, but it can never supersede a
Write/Edit/patch post-image merely because its timestamp is newer. Attachments
without an exact workspace path are recoverable artifacts, not workspace files.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable


SCHEMA_VERSION = 1
MUTATION_AUTHORITIES = {"mutation-postimage", "proved-command-postimage"}
READ_AUTHORITIES = {"read-exact", "attachment-exact-path"}
DESTRUCTIVE_AUTHORITIES = {"delete-intent", "move-intent"}
NON_WINNER_AUTHORITIES = {"intent-only", "artifact-exact", "derivative-artifact", "unknown"}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _parse_time(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


@dataclass(frozen=True)
class EvidenceRecord:
    """One provider observation normalized without losing byte identity."""

    evidence_id: str
    provider: str
    session_id: str
    timestamp: str
    sequence: int
    kind: str
    authority: str
    source_locator: str
    path: str | None = None
    artifact_name: str | None = None
    mime_type: str | None = None
    content_b64: str | None = None
    content_sha256: str | None = None
    content_length: int | None = None
    causal_parent: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported evidence schema {self.schema_version}")
        if not self.evidence_id or not self.provider or not self.session_id:
            raise ValueError("evidence_id, provider, and session_id are required")
        if self.authority not in (
            MUTATION_AUTHORITIES | READ_AUTHORITIES | DESTRUCTIVE_AUTHORITIES | NON_WINNER_AUTHORITIES
        ):
            raise ValueError(f"unknown evidence authority: {self.authority}")
        if self.content_b64 is None:
            if self.content_sha256 is not None or self.content_length is not None:
                raise ValueError("hash/length require exact content bytes")
            return
        try:
            content = base64.b64decode(self.content_b64, validate=True)
        except Exception as exc:  # binascii.Error is intentionally not leaked as API.
            raise ValueError("content_b64 is not valid base64") from exc
        if self.content_sha256 != _sha256(content):
            raise ValueError("content_sha256 does not match content_b64")
        if self.content_length != len(content):
            raise ValueError("content_length does not match content_b64")

    @classmethod
    def from_bytes(
        cls,
        *,
        evidence_id: str,
        provider: str,
        session_id: str,
        timestamp: str,
        sequence: int,
        kind: str,
        authority: str,
        source_locator: str,
        content: bytes,
        path: str | None = None,
        artifact_name: str | None = None,
        mime_type: str | None = None,
        causal_parent: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "EvidenceRecord":
        return cls(
            evidence_id=evidence_id,
            provider=provider,
            session_id=session_id,
            timestamp=timestamp,
            sequence=sequence,
            kind=kind,
            authority=authority,
            source_locator=source_locator,
            path=path,
            artifact_name=artifact_name,
            mime_type=mime_type,
            content_b64=base64.b64encode(content).decode("ascii"),
            content_sha256=_sha256(content),
            content_length=len(content),
            causal_parent=causal_parent,
            metadata=metadata or {},
        )

    @property
    def has_exact_bytes(self) -> bool:
        """True even for an exact empty file."""

        return self.content_b64 is not None

    def content_bytes(self) -> bytes:
        if self.content_b64 is None:
            raise ValueError(f"evidence {self.evidence_id} has no exact bytes")
        return base64.b64decode(self.content_b64, validate=True)

    def as_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "EvidenceRecord":
        return cls(**payload)


@dataclass(frozen=True)
class RecoveryAction:
    action_id: str
    decision: str
    target_path: str | None
    artifact_relative_path: str | None
    candidate_sha256: str | None
    candidate_length: int | None
    content_b64: str | None
    winner_evidence_id: str | None
    authority: str | None
    provenance: tuple[str, ...]
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    before_exists: bool | None = None
    before_sha256: str | None = None


@dataclass(frozen=True)
class RecoveryPlan:
    workspace_root: str
    actions: tuple[RecoveryAction, ...]
    generated_at: str
    plan_sha256: str
    schema_version: int = SCHEMA_VERSION

    def payload_without_hash(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "workspace_root": self.workspace_root,
            "generated_at": self.generated_at,
            "actions": [asdict(action) for action in self.actions],
        }

    def as_json(self) -> dict[str, Any]:
        return {**self.payload_without_hash(), "plan_sha256": self.plan_sha256}

    def verify(self) -> None:
        expected = _sha256(_canonical_bytes(self.payload_without_hash()))
        if expected != self.plan_sha256:
            raise ValueError("recovery plan hash mismatch")
        for action in self.actions:
            if action.content_b64 is None:
                continue
            content = base64.b64decode(action.content_b64, validate=True)
            if action.candidate_sha256 != _sha256(content) or action.candidate_length != len(content):
                raise ValueError(f"candidate hash mismatch for {action.action_id}")

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "RecoveryPlan":
        actions = tuple(RecoveryAction(**row) for row in payload["actions"])
        plan = cls(
            workspace_root=payload["workspace_root"],
            actions=actions,
            generated_at=payload["generated_at"],
            plan_sha256=payload["plan_sha256"],
            schema_version=payload.get("schema_version", SCHEMA_VERSION),
        )
        plan.verify()
        return plan


def _stream_winner(records: list[EvidenceRecord]) -> EvidenceRecord:
    """Provider sequence is authoritative inside one provider session."""

    return max(records, key=lambda row: (row.sequence, _parse_time(row.timestamp), row.evidence_id))


def _latest_candidates(records: list[EvidenceRecord]) -> tuple[list[EvidenceRecord], list[str]]:
    streams: dict[tuple[str, str], list[EvidenceRecord]] = {}
    for record in records:
        streams.setdefault((record.provider, record.session_id), []).append(record)
    winners = [_stream_winner(rows) for rows in streams.values()]
    if not winners:
        return [], []

    by_id = {record.evidence_id: record for record in records}
    parent_ids = {record.causal_parent for record in winners if record.causal_parent}
    causally_superseded = {candidate for candidate in parent_ids if candidate in by_id}
    active = [record for record in winners if record.evidence_id not in causally_superseded]
    if not active:
        active = winners
    newest_time = max(_parse_time(record.timestamp) for record in active)
    newest = [record for record in active if _parse_time(record.timestamp) == newest_time]
    hashes = {record.content_sha256 for record in newest}
    blockers = []
    if len(hashes) > 1:
        blockers.append("concurrent-exact-postimages-disagree")
    return sorted(newest, key=lambda row: (row.provider, row.session_id, row.sequence, row.evidence_id)), blockers


def _is_after(record: EvidenceRecord, candidate: EvidenceRecord) -> bool:
    if (record.provider, record.session_id) == (candidate.provider, candidate.session_id):
        return record.sequence > candidate.sequence
    if record.causal_parent == candidate.evidence_id:
        return True
    return _parse_time(record.timestamp) > _parse_time(candidate.timestamp)


def _resolve_target(workspace: Path, raw_path: str) -> Path | None:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(workspace)
    except ValueError:
        return None
    return resolved


def _action_id(seed: str) -> str:
    return _sha256(seed.encode("utf-8"))[:20]


def plan_recovery(
    records: Iterable[EvidenceRecord],
    workspace_root: Path,
    *,
    generated_at: str | None = None,
) -> RecoveryPlan:
    """Freeze a conservative plan from normalized evidence.

    Mutation post-images outrank all Reads independent of timestamp. Exact Reads
    become missing-file fallbacks only when a path has no complete mutation.
    Pathless exact attachments are preserved in the artifact quarantine.
    """

    workspace = workspace_root.resolve(strict=False)
    rows = list(records)
    grouped: dict[str, list[EvidenceRecord]] = {}
    artifacts: list[EvidenceRecord] = []
    for record in rows:
        if record.path:
            grouped.setdefault(record.path.replace("\\", "/").casefold(), []).append(record)
        elif record.has_exact_bytes:
            artifacts.append(record)

    actions: list[RecoveryAction] = []
    for normalized, history in sorted(grouped.items()):
        mutations = [
            row for row in history if row.authority in MUTATION_AUTHORITIES and row.has_exact_bytes
        ]
        reads = [row for row in history if row.authority in READ_AUTHORITIES and row.has_exact_bytes]
        candidate_pool = mutations if mutations else reads
        winners, blockers = _latest_candidates(candidate_pool)
        candidate = winners[-1] if winners else None
        provenance = tuple(row.evidence_id for row in sorted(history, key=lambda row: (
            _parse_time(row.timestamp), row.provider, row.session_id, row.sequence, row.evidence_id
        )))
        warnings: list[str] = []

        if candidate is None:
            decision = "evidence-only"
            target = _resolve_target(workspace, history[-1].path or "")
            if target is None:
                blockers.append("path-outside-workspace")
            actions.append(RecoveryAction(
                action_id=_action_id(normalized), decision=decision,
                target_path=str(target) if target else None, artifact_relative_path=None,
                candidate_sha256=None, candidate_length=None, content_b64=None,
                winner_evidence_id=None, authority=None, provenance=provenance,
                blockers=tuple(sorted(set(blockers))), warnings=tuple(warnings),
            ))
            continue

        target = _resolve_target(workspace, candidate.path or "")
        if target is None:
            blockers.append("path-outside-workspace")
        for destructive in (row for row in history if row.authority in DESTRUCTIVE_AUTHORITIES):
            if _is_after(destructive, candidate):
                blockers.append(f"{destructive.authority}-after-candidate")
        for observation in reads:
            if mutations and _is_after(observation, candidate):
                if observation.content_sha256 == candidate.content_sha256:
                    warnings.append("later-read-corroborates-winner")
                else:
                    blockers.append("later-read-disagrees-with-mutation-winner")

        before_exists: bool | None = None
        before_sha: str | None = None
        if target is not None:
            before_exists = target.is_file()
            before_sha = _sha256(target.read_bytes()) if before_exists else None
        if blockers:
            decision = "conflict-isolated"
        elif before_exists and before_sha == candidate.content_sha256:
            decision = "already-present"
        elif before_exists:
            decision = "conflict-existing"
        elif candidate.authority in MUTATION_AUTHORITIES:
            decision = "restore-missing-mutation"
        else:
            decision = "restore-missing-read-fallback"
            warnings.append("no-mutation-postimage-read-used-as-fallback")

        actions.append(RecoveryAction(
            action_id=_action_id(normalized), decision=decision,
            target_path=str(target) if target else None, artifact_relative_path=None,
            candidate_sha256=candidate.content_sha256,
            candidate_length=candidate.content_length,
            content_b64=candidate.content_b64,
            winner_evidence_id=candidate.evidence_id, authority=candidate.authority,
            provenance=provenance, blockers=tuple(sorted(set(blockers))),
            warnings=tuple(sorted(set(warnings))), before_exists=before_exists,
            before_sha256=before_sha,
        ))

    used_artifact_names: set[str] = set()
    for record in sorted(artifacts, key=lambda row: (
        _parse_time(row.timestamp), row.provider, row.session_id, row.sequence, row.evidence_id
    )):
        base_name = Path(record.artifact_name or f"{record.evidence_id}.bin").name
        relative = f"{record.provider}/{record.session_id}/{base_name}"
        if relative.casefold() in used_artifact_names:
            relative = f"{record.provider}/{record.session_id}/{record.evidence_id}-{base_name}"
        used_artifact_names.add(relative.casefold())
        actions.append(RecoveryAction(
            action_id=_action_id(f"artifact:{record.evidence_id}"),
            decision="quarantine-exact-artifact",
            target_path=None,
            artifact_relative_path=relative,
            candidate_sha256=record.content_sha256,
            candidate_length=record.content_length,
            content_b64=record.content_b64,
            winner_evidence_id=record.evidence_id,
            authority=record.authority,
            provenance=(record.evidence_id,),
            warnings=("artifact-has-no-exact-workspace-path",),
        ))

    timestamp = generated_at or dt.datetime.now(dt.timezone.utc).isoformat()
    partial = {
        "schema_version": SCHEMA_VERSION,
        "workspace_root": str(workspace),
        "generated_at": timestamp,
        "actions": [asdict(action) for action in actions],
    }
    return RecoveryPlan(
        workspace_root=str(workspace), actions=tuple(actions), generated_at=timestamp,
        plan_sha256=_sha256(_canonical_bytes(partial)),
    )


def write_plan(plan: RecoveryPlan, path: Path) -> None:
    plan.verify()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(plan.as_json(), ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def read_plan(path: Path) -> RecoveryPlan:
    return RecoveryPlan.from_json(json.loads(path.read_text(encoding="utf-8")))
