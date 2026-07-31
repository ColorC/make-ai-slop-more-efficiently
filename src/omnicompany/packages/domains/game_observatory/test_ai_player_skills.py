from __future__ import annotations

import hashlib
from collections.abc import Mapping

import pytest

from omnicompany.packages.domains.game_observatory.ai_player.contracts import (
    EnvironmentScopeV1,
    EvidenceReferenceV1,
    SemanticStateV1,
    SkillRunV1,
    SkillStepV1,
    SkillVersionV1,
    TransitionEdgeV1,
)
from omnicompany.packages.domains.game_observatory.ai_player.crystallizer import (
    SkillCrystallizationRequestV1,
    SkillCrystallizer,
)
from omnicompany.packages.domains.game_observatory.ai_player.skill_runtime import (
    SkillExecutionRequestV1,
    SkillRuntime,
    SkillStepExecutionResultV1,
)
from omnicompany.packages.domains.game_observatory.ai_player.skills import (
    SkillLifecycle,
    SkillLifecycleError,
)
from omnicompany.packages.domains.game_observatory.ai_player.store import AIPlayerStore
from omnicompany.packages.domains.game_observatory.models import (
    ArtifactRef,
    NormalizedAction,
    SourcePixelRect,
)
from omnicompany.packages.domains.game_observatory.store import ObservatoryStore


ENVIRONMENT_ID = "environment.skill.fixture"


def seed_artifact(store: ObservatoryStore, artifact_id: str) -> EvidenceReferenceV1:
    raw = f"skill-evidence:{artifact_id}".encode()
    path = store.artifact_root / f"{artifact_id}.bin"
    path.write_bytes(raw)
    store.save_artifact(
        ArtifactRef(
            id=artifact_id,
            kind="screenshot",
            path=str(path),
            sha256=hashlib.sha256(raw).hexdigest(),
            metadata={"environment_id": ENVIRONMENT_ID},
        )
    )
    return EvidenceReferenceV1(environment_id=ENVIRONMENT_ID, artifact_ids=[artifact_id])


def initialized_skill_store(tmp_path):
    observatory = ObservatoryStore(tmp_path / "observatory")
    base_evidence = seed_artifact(observatory, "artifact.skill.base")
    player = AIPlayerStore(observatory)
    environment = EnvironmentScopeV1(
        id=ENVIRONMENT_ID,
        game_id="sanguo-mouding-tianxia",
        build_scope_id="build.skill.1",
        account_scope_id="account.skill.ai",
        channel="bilibili",
        device_scope_id="device.skill.mumu",
        locale="zh-CN",
        viewport_width=1600,
        viewport_height=900,
        identity_hash="identity-hash-skill-fixture",
        evidence_refs=[base_evidence],
    )
    player.put_environment(environment)
    before = SemanticStateV1(
        id="state.skill.before",
        environment_id=ENVIRONMENT_ID,
        evidence_refs=[base_evidence],
        title="技能起点",
        description="技能验证起点",
        semantic_fingerprint="skill-before",
        observation_feature_hashes=["feature.skill.before"],
        status="accepted",
    )
    after = SemanticStateV1(
        id="state.skill.after",
        environment_id=ENVIRONMENT_ID,
        evidence_refs=[base_evidence],
        title="技能终点",
        description="技能验证终点",
        semantic_fingerprint="skill-after",
        observation_feature_hashes=["feature.skill.after"],
        status="accepted",
    )
    player.put_semantic_state(before)
    player.put_semantic_state(after)
    edge = TransitionEdgeV1(
        id="edge.skill.open",
        environment_id=ENVIRONMENT_ID,
        evidence_refs=[base_evidence],
        from_state_id=before.id,
        to_state_id=after.id,
        action=NormalizedAction(type="tap", x=420, y=360),
        target_bounds=SourcePixelRect(x=380, y=320, width=120, height=90),
        expected_change="打开目标界面",
        observed_change="目标界面已显示",
        outcome="verified_transition",
    )
    player.put_transition_edge(edge)
    return observatory, player, environment, edge, base_evidence


def crystallize(player: AIPlayerStore) -> SkillVersionV1:
    return SkillCrystallizer(player).crystallize(
        SkillCrystallizationRequestV1(
            environment_id=ENVIRONMENT_ID,
            skill_id="skill.fixture.open",
            title="打开目标界面",
            level="L2",
            transition_ids=["edge.skill.open"],
            applicability="技能起点可见且身份完全匹配",
            safety_level="reversible",
            success_checks=["state.skill.after 可见"],
            failure_checks=["仍停留在 state.skill.before"],
            visual_variant_ids=["variant.a", "variant.b"],
        )
    )


