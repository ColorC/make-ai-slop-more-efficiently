from __future__ import annotations

import hashlib
import json

import pytest

from omnicompany.packages.domains.game_observatory.ai_player.contracts import (
    EnvironmentScopeV1,
    EvidenceReferenceV1,
    GuideKnowledgeV1,
    MemoryRecordV1,
)
from omnicompany.packages.domains.game_observatory.ai_player.knowledge_memory_seed import (
    KnowledgeMemorySeedV1,
    LocalSourceSeedV1,
    ingest_knowledge_memory_seed,
)
from omnicompany.packages.domains.game_observatory.ai_player.store import AIPlayerStore
from omnicompany.packages.domains.game_observatory.models import ArtifactRef, SourceSnapshot
from omnicompany.packages.domains.game_observatory.store import ObservatoryStore


ENVIRONMENT_ID = "environment.knowledge-seed.fixture"
CAPTURED_AT = "2026-07-15T16:52:58+00:00"


def _build_seed(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source_path = workspace / "sources" / "guide.md"
    source_path.parent.mkdir()
    source_path.write_text("sourced guide\
", encoding="utf-8")
    source_id = "research:guide.fixture"
    snapshot = SourceSnapshot(
        id="snapshot.guide.fixture",
        source_id=source_id,
        content_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
        locator="sources/guide.md",
        excerpt="fixture source",
        captured_at=CAPTURED_AT,
    )
    refs = [EvidenceReferenceV1(environment_id=ENVIRONMENT_ID, source_ids=[source_id])]
    seed = KnowledgeMemorySeedV1(
        environment_id=ENVIRONMENT_ID,
        sources=[LocalSourceSeedV1(local_path="sources/guide.md", snapshot=snapshot)],
        guides=[
            GuideKnowledgeV1(
                id="guide.fixture",
                environment_id=ENVIRONMENT_ID,
                url="https://example.com/guide",
                platform="fixture",
                author="fixture author",
                updated_at="2026-07-15",
                retrieved_at=CAPTURED_AT,
                summary="fixture summary",
                locators=["fixture section"],
                status="unverified",
                missing_applicability_reason="fixture environment has no live season",
                evidence_refs=refs,
                created_at=CAPTURED_AT,
            )
        ],
        memories=[
            MemoryRecordV1(
                id="memory.fixture",
                environment_id=ENVIRONMENT_ID,
                kind="episodic",
                subject_id="decision.fixture",
                payload={"decision": "fixture"},
                evidence_refs=refs,
                created_at=CAPTURED_AT,
            )
        ],
    )
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(
        json.dumps(seed.model_dump(mode="json", by_alias=True), ensure_ascii=False),
        encoding="utf-8",
    )
    return workspace, source_path, seed_path, seed


def _initialize_environment(store_root):
    observatory = ObservatoryStore(store_root)
    raw = b"environment evidence"
    path = observatory.artifact_root / "environment.bin"
    path.write_bytes(raw)
    artifact = ArtifactRef(
        id="artifact.environment.fixture",
        kind="screenshot",
        path=str(path),
        sha256=hashlib.sha256(raw).hexdigest(),
        metadata={"environment_id": ENVIRONMENT_ID},
    )
    observatory.save_artifact(artifact)
    refs = [
        EvidenceReferenceV1(
            environment_id=ENVIRONMENT_ID,
            artifact_ids=[artifact.id],
        )
    ]
    AIPlayerStore(observatory).put_environment(
        EnvironmentScopeV1(
            id=ENVIRONMENT_ID,
            game_id="fixture",
            build_scope_id="build.fixture",
            account_scope_id="account.fixture",
            channel="fixture",
            device_scope_id="device.fixture",
            locale="zh-CN",
            viewport_width=1920,
            viewport_height=1080,
            identity_hash="identity-hash-knowledge-seed-fixture",
            evidence_refs=refs,
            created_at=CAPTURED_AT,
        )
    )


def test_sourced_knowledge_and_memory_seed_is_idempotent_and_reopen_verified(tmp_path):
    store_root = tmp_path / "store"
    _initialize_environment(store_root)
    workspace, source_path, seed_path, seed = _build_seed(tmp_path)

    first = ingest_knowledge_memory_seed(
        store_root,
        seed_path,
        workspace_root=workspace,
    )
    second = ingest_knowledge_memory_seed(
        store_root,
        seed_path,
        workspace_root=workspace,
    )

    assert first.persistence_reopen_verified is True
    assert first.inserted_source_snapshot_count == 1
    assert first.inserted_guide_count == 1
    assert first.inserted_memory_count == 1
    assert second.inserted_source_snapshot_count == 0
    assert second.inserted_guide_count == 0
    assert second.inserted_memory_count == 0
    player = AIPlayerStore(ObservatoryStore(store_root))
    assert player.get_guide_knowledge(ENVIRONMENT_ID, seed.guides[0].id) == seed.guides[0]
    assert player.get_memory(ENVIRONMENT_ID, seed.memories[0].id) == seed.memories[0]

    source_path.write_text("tampered\
", encoding="utf-8")
    with pytest.raises(ValueError, match="source hash mismatch"):
        ingest_knowledge_memory_seed(
            store_root,
            seed_path,
            workspace_root=workspace,
        )


def test_seed_rejects_absolute_or_escaping_source_paths_before_writes(tmp_path):
    store_root = tmp_path / "store"
    _initialize_environment(store_root)
    workspace, _source_path, seed_path, _seed = _build_seed(tmp_path)
    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    payload["sources"][0]["local_path"] = "../outside.md"
    payload["sources"][0]["snapshot"]["locator"] = "../outside.md"
    seed_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="escapes workspace"):
        ingest_knowledge_memory_seed(
            store_root,
            seed_path,
            workspace_root=workspace,
        )