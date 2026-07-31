"""Import, verify, and immutably freeze human AFK Stage-1 truth.

The trust boundary is deliberately narrow: callers must supply a detached hash for
the v4 candidate manifest, every candidate needs one human decision, and a detached
Ed25519 attestation must resolve to an active human reviewer in the code-owned trust
registry.  This module never creates a signature and has no production output default.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import real_image_holdout
from .afk_freeze_candidates import validate_afk_freeze_candidate


REVIEW_SCHEMA = "game-observatory.ai-player.afk-human-truth-review.v1"
ATTESTATION_SCHEMA = "game-observatory.ai-player.afk-human-truth-attestation.v1"
IMPORT_SCHEMA = "game-observatory.ai-player.afk-human-truth-import.v1"
FROZEN_ITEM_SCHEMA = "game-observatory.ai-player.afk-frozen-truth-item.v1"
FROZEN_COLLECTION_SCHEMA = "game-observatory.ai-player.afk-frozen-truth-collection.v1"
FROZEN_MANIFEST_SCHEMA = "game-observatory.ai-player.afk-frozen-truth-manifest.v1"
CANDIDATE_MANIFEST_SCHEMA = "game-observatory.ai-player.afk-freeze-candidate-manifest.v1"
CANDIDATE_MANIFEST_ID = "afk_hero_growth_v1_candidate_v4"
BENCHMARK_ID = "afk_hero_growth_v1"
HUMAN_REVIEWER_KIND = "human_reviewer"
APPROVAL_VERDICT = "approved_for_afk_stage1_truth_freeze"
COLLECTION_ORDER = (
    "states",
    "edges",
    "objects",
    "routes",
    "controlled_interruptions",
    "boundaries",
)

Sha256 = str
CollectionName = Literal[
    "states",
    "edges",
    "objects",
    "routes",
    "controlled_interruptions",
    "boundaries",
]
Decision = Literal["accepted", "excluded"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class HumanReviewerV1(_StrictModel):
    kind: Literal["human_reviewer"]
    id: str = Field(min_length=1)


class HumanTruthEvidenceRefV1(_StrictModel):
    kind: str = Field(min_length=1)
    id: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class ImmutableFileRefV1(_StrictModel):
    path: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class AFKHumanTruthDecisionV1(_StrictModel):
    collection: CollectionName
    candidate_id: str = Field(min_length=1)
    candidate_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    decision: Decision
    human_reason: str = Field(min_length=20)
    evidence_refs: tuple[HumanTruthEvidenceRefV1, ...] = Field(min_length=1)

    @field_validator("human_reason")
    @classmethod
    def _substantive_reason(cls, value: str) -> str:
        if len(value.strip()) < 20:
            raise ValueError("human_reason must contain at least 20 substantive characters")
        return value

    @field_validator("evidence_refs")
    @classmethod
    def _unique_evidence_refs(
        cls, value: tuple[HumanTruthEvidenceRefV1, ...]
    ) -> tuple[HumanTruthEvidenceRefV1, ...]:
        keys = {(item.kind, item.id, item.sha256) for item in value}
        if len(keys) != len(value):
            raise ValueError("evidence_refs must not contain duplicates")
        return value


class AFKHumanTruthReviewV1(_StrictModel):
    schema_id: Literal[REVIEW_SCHEMA] = Field(default=REVIEW_SCHEMA, alias="schema")
    id: str = Field(min_length=1)
    benchmark_id: Literal[BENCHMARK_ID] = BENCHMARK_ID
    candidate_manifest_id: Literal[CANDIDATE_MANIFEST_ID] = CANDIDATE_MANIFEST_ID
    candidate_manifest_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    reviewer: HumanReviewerV1
    reviewed_at: datetime
    items: tuple[AFKHumanTruthDecisionV1, ...] = Field(min_length=1)

    @field_validator("reviewed_at")
    @classmethod
    def _aware_reviewed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reviewed_at must include a timezone")
        return value


class AFKHumanTruthAttestationV1(_StrictModel):
    schema_id: Literal[ATTESTATION_SCHEMA] = Field(default=ATTESTATION_SCHEMA, alias="schema")
    reviewer: HumanReviewerV1
    candidate_manifest_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    review_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    verdict: Literal[APPROVAL_VERDICT]
    generated_at: datetime
    adjudication_body: str = Field(min_length=20)
    signature_algorithm: Literal["ed25519"]
    signature_base64: str = Field(min_length=1)

    @field_validator("generated_at")
    @classmethod
    def _aware_generated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must include a timezone")
        return value

    @field_validator("adjudication_body")
    @classmethod
    def _substantive_adjudication_body(cls, value: str) -> str:
        if len(value.strip()) < 20:
            raise ValueError(
                "adjudication_body must contain at least 20 substantive characters"
            )
        return value


class AFKHumanTruthImportManifestV1(_StrictModel):
    schema_id: Literal[IMPORT_SCHEMA] = Field(default=IMPORT_SCHEMA, alias="schema")
    id: str = Field(min_length=1)
    benchmark_id: Literal[BENCHMARK_ID] = BENCHMARK_ID
    candidate_manifest_id: Literal[CANDIDATE_MANIFEST_ID] = CANDIDATE_MANIFEST_ID
    candidate_manifest_sha256: str = Field(min_length=64, max_length=64)
    review: ImmutableFileRefV1
    attestation: ImmutableFileRefV1
    reviewer: HumanReviewerV1
    reviewed_at: datetime
    item_count: int = Field(ge=1)
    counts_by_collection: dict[str, int]
    human_review_status: Literal["signature_verified"]
    import_hash: str = Field(min_length=64, max_length=64)


class AFKFrozenTruthItemV1(_StrictModel):
    schema_id: Literal[FROZEN_ITEM_SCHEMA] = Field(default=FROZEN_ITEM_SCHEMA, alias="schema")
    collection: CollectionName
    candidate_id: str = Field(min_length=1)
    candidate_hash: str = Field(min_length=64, max_length=64)
    candidate: dict[str, Any]
    decision: Decision
    human_reason: str = Field(min_length=20)
    evidence_refs: tuple[HumanTruthEvidenceRefV1, ...] = Field(min_length=1)


class AFKFrozenTruthCollectionV1(_StrictModel):
    schema_id: Literal[FROZEN_COLLECTION_SCHEMA] = Field(
        default=FROZEN_COLLECTION_SCHEMA, alias="schema"
    )
    collection: CollectionName
    items: tuple[AFKFrozenTruthItemV1, ...]


class AFKFrozenTruthManifestV1(_StrictModel):
    schema_id: Literal[FROZEN_MANIFEST_SCHEMA] = Field(
        default=FROZEN_MANIFEST_SCHEMA, alias="schema"
    )
    id: str = Field(min_length=1)
    benchmark_id: Literal[BENCHMARK_ID] = BENCHMARK_ID
    candidate_manifest_id: Literal[CANDIDATE_MANIFEST_ID] = CANDIDATE_MANIFEST_ID
    candidate_manifest_sha256: str = Field(min_length=64, max_length=64)
    review_sha256: str = Field(min_length=64, max_length=64)
    attestation_sha256: str = Field(min_length=64, max_length=64)
    reviewer: HumanReviewerV1
    frozen_at: datetime
    freeze_status: Literal["human_signed_frozen"]
    frozen: Literal[True]
    freeze_pass: Literal[True]
    counts: dict[str, int]
    collections: dict[str, ImmutableFileRefV1]
    manifest_hash: str = Field(min_length=64, max_length=64)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _load_json_object(path: Path, context: str) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"{context} does not exist: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a JSON object")
    return value


def _immutable_write(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != payload:
            raise FileExistsError(f"immutable artifact already exists with other bytes: {path}")
    else:
        path.write_bytes(payload)
    return _sha256_bytes(payload)


def _immutable_write_batch(payloads: Mapping[Path, bytes]) -> dict[Path, str]:
    """Preflight every destination so an immutable conflict cannot leave a half-package."""

    for path, payload in payloads.items():
        if path.exists() and (not path.is_file() or path.read_bytes() != payload):
            raise FileExistsError(f"immutable artifact already exists with other bytes: {path}")
    return {path: _immutable_write(path, payload) for path, payload in payloads.items()}


def _verified_collection_file(
    candidate_root: Path,
    reference: Mapping[str, Any],
    context: str,
) -> dict[str, Any]:
    if not {"path", "sha256"}.issubset(reference):
        raise ValueError(f"{context} collection reference is incomplete")
    relative = Path(str(reference["path"]))
    if relative.is_absolute():
        raise ValueError(f"{context} collection path must be relative")
    path = (candidate_root / relative).resolve()
    if not _inside(path, candidate_root):
        raise ValueError(f"{context} collection path escapes candidate package")
    if _sha256_file(path) != reference["sha256"]:
        raise ValueError(f"{context} collection hash mismatch")
    return _load_json_object(path, context)


def _candidate_inventory(
    candidate_manifest_path: Path,
    expected_candidate_manifest_sha256: str,
) -> tuple[dict[str, Any], dict[str, tuple[str, dict[str, Any]]]]:
    candidate_manifest_path = candidate_manifest_path.resolve()
    actual_hash = _sha256_file(candidate_manifest_path)
    if actual_hash != expected_candidate_manifest_sha256:
        raise ValueError("candidate manifest does not match detached expected hash")
    manifest = _load_json_object(candidate_manifest_path, "candidate manifest")
    if manifest.get("schema") != CANDIDATE_MANIFEST_SCHEMA:
        raise ValueError("candidate manifest schema is unsupported")
    if manifest.get("id") != CANDIDATE_MANIFEST_ID:
        raise ValueError("candidate manifest is not AFK candidate v4")
    if manifest.get("benchmark_id") != BENCHMARK_ID:
        raise ValueError("candidate benchmark id is unsupported")
    validation = validate_afk_freeze_candidate(candidate_manifest_path)
    if not validation["candidate_structure_pass"] or validation["errors"]:
        raise ValueError(f"candidate structure is invalid: {validation['errors']}")

    root = candidate_manifest_path.parent.resolve()
    collections = manifest.get("collections")
    if not isinstance(collections, Mapping):
        raise ValueError("candidate collections must be an object")
    states = _verified_collection_file(root, collections.get("states", {}), "states")
    edges = _verified_collection_file(root, collections.get("edges", {}), "edges")
    objects = _verified_collection_file(root, collections.get("objects", {}), "objects")
    boundaries = _verified_collection_file(root, collections.get("boundaries", {}), "boundaries")
    routes = [
        _verified_collection_file(root, reference, f"route[{index}]")
        for index, reference in enumerate(collections.get("routes", []))
    ]
    interruptions = [
        _verified_collection_file(root, reference, f"interruption[{index}]")
        for index, reference in enumerate(collections.get("interruptions", []))
    ]
    grouped: dict[str, list[dict[str, Any]]] = {
        "states": list(states.get("items", [])),
        "edges": [
            *edges.get("safe_edges", []),
            *edges.get("excluded_edges", []),
            *edges.get("unresolved_edges", []),
        ],
        "objects": list(objects.get("items", [])),
        "routes": routes,
        "controlled_interruptions": interruptions,
        "boundaries": list(boundaries.get("items", [])),
    }
    inventory: dict[str, tuple[str, dict[str, Any]]] = {}
    for collection in COLLECTION_ORDER:
        for raw_item in grouped[collection]:
            if not isinstance(raw_item, dict):
                raise ValueError(f"candidate item in {collection} must be an object")
            candidate_id = raw_item.get("id")
            if not isinstance(candidate_id, str) or not candidate_id:
                raise ValueError(f"candidate item in {collection} has no id")
            if candidate_id in inventory:
                raise ValueError(f"duplicate candidate id across collections: {candidate_id}")
            candidate_hash = raw_item.get("candidate_hash")
            body = dict(raw_item)
            body.pop("candidate_hash", None)
            if candidate_hash != _sha256_bytes(_canonical_bytes(body)):
                raise ValueError(f"candidate item hash mismatch: {candidate_id}")
            inventory[candidate_id] = (collection, raw_item)
    return manifest, inventory


def _evidence_ref_keys(value: Any) -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    if isinstance(value, Mapping):
        if all(isinstance(value.get(key), str) for key in ("kind", "id", "sha256")):
            found.add((str(value["kind"]), str(value["id"]), str(value["sha256"])))
        for child in value.values():
            found.update(_evidence_ref_keys(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_evidence_ref_keys(child))
    return found


def _verified_review_and_attestation(
    *,
    candidate_manifest_path: Path,
    expected_candidate_manifest_sha256: str,
    review_path: Path,
    attestation_path: Path,
) -> tuple[
    AFKHumanTruthReviewV1,
    AFKHumanTruthAttestationV1,
    dict[str, tuple[str, dict[str, Any]]],
    str,
    str,
]:
    _manifest, inventory = _candidate_inventory(
        candidate_manifest_path, expected_candidate_manifest_sha256
    )
    review_raw = review_path.read_bytes()
    review_sha256 = _sha256_bytes(review_raw)
    review = AFKHumanTruthReviewV1.model_validate_json(review_raw)
    if review.candidate_manifest_sha256 != expected_candidate_manifest_sha256:
        raise ValueError("review candidate manifest hash does not match detached hash")

    decisions: dict[str, AFKHumanTruthDecisionV1] = {}
    for item in review.items:
        if item.candidate_id in decisions:
            raise ValueError(f"duplicate human decision: {item.candidate_id}")
        candidate = inventory.get(item.candidate_id)
        if candidate is None:
            raise ValueError(f"unknown candidate in human review: {item.candidate_id}")
        expected_collection, candidate_payload = candidate
        if item.collection != expected_collection:
            raise ValueError(f"candidate collection mismatch: {item.candidate_id}")
        if item.candidate_hash != candidate_payload["candidate_hash"]:
            raise ValueError(f"candidate hash mismatch in human review: {item.candidate_id}")
        allowed_refs = _evidence_ref_keys(candidate_payload)
        for reference in item.evidence_refs:
            key = (reference.kind, reference.id, reference.sha256)
            if key not in allowed_refs:
                raise ValueError(
                    f"human evidence reference is not bound to candidate: {item.candidate_id}"
                )
        decisions[item.candidate_id] = item
    missing = sorted(set(inventory) - set(decisions))
    if missing:
        raise ValueError(f"human review is missing candidate decisions: {missing}")

    attestation_raw = attestation_path.read_bytes()
    attestation_sha256 = _sha256_bytes(attestation_raw)
    attestation = AFKHumanTruthAttestationV1.model_validate_json(attestation_raw)
    if attestation.reviewer != review.reviewer:
        raise ValueError("attestation reviewer does not match human review")
    if attestation.candidate_manifest_sha256 != expected_candidate_manifest_sha256:
        raise ValueError("attestation candidate manifest hash does not match detached hash")
    if attestation.review_sha256 != review_sha256:
        raise ValueError("attestation review hash does not match detached review")
    if attestation.generated_at != review.reviewed_at:
        raise ValueError("attestation timestamp does not match human review")

    reviewer_id = review.reviewer.id
    registry_entry = real_image_holdout.TRUSTED_REVIEWER_REGISTRY.get(reviewer_id)
    if registry_entry is None:
        raise ValueError(f"human reviewer is not trusted: {reviewer_id}")
    if set(registry_entry) != {"kind", "public_key_base64", "status"}:
        raise ValueError("trusted human reviewer registry entry has unsupported fields")
    if registry_entry["status"] != "trusted":
        raise ValueError(f"human reviewer is not active: {reviewer_id}")
    if registry_entry["kind"] != HUMAN_REVIEWER_KIND:
        raise ValueError("trusted reviewer is not registered as a human")
    if review.reviewer.kind != registry_entry["kind"]:
        raise ValueError("human reviewer kind does not match registry")

    signed_payload = attestation.model_dump(mode="json", exclude={"signature_base64"})
    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(registry_entry["public_key_base64"], validate=True)
        )
        signature = base64.b64decode(attestation.signature_base64, validate=True)
        public_key.verify(signature, _canonical_bytes(signed_payload))
    except (ValueError, TypeError, InvalidSignature) as error:
        raise ValueError("detached human truth signature is invalid") from error
    return review, attestation, inventory, review_sha256, attestation_sha256


def validate_afk_human_truth_review(
    *,
    candidate_manifest_path: Path,
    expected_candidate_manifest_sha256: str,
    review_path: Path,
    attestation_path: Path,
) -> dict[str, Any]:
    """Validate the complete signed human adjudication without writing any artifact."""

    review, attestation, inventory, review_sha256, attestation_sha256 = (
        _verified_review_and_attestation(
            candidate_manifest_path=candidate_manifest_path,
            expected_candidate_manifest_sha256=expected_candidate_manifest_sha256,
            review_path=review_path,
            attestation_path=attestation_path,
        )
    )
    counts = {collection: 0 for collection in COLLECTION_ORDER}
    accepted = 0
    excluded = 0
    for item in review.items:
        counts[item.collection] += 1
        accepted += item.decision == "accepted"
        excluded += item.decision == "excluded"
    return {
        "schema": "game-observatory.ai-player.afk-human-truth-validation.v1",
        "valid": True,
        "candidate_manifest_sha256": expected_candidate_manifest_sha256,
        "review_sha256": review_sha256,
        "attestation_sha256": attestation_sha256,
        "reviewer": review.reviewer.model_dump(mode="json"),
        "reviewed_at": review.reviewed_at.isoformat(),
        "signature_algorithm": attestation.signature_algorithm,
        "item_count": len(inventory),
        "accepted_count": accepted,
        "excluded_count": excluded,
        "counts_by_collection": counts,
    }


def import_afk_human_truth_review(
    *,
    candidate_manifest_path: Path,
    expected_candidate_manifest_sha256: str,
    review_path: Path,
    attestation_path: Path,
    output_dir: Path,
) -> Path:
    """Copy an already verified human review into an immutable import package."""

    validation = validate_afk_human_truth_review(
        candidate_manifest_path=candidate_manifest_path,
        expected_candidate_manifest_sha256=expected_candidate_manifest_sha256,
        review_path=review_path,
        attestation_path=attestation_path,
    )
    review_payload = review_path.read_bytes()
    attestation_payload = attestation_path.read_bytes()
    review_target = output_dir / "review.v1.json"
    attestation_target = output_dir / "attestation.v1.json"
    review_sha256 = _sha256_bytes(review_payload)
    attestation_sha256 = _sha256_bytes(attestation_payload)
    body: dict[str, Any] = {
        "schema": IMPORT_SCHEMA,
        "id": f"afk-human-truth-import.{review_sha256[:16]}",
        "benchmark_id": BENCHMARK_ID,
        "candidate_manifest_id": CANDIDATE_MANIFEST_ID,
        "candidate_manifest_sha256": expected_candidate_manifest_sha256,
        "review": {"path": review_target.name, "sha256": review_sha256},
        "attestation": {"path": attestation_target.name, "sha256": attestation_sha256},
        "reviewer": validation["reviewer"],
        "reviewed_at": validation["reviewed_at"],
        "item_count": validation["item_count"],
        "counts_by_collection": validation["counts_by_collection"],
        "human_review_status": "signature_verified",
    }
    body["import_hash"] = _sha256_bytes(_canonical_bytes(body))
    AFKHumanTruthImportManifestV1.model_validate(body)
    target = output_dir / "import_manifest.v1.json"
    _immutable_write_batch(
        {
            review_target: review_payload,
            attestation_target: attestation_payload,
            target: _json_bytes(body),
        }
    )
    return target


def _load_verified_import(
    *,
    candidate_manifest_path: Path,
    expected_candidate_manifest_sha256: str,
    import_manifest_path: Path,
) -> tuple[AFKHumanTruthImportManifestV1, Path, Path, dict[str, Any]]:
    raw = _load_json_object(import_manifest_path, "human truth import manifest")
    claimed_hash = raw.pop("import_hash", None)
    if claimed_hash != _sha256_bytes(_canonical_bytes(raw)):
        raise ValueError("human truth import manifest hash mismatch")
    raw["import_hash"] = claimed_hash
    imported = AFKHumanTruthImportManifestV1.model_validate(raw)
    if imported.candidate_manifest_sha256 != expected_candidate_manifest_sha256:
        raise ValueError("human truth import is bound to another candidate manifest")
    root = import_manifest_path.resolve().parent

    def resolve(reference: Mapping[str, str], context: str) -> Path:
        relative = Path(reference["path"])
        if relative.is_absolute():
            raise ValueError(f"{context} path must be import-relative")
        path = (root / relative).resolve()
        if not _inside(path, root):
            raise ValueError(f"{context} path escapes import package")
        if _sha256_file(path) != reference["sha256"]:
            raise ValueError(f"{context} hash mismatch")
        return path

    review_path = resolve(imported.review.model_dump(mode="json"), "imported review")
    attestation_path = resolve(
        imported.attestation.model_dump(mode="json"), "imported attestation"
    )
    validation = validate_afk_human_truth_review(
        candidate_manifest_path=candidate_manifest_path,
        expected_candidate_manifest_sha256=expected_candidate_manifest_sha256,
        review_path=review_path,
        attestation_path=attestation_path,
    )
    if validation["item_count"] != imported.item_count:
        raise ValueError("human truth import item count mismatch")
    if validation["counts_by_collection"] != imported.counts_by_collection:
        raise ValueError("human truth import collection counts mismatch")
    return imported, review_path, attestation_path, validation


def build_frozen_afk_human_truth(
    *,
    candidate_manifest_path: Path,
    expected_candidate_manifest_sha256: str,
    import_manifest_path: Path,
    output_dir: Path,
) -> Path:
    """Build a self-contained immutable frozen artifact from a verified import."""

    imported, review_path, _attestation_path, validation = _load_verified_import(
        candidate_manifest_path=candidate_manifest_path,
        expected_candidate_manifest_sha256=expected_candidate_manifest_sha256,
        import_manifest_path=import_manifest_path,
    )
    _manifest, inventory = _candidate_inventory(
        candidate_manifest_path, expected_candidate_manifest_sha256
    )
    review = AFKHumanTruthReviewV1.model_validate_json(review_path.read_bytes())
    decisions = {item.candidate_id: item for item in review.items}
    collection_refs: dict[str, dict[str, str]] = {}
    collection_payloads: dict[Path, bytes] = {}
    for collection in COLLECTION_ORDER:
        frozen_items = []
        for candidate_id, (candidate_collection, candidate_payload) in sorted(inventory.items()):
            if candidate_collection != collection:
                continue
            decision = decisions[candidate_id]
            frozen_item = AFKFrozenTruthItemV1(
                collection=collection,
                candidate_id=candidate_id,
                candidate_hash=candidate_payload["candidate_hash"],
                candidate=candidate_payload,
                decision=decision.decision,
                human_reason=decision.human_reason,
                evidence_refs=decision.evidence_refs,
            )
            frozen_items.append(frozen_item.model_dump(mode="json"))
        payload = AFKFrozenTruthCollectionV1(
            collection=collection,
            items=tuple(AFKFrozenTruthItemV1.model_validate(item) for item in frozen_items),
        ).model_dump(mode="json")
        relative = f"{collection}.v1.json"
        serialized = _json_bytes(payload)
        digest = _sha256_bytes(serialized)
        collection_payloads[output_dir / relative] = serialized
        collection_refs[collection] = {"path": relative, "sha256": digest}

    body: dict[str, Any] = {
        "schema": FROZEN_MANIFEST_SCHEMA,
        "id": f"afk-human-truth-freeze.{validation['review_sha256'][:16]}",
        "benchmark_id": BENCHMARK_ID,
        "candidate_manifest_id": CANDIDATE_MANIFEST_ID,
        "candidate_manifest_sha256": expected_candidate_manifest_sha256,
        "review_sha256": validation["review_sha256"],
        "attestation_sha256": validation["attestation_sha256"],
        "reviewer": validation["reviewer"],
        "frozen_at": validation["reviewed_at"],
        "freeze_status": "human_signed_frozen",
        "frozen": True,
        "freeze_pass": True,
        "counts": {
            "items": validation["item_count"],
            "accepted": validation["accepted_count"],
            "excluded": validation["excluded_count"],
            **validation["counts_by_collection"],
        },
        "collections": collection_refs,
    }
    body["manifest_hash"] = _sha256_bytes(_canonical_bytes(body))
    AFKFrozenTruthManifestV1.model_validate(body)
    target = output_dir / "frozen_manifest.v1.json"
    _immutable_write_batch({**collection_payloads, target: _json_bytes(body)})
    return target


def validate_frozen_afk_human_truth(
    *,
    candidate_manifest_path: Path,
    expected_candidate_manifest_sha256: str,
    import_manifest_path: Path,
    frozen_manifest_path: Path,
) -> dict[str, Any]:
    """Revalidate a frozen artifact against its candidate and signed import roots."""

    imported, review_path, _attestation_path, validation = _load_verified_import(
        candidate_manifest_path=candidate_manifest_path,
        expected_candidate_manifest_sha256=expected_candidate_manifest_sha256,
        import_manifest_path=import_manifest_path,
    )
    _manifest, inventory = _candidate_inventory(
        candidate_manifest_path, expected_candidate_manifest_sha256
    )
    raw = _load_json_object(frozen_manifest_path, "frozen manifest")
    claimed_hash = raw.pop("manifest_hash", None)
    if claimed_hash != _sha256_bytes(_canonical_bytes(raw)):
        raise ValueError("frozen manifest hash mismatch")
    raw["manifest_hash"] = claimed_hash
    frozen = AFKFrozenTruthManifestV1.model_validate(raw)
    if frozen.candidate_manifest_sha256 != expected_candidate_manifest_sha256:
        raise ValueError("frozen artifact is bound to another candidate manifest")
    if frozen.review_sha256 != imported.review.sha256:
        raise ValueError("frozen artifact review hash mismatch")
    if frozen.attestation_sha256 != imported.attestation.sha256:
        raise ValueError("frozen artifact attestation hash mismatch")
    review = AFKHumanTruthReviewV1.model_validate_json(review_path.read_bytes())
    decisions = {item.candidate_id: item for item in review.items}
    seen: set[str] = set()
    root = frozen_manifest_path.resolve().parent
    if set(frozen.collections) != set(COLLECTION_ORDER):
        raise ValueError("frozen manifest collections do not match the six required classes")
    for collection in COLLECTION_ORDER:
        reference = frozen.collections.get(collection)
        if reference is None:
            raise ValueError(f"frozen collection is missing: {collection}")
        payload = _verified_collection_file(
            root, reference.model_dump(mode="json"), f"frozen {collection}"
        )
        parsed = AFKFrozenTruthCollectionV1.model_validate(payload)
        if parsed.collection != collection:
            raise ValueError(f"frozen collection name mismatch: {collection}")
        for item in parsed.items:
            if item.candidate_id in seen:
                raise ValueError(f"duplicate frozen item: {item.candidate_id}")
            candidate = inventory.get(item.candidate_id)
            if candidate is None or candidate[0] != collection:
                raise ValueError(f"unknown frozen candidate: {item.candidate_id}")
            if item.candidate != candidate[1] or item.candidate_hash != candidate[1]["candidate_hash"]:
                raise ValueError(f"frozen candidate payload mismatch: {item.candidate_id}")
            decision = decisions[item.candidate_id]
            if (
                item.decision != decision.decision
                or item.human_reason != decision.human_reason
                or item.evidence_refs != decision.evidence_refs
            ):
                raise ValueError(f"frozen human decision mismatch: {item.candidate_id}")
            seen.add(item.candidate_id)
    if seen != set(inventory):
        raise ValueError("frozen artifact does not cover every candidate")
    if frozen.counts.get("items") != len(seen):
        raise ValueError("frozen manifest item count mismatch")
    return {
        "schema": "game-observatory.ai-player.afk-frozen-truth-validation.v1",
        "valid": True,
        "freeze_pass": True,
        "candidate_manifest_sha256": expected_candidate_manifest_sha256,
        "frozen_manifest_sha256": _sha256_file(frozen_manifest_path),
        "item_count": len(seen),
        "reviewer": validation["reviewer"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "import"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--candidate-manifest", type=Path, required=True)
        sub.add_argument("--candidate-manifest-sha256", required=True)
        sub.add_argument("--review", type=Path, required=True)
        sub.add_argument("--attestation", type=Path, required=True)
        if command == "import":
            sub.add_argument("--output-dir", type=Path, required=True)
    freeze = subparsers.add_parser("freeze")
    freeze.add_argument("--candidate-manifest", type=Path, required=True)
    freeze.add_argument("--candidate-manifest-sha256", required=True)
    freeze.add_argument("--import-manifest", type=Path, required=True)
    freeze.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    common = {
        "candidate_manifest_path": args.candidate_manifest,
        "expected_candidate_manifest_sha256": args.candidate_manifest_sha256,
    }
    if args.command == "validate":
        result = validate_afk_human_truth_review(
            **common, review_path=args.review, attestation_path=args.attestation
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "import":
        result = import_afk_human_truth_review(
            **common,
            review_path=args.review,
            attestation_path=args.attestation,
            output_dir=args.output_dir,
        )
        print(result)
    else:
        result = build_frozen_afk_human_truth(
            **common,
            import_manifest_path=args.import_manifest,
            output_dir=args.output_dir,
        )
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