def test_crystallizer_produces_only_a_structured_candidate(tmp_path):
    _observatory, player, _environment, _edge, _evidence = initialized_skill_store(tmp_path)
    candidate = crystallize(player)

    assert candidate.status == "candidate"
    assert candidate.level == "L2"
    assert candidate.skill_layer == "atomic"
    assert candidate.scope == "interaction"
    assert candidate.locators[0].reference_bounds == SourcePixelRect(
        x=380,
        y=320,
        width=120,
        height=90,
    )
    assert [step.kind for step in candidate.steps] == ["action", "assert"]
    assert candidate.content_sha256 == candidate.compute_content_sha256()
    assert SkillLifecycle(player).select_preferred(
        ENVIRONMENT_ID,
        current_state_id="state.skill.before",
        visual_variant_id="variant.a",
    ) == []


def test_crystallizer_rejects_failed_or_open_routes(tmp_path):
    _observatory, player, _environment, edge, evidence = initialized_skill_store(tmp_path)
    failed = edge.model_copy(
        update={
            "id": "edge.skill.failed",
            "outcome": "failed",
            "to_state_id": None,
            "observed_change": "没有进入目标界面",
            "evidence_refs": [evidence],
        }
    )
    player.put_transition_edge(failed)

    with pytest.raises(SkillLifecycleError, match="successful"):
        SkillCrystallizer(player).crystallize(
            SkillCrystallizationRequestV1(
                environment_id=ENVIRONMENT_ID,
                skill_id="skill.fixture.failed",
                title="失败路径",
                level="L2",
                transition_ids=[failed.id],
                applicability="固定反例",
                safety_level="reversible",
                success_checks=["目标可见"],
                failure_checks=["目标不可见"],
            )
        )


def test_twenty_independent_runs_promote_then_drift_disables_selection(tmp_path):
    observatory, player, _environment, _edge, _evidence = initialized_skill_store(tmp_path)
    candidate = crystallize(player)
    lifecycle = SkillLifecycle(player)
    run_evidence = [seed_artifact(observatory, f"artifact.skill.run.{index}") for index in range(20)]
    for index, evidence in enumerate(run_evidence):
        outcome = "success"
        precondition_satisfied = True
        objective_success = True
        recovery_attempted = False
        recovery_succeeded = False
        if index == 18:
            outcome = "precondition_unmet"
            precondition_satisfied = False
            objective_success = False
        elif index == 19:
            outcome = "interrupted"
            objective_success = False
            recovery_attempted = True
            recovery_succeeded = True
        lifecycle.record_run(
            SkillRunV1(
                id=f"skill-run.{index}",
                environment_id=ENVIRONMENT_ID,
                evidence_refs=[evidence],
                skill_version_id=candidate.id,
                attempt_index=index + 1,
                independent_reset_id=f"reset.{index % 3}",
                visual_variant_id=f"variant.{index % 2}",
                outcome=outcome,
                precondition_satisfied=precondition_satisfied,
                objective_success=objective_success,
                validation_passed=True,
                recovery_attempted=recovery_attempted,
                recovery_succeeded=recovery_succeeded,
                action_count=1,
                model_input_tokens=50,
                baseline_model_input_tokens=100,
                decision_latency_ms=60,
                baseline_decision_latency_ms=100,
            )
        )

    validation = lifecycle.validate(
        ENVIRONMENT_ID,
        candidate.id,
        evaluator="independent-fixture-validator",
    )
    assert validation.status == "passed"
    assert validation.total_run_count == 20
    assert validation.success_rate == 1
    assert validation.evidence_reuse_count == 0

    preferred = lifecycle.promote_preferred(ENVIRONMENT_ID, validation.id)
    assert preferred.status == "preferred"
    assert preferred.source_skill_version_id == candidate.id
    assert lifecycle.select_preferred(
        ENVIRONMENT_ID,
        current_state_id="state.skill.before",
        visual_variant_id="variant.a",
    ) == [preferred]

    drift_evidence = seed_artifact(observatory, "artifact.skill.drift")
    degraded = lifecycle.degrade(
        ENVIRONMENT_ID,
        preferred.skill_id,
        reason="构建更新后目标定位与客观后置均失配",
        evidence_refs=[drift_evidence],
    )
    assert degraded.status == "degraded"
    assert degraded.content_sha256 == preferred.content_sha256
    assert lifecycle.select_preferred(
        ENVIRONMENT_ID,
        current_state_id="state.skill.before",
        visual_variant_id="variant.a",
    ) == []


