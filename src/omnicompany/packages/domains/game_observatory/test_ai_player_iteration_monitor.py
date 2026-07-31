from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from omnicompany.packages.domains.game_observatory.ai_player.contracts import (
    ActionQualitySampleV1,
    EnvironmentScopeV1,
    EvidenceReferenceV1,
    FrontierTaskV1,
    PlayerMetricDeltaV1,
    PlayerSoftSignalV1,
    SemanticStateV1,
)
from omnicompany.packages.domains.game_observatory.ai_player.iteration_monitor import (
    PlayerIterationMonitor,
    assess_player_iteration,
)
from omnicompany.packages.domains.game_observatory.ai_player.store import AIPlayerStore
from omnicompany.packages.domains.game_observatory.models import (
    ArtifactRef,
    EvidenceRun,
    EvidenceStep,
    NormalizedAction,
    RunResult,
)
from omnicompany.packages.domains.game_observatory.store import ObservatoryStore


ENVIRONMENT_ID = "environment.iteration.fixture"


def _seed(tmp_path) -> tuple[AIPlayerStore, EvidenceReferenceV1]:
    observatory = ObservatoryStore(tmp_path / "observatory")
    raw = b"iteration-fixture"
    artifact_path = observatory.artifact_root / "iteration.png"
    artifact_path.write_bytes(raw)
    artifact = ArtifactRef(
        id="artifact.iteration.fixture",
        kind="screenshot",
        path=str(artifact_path),
        sha256=hashlib.sha256(raw).hexdigest(),
        metadata={"environment_id": ENVIRONMENT_ID},
    )
    observatory.save_artifact(artifact)
    action_run = RunResult(
        id="run.iteration.fixture",
        adapter="fixture",
        target_id="device.iteration.fixture",
        task_id="task.iteration.fixture",
        status="passed",
        artifact_ids=[artifact.id],
    )
    evidence_run = EvidenceRun(
        id="evidence-run.iteration.fixture",
        target_id="device.iteration.fixture",
        adapter="fixture",
        status="passed",
        game_id="sanguo-mouding-tianxia",
        build_scope_id="build.iteration.fixture",
        scope_id=ENVIRONMENT_ID,
        viewport_width=1920,
        viewport_height=1080,
        orientation="landscape",
        environment={"environment_id": ENVIRONMENT_ID},
        step_ids=["evidence-step.iteration.fixture"],
        artifact_ids=[artifact.id],
    )
    evidence_step = EvidenceStep(
        id="evidence-step.iteration.fixture",
        evidence_run_id=evidence_run.id,
        step_index=1,
        status="passed",
        action=NormalizedAction(type="tap", x=100, y=200),
        action_run_id=action_run.id,
        artifact_ids=[artifact.id],
        viewport_width=1920,
        viewport_height=1080,
    )
    observatory.save_run(action_run)
    observatory.save_evidence_run(evidence_run)
    observatory.save_evidence_step(evidence_step)
    reference = EvidenceReferenceV1(
        environment_id=ENVIRONMENT_ID,
        artifact_ids=[artifact.id],
        evidence_run_ids=[evidence_run.id],
        evidence_step_ids=[evidence_step.id],
        trace_run_ids=[action_run.id],
    )
    player = AIPlayerStore(observatory)
    player.put_environment(
        EnvironmentScopeV1(
            id=ENVIRONMENT_ID,
            game_id="sanguo-mouding-tianxia",
            build_scope_id="build.iteration.fixture",
            account_scope_id="account.iteration.fixture",
            channel="fixture",
            device_scope_id="device.iteration.fixture",
            locale="zh-CN",
            viewport_width=1920,
            viewport_height=1080,
            identity_hash="identity-iteration-fixture",
            evidence_refs=[reference],
        )
    )
    player.put_semantic_state(
        SemanticStateV1(
            id="state.iteration.fixture",
            environment_id=ENVIRONMENT_ID,
            evidence_refs=[reference],
            title="迭代测试界面",
            description="用于验证持续迭代监控的数据界面。",
            semantic_fingerprint="semantic.iteration.fixture",
            observation_feature_hashes=["feature.iteration.fixture"],
        )
    )
    player.put_task(
        FrontierTaskV1(
            id="task.iteration.fixture",
            environment_id=ENVIRONMENT_ID,
            evidence_refs=[reference],
            title="推进迭代测试任务",
            source="coverage_gap",
            reason="测试设施需要一个真实存在的任务引用。",
            action_budget=20,
            time_budget_seconds=600,
        )
    )
    return player, reference


