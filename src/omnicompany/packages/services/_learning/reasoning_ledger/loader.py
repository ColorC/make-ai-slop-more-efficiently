"""Filesystem loader for Reasoning Ledger v0 cases."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

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
    RecordType,
    ValidationIssue,
)


DIR_TYPES: dict[str, RecordType] = {
    "claims": "claim",
    "evidence": "evidence",
    "arguments": "argument",
    "conflicts": "conflict",
    "decisions": "decision",
    "experiments": "experiment",
    "artifacts": "artifact",
}

MODEL_BY_TYPE: dict[str, type[LedgerRecord]] = {
    "claim": ClaimRecord,
    "evidence": EvidenceRecord,
    "argument": ArgumentRecord,
    "conflict": ConflictRecord,
    "decision": DecisionRecord,
    "experiment": ExperimentRecord,
    "artifact": ArtifactRecord,
}

_LINKED_RECORD_RE = re.compile(
    r"^\s*-\s*(Claim|Evidence|Argument|Conflict|Decision|Experiment|Artifact)\s*:\s*(.+?)\s*$",
    re.IGNORECASE,
)


def load_ledger_case(root: str | Path) -> LedgerCase:
    """Load one ledger case from disk.

    Loading is intentionally forgiving: malformed records become parse issues so
    the validator can report everything it can see in one pass.
    """

    root_path = Path(root)
    records: dict[str, LedgerRecord] = {}
    issues: list[ValidationIssue] = []

    if not root_path.exists():
        return LedgerCase(
            root=str(root_path),
            parse_issues=[
                ValidationIssue(
                    severity="error",
                    code="case_root_missing",
                    message=f"Ledger case root does not exist: {root_path}",
                    path=str(root_path),
                )
            ],
        )

    for dirname, expected_type in DIR_TYPES.items():
        directory = root_path / dirname
        if not directory.exists():
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="required_set_missing",
                    message=f"Required ledger set directory is missing: {dirname}",
                    field=dirname,
                    path=_display_path(directory, root_path),
                )
            )
            continue

        if expected_type == "decision":
            for path in sorted(directory.glob("*.md")):
                _add_record(records, issues, _load_decision(path, root_path))
            for path in sorted(directory.glob("*.yaml")):
                _add_record(records, issues, _load_yaml_record(path, root_path, expected_type))
            continue

        for path in sorted(directory.glob("*.yaml")):
            _add_record(records, issues, _load_yaml_record(path, root_path, expected_type))

    return LedgerCase(root=str(root_path), records=records, parse_issues=issues)


def _add_record(
    records: dict[str, LedgerRecord],
    issues: list[ValidationIssue],
    result: tuple[LedgerRecord | None, list[ValidationIssue]],
) -> None:
    record, new_issues = result
    issues.extend(new_issues)
    if record is None:
        return
    if record.id in records:
        issues.append(
            ValidationIssue(
                severity="error",
                code="duplicate_id",
                message=f"Duplicate ledger record id: {record.id}",
                object_id=record.id,
                field="id",
                path=record.path,
            )
        )
        return
    records[record.id] = record


def _load_yaml_record(
    path: Path,
    root_path: Path,
    expected_type: RecordType,
) -> tuple[LedgerRecord | None, list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    display_path = _display_path(path, root_path)

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - exact yaml exception varies
        return None, [
            ValidationIssue(
                severity="error",
                code="yaml_parse_failed",
                message=f"YAML parse failed: {exc}",
                path=display_path,
            )
        ]

    if not isinstance(raw, dict):
        return None, [
            ValidationIssue(
                severity="error",
                code="record_not_mapping",
                message="Ledger YAML record must be a mapping.",
                path=display_path,
            )
        ]

    record_id = raw.get("id")
    record_type = raw.get("type")
    if not record_id:
        issues.append(
            ValidationIssue(
                severity="error",
                code="missing_id",
                message="Ledger record is missing required id.",
                field="id",
                path=display_path,
            )
        )
    if not record_type:
        issues.append(
            ValidationIssue(
                severity="error",
                code="missing_type",
                message="Ledger record is missing required type.",
                object_id=str(record_id) if record_id else None,
                field="type",
                path=display_path,
            )
        )
    elif record_type != expected_type:
        issues.append(
            ValidationIssue(
                severity="error",
                code="wrong_directory_type",
                message=(
                    f"Record type {record_type!r} does not match directory "
                    f"type {expected_type!r}."
                ),
                object_id=str(record_id) if record_id else None,
                field="type",
                path=display_path,
            )
        )

    if not record_id or record_type not in MODEL_BY_TYPE:
        return None, issues

    raw = {**raw, "path": display_path}
    model = MODEL_BY_TYPE[record_type]
    try:
        return model.model_validate(raw), issues
    except ValidationError as exc:
        issues.append(
            ValidationIssue(
                severity="error",
                code="schema_validation_failed",
                message=str(exc),
                object_id=str(record_id),
                path=display_path,
            )
        )
        return None, issues


def _load_decision(path: Path, root_path: Path) -> tuple[LedgerRecord | None, list[ValidationIssue]]:
    display_path = _display_path(path, root_path)
    text = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text)
    linked_records = _parse_linked_records(body)
    status = _parse_heading_value(body, "Status")

    raw: dict[str, Any] = {
        "id": path.stem,
        "type": "decision",
        "status": status,
        "linked_records": linked_records,
        "path": display_path,
    }

    issues: list[ValidationIssue] = []
    if frontmatter:
        raw.update(frontmatter)
        if raw.get("type") != "decision":
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="wrong_directory_type",
                    message="Decision Markdown frontmatter type must be 'decision'.",
                    object_id=str(raw.get("id") or path.stem),
                    field="type",
                    path=display_path,
                )
            )

    try:
        return DecisionRecord.model_validate(raw), issues
    except ValidationError as exc:
        issues.append(
            ValidationIssue(
                severity="error",
                code="schema_validation_failed",
                message=str(exc),
                object_id=str(raw.get("id") or path.stem),
                path=display_path,
            )
        )
        return None, issues


def _split_frontmatter(text: str) -> tuple[dict[str, Any] | None, str]:
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---", 4)
    if end == -1:
        return None, text
    raw = text[4:end]
    body = text[end + 4 :]
    data = yaml.safe_load(raw) or {}
    return data if isinstance(data, dict) else None, body


def _parse_heading_value(body: str, heading: str) -> str | None:
    lines = body.splitlines()
    marker = f"## {heading}".lower()
    for index, line in enumerate(lines):
        if line.strip().lower() != marker:
            continue
        for value in lines[index + 1 :]:
            value = value.strip()
            if not value:
                continue
            if value.startswith("## "):
                return None
            return value
    return None


def _parse_linked_records(body: str) -> dict[str, list[str]]:
    lines = body.splitlines()
    in_section = False
    links: dict[str, list[str]] = {}

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            in_section = stripped.lower() == "## linked records"
            continue
        if not in_section:
            continue
        match = _LINKED_RECORD_RE.match(line)
        if not match:
            continue
        record_type = match.group(1).lower()
        record_id = match.group(2).strip()
        links.setdefault(record_type, []).append(record_id)

    return links


def _display_path(path: Path, root_path: Path) -> str:
    try:
        return path.relative_to(root_path).as_posix()
    except ValueError:
        return path.as_posix()
