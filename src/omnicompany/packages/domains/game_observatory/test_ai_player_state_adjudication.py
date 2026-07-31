from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from omnicompany.packages.domains.game_observatory.ai_player.live_evidence_state_ingest import (
    ingest_live_evidence_state_seed,
)
from omnicompany.packages.domains.game_observatory.ai_player.state_adjudication import (
    StateAdjudicationDecisionV1,
    StateAdjudicationSeedV1,
    TransitionAdjudicationDecisionV1,
    apply_state_adjudication_seed,
    export_state_review_packet,
)
from omnicompany.packages.domains.game_observatory.ai_player.store import AIPlayerStore
from omnicompany.packages.domains.game_observatory.store import ObservatoryStore
from tests.domains.game_observatory.test_ai_player_live_evidence_state_ingest import (
    ENDED_AT,
    ENVIRONMENT_ID,
    _fixture_store,
    _seed,
    _write_seed,
)


def _prepare_packet(tmp_path: Path):
    store_root = tmp_path / "store"
    _observatory, player, _artifacts = _fixture_store(store_root)
    ingest_seed_path = tmp_path / "ingest.json"
    ingest_seed_sha256 = _write_seed(ingest_seed_path, _seed())
    ingest_live_evidence_state_seed(
        store_root,
        ingest_seed_path,
        expected_store_root=store_root,
        expected_seed_sha256=ingest_seed_sha256,
    )
    packet_path = tmp_path / "review-packet.json"
    packet, packet_sha256 = export_state_review_packet(
        player,
        ENVIRONMENT_ID,
        packet_path,
    )
    return store_root, player, packet_path, packet, packet_sha256


def _decision_seed(packet, packet_sha256: str) -> StateAdjudicationSeedV1:
    state_decisions = []
    for index, item in enumerate(packet.states, start=1):
        state_decisions.append(
            StateAdjudicationDecisionV1(
                state_id=item.state.id,
                expected_version=item.state.version,
                expected_state_sha256=item.state_sha256,
                disposition="accepted",
                title=f"已审定界面 {index}",
                description=f"独立审查全部观察后确认的语义状态 {index}。",
                tags=("fixture-reviewed",),
                reviewed_observation_ids=tuple(
                    assignment.observation_id for assignment in item.assignments
                ),
                rationale="Before/After 图像与稳定界面文字证明该状态边界。",
            )
        )
    edge_decisions = []
    for item in packet.edges:
        edge_decisions.append(
            TransitionAdjudicationDecisionV1(
                edge_id=item.edge.id,
                expected_version=item.edge.version,
                expected_edge_sha256=item.edge_sha256,
                outcome="verified_transition",
                observed_change="点击征兵入口后从主城进入征兵界面。",
                reviewed_evidence_step_ids=tuple(
                    dict.fromkeys(
                        step_id
                        for reference in item.edge.evidence_refs
                        for step_id in reference.evidence_step_ids
                    )
                ),
                rationale="完整 Before/Action/After 证据显示两个已审定状态之间发生变化。",
            )
        )
    return StateAdjudicationSeedV1(
        seed_id="state-adjudication.fixture.v1",
        environment_id=ENVIRONMENT_ID,
        packet_sha256=packet_sha256,
        adjudicator_id="independent-agent.fixture",
        reviewed_at=ENDED_AT,
        state_decisions=tuple(state_decisions),
        transition_decisions=tuple(edge_decisions),
    )


def _write_adjudication_seed(path: Path, seed: StateAdjudicationSeedV1) -> str:
    content = (
        json.dumps(
            seed.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\
"
    ).encode("utf-8")
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def test_review_packet_apply_is_hash_locked_idempotent_and_reopen_verified(tmp_path: Path):
    store_root, _player, packet_path, packet, packet_sha256 = _prepare_packet(tmp_path)
    assert len(packet.states) == 2
    assert len(packet.edges) == 1
    assert all(item.artifacts for item in packet.states)
    assert all(Path(artifact.path).is_file() for item in packet.states for artifact in item.artifacts)

    seed_path = tmp_path / "adjudication.json"
    seed_sha256 = _write_adjudication_seed(
        seed_path,
        _decision_seed(packet, packet_sha256),
    )
    result_path = tmp_path / "adjudication-result.json"
    first = apply_state_adjudication_seed(
        store_root,
        packet_path,
        seed_path,
        result_path,
        expected_store_root=store_root,
        expected_seed_sha256=seed_sha256,
    )
    second = apply_state_adjudication_seed(
        store_root,
        packet_path,
        seed_path,
        result_path,
        expected_store_root=store_root,
        expected_seed_sha256=seed_sha256,
    )

    assert first == second
    assert first.persistence_reopen_verified is True
    assert len(first.state_version_ids) == 2
    assert len(first.transition_version_ids) == 1
    reopened = AIPlayerStore(ObservatoryStore(store_root))
    assert {
        item.status for item in reopened.list_semantic_states(ENVIRONMENT_ID)
    } == {"accepted"}
    edge = reopened.list_transition_edges(ENVIRONMENT_ID)[0]
    assert edge.version == 2
    assert edge.outcome == "verified_transition"
    assert "independently-adjudicated" in reopened.list_semantic_states(ENVIRONMENT_ID)[0].tags
    assert result_path.is_file()


def test_review_apply_rejects_packet_tampering_and_seed_hash_drift(tmp_path: Path):
    store_root, _player, packet_path, packet, packet_sha256 = _prepare_packet(tmp_path)
    seed_path = tmp_path / "adjudication.json"
    seed_sha256 = _write_adjudication_seed(
        seed_path,
        _decision_seed(packet, packet_sha256),
    )
    packet_path.write_bytes(packet_path.read_bytes() + b" ")

    with pytest.raises(ValueError, match="packet SHA-256 mismatch"):
        apply_state_adjudication_seed(
            store_root,
            packet_path,
            seed_path,
            tmp_path / "result.json",
            expected_store_root=store_root,
            expected_seed_sha256=seed_sha256,
        )
    with pytest.raises(ValueError, match="seed SHA-256 mismatch"):
        apply_state_adjudication_seed(
            store_root,
            packet_path,
            seed_path,
            tmp_path / "result.json",
            expected_store_root=store_root,
            expected_seed_sha256="0" * 64,
        )


def test_verified_edge_requires_accepted_endpoints(tmp_path: Path):
    store_root, _player, packet_path, packet, packet_sha256 = _prepare_packet(tmp_path)
    full_seed = _decision_seed(packet, packet_sha256)
    edge_only = full_seed.model_copy(update={"state_decisions": ()})
    seed_path = tmp_path / "edge-only.json"
    seed_sha256 = _write_adjudication_seed(seed_path, edge_only)

    with pytest.raises(ValueError, match="accepted endpoints"):
        apply_state_adjudication_seed(
            store_root,
            packet_path,
            seed_path,
            tmp_path / "result.json",
            expected_store_root=store_root,
            expected_seed_sha256=seed_sha256,
        )