def test_reused_validation_evidence_cannot_pass(tmp_path):
    _observatory, player, _environment, _edge, evidence = initialized_skill_store(tmp_path)
    candidate = crystallize(player)
    lifecycle = SkillLifecycle(player)
    for index in range(20):
        lifecycle.record_run(
            SkillRunV1(
                id=f"skill-run.reused.{index}",
                environment_id=ENVIRONMENT_ID,
                evidence_refs=[evidence],
                skill_version_id=candidate.id,
                attempt_index=index + 1,
                independent_reset_id=f"reset.{index % 3}",
                visual_variant_id=f"variant.{index % 2}",
                outcome=(
                    "precondition_unmet"
                    if index == 18
                    else "interrupted"
                    if index == 19
                    else "success"
                ),
                precondition_satisfied=index != 18,
                objective_success=index < 18,
                validation_passed=True,
                recovery_attempted=index == 19,
                recovery_succeeded=index == 19,
                action_count=1,
                model_input_tokens=40,
                baseline_model_input_tokens=100,
                decision_latency_ms=50,
                baseline_decision_latency_ms=100,
            )
        )

    validation = lifecycle.validate(
        ENVIRONMENT_ID,
        candidate.id,
        evaluator="independent-fixture-validator",
    )
    assert validation.status == "failed"
    assert validation.evidence_reuse_count == 19
    with pytest.raises(SkillLifecycleError, match="failed validation"):
        lifecycle.promote_preferred(ENVIRONMENT_ID, validation.id)


class FakeStepAdapter:
    def __init__(self, evidence: EvidenceReferenceV1, *, fail_assertion: bool = False) -> None:
        self.evidence = evidence
        self.fail_assertion = fail_assertion
        self.calls: list[str] = []

    def execute_step(
        self,
        _skill: SkillVersionV1,
        step: SkillStepV1,
        _parameters: Mapping[str, object],
    ) -> SkillStepExecutionResultV1:
        self.calls.append(step.id)
        objective_success = not (self.fail_assertion and step.kind == "assert")
        return SkillStepExecutionResultV1(
            step_id=step.id,
            outcome="success",
            claimed_success=True,
            objective_success=objective_success,
            evidence_refs=[self.evidence],
            action_count=1 if step.kind == "action" else 0,
            model_input_tokens=10,
            decision_latency_ms=5,
            result_summary="固定适配器结果",
        )

    def execute_recovery(
        self,
        _recovery_skill: SkillVersionV1,
        _failed_skill: SkillVersionV1,
        failed_step: SkillStepV1,
        _parameters: Mapping[str, object],
    ) -> SkillStepExecutionResultV1:
        return SkillStepExecutionResultV1(
            step_id=failed_step.id,
            outcome="success",
            claimed_success=True,
            objective_success=True,
            evidence_refs=[self.evidence],
            result_summary="固定恢复结果",
        )


def test_runtime_precondition_is_zero_action_and_false_success_is_recorded(tmp_path):
    _observatory, player, _environment, _edge, evidence = initialized_skill_store(tmp_path)
    candidate = crystallize(player)
    adapter = FakeStepAdapter(evidence)
    runtime = SkillRuntime(player, adapter)
    precondition = runtime.execute(
        SkillExecutionRequestV1(
            environment_id=ENVIRONMENT_ID,
            skill_id=candidate.skill_id,
            skill_version_id=candidate.id,
            validation_mode=True,
            run_id="skill-run.precondition",
            attempt_index=1,
            independent_reset_id="reset.precondition",
            visual_variant_id="variant.a",
            current_state_id="state.other",
            initial_evidence_refs=[evidence],
            baseline_model_input_tokens=100,
            baseline_decision_latency_ms=100,
        )
    )
    assert precondition.run.outcome == "precondition_unmet"
    assert precondition.run.action_count == 0
    assert adapter.calls == []

    failing_adapter = FakeStepAdapter(evidence, fail_assertion=True)
    failed = SkillRuntime(player, failing_adapter).execute(
        SkillExecutionRequestV1(
            environment_id=ENVIRONMENT_ID,
            skill_id=candidate.skill_id,
            skill_version_id=candidate.id,
            validation_mode=True,
            run_id="skill-run.false-success",
            attempt_index=2,
            independent_reset_id="reset.false-success",
            visual_variant_id="variant.a",
            current_state_id="state.skill.before",
            initial_evidence_refs=[evidence],
            baseline_model_input_tokens=100,
            baseline_decision_latency_ms=100,
        )
    )
    assert failed.run.outcome == "false_success"
    assert failed.run.false_success is True
    assert failed.run.validation_passed is False
    assert failing_adapter.calls == ["step.1.action", "step.1.assert"]