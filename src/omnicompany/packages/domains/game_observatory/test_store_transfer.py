from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from omnicompany.packages.domains.game_observatory.models import (
    ArtifactRef,
    EvidenceRun,
)
from omnicompany.packages.domains.game_observatory.store import (
    ObservatoryStore,
    default_observatory_root,
)
from omnicompany.packages.domains.game_observatory.store_transfer import (
    transfer_evidence_store,
)


def test_default_store_root_is_cwd_independent_and_overrideable(tmp_path, monkeypatch):
    monkeypatch.delenv("GAME_OBSERVATORY_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    expected = Path(__file__).resolve().parents[3] / "data/domains/game_observatory"
    assert default_observatory_root() == expected

    override = tmp_path / "explicit-store"
    monkeypatch.setenv("GAME_OBSERVATORY_ROOT", str(override))
    assert default_observatory_root() == override.resolve()


def test_store_transfer_copies_and_reverifies_artifacts_and_evidence(tmp_path):
    source = ObservatoryStore(tmp_path / "source")
    destination_root = tmp_path / "destination"
    raw = b"transfer fixture"
    artifact_path = source.artifact_root / "fixture.png"
    artifact_path.write_bytes(raw)
    artifact = ArtifactRef(
        id="artifact.transfer.fixture",
        kind="screenshot",
        path=str(artifact_path),
        sha256=hashlib.sha256(raw).hexdigest(),
        metadata={"environment_id": "environment.transfer"},
    )
    source.save_artifact(artifact)
    run = EvidenceRun(
        id="evidence.run.transfer",
        target_id="device://fixture",
        adapter="fixture",
        status="failed",
        game_id="fixture-game",
        build_scope_id="build.fixture",
        viewport_width=100,
        viewport_height=200,
        orientation="portrait",
        environment={"environment_id": "environment.transfer"},
        artifact_ids=[artifact.id],
        error="intentional fixture failure",
    )
    source.save_evidence_run(run)

    preview = transfer_evidence_store(source.root, destination_root, dry_run=True)
    assert preview.verification_pass is True
    assert preview.copied_artifacts == 1
    assert not (destination_root / "artifacts" / artifact_path.name).is_file()

    result = transfer_evidence_store(source.root, destination_root)
    assert result.verification_pass is True
    assert result.inserted_evidence_runs == 1
    destination = ObservatoryStore(destination_root)
    stored_artifact = destination.get_artifact(artifact.id)
    assert stored_artifact is not None
    assert Path(stored_artifact.path).is_relative_to(destination_root)
    assert destination.get_evidence_run(run.id) == run

    repeated = transfer_evidence_store(source.root, destination_root)
    assert repeated.verification_pass is True
    assert repeated.inserted_evidence_runs == 0
    assert repeated.reused_artifacts == 1


def test_store_transfer_fails_before_write_on_hash_or_id_conflict(tmp_path):
    source = ObservatoryStore(tmp_path / "source")
    destination = ObservatoryStore(tmp_path / "destination")
    source_path = source.artifact_root / "fixture.bin"
    source_path.write_bytes(b"source")
    source.save_artifact(
        ArtifactRef(
            id="artifact.conflict",
            kind="screenshot",
            path=str(source_path),
            sha256=hashlib.sha256(b"source").hexdigest(),
        )
    )
    destination_path = destination.artifact_root / "other.bin"
    destination_path.write_bytes(b"destination")
    destination.save_artifact(
        ArtifactRef(
            id="artifact.conflict",
            kind="screenshot",
            path=str(destination_path),
            sha256=hashlib.sha256(b"destination").hexdigest(),
        )
    )

    with pytest.raises(ValueError, match="different content"):
        transfer_evidence_store(source.root, destination.root)
    assert destination.list_evidence_runs() == []