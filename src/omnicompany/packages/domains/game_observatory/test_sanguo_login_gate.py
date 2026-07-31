from __future__ import annotations

import hashlib
import json
from pathlib import Path

from omnicompany.packages.domains.game_observatory.ai_player.sanguo_login_gate import (
    BUILD_SCOPE_ID,
    EDGE_SPECS,
    ENVIRONMENT_ID,
    RESEARCH_RECORD_ID,
    STATE_SPECS,
    build_login_gate_fixture,
    seed_login_gate_fixture,
)
from omnicompany.packages.domains.game_observatory.ai_player.state_graph import (
    VerifiedStateGraph,
)
from omnicompany.packages.domains.game_observatory.ai_player.store import AIPlayerStore
from omnicompany.packages.domains.game_observatory.models import (
    ArtifactRef,
    EvidenceRun,
    EvidenceStep,
    NormalizedAction,
    SourceSnapshot,
)
from omnicompany.packages.domains.game_observatory.store import ObservatoryStore


IDENTITY_RUN_ID = "evidence.run.19651f10f47a4c04bc5b85100f9b21e3"
FIXED_TIME = "2026-07-15T21:00:00+08:00"


def _save_artifact(store: ObservatoryStore, artifact_id: str) -> None:
    raw = f"fixture:{artifact_id}".encode()
    path = store.artifact_root / f"{artifact_id}.png"
    path.write_bytes(raw)
    store.save_artifact(
        ArtifactRef(
            id=artifact_id,
            kind="screenshot",
            path=str(path),
            sha256=hashlib.sha256(raw).hexdigest(),
        )
    )


def _seed_login_gate_evidence(store: ObservatoryStore) -> None:
    state_artifact_by_step = {spec["step_id"]: spec["artifact_id"] for spec in STATE_SPECS}
    for edge in EDGE_SPECS:
        before_id = f"artifact.before.{edge['step_id']}"
        after_id = state_artifact_by_step.get(
            edge["step_id"],
            f"artifact.after.{edge['step_id']}",
        )
        artifact_ids = list(
            dict.fromkeys([before_id, after_id, *([state_artifact_by_step[edge["step_id"]]] if edge["step_id"] in state_artifact_by_step else [])])
        )
        for artifact_id in artifact_ids:
            if store.get_artifact(artifact_id) is None:
                _save_artifact(store, artifact_id)
        store.save_evidence_run(
            EvidenceRun(
                id=edge["run_id"],
                target_id="device://adb/127.0.0.1:16384",
                adapter="fixture",
                status="passed",
                game_id="nslg",
                build_scope_id=BUILD_SCOPE_ID,
                viewport_width=1920,
                viewport_height=1080,
                orientation="landscape",
                started_at=FIXED_TIME,
                ended_at=FIXED_TIME,
                step_ids=[edge["step_id"]],
                artifact_ids=artifact_ids,
            )
        )
        store.save_evidence_step(
            EvidenceStep(
                id=edge["step_id"],
                evidence_run_id=edge["run_id"],
                step_index=1,
                status="passed",
                started_at=FIXED_TIME,
                ended_at=FIXED_TIME,
                before_frame_id=before_id,
                after_frame_id=after_id,
                action=NormalizedAction(type="wait", seconds=0),
                viewport_width=1920,
                viewport_height=1080,
                artifact_ids=artifact_ids,
            )
        )
    store.save_evidence_run(
        EvidenceRun(
            id=IDENTITY_RUN_ID,
            target_id="device.mumu15.local.canonical-16384",
            adapter="fixture",
            status="passed",
            game_id="nslg",
            build_scope_id=BUILD_SCOPE_ID,
            viewport_width=1920,
            viewport_height=1080,
            orientation="landscape",
            started_at=FIXED_TIME,
            ended_at=FIXED_TIME,
        )
    )
    store.save_source_snapshot(
        SourceSnapshot(
            id="snapshot.sanguo.guide.fixture",
            source_id=RESEARCH_RECORD_ID,
            content_sha256="0" * 64,
            captured_at=FIXED_TIME,
        )
    )


def test_sanguo_login_gate_clean_seed_is_evidenced_idempotent_and_navigable(tmp_path):
    root = tmp_path / "observatory"
    store = ObservatoryStore(root)
    _seed_login_gate_evidence(store)
    fixture_path = tmp_path / "sanguo-login-gate.json"
    fixture_path.write_text(
        json.dumps(build_login_gate_fixture(store), ensure_ascii=False, indent=2) + "\
",
        encoding="utf-8",
    )
    guide_seed = (
        Path(__file__).resolve().parents[3]
        / "data/domains/game_observatory/benchmarks/ai_player/guides/sanguo_guide_seed.v1.json"
    )

    first = seed_login_gate_fixture(root, fixture_path, guide_seed)
    second = seed_login_gate_fixture(root, fixture_path, guide_seed)

    assert first == second
    assert first.persistence_reopen_verified is True
    assert (first.state_count, first.transition_count, first.guide_count) == (4, 6, 14)
    player = AIPlayerStore(ObservatoryStore(root))
    environment = player.get_environment(ENVIRONMENT_ID)
    assert environment is not None
    assert environment.game_id_aliases == ["nslg"]
    assert len(player.list_state_observations(ENVIRONMENT_ID)) == 4
    assert len(player.list_state_assignments(ENVIRONMENT_ID)) == 4
    assert all(
        ObservatoryStore(root).get_artifact(artifact_id).metadata["environment_id"]
        == ENVIRONMENT_ID
        for artifact_id in {
            state["artifact_id"] for state in build_login_gate_fixture(store)["states"]
        }
    )
    graph = VerifiedStateGraph.from_store(player, ENVIRONMENT_ID)
    route = graph.shortest_route(
        "state.sanguo.prelogin.local-login",
        "state.sanguo.prelogin.settings",
    )
    assert [edge.id for edge in route] == ["edge.sanguo.prelogin.login-to-settings"]
    blocker = player.get_memory(
        ENVIRONMENT_ID,
        "memory.sanguo.prelogin.external-qr-blocker.v1",
    )
    assert blocker is not None
    assert blocker.payload["user_gameplay_actions"] == 0