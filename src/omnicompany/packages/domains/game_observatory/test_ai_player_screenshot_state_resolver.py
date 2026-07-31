from __future__ import annotations

import hashlib
from pathlib import Path

import cv2
import numpy as np

from omnicompany.packages.domains.game_observatory.ai_player.contracts import (
    EnvironmentScopeV1,
    EvidenceReferenceV1,
    FrontierTaskV1,
)
from omnicompany.packages.domains.game_observatory.ai_player.orchestrator import (
    AutonomousExecutionCommandV1,
    AutonomousExecutorRequestV1,
)
from omnicompany.packages.domains.game_observatory.ai_player.screenshot_state_resolver import (
    ScreenshotStateResolver,
)
from omnicompany.packages.domains.game_observatory.ai_player.store import AIPlayerStore
from omnicompany.packages.domains.game_observatory.models import (
    ArtifactRef,
    EvidenceRun,
    EvidenceStability,
    EvidenceStep,
    NormalizedAction,
    RunResult,
)
from omnicompany.packages.domains.game_observatory.store import ObservatoryStore


ENVIRONMENT_ID = "environment.fixture.screenshot-resolver"
RUN_ID = "evidence.run.screenshot-resolver"
STEP_ID = "evidence.step.screenshot-resolver"
ACTION_RUN_ID = "run.action.screenshot-resolver"


def _png(path: Path, *, changed: bool) -> None:
    image = np.zeros((200, 100, 3), dtype=np.uint8)
    image[:, :, 0] = np.arange(100, dtype=np.uint8)
    image[20:90, 10:70, 1] = 170 if changed else 60
    assert cv2.imwrite(str(path), image)


def _artifact(
    store: ObservatoryStore,
    artifact_id: str,
    *,
    changed: bool,
    evidence: bool,
) -> ArtifactRef:
    path = store.artifact_root / f"{artifact_id}.png"
    _png(path, changed=changed)
    artifact = ArtifactRef(
        id=artifact_id,
        kind="screenshot",
        path=str(path),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        run_id=RUN_ID if evidence else None,
        media_type="image/png",
        metadata={
            "environment_id": ENVIRONMENT_ID,
            **(
                {
                    "evidence_run_id": RUN_ID,
                    "evidence_step_id": STEP_ID,
                    "semantic_state_eligible": True,
                }
                if evidence
                else {}
            ),
        },
    )
    store.save_artifact(artifact)
    return artifact


def test_screenshot_state_resolver_persists_reopenable_before_after_states(tmp_path):
    store = ObservatoryStore(tmp_path / "observatory")
    identity = _artifact(store, "artifact.identity", changed=False, evidence=False)
    player = AIPlayerStore(store)
    player.put_environment(
        EnvironmentScopeV1(
            id=ENVIRONMENT_ID,
            game_id="game.fixture",
            build_scope_id="build.fixture",
            account_scope_id="account.fixture",
            channel="fixture",
            device_scope_id="device.fixture",
            locale="zh-CN",
            viewport_width=100,
            viewport_height=200,
            identity_hash="a" * 64,
            evidence_refs=[
                EvidenceReferenceV1(
                    environment_id=ENVIRONMENT_ID,
                    artifact_ids=[identity.id],
                )
            ],
        )
    )
    before = _artifact(store, "artifact.before", changed=False, evidence=True)
    after = _artifact(store, "artifact.after", changed=True, evidence=True)
    action = NormalizedAction(type="wait", seconds=0)
    action_run = RunResult(
        id=ACTION_RUN_ID,
        adapter="fixture",
        target_id="device.fixture",
        task_id="task.fixture",
        status="passed",
        ended_at="2026-07-16T00:00:02+00:00",
        artifact_ids=[before.id, after.id],
    )
    step = EvidenceStep(
        id=STEP_ID,
        evidence_run_id=RUN_ID,
        step_index=1,
        status="passed",
        ended_at="2026-07-16T00:00:02+00:00",
        before_frame_id=before.id,
        action=action,
        action_run_id=action_run.id,
        after_frame_id=after.id,
        artifact_ids=[before.id, after.id],
        viewport_width=100,
        viewport_height=200,
        stability=EvidenceStability(
            observed_consecutive=2,
            sampled_frames=3,
            settled=True,
        ),
        metadata={"capture_profile": "compact_static"},
    )
    run = EvidenceRun(
        id=RUN_ID,
        target_id="device.fixture",
        adapter="fixture",
        game_id="game.fixture",
        build_scope_id="build.fixture",
        scope_id=ENVIRONMENT_ID,
        status="passed",
        ended_at="2026-07-16T00:00:02+00:00",
        viewport_width=100,
        viewport_height=200,
        orientation="portrait",
        environment={"environment_id": ENVIRONMENT_ID},
        step_ids=[step.id],
        artifact_ids=[before.id, after.id],
        action_run_ids=[action_run.id],
    )
    store.save_run(action_run)
    store.save_evidence_step(step)
    store.save_evidence_run(run)
    task = FrontierTaskV1(
        id="task.fixture",
        environment_id=ENVIRONMENT_ID,
        title="Observe a screenshot transition",
        source="coverage_gap",
        reason="Verify production screenshot state resolution.",
        action_budget=1,
        time_budget_seconds=60,
        evidence_refs=[
            EvidenceReferenceV1(
                environment_id=ENVIRONMENT_ID,
                artifact_ids=[identity.id],
            )
        ],
    )
    request = AutonomousExecutorRequestV1(
        command=AutonomousExecutionCommandV1(
            command_id="command.fixture",
            environment_id=ENVIRONMENT_ID,
            session_id="session.fixture",
            expected_identity_hash="a" * 64,
            intent="Observe one screenshot transition",
            action=action,
        ),
        task=task,
        pending_capsule_id="capsule.fixture",
        selection_reason="fixture",
        instruction="fixture",
    )

    result = ScreenshotStateResolver(player).resolve(
        request=request,
        evidence_run=run,
        evidence_step=step,
        artifacts=[before, after],
        action_run=action_run,
    )

    assert result.before_state.id != result.after_state.id
    assert len(player.list_state_observations(ENVIRONMENT_ID)) == 2
    reopened = AIPlayerStore(ObservatoryStore(store.root))
    assert reopened.get_semantic_state(ENVIRONMENT_ID, result.before_state.id) is not None
    assert reopened.get_semantic_state(ENVIRONMENT_ID, result.after_state.id) is not None