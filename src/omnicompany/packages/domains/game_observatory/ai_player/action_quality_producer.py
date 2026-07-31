"""Deterministically derive one action-quality sample from canonical execution facts."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .contracts import ActionQualitySampleV1, FrontierTaskV1, TransitionEdgeV1

if TYPE_CHECKING:
    from .consolidation import CanonicalExecutionOutcomeV1, ConsolidationResultV1
    from .orchestrator import AutonomousExecutionCommandV1
    from .session_control import AIPlayerSessionV1


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActionDecisionTelemetryV1(_StrictModel):
    """Optional measured planner telemetry; absence is preserved as unavailable."""

    schema_id: Literal["game-observatory.ai-player.action-decision-telemetry.v1"] = Field(
        default="game-observatory.ai-player.action-decision-telemetry.v1",
        alias="schema",
    )
    model_input_tokens: int | None = Field(default=None, ge=0)
    model_output_tokens: int | None = Field(default=None, ge=0)
    baseline_model_input_tokens: int | None = Field(default=None, gt=0)
    decision_latency_ms: int | None = Field(default=None, ge=0)
    baseline_decision_latency_ms: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def keep_measurements_complete(self) -> "ActionDecisionTelemetryV1":
        token_values = (self.model_input_tokens, self.model_output_tokens)
        if any(value is not None for value in token_values) and not all(
            value is not None for value in token_values
        ):
            raise ValueError("input and output token measurements must be supplied together")
        if self.baseline_model_input_tokens is not None and self.model_input_tokens is None:
            raise ValueError("a token baseline requires measured input tokens")
        if self.baseline_decision_latency_ms is not None and self.decision_latency_ms is None:
            raise ValueError("a latency baseline requires measured decision latency")
        return self


class ActionQualityHistorySnapshotV1(_StrictModel):
    """Immutable pre-action graph facts retained with terminal evidence for replay."""

    schema_id: Literal["game-observatory.ai-player.action-quality-history-snapshot.v1"] = (
        Field(
            default="game-observatory.ai-player.action-quality-history-snapshot.v1",
            alias="schema",
        )
    )
    environment_id: str = Field(min_length=1)
    command_id: str = Field(min_length=1)
    request_sha256: str = Field(min_length=64, max_length=64)
    session_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    known_state_ids_before_command: list[str] = Field(default_factory=list)
    known_transition_ids_before_command: list[str] = Field(default_factory=list)
    matched_transition_ids: list[str] = Field(default_factory=list)

    @field_validator(
        "known_state_ids_before_command",
        "known_transition_ids_before_command",
        "matched_transition_ids",
    )
    @classmethod
    def require_unique_snapshot_ids(cls, values: list[str], info: object) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("history snapshot ids must be non-blank")
        if len(values) != len(set(values)):
            raise ValueError(f"{getattr(info, 'field_name', 'history snapshot ids')} must be unique")
        return values

    @model_validator(mode="after")
    def keep_matches_inside_known_history(self) -> "ActionQualityHistorySnapshotV1":
        if not set(self.matched_transition_ids).issubset(
            self.known_transition_ids_before_command
        ):
            raise ValueError("matched transitions must belong to the pre-action graph snapshot")
        return self


class ActionQualityHistoryContextV1(_StrictModel):
    """Canonical command-time history needed to identify genuinely new graph facts."""

    schema_id: Literal["game-observatory.ai-player.action-quality-history-context.v1"] = Field(
        default="game-observatory.ai-player.action-quality-history-context.v1",
        alias="schema",
    )
    environment_id: str = Field(min_length=1)
    command_id: str = Field(min_length=1)
    result_transition: TransitionEdgeV1
    known_state_ids_before_command: list[str] = Field(default_factory=list)
    known_transition_ids_before_command: list[str] = Field(default_factory=list)
    matching_prior_transitions: list[TransitionEdgeV1] = Field(default_factory=list)
    selectable_task_ids_after_consolidation: list[str] = Field(default_factory=list)

    @field_validator(
        "known_state_ids_before_command",
        "known_transition_ids_before_command",
        "selectable_task_ids_after_consolidation",
    )
    @classmethod
    def require_unique_ids(cls, values: list[str], info: object) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("history ids must be non-blank")
        if len(values) != len(set(values)):
            raise ValueError(f"{getattr(info, 'field_name', 'history ids')} must be unique")
        return values

    @model_validator(mode="after")
    def keep_history_in_one_environment(self) -> "ActionQualityHistoryContextV1":
        transitions = [self.result_transition, *self.matching_prior_transitions]
        if any(item.environment_id != self.environment_id for item in transitions):
            raise ValueError("history transitions must belong to the command environment")
        blocking = {"failed", "forbidden", "verified_no_change"}
        if any(item.outcome not in blocking for item in self.matching_prior_transitions):
            raise ValueError("matching prior transitions must be canonical blocking outcomes")
        return self


def stable_action_quality_sample_id(command_id: str) -> str:
    digest = hashlib.sha256(command_id.encode("utf-8")).hexdigest()[:32]
    return f"action-quality.autonomous-command.{digest}"


def _session_identity(session: "AIPlayerSessionV1 | BaseModel") -> tuple[str, str]:
    session_id = getattr(session, "session_id", None) or getattr(session, "id", None)
    environment_id = getattr(session, "environment_id", None)
    if not isinstance(session_id, str) or not isinstance(environment_id, str):
        raise ValueError("session context lacks canonical session and environment ids")
    return session_id, environment_id


def _target_cluster_id(
    command: "AutonomousExecutionCommandV1",
    before_state_id: str,
) -> str:
    payload = {
        "action": command.action.model_dump(mode="json"),
        "before_state_id": before_state_id,
        "target_bounds": (
            command.target_bounds.model_dump(mode="json")
            if command.target_bounds is not None
            else None
        ),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"action-cluster.{hashlib.sha256(encoded).hexdigest()[:24]}"


def _terminal_evidence_is_complete(
    outcome: "CanonicalExecutionOutcomeV1",
    consolidation: "ConsolidationResultV1",
) -> bool:
    step = outcome.evidence_step
    run = outcome.evidence_run
    action_run = outcome.action_run
    reference = consolidation.evidence_ref
    artifact_ids = {artifact.id for artifact in outcome.artifacts}
    required_artifact_ids = {
        artifact_id
        for artifact_id in (
            step.before_frame_id,
            step.after_frame_id,
            step.video_artifact_id,
            step.before_ui_tree_id,
            step.after_ui_tree_id,
            *step.intermediate_frame_ids,
        )
        if artifact_id is not None
    }
    required_roles_present = all(
        value is not None
        for value in (
            step.before_frame_id,
            step.after_frame_id,
            step.video_artifact_id,
            step.action_run_id,
            step.ended_at,
            run.ended_at,
            action_run.ended_at,
        )
    )
    return all(
        (
            required_roles_present,
            step.status != "running",
            run.status not in {"running", "paused"},
            action_run.status != "running",
            not step.quality_issues,
            required_artifact_ids.issubset(artifact_ids),
            required_artifact_ids.issubset(reference.artifact_ids),
            step.id in reference.evidence_step_ids,
            run.id in reference.evidence_run_ids,
            action_run.id in reference.trace_run_ids,
        )
    )


def _decision_mode(
    outcome: "CanonicalExecutionOutcomeV1",
    history: ActionQualityHistoryContextV1,
) -> Literal["new_state", "known_state", "skill_replay", "recovery"]:
    if outcome.recovered_from_interruption:
        return "recovery"
    if isinstance(outcome.evidence_run.environment.get("skill_validation"), dict):
        return "skill_replay"
    if outcome.before_state.id not in history.known_state_ids_before_command:
        return "new_state"
    return "known_state"


def _validate_canonical_bindings(
    *,
    command: "AutonomousExecutionCommandV1",
    outcome: "CanonicalExecutionOutcomeV1",
    consolidation: "ConsolidationResultV1",
    session: "AIPlayerSessionV1 | BaseModel",
    task: FrontierTaskV1,
    history: ActionQualityHistoryContextV1,
) -> None:
    session_id, session_environment_id = _session_identity(session)
    environment_ids = {
        command.environment_id,
        outcome.environment_id,
        consolidation.environment_id,
        task.environment_id,
        history.environment_id,
        session_environment_id,
    }
    if len(environment_ids) != 1:
        raise ValueError("action-quality inputs belong to different environments")
    if session_id != command.session_id:
        raise ValueError("session context does not match the autonomous command")
    if len({command.command_id, outcome.command_id, consolidation.command_id, history.command_id}) != 1:
        raise ValueError("action-quality inputs bind different command ids")
    if len({outcome.task_id, consolidation.task_id, task.id}) != 1:
        raise ValueError("action-quality inputs bind different task ids")
    if outcome.evidence_step.action != command.action and not (
        outcome.recovered_from_interruption
        and outcome.evidence_step.action.type == "wait"
    ):
        raise ValueError("canonical evidence action does not match the command or safe recovery")
    if outcome.action_run.task_id != task.id:
        raise ValueError("canonical action run does not match the task")
    if consolidation.before_state_id != outcome.before_state.id or (
        consolidation.after_state_id != outcome.after_state.id
    ):
        raise ValueError("consolidated semantic states do not match the terminal outcome")

    transition = history.result_transition
    if transition.id != consolidation.transition_edge_id:
        raise ValueError("history result transition does not match consolidation")
    if transition.from_state_id != consolidation.before_state_id or (
        transition.to_state_id not in {None, consolidation.after_state_id}
    ):
        raise ValueError("result transition does not match consolidated semantic states")
    if transition.action != command.action or transition.expected_change != task.reason:
        raise ValueError("result transition does not match the command and task")
    if transition.observed_change != outcome.observed_change:
        raise ValueError("result transition does not match the observed change")
    expected_edge_outcome = (
        "failed"
        if outcome.status == "failed"
        else (
            "deferred"
            if outcome.before_state.status != "accepted"
            or outcome.after_state.status != "accepted"
            else (
                "verified_transition"
                if outcome.before_state.id != outcome.after_state.id
                else "verified_no_change"
            )
        )
    )
    if transition.outcome != expected_edge_outcome:
        raise ValueError("result transition outcome contradicts canonical execution")
    expected_task_status = "completed" if outcome.status == "succeeded" else "failed"
    if consolidation.task_status != expected_task_status:
        raise ValueError("consolidated task status contradicts canonical execution")

    for prior in history.matching_prior_transitions:
        if prior.from_state_id != outcome.before_state.id or prior.action != command.action:
            raise ValueError("matching prior transitions do not belong to this action cluster")


def produce_action_quality_sample(
    *,
    command: "AutonomousExecutionCommandV1",
    outcome: "CanonicalExecutionOutcomeV1",
    consolidation: "ConsolidationResultV1",
    session: "AIPlayerSessionV1 | BaseModel",
    task: FrontierTaskV1,
    history: ActionQualityHistoryContextV1,
    telemetry: ActionDecisionTelemetryV1 | None = None,
) -> ActionQualitySampleV1:
    """Produce an idempotent sample after evidence consolidation has succeeded."""

    _validate_canonical_bindings(
        command=command,
        outcome=outcome,
        consolidation=consolidation,
        session=session,
        task=task,
        history=history,
    )
    telemetry = telemetry or ActionDecisionTelemetryV1()
    measured_tokens = telemetry.model_input_tokens is not None
    new_state_count = int(
        outcome.after_state.id not in history.known_state_ids_before_command
        and outcome.after_state.id != outcome.before_state.id
    )
    new_transition_count = int(
        consolidation.transition_edge_id not in history.known_transition_ids_before_command
    )
    task_progress = consolidation.task_status == "completed"
    objective_completed = task_progress
    meaningful_change = any(
        (
            outcome.before_state.id != outcome.after_state.id,
            task_progress,
            objective_completed,
            new_state_count > 0,
            new_transition_count > 0,
        )
    )
    created_at = (
        outcome.evidence_step.ended_at
        or outcome.evidence_step.action_ended_at
        or outcome.action_run.ended_at
        or outcome.evidence_run.ended_at
        or outcome.evidence_step.started_at
    )

    return ActionQualitySampleV1(
        id=stable_action_quality_sample_id(command.command_id),
        environment_id=command.environment_id,
        evidence_refs=[consolidation.evidence_ref],
        session_id=command.session_id,
        task_id=task.id,
        semantic_state_id=outcome.before_state.id,
        command_id=command.command_id,
        action_run_id=outcome.action_run.id,
        evidence_step_id=outcome.evidence_step.id,
        decision_mode=_decision_mode(outcome, history),
        execution_disposition="executed",
        preflight_disposition=(
            "passed" if command.interaction_preflight is not None else "not_applicable"
        ),
        outcome="confirmed" if outcome.status == "succeeded" else "failed",
        expected_change=(
            json.dumps(
                command.interaction_preflight.expected_change.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if command.interaction_preflight is not None
            else task.reason
        ),
        adapter_call_count=1,
        invalid_target_execution=False,
        policy_violation=False,
        evidence_complete=_terminal_evidence_is_complete(outcome, consolidation),
        meaningful_change=meaningful_change,
        task_progress=task_progress,
        objective_completed=objective_completed,
        information_gain_units=new_state_count + new_transition_count,
        new_state_count=new_state_count,
        new_transition_count=new_transition_count,
        target_cluster_id=_target_cluster_id(command, outcome.before_state.id),
        prior_cluster_failures=len(history.matching_prior_transitions),
        recovery_succeeded=outcome.recovered_from_interruption
        and outcome.status == "succeeded",
        task_queue_falsely_empty=consolidation.next_task_id is None
        and bool(history.selectable_task_ids_after_consolidation),
        token_measurement_status="measured" if measured_tokens else "unavailable",
        model_input_tokens=telemetry.model_input_tokens,
        model_output_tokens=telemetry.model_output_tokens,
        baseline_model_input_tokens=telemetry.baseline_model_input_tokens,
        decision_latency_ms=telemetry.decision_latency_ms,
        baseline_decision_latency_ms=telemetry.baseline_decision_latency_ms,
        created_at=created_at,
    )


__all__ = [
    "ActionDecisionTelemetryV1",
    "ActionQualityHistoryContextV1",
    "produce_action_quality_sample",
    "stable_action_quality_sample_id",
]
