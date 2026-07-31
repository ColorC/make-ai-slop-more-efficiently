from __future__ import annotations

import json
from pathlib import Path

from omnicompany.packages.domains.game_observatory.ai_player.route_replay import (
    assess_candidate_route_replay,
)
from omnicompany.packages.domains.game_observatory.evidence_route import (
    EvidenceRoute,
    EvidenceRouteRunner,
    EvidenceRouteStep,
)
from omnicompany.packages.domains.game_observatory.gateway import DeviceGateway
from omnicompany.packages.domains.game_observatory.models import (
    NormalizedAction,
    SourcePixelRect,
    TargetRecord,
)
from omnicompany.packages.domains.game_observatory.store import ObservatoryStore
from tests.domains.game_observatory.test_evidence_recorder import FakeEvidenceAdapter


def fixture_replay(tmp_path: Path, monkeypatch):
    store = ObservatoryStore(tmp_path / "store")
    target = TargetRecord(
        id="device://fake/route-assessment",
        provider="route-assessment",
        endpoint="route-assessment",
        kind="adb",
        label="Route assessment fixture",
        status="online",
        capabilities=["pixel", "touch", "screenrecord"],
        metadata={"serial": "route-assessment"},
    )

    class Provider:
        name = "route-assessment"

        @staticmethod
        def discover():
            return [target]

    gateway = DeviceGateway(store, [Provider()])
    gateway.refresh()
    lease = gateway.acquire(target.id, "route-assessment", ttl_seconds=300)
    monkeypatch.setattr(
        gateway,
        "_adb_adapter",
        lambda _target_id: FakeEvidenceAdapter(store, tmp_path),
    )
    candidate = {
        "id": "route.fixture.hero-to-overlay",
        "start_state_id": "state.hero",
        "goal_state_id": "state.overlay",
        "interaction_ids": ["interaction.open-overlay"],
        "action_budget": 2,
    }
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    route = EvidenceRoute(
        id=candidate["id"],
        title="打开角色信息摘要",
        target_id=target.id,
        viewport_width=100,
        viewport_height=200,
        game_id="afk-journey",
        build_scope_id="build.afk.fixture",
        scope_id="scope.afk.fixture",
        start_state=candidate["start_state_id"],
        end_state=candidate["goal_state_id"],
        excluded_actions=["purchase", "resource_spend"],
        steps=[
            EvidenceRouteStep(
                id=candidate["interaction_ids"][0],
                action=NormalizedAction(type="tap", x=25, y=50),
                target_name="角色信息摘要入口",
                target_bounds=SourcePixelRect(x=10, y=20, width=50, height=60),
                settle_timeout_seconds=1,
                sample_interval_seconds=0.05,
                required_consecutive=2,
            )
        ],
    )
    result = EvidenceRouteRunner(store, gateway).run(route, lease.token)
    return store, candidate_path, Path(result["verification_path"])


def test_route_execution_evidence_is_checked_without_claiming_semantic_truth(
    tmp_path, monkeypatch
):
    store, candidate_path, verification_path = fixture_replay(tmp_path, monkeypatch)

    assessment = assess_candidate_route_replay(
        candidate_path,
        verification_path,
        store,
        expected_game_id="afk-journey",
        expected_build_scope_id="build.afk.fixture",
    )

    assert assessment.execution_evidence_pass is True
    assert assessment.issues == []
    assert assessment.semantic_goal_status == "unadjudicated"
    assert assessment.replay_can_be_frozen is False
    assert len(assessment.evidence_run_ids) == 1
    assert len(assessment.evidence_step_ids) == 1


def test_route_assessment_rejects_id_drift_and_corrupted_target_text(tmp_path, monkeypatch):
    store, candidate_path, verification_path = fixture_replay(tmp_path, monkeypatch)
    payload = json.loads(verification_path.read_text(encoding="utf-8"))
    payload["route"]["id"] = "route.foreign"
    payload["route"]["steps"][0]["target_name"] = "????1"
    verification_path.write_text(json.dumps(payload), encoding="utf-8")

    assessment = assess_candidate_route_replay(candidate_path, verification_path, store)

    assert assessment.execution_evidence_pass is False
    assert any(issue.startswith("route id mismatch") for issue in assessment.issues)
    assert "route target name contains corrupted text: interaction.open-overlay" in assessment.issues
    assert assessment.replay_can_be_frozen is False