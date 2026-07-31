"""Ingest an evidence-backed AI-player checkpoint from a strict JSON seed.

The seed is declarative: memory payloads, task descriptions, and capsule summaries
must be authored explicitly.  This module only validates references and persists the
declared checkpoint; it never infers gameplay mechanics from evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..store import ObservatoryStore
from .contracts import (
    EnvironmentScopeV1,
    EvidenceReferenceV1,
    FrontierTaskV1,
    MemoryRecordV1,
    SessionCapsuleV1,
)
from .store import AIPlayerStore


class EvidenceMilestoneSeedV1(BaseModel):
    """One immutable environment checkpoint and its explicitly authored state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["game-observatory.ai-player.evidence-milestone-seed.v1"] = Field(
        default="game-observatory.ai-player.evidence-milestone-seed.v1",
        alias="schema",
    )
    seed_id: str = Field(min_length=1)
    environment: EnvironmentScopeV1
    memories: tuple[MemoryRecordV1, ...] = ()
    frontier_tasks: tuple[FrontierTaskV1, ...] = ()
    session_capsules: tuple[SessionCapsuleV1, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def require_explicit_timestamps(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        required_groups = {
            "environment": [value.get("environment")],
            "memories": value.get("memories", []),
            "frontier_tasks": value.get("frontier_tasks", []),
            "session_capsules": value.get("session_capsules", []),
        }
        for group, items in required_groups.items():
            for index, item in enumerate(items):
                created_at = (
                    item.get("created_at")
                    if isinstance(item, dict)
                    else getattr(item, "created_at", None)
                )
                if not created_at:
                    raise ValueError(f"{group}[{index}] requires explicit created_at")
                pending = (
                    item.get("pending_action")
                    if isinstance(item, dict)
                    else getattr(item, "pending_action", None)
                )
                if pending is not None and (
                    not (
                        pending.get("issued_at")
                        if isinstance(pending, dict)
                        else getattr(pending, "issued_at", None)
                    )
                ):
                    raise ValueError(
                        f"{group}[{index}].pending_action requires explicit issued_at"
                    )
        return value

    @model_validator(mode="after")
    def keep_checkpoint_consistent(self) -> "EvidenceMilestoneSeedV1":
        environment_id = self.environment.id
        groups: tuple[tuple[str, Iterable[Any]], ...] = (
            ("memory", self.memories),
            ("frontier task", self.frontier_tasks),
            ("session capsule", self.session_capsules),
        )
        for label, entities in groups:
            if any(entity.environment_id != environment_id for entity in entities):
                raise ValueError(f"{label} environment does not match checkpoint environment")
        for label, ids in (
            ("memory ids", [item.id for item in self.memories]),
            ("frontier task ids", [item.id for item in self.frontier_tasks]),
            ("session capsule ids", [item.id for item in self.session_capsules]),
            (
                "session capsule sequences",
                [(item.session_id, item.sequence) for item in self.session_capsules],
            ),
        ):
            if len(ids) != len(set(ids)):
                raise ValueError(f"{label} must be unique")
        if any(task.status != "queued" for task in self.frontier_tasks):
            raise ValueError("checkpoint frontier tasks must be newly queued")
        return self


class EvidenceMilestoneSeedResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal[
        "game-observatory.ai-player.evidence-milestone-seed-result.v1"
    ] = Field(
        default="game-observatory.ai-player.evidence-milestone-seed-result.v1",
        alias="schema",
    )
    seed_id: str
    seed_sha256: str = Field(min_length=64, max_length=64)
    environment_id: str
    environment_count: Literal[1] = 1
    memory_count: int = Field(ge=0)
    frontier_task_count: int = Field(ge=0)
    session_capsule_count: int = Field(ge=0)
    inserted_environment_count: int = Field(ge=0, le=1)
    inserted_memory_count: int = Field(ge=0)
    inserted_frontier_task_count: int = Field(ge=0)
    inserted_session_capsule_count: int = Field(ge=0)
    persistence_reopen_verified: bool


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _all_references(seed: EvidenceMilestoneSeedV1) -> list[EvidenceReferenceV1]:
    references = list(seed.environment.evidence_refs)
    for entity in (*seed.memories, *seed.frontier_tasks, *seed.session_capsules):
        references.extend(entity.evidence_refs)
    for capsule in seed.session_capsules:
        if capsule.pending_action is not None:
            references.extend(capsule.pending_action.evidence_refs)
            references.extend(capsule.pending_action.after_evidence_refs)
    return references


def _reject_unsupported_trace_references(
    seed: EvidenceMilestoneSeedV1,
    references: Sequence[EvidenceReferenceV1],
) -> None:
    if any(reference.trace_run_ids for reference in references):
        raise ValueError(
            "checkpoint seeds only accept Artifact, EvidenceRun, EvidenceStep, and Source"
        )
    if any(
        capsule.pending_action is not None
        and capsule.pending_action.action_run_id is not None
        for capsule in seed.session_capsules
    ):
        raise ValueError("checkpoint seeds cannot reference trace action runs")


def ingest_evidence_milestone_seed(
    store_root: Path,
    seed_path: Path,
    *,
    expected_seed_sha256: str,
) -> EvidenceMilestoneSeedResultV1:
    """Validate, ingest, close, reopen, and verify one checkpoint seed."""

    if len(expected_seed_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in expected_seed_sha256.lower()
    ):
        raise ValueError("expected seed SHA-256 must be 64 hexadecimal characters")
    seed_bytes = seed_path.read_bytes()
    seed_sha256 = _sha256_bytes(seed_bytes)
    if seed_sha256 != expected_seed_sha256.lower():
        raise ValueError("checkpoint seed SHA-256 mismatch")
    seed = EvidenceMilestoneSeedV1.model_validate_json(seed_bytes)

    observatory = ObservatoryStore(store_root)
    player = AIPlayerStore(observatory)
    references = _all_references(seed)
    _reject_unsupported_trace_references(seed, references)
    inserted = player.apply_evidence_milestone(
        seed.environment,
        seed.memories,
        seed.frontier_tasks,
        seed.session_capsules,
    )

    reopened_observatory = ObservatoryStore(store_root)
    reopened = AIPlayerStore(reopened_observatory)
    persistence_verified = (
        _sha256_bytes(seed_path.read_bytes()) == seed_sha256
        and reopened.get_environment(seed.environment.id) == seed.environment
        and all(
            reopened.get_memory(seed.environment.id, item.id) == item
            for item in seed.memories
        )
        and all(
            reopened.get_task(seed.environment.id, item.id) == item
            for item in seed.frontier_tasks
        )
        and all(
            reopened.get_session_capsule(seed.environment.id, item.id) == item
            for item in seed.session_capsules
        )
    )
    reopened.resolve_evidence_references(references)
    reopened.validate_terminal_evidence_references(references)
    return EvidenceMilestoneSeedResultV1(
        seed_id=seed.seed_id,
        seed_sha256=seed_sha256,
        environment_id=seed.environment.id,
        memory_count=len(seed.memories),
        frontier_task_count=len(seed.frontier_tasks),
        session_capsule_count=len(seed.session_capsules),
        inserted_environment_count=inserted["inserted_environment_count"],
        inserted_memory_count=inserted["inserted_memory_count"],
        inserted_frontier_task_count=inserted["inserted_frontier_task_count"],
        inserted_session_capsule_count=inserted["inserted_session_capsule_count"],
        persistence_reopen_verified=persistence_verified,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store-root", type=Path, required=True)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--expected-seed-sha256", required=True)
    parser.add_argument("--result", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = ingest_evidence_milestone_seed(
        args.store_root,
        args.seed,
        expected_seed_sha256=args.expected_seed_sha256,
    )
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(
        json.dumps(
            result.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return int(not result.persistence_reopen_verified)


if __name__ == "__main__":
    raise SystemExit(main())
