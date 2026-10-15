# [OMNI] origin=codex created_by=codex intent="evaluate evidence-bound play strategy candidates"
"""Deterministic, evidence-preserving evaluation for play strategy records."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .game_modeling import (
    PlayStrategyRecordV1,
    StrategyMetricDefinitionV1,
    StrategyTrialResultV1,
)


class _StrictEvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MetricComparisonV1(_StrictEvaluationModel):
    decision_point_id: str = Field(min_length=1)
    metric_id: str = Field(min_length=1)
    baseline_value: float
    candidate_value: float
    utility_delta: float
    candidate_better: bool
    candidate_worse: bool


class StrategyEvaluationResultV1(_StrictEvaluationModel):
    strategy_record_id: str = Field(min_length=1)
    baseline_candidate_id: str = Field(min_length=1)
    selected_candidate_id: str = Field(min_length=1)
    primary_metric_ids: list[str] = Field(min_length=1)
    safety_metric_ids: list[str] = Field(default_factory=list)
    comparable_decision_point_ids: list[str] = Field(default_factory=list)
    comparisons: list[MetricComparisonV1] = Field(default_factory=list)
    retained_failed_trial_ids: list[str] = Field(default_factory=list)
    retained_aborted_trial_ids: list[str] = Field(default_factory=list)
    omitted_completed_trial_ids: list[str] = Field(default_factory=list)
    confounders: list[str] = Field(default_factory=list)
    conclusion: Literal[
        "inconclusive",
        "evaluated_not_better",
        "provisionally_better",
        "verified_better",
    ]
    conclusion_notes: list[str] = Field(min_length=1)


def _utility(metric: StrategyMetricDefinitionV1, value: float) -> float:
    if metric.direction == "maximize":
        return value
    if metric.direction == "minimize":
        return -value
    assert metric.target_value is not None
    return -abs(value - metric.target_value)


def _comparison(
    point_id: str,
    metric: StrategyMetricDefinitionV1,
    baseline: StrategyTrialResultV1,
    candidate: StrategyTrialResultV1,
) -> MetricComparisonV1:
    baseline_value = baseline.metric_values[metric.id]
    candidate_value = candidate.metric_values[metric.id]
    delta = _utility(metric, candidate_value) - _utility(metric, baseline_value)
    return MetricComparisonV1(
        decision_point_id=point_id,
        metric_id=metric.id,
        baseline_value=baseline_value,
        candidate_value=candidate_value,
        utility_delta=delta,
        candidate_better=delta > 0,
        candidate_worse=delta < 0,
    )


def evaluate_strategy(record: PlayStrategyRecordV1) -> StrategyEvaluationResultV1:
    """Compare baseline and selected trials using only predeclared metrics.

    Failed, aborted, ambiguous, and metrically incomplete trials are retained in
    the result instead of being removed from the evidence population.
    """

    if not record.selected_candidate_id:
        raise ValueError("strategy evaluation requires selected_candidate_id")
    if record.selected_candidate_id == record.baseline_candidate_id:
        raise ValueError("selected candidate must differ from the baseline")

    metrics = {metric.id: metric for metric in record.metrics}
    primary = [metric for metric in record.metrics if metric.primary]
    safety = [metric for metric in record.metrics if metric.safety_critical]
    required_metric_ids = {metric.id for metric in [*primary, *safety]}

    grouped: dict[tuple[str, str], list[StrategyTrialResultV1]] = defaultdict(list)
    failed: list[str] = []
    aborted: list[str] = []
    for trial in record.trial_results:
        if trial.status == "failed":
            failed.append(trial.id)
        elif trial.status == "aborted":
            aborted.append(trial.id)
        else:
            grouped[(trial.decision_point_id, trial.candidate_id)].append(trial)

    point_ids = sorted({trial.decision_point_id for trial in record.trial_results})
    comparable: list[str] = []
    omitted: list[str] = []
    comparisons: list[MetricComparisonV1] = []
    confounders: list[str] = []
    for point_id in point_ids:
        baseline_trials = grouped.get((point_id, record.baseline_candidate_id), [])
        candidate_trials = grouped.get((point_id, record.selected_candidate_id), [])
        pair = [*baseline_trials, *candidate_trials]
        if len(baseline_trials) != 1 or len(candidate_trials) != 1:
            omitted.extend(trial.id for trial in pair)
            if pair:
                confounders.append(
                    f"{point_id}: requires exactly one completed baseline and selected trial"
                )
            continue
        baseline_trial = baseline_trials[0]
        candidate_trial = candidate_trials[0]
        if not required_metric_ids.issubset(
            baseline_trial.metric_values
        ) or not required_metric_ids.issubset(candidate_trial.metric_values):
            omitted.extend((baseline_trial.id, candidate_trial.id))
            confounders.append(f"{point_id}: missing a predeclared primary or safety metric")
            continue
        comparable.append(point_id)
        confounders.extend(
            f"{point_id}/{baseline_trial.id}: {item}" for item in baseline_trial.confounders
        )
        confounders.extend(
            f"{point_id}/{candidate_trial.id}: {item}" for item in candidate_trial.confounders
        )
        for metric_id in sorted(required_metric_ids):
            comparisons.append(
                _comparison(point_id, metrics[metric_id], baseline_trial, candidate_trial)
            )

    primary_comparisons = [
        item for item in comparisons if item.metric_id in {m.id for m in primary}
    ]
    safety_comparisons = [item for item in comparisons if item.metric_id in {m.id for m in safety}]
    aggregate_primary_delta = sum(item.utility_delta for item in primary_comparisons)
    primary_improved = bool(primary_comparisons) and aggregate_primary_delta > 0
    safety_not_worse = not any(item.candidate_worse for item in safety_comparisons)

    if len(comparable) >= 3 and primary_improved and safety_not_worse:
        conclusion = "verified_better"
        notes = [
            f"{len(comparable)} comparable decision points use the predeclared metrics.",
            "Aggregate primary utility is better and no safety-critical metric is worse.",
        ]
    elif len(comparable) >= 3:
        conclusion = "evaluated_not_better"
        notes = [
            f"{len(comparable)} comparable decision points were retained.",
            "The selected candidate did not improve aggregate primary utility without worsening safety.",
        ]
    elif comparable and primary_improved and safety_not_worse:
        conclusion = "provisionally_better"
        notes = [
            f"Only {len(comparable)} comparable decision points are available; three are required.",
            "The current primary result is favorable but remains provisional.",
        ]
    else:
        conclusion = "inconclusive"
        notes = [
            f"Only {len(comparable)} comparable decision points are available.",
            "Incomplete, ambiguous, or unfavorable evidence prevents a better-strategy claim.",
        ]

    if not math.isfinite(aggregate_primary_delta):
        raise ValueError("strategy metric comparison produced a non-finite result")
    return StrategyEvaluationResultV1(
        strategy_record_id=record.id,
        baseline_candidate_id=record.baseline_candidate_id,
        selected_candidate_id=record.selected_candidate_id,
        primary_metric_ids=[metric.id for metric in primary],
        safety_metric_ids=[metric.id for metric in safety],
        comparable_decision_point_ids=comparable,
        comparisons=comparisons,
        retained_failed_trial_ids=failed,
        retained_aborted_trial_ids=aborted,
        omitted_completed_trial_ids=omitted,
        confounders=list(dict.fromkeys(confounders)),
        conclusion=conclusion,
        conclusion_notes=notes,
    )


def apply_strategy_evaluation(
    record: PlayStrategyRecordV1,
) -> tuple[PlayStrategyRecordV1, StrategyEvaluationResultV1]:
    result = evaluate_strategy(record)
    updated = record.model_copy(
        update={
            "conclusion": result.conclusion,
            "conclusion_notes": result.conclusion_notes,
        }
    )
    return PlayStrategyRecordV1.model_validate(updated.model_dump()), result
