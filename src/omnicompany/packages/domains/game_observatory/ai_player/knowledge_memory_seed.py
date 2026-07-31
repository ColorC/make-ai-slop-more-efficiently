"""Idempotently ingest sourced guide knowledge and AI-player memory."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models import SourceSnapshot
from ..store import ObservatoryStore
from .contracts import GuideKnowledgeV1, MemoryRecordV1
from .store import AIPlayerStore


class LocalSourceSeedV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    local_path: str = Field(min_length=1)
    snapshot: SourceSnapshot


class KnowledgeMemorySeedV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["game-observatory.ai-player.knowledge-memory-seed.v1"] = Field(
        default="game-observatory.ai-player.knowledge-memory-seed.v1",
        alias="schema",
    )
    environment_id: str = Field(min_length=1)
    sources: tuple[LocalSourceSeedV1, ...] = ()
    guides: tuple[GuideKnowledgeV1, ...] = ()
    memories: tuple[MemoryRecordV1, ...] = ()

    @model_validator(mode="after")
    def keep_entities_unique_and_in_environment(self) -> "KnowledgeMemorySeedV1":
        if not (self.sources or self.guides or self.memories):
            raise ValueError("knowledge-memory seed must contain at least one entity")
        source_ids = [item.snapshot.id for item in self.sources]
        guide_keys = [(item.id, item.version) for item in self.guides]
        memory_ids = [item.id for item in self.memories]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source snapshot ids must be unique")
        if len(guide_keys) != len(set(guide_keys)):
            raise ValueError("guide ids and versions must be unique")
        if len(memory_ids) != len(set(memory_ids)):
            raise ValueError("memory ids must be unique")
        if any(item.environment_id != self.environment_id for item in self.guides):
            raise ValueError("guide environment does not match the seed environment")
        if any(item.environment_id != self.environment_id for item in self.memories):
            raise ValueError("memory environment does not match the seed environment")
        return self


class KnowledgeMemorySeedResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["game-observatory.ai-player.knowledge-memory-seed-result.v1"] = Field(
        default="game-observatory.ai-player.knowledge-memory-seed-result.v1",
        alias="schema",
    )
    environment_id: str
    source_snapshot_count: int = Field(ge=0)
    guide_count: int = Field(ge=0)
    memory_count: int = Field(ge=0)
    inserted_source_snapshot_count: int = Field(ge=0)
    inserted_guide_count: int = Field(ge=0)
    inserted_memory_count: int = Field(ge=0)
    persistence_reopen_verified: bool


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _resolved_source_path(local_path: str, workspace_root: Path) -> Path:
    candidate = Path(local_path)
    if candidate.is_absolute():
        raise ValueError(f"seed source path must be workspace-relative: {local_path}")
    resolved = (workspace_root / candidate).resolve()
    if not resolved.is_relative_to(workspace_root.resolve()):
        raise ValueError(f"seed source path escapes workspace: {local_path}")
    return resolved


def ingest_knowledge_memory_seed(
    store_root: Path,
    seed_path: Path,
    *,
    workspace_root: Path | None = None,
) -> KnowledgeMemorySeedResultV1:
    workspace = (workspace_root or Path(__file__).resolve().parents[5]).resolve()
    seed = KnowledgeMemorySeedV1.model_validate_json(seed_path.read_text(encoding="utf-8"))
    observatory = ObservatoryStore(store_root)
    player = AIPlayerStore(observatory)
    if player.get_environment(seed.environment_id) is None:
        raise KeyError(f"unknown AI-player environment: {seed.environment_id}")

    existing_snapshots = {
        item.id: item for item in observatory.list_source_snapshots()
    }
    source_paths: dict[str, Path] = {}
    for source in seed.sources:
        path = _resolved_source_path(source.local_path, workspace)
        if not path.is_file():
            raise FileNotFoundError(f"seed source file is missing: {source.local_path}")
        if source.snapshot.locator != source.local_path:
            raise ValueError(
                f"source snapshot locator must equal local_path: {source.snapshot.id}"
            )
        if _sha256(path) != source.snapshot.content_sha256:
            raise ValueError(f"seed source hash mismatch: {source.snapshot.id}")
        existing = existing_snapshots.get(source.snapshot.id)
        if existing is not None and existing != source.snapshot:
            raise ValueError(f"source snapshot id conflicts: {source.snapshot.id}")
        source_paths[source.snapshot.id] = path

    inserted_sources = inserted_guides = inserted_memories = 0
    for source in seed.sources:
        if source.snapshot.id not in existing_snapshots:
            if not observatory.save_source_snapshot(source.snapshot):
                raise RuntimeError(f"source snapshot insert failed: {source.snapshot.id}")
            inserted_sources += 1
    for guide in seed.guides:
        existing = player.get_guide_knowledge(
            seed.environment_id,
            guide.id,
            version=guide.version,
        )
        if existing is None:
            player.append_guide_knowledge(guide)
            inserted_guides += 1
        elif existing != guide:
            raise ValueError(f"guide knowledge conflicts: {guide.id}@{guide.version}")
    for memory in seed.memories:
        existing = player.get_memory(seed.environment_id, memory.id)
        if existing is None:
            player.append_memory(memory)
            inserted_memories += 1
        elif existing != memory:
            raise ValueError(f"memory record conflicts: {memory.id}")

    reopened_observatory = ObservatoryStore(store_root)
    reopened = AIPlayerStore(reopened_observatory)
    persisted_snapshots = {
        item.id: item for item in reopened_observatory.list_source_snapshots()
    }
    persistence_verified = (
        all(persisted_snapshots.get(item.snapshot.id) == item.snapshot for item in seed.sources)
        and all(
            reopened.get_guide_knowledge(
                seed.environment_id,
                guide.id,
                version=guide.version,
            )
            == guide
            for guide in seed.guides
        )
        and all(
            reopened.get_memory(seed.environment_id, memory.id) == memory
            for memory in seed.memories
        )
        and all(_sha256(source_paths[item.snapshot.id]) == item.snapshot.content_sha256 for item in seed.sources)
    )
    return KnowledgeMemorySeedResultV1(
        environment_id=seed.environment_id,
        source_snapshot_count=len(seed.sources),
        guide_count=len(seed.guides),
        memory_count=len(seed.memories),
        inserted_source_snapshot_count=inserted_sources,
        inserted_guide_count=inserted_guides,
        inserted_memory_count=inserted_memories,
        persistence_reopen_verified=persistence_verified,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store-root", type=Path, required=True)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = ingest_knowledge_memory_seed(args.store_root, args.seed)
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(
        json.dumps(
            result.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            indent=2,
        )
        + "\
",
        encoding="utf-8",
    )
    return int(not result.persistence_reopen_verified)


if __name__ == "__main__":
    raise SystemExit(main())