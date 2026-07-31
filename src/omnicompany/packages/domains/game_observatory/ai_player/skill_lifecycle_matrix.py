"""Read-only G-07 skill-lifecycle matrix derived from canonical skill records.

The matrix is deliberately a projection.  It never persists a validation and it
never changes a candidate skill's lifecycle status.  P-13 producers can consume
``SkillLifecycleMatrixV1.model_dump(by_alias=True)`` directly and attach their
own signed receipt around the deterministic payload.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from statistics import median
from typing import Literal, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from .contracts import SkillRunV1, SkillVersionV1
from .skill_validation import derive_skill_validation

if TYPE_CHECKING:
    from .store import AIPlayerStore


REFERENCE_SKILL_COUNT_PER_GAME = 6
REQUIRED_LEVELS = ("L2", "L3", "L4")


class _StrictProjection(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class SkillLifecycleThresholdsV1(_StrictProjection):
    minimum_replay_count: int = 20
    minimum_success_rate: float = 0.95
    minimum_independent_reset_count: int = 3
    minimum_visual_variant_count: int = 2
    minimum_unmet_precondition_count: int = 1
    minimum_interruption_count: int = 1
    minimum_successful_recovery_count: int = 1
    required_zero_model_ratio: float = 1.0
    decision_latency_p50_max_ms: float = 5_000.0
    decision_latency_p95_max_ms: float = 10_000.0
    minimum_token_reduction_rate: float = 0.40
    minimum_latency_reduction_rate: float = 0.30
    reference_skill_count_per_game: int = REFERENCE_SKILL_COUNT_PER_GAME


class ReplayReuseEvidenceV1(_StrictProjection):
    status: Literal["proven", "missing"]
    run_id: str | None = None
    same_skill_version_reused: bool = False


class ThirdReplayCandidateEvidenceV1(_StrictProjection):
    status: Literal[
        "measured_zero_delta",
        "same_version_only",
        "missing",
    ]
    run_id: str | None = None
    same_skill_version_reused: bool = False
    equivalent_candidate_delta_measurement: Literal["measured", "unavailable"] = (
        "unavailable"
    )
    equivalent_candidate_delta: int | None = Field(default=None, ge=0)
    note: str


class SkillLifecycleRowV1(_StrictProjection):
    game_id: str
    environment_id: str
    skill_id: str
    skill_version_id: str
    version: int
    title: str
    level: str
    skill_layer: str
    lifecycle_status: str
    validation_status: Literal["PASS", "FAIL", "UNAVAILABLE"]
    replay_total_count: int = Field(ge=0)
    successful_validation_count: int = Field(ge=0)
    successful_outcome_count: int = Field(ge=0)
    success_rate: float = Field(ge=0, le=1)
    false_success_count: int = Field(ge=0)
    safety_violation_count: int = Field(ge=0)
    independent_reset_count: int = Field(ge=0)
    visual_variant_count: int = Field(ge=0)
    unmet_precondition_count: int = Field(ge=0)
    interruption_count: int = Field(ge=0)
    successful_recovery_count: int = Field(ge=0)
    zero_model_replay_count: int = Field(ge=0)
    zero_model_ratio: float = Field(ge=0, le=1)
    latency_sample_count: int = Field(ge=0)
    decision_latency_p50_ms: float | None = Field(default=None, ge=0)
    decision_latency_p95_ms: float | None = Field(default=None, ge=0)
    token_reduction_rate: float | None = Field(default=None, ge=-1, le=1)
    latency_reduction_rate: float | None = Field(default=None, ge=-1, le=1)
    validator_ids: list[str]
    second_replay_evidence: ReplayReuseEvidenceV1
    third_replay_evidence: ThirdReplayCandidateEvidenceV1
    gate_pass_count: int = Field(ge=0)
    gate_check_count: int = Field(ge=1)
    readiness_score: float = Field(ge=0, le=1)
    g07_ready: bool
    reasons: list[str]


class GameReferenceSkillSelectionV1(_StrictProjection):
    game_id: str
    requested_count: int = Field(ge=1)
    selected_skill_version_ids: list[str]
    selected_count: int = Field(ge=0)
    missing_count: int = Field(ge=0)
    selected_level_counts: dict[str, int]
    missing_required_levels: list[str]
    g07_ready_selected_count: int = Field(ge=0)
    status: Literal["complete", "incomplete"]


class SkillLifecycleMatrixSummaryV1(_StrictProjection):
    expected_game_count: int = Field(ge=1)
    expected_reference_skill_count: int = Field(ge=1)
    latest_skill_count: int = Field(ge=0)
    matched_run_count: int = Field(ge=0)
    unmatched_run_count: int = Field(ge=0)
    selected_reference_skill_count: int = Field(ge=0)
    g07_ready_selected_skill_count: int = Field(ge=0)
    status: Literal["PASS", "FAIL"]
    reasons: list[str]


class SkillLifecycleMatrixV1(_StrictProjection):
    schema_id: Literal[
        "game-observatory.ai-player.skill-lifecycle-matrix.v1"
    ] = Field(
        default="game-observatory.ai-player.skill-lifecycle-matrix.v1",
        alias="schema",
    )
    thresholds: SkillLifecycleThresholdsV1
    expected_game_ids: list[str]
    skills: list[SkillLifecycleRowV1]
    reference_selection: list[GameReferenceSkillSelectionV1]
    summary: SkillLifecycleMatrixSummaryV1


def _nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile) - 1)
    return float(ordered[index])


def _safe_reduction(actual: float, baseline: float) -> float | None:
    if baseline <= 0:
        return None
    return max(-1.0, min(1.0, 1 - (actual / baseline)))


def _successful_replays(runs: Sequence[SkillRunV1]) -> list[SkillRunV1]:
    return [
        run
        for run in runs
        if run.outcome == "success"
        and run.objective_success
        and run.validation_passed
        and not run.false_success
        and run.safety_violation_count == 0
    ]


def _run_order(run: SkillRunV1) -> tuple[str, str, int, str]:
    return (run.started_at, run.finished_at, run.attempt_index, run.id)


def _validation_projection(
    skill: SkillVersionV1,
    runs: list[SkillRunV1],
) -> tuple[Literal["PASS", "FAIL", "UNAVAILABLE"], dict[str, float | int], list[str]]:
    """Reuse canonical validation derivation when its trust boundary permits it."""

    validators = sorted({run.validator_id for run in runs})
    if not runs:
        return "UNAVAILABLE", {}, ["没有回放记录"]
    if len(validators) != 1:
        return (
            "UNAVAILABLE",
            {},
            ["回放来自多个 validator，不能合并为一份 canonical SkillValidation"],
        )
    validation = derive_skill_validation(
        environment_id=skill.environment_id,
        skill_version_id=skill.id,
        evaluator=validators[0],
        runs=runs,
        created_at="1970-01-01T00:00:00+00:00",
    )
    return (
        "PASS" if validation.status == "passed" else "FAIL",
        {
            "successful_validation_count": validation.successful_run_count,
            "success_rate": validation.success_rate,
            "false_success_count": validation.false_success_count,
            "safety_violation_count": validation.safety_violation_count,
            "independent_reset_count": validation.independent_reset_count,
            "visual_variant_count": validation.visual_variant_count,
            "unmet_precondition_count": validation.unmet_precondition_count,
            "interruption_count": validation.interruption_count,
            "successful_recovery_count": validation.successful_recovery_count,
            "token_reduction_rate": validation.token_reduction_rate,
            "latency_reduction_rate": validation.latency_reduction_rate,
        },
        list(validation.reasons),
    )


def _build_row(
    *,
    skill: SkillVersionV1,
    game_id: str,
    runs: list[SkillRunV1],
    thresholds: SkillLifecycleThresholdsV1,
) -> SkillLifecycleRowV1:
    runs = sorted(runs, key=_run_order)
    successful_replays = _successful_replays(runs)
    validation_status, validation_metrics, reasons = _validation_projection(skill, runs)

    total = len(runs)
    successful_validation_count = int(
        validation_metrics.get(
            "successful_validation_count",
            sum(run.validation_passed for run in runs),
        )
    )
    success_rate = float(
        validation_metrics.get(
            "success_rate",
            successful_validation_count / total if total else 0.0,
        )
    )
    false_success_count = int(
        validation_metrics.get("false_success_count", sum(run.false_success for run in runs))
    )
    safety_violation_count = int(
        validation_metrics.get(
            "safety_violation_count", sum(run.safety_violation_count for run in runs)
        )
    )
    reset_count = int(
        validation_metrics.get(
            "independent_reset_count", len({run.independent_reset_id for run in runs})
        )
    )
    variant_count = int(
        validation_metrics.get(
            "visual_variant_count", len({run.visual_variant_id for run in runs})
        )
    )
    unmet_count = int(
        validation_metrics.get(
            "unmet_precondition_count",
            sum(run.outcome == "precondition_unmet" and run.validation_passed for run in runs),
        )
    )
    interruption_count = int(
        validation_metrics.get(
            "interruption_count",
            sum(run.outcome == "interrupted" and run.validation_passed for run in runs),
        )
    )
    recovery_count = int(
        validation_metrics.get(
            "successful_recovery_count", sum(run.recovery_succeeded for run in runs)
        )
    )
    zero_model_count = sum(run.model_input_tokens == 0 for run in runs)
    zero_model_ratio = zero_model_count / total if total else 0.0
    latencies = [run.decision_latency_ms for run in successful_replays]
    p50 = float(median(latencies)) if latencies else None
    p95 = _nearest_rank(latencies, 0.95)
    token_reduction = validation_metrics.get("token_reduction_rate")
    latency_reduction = validation_metrics.get("latency_reduction_rate")
    if token_reduction is None and runs:
        token_reduction = _safe_reduction(
            sum(run.model_input_tokens for run in runs),
            sum(run.baseline_model_input_tokens for run in runs),
        )
    if latency_reduction is None and runs:
        latency_reduction = _safe_reduction(
            sum(run.decision_latency_ms for run in runs),
            sum(run.baseline_decision_latency_ms for run in runs),
        )

    second = successful_replays[1] if len(successful_replays) >= 2 else None
    third = successful_replays[2] if len(successful_replays) >= 3 else None
    second_evidence = ReplayReuseEvidenceV1(
        status="proven" if second else "missing",
        run_id=second.id if second else None,
        same_skill_version_reused=second is not None,
    )
    third_evidence = ThirdReplayCandidateEvidenceV1(
        status="same_version_only" if third else "missing",
        run_id=third.id if third else None,
        same_skill_version_reused=third is not None,
        equivalent_candidate_delta_measurement="unavailable",
        equivalent_candidate_delta=None,
        note=(
            "第三次成功回放仍使用同一技能版本；仅凭 latest skill versions 与 SkillRun "
            "无法证明语义等价候选增量为 0。"
            if third
            else "少于三次成功回放，尚无第三次复用证据。"
        ),
    )

    checks: list[tuple[bool, str]] = [
        (total >= thresholds.minimum_replay_count, "独立回放少于 20 次"),
        (success_rate >= thresholds.minimum_success_rate, "适用域成功率低于 95%"),
        (false_success_count == 0, "存在错误成功判定"),
        (safety_violation_count == 0, "存在安全越界"),
        (
            reset_count >= thresholds.minimum_independent_reset_count,
            "独立重置少于 3 类",
        ),
        (
            variant_count >= thresholds.minimum_visual_variant_count,
            "视觉或目标变体少于 2 类",
        ),
        (
            unmet_count >= thresholds.minimum_unmet_precondition_count,
            "缺少前置不满足注入",
        ),
        (
            interruption_count >= thresholds.minimum_interruption_count,
            "缺少中断注入",
        ),
        (
            recovery_count >= thresholds.minimum_successful_recovery_count,
            "缺少成功恢复",
        ),
        (
            zero_model_ratio >= thresholds.required_zero_model_ratio,
            "固定层回放并非全部零模型",
        ),
        (second is not None, "缺少第二次同版本复用证据"),
        (third is not None, "缺少第三次同版本复用证据"),
        (
            third_evidence.status == "measured_zero_delta",
            "第三次等价候选增量尚未测量为 0",
        ),
        (
            p50 is not None and p50 <= thresholds.decision_latency_p50_max_ms,
            "成功回放决策时延 P50 超门或缺失",
        ),
        (
            p95 is not None and p95 <= thresholds.decision_latency_p95_max_ms,
            "成功回放决策时延 P95 超门或缺失",
        ),
        (
            token_reduction is not None
            and token_reduction >= thresholds.minimum_token_reduction_rate,
            "模型输入 token 降幅低于 40% 或缺失",
        ),
        (
            latency_reduction is not None
            and latency_reduction >= thresholds.minimum_latency_reduction_rate,
            "动作决策时延降幅低于 30% 或缺失",
        ),
    ]
    failed_reasons = list(dict.fromkeys([*reasons, *(reason for ok, reason in checks if not ok)]))
    gate_pass_count = sum(ok for ok, _ in checks)

    return SkillLifecycleRowV1(
        game_id=game_id,
        environment_id=skill.environment_id,
        skill_id=skill.skill_id,
        skill_version_id=skill.id,
        version=skill.version,
        title=skill.title,
        level=skill.level,
        skill_layer=skill.skill_layer,
        lifecycle_status=skill.status,
        validation_status=validation_status,
        replay_total_count=total,
        successful_validation_count=successful_validation_count,
        successful_outcome_count=len(successful_replays),
        success_rate=success_rate,
        false_success_count=false_success_count,
        safety_violation_count=safety_violation_count,
        independent_reset_count=reset_count,
        visual_variant_count=variant_count,
        unmet_precondition_count=unmet_count,
        interruption_count=interruption_count,
        successful_recovery_count=recovery_count,
        zero_model_replay_count=zero_model_count,
        zero_model_ratio=zero_model_ratio,
        latency_sample_count=len(latencies),
        decision_latency_p50_ms=p50,
        decision_latency_p95_ms=p95,
        token_reduction_rate=float(token_reduction) if token_reduction is not None else None,
        latency_reduction_rate=(
            float(latency_reduction) if latency_reduction is not None else None
        ),
        validator_ids=sorted({run.validator_id for run in runs}),
        second_replay_evidence=second_evidence,
        third_replay_evidence=third_evidence,
        gate_pass_count=gate_pass_count,
        gate_check_count=len(checks),
        readiness_score=round(gate_pass_count / len(checks), 6),
        g07_ready=not failed_reasons,
        reasons=failed_reasons,
    )


def _reference_rank(row: SkillLifecycleRowV1) -> tuple[float, int, float, int, str]:
    return (
        -row.readiness_score,
        -min(row.replay_total_count, 20),
        -row.success_rate,
        row.false_success_count + row.safety_violation_count,
        row.skill_version_id,
    )


def _select_reference_rows(
    rows: Sequence[SkillLifecycleRowV1],
    limit: int,
) -> list[SkillLifecycleRowV1]:
    eligible = sorted(
        (
            row
            for row in rows
            if row.level in REQUIRED_LEVELS
            and row.lifecycle_status not in {"degraded", "invalidated"}
        ),
        key=_reference_rank,
    )
    selected: list[SkillLifecycleRowV1] = []
    selected_ids: set[str] = set()
    for level in REQUIRED_LEVELS:
        candidate = next((row for row in eligible if row.level == level), None)
        if candidate is not None:
            selected.append(candidate)
            selected_ids.add(candidate.skill_version_id)
    for row in eligible:
        if len(selected) >= limit:
            break
        if row.skill_version_id not in selected_ids:
            selected.append(row)
            selected_ids.add(row.skill_version_id)
    return sorted(selected[:limit], key=_reference_rank)


def build_skill_lifecycle_matrix(
    *,
    latest_skill_versions: Sequence[SkillVersionV1],
    skill_runs: Sequence[SkillRunV1],
    game_id_by_environment: Mapping[str, str],
    expected_game_ids: Sequence[str] = ("afk-journey", "sanguo-mouding-tianxia"),
    reference_skill_count_per_game: int = REFERENCE_SKILL_COUNT_PER_GAME,
) -> SkillLifecycleMatrixV1:
    """Build a deterministic G-07 projection without changing canonical records."""

    if reference_skill_count_per_game < 1:
        raise ValueError("reference_skill_count_per_game must be positive")
    if not expected_game_ids or len(set(expected_game_ids)) != len(expected_game_ids):
        raise ValueError("expected_game_ids must be non-empty and unique")

    latest_by_skill: dict[tuple[str, str], SkillVersionV1] = {}
    for skill in latest_skill_versions:
        if skill.environment_id not in game_id_by_environment:
            raise ValueError(f"missing game id for environment: {skill.environment_id}")
        key = (skill.environment_id, skill.skill_id)
        previous = latest_by_skill.get(key)
        if previous is None or (skill.version, skill.id) > (previous.version, previous.id):
            latest_by_skill[key] = skill

    latest = sorted(
        latest_by_skill.values(),
        key=lambda item: (
            game_id_by_environment[item.environment_id],
            item.environment_id,
            item.skill_id,
            item.version,
            item.id,
        ),
    )
    latest_ids = {skill.id for skill in latest}
    runs_by_version: dict[str, list[SkillRunV1]] = defaultdict(list)
    unmatched_run_count = 0
    for run in skill_runs:
        if run.skill_version_id not in latest_ids:
            unmatched_run_count += 1
            continue
        runs_by_version[run.skill_version_id].append(run)

    thresholds = SkillLifecycleThresholdsV1(
        reference_skill_count_per_game=reference_skill_count_per_game
    )
    rows = [
        _build_row(
            skill=skill,
            game_id=game_id_by_environment[skill.environment_id],
            runs=runs_by_version.get(skill.id, []),
            thresholds=thresholds,
        )
        for skill in latest
    ]

    selections: list[GameReferenceSkillSelectionV1] = []
    selected_ids: list[str] = []
    selected_ready_count = 0
    summary_reasons: list[str] = []
    for game_id in expected_game_ids:
        game_rows = [row for row in rows if row.game_id == game_id]
        selected = _select_reference_rows(game_rows, reference_skill_count_per_game)
        selected_ids.extend(row.skill_version_id for row in selected)
        selected_ready = sum(row.g07_ready for row in selected)
        selected_ready_count += selected_ready
        level_counts = {level: sum(row.level == level for row in selected) for level in REQUIRED_LEVELS}
        missing_levels = [level for level, count in level_counts.items() if count == 0]
        missing_count = max(0, reference_skill_count_per_game - len(selected))
        status: Literal["complete", "incomplete"] = (
            "complete" if missing_count == 0 and not missing_levels else "incomplete"
        )
        if missing_count:
            summary_reasons.append(f"{game_id} 缺少 {missing_count} 个参考技能")
        if missing_levels:
            summary_reasons.append(f"{game_id} 缺少层级：{', '.join(missing_levels)}")
        if selected_ready != len(selected):
            summary_reasons.append(
                f"{game_id} 已选参考技能中 {len(selected) - selected_ready} 个未通过 G-07"
            )
        selections.append(
            GameReferenceSkillSelectionV1(
                game_id=game_id,
                requested_count=reference_skill_count_per_game,
                selected_skill_version_ids=[row.skill_version_id for row in selected],
                selected_count=len(selected),
                missing_count=missing_count,
                selected_level_counts=level_counts,
                missing_required_levels=missing_levels,
                g07_ready_selected_count=selected_ready,
                status=status,
            )
        )

    expected_reference_count = len(expected_game_ids) * reference_skill_count_per_game
    if len(selected_ids) != expected_reference_count:
        summary_reasons.append("参考技能总数未达到双游戏门槛")
    if selected_ready_count != expected_reference_count:
        summary_reasons.append("并非全部参考技能都通过 G-07")

    return SkillLifecycleMatrixV1(
        thresholds=thresholds,
        expected_game_ids=list(expected_game_ids),
        skills=rows,
        reference_selection=selections,
        summary=SkillLifecycleMatrixSummaryV1(
            expected_game_count=len(expected_game_ids),
            expected_reference_skill_count=expected_reference_count,
            latest_skill_count=len(rows),
            matched_run_count=sum(len(items) for items in runs_by_version.values()),
            unmatched_run_count=unmatched_run_count,
            selected_reference_skill_count=len(selected_ids),
            g07_ready_selected_skill_count=selected_ready_count,
            status="FAIL" if summary_reasons else "PASS",
            reasons=list(dict.fromkeys(summary_reasons)),
        ),
    )


def build_skill_lifecycle_matrix_from_store(
    store: AIPlayerStore,
    *,
    environment_ids: Sequence[str],
    expected_game_ids: Sequence[str] = ("afk-journey", "sanguo-mouding-tianxia"),
    reference_skill_count_per_game: int = REFERENCE_SKILL_COUNT_PER_GAME,
) -> SkillLifecycleMatrixV1:
    """Read latest skills and immutable runs; no AIPlayerStore write API is used."""

    game_id_by_environment: dict[str, str] = {}
    skills: list[SkillVersionV1] = []
    runs: list[SkillRunV1] = []
    for environment_id in environment_ids:
        environment = store.get_environment(environment_id)
        if environment is None:
            raise KeyError(f"unknown AI-player environment: {environment_id}")
        game_id_by_environment[environment_id] = environment.game_id
        skills.extend(store.list_skill_versions(environment_id, latest_only=True))
        runs.extend(store.list_skill_runs(environment_id))
    return build_skill_lifecycle_matrix(
        latest_skill_versions=skills,
        skill_runs=runs,
        game_id_by_environment=game_id_by_environment,
        expected_game_ids=expected_game_ids,
        reference_skill_count_per_game=reference_skill_count_per_game,
    )


__all__ = [
    "GameReferenceSkillSelectionV1",
    "ReplayReuseEvidenceV1",
    "SkillLifecycleMatrixSummaryV1",
    "SkillLifecycleMatrixV1",
    "SkillLifecycleRowV1",
    "SkillLifecycleThresholdsV1",
    "ThirdReplayCandidateEvidenceV1",
    "build_skill_lifecycle_matrix",
    "build_skill_lifecycle_matrix_from_store",
]
