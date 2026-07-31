"""Validation rules for Reasoning Ledger v0."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .loader import load_ledger_case
from .models import (
    ArgumentRecord,
    ArtifactRecord,
    ClaimRecord,
    ConflictRecord,
    DecisionRecord,
    EvidenceRecord,
    ExperimentRecord,
    LedgerCase,
    LedgerRecord,
    ValidationIssue,
)


REFERENCE_FIELDS: dict[str, str] = {
    "supported_by": "evidence",
    "attacked_by": "evidence",
    "supports": "claim",
    "attacks": "claim",
    "grounds": "evidence",
    "evidence": "evidence",
    "tests_claims": "claim",
    "compares_against": "claim",
    "supports_evidence": "evidence",
}


def validate_ledger_case_path(
    root: str | Path,
    *,
    project_root: str | Path | None = None,
    missing_artifact_is_error: bool = True,
) -> list[ValidationIssue]:
    """Load and validate a ledger case path."""

    case = load_ledger_case(root)
    return validate_ledger_case(
        case,
        project_root=project_root,
        missing_artifact_is_error=missing_artifact_is_error,
    )


def validate_ledger_case(
    case: LedgerCase,
    *,
    project_root: str | Path | None = None,
    missing_artifact_is_error: bool = True,
) -> list[ValidationIssue]:
    """Validate structural quality of one ledger case.

    The validator checks the ledger graph, not whether the real-world claims are
    true.
    """

    issues = list(case.parse_issues)
    records = case.records

    issues.extend(_validate_direct_references(records))
    issues.extend(_validate_claim_falsification(records))
    issues.extend(_validate_conflicts(records))
    issues.extend(_validate_decisions(records))
    issues.extend(_validate_experiments(records))
    issues.extend(
        _validate_artifacts(
            records,
            project_root=Path(project_root) if project_root else _default_project_root(),
            missing_artifact_is_error=missing_artifact_is_error,
        )
    )
    issues.extend(_validate_orphans(records))

    return issues


def _validate_direct_references(records: dict[str, LedgerRecord]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    for record in records.values():
        if isinstance(record, ClaimRecord):
            issues.extend(_check_refs(records, record, "supported_by", record.supported_by, "evidence"))
            issues.extend(_check_refs(records, record, "attacked_by", record.attacked_by, "evidence"))
            issues.extend(_check_relation_refs(records, record))
        elif isinstance(record, EvidenceRecord):
            issues.extend(_check_refs(records, record, "supports", record.supports, "claim"))
            issues.extend(_check_refs(records, record, "attacks", record.attacks, "claim"))
        elif isinstance(record, ArgumentRecord):
            if record.claim:
                issues.extend(_check_refs(records, record, "claim", [record.claim], "claim"))
            issues.extend(_check_refs(records, record, "grounds", record.grounds, "evidence"))
            issues.extend(_check_argument_result_refs(records, record))
        elif isinstance(record, ConflictRecord):
            participants = [value for value in [record.source_id, record.target_id] if value]
            issues.extend(_check_refs(records, record, "from/to", participants, None))
            issues.extend(_check_refs(records, record, "evidence", record.evidence, "evidence"))
            decision_id = record.resolution.get("decision") if isinstance(record.resolution, dict) else None
            if decision_id:
                issues.extend(_check_refs(records, record, "resolution.decision", [decision_id], "decision"))
        elif isinstance(record, DecisionRecord):
            for ref_type, ids in record.linked_records.items():
                issues.extend(_check_refs(records, record, f"linked_records.{ref_type}", ids, ref_type))
        elif isinstance(record, ExperimentRecord):
            issues.extend(_check_refs(records, record, "tests_claims", record.tests_claims, "claim"))
            issues.extend(_check_refs(records, record, "compares_against", record.compares_against, "claim"))
        elif isinstance(record, ArtifactRecord):
            issues.extend(
                _check_refs(records, record, "supports_evidence", record.supports_evidence, "evidence")
            )

    return issues


def _validate_claim_falsification(records: dict[str, LedgerRecord]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    evidence_attacks: dict[str, list[str]] = {}
    conflict_targets: dict[str, list[str]] = {}

    for record in records.values():
        if isinstance(record, EvidenceRecord):
            for claim_id in record.attacks:
                evidence_attacks.setdefault(claim_id, []).append(record.id)
        elif isinstance(record, ConflictRecord) and record.target_id:
            conflict_targets.setdefault(record.target_id, []).append(record.id)

    for record in records.values():
        if not isinstance(record, ClaimRecord) or record.status != "falsified":
            continue
        has_attack = bool(
            record.attacked_by
            or evidence_attacks.get(record.id)
            or conflict_targets.get(record.id)
        )
        if not has_attack:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="falsified_claim_without_attacker",
                    message="Falsified claim must be traceable to attacking evidence or conflict.",
                    object_id=record.id,
                    field="status",
                    path=record.path,
                )
            )

    return issues


def _validate_conflicts(records: dict[str, LedgerRecord]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for record in records.values():
        if not isinstance(record, ConflictRecord):
            continue
        if not record.source_id or not record.target_id:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="conflict_missing_attacker_or_target",
                    message="Conflict must identify attacker/source and target.",
                    object_id=record.id,
                    field="from/to",
                    path=record.path,
                )
            )
    return issues


def _validate_decisions(records: dict[str, LedgerRecord]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for record in records.values():
        if not isinstance(record, DecisionRecord):
            continue
        claim_refs = record.linked_records.get("claim", [])
        argument_refs = record.linked_records.get("argument", [])
        if not claim_refs and not argument_refs:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="decision_without_claim_or_argument",
                    message="Decision must link at least one claim or argument.",
                    object_id=record.id,
                    field="linked_records",
                    path=record.path,
                )
            )
    return issues


def _validate_experiments(records: dict[str, LedgerRecord]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for record in records.values():
        if not isinstance(record, ExperimentRecord):
            continue
        if not record.tests_claims:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="experiment_without_tested_claim",
                    message="Experiment must state which claim it tests.",
                    object_id=record.id,
                    field="tests_claims",
                    path=record.path,
                )
            )
    return issues


def _validate_artifacts(
    records: dict[str, LedgerRecord],
    *,
    project_root: Path,
    missing_artifact_is_error: bool,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    severity = "error" if missing_artifact_is_error else "warning"

    for record in records.values():
        if not isinstance(record, ArtifactRecord):
            continue
        if record.external:
            continue
        if not record.artifact_path:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="artifact_without_path_or_external",
                    message="Artifact must have artifact_path or external=true.",
                    object_id=record.id,
                    field="artifact_path",
                    path=record.path,
                )
            )
            continue
        artifact_path = Path(record.artifact_path)
        full_path = artifact_path if artifact_path.is_absolute() else project_root / artifact_path
        if not full_path.exists():
            issues.append(
                ValidationIssue(
                    severity=severity,
                    code="artifact_path_missing",
                    message=f"Artifact path does not exist: {record.artifact_path}",
                    object_id=record.id,
                    field="artifact_path",
                    path=record.path,
                )
            )

    return issues


def _validate_orphans(records: dict[str, LedgerRecord]) -> list[ValidationIssue]:
    referenced = _referenced_ids(records)
    issues: list[ValidationIssue] = []

    for record in records.values():
        if isinstance(record, EvidenceRecord):
            if not record.supports and not record.attacks and record.id not in referenced:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        code="orphan_evidence",
                        message="Evidence is not linked to any claim, argument, conflict, or artifact.",
                        object_id=record.id,
                        path=record.path,
                    )
                )
        elif isinstance(record, ArtifactRecord):
            if not record.supports_evidence and record.id not in referenced:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        code="orphan_artifact",
                        message="Artifact is not linked to evidence or any other record.",
                        object_id=record.id,
                        path=record.path,
                    )
                )

    return issues


def _check_relation_refs(
    records: dict[str, LedgerRecord],
    record: ClaimRecord,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for field, expected_type in {
        "refines": "claim",
        "refined_by": "claim",
        "conflicts": "conflict",
    }.items():
        value = record.relations.get(field)
        if value is None:
            continue
        issues.extend(_check_refs(records, record, f"relations.{field}", _as_list(value), expected_type))
    return issues


def _check_argument_result_refs(
    records: dict[str, LedgerRecord],
    record: ArgumentRecord,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    result = record.result if isinstance(record.result, dict) else {}
    status_change = result.get("target_status_change")
    if isinstance(status_change, dict) and status_change.get("claim"):
        issues.extend(
            _check_refs(
                records,
                record,
                "result.target_status_change.claim",
                [status_change["claim"]],
                "claim",
            )
        )
    derived = result.get("derived_claim")
    if derived:
        issues.extend(_check_refs(records, record, "result.derived_claim", _as_list(derived), "claim"))
    return issues


def _check_refs(
    records: dict[str, LedgerRecord],
    owner: LedgerRecord,
    field: str,
    refs: Iterable[str],
    expected_type: str | None,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for ref in refs:
        target = records.get(ref)
        if target is None:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="missing_reference",
                    message=f"Referenced record does not exist: {ref}",
                    object_id=owner.id,
                    field=field,
                    path=owner.path,
                )
            )
            continue
        if expected_type is not None and target.type != expected_type:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="wrong_reference_type",
                    message=(
                        f"Referenced record {ref} has type {target.type!r}, "
                        f"expected {expected_type!r}."
                    ),
                    object_id=owner.id,
                    field=field,
                    path=owner.path,
                )
            )
    return issues


def _referenced_ids(records: dict[str, LedgerRecord]) -> set[str]:
    refs: set[str] = set()
    for record in records.values():
        if isinstance(record, ClaimRecord):
            refs.update(record.supported_by)
            refs.update(record.attacked_by)
            for value in record.relations.values():
                refs.update(_as_list(value))
        elif isinstance(record, EvidenceRecord):
            refs.update(record.supports)
            refs.update(record.attacks)
        elif isinstance(record, ArgumentRecord):
            if record.claim:
                refs.add(record.claim)
            refs.update(record.grounds)
            result = record.result if isinstance(record.result, dict) else {}
            status_change = result.get("target_status_change")
            if isinstance(status_change, dict) and status_change.get("claim"):
                refs.add(status_change["claim"])
            refs.update(_as_list(result.get("derived_claim")))
        elif isinstance(record, ConflictRecord):
            refs.update(value for value in [record.source_id, record.target_id] if value)
            refs.update(record.evidence)
            decision_id = record.resolution.get("decision") if isinstance(record.resolution, dict) else None
            if decision_id:
                refs.add(decision_id)
        elif isinstance(record, DecisionRecord):
            for values in record.linked_records.values():
                refs.update(values)
        elif isinstance(record, ExperimentRecord):
            refs.update(record.tests_claims)
            refs.update(record.compares_against)
        elif isinstance(record, ArtifactRecord):
            refs.update(record.supports_evidence)
    return refs


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[6]
