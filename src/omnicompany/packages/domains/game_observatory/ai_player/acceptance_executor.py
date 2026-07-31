"""Fail-closed P-13 acceptance package executor.

The executor verifies externally produced receipts. It never executes a benchmark,
changes a frozen manifest, or issues an independent-review conclusion.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import shutil
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..models import utc_now


EXPECTED_ITEMS: dict[str, tuple[str, ...]] = {
    "AP": tuple(f"AP-{index:02d}" for index in range(1, 12)),
    "P": tuple(f"P-{index:02d}" for index in range(1, 14)),
    "E2E": (
        "E2E-A1",
        "E2E-A2",
        "E2E-A3",
        "E2E-A4",
        "E2E-S1",
        "E2E-S2",
        "E2E-S3",
        "E2E-X1",
        "E2E-X2",
        "E2E-X3",
    ),
    "G": tuple(f"G-{index:02d}" for index in range(1, 13)),
}
SPECIAL_ITEMS = {
    "clean_database_rerun": "clean-database-rerun",
    "independent_review": "independent-review",
}
REVIEWED_ITEM_IDS = frozenset(
    item_id for item_ids in EXPECTED_ITEMS.values() for item_id in item_ids
) | {SPECIAL_ITEMS["clean_database_rerun"]}
EXPECTED_FINAL_EXIT = {
    "requirements_passed": "11/11",
    "products_passed": "13/13",
    "e2e_passed": "10/10",
    "gates_passed": "12/12",
    "blocker_count": 0,
    "major_count": 0,
    "clean_database_rerun": "PASS",
    "independent_review": "PASS",
}
EXECUTOR_ISSUER_ID = "ai-player-acceptance-executor"
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
SHA256_PATTERN = r"^[0-9a-f]{64}$"
RECEIPT_SIGNATURE_DOMAIN = b"omnicompany:game-observatory:ai-player:acceptance-receipt:v1\n"
REVIEWED_RECEIPT_ROOT_DOMAIN = "acceptance-reviewed-receipt-set.v1"
REVIEWED_EVIDENCE_ROOT_DOMAIN = "acceptance-reviewed-evidence-set.v1"
PACKAGE_INPUT_ROOT_DOMAIN = "acceptance-package-input-set.v1"
GateCategory = Literal["AP", "P", "E2E", "G", "clean_database_rerun"]


def _receipt_schema_constraints(schema: dict[str, Any]) -> None:
    independent_fields = (
        "reviewer_id",
        "reviewed_item_ids",
        "reviewed_at",
        "reviewed_receipt_root_sha256",
        "reviewed_evidence_root_sha256",
    )
    schema["allOf"] = [
        {
            "if": {
                "properties": {"category": {"const": "independent_review"}},
                "required": ["category"],
            },
            "then": {
                "properties": {"independent_reviewer": {"const": True}},
                "required": ["independent_reviewer", *independent_fields],
            },
            "else": {
                "properties": {
                    "independent_reviewer": {"const": False},
                    "reviewer_id": {"type": "null"},
                    "reviewed_item_ids": {"maxItems": 0},
                    "reviewed_at": {"type": "null"},
                    "reviewed_receipt_root_sha256": {"type": "null"},
                    "reviewed_evidence_root_sha256": {"type": "null"},
                }
            },
        }
    ]


def _result_schema_constraints(schema: dict[str, Any]) -> None:
    expected_pairs = [
        (category, item_id)
        for category, item_ids in EXPECTED_ITEMS.items()
        for item_id in item_ids
    ] + list(SPECIAL_ITEMS.items())
    exact_items = [
        {
            "properties": {
                "items": {
                    "contains": {
                        "properties": {
                            "category": {"const": category},
                            "item_id": {"const": item_id},
                            "status": {"const": "PASS"},
                        },
                        "required": ["category", "item_id", "status"],
                    },
                    "minContains": 1,
                    "maxContains": 1,
                }
            }
        }
        for category, item_id in expected_pairs
    ]
    schema["allOf"] = [
        {
            "if": {
                "properties": {"status": {"const": "PASS"}},
                "required": ["status"],
            },
            "then": {
                "properties": {
                    "counts": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "AP": {"const": "11/11"},
                            "P": {"const": "13/13"},
                            "E2E": {"const": "10/10"},
                            "G": {"const": "12/12"},
                        },
                        "required": ["AP", "P", "E2E", "G"],
                    },
                    "blocker_count": {"const": 0},
                    "major_count": {"const": 0},
                    "safety_violation_count": {"const": 0},
                    "clean_database_rerun": {"const": "PASS"},
                    "independent_review": {"const": "PASS"},
                    "issues": {"maxItems": 0},
                    "items": {
                        "minItems": len(expected_pairs),
                        "maxItems": len(expected_pairs),
                    },
                },
                "allOf": exact_items,
            },
        }
    ]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AcceptanceFileReferenceV1(_StrictModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=SHA256_PATTERN)


class AcceptanceEvidenceReferenceV1(AcceptanceFileReferenceV1):
    id: str = Field(min_length=1)


class AcceptanceTrustedGateIssuerV1(_StrictModel):
    issuer_id: str = Field(min_length=1)
    key_id: str = Field(min_length=1)
    public_key_base64: str = Field(min_length=1)
    allowed_categories: list[GateCategory] = Field(min_length=1, json_schema_extra={"uniqueItems": True})

    @field_validator("allowed_categories")
    @classmethod
    def unique_categories(cls, value: list[GateCategory]) -> list[GateCategory]:
        if len(value) != len(set(value)):
            raise ValueError("trusted gate issuer categories must be unique")
        return value


class AcceptanceTrustedReviewerV1(_StrictModel):
    reviewer_id: str = Field(min_length=1)
    key_id: str = Field(min_length=1)
    public_key_base64: str = Field(min_length=1)


class AcceptanceTrustPolicyV1(_StrictModel):
    schema_id: Literal["game-observatory.ai-player.acceptance-trust-policy.v1"] = Field(
        default="game-observatory.ai-player.acceptance-trust-policy.v1",
        alias="schema",
    )
    policy_id: str = Field(min_length=1)
    created_at: str = Field(min_length=1)
    canonical_manifest: AcceptanceFileReferenceV1
    executor_identity_ids: list[str] = Field(min_length=1, json_schema_extra={"uniqueItems": True})
    implementation_identity_ids: list[str] = Field(
        min_length=1,
        json_schema_extra={"uniqueItems": True},
    )
    gate_issuers: list[AcceptanceTrustedGateIssuerV1] = Field(
        min_length=1,
        json_schema_extra={"uniqueItems": True},
    )
    independent_reviewers: list[AcceptanceTrustedReviewerV1] = Field(
        min_length=1,
        json_schema_extra={"uniqueItems": True},
    )

    @model_validator(mode="after")
    def identities_are_explicit_and_disjoint(self) -> "AcceptanceTrustPolicyV1":
        if EXECUTOR_ISSUER_ID not in self.executor_identity_ids:
            raise ValueError("trust policy must name the acceptance executor identity")
        for field_name, values in (
            ("executor_identity_ids", self.executor_identity_ids),
            ("implementation_identity_ids", self.implementation_identity_ids),
        ):
            if any(not value.strip() for value in values) or len(values) != len(set(values)):
                raise ValueError(f"{field_name} must contain unique non-empty identities")
        gate_ids = [item.issuer_id for item in self.gate_issuers]
        reviewer_ids = [item.reviewer_id for item in self.independent_reviewers]
        key_ids = [item.key_id for item in self.gate_issuers] + [
            item.key_id for item in self.independent_reviewers
        ]
        public_keys = [item.public_key_base64 for item in self.gate_issuers] + [
            item.public_key_base64 for item in self.independent_reviewers
        ]
        if len(gate_ids) != len(set(gate_ids)):
            raise ValueError("trusted gate issuer identities must be unique")
        if len(reviewer_ids) != len(set(reviewer_ids)):
            raise ValueError("trusted reviewer identities must be unique")
        if len(key_ids) != len(set(key_ids)):
            raise ValueError("acceptance trust-policy key IDs must be globally unique")
        if len(public_keys) != len(set(public_keys)):
            raise ValueError("gate issuers and reviewers must not share Ed25519 keys")
        forbidden = set(self.executor_identity_ids) | set(self.implementation_identity_ids)
        if forbidden.intersection(gate_ids) or forbidden.intersection(reviewer_ids):
            raise ValueError("executor or implementation identities cannot be trusted signers")
        if set(gate_ids).intersection(reviewer_ids):
            raise ValueError("independent reviewers must be disjoint from gate issuers")
        for item in [*self.gate_issuers, *self.independent_reviewers]:
            try:
                raw = base64.b64decode(item.public_key_base64, validate=True)
                Ed25519PublicKey.from_public_bytes(raw)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"invalid Ed25519 public key for {item.key_id}") from exc
        _parse_aware_time(self.created_at, label="trust policy created_at")
        return self


class AcceptanceEvidenceReceiptV1(_StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        json_schema_extra=_receipt_schema_constraints,
    )

    schema_id: Literal[
        "game-observatory.ai-player.acceptance-evidence-receipt.v1"
    ] = Field(
        default="game-observatory.ai-player.acceptance-evidence-receipt.v1",
        alias="schema",
    )
    receipt_id: str = Field(min_length=1)
    category: Literal["AP", "P", "E2E", "G", "clean_database_rerun", "independent_review"]
    item_id: str = Field(min_length=1)
    status: Literal["PASS", "FAIL"]
    issuer_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    blocker_count: int = Field(ge=0)
    major_count: int = Field(ge=0)
    safety_violation_count: int = Field(ge=0)
    evidence_refs: list[AcceptanceEvidenceReferenceV1] = Field(
        min_length=1,
        json_schema_extra={"uniqueItems": True},
    )
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    key_id: str = Field(min_length=1)
    signature_algorithm: Literal["ed25519"]
    signature_base64: str = Field(min_length=1)
    issued_at: str = Field(min_length=1)
    independent_reviewer: bool = False
    reviewer_id: str | None = Field(default=None, min_length=1)
    reviewed_item_ids: list[str] = Field(
        default_factory=list,
        json_schema_extra={"uniqueItems": True},
    )
    reviewed_at: str | None = Field(default=None, min_length=1)
    reviewed_receipt_root_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    reviewed_evidence_root_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)

    @field_validator("evidence_refs")
    @classmethod
    def unique_evidence_ids(
        cls,
        value: list[AcceptanceEvidenceReferenceV1],
    ) -> list[AcceptanceEvidenceReferenceV1]:
        ids = [item.id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("acceptance receipt evidence IDs must be unique")
        return value

    @field_validator("reviewed_item_ids")
    @classmethod
    def unique_reviewed_items(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("reviewed_item_ids must contain non-empty values")
        if len(value) != len(set(value)):
            raise ValueError("reviewed_item_ids must be unique")
        return value

    @model_validator(mode="after")
    def review_fields_belong_to_external_review(self) -> "AcceptanceEvidenceReceiptV1":
        if self.category == "independent_review":
            if not self.independent_reviewer or not self.reviewer_id:
                raise ValueError("independent review requires an identified external reviewer")
            if not self.reviewed_item_ids:
                raise ValueError("independent review requires reviewed_item_ids")
            if not all(
                (
                    self.reviewed_at,
                    self.reviewed_receipt_root_sha256,
                    self.reviewed_evidence_root_sha256,
                )
            ):
                raise ValueError("independent review requires exact corpus roots and review time")
            reviewed_at = _parse_aware_time(self.reviewed_at or "", label="reviewed_at")
            issued_at = _parse_aware_time(self.issued_at, label="issued_at")
            if reviewed_at > issued_at:
                raise ValueError("independent review cannot be issued before it was performed")
        elif any(
            (
                self.independent_reviewer,
                self.reviewer_id,
                self.reviewed_item_ids,
                self.reviewed_at,
                self.reviewed_receipt_root_sha256,
                self.reviewed_evidence_root_sha256,
            )
        ):
            raise ValueError("independent-review fields are exclusive to its receipt")
        else:
            _parse_aware_time(self.issued_at, label="issued_at")
        return self


class AcceptanceRunRequestV1(_StrictModel):
    schema_id: Literal["game-observatory.ai-player.acceptance-run-request.v1"] = Field(
        default="game-observatory.ai-player.acceptance-run-request.v1",
        alias="schema",
    )
    run_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    )
    receipts: list[AcceptanceFileReferenceV1] = Field(
        min_length=1,
        json_schema_extra={"uniqueItems": True},
    )

    @field_validator("run_id")
    @classmethod
    def safe_run_id(cls, value: str) -> str:
        if RUN_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("run_id contains unsafe path characters")
        return value

    @field_validator("receipts")
    @classmethod
    def unique_receipt_paths(
        cls,
        value: list[AcceptanceFileReferenceV1],
    ) -> list[AcceptanceFileReferenceV1]:
        paths = [item.path for item in value]
        if len(paths) != len(set(paths)):
            raise ValueError("acceptance receipt paths must be unique")
        return value


class AcceptanceItemResultV1(_StrictModel):
    category: Literal["AP", "P", "E2E", "G", "clean_database_rerun", "independent_review"]
    item_id: str = Field(min_length=1)
    status: Literal["PASS", "FAIL"]
    receipt_id: str | None = Field(default=None, min_length=1)
    receipt_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    issues: list[str] = Field(default_factory=list)


class AcceptanceExecutionResultV1(_StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        json_schema_extra=_result_schema_constraints,
    )

    schema_id: Literal["game-observatory.ai-player.acceptance-results.v1"] = Field(
        default="game-observatory.ai-player.acceptance-results.v1",
        alias="schema",
    )
    run_id: str = Field(min_length=1)
    status: Literal["PASS", "FAIL"]
    acceptance_manifest_path: str = Field(min_length=1)
    acceptance_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    trust_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewed_receipt_root_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewed_evidence_root_sha256: str = Field(pattern=SHA256_PATTERN)
    package_input_root_sha256: str = Field(pattern=SHA256_PATTERN)
    counts: dict[str, str]
    blocker_count: int = Field(ge=0)
    major_count: int = Field(ge=0)
    safety_violation_count: int = Field(ge=0)
    clean_database_rerun: Literal["PASS", "FAIL"]
    independent_review: Literal["PASS", "FAIL"]
    items: list[AcceptanceItemResultV1]
    issues: list[str] = Field(default_factory=list)
    evidence_index_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    report_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    independent_review_sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def pass_is_exact(self) -> "AcceptanceExecutionResultV1":
        if self.status != "PASS":
            return self
        expected_pairs = {
            (category, item_id)
            for category, item_ids in EXPECTED_ITEMS.items()
            for item_id in item_ids
        } | set(SPECIAL_ITEMS.items())
        actual_pairs = [(item.category, item.item_id) for item in self.items]
        if (
            self.counts != {"AP": "11/11", "P": "13/13", "E2E": "10/10", "G": "12/12"}
            or self.blocker_count
            or self.major_count
            or self.safety_violation_count
            or self.clean_database_rerun != "PASS"
            or self.independent_review != "PASS"
            or self.issues
            or len(actual_pairs) != len(expected_pairs)
            or set(actual_pairs) != expected_pairs
            or any(item.status != "PASS" for item in self.items)
        ):
            raise ValueError("PASS acceptance result must satisfy every exact final-exit invariant")
        return self


class AcceptanceEvidenceIndexEntryV1(_StrictModel):
    kind: Literal["trust_policy", "acceptance_manifest", "receipt", "receipt_evidence"]
    id: str = Field(min_length=1)
    path: str = Field(min_length=1, pattern=r"^traces/[A-Za-z0-9][A-Za-z0-9._/-]*$")
    expected_sha256: str = Field(pattern=SHA256_PATTERN)
    actual_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    verified: bool
    trace_path: str | None = Field(default=None, min_length=1)
    issues: list[str] = Field(default_factory=list)


class AcceptanceEvidenceIndexV1(_StrictModel):
    schema_id: Literal["game-observatory.ai-player.acceptance-evidence-index.v1"] = Field(
        default="game-observatory.ai-player.acceptance-evidence-index.v1",
        alias="schema",
    )
    run_id: str = Field(min_length=1)
    status: Literal["PASS", "FAIL"]
    entries: list[AcceptanceEvidenceIndexEntryV1] = Field(
        json_schema_extra={"uniqueItems": True}
    )
    created_at: str = Field(default_factory=utc_now)


class _FrozenExitRequirementV1(_StrictModel):
    requirements_passed: Literal["11/11"]
    products_passed: Literal["13/13"]
    e2e_passed: Literal["10/10"]
    gates_passed: Literal["12/12"]
    blocker_count: Literal[0]
    major_count: Literal[0]
    clean_database_rerun: Literal["PASS"]
    independent_review: Literal["PASS"]


class _FrozenAcceptanceManifestV1(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    schema_id: Literal["ai-player-acceptance-manifest.v1"] = Field(alias="schema")
    manifest_id: str = Field(min_length=1)
    freeze_status: Literal["frozen", "approved"]
    manifest_pass: Literal[True]
    required_final_exit: _FrozenExitRequirementV1


@dataclass
class _LoadedFile:
    kind: Literal["trust_policy", "acceptance_manifest", "receipt", "receipt_evidence"]
    id: str
    configured_path: str
    expected_sha256: str
    path: Path | None = None
    content: bytes | None = None
    actual_sha256: str | None = None
    issues: list[str] = field(default_factory=list)
    trace_path: str | None = None

    @property
    def verified(self) -> bool:
        return not self.issues and self.actual_sha256 == self.expected_sha256

    def index_entry(self) -> AcceptanceEvidenceIndexEntryV1:
        if self.trace_path is None:
            raise ValueError(f"package-local trace is missing for {self.id}")
        return AcceptanceEvidenceIndexEntryV1(
            kind=self.kind,
            id=self.id,
            path=self.trace_path,
            expected_sha256=self.expected_sha256,
            actual_sha256=self.actual_sha256,
            verified=self.verified,
            trace_path=self.trace_path,
            issues=self.issues,
        )


@dataclass
class _LoadedReceipt:
    source: _LoadedFile
    receipt: AcceptanceEvidenceReceiptV1 | None = None
    evidence: list[_LoadedFile] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def trusted(self) -> bool:
        return (
            self.receipt is not None
            and self.source.verified
            and not self.issues
            and all(item.verified for item in self.evidence)
        )


class AcceptanceExecutionFailed(RuntimeError):
    """The facility executed correctly, but the frozen acceptance did not pass."""

    def __init__(
        self,
        result: AcceptanceExecutionResultV1,
        run_dir: Path | None = None,
    ) -> None:
        super().__init__(f"AI-player acceptance result is FAIL: {len(result.issues)} issue(s)")
        self.result = result
        self.run_dir = run_dir


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _parse_aware_time(value: str, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed


def _canonical_json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def acceptance_receipt_signing_bytes(receipt: AcceptanceEvidenceReceiptV1) -> bytes:
    """Return the stable, domain-separated bytes every receipt signer must sign."""

    payload = receipt.model_dump(
        mode="json",
        by_alias=True,
        exclude={"signature_base64"},
        exclude_unset=True,
    )
    return RECEIPT_SIGNATURE_DOMAIN + _canonical_json_bytes(payload)


def acceptance_content_root(label: str, entries: list[dict[str, str]]) -> str:
    """Hash a deterministic acceptance corpus; exposed for external signers."""

    ordered = sorted(entries, key=lambda item: _canonical_json_bytes(item))
    return _sha256_bytes(_canonical_json_bytes({"domain": label, "entries": ordered}))


def _scoped_path(workspace_root: Path, configured_path: str) -> Path:
    candidate = Path(configured_path)
    resolved = (
        candidate.resolve()
        if candidate.is_absolute()
        else (workspace_root / candidate).resolve()
    )
    if not resolved.is_relative_to(workspace_root):
        raise ValueError(f"path escapes workspace root: {configured_path}")
    return resolved


def _load_file(
    workspace_root: Path,
    reference: AcceptanceFileReferenceV1,
    *,
    kind: Literal["trust_policy", "acceptance_manifest", "receipt", "receipt_evidence"],
    item_id: str,
) -> _LoadedFile:
    loaded = _LoadedFile(
        kind=kind,
        id=item_id,
        configured_path=reference.path,
        expected_sha256=reference.sha256,
    )
    try:
        loaded.path = _scoped_path(workspace_root, reference.path)
        if not loaded.path.is_file():
            raise ValueError(f"file is missing: {reference.path}")
        loaded.content = loaded.path.read_bytes()
        loaded.actual_sha256 = _sha256_bytes(loaded.content)
        if loaded.actual_sha256 != reference.sha256:
            loaded.issues.append(f"SHA-256 mismatch: {reference.path}")
    except (OSError, ValueError) as exc:
        loaded.issues.append(str(exc))
    return loaded


def _load_trust_policy(
    policy_path: Path,
    expected_sha256: str,
) -> tuple[_LoadedFile, AcceptanceTrustPolicyV1]:
    if re.fullmatch(SHA256_PATTERN, expected_sha256) is None:
        raise ValueError("trust-policy SHA-256 must contain 64 lowercase hex characters")
    resolved = policy_path.resolve()
    if not resolved.is_file():
        raise ValueError(f"acceptance trust policy is missing: {resolved}")
    content = resolved.read_bytes()
    actual_sha256 = _sha256_bytes(content)
    if actual_sha256 != expected_sha256:
        raise ValueError("acceptance trust-policy SHA-256 mismatch")
    policy = AcceptanceTrustPolicyV1.model_validate(
        _parse_json(content, label=str(resolved))
    )
    return (
        _LoadedFile(
            kind="trust_policy",
            id=policy.policy_id,
            configured_path=str(resolved),
            expected_sha256=expected_sha256,
            path=resolved,
            content=content,
            actual_sha256=actual_sha256,
        ),
        policy,
    )


def _parse_json(content: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain one JSON object")
    return payload


def _load_receipts(
    workspace_root: Path,
    references: list[AcceptanceFileReferenceV1],
) -> list[_LoadedReceipt]:
    loaded_receipts: list[_LoadedReceipt] = []
    for index, reference in enumerate(references, start=1):
        source = _load_file(
            workspace_root,
            reference,
            kind="receipt",
            item_id=f"unparsed-receipt-{index:03d}",
        )
        loaded = _LoadedReceipt(source=source)
        if source.content is not None:
            try:
                payload = _parse_json(source.content, label=reference.path)
                loaded.receipt = AcceptanceEvidenceReceiptV1.model_validate(payload)
                source.id = loaded.receipt.receipt_id
            except (TypeError, ValueError) as exc:
                source.issues.append(f"receipt schema validation failed: {exc}")
        if loaded.receipt is not None:
            for evidence_ref in loaded.receipt.evidence_refs:
                evidence = _load_file(
                    workspace_root,
                    AcceptanceFileReferenceV1(
                        path=evidence_ref.path,
                        sha256=evidence_ref.sha256,
                    ),
                    kind="receipt_evidence",
                    item_id=evidence_ref.id,
                )
                loaded.evidence.append(evidence)
                loaded.issues.extend(evidence.issues)
        loaded_receipts.append(loaded)
    return loaded_receipts


def _duplicate_issues(receipts: list[_LoadedReceipt]) -> list[str]:
    issues: list[str] = []
    for field_name, values in (
        (
            "receipt_id",
            [item.receipt.receipt_id for item in receipts if item.receipt is not None],
        ),
        (
            "item_id",
            [item.receipt.item_id for item in receipts if item.receipt is not None],
        ),
        (
            "evidence ID",
            [evidence.id for item in receipts for evidence in item.evidence],
        ),
    ):
        duplicates = sorted({value for value in values if values.count(value) > 1})
        if duplicates:
            issues.append(f"duplicate {field_name}: {', '.join(duplicates)}")
    return issues


def _review_corpus_roots(
    receipts: list[_LoadedReceipt],
) -> tuple[str, str, datetime | None]:
    receipt_entries: list[dict[str, str]] = []
    evidence_entries: list[dict[str, str]] = []
    issued_times: list[datetime] = []
    for loaded in receipts:
        receipt = loaded.receipt
        if receipt is None or receipt.item_id not in REVIEWED_ITEM_IDS:
            continue
        receipt_entries.append(
            {
                "item_id": receipt.item_id,
                "receipt_sha256": loaded.source.actual_sha256 or "0" * 64,
            }
        )
        issued_times.append(_parse_aware_time(receipt.issued_at, label="issued_at"))
        for evidence in loaded.evidence:
            evidence_entries.append(
                {
                    "item_id": receipt.item_id,
                    "evidence_id": evidence.id,
                    "evidence_path": evidence.configured_path,
                    "evidence_sha256": evidence.actual_sha256 or "0" * 64,
                }
            )
    return (
        acceptance_content_root(REVIEWED_RECEIPT_ROOT_DOMAIN, receipt_entries),
        acceptance_content_root(REVIEWED_EVIDENCE_ROOT_DOMAIN, evidence_entries),
        max(issued_times) if issued_times else None,
    )


def _verify_receipt_signature(
    receipt: AcceptanceEvidenceReceiptV1,
    *,
    public_key_base64: str,
) -> str | None:
    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(public_key_base64, validate=True)
        )
        signature = base64.b64decode(receipt.signature_base64, validate=True)
        public_key.verify(signature, acceptance_receipt_signing_bytes(receipt))
    except (InvalidSignature, TypeError, ValueError):
        return f"{receipt.item_id} receipt signature is invalid"
    return None


def _receipt_trust_issues(
    receipt: AcceptanceEvidenceReceiptV1,
    *,
    policy: AcceptanceTrustPolicyV1,
) -> list[str]:
    issues: list[str] = []
    forbidden = set(policy.executor_identity_ids) | set(policy.implementation_identity_ids)
    if receipt.category == "independent_review":
        if receipt.reviewer_id in forbidden or receipt.issuer_id in forbidden:
            issues.append("executor or implementation identity cannot issue independent review")
        if receipt.issuer_id != receipt.reviewer_id:
            issues.append("independent review signer must be the identified reviewer")
        identity = next(
            (
                item
                for item in policy.independent_reviewers
                if item.reviewer_id == receipt.reviewer_id
            ),
            None,
        )
        if identity is None:
            issues.append("independent reviewer is not trusted by the acceptance policy")
            return issues
        if identity.key_id != receipt.key_id:
            issues.append("independent review key is not bound to the trusted reviewer")
            return issues
        signature_issue = _verify_receipt_signature(
            receipt,
            public_key_base64=identity.public_key_base64,
        )
    else:
        if receipt.issuer_id in forbidden:
            issues.append("executor or implementation identity cannot issue gate receipts")
        identity = next(
            (item for item in policy.gate_issuers if item.issuer_id == receipt.issuer_id),
            None,
        )
        if identity is None:
            issues.append(f"untrusted gate receipt issuer: {receipt.issuer_id}")
            return issues
        if receipt.category not in identity.allowed_categories:
            issues.append(
                f"gate issuer {receipt.issuer_id} is not authorized for {receipt.category}"
            )
        if identity.key_id != receipt.key_id:
            issues.append("gate receipt key is not bound to the trusted issuer")
            return issues
        signature_issue = _verify_receipt_signature(
            receipt,
            public_key_base64=identity.public_key_base64,
        )
    if signature_issue is not None:
        issues.append(signature_issue)
    return issues


def _receipt_item_issues(
    loaded: _LoadedReceipt,
    *,
    category: str,
    item_id: str,
    policy: AcceptanceTrustPolicyV1,
    manifest_sha256: str,
    reviewed_receipt_root_sha256: str,
    reviewed_evidence_root_sha256: str,
    latest_reviewed_receipt_issued_at: datetime | None,
) -> list[str]:
    receipt = loaded.receipt
    issues = [*loaded.source.issues, *loaded.issues]
    if receipt is None:
        return issues or [f"receipt for {item_id} could not be parsed"]
    if receipt.category != category:
        issues.append(
            f"{item_id} receipt category is {receipt.category}, expected {category}"
        )
    if receipt.item_id != item_id:
        issues.append(f"receipt item is {receipt.item_id}, expected {item_id}")
    if receipt.manifest_sha256 != manifest_sha256:
        issues.append(f"{item_id} receipt is not bound to the canonical manifest")
    issues.extend(_receipt_trust_issues(receipt, policy=policy))
    if receipt.status != "PASS":
        issues.append(f"{item_id} receipt status is not PASS")
    if receipt.blocker_count:
        issues.append(f"{item_id} reports blockers: {receipt.blocker_count}")
    if receipt.major_count:
        issues.append(f"{item_id} reports major findings: {receipt.major_count}")
    if receipt.safety_violation_count:
        issues.append(
            f"{item_id} reports safety violations: {receipt.safety_violation_count}"
        )
    if category == "independent_review":
        if set(receipt.reviewed_item_ids) != REVIEWED_ITEM_IDS:
            issues.append("independent review does not cover every required item and clean rerun")
        if receipt.reviewed_receipt_root_sha256 != reviewed_receipt_root_sha256:
            issues.append("independent review is not bound to the exact receipt set")
        if receipt.reviewed_evidence_root_sha256 != reviewed_evidence_root_sha256:
            issues.append("independent review is not bound to the exact evidence set")
        if (
            latest_reviewed_receipt_issued_at is not None
            and _parse_aware_time(receipt.reviewed_at or "", label="reviewed_at")
            < latest_reviewed_receipt_issued_at
        ):
            issues.append("independent review time predates the reviewed receipt set")
    return list(dict.fromkeys(issues))


def _evaluate_items(
    receipts: list[_LoadedReceipt],
    *,
    policy: AcceptanceTrustPolicyV1,
    manifest_sha256: str,
    reviewed_receipt_root_sha256: str,
    reviewed_evidence_root_sha256: str,
    latest_reviewed_receipt_issued_at: datetime | None,
) -> tuple[list[AcceptanceItemResultV1], list[str]]:
    parsed_by_item: dict[str, list[_LoadedReceipt]] = {}
    for loaded in receipts:
        if loaded.receipt is not None:
            parsed_by_item.setdefault(loaded.receipt.item_id, []).append(loaded)

    results: list[AcceptanceItemResultV1] = []
    issues: list[str] = []
    expected_pairs = [
        (category, item_id)
        for category, item_ids in EXPECTED_ITEMS.items()
        for item_id in item_ids
    ] + list(SPECIAL_ITEMS.items())
    expected_ids = {item_id for _category, item_id in expected_pairs}
    unexpected = sorted(set(parsed_by_item).difference(expected_ids))
    if unexpected:
        issues.append(f"unexpected acceptance item IDs: {', '.join(unexpected)}")

    for category, item_id in expected_pairs:
        candidates = parsed_by_item.get(item_id, [])
        item_issues: list[str] = []
        loaded: _LoadedReceipt | None = None
        if not candidates:
            item_issues.append(f"missing receipt for {item_id}")
        elif len(candidates) > 1:
            item_issues.append(f"duplicate receipts for {item_id}")
        else:
            loaded = candidates[0]
            item_issues.extend(
                _receipt_item_issues(
                    loaded,
                    category=category,
                    item_id=item_id,
                    policy=policy,
                    manifest_sha256=manifest_sha256,
                    reviewed_receipt_root_sha256=reviewed_receipt_root_sha256,
                    reviewed_evidence_root_sha256=reviewed_evidence_root_sha256,
                    latest_reviewed_receipt_issued_at=latest_reviewed_receipt_issued_at,
                )
            )
        item_status = "PASS" if not item_issues and loaded and loaded.trusted else "FAIL"
        if item_status == "FAIL" and not item_issues:
            item_issues.append(f"{item_id} receipt did not establish a trusted PASS")
        results.append(
            AcceptanceItemResultV1(
                category=category,
                item_id=item_id,
                status=item_status,
                receipt_id=(loaded.receipt.receipt_id if loaded and loaded.receipt else None),
                receipt_sha256=(loaded.source.actual_sha256 if loaded else None),
                issues=item_issues,
            )
        )
        issues.extend(item_issues)
    return results, issues


def _render_report(result: AcceptanceExecutionResultV1) -> str:
    lines = [
        f"# {result.status}",
        "",
        "## 总门",
        "",
        f"- AP：{result.counts['AP']}",
        f"- P：{result.counts['P']}",
        f"- E2E：{result.counts['E2E']}",
        f"- G：{result.counts['G']}",
        f"- Blocker：{result.blocker_count}",
        f"- Major：{result.major_count}",
        f"- 安全违规：{result.safety_violation_count}",
        f"- 干净数据库重跑：{result.clean_database_rerun}",
        f"- 独立审查：{result.independent_review}",
        f"- 冻结清单 SHA-256：{result.acceptance_manifest_sha256}",
        f"- 信任策略 SHA-256：{result.trust_policy_sha256}",
        f"- 受审收据根：{result.reviewed_receipt_root_sha256}",
        f"- 受审证据根：{result.reviewed_evidence_root_sha256}",
        f"- 结果包输入根：{result.package_input_root_sha256}",
        "",
        "## 逐项结果",
        "",
        "| 类别 | ID | 结果 | 收据 |",
        "|---|---|---|---|",
    ]
    lines.extend(
        f"| {item.category} | {item.item_id} | {item.status} | {item.receipt_id or '缺失'} |"
        for item in result.items
    )
    lines.extend(["", "## 未关闭项", ""])
    if result.issues:
        lines.extend(f"- {issue}" for issue in result.issues)
    else:
        lines.append("- 无")
    return "\n".join(lines) + "\n"


def _render_independent_review(
    receipts: list[_LoadedReceipt],
    item_results: list[AcceptanceItemResultV1],
) -> str:
    item = next(
        result for result in item_results if result.item_id == "independent-review"
    )
    if item.status != "PASS":
        return "# FAIL\n\n没有可信、完整且通过的外部独立审查收据。\n"
    loaded = next(
        receipt
        for receipt in receipts
        if receipt.receipt is not None
        and receipt.receipt.item_id == "independent-review"
    )
    receipt = loaded.receipt
    assert receipt is not None
    lines = [
        "# PASS",
        "",
        f"- 审查者：{receipt.reviewer_id}",
        f"- 签发者：{receipt.issuer_id}",
        f"- 收据：{receipt.receipt_id}",
        f"- 审查时间：{receipt.reviewed_at}",
        f"- 受审收据根：{receipt.reviewed_receipt_root_sha256}",
        f"- 受审证据根：{receipt.reviewed_evidence_root_sha256}",
        f"- 结论：{receipt.summary}",
        f"- 覆盖项目：{len(receipt.reviewed_item_ids)}",
        "",
        "## 外部审查证据",
        "",
    ]
    lines.extend(
        f"- {evidence.id}：{evidence.trace_path}（{evidence.actual_sha256}）"
        for evidence in loaded.evidence
    )
    return "\n".join(lines) + "\n"


def _json_bytes(model: BaseModel) -> bytes:
    return (
        json.dumps(
            model.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _verify_inputs_unchanged(files: list[_LoadedFile]) -> list[str]:
    issues: list[str] = []
    for loaded in files:
        if loaded.path is None or loaded.content is None:
            continue
        try:
            current_hash = _sha256_bytes(loaded.path.read_bytes())
        except OSError as exc:
            issues.append(f"input disappeared before package commit: {loaded.configured_path}: {exc}")
            continue
        if current_hash != loaded.actual_sha256:
            issues.append(f"input changed during acceptance execution: {loaded.configured_path}")
    return issues


def _package_input_root(files: list[_LoadedFile], *, reread: bool = False) -> str:
    entries: list[dict[str, str]] = []
    for loaded in files:
        digest = loaded.actual_sha256 or "0" * 64
        if reread and loaded.path is not None and loaded.path.is_file():
            digest = _sha256_bytes(loaded.path.read_bytes())
        entries.append(
            {
                "kind": loaded.kind,
                "id": loaded.id,
                "sha256": digest,
            }
        )
    return acceptance_content_root(PACKAGE_INPUT_ROOT_DOMAIN, entries)


def _trace_filename(index: int, loaded: _LoadedFile) -> str:
    suffix = Path(loaded.configured_path).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
        suffix = ".bin"
    digest = loaded.actual_sha256 or _sha256_bytes(
        _canonical_json_bytes({"missing": loaded.configured_path, "issues": loaded.issues})
    )
    return f"{index:03d}-{loaded.kind}-{digest[:16]}{suffix}"


def _write_trace_snapshot(trace_dir: Path, index: int, loaded: _LoadedFile) -> None:
    trace_name = _trace_filename(index, loaded)
    trace_path = trace_dir / trace_name
    if loaded.content is None:
        trace_path = trace_path.with_suffix(".missing.json")
        trace_path.write_bytes(
            _canonical_json_bytes(
                {
                    "source_unavailable": True,
                    "configured_path": loaded.configured_path,
                    "issues": loaded.issues,
                }
            )
        )
    else:
        trace_path.write_bytes(loaded.content)
    loaded.trace_path = f"traces/{trace_path.name}"


def _snapshot_input_root(package_dir: Path, files: list[_LoadedFile]) -> str:
    entries: list[dict[str, str]] = []
    for loaded in files:
        if loaded.trace_path is None:
            raise ValueError(f"package-local trace is missing for {loaded.id}")
        snapshot = package_dir / loaded.trace_path
        if not snapshot.is_file():
            raise ValueError(f"package-local trace was not written for {loaded.id}")
        digest = _sha256_bytes(snapshot.read_bytes()) if loaded.content is not None else "0" * 64
        entries.append({"kind": loaded.kind, "id": loaded.id, "sha256": digest})
    return acceptance_content_root(PACKAGE_INPUT_ROOT_DOMAIN, entries)


def _write_package(
    result: AcceptanceExecutionResultV1,
    receipts: list[_LoadedReceipt],
    request: _LoadedFile,
    manifest: _LoadedFile,
    trust_policy: _LoadedFile,
    *,
    output_root: Path,
    workspace_root: Path,
    package_input_root_sha256: str,
) -> tuple[AcceptanceExecutionResultV1, Path]:
    resolved_output = output_root.resolve()
    if not resolved_output.is_relative_to(workspace_root):
        raise ValueError("acceptance output root escapes workspace root")
    run_dir = resolved_output / result.run_id
    if run_dir.exists():
        raise ValueError(f"acceptance run directory already exists: {run_dir}")
    resolved_output.mkdir(parents=True, exist_ok=True)
    temporary = resolved_output / f".{result.run_id}.tmp-{uuid.uuid4().hex}"
    trace_dir = temporary / "traces"
    trace_dir.mkdir(parents=True)
    try:
        all_inputs = [trust_policy, request, manifest]
        for loaded in receipts:
            all_inputs.append(loaded.source)
            all_inputs.extend(loaded.evidence)
        for index, loaded in enumerate(all_inputs, start=1):
            _write_trace_snapshot(trace_dir, index, loaded)

        result = result.model_copy(
            update={"acceptance_manifest_path": manifest.trace_path}
        )
        entries = [loaded.index_entry() for loaded in all_inputs]
        evidence_index = AcceptanceEvidenceIndexV1(
            run_id=result.run_id,
            status=result.status,
            entries=entries,
        )
        evidence_index_bytes = _json_bytes(evidence_index)
        independent_review_bytes = _render_independent_review(
            receipts,
            result.items,
        ).encode("utf-8")
        report_bytes = _render_report(result).encode("utf-8")
        final_result = result.model_copy(
            update={
                "evidence_index_sha256": _sha256_bytes(evidence_index_bytes),
                "report_sha256": _sha256_bytes(report_bytes),
                "independent_review_sha256": _sha256_bytes(independent_review_bytes),
            }
        )
        (temporary / "evidence_index.json").write_bytes(evidence_index_bytes)
        (temporary / "independent_review.md").write_bytes(independent_review_bytes)
        (temporary / "AI_PLAYER_ACCEPTANCE_REPORT.md").write_bytes(report_bytes)
        (temporary / "results.json").write_bytes(_json_bytes(final_result))
        unchanged_issues = _verify_inputs_unchanged(all_inputs)
        if unchanged_issues:
            raise ValueError("; ".join(unchanged_issues))
        committed_input_root = _package_input_root(all_inputs, reread=True)
        if committed_input_root != package_input_root_sha256:
            raise ValueError("acceptance input root changed before atomic package commit")
        snapshot_input_root = _snapshot_input_root(temporary, all_inputs)
        if snapshot_input_root != package_input_root_sha256:
            raise ValueError("acceptance package snapshots do not match the verified input root")
        temporary.replace(run_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return final_result, run_dir


def execute_acceptance_request(
    request_path: Path,
    *,
    workspace_root: Path,
    output_root: Path,
    trust_policy_path: Path,
    trust_policy_sha256: str,
    write_failing_run: bool = False,
) -> AcceptanceExecutionResultV1:
    """Verify explicit receipts and optionally persist a fail-closed package."""

    workspace = workspace_root.resolve()
    if not workspace.is_dir():
        raise ValueError(f"workspace root does not exist: {workspace}")
    trust_policy_file, trust_policy = _load_trust_policy(
        trust_policy_path,
        trust_policy_sha256,
    )
    scoped_request = _scoped_path(workspace, str(request_path))
    request_content = scoped_request.read_bytes()
    request_sha256 = _sha256_bytes(request_content)
    request = AcceptanceRunRequestV1.model_validate(
        _parse_json(request_content, label=str(scoped_request))
    )
    # The v1 evidence-index kind enum is intentionally kept stable.  The run
    # request is evidence of the exact receipt set selected for this execution,
    # rather than a receipt itself, so it uses the existing receipt_evidence
    # kind with a distinct ID.  Its initial digest is verified again before the
    # atomic package commit and participates in every package-input root.
    request_file = _LoadedFile(
        kind="receipt_evidence",
        id=f"acceptance-request:{request.run_id}",
        configured_path=str(request_path),
        expected_sha256=request_sha256,
        path=scoped_request,
        content=request_content,
        actual_sha256=request_sha256,
    )

    manifest = _load_file(
        workspace,
        trust_policy.canonical_manifest,
        kind="acceptance_manifest",
        item_id="acceptance-manifest",
    )
    global_issues = [*manifest.issues]
    manifest_hash = trust_policy.canonical_manifest.sha256
    if manifest.content is not None:
        try:
            _FrozenAcceptanceManifestV1.model_validate(
                _parse_json(manifest.content, label=trust_policy.canonical_manifest.path)
            )
        except (TypeError, ValueError) as exc:
            global_issues.append(f"frozen acceptance manifest validation failed: {exc}")

    receipts = _load_receipts(workspace, request.receipts)
    for loaded in receipts:
        global_issues.extend(loaded.source.issues)
        global_issues.extend(loaded.issues)
    global_issues.extend(_duplicate_issues(receipts))
    (
        reviewed_receipt_root,
        reviewed_evidence_root,
        latest_reviewed_receipt_issued_at,
    ) = _review_corpus_roots(receipts)
    item_results, item_issues = _evaluate_items(
        receipts,
        policy=trust_policy,
        manifest_sha256=manifest_hash,
        reviewed_receipt_root_sha256=reviewed_receipt_root,
        reviewed_evidence_root_sha256=reviewed_evidence_root,
        latest_reviewed_receipt_issued_at=latest_reviewed_receipt_issued_at,
    )
    global_issues.extend(item_issues)

    all_loaded_files = [trust_policy_file, request_file, manifest]
    for loaded in receipts:
        all_loaded_files.append(loaded.source)
        all_loaded_files.extend(loaded.evidence)
    global_issues.extend(_verify_inputs_unchanged(all_loaded_files))
    global_issues = list(dict.fromkeys(global_issues))
    package_input_root = _package_input_root(all_loaded_files)

    counts = {
        category: f"{sum(item.status == 'PASS' for item in item_results if item.category == category)}/{len(item_ids)}"
        for category, item_ids in EXPECTED_ITEMS.items()
    }
    parsed_receipts = [item.receipt for item in receipts if item.receipt is not None]
    blocker_count = sum(item.blocker_count for item in parsed_receipts)
    major_count = sum(item.major_count for item in parsed_receipts)
    safety_violation_count = sum(item.safety_violation_count for item in parsed_receipts)
    clean_status = next(
        item.status for item in item_results if item.item_id == "clean-database-rerun"
    )
    review_status = next(
        item.status for item in item_results if item.item_id == "independent-review"
    )
    status = (
        "PASS"
        if not global_issues
        and counts == {"AP": "11/11", "P": "13/13", "E2E": "10/10", "G": "12/12"}
        and blocker_count == 0
        and major_count == 0
        and safety_violation_count == 0
        and clean_status == "PASS"
        and review_status == "PASS"
        else "FAIL"
    )
    result = AcceptanceExecutionResultV1(
        run_id=request.run_id,
        status=status,
        acceptance_manifest_path=trust_policy.canonical_manifest.path,
        acceptance_manifest_sha256=manifest_hash,
        trust_policy_sha256=trust_policy_sha256,
        reviewed_receipt_root_sha256=reviewed_receipt_root,
        reviewed_evidence_root_sha256=reviewed_evidence_root,
        package_input_root_sha256=package_input_root,
        counts=counts,
        blocker_count=blocker_count,
        major_count=major_count,
        safety_violation_count=safety_violation_count,
        clean_database_rerun=clean_status,
        independent_review=review_status,
        items=item_results,
        issues=global_issues,
    )
    if status == "FAIL" and not write_failing_run:
        raise AcceptanceExecutionFailed(result)
    written_result, run_dir = _write_package(
        result,
        receipts,
        request_file,
        manifest,
        trust_policy_file,
        output_root=output_root,
        workspace_root=workspace,
        package_input_root_sha256=package_input_root,
    )
    if status == "FAIL":
        raise AcceptanceExecutionFailed(written_result, run_dir)
    return written_result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--trust-policy", type=Path, required=True)
    parser.add_argument("--trust-policy-sha256", required=True)
    parser.add_argument("--write-failing-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = execute_acceptance_request(
            args.request,
            workspace_root=args.workspace_root,
            output_root=args.output_root,
            trust_policy_path=args.trust_policy,
            trust_policy_sha256=args.trust_policy_sha256,
            write_failing_run=args.write_failing_run,
        )
    except AcceptanceExecutionFailed as exc:
        print(exc.result.model_dump_json(by_alias=True, indent=2))
        return 1
    print(result.model_dump_json(by_alias=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
