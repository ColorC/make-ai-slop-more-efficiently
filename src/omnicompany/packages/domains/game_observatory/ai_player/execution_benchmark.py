# [OMNI] origin=codex created_by=codex intent="derive honest end-to-end AI player execution benchmarks"
"""Build execution benchmarks only from canonical AI-player receipts."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from .contracts import EvidenceReferenceV1
from ..game_modeling import (
    ChainKind,
    ExecutionAttemptV1,
    ExecutionBatchV1,
    ExecutionBenchmarkV1,
    ExecutionSafetyReceiptV1,
    FacilityTimingV1,
    RecoveryEventV1,
)


def build_execution_batch(
    *,
    batch_id: str,
    game_id: str,
    environment_id: str,
    session_id: str,
    chain_kind: ChainKind,
    route_id: str,
    planned_action_count: int,
    legal_business_terminal_reached: bool,
    branch_decisions: Sequence[str],
    knowledge_outcomes: Sequence[str],
    timing: FacilityTimingV1,
    attempts: Sequence[ExecutionAttemptV1],
    recovery_events: Sequence[RecoveryEventV1],
    safety: ExecutionSafetyReceiptV1,
    evidence_refs: Sequence[EvidenceReferenceV1],
) -> ExecutionBatchV1:
    """Summarize retained Session/telemetry/EvidenceStep/route result records.

    This function performs no device, recognition, memory, session, or blob I/O;
    those remain owned by the canonical Game Observatory facilities.
    """

    attempt_list = list(attempts)
    recovery_list = list(recovery_events)
    effective_count = sum(item.outcome == "effective" for item in attempt_list)
    no_change_count = sum(item.outcome == "no_change" for item in attempt_list)
    rejected_count = sum(item.outcome == "rejected" for item in attempt_list)
    failed_count = sum(item.outcome == "failed" for item in attempt_list)
    recovered_count = sum(item.outcome == "recovered" for item in recovery_list)
    canonical_source_gap_count = sum(bool(item.canonical_source_gaps) for item in attempt_list)
    expected_hits = sum(
        item.expectation_met and item.business_state_changed for item in attempt_list
    )
    count = len(attempt_list)
    if not count:
        raise ValueError("execution batch requires canonical attempts")
    hit_rate = expected_hits / count
    no_change_rate = no_change_count / count
    throughput = effective_count * 60 / timing.wall_clock_seconds

    if chain_kind == "high_repetition":
        passed = (
            planned_action_count >= 10
            and throughput >= 10.0
            and hit_rate >= 0.95
            and no_change_rate <= 0.02
            and canonical_source_gap_count == 0
            and safety.is_clean()
        )
        gate_status = "passed" if passed else "failed"
    elif chain_kind == "semi_fixed":
        passed = (
            planned_action_count >= 5
            and bool(branch_decisions)
            and throughput >= 5.0
            and hit_rate >= 0.90
            and no_change_rate <= 0.05
            and canonical_source_gap_count == 0
            and safety.is_clean()
        )
        gate_status = "passed" if passed else "failed"
    else:
        gate_status = "not_applicable"

    return ExecutionBatchV1(
        id=batch_id,
        game_id=game_id,
        environment_id=environment_id,
        session_id=session_id,
        chain_kind=chain_kind,
        route_id=route_id,
        planned_action_count=planned_action_count,
        canonical_attempt_count=count,
        legal_business_terminal_reached=legal_business_terminal_reached,
        branch_decisions=list(branch_decisions),
        knowledge_outcomes=list(knowledge_outcomes),
        timing=timing,
        attempts=attempt_list,
        recovery_events=recovery_list,
        safety=safety,
        effective_action_count=effective_count,
        no_change_count=no_change_count,
        rejected_count=rejected_count,
        failed_count=failed_count,
        recovered_count=recovered_count,
        canonical_source_gap_count=canonical_source_gap_count,
        expected_effect_hit_count=expected_hits,
        expected_effect_hit_rate=hit_rate,
        no_change_rate=no_change_rate,
        effective_actions_per_minute=throughput,
        gate_status=gate_status,
        evidence_refs=list(evidence_refs),
    )


def summarize_execution_benchmark(
    *,
    benchmark_id: str,
    game_id: str,
    environment_id: str,
    workload_kind: ChainKind,
    batches: Sequence[ExecutionBatchV1],
    notes: Sequence[str],
    updated_at: datetime,
) -> ExecutionBenchmarkV1:
    retained = list(batches)
    if not retained:
        raise ValueError("execution benchmark requires at least one retained batch")
    if workload_kind == "unknown_exploration":
        target = None
        gate_status = "not_applicable"
    else:
        target = 10.0 if workload_kind == "high_repetition" else 5.0
        trailing_passes = 0
        for batch in reversed(retained):
            if batch.gate_status != "passed":
                break
            trailing_passes += 1
        if trailing_passes >= 3:
            gate_status = "passed"
        elif any(batch.gate_status == "failed" for batch in retained):
            gate_status = "failed"
        elif len(retained) < 3:
            gate_status = "in_progress"
        else:
            gate_status = "failed"
    return ExecutionBenchmarkV1(
        id=benchmark_id,
        game_id=game_id,
        environment_id=environment_id,
        workload_kind=workload_kind,
        target_effective_actions_per_minute=target,
        consecutive_passes_required=3,
        batches=retained,
        gate_status=gate_status,
        notes=list(notes),
        updated_at=updated_at,
    )
