"""Pydantic models for Reasoning Ledger v0."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


RecordType = Literal[
    "claim",
    "evidence",
    "argument",
    "conflict",
    "decision",
    "experiment",
    "artifact",
]

Severity = Literal["error", "warning"]


class ValidationIssue(BaseModel):
    """A machine-readable ledger validation finding."""

    severity: Severity
    code: str
    message: str
    object_id: str | None = None
    field: str | None = None
    path: str | None = None


class LedgerRecord(BaseModel):
    """Base record shared by all seven ledger sets."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    type: RecordType
    path: str | None = None


class ClaimRecord(LedgerRecord):
    type: Literal["claim"]
    kind: str | None = None
    strictness: str | None = None
    status: str | None = None
    text: str | None = None
    supported_by: list[str] = Field(default_factory=list)
    attacked_by: list[str] = Field(default_factory=list)
    relations: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)


class EvidenceRecord(LedgerRecord):
    type: Literal["evidence"]
    kind: str | None = None
    summary: str | None = None
    supports: list[str] = Field(default_factory=list)
    attacks: list[str] = Field(default_factory=list)
    source: dict[str, Any] = Field(default_factory=dict)


class ArgumentRecord(LedgerRecord):
    type: Literal["argument"]
    structure: str | None = None
    claim: str | None = None
    conclusion: str | None = None
    grounds: list[str] = Field(default_factory=list)
    result: dict[str, Any] = Field(default_factory=dict)


class ConflictRecord(LedgerRecord):
    type: Literal["conflict"]
    kind: str | None = None
    source_id: str | None = Field(default=None, alias="from")
    target_id: str | None = Field(default=None, alias="to")
    relation: str | None = None
    evidence: list[str] = Field(default_factory=list)
    resolution: dict[str, Any] = Field(default_factory=dict)


class DecisionRecord(LedgerRecord):
    type: Literal["decision"]
    status: str | None = None
    linked_records: dict[str, list[str]] = Field(default_factory=dict)


class ExperimentRecord(LedgerRecord):
    type: Literal["experiment"]
    kind: str | None = None
    status: str | None = None
    question: str | None = None
    tests_claims: list[str] = Field(default_factory=list)
    compares_against: list[str] = Field(default_factory=list)


class ArtifactRecord(LedgerRecord):
    type: Literal["artifact"]
    kind: str | None = None
    artifact_path: str | None = None
    external: bool = False
    supports_evidence: list[str] = Field(default_factory=list)
    checker: dict[str, Any] = Field(default_factory=dict)


class LedgerCase(BaseModel):
    """A loaded ledger case plus non-fatal parse issues."""

    root: str
    records: dict[str, LedgerRecord] = Field(default_factory=dict)
    parse_issues: list[ValidationIssue] = Field(default_factory=list)

    def by_type(self, record_type: RecordType) -> list[LedgerRecord]:
        return [record for record in self.records.values() if record.type == record_type]
