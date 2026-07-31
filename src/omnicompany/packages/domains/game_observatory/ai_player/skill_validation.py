"""Pure derivation of skill validation aggregates from immutable skill runs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from .contracts import EvidenceReferenceV1, SkillRunV1, SkillValidationV1


def _dedupe_evidence(
    references: Iterable[EvidenceReferenceV1],
) -> list[EvidenceReferenceV1]:
    unique: dict[str, EvidenceReferenceV1] = {}
    for reference in references:
        unique.setdefault(reference.model_dump_json(by_alias=True), reference)
    return list(unique.values())


def derive_skill_validation(
    *,
    environment_id: str,
    skill_version_id: str,
    evaluator: str,
    runs: list[SkillRunV1],
    created_at: str | None = None,
) -> SkillValidationV1:
    """Derive every aggregate and gate result; callers cannot self-report metrics."""

    if not runs:
        raise ValueError("a skill cannot be validated without independent runs")
    if any(run.environment_id != environment_id for run in runs):
        raise ValueError("skill validation runs crossed environments")
    if any(run.skill_version_id != skill_version_id for run in runs):
        raise ValueError("skill validation mixes different skill versions")

    successful = sum(run.validation_passed for run in runs)
    false_successes = sum(run.false_success for run in runs)
    safety_violations = sum(run.safety_violation_count for run in runs)
    reset_count = len({run.independent_reset_id for run in runs})
    variant_count = len({run.visual_variant_id for run in runs})
    unmet_count = sum(
        run.outcome == "precondition_unmet" and run.validation_passed for run in runs
    )
    interruption_count = sum(
        run.outcome == "interrupted" and run.validation_passed for run in runs
    )
    recovery_count = sum(run.recovery_succeeded for run in runs)
    baseline_tokens = sum(run.baseline_model_input_tokens for run in runs)
    actual_tokens = sum(run.model_input_tokens for run in runs)
    baseline_latency = sum(run.baseline_decision_latency_ms for run in runs)
    actual_latency = sum(run.decision_latency_ms for run in runs)

    seen_evidence_ids: set[tuple[str, str]] = set()
    evidence_reuse_count = 0
    for run in runs:
        run_ids: set[tuple[str, str]] = set()
        for reference in run.evidence_refs:
            run_ids.update(("artifact", item) for item in reference.artifact_ids)
            run_ids.update(("evidence_run", item) for item in reference.evidence_run_ids)
            run_ids.update(("evidence_step", item) for item in reference.evidence_step_ids)
            run_ids.update(("trace_run", item) for item in reference.trace_run_ids)
            run_ids.update(("source", item) for item in reference.source_ids)
        evidence_reuse_count += len(run_ids.intersection(seen_evidence_ids))
        seen_evidence_ids.update(run_ids)

    token_reduction = 1 - (actual_tokens / baseline_tokens) if baseline_tokens else -1.0
    latency_reduction = 1 - (actual_latency / baseline_latency) if baseline_latency else -1.0
    success_rate = successful / len(runs)
    checks = (
        (len(runs) >= 20, "独立回放少于 20 次"),
        (success_rate >= 0.95, "适用域成功率低于 95%"),
        (false_successes == 0, "存在错误成功判定"),
        (safety_violations == 0, "存在安全越界"),
        (reset_count >= 3, "独立重置少于 3 类"),
        (variant_count >= 2, "视觉或目标变体少于 2 类"),
        (unmet_count >= 1, "缺少前置不满足注入"),
        (interruption_count >= 1, "缺少中断注入"),
        (recovery_count >= 1, "缺少成功恢复"),
        (evidence_reuse_count == 0, "独立验证运行复用了证据"),
        (token_reduction >= 0.40, "模型输入 token 降幅低于 40%"),
        (latency_reduction >= 0.30, "动作决策时延降幅低于 30%"),
    )
    reasons = [reason for passed, reason in checks if not passed]
    run_ids = [run.id for run in runs]
    digest = hashlib.sha256(
        json.dumps(
            {"skill_version_id": skill_version_id, "run_ids": run_ids},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:24]
    values = {
        "id": f"skill-validation.{digest}",
        "environment_id": environment_id,
        "evidence_refs": _dedupe_evidence(
            reference for run in runs for reference in run.evidence_refs
        ),
        "skill_version_id": skill_version_id,
        "skill_run_ids": run_ids,
        "evaluator": evaluator,
        "status": "failed" if reasons else "passed",
        "total_run_count": len(runs),
        "successful_run_count": successful,
        "false_success_count": false_successes,
        "safety_violation_count": safety_violations,
        "independent_reset_count": reset_count,
        "visual_variant_count": variant_count,
        "unmet_precondition_count": unmet_count,
        "interruption_count": interruption_count,
        "successful_recovery_count": recovery_count,
        "evidence_reuse_count": evidence_reuse_count,
        "success_rate": success_rate,
        "token_reduction_rate": token_reduction,
        "latency_reduction_rate": latency_reduction,
        "reasons": reasons,
    }
    if created_at is not None:
        values["created_at"] = created_at
    return SkillValidationV1.model_validate(values)