"""Evidence-bound monitoring and deterministic iteration policy for the AI player."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from statistics import fmean

from .contracts import (
    ActionQualitySampleV1,
    EvidenceReferenceV1,
    PlayerIterationAssessmentV1,
)
from .store import AIPlayerStore


@dataclass(frozen=True)
class PlayerIterationPolicy:
    version: str = "player-iteration.v1"
    actions_per_review: int = 10
    expected_change_match_rate: float = 0.90
    telemetry_coverage_rate: float = 0.95
    maximum_no_effect_rate: float = 0.08
    maximum_repeat_rate: float = 0.03
    minimum_meaningful_rate: float = 0.92
    skill_token_reduction_rate: float = 0.40
    skill_latency_reduction_rate: float = 0.30
    minimum_soft_signal_score: float = 3.0


DEFAULT_ITERATION_POLICY = PlayerIterationPolicy()


def _ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


def _mean_reduction(samples: list[tuple[int, int]]) -> float | None:
    if not samples:
        return None
    return fmean((baseline - measured) / baseline for measured, baseline in samples)


def _merged_evidence(samples: list[ActionQualitySampleV1]) -> list[EvidenceReferenceV1]:
    environment_id = samples[0].environment_id
    values: dict[str, set[str]] = {
        "artifact_ids": set(),
        "evidence_run_ids": set(),
        "evidence_step_ids": set(),
        "trace_run_ids": set(),
        "source_ids": set(),
    }
    for sample in samples:
        if sample.environment_id != environment_id:
            raise ValueError("an iteration window cannot cross environments")
        for reference in sample.evidence_refs:
            for field in values:
                values[field].update(getattr(reference, field))
    return [
        EvidenceReferenceV1(
            environment_id=environment_id,
            **{field: sorted(items) for field, items in values.items()},
            note="AI 玩家迭代窗口的原始动作、画面与来源证据。",
        )
    ]


def _tier(
    tier: int,
    name: str,
    status: str,
    metrics: dict[str, float | int | None],
    thresholds: dict[str, float | int],
    reasons: list[str],
    directive: str,
) -> dict[str, object]:
    return {
        "tier": tier,
        "name": name,
        "status": status,
        "metrics": metrics,
        "thresholds": thresholds,
        "reasons": reasons,
        "directive": directive,
    }


def assess_player_iteration(
    *,
    assessment_id: str,
    window_kind: str,
    samples: list[ActionQualitySampleV1],
    frontier_exhausted: bool = False,
    policy: PlayerIterationPolicy = DEFAULT_ITERATION_POLICY,
) -> PlayerIterationAssessmentV1:
    """Evaluate correctness, useful behavior, account progress, then discovery coverage."""

    if not samples:
        raise ValueError("an iteration assessment requires at least one action sample")
    ordered = sorted(samples, key=lambda item: (item.created_at, item.id))
    environment_id = ordered[0].environment_id
    if any(item.environment_id != environment_id for item in ordered):
        raise ValueError("an iteration assessment cannot cross environments")

    executed = [item for item in ordered if item.execution_disposition == "executed"]
    sample_count = len(ordered)
    executed_count = len(executed)
    required_samples = policy.actions_per_review if window_kind == "actions_10" else 1
    policy_violations = sum(item.policy_violation for item in ordered)
    invalid_target_executions = sum(item.invalid_target_execution for item in ordered)
    incomplete_evidence = sum(
        item.execution_disposition == "executed" and not item.evidence_complete
        for item in ordered
    )
    confirmed = sum(item.outcome == "confirmed" for item in executed)
    token_measured = sum(item.token_measurement_status == "measured" for item in executed)
    latency_measured = sum(item.decision_latency_ms is not None for item in executed)
    skill_token_pairs = [
        (item.model_input_tokens, item.baseline_model_input_tokens)
        for item in executed
        if item.decision_mode == "skill_replay"
        and item.model_input_tokens is not None
        and item.baseline_model_input_tokens is not None
    ]
    skill_latency_pairs = [
        (item.decision_latency_ms, item.baseline_decision_latency_ms)
        for item in executed
        if item.decision_mode == "skill_replay"
        and item.decision_latency_ms is not None
        and item.baseline_decision_latency_ms is not None
    ]
    expected_rate = _ratio(confirmed, executed_count)
    token_coverage = _ratio(token_measured, executed_count)
    latency_coverage = _ratio(latency_measured, executed_count)
    skill_token_reduction = _mean_reduction(skill_token_pairs)
    skill_latency_reduction = _mean_reduction(skill_latency_pairs)
    tier1_metrics: dict[str, float | int | None] = {
        "sample_count": sample_count,
        "executed_count": executed_count,
        "policy_violation_count": policy_violations,
        "invalid_target_execution_count": invalid_target_executions,
        "incomplete_evidence_count": incomplete_evidence,
        "expected_change_match_rate": expected_rate,
        "token_telemetry_coverage_rate": token_coverage,
        "latency_telemetry_coverage_rate": latency_coverage,
        "skill_token_reduction_rate": skill_token_reduction,
        "skill_latency_reduction_rate": skill_latency_reduction,
    }
    tier1_thresholds = {
        "minimum_sample_count": required_samples,
        "maximum_policy_violation_count": 0,
        "maximum_invalid_target_execution_count": 0,
        "maximum_incomplete_evidence_count": 0,
        "minimum_expected_change_match_rate": policy.expected_change_match_rate,
        "minimum_telemetry_coverage_rate": policy.telemetry_coverage_rate,
        "minimum_skill_token_reduction_rate": policy.skill_token_reduction_rate,
        "minimum_skill_latency_reduction_rate": policy.skill_latency_reduction_rate,
    }
    tier1_reasons: list[str] = []
    if sample_count < required_samples or executed_count == 0:
        tier1_status = "insufficient_data"
        tier1_reasons.append("基础动作样本不足，继续影子裁决，不下发新的实体动作。")
    else:
        if policy_violations:
            tier1_reasons.append("存在策略越界记录。")
        if invalid_target_executions:
            tier1_reasons.append("存在目标无效却仍调用设备的动作。")
        if incomplete_evidence:
            tier1_reasons.append("存在已执行动作缺少完整终态证据。")
        if expected_rate < policy.expected_change_match_rate:
            tier1_reasons.append("动作产生预期语义变化的比例不足。")
        if token_coverage < policy.telemetry_coverage_rate:
            tier1_reasons.append("token 计量覆盖不足。")
        if latency_coverage < policy.telemetry_coverage_rate:
            tier1_reasons.append("决策时延计量覆盖不足。")
        if skill_token_reduction is not None and (
            skill_token_reduction < policy.skill_token_reduction_rate
        ):
            tier1_reasons.append("已知技能重放的 token 降幅不足。")
        if skill_latency_reduction is not None and (
            skill_latency_reduction < policy.skill_latency_reduction_rate
        ):
            tier1_reasons.append("已知技能重放的决策时延降幅不足。")
        tier1_status = "failed" if tier1_reasons else "passed"

    tiers: list[dict[str, object]] = [
        _tier(
            1,
            "基础操作正确、快速且节省 token",
            tier1_status,
            tier1_metrics,
            tier1_thresholds,
            tier1_reasons,
            "修复感知、坐标、执行器或遥测后再恢复实体动作。",
        )
    ]
    if tier1_status != "passed":
        for tier_number, name in (
            (2, "行为有意义且不空转"),
            (3, "像认真玩家一样经营账号"),
            (4, "主动理解尚未记录的游戏内容"),
        ):
            tiers.append(
                _tier(
                    tier_number,
                    name,
                    "not_evaluated",
                    {},
                    {},
                    ["上一层级尚未通过。"],
                    "等待上一层级通过。",
                )
            )
    else:
        no_effect_count = sum(item.outcome == "no_effect" for item in executed)
        repeated_count = sum(item.prior_cluster_failures > 0 for item in executed)
        spin_cluster_count = sum(item.prior_cluster_failures >= 2 for item in executed)
        meaningful_count = sum(
            item.outcome == "confirmed"
            and any(
                (
                    item.meaningful_change,
                    item.task_progress,
                    item.objective_completed,
                    item.information_gain_units > 0,
                    item.new_state_count > 0,
                    item.new_transition_count > 0,
                    item.new_interface_count > 0,
                    item.new_gameplay_count > 0,
                    item.new_rule_count > 0,
                    bool(item.account_metric_deltas),
                )
            )
            for item in executed
        )
        false_empty_count = sum(item.task_queue_falsely_empty for item in ordered)
        recovery_samples = [item for item in executed if item.decision_mode == "recovery"]
        recovery_rate = (
            _ratio(sum(item.recovery_succeeded for item in recovery_samples), len(recovery_samples))
            if recovery_samples
            else None
        )
        tier2_metrics: dict[str, float | int | None] = {
            "no_effect_rate": _ratio(no_effect_count, executed_count),
            "repeated_target_rate": _ratio(repeated_count, executed_count),
            "spin_cluster_count": spin_cluster_count,
            "meaningful_action_rate": _ratio(meaningful_count, executed_count),
            "false_empty_task_queue_count": false_empty_count,
            "recovery_success_rate": recovery_rate,
        }
        tier2_thresholds = {
            "maximum_no_effect_rate": policy.maximum_no_effect_rate,
            "maximum_repeated_target_rate": policy.maximum_repeat_rate,
            "maximum_spin_cluster_count": 0,
            "minimum_meaningful_action_rate": policy.minimum_meaningful_rate,
            "maximum_false_empty_task_queue_count": 0,
        }
        tier2_reasons: list[str] = []
        if tier2_metrics["no_effect_rate"] > policy.maximum_no_effect_rate:
            tier2_reasons.append("无效果动作比例过高。")
        if tier2_metrics["repeated_target_rate"] > policy.maximum_repeat_rate:
            tier2_reasons.append("重复目标动作比例过高。")
        if spin_cluster_count:
            tier2_reasons.append("出现同一目标连续失败仍继续尝试的动作簇。")
        if tier2_metrics["meaningful_action_rate"] < policy.minimum_meaningful_rate:
            tier2_reasons.append("带来任务、账号或知识收益的动作比例不足。")
        if false_empty_count:
            tier2_reasons.append("存在仍有可达前沿却误判无事可做。")
        tier2_status = "failed" if tier2_reasons else "passed"
        tiers.append(
            _tier(
                2,
                "行为有意义且不空转",
                tier2_status,
                tier2_metrics,
                tier2_thresholds,
                tier2_reasons,
                "调整任务选择、动作冷却和恢复策略。",
            )
        )
        if tier2_status != "passed":
            tiers.extend(
                [
                    _tier(
                        3,
                        "像认真玩家一样经营账号",
                        "not_evaluated",
                        {},
                        {},
                        ["上一层级尚未通过。"],
                        "等待上一层级通过。",
                    ),
                    _tier(
                        4,
                        "主动理解尚未记录的游戏内容",
                        "not_evaluated",
                        {},
                        {},
                        ["上一层级尚未通过。"],
                        "等待上一层级通过。",
                    ),
                ]
            )
        else:
            evaluate_progress = window_kind in {"verified_task", "daily_close"}
            favorable_progress = sum(
                delta.favorable and delta.category in {"account_progression", "objective"}
                for item in ordered
                for delta in item.account_metric_deltas
            )
            objectives_completed = sum(item.objective_completed for item in ordered)
            progress_metrics: dict[str, float | int | None] = {
                "favorable_account_or_objective_metric_count": favorable_progress,
                "objective_completed_count": objectives_completed,
                "task_progress_action_count": sum(item.task_progress for item in ordered),
            }
            progress_thresholds = {
                "minimum_favorable_metric_count": 1,
                "minimum_objective_completed_count": 1 if window_kind == "daily_close" else 0,
            }
            progress_reasons: list[str] = []
            if not evaluate_progress:
                tier3_status = "not_evaluated"
                progress_reasons.append("该窗口只复盘动作质量，账号经营在任务完成或日结窗口复盘。")
            else:
                if favorable_progress < 1:
                    progress_reasons.append("窗口内没有可核验的账号或目标正向变化。")
                if window_kind == "daily_close" and objectives_completed < 1:
                    progress_reasons.append("日结窗口没有完成任何明确目标。")
                tier3_status = "failed" if progress_reasons else "passed"
            tiers.append(
                _tier(
                    3,
                    "像认真玩家一样经营账号",
                    tier3_status,
                    progress_metrics,
                    progress_thresholds,
                    progress_reasons,
                    "刷新攻略，重排短中长期目标并检查账号成长停滞。",
                )
            )
            if tier3_status == "failed":
                tiers.append(
                    _tier(
                        4,
                        "主动理解尚未记录的游戏内容",
                        "not_evaluated",
                        {},
                        {},
                        ["账号经营层级尚未通过。"],
                        "等待上一层级通过。",
                    )
                )
            else:
                evaluate_discovery = window_kind in {"actions_10", "verified_task", "daily_close"}
                coverage_gain = sum(
                    item.new_state_count
                    + item.new_transition_count
                    + item.new_interface_count
                    + item.new_gameplay_count
                    + item.new_rule_count
                    for item in ordered
                )
                information_gain = sum(item.information_gain_units for item in ordered)
                discovery_metrics: dict[str, float | int | None] = {
                    "new_canonical_content_count": coverage_gain,
                    "information_gain_units": information_gain,
                    "frontier_exhausted": int(frontier_exhausted),
                }
                discovery_thresholds = {"minimum_new_content_or_information": 1}
                discovery_reasons: list[str] = []
                if not evaluate_discovery:
                    tier4_status = "not_evaluated"
                    discovery_reasons.append("事故窗口只处理当前故障。")
                elif coverage_gain + information_gain < 1 and not frontier_exhausted:
                    tier4_status = "failed"
                    discovery_reasons.append("窗口内没有关闭知识缺口，也没有证明前沿已经穷尽。")
                else:
                    tier4_status = "passed"
                tiers.append(
                    _tier(
                        4,
                        "主动理解尚未记录的游戏内容",
                        tier4_status,
                        discovery_metrics,
                        discovery_thresholds,
                        discovery_reasons,
                        "扩展入口、状态、规则和相邻玩法的探索前沿。",
                    )
                )

    signal_values: dict[str, list[int]] = {}
    for sample in ordered:
        for signal in sample.soft_signals:
            signal_values.setdefault(signal.signal, []).append(signal.score)
    soft_averages = {key: fmean(values) for key, values in sorted(signal_values.items())}
    soft_reasons = [
        f"{signal} 的平均评分为 {score:.2f}，需要回看原始动作与画面。"
        for signal, score in soft_averages.items()
        if score < policy.minimum_soft_signal_score
    ]

    first_failure = next((item["tier"] for item in tiers if item["status"] == "failed"), None)
    if first_failure is not None:
        directive = {
            1: "pause_physical_and_repair_perception_executor",
            2: "revise_planner_and_task_policy",
            3: "refresh_guides_and_reprioritize_objectives",
            4: "expand_discovery_frontier",
        }[first_failure]
        overall_status = "failed"
    elif tiers[0]["status"] == "insufficient_data":
        directive = "shadow_only"
        overall_status = "insufficient_data"
    else:
        directive = "continue"
        overall_status = "passed"
    contiguous = 0
    for item in tiers:
        if item["status"] != "passed":
            break
        contiguous += 1

    return PlayerIterationAssessmentV1(
        id=assessment_id,
        environment_id=environment_id,
        evidence_refs=_merged_evidence(ordered),
        window_kind=window_kind,
        sample_ids=[item.id for item in ordered],
        tiers=tiers,
        highest_contiguous_passed_tier=contiguous,
        overall_status=overall_status,
        directive=directive,
        soft_signal_averages=soft_averages,
        soft_review_reasons=soft_reasons,
        window_started_at=ordered[0].created_at,
        window_ended_at=ordered[-1].created_at,
    )


def stable_iteration_assessment_id(
    environment_id: str,
    window_kind: str,
    sample_ids: list[str],
) -> str:
    payload = json.dumps(
        [environment_id, window_kind, sample_ids],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"iteration.assessment.{hashlib.sha256(payload).hexdigest()[:24]}"


class PlayerIterationMonitor:
    """Append samples and trigger incident or ten-action reviews without model judgement."""

    def __init__(
        self,
        player_store: AIPlayerStore,
        *,
        policy: PlayerIterationPolicy = DEFAULT_ITERATION_POLICY,
    ) -> None:
        self.player_store = player_store
        self.policy = policy

    def record(
        self,
        sample: ActionQualitySampleV1,
    ) -> tuple[ActionQualitySampleV1, PlayerIterationAssessmentV1 | None]:
        existing = self.player_store.get_action_quality_sample(sample.environment_id, sample.id)
        if existing is not None:
            if existing != sample:
                raise ValueError("action-quality sample id already contains different content")
            return existing, None
        stored = self.player_store.append_action_quality_sample(sample)
        incident = any(
            (
                stored.policy_violation,
                stored.invalid_target_execution,
                not stored.evidence_complete and stored.execution_disposition == "executed",
                stored.prior_cluster_failures >= 2,
                stored.outcome in {"blocked_by_overlay", "wrong_target"},
            )
        )
        if incident:
            return stored, self.assess("incident", [stored.id])
        recent = self.player_store.list_action_quality_samples(
            stored.environment_id,
            session_id=stored.session_id,
            limit=self.policy.actions_per_review,
        )
        total = len(
            self.player_store.list_action_quality_samples(
                stored.environment_id,
                session_id=stored.session_id,
                limit=1_000_000,
            )
        )
        if total % self.policy.actions_per_review == 0:
            return stored, self.assess("actions_10", list(reversed(recent)))
        return stored, None

    def assess(
        self,
        window_kind: str,
        sample_ids: list[str],
        *,
        frontier_exhausted: bool = False,
    ) -> PlayerIterationAssessmentV1:
        if not sample_ids:
            raise ValueError("an iteration review requires sample ids")
        samples: list[ActionQualitySampleV1] = []
        environment_id: str | None = None
        for candidate_environment in self.player_store.list_environments():
            candidate_samples = [
                self.player_store.get_action_quality_sample(candidate_environment.id, sample_id)
                for sample_id in sample_ids
            ]
            if all(item is not None for item in candidate_samples):
                environment_id = candidate_environment.id
                samples = [item for item in candidate_samples if item is not None]
                break
        if environment_id is None:
            raise ValueError("iteration review samples do not exist in one environment")
        assessment_id = stable_iteration_assessment_id(
            environment_id,
            window_kind,
            sample_ids,
        )
        existing = self.player_store.get_iteration_assessment(environment_id, assessment_id)
        if existing is not None:
            return existing
        assessment = assess_player_iteration(
            assessment_id=assessment_id,
            window_kind=window_kind,
            samples=samples,
            frontier_exhausted=frontier_exhausted,
            policy=self.policy,
        )
        return self.player_store.append_iteration_assessment(assessment)


__all__ = [
    "DEFAULT_ITERATION_POLICY",
    "PlayerIterationMonitor",
    "PlayerIterationPolicy",
    "assess_player_iteration",
    "stable_iteration_assessment_id",
]