"""Ingest every complete gameplay action produced by one external invocation.

The canonical session only retains its most recent evidence reference.  That is a
useful resume cursor, but it is not an action ledger.  This module queries the
immutable invocation ownership recorded on live EvidenceRuns so a multi-action
provider turn is sedimented in action order without consulting the model summary.

Terminal-failed actions use a deliberately separate path: their complete evidence
can create a ``failed`` transition edge from an already known source state, but it
cannot create observations, state assignments, semantic-state candidates, or a
verified transition.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..models import LIFECYCLE_ACTION_TYPES, EvidenceRun, EvidenceStep
from ..store import ObservatoryStore
from .contracts import EvidenceReferenceV1, TransitionEdgeV1
from .deferred_sedimentation import settle_deferred_skill_runs
from .external_agent_runtime import ExternalAgentInvocationV1
from .live_evidence_state_ingest import (
    LiveEvidenceStateAutoIngestResultV1,
    auto_ingest_terminal_evidence_runs,
)
from .store import AIPlayerStore


class InvocationEvidenceItemV1(BaseModel):
    """One ordered canonical action discovered from invocation-owned evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ordinal: int = Field(ge=1)
    evidence_run_id: str = Field(min_length=1)
    evidence_step_id: str = Field(min_length=1)
    terminal_status: Literal["passed", "failed"]
    disposition: Literal["state_ingested", "failed_edge", "skipped"]
    transition_edge_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)


class InvocationEvidenceQueryItemV1(BaseModel):
    """Read-only classification of one invocation-owned EvidenceStep."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ordinal: int = Field(ge=1)
    evidence_run_id: str = Field(min_length=1)
    evidence_step_id: str = Field(min_length=1)
    terminal_status: Literal["passed", "failed", "incomplete"]
    complete: bool
    intended_disposition: Literal["state_ingest", "failed_edge", "skip"]
    issues: list[str] = Field(default_factory=list)


class InvocationEvidenceQueryResultV1(BaseModel):
    """Ordered canonical evidence inventory independent from session resume cursors."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    environment_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    invocation_id: str = Field(min_length=1)
    invocation_sequence: int = Field(ge=1)
    items: list[InvocationEvidenceQueryItemV1] = Field(default_factory=list)


