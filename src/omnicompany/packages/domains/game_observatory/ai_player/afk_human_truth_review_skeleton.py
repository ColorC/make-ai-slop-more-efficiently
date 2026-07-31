"""Build the deterministic unsigned AFK v4 human-review skeleton."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from .afk_human_truth_freeze import (
    ATTESTATION_SCHEMA,
    BENCHMARK_ID,
    CANDIDATE_MANIFEST_ID,
    COLLECTION_ORDER,
    REVIEW_SCHEMA,
    CollectionName,
    ImmutableFileRefV1,
    _StrictModel,
    _candidate_inventory,
    _canonical_bytes,
    _immutable_write,
    _json_bytes,
    _sha256_bytes,
    _sha256_file,
    _verified_collection_file,
)


SKELETON_SCHEMA = "game-observatory.ai-player.afk-human-truth-review-skeleton.v1"
REVIEW_STATUS = "unsigned_pending_human_review"
EXPECTED_COUNTS = {
    "states": 26,
    "edges": 45,
    "objects": 51,
    "routes": 8,
    "controlled_interruptions": 6,
    "boundaries": 4,
}
EXPECTED_ITEM_COUNT = sum(EXPECTED_COUNTS.values())


class AFKHumanTruthCandidateSourceV1(_StrictModel):
    path: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    json_pointer: str


class AFKHumanTruthPendingDecisionV1(_StrictModel):
    collection: CollectionName
    candidate_id: str = Field(min_length=1)
    candidate_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    candidate_source: AFKHumanTruthCandidateSourceV1
    candidate: dict[str, Any]
    evidence_refs: tuple[dict[str, Any], ...] = Field(min_length=1)
    decision: None = None
    human_reason: Literal[""] = ""

    @model_validator(mode="after")
    def candidate_and_evidence_are_exact(self) -> "AFKHumanTruthPendingDecisionV1":
        if self.candidate.get("id") != self.candidate_id:
            raise ValueError("pending decision candidate id does not match candidate payload")
        if self.candidate.get("candidate_hash") != self.candidate_hash:
            raise ValueError("pending decision candidate hash does not match candidate payload")
        if tuple(self.candidate.get("evidence_refs", ())) != self.evidence_refs:
            raise ValueError("pending decision evidence does not match candidate payload")
        return self


class AFKHumanTruthReviewSkeletonV1(_StrictModel):
    schema_id: Literal[SKELETON_SCHEMA] = Field(default=SKELETON_SCHEMA, alias="schema")
    id: str = Field(min_length=1)
    benchmark_id: Literal[BENCHMARK_ID] = BENCHMARK_ID
    candidate_manifest_id: Literal[CANDIDATE_MANIFEST_ID] = CANDIDATE_MANIFEST_ID
    candidate_manifest: ImmutableFileRefV1
    target_review_schema: Literal[REVIEW_SCHEMA] = REVIEW_SCHEMA
    target_attestation_schema: Literal[ATTESTATION_SCHEMA] = ATTESTATION_SCHEMA
    review_status: Literal[REVIEW_STATUS] = REVIEW_STATUS
    reviewer: None = None
    reviewed_at: None = None
    item_count: Literal[140] = EXPECTED_ITEM_COUNT
    counts_by_collection: dict[str, int]
    items: tuple[AFKHumanTruthPendingDecisionV1, ...] = Field(min_length=EXPECTED_ITEM_COUNT)
    package_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def complete_pending_inventory(self) -> "AFKHumanTruthReviewSkeletonV1":
        if self.counts_by_collection != EXPECTED_COUNTS:
            raise ValueError("review skeleton collection counts do not match AFK v4")
        if len(self.items) != EXPECTED_ITEM_COUNT:
            raise ValueError("review skeleton does not contain exactly 140 candidates")
        candidate_ids = [item.candidate_id for item in self.items]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("review skeleton contains duplicate candidate ids")
        if any(item.decision is not None or item.human_reason for item in self.items):
            raise ValueError("review skeleton contains a human decision")
        return self


def _collection_reference(
    collections: Mapping[str, Any],
    name: str,
) -> Mapping[str, Any]:
    reference = collections.get(name)
    if not isinstance(reference, Mapping):
        raise ValueError(f"candidate collection reference is missing: {name}")
    return reference


def _candidate_source_index(
    manifest: Mapping[str, Any],
    candidate_root: Path,
) -> dict[str, AFKHumanTruthCandidateSourceV1]:
    collections = manifest.get("collections")
    if not isinstance(collections, Mapping):
        raise ValueError("candidate collections must be an object")
    result: dict[str, AFKHumanTruthCandidateSourceV1] = {}

    def add_items(
        collection_name: str,
        reference: Mapping[str, Any],
        item_key: str | None,
    ) -> None:
        payload = _verified_collection_file(candidate_root, reference, collection_name)
        raw_items = [payload] if item_key is None else payload.get(item_key)
        if not isinstance(raw_items, list):
            raise ValueError(f"candidate collection has no item list: {collection_name}")
        for index, item in enumerate(raw_items):
            if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
                raise ValueError(f"candidate source item is invalid: {collection_name}[{index}]")
            candidate_id = str(item["id"])
            if candidate_id in result:
                raise ValueError(f"duplicate candidate source id: {candidate_id}")
            pointer = "" if item_key is None else f"/{item_key}/{index}"
            result[candidate_id] = AFKHumanTruthCandidateSourceV1(
                path=str(reference["path"]),
                sha256=str(reference["sha256"]),
                json_pointer=pointer,
            )

    add_items("states", _collection_reference(collections, "states"), "items")
    edge_reference = _collection_reference(collections, "edges")
    add_items("safe edges", edge_reference, "safe_edges")
    add_items("excluded edges", edge_reference, "excluded_edges")
    add_items("unresolved edges", edge_reference, "unresolved_edges")
    add_items("objects", _collection_reference(collections, "objects"), "items")
    add_items("boundaries", _collection_reference(collections, "boundaries"), "items")

    for collection_name in ("routes", "interruptions"):
        references = collections.get(collection_name)
        if not isinstance(references, list):
            raise ValueError(f"candidate collection reference is missing: {collection_name}")
        for index, reference in enumerate(references):
            if not isinstance(reference, Mapping):
                raise ValueError(f"candidate file reference is invalid: {collection_name}[{index}]")
            add_items(f"{collection_name}[{index}]", reference, None)
    return result


def build_afk_human_truth_review_skeleton(
    candidate_manifest_path: Path,
    output_path: Path,
) -> Path:
    """Write one immutable, unsigned review skeleton for every AFK v4 candidate."""

    candidate_manifest_path = candidate_manifest_path.resolve()
    output_path = output_path.resolve()
    candidate_manifest_sha256 = _sha256_file(candidate_manifest_path)
    manifest, inventory = _candidate_inventory(
        candidate_manifest_path,
        candidate_manifest_sha256,
    )
    sources = _candidate_source_index(manifest, candidate_manifest_path.parent)
    if set(sources) != set(inventory):
        missing = sorted(set(inventory) - set(sources))
        extra = sorted(set(sources) - set(inventory))
        raise ValueError(f"candidate source coverage mismatch: missing={missing}, extra={extra}")

    counts = {collection: 0 for collection in COLLECTION_ORDER}
    for collection, _candidate in inventory.values():
        counts[collection] += 1
    if counts != EXPECTED_COUNTS:
        raise ValueError(f"AFK v4 candidate count drift: expected={EXPECTED_COUNTS}, actual={counts}")

    collection_rank = {collection: index for index, collection in enumerate(COLLECTION_ORDER)}
    pending_items = []
    for candidate_id, (collection, candidate) in sorted(
        inventory.items(),
        key=lambda item: (collection_rank[item[1][0]], item[0]),
    ):
        evidence_refs = candidate.get("evidence_refs")
        if not isinstance(evidence_refs, list) or not evidence_refs:
            raise ValueError(f"candidate has no evidence for human review: {candidate_id}")
        pending_items.append(
            AFKHumanTruthPendingDecisionV1(
                collection=collection,
                candidate_id=candidate_id,
                candidate_hash=str(candidate["candidate_hash"]),
                candidate_source=sources[candidate_id],
                candidate=dict(candidate),
                evidence_refs=tuple(dict(reference) for reference in evidence_refs),
                decision=None,
                human_reason="",
            ).model_dump(mode="json")
        )

    body: dict[str, Any] = {
        "schema": SKELETON_SCHEMA,
        "id": f"afk-human-truth-review-skeleton.{candidate_manifest_sha256[:16]}",
        "benchmark_id": BENCHMARK_ID,
        "candidate_manifest_id": CANDIDATE_MANIFEST_ID,
        "candidate_manifest": {
            "path": candidate_manifest_path.name,
            "sha256": candidate_manifest_sha256,
        },
        "target_review_schema": REVIEW_SCHEMA,
        "target_attestation_schema": ATTESTATION_SCHEMA,
        "review_status": REVIEW_STATUS,
        "reviewer": None,
        "reviewed_at": None,
        "item_count": EXPECTED_ITEM_COUNT,
        "counts_by_collection": counts,
        "items": pending_items,
    }
    body["package_hash"] = _sha256_bytes(_canonical_bytes(body))
    package = AFKHumanTruthReviewSkeletonV1.model_validate(body)
    serialized = package.model_dump(mode="json", by_alias=True)
    claimed_hash = serialized.pop("package_hash")
    if claimed_hash != _sha256_bytes(_canonical_bytes(serialized)):
        raise ValueError("review skeleton package hash is not canonical")
    serialized["package_hash"] = claimed_hash
    _immutable_write(output_path, _json_bytes(serialized))
    return output_path


__all__ = [
    "AFKHumanTruthCandidateSourceV1",
    "AFKHumanTruthPendingDecisionV1",
    "AFKHumanTruthReviewSkeletonV1",
    "EXPECTED_COUNTS",
    "EXPECTED_ITEM_COUNT",
    "REVIEW_STATUS",
    "SKELETON_SCHEMA",
    "build_afk_human_truth_review_skeleton",
]