def _sample(
    reference: EvidenceReferenceV1,
    index: int,
    **updates,
) -> ActionQualitySampleV1:
    values = {
        "id": f"action-quality.fixture.{index}",
        "environment_id": ENVIRONMENT_ID,
        "evidence_refs": [reference],
        "session_id": "session.iteration.fixture",
        "task_id": "task.iteration.fixture",
        "semantic_state_id": "state.iteration.fixture",
        "command_id": f"command.iteration.fixture.{index}",
        "action_run_id": "run.iteration.fixture",
        "evidence_step_id": "evidence-step.iteration.fixture",
        "decision_mode": "known_state",
        "execution_disposition": "executed",
        "preflight_disposition": "passed",
        "outcome": "confirmed",
        "expected_change": "进入目标界面并确认语义状态变化。",
        "adapter_call_count": 1,
        "evidence_complete": True,
        "meaningful_change": True,
        "task_progress": True,
        "information_gain_units": 1,
        "token_measurement_status": "measured",
        "model_input_tokens": 200,
        "model_output_tokens": 40,
        "decision_latency_ms": 800,
        "created_at": f"2026-07-15T10:{index:02d}:00Z",
    }
    values.update(updates)
    return ActionQualitySampleV1(**values)


def test_action_quality_contract_does_not_allow_false_execution_or_false_no_effect(tmp_path):
    _, reference = _seed(tmp_path)
    with pytest.raises(ValidationError, match="one adapter call"):
        _sample(reference, 1, adapter_call_count=0)
    with pytest.raises(ValidationError, match="no-effect"):
        _sample(reference, 2, outcome="no_effect")
    with pytest.raises(ValidationError, match="included by the sample"):
        _sample(
            reference,
            3,
            soft_signals=[
                PlayerSoftSignalV1(
                    signal="tutorial_comprehension",
                    score=1,
                    rationale="忽略了高亮的新手引导入口。",
                    evidence_step_ids=["evidence-step.other"],
                )
            ],
        )


def test_incident_pauses_physical_actions_at_the_first_tier(tmp_path):
    player, reference = _seed(tmp_path)
    monitor = PlayerIterationMonitor(player)
    bad = _sample(
        reference,
        1,
        outcome="wrong_target",
        meaningful_change=False,
        task_progress=False,
        information_gain_units=0,
        invalid_target_execution=True,
    )

    stored, assessment = monitor.record(bad)

    assert stored.id == bad.id
    assert assessment is not None
    assert assessment.window_kind == "incident"
    assert assessment.overall_status == "failed"
    assert assessment.directive == "pause_physical_and_repair_perception_executor"
    assert assessment.tiers[0].status == "failed"
    assert assessment.tiers[1].status == "not_evaluated"
    assert player.get_iteration_assessment(ENVIRONMENT_ID, assessment.id) == assessment


def test_ten_action_window_passes_correctness_and_no_spin_before_higher_reviews(tmp_path):
    player, reference = _seed(tmp_path)
    monitor = PlayerIterationMonitor(player)
    assessment = None
    for index in range(10):
        _, assessment = monitor.record(_sample(reference, index, new_state_count=index == 0))

    assert assessment is not None
    assert assessment.window_kind == "actions_10"
    assert assessment.overall_status == "passed"
    assert assessment.directive == "continue"
    assert assessment.highest_contiguous_passed_tier == 2
    assert [tier.status for tier in assessment.tiers] == [
        "passed",
        "passed",
        "not_evaluated",
        "passed",
    ]
    assert len(player.list_action_quality_samples(ENVIRONMENT_ID)) == 10


def test_daily_review_requires_account_progress_and_preserves_soft_review_separation(tmp_path):
    player, reference = _seed(tmp_path)
    sample = _sample(
        reference,
        1,
        objective_completed=True,
        new_rule_count=1,
        account_metric_deltas=[
            PlayerMetricDeltaV1(
                metric_key="account.power",
                label="势力值",
                category="account_progression",
                before=100,
                after=120,
                delta=20,
                unit="点",
                favorable=True,
                evidence_step_ids=["evidence-step.iteration.fixture"],
            )
        ],
        soft_signals=[
            PlayerSoftSignalV1(
                signal="opportunity_awareness",
                score=2,
                rationale="完成主要目标后仍漏看了限时入口。",
                evidence_step_ids=["evidence-step.iteration.fixture"],
            )
        ],
    )
    player.append_action_quality_sample(sample)

    assessment = PlayerIterationMonitor(player).assess(
        ENVIRONMENT_ID,
        "daily_close",
        [sample.id],
    )

    assert assessment.overall_status == "passed"
    assert assessment.highest_contiguous_passed_tier == 4
    assert [tier.status for tier in assessment.tiers] == ["passed"] * 4
    assert assessment.soft_signal_averages == {"opportunity_awareness": 2.0}
    assert assessment.soft_review_reasons


def test_skill_replay_efficiency_threshold_is_a_hard_first_tier_gate(tmp_path):
    _, reference = _seed(tmp_path)
    samples = [
        _sample(
            reference,
            index,
            decision_mode="skill_replay",
            model_input_tokens=80,
            baseline_model_input_tokens=100,
            decision_latency_ms=800,
            baseline_decision_latency_ms=1000,
        )
        for index in range(10)
    ]

    assessment = assess_player_iteration(
        assessment_id="iteration.assessment.skill-efficiency",
        window_kind="actions_10",
        samples=samples,
    )

    assert assessment.overall_status == "failed"
    assert assessment.directive == "pause_physical_and_repair_perception_executor"
    assert "token 降幅不足" in " ".join(assessment.tiers[0].reasons)
    assert "时延降幅不足" in " ".join(assessment.tiers[0].reasons)