class InvocationStateIngestResultV1(BaseModel):
    """Idempotent audit receipt for one invocation-wide state sedimentation pass."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal[
        "game-observatory.ai-player.invocation-state-ingest-result.v1"
    ] = Field(
        default="game-observatory.ai-player.invocation-state-ingest-result.v1",
        alias="schema",
    )
    environment_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    invocation_id: str = Field(min_length=1)
    invocation_sequence: int = Field(ge=1)
    ordered_evidence_run_ids: list[str] = Field(default_factory=list)
    ordered_evidence_step_ids: list[str] = Field(default_factory=list)
    items: list[InvocationEvidenceItemV1] = Field(default_factory=list)
    state_ingest_results: list[LiveEvidenceStateAutoIngestResultV1] = Field(
        default_factory=list
    )
    inserted_state_observation_count: int = Field(ge=0)
    inserted_state_assignment_count: int = Field(ge=0)
    inserted_semantic_state_version_count: int = Field(ge=0)
    inserted_transition_edge_version_count: int = Field(ge=0)


@dataclass(frozen=True)
class _InvocationEvidenceBundle:
    run: EvidenceRun
    step: EvidenceStep


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"{prefix}.{digest}"


def _artifact_hash_is_current(observatory: ObservatoryStore, artifact_id: str) -> bool:
    artifact = observatory.get_artifact(artifact_id)
    if artifact is None:
        return False
    path = Path(artifact.path).resolve()
    artifact_root = observatory.artifact_root.resolve()
    if path != artifact_root and artifact_root not in path.parents:
        return False
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == artifact.sha256


def _structural_step_issues(
    observatory: ObservatoryStore,
    run: EvidenceRun,
    step: EvidenceStep,
) -> list[str]:
    """Return issues that make terminal evidence unsafe for state learning.

    A failed expectation and its failed terminal condition are legitimate facts for
    a failed edge.  Missing/unstable/tampered Before-Action-After evidence is not.
    """

    issues: list[str] = []
    if run.status not in {"passed", "failed"} or not run.ended_at:
        issues.append("EvidenceRun is not terminal passed/failed")
    if step.status not in {"passed", "failed"} or not step.ended_at:
        issues.append("EvidenceStep is not terminal passed/failed")
    if step.evidence_run_id != run.id or step.id not in run.step_ids:
        issues.append("EvidenceRun/EvidenceStep relationship mismatch")
    if step.action.type in LIFECYCLE_ACTION_TYPES:
        issues.append("lifecycle action is not semantic gameplay state evidence")
    if run.environment.get("semantic_state_eligible") is False:
        issues.append("EvidenceRun is marked semantic_state_eligible=false")
    if step.metadata.get("semantic_state_eligible") is False:
        issues.append("EvidenceStep is marked semantic_state_eligible=false")
    if not step.before_frame_id or not step.after_frame_id or not step.action_run_id:
        issues.append("Before/Action/After evidence is incomplete")
    elif step.action_run_id not in run.action_run_ids:
        issues.append("action run is absent from EvidenceRun")
    else:
        action_run = observatory.get_run(step.action_run_id)
        if action_run is None or action_run.status not in {"passed", "failed"}:
            issues.append("canonical action run is missing or non-terminal")
        elif action_run.task_id != run.environment.get("task_id"):
            issues.append("canonical action run task provenance mismatch")
    if not step.stability.settled:
        issues.append("after state is not settled")
    ignored_failed_issues = {
        f"{step.id}: step status is failed",
        f"{step.id}: declared terminal condition was not satisfied",
    }
    issues.extend(
        issue
        for issue in step.publication_issues()
        if not (step.status == "failed" and issue in ignored_failed_issues)
    )
    required_artifact_ids = {
        item for item in (step.before_frame_id, step.after_frame_id) if item is not None
    }
    if not required_artifact_ids.issubset(set(step.artifact_ids)):
        issues.append("required screenshots are absent from EvidenceStep artifacts")
    if not required_artifact_ids.issubset(set(run.artifact_ids)):
        issues.append("required screenshots are absent from EvidenceRun artifacts")
    expected_roles = {
        step.before_frame_id: "before",
        step.after_frame_id: "after",
    }
    for artifact_id in sorted(required_artifact_ids):
        if not _artifact_hash_is_current(observatory, artifact_id):
            issues.append(f"artifact is missing, outside the store, or hash-drifted: {artifact_id}")
            continue
        artifact = observatory.get_artifact(artifact_id)
        expected_metadata = {
            "environment_id": run.scope_id,
            "evidence_run_id": run.id,
            "evidence_step_id": step.id,
            "evidence_role": expected_roles[artifact_id],
        }
        if artifact is None or any(
            artifact.metadata.get(key) != value for key, value in expected_metadata.items()
        ):
            issues.append(f"artifact provenance mismatch: {artifact_id}")
    manifest = observatory.get_evidence_manifest(run.id)
    if manifest is None:
        issues.append("canonical EvidenceRun manifest is missing")
    elif manifest.run != run or step not in manifest.steps:
        issues.append("canonical EvidenceRun manifest differs from run/step facts")
    elif (
        not required_artifact_ids.issubset(set(manifest.artifact_ids))
        or step.action_run_id not in manifest.action_run_ids
    ):
        issues.append("canonical EvidenceRun manifest omits action evidence")
    return list(dict.fromkeys(issues))


def _owned_invocation_bundles(
    observatory: ObservatoryStore,
    *,
    environment_id: str,
    invocation: ExternalAgentInvocationV1,
) -> list[_InvocationEvidenceBundle]:
    if invocation.status not in {"succeeded", "failed", "timed_out"}:
        raise ValueError("external invocation is not terminal")
    invocation_start = _timestamp(invocation.started_at)
    invocation_end = _timestamp(invocation.completed_at)
    candidates = [
        run
        for run in observatory.list_evidence_runs(limit=1000)
        if run.environment.get("external_agent_invocation_id") == invocation.id
    ]
    bundles: list[_InvocationEvidenceBundle] = []
    for run in candidates:
        expected_owner = {
            "caller": "ai-player-live-step",
            "environment_id": environment_id,
            "ai_player_session_id": invocation.session_id,
            "external_agent_invocation_id": invocation.id,
            "external_agent_session_id": invocation.session_id,
            "external_agent_invocation_sequence": str(invocation.sequence),
        }
        if any(run.environment.get(key) != value for key, value in expected_owner.items()):
            raise ValueError(f"external live evidence ownership mismatch: {run.id}")
        if run.scope_id != environment_id:
            raise ValueError(f"external live evidence scope mismatch: {run.id}")
        for step_id in run.step_ids:
            step = observatory.get_evidence_step(step_id)
            if step is None:
                raise ValueError(f"external live EvidenceStep is missing: {step_id}")
            action_start = _timestamp(step.action_started_at or step.started_at)
            action_end_value = step.action_ended_at or step.ended_at
            action_end = _timestamp(action_end_value) if action_end_value is not None else None
            if not (
                invocation_start <= action_start <= invocation_end
                and (action_end is None or action_start <= action_end <= invocation_end)
            ):
                raise ValueError(f"external live evidence escapes invocation time: {step.id}")
            bundles.append(_InvocationEvidenceBundle(run=run, step=step))
    return sorted(
        bundles,
        key=lambda item: (
            _timestamp(item.step.action_started_at or item.step.started_at),
            item.step.step_index,
            item.step.id,
        ),
    )


def _state_id_for_source_artifact(
    player: AIPlayerStore,
    *,
    environment_id: str,
    artifact_id: str,
) -> str | None:
    matches = []
    for assignment in player.list_state_assignments(environment_id):
        if assignment.status != "active":
            continue
        observation = player.get_state_observation(environment_id, assignment.observation_id)
        if observation is None:
            continue
        if any(artifact_id in reference.artifact_ids for reference in observation.evidence_refs):
            state = player.get_semantic_state(environment_id, assignment.state_id)
            if state is not None and state.status == "accepted":
                matches.append(assignment)
    return matches[-1].state_id if matches else None


def query_external_invocation_state_evidence(
    store_root: Path,
    *,
    environment_id: str,
    invocation: ExternalAgentInvocationV1,
) -> InvocationEvidenceQueryResultV1:
    """Return the full ordered invocation evidence set without writing state data."""

    observatory = ObservatoryStore(store_root.resolve())
    bundles = _owned_invocation_bundles(
        observatory,
        environment_id=environment_id,
        invocation=invocation,
    )
    items: list[InvocationEvidenceQueryItemV1] = []
    for ordinal, bundle in enumerate(bundles, start=1):
        issues = _structural_step_issues(observatory, bundle.run, bundle.step)
        failed = bundle.run.status == "failed" or bundle.step.status == "failed"
        complete = not issues
        terminal_status: Literal["passed", "failed", "incomplete"] = (
            "incomplete"
            if bundle.run.status not in {"passed", "failed"}
            or bundle.step.status not in {"passed", "failed"}
            else "failed"
            if failed
            else "passed"
        )
        items.append(
            InvocationEvidenceQueryItemV1(
                ordinal=ordinal,
                evidence_run_id=bundle.run.id,
                evidence_step_id=bundle.step.id,
                terminal_status=terminal_status,
                complete=complete,
                intended_disposition=(
                    "skip" if not complete else "failed_edge" if failed else "state_ingest"
                ),
                issues=issues,
            )
        )
    return InvocationEvidenceQueryResultV1(
        environment_id=environment_id,
        session_id=invocation.session_id,
        invocation_id=invocation.id,
        invocation_sequence=invocation.sequence,
        items=items,
    )


def _failed_edge_reference(
    environment_id: str,
    run: EvidenceRun,
    step: EvidenceStep,
) -> EvidenceReferenceV1:
    return EvidenceReferenceV1(
        environment_id=environment_id,
        artifact_ids=list(
            dict.fromkeys(
                item
                for item in (step.before_frame_id, step.after_frame_id)
                if item is not None
            )
        ),
        evidence_run_ids=[run.id],
        evidence_step_ids=[step.id],
        trace_run_ids=[step.action_run_id] if step.action_run_id else [],
        note="完整的失败动作 Before/Action/After 证据；只记录 failed edge。",
    )


def _put_failed_edge(
    player: AIPlayerStore,
    *,
    environment_id: str,
    run: EvidenceRun,
    step: EvidenceStep,
) -> TransitionEdgeV1:
    guard = run.environment.get("source_state_guard")
    source_artifact_id = guard.get("artifact_id") if isinstance(guard, dict) else None
    if not isinstance(source_artifact_id, str) or not source_artifact_id:
        raise ValueError(f"failed action has no source-state artifact guard: {step.id}")
    source_artifact_sha256 = guard.get("artifact_sha256") if isinstance(guard, dict) else None
    source_artifact = player.observatory_store.get_artifact(source_artifact_id)
    if (
        source_artifact is None
        or source_artifact_sha256 != source_artifact.sha256
        or not _artifact_hash_is_current(player.observatory_store, source_artifact_id)
    ):
        raise ValueError(f"failed action source-state artifact guard is not hash-current: {step.id}")
    source_state_id = _state_id_for_source_artifact(
        player,
        environment_id=environment_id,
        artifact_id=source_artifact_id,
    )
    if source_state_id is None:
        raise ValueError(
            f"failed action source-state artifact lacks an accepted assignment: {step.id}"
        )
    edge = TransitionEdgeV1(
        id=_stable_id("edge.failed-evidence", environment_id, run.id, step.id),
        environment_id=environment_id,
        from_state_id=source_state_id,
        to_state_id=None,
        action=step.action,
        target_bounds=step.target_bounds,
        expected_change=str(
            run.environment.get("pre_execution_expectation")
            or f"执行 {step.action.type} 后出现预期的可见反馈"
        ),
        observed_change="动作证据完整但未达到预期；未据此建立或验证目标状态。",
        outcome="failed",
        evidence_refs=[_failed_edge_reference(environment_id, run, step)],
        created_at=str(step.ended_at),
    )
    return player.put_transition_edge(edge)


def _sum_inserted(
    results: Sequence[LiveEvidenceStateAutoIngestResultV1],
    field: str,
) -> int:
    return sum(int(getattr(item.result, field)) for item in results if item.result is not None)


def ingest_external_invocation_state_evidence(
    store_root: Path,
    *,
    environment_id: str,
    invocation: ExternalAgentInvocationV1,
) -> InvocationStateIngestResultV1:
    """Query and idempotently ingest all complete actions from one invocation.

    Ordering comes only from canonical EvidenceStep timestamps.  The provider's
    prose output and the canonical session's single resume cursor are intentionally
    absent from this boundary.
    """

    resolved_root = store_root.resolve()
    observatory = ObservatoryStore(resolved_root)
    player = AIPlayerStore(observatory)
    environment = player.get_environment(environment_id)
    if environment is None:
        raise KeyError(f"unknown AI-player environment: {environment_id}")
    # The canonical SkillRun/EvidenceRun ledger is the crash-recovery queue.
    # Drain any route work left by a process exit before handling this turn's
    # ordinary invocation-owned actions.  Failure propagates to the outer
    # session boundary and therefore cannot silently enable an unsettled route.
    settle_deferred_skill_runs(
        resolved_root,
        environment_id=environment_id,
    )
    bundles = _owned_invocation_bundles(
        observatory,
        environment_id=environment_id,
        invocation=invocation,
    )
    items: list[InvocationEvidenceItemV1] = []
    ingest_results: list[LiveEvidenceStateAutoIngestResultV1] = []
    for ordinal, bundle in enumerate(bundles, start=1):
        run, step = bundle.run, bundle.step
        issues = _structural_step_issues(observatory, run, step)
        if issues:
            items.append(
                InvocationEvidenceItemV1(
                    ordinal=ordinal,
                    evidence_run_id=run.id,
                    evidence_step_id=step.id,
                    terminal_status=(
                        step.status if step.status in {"passed", "failed"} else "failed"
                    ),
                    disposition="skipped",
                    reason="; ".join(issues),
                )
            )
            continue
        failed = run.status == "failed" or step.status == "failed"
        if failed:
            try:
                edge = _put_failed_edge(
                    player,
                    environment_id=environment_id,
                    run=run,
                    step=step,
                )
            except ValueError as exc:
                items.append(
                    InvocationEvidenceItemV1(
                        ordinal=ordinal,
                        evidence_run_id=run.id,
                        evidence_step_id=step.id,
                        terminal_status="failed",
                        disposition="skipped",
                        reason=str(exc),
                    )
                )
                continue
            items.append(
                InvocationEvidenceItemV1(
                    ordinal=ordinal,
                    evidence_run_id=run.id,
                    evidence_step_id=step.id,
                    terminal_status="failed",
                    disposition="failed_edge",
                    transition_edge_ids=[edge.id],
                    reason="失败动作只保留 failed edge，未写入状态观察或候选。",
                )
            )
            continue
        # Canonical ai-player-live-step runs contain one action.  Refuse a
        # surprising multi-step shape rather than ingesting the same run twice.
        if len(run.step_ids) != 1:
            raise ValueError(f"external live EvidenceRun must contain one step: {run.id}")
        outcome = auto_ingest_terminal_evidence_runs(
            resolved_root,
            environment_id=environment_id,
            evidence_run_ids=[run.id],
        )
        ingest_results.append(outcome)
        edge_ids = outcome.result.transition_edge_ids if outcome.result is not None else []
        items.append(
            InvocationEvidenceItemV1(
                ordinal=ordinal,
                evidence_run_id=run.id,
                evidence_step_id=step.id,
                terminal_status="passed",
                disposition="state_ingested",
                transition_edge_ids=edge_ids,
                reason=outcome.reason,
            )
        )

    return InvocationStateIngestResultV1(
        environment_id=environment_id,
        session_id=invocation.session_id,
        invocation_id=invocation.id,
        invocation_sequence=invocation.sequence,
        ordered_evidence_run_ids=[item.run.id for item in bundles],
        ordered_evidence_step_ids=[item.step.id for item in bundles],
        items=items,
        state_ingest_results=ingest_results,
        inserted_state_observation_count=_sum_inserted(
            ingest_results, "inserted_state_observation_count"
        ),
        inserted_state_assignment_count=_sum_inserted(
            ingest_results, "inserted_state_assignment_count"
        ),
        inserted_semantic_state_version_count=_sum_inserted(
            ingest_results, "inserted_semantic_state_version_count"
        ),
        inserted_transition_edge_version_count=_sum_inserted(
            ingest_results, "inserted_transition_edge_version_count"
        ),
    )
