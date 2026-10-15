# [OMNI] origin=codex created_by=codex intent="define evidence-bound game modeling contracts"
"""Versioned contracts and deterministic tooling for Game Observatory models.

This module validates and projects game knowledge. It intentionally does not own
device control, recognition, sessions, telemetry, operation memory, evidence storage,
or CAS; references point to the existing AI-player authority contracts.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .ai_player.account_policy import AccountActionIntentV1
from .ai_player.contracts import EvidenceReferenceV1
from .models import NormalizedAction, SourcePixelPoint, SourcePixelRect

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
DEFAULT_SCHEMA_DIR = Path(__file__).with_name("schemas")
FactStatus: TypeAlias = Literal["verified", "inferred", "unknown", "contradicted"]
MergeDisposition: TypeAlias = Literal["confirmed", "extended", "corrected", "refuted", "unresolved"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)


class ApplicabilityScopeV1(_StrictModel):
    environment_id: str = Field(min_length=1)
    game_id: str = Field(min_length=1)
    build_scope_id: str = Field(min_length=1)
    game_version: str = Field(min_length=1)
    account_scope_id: str | None = Field(default=None, min_length=1)
    locale: str | None = Field(default=None, min_length=1)
    valid_from: datetime | None = None
    valid_through: datetime | None = None
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_window(self) -> "ApplicabilityScopeV1":
        if self.valid_from and self.valid_through and self.valid_through < self.valid_from:
            raise ValueError("valid_through must not precede valid_from")
        return self


class FactRecordV1(_StrictModel):
    id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    status: FactStatus
    applicability: ApplicabilityScopeV1
    evidence_refs: list[EvidenceReferenceV1] = Field(default_factory=list)
    rationale: str | None = Field(default=None, min_length=1)
    conflict_fact_ids: list[str] = Field(default_factory=list)
    supersedes_fact_ids: list[str] = Field(default_factory=list)
    recorded_at: datetime

    @field_validator("conflict_fact_ids", "supersedes_fact_ids")
    @classmethod
    def unique_fact_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(not item.strip() for item in value):
            raise ValueError("fact id references must be unique and non-empty")
        return value

    @model_validator(mode="after")
    def enforce_evidence_semantics(self) -> "FactRecordV1":
        if any(
            ref.environment_id != self.applicability.environment_id for ref in self.evidence_refs
        ):
            raise ValueError("fact evidence must use the applicability environment")
        if self.status == "verified" and not self.evidence_refs:
            raise ValueError("verified facts require canonical evidence")
        if self.status in {"inferred", "unknown"} and not self.rationale:
            raise ValueError(f"{self.status} facts require a rationale")
        if self.status == "contradicted" and (not self.evidence_refs or not self.conflict_fact_ids):
            raise ValueError("contradicted facts require evidence and conflict_fact_ids")
        return self


class SystemStateV1(_StrictModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    facts: list[FactRecordV1] = Field(default_factory=list)


class SystemTransitionV1(_StrictModel):
    id: str = Field(min_length=1)
    from_state_id: str = Field(min_length=1)
    to_state_id: str = Field(min_length=1)
    trigger: str = Field(min_length=1)
    preconditions: list[str] = Field(default_factory=list)
    effects: list[str] = Field(default_factory=list)
    facts: list[FactRecordV1] = Field(min_length=1)


class ResourceFlowV1(_StrictModel):
    id: str = Field(min_length=1)
    direction: Literal["input", "consume", "produce", "reward", "convert"]
    resource_entity_id: str = Field(min_length=1)
    quantity_expression: str | None = Field(default=None, min_length=1)
    conditions: list[str] = Field(default_factory=list)
    facts: list[FactRecordV1] = Field(min_length=1)


class SystemRelationV1(_StrictModel):
    id: str = Field(min_length=1)
    relation_type: str = Field(min_length=1)
    target_system_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    facts: list[FactRecordV1] = Field(min_length=1)


class FailureRecoveryBoundaryV1(_StrictModel):
    id: str = Field(min_length=1)
    kind: Literal[
        "failure", "invalid", "cooldown", "depletion", "recovery", "irreversible", "safety"
    ]
    condition: str = Field(min_length=1)
    response: str = Field(min_length=1)
    facts: list[FactRecordV1] = Field(min_length=1)


class UnknownQuestionV1(_StrictModel):
    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    priority: Literal["critical", "high", "medium", "low"]
    next_observation: str = Field(min_length=1)
    stop_reason: str | None = Field(default=None, min_length=1)
    related_fact_ids: list[str] = Field(default_factory=list)


class GameSystemDossierV1(_StrictModel):
    schema_id: Literal["game-observatory.game-system-dossier.v1"] = Field(
        default="game-observatory.game-system-dossier.v1", alias="schema"
    )
    id: str = Field(min_length=1)
    game_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    classification: Literal["core_loop", "adjacent", "top_level", "read_only", "unknown"]
    modeling_status: Literal["modeled", "partial", "read_only_boundary", "unknown"]
    objective: list[FactRecordV1] = Field(min_length=1)
    entry_and_unlock: list[FactRecordV1] = Field(min_length=1)
    states: list[SystemStateV1] = Field(min_length=1)
    transitions: list[SystemTransitionV1] = Field(default_factory=list)
    resource_flows: list[ResourceFlowV1] = Field(default_factory=list)
    relations: list[SystemRelationV1] = Field(default_factory=list)
    failure_recovery_boundaries: list[FailureRecoveryBoundaryV1] = Field(min_length=1)
    unknown_questions: list[UnknownQuestionV1] = Field(default_factory=list)
    source_document_ids: list[str] = Field(default_factory=list)
    updated_at: datetime

    @model_validator(mode="after")
    def enforce_identity_and_coverage(self) -> "GameSystemDossierV1":
        facts = list(iter_dossier_facts(self))
        if any(f.applicability.game_id != self.game_id for f in facts):
            raise ValueError("dossier facts must use the dossier game_id")
        if any(f.applicability.environment_id != self.environment_id for f in facts):
            raise ValueError("dossier facts must use the dossier environment_id")
        state_ids = [state.id for state in self.states]
        if len(state_ids) != len(set(state_ids)):
            raise ValueError("system state ids must be unique")
        missing = sorted(
            {
                sid
                for t in self.transitions
                for sid in (t.from_state_id, t.to_state_id)
                if sid not in state_ids
            }
        )
        if missing:
            raise ValueError(
                "transition endpoints must refer to dossier states: " + ", ".join(missing)
            )
        if self.modeling_status == "modeled" and self.unknown_questions:
            raise ValueError("a modeled dossier must not retain unresolved unknown questions")
        return self


class SystemRegistryEntryV1(_StrictModel):
    system_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    classification: Literal["core_loop", "adjacent", "top_level", "read_only", "unknown"]
    modeling_status: Literal["modeled", "partial", "read_only_boundary", "unknown"]
    dossier_path: str | None = Field(default=None, min_length=1)
    evidence_fact_ids: list[str] = Field(default_factory=list)
    unknown_question_ids: list[str] = Field(default_factory=list)


class GameSystemRegistryV1(_StrictModel):
    schema_id: Literal["game-observatory.game-system-registry.v1"] = Field(
        default="game-observatory.game-system-registry.v1", alias="schema"
    )
    game_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    systems: list[SystemRegistryEntryV1] = Field(min_length=1)
    updated_at: datetime

    @model_validator(mode="after")
    def require_unique_systems(self) -> "GameSystemRegistryV1":
        ids = [item.system_id for item in self.systems]
        if len(ids) != len(set(ids)):
            raise ValueError("system registry ids must be unique")
        return self


PredictionDisposition: TypeAlias = Literal["pending", "confirmed", "corrected", "refuted"]


class NextStatePredictionV1(_StrictModel):
    schema_id: Literal["game-observatory.next-state-prediction.v1"] = Field(
        default="game-observatory.next-state-prediction.v1", alias="schema"
    )
    id: str = Field(min_length=1)
    game_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    system_id: str = Field(min_length=1)
    unknown_question_ids: list[str] = Field(min_length=1)
    source_fact_ids: list[str] = Field(min_length=1)
    source_evidence_refs: list[EvidenceReferenceV1] = Field(min_length=1)
    current_state_condition: str = Field(min_length=1)
    proposed_observation: str = Field(min_length=1)
    predicted_next_state: str = Field(min_length=1)
    expected_signals: list[str] = Field(min_length=1)
    disconfirming_signals: list[str] = Field(min_length=1)
    created_at: datetime

    @field_validator("unknown_question_ids", "source_fact_ids")
    @classmethod
    def unique_prediction_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(not item.strip() for item in value):
            raise ValueError("prediction references must be unique and non-empty")
        return value

    @model_validator(mode="after")
    def keep_prediction_evidence_in_scope(self) -> "NextStatePredictionV1":
        if any(ref.environment_id != self.environment_id for ref in self.source_evidence_refs):
            raise ValueError("prediction source evidence must use the prediction environment")
        return self


class PredictionAdjudicationV1(_StrictModel):
    schema_id: Literal["game-observatory.prediction-adjudication.v1"] = Field(
        default="game-observatory.prediction-adjudication.v1", alias="schema"
    )
    id: str = Field(min_length=1)
    prediction_id: str = Field(min_length=1)
    game_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    disposition: PredictionDisposition
    observed_outcome: str | None = Field(default=None, min_length=1)
    explanation: str = Field(min_length=1)
    evidence_refs: list[EvidenceReferenceV1] = Field(default_factory=list)
    resulting_fact_ids: list[str] = Field(default_factory=list)
    model_change_summary: str | None = Field(default=None, min_length=1)
    next_action: str | None = Field(default=None, min_length=1)
    adjudicated_at: datetime | None = None

    @field_validator("resulting_fact_ids")
    @classmethod
    def unique_resulting_facts(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(not item.strip() for item in value):
            raise ValueError("resulting_fact_ids must be unique and non-empty")
        return value

    @model_validator(mode="after")
    def enforce_adjudication_state(self) -> "PredictionAdjudicationV1":
        if any(ref.environment_id != self.environment_id for ref in self.evidence_refs):
            raise ValueError("adjudication evidence must use the adjudication environment")
        if self.disposition == "pending":
            if self.evidence_refs or self.observed_outcome or self.adjudicated_at:
                raise ValueError(
                    "pending adjudication must not claim observation evidence or outcome"
                )
            if self.resulting_fact_ids or self.model_change_summary:
                raise ValueError("pending adjudication must not claim a model change")
            if not self.next_action:
                raise ValueError("pending adjudication requires a next_action")
            return self
        if not self.evidence_refs or not self.observed_outcome or not self.adjudicated_at:
            raise ValueError("terminal adjudication requires outcome, time, and canonical evidence")
        if any(not ref.evidence_step_ids for ref in self.evidence_refs):
            raise ValueError("terminal adjudication evidence must include EvidenceStep ids")
        if self.disposition in {"corrected", "refuted"} and (
            not self.resulting_fact_ids or not self.model_change_summary
        ):
            raise ValueError("corrected/refuted adjudication requires an explicit model change")
        return self


@dataclass(frozen=True)
class EvidenceStepMomentV1:
    step_id: str
    started_at: datetime
    session_id: str | None = None


def validate_prediction_adjudication(
    prediction: NextStatePredictionV1,
    adjudication: PredictionAdjudicationV1,
    evidence_steps: Mapping[str, EvidenceStepMomentV1],
    *,
    expected_session_id: str | None = None,
) -> None:
    """Reconcile a prediction verdict against later independent EvidenceSteps."""

    if adjudication.prediction_id != prediction.id:
        raise ValueError("adjudication prediction_id does not match the prediction")
    if adjudication.game_id != prediction.game_id:
        raise ValueError("adjudication game_id does not match the prediction")
    if adjudication.environment_id != prediction.environment_id:
        raise ValueError("adjudication environment_id does not match the prediction")
    if adjudication.disposition == "pending":
        return
    source_step_ids = {
        step_id
        for reference in prediction.source_evidence_refs
        for step_id in reference.evidence_step_ids
    }
    adjudication_step_ids = [
        step_id
        for reference in adjudication.evidence_refs
        for step_id in reference.evidence_step_ids
    ]
    if len(adjudication_step_ids) != len(set(adjudication_step_ids)):
        raise ValueError("adjudication EvidenceStep ids must be unique")
    for step_id in adjudication_step_ids:
        if step_id in source_step_ids:
            raise ValueError("adjudication must use an independent EvidenceStep")
        moment = evidence_steps.get(step_id)
        if moment is None:
            raise ValueError(f"adjudication EvidenceStep is not canonical: {step_id}")
        if moment.started_at <= prediction.created_at:
            raise ValueError("prediction must be created before every adjudication EvidenceStep")
        if expected_session_id is not None and moment.session_id != expected_session_id:
            raise ValueError("adjudication EvidenceStep does not belong to the expected Session")


class ValidationQuestionV1(_StrictModel):
    id: str = Field(min_length=1)
    unknown_question_id: str = Field(min_length=1)
    prediction_id: str = Field(min_length=1)
    priority: Literal["critical", "high", "medium", "low"]
    preconditions: list[str] = Field(min_length=1)
    observation_plan: list[str] = Field(min_length=1)
    success_condition: str = Field(min_length=1)
    failure_condition: str = Field(min_length=1)
    recovery_action: str = Field(min_length=1)
    authorization_state: Literal[
        "ready_for_new_session", "adjudicated", "blocked_pending_authorization"
    ]


class GuardedBatchLocatorV1(_StrictModel):
    id: str = Field(min_length=1)
    kind: Literal["fixed_source_pixel", "runtime_recognized_board_cell"]
    target_name: str = Field(min_length=1)
    source_evidence_step_ids: list[str] = Field(min_length=1)
    source_artifact_ids: list[str] = Field(min_length=1)
    point: SourcePixelPoint | None = None
    bounds: SourcePixelRect | None = None
    board_cell: str | None = Field(default=None, min_length=1)
    recognition_requirements: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_locator_provenance(self) -> "GuardedBatchLocatorV1":
        if len(self.source_evidence_step_ids) != len(set(self.source_evidence_step_ids)):
            raise ValueError("guarded batch locator EvidenceStep ids must be unique")
        if len(self.source_artifact_ids) != len(set(self.source_artifact_ids)):
            raise ValueError("guarded batch locator artifact ids must be unique")
        if self.kind == "fixed_source_pixel" and (self.point is None or self.bounds is None):
            raise ValueError("fixed-source-pixel locator requires point and bounds")
        if self.kind == "runtime_recognized_board_cell" and (
            not self.board_cell or not self.recognition_requirements
        ):
            raise ValueError("runtime-recognized locator requires board cell and recognition rules")
        if (
            self.bounds is not None
            and self.point is not None
            and not self.bounds.contains(self.point)
        ):
            raise ValueError("guarded batch locator point must be inside its bounds")
        return self


class GuardedBatchSourceStateV1(_StrictModel):
    required_surface: str = Field(min_length=1)
    source_evidence_refs: list[EvidenceReferenceV1] = Field(min_length=1)
    reference_artifact_id: str = Field(min_length=1)
    generator_locator_id: str = Field(min_length=1)
    minimum_empty_cells: int = Field(ge=0)
    minimum_energy: int = Field(ge=0)
    minimum_session_action_budget: int = Field(ge=1)
    checkpoint_action_reserve: int = Field(default=1, ge=1)
    required_conditions: list[str] = Field(min_length=1)
    forbidden_surface_signals: list[str] = Field(min_length=1)
    source_mismatch_action: Literal["reject_without_device_action"] = "reject_without_device_action"


class GuardedBatchExpectationV1(_StrictModel):
    summary: str = Field(min_length=1)
    kind: Literal["visual_change", "visual_no_change"]
    min_visual_distance: float = Field(default=0.02, ge=0, le=1)
    expected_business_change: str = Field(min_length=1)
    effective_when: list[str] = Field(min_length=1)
    stop_conditions: list[str] = Field(min_length=1)


class GuardedBatchActionV1(_StrictModel):
    id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    locator_id: str = Field(min_length=1)
    action: NormalizedAction
    account_action_intent: AccountActionIntentV1
    expectation: GuardedBatchExpectationV1
    lightweight_guard_checks: list[str] = Field(min_length=1)
    recovery_action: str = Field(min_length=1)


class GuardedBatchRoutePreparationV1(_StrictModel):
    candidate_id: str = Field(min_length=1)
    route_id: str = Field(min_length=1)
    route_version: int = Field(default=1, ge=1)
    game_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    chain_kind: Literal["high_repetition", "semi_fixed"]
    execution_status: Literal[
        "not_executed", "dry_run_only", "ready_for_live_validation", "live_validated"
    ]
    execution_authorization: Literal["none", "single_live_validation", "benchmark"]
    executable: bool
    activation_requirements: list[str] = Field(min_length=1)
    blocking_reasons: list[str] = Field(default_factory=list)
    source_state_guard: GuardedBatchSourceStateV1
    locators: list[GuardedBatchLocatorV1] = Field(min_length=1)
    planned_actions: list[GuardedBatchActionV1] = Field(default_factory=list)
    batch_end_checkpoint: list[str] = Field(min_length=1)
    terminal_receipt_requirements: list[str] = Field(min_length=1)
    wall_clock_boundary: str = Field(min_length=1)
    validation_evidence_refs: list[EvidenceReferenceV1] = Field(default_factory=list)
    validation_skill_run_ids: list[str] = Field(default_factory=list)
    success_branch: list[str] = Field(min_length=1)
    failure_branch: list[str] = Field(min_length=1)
    recovery_branch: list[str] = Field(min_length=1)
    forbidden_actions: list[str] = Field(min_length=1)
    required_consecutive_terminal_frames: int = Field(default=2, ge=1, le=4)
    settle_timeout_seconds: float = Field(default=4.0, gt=0, le=10)
    sample_interval_seconds: float = Field(default=0.25, gt=0, le=2)
    execution_capture_mode: Literal[
        "canonical_per_action", "guarded_batch_checkpoints"
    ] = "canonical_per_action"
    execution_business_measurement_mode: Literal[
        "canonical_per_action", "incremental_checkpoints_with_canonical_terminal"
    ] = "canonical_per_action"
    high_repetition_target_per_minute: float = Field(gt=0)
    semi_fixed_target_per_minute: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_guarded_route_lifecycle(self) -> "GuardedBatchRoutePreparationV1":
        locator_ids = [item.id for item in self.locators]
        if len(locator_ids) != len(set(locator_ids)):
            raise ValueError("guarded batch locator ids must be unique")
        action_ids = [item.id for item in self.planned_actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("guarded batch action ids must be unique")
        if [item.sequence for item in self.planned_actions] != list(
            range(1, len(self.planned_actions) + 1)
        ):
            raise ValueError("guarded batch action sequence must be contiguous and ordered")
        if any(item.locator_id not in set(locator_ids) for item in self.planned_actions):
            raise ValueError("guarded batch action refers to an unknown locator")
        if self.source_state_guard.generator_locator_id not in set(locator_ids):
            raise ValueError("source-state generator locator is missing from the route")
        required_actions = 10 if self.chain_kind == "high_repetition" else 5
        if self.planned_actions and len(self.planned_actions) < required_actions:
            raise ValueError("guarded batch route does not meet its minimum action count")
        if self.source_state_guard.minimum_session_action_budget < (
            len(self.planned_actions) + self.source_state_guard.checkpoint_action_reserve
        ):
            raise ValueError("session action budget must cover the route and checkpoint reserve")

        forbidden_categories = {
            "real_money_payment",
            "advertising",
            "login",
            "external_link",
            "share",
            "speech",
            "social",
            "order_submission",
            "reward_claim",
            "currency_spend",
        }
        for action in self.planned_actions:
            if action.action.type not in {"tap", "swipe"}:
                raise ValueError("guarded batch route only permits bounded tap or swipe actions")
            intent = action.account_action_intent
            if (
                intent.category in forbidden_categories
                or intent.involves_real_money
                or intent.submits_external_personal_identity
                or intent.impersonates_real_person
            ):
                raise ValueError("guarded batch route contains a forbidden action intent")

        if self.execution_status in {"not_executed", "dry_run_only"}:
            if self.executable or self.execution_authorization != "none":
                raise ValueError("an unready guarded route must remain non-executable")
            if self.execution_status == "not_executed" and self.planned_actions:
                raise ValueError("not-executed route cannot claim a frozen action batch")
            if not self.blocking_reasons:
                raise ValueError("an unready guarded route must retain blocking reasons")
        elif self.execution_status == "ready_for_live_validation":
            if not self.executable or self.execution_authorization != "single_live_validation":
                raise ValueError("ready route is executable only for one controlled validation")
            if len(self.planned_actions) < required_actions:
                raise ValueError("ready route requires a complete frozen action batch")
            if self.validation_evidence_refs or self.validation_skill_run_ids:
                raise ValueError("ready route cannot claim live-validation evidence")
            if not self.blocking_reasons:
                raise ValueError("ready route must still block formal benchmark use")
        else:
            benchmark_ready = self.executable and self.execution_authorization == "benchmark"
            validated_but_blocked = (
                not self.executable
                and self.execution_authorization == "none"
                and bool(self.blocking_reasons)
            )
            if not (benchmark_ready or validated_but_blocked):
                raise ValueError(
                    "live-validated route must be benchmark-ready or explicitly non-executable "
                    "with activation blockers"
                )
            if len(self.planned_actions) < required_actions:
                raise ValueError("live-validated route requires a complete action batch")
            if not self.validation_evidence_refs or not self.validation_skill_run_ids:
                raise ValueError("live-validated route requires canonical validation evidence")
            if benchmark_ready and self.blocking_reasons:
                raise ValueError("benchmark-ready live-validated route cannot retain blockers")
        return self


class RecoveryRoutePreparationV1(_StrictModel):
    """One-action capacity recovery contract kept outside throughput credit."""

    schema_id: Literal["game-observatory.recovery-route-preparation.v1"] = Field(
        default="game-observatory.recovery-route-preparation.v1", alias="schema"
    )
    candidate_id: str = Field(min_length=1)
    route_id: str = Field(min_length=1)
    route_version: int = Field(default=1, ge=1)
    game_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    created_at: datetime
    execution_status: Literal["dry_run_only", "ready_for_live_validation", "live_validated"]
    execution_authorization: Literal["none", "single_live_validation"]
    executable: bool
    execution_credit: Literal["recovery_only_not_throughput"] = "recovery_only_not_throughput"
    activation_requirements: list[str] = Field(min_length=1)
    blocking_reasons: list[str] = Field(default_factory=list)
    reference_artifact_id: str = Field(min_length=1)
    source_evidence_refs: list[EvidenceReferenceV1] = Field(min_length=1)
    expected_empty_cells: Literal[0]
    expected_generator_next_tap_candidate: Literal[False]
    minimum_energy: int = Field(ge=0)
    minimum_session_action_budget: int = Field(ge=2)
    source_identity_threshold: int = Field(ge=0)
    selected_pair_identity_distance: int = Field(ge=0)
    minimum_negative_identity_distance: int = Field(ge=0)
    expected_released_cells: Literal[1]
    source_locator_id: str = Field(min_length=1)
    target_locator_id: str = Field(min_length=1)
    locators: list[GuardedBatchLocatorV1] = Field(min_length=2)
    planned_action: GuardedBatchActionV1
    checkpoint_requirements: list[str] = Field(min_length=1)
    terminal_receipt_requirements: list[str] = Field(min_length=1)
    wall_clock_boundary: str = Field(min_length=1)
    validation_evidence_refs: list[EvidenceReferenceV1] = Field(default_factory=list)
    validation_skill_run_ids: list[str] = Field(default_factory=list)
    success_branch: list[str] = Field(min_length=1)
    failure_branch: list[str] = Field(min_length=1)
    recovery_branch: list[str] = Field(min_length=1)
    forbidden_actions: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_recovery_route(self) -> "RecoveryRoutePreparationV1":
        locators = {item.id: item for item in self.locators}
        if len(locators) != len(self.locators):
            raise ValueError("recovery route locator ids must be unique")
        if self.source_locator_id == self.target_locator_id:
            raise ValueError("recovery route source and target locators must differ")
        if self.source_locator_id not in locators or self.target_locator_id not in locators:
            raise ValueError("recovery route source and target locators must be declared")
        if self.planned_action.sequence != 1:
            raise ValueError("recovery route must contain exactly one ordered action")
        if self.planned_action.locator_id != self.source_locator_id:
            raise ValueError("recovery action must bind to the declared source locator")
        action = self.planned_action.action
        source = locators[self.source_locator_id]
        target = locators[self.target_locator_id]
        if action.type != "swipe" or None in (action.x, action.y, action.x2, action.y2):
            raise ValueError("recovery action must be one bounded source-to-target swipe")
        if source.point is None or target.point is None:
            raise ValueError("recovery source and target locators require fixed points")
        if (action.x, action.y) != (source.point.x, source.point.y):
            raise ValueError("recovery swipe start must match the source locator")
        if (action.x2, action.y2) != (target.point.x, target.point.y):
            raise ValueError("recovery swipe end must match the target locator")
        if self.selected_pair_identity_distance > self.source_identity_threshold:
            raise ValueError("selected recovery pair exceeds the calibrated identity threshold")
        if self.minimum_negative_identity_distance <= self.source_identity_threshold:
            raise ValueError("recovery identity threshold does not separate negative pairs")
        if any(
            self.reference_artifact_id not in item.source_artifact_ids for item in self.locators
        ):
            raise ValueError("all recovery locators must bind to the reference artifact")

        if self.execution_status == "dry_run_only":
            if self.executable or self.execution_authorization != "none":
                raise ValueError("dry-run recovery route must remain non-executable")
            if not self.blocking_reasons:
                raise ValueError("dry-run recovery route must retain activation blockers")
            if self.validation_evidence_refs or self.validation_skill_run_ids:
                raise ValueError("dry-run recovery route cannot claim live-validation evidence")
        elif self.execution_status == "ready_for_live_validation":
            if not self.executable or self.execution_authorization != "single_live_validation":
                raise ValueError("ready recovery route requires one live-validation authorization")
            if not self.blocking_reasons:
                raise ValueError("ready recovery route must still block throughput credit")
            if self.validation_evidence_refs or self.validation_skill_run_ids:
                raise ValueError("ready recovery route cannot claim live-validation evidence")
        else:
            if self.executable or self.execution_authorization != "none":
                raise ValueError("consumed recovery route must not remain executable")
            if not self.validation_evidence_refs or not self.validation_skill_run_ids:
                raise ValueError("live-validated recovery route requires canonical evidence")
        return self


class NewSessionPreparationV1(_StrictModel):
    schema_id: Literal["game-observatory.new-session-preparation.v1"] = Field(
        default="game-observatory.new-session-preparation.v1", alias="schema"
    )
    id: str = Field(min_length=1)
    game_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    generated_at: datetime
    source_model_sha256: dict[str, str] = Field(min_length=1)
    preparation_device_action_count: Literal[0] = 0
    preparation_resource_spend: Literal[0] = 0
    current_phase_allowed_operations: list[
        Literal["read_model", "read_evidence", "generate_contract", "validate_contract"]
    ] = Field(min_length=1)
    current_phase_forbidden_actions: list[str] = Field(min_length=1)
    predictions: list[NextStatePredictionV1] = Field(min_length=1)
    adjudications: list[PredictionAdjudicationV1] = Field(min_length=1)
    validation_questions: list[ValidationQuestionV1] = Field(min_length=1)
    guarded_batch_route: GuardedBatchRoutePreparationV1
    successor_route_path: str | None = Field(default=None, min_length=1)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def enforce_offline_preparation_integrity(self) -> "NewSessionPreparationV1":
        prediction_ids = [item.id for item in self.predictions]
        if len(prediction_ids) != len(set(prediction_ids)):
            raise ValueError("preparation prediction ids must be unique")
        adjudication_ids = [item.id for item in self.adjudications]
        if len(adjudication_ids) != len(set(adjudication_ids)):
            raise ValueError("preparation adjudication ids must be unique")
        by_prediction = {item.prediction_id: item for item in self.adjudications}
        if set(by_prediction) != set(prediction_ids):
            raise ValueError("preparation requires exactly one adjudication state per prediction")
        if any(item.game_id != self.game_id for item in self.adjudications):
            raise ValueError("preparation adjudications must use the preparation game")
        if any(item.environment_id != self.environment_id for item in self.adjudications):
            raise ValueError("preparation adjudications must use the preparation environment")
        question_prediction_ids = {item.prediction_id for item in self.validation_questions}
        if not question_prediction_ids.issubset(set(prediction_ids)):
            raise ValueError("validation questions must refer to declared predictions")
        unknown_ids = {
            question_id
            for prediction in self.predictions
            for question_id in prediction.unknown_question_ids
        }
        if any(item.unknown_question_id not in unknown_ids for item in self.validation_questions):
            raise ValueError("validation questions must refer to declared unknown questions")
        if any(prediction.game_id != self.game_id for prediction in self.predictions):
            raise ValueError("preparation predictions must use the preparation game")
        if any(prediction.environment_id != self.environment_id for prediction in self.predictions):
            raise ValueError("preparation predictions must use the preparation environment")
        return self


ContentEntityKind: TypeAlias = Literal[
    "system",
    "state",
    "order",
    "item",
    "generator",
    "resource",
    "currency",
    "task",
    "story",
    "restoration",
    "activity",
    "shop",
    "collection",
    "map",
    "character",
    "reward",
    "other",
]


class ContentEntityV1(_StrictModel):
    id: str = Field(min_length=1)
    kind: ContentEntityKind
    label: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    applicability: ApplicabilityScopeV1
    attributes: dict[str, Any] = Field(default_factory=dict)
    facts: list[FactRecordV1] = Field(default_factory=list)


class ContentRelationV1(_StrictModel):
    id: str = Field(min_length=1)
    source_entity_id: str = Field(min_length=1)
    relation_type: str = Field(min_length=1)
    target_entity_id: str = Field(min_length=1)
    applicability: ApplicabilityScopeV1
    facts: list[FactRecordV1] = Field(min_length=1)


class ContentConflictV1(_StrictModel):
    id: str = Field(min_length=1)
    fact_ids: list[str] = Field(min_length=2)
    status: Literal["open", "resolved"]
    resolution: str | None = Field(default=None, min_length=1)
    evidence_refs: list[EvidenceReferenceV1] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_resolution_when_closed(self) -> "ContentConflictV1":
        if self.status == "resolved" and not self.resolution:
            raise ValueError("resolved conflicts require a resolution")
        return self


class GameContentGraphV1(_StrictModel):
    schema_id: Literal["game-observatory.game-content-graph.v1"] = Field(
        default="game-observatory.game-content-graph.v1", alias="schema"
    )
    id: str = Field(min_length=1)
    game_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    entities: list[ContentEntityV1] = Field(min_length=1)
    relations: list[ContentRelationV1] = Field(default_factory=list)
    conflicts: list[ContentConflictV1] = Field(default_factory=list)
    source_dossier_ids: list[str] = Field(default_factory=list)
    updated_at: datetime

    @model_validator(mode="after")
    def enforce_graph_integrity(self) -> "GameContentGraphV1":
        entity_ids = [entity.id for entity in self.entities]
        relation_ids = [relation.id for relation in self.relations]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("content entity ids must be unique")
        if len(relation_ids) != len(set(relation_ids)):
            raise ValueError("content relation ids must be unique")
        entity_set = set(entity_ids)
        missing = sorted(
            {
                eid
                for relation in self.relations
                for eid in (relation.source_entity_id, relation.target_entity_id)
                if eid not in entity_set
            }
        )
        if missing:
            raise ValueError("content relations refer to missing entities: " + ", ".join(missing))
        scoped = [*self.entities, *self.relations]
        if any(item.applicability.game_id != self.game_id for item in scoped):
            raise ValueError("content graph objects must use the graph game_id")
        if any(item.applicability.environment_id != self.environment_id for item in scoped):
            raise ValueError("content graph objects must use the graph environment_id")
        return self


class StrategyMetricDefinitionV1(_StrictModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    direction: Literal["maximize", "minimize", "target"]
    unit: str = Field(min_length=1)
    target_value: float | None = None
    primary: bool = False
    safety_critical: bool = False

    @model_validator(mode="after")
    def require_target_value(self) -> "StrategyMetricDefinitionV1":
        if self.direction == "target" and self.target_value is None:
            raise ValueError("target metrics require target_value")
        return self


class StrategyCandidateV1(_StrictModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    legal_actions: list[str] = Field(min_length=1)
    expected_benefits: list[str] = Field(min_length=1)
    risks: list[str] = Field(default_factory=list)
    opportunity_costs: list[str] = Field(default_factory=list)
    short_term_effects: list[str] = Field(default_factory=list)
    long_term_effects: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceReferenceV1] = Field(default_factory=list)


class StrategyTrialResultV1(_StrictModel):
    id: str = Field(min_length=1)
    decision_point_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    started_at: datetime
    ended_at: datetime
    status: Literal["completed", "failed", "aborted"]
    metric_values: dict[str, float] = Field(default_factory=dict)
    action_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceReferenceV1] = Field(min_length=1)
    pre_state: list[str] = Field(default_factory=list)
    considered_candidate_ids: list[str] = Field(default_factory=list)
    choice_rationale: str | None = Field(default=None, min_length=1)
    result_summary: str | None = Field(default=None, min_length=1)
    observed_constraints: list[str] = Field(default_factory=list)
    realized_opportunity_costs: list[str] = Field(default_factory=list)
    deviations: list[str] = Field(default_factory=list)
    confounders: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_trial(self) -> "StrategyTrialResultV1":
        if self.ended_at < self.started_at:
            raise ValueError("strategy trial ended_at must not precede started_at")
        if any(ref.environment_id != self.environment_id for ref in self.evidence_refs):
            raise ValueError("strategy trial evidence must use the trial environment")
        if len(self.considered_candidate_ids) != len(set(self.considered_candidate_ids)):
            raise ValueError("considered_candidate_ids must be unique")
        return self


class PlayStrategyRecordV1(_StrictModel):
    schema_id: Literal["game-observatory.play-strategy-record.v1"] = Field(
        default="game-observatory.play-strategy-record.v1", alias="schema"
    )
    id: str = Field(min_length=1)
    game_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    decision_problem: str = Field(min_length=1)
    objective_priority: list[str] = Field(min_length=1)
    preconditions: list[FactRecordV1] = Field(min_length=1)
    constraints: list[str] = Field(min_length=1)
    metrics: list[StrategyMetricDefinitionV1] = Field(min_length=1)
    candidates: list[StrategyCandidateV1] = Field(min_length=2)
    baseline_candidate_id: str = Field(min_length=1)
    selected_candidate_id: str | None = Field(default=None, min_length=1)
    external_guide_facts: list[FactRecordV1] = Field(default_factory=list)
    trial_results: list[StrategyTrialResultV1] = Field(default_factory=list)
    counterexamples: list[str] = Field(default_factory=list)
    conclusion: Literal[
        "baseline_only",
        "inconclusive",
        "evaluated_not_better",
        "provisionally_better",
        "verified_better",
    ] = "baseline_only"
    conclusion_notes: list[str] = Field(min_length=1)
    updated_at: datetime

    @model_validator(mode="after")
    def enforce_strategy_integrity(self) -> "PlayStrategyRecordV1":
        candidate_ids = [candidate.id for candidate in self.candidates]
        candidate_set = set(candidate_ids)
        if len(candidate_ids) != len(candidate_set):
            raise ValueError("strategy candidate ids must be unique")
        if self.baseline_candidate_id not in candidate_set:
            raise ValueError("baseline_candidate_id must refer to a candidate")
        if self.selected_candidate_id and self.selected_candidate_id not in candidate_set:
            raise ValueError("selected_candidate_id must refer to a candidate")
        if any(trial.candidate_id not in candidate_set for trial in self.trial_results):
            raise ValueError("strategy trials must refer to candidates")
        if any(trial.environment_id != self.environment_id for trial in self.trial_results):
            raise ValueError("strategy trials must use the record environment")
        metric_ids = [metric.id for metric in self.metrics]
        if len(metric_ids) != len(set(metric_ids)) or not any(
            metric.primary for metric in self.metrics
        ):
            raise ValueError("strategy metrics must be unique and include a primary metric")
        unknown_metrics = sorted(
            {
                key
                for trial in self.trial_results
                for key in trial.metric_values
                if key not in set(metric_ids)
            }
        )
        if unknown_metrics:
            raise ValueError("strategy trials use undefined metrics: " + ", ".join(unknown_metrics))
        if self.conclusion == "verified_better":
            points = {
                trial.decision_point_id
                for trial in self.trial_results
                if trial.status == "completed"
            }
            if not self.selected_candidate_id or len(points) < 3:
                raise ValueError(
                    "verified_better requires a selected candidate and three decision points"
                )
        if self.conclusion in {
            "evaluated_not_better",
            "provisionally_better",
            "verified_better",
        }:
            completed = [trial for trial in self.trial_results if trial.status == "completed"]
            points = {trial.decision_point_id for trial in completed}
            if not self.selected_candidate_id or len(points) < 3:
                raise ValueError(
                    "an evaluated strategy conclusion requires a selected candidate and "
                    "three completed decision points"
                )
            incomplete = [
                trial.id
                for trial in completed
                if not trial.pre_state
                or len(trial.considered_candidate_ids) < 2
                or self.baseline_candidate_id not in trial.considered_candidate_ids
                or self.selected_candidate_id not in trial.considered_candidate_ids
                or not trial.choice_rationale
                or not trial.result_summary
                or not trial.observed_constraints
            ]
            if incomplete:
                raise ValueError(
                    "evaluated strategy trials require pre-state, both candidates, choice, "
                    "result, and observed constraints: " + ", ".join(incomplete)
                )
        return self


AttemptOutcome: TypeAlias = Literal["effective", "no_change", "rejected", "failed"]
ChainKind: TypeAlias = Literal["high_repetition", "semi_fixed", "unknown_exploration"]
GateStatus: TypeAlias = Literal["passed", "failed", "not_applicable"]
CanonicalSourceGap: TypeAlias = Literal["telemetry_event_ids", "route_result_ids"]
ExecutionReceiptKind: TypeAlias = Literal["route_execution", "single_action_execution"]


class FacilityTimingV1(_StrictModel):
    request_received_at: datetime
    checkpoint_completed_at: datetime
    terminal_receipt_completed_at: datetime
    wall_clock_seconds: float = Field(gt=0)
    evidence_processing_seconds: float | None = Field(default=None, ge=0)
    device_action_seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_full_wall_clock(self) -> "FacilityTimingV1":
        if self.checkpoint_completed_at < self.request_received_at:
            raise ValueError("checkpoint must not precede request receipt")
        if self.terminal_receipt_completed_at < self.checkpoint_completed_at:
            raise ValueError("terminal receipt must not precede checkpoint completion")
        measured = (self.terminal_receipt_completed_at - self.request_received_at).total_seconds()
        tolerance = max(0.01, measured * 0.001)
        if not math.isclose(self.wall_clock_seconds, measured, abs_tol=tolerance):
            raise ValueError(
                "wall_clock_seconds must cover request receipt through terminal receipt"
            )
        for name, value in (
            ("evidence_processing_seconds", self.evidence_processing_seconds),
            ("device_action_seconds", self.device_action_seconds),
        ):
            if value is not None and value > self.wall_clock_seconds + tolerance:
                raise ValueError(f"{name} must not exceed the complete wall clock")
        return self


class ExecutionAttemptV1(_StrictModel):
    id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    environment_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    route_id: str = Field(min_length=1)
    planned_action_id: str = Field(min_length=1)
    action_kind: str = Field(min_length=1)
    started_at: datetime
    ended_at: datetime
    outcome: AttemptOutcome
    expectation_met: bool
    business_state_changed: bool
    business_change: str | None = Field(default=None, min_length=1)
    rejection_category: (
        Literal["source_guard", "budget", "lease", "policy", "state", "other"] | None
    ) = None
    rejection_reason: str | None = Field(default=None, min_length=1)
    failure_reason: str | None = Field(default=None, min_length=1)
    observed_detail: str | None = Field(default=None, min_length=1)
    execution_receipt_kind: ExecutionReceiptKind = "route_execution"
    telemetry_event_ids: list[str] = Field(default_factory=list)
    route_result_ids: list[str] = Field(default_factory=list)
    canonical_source_gaps: list[CanonicalSourceGap] = Field(default_factory=list)
    evidence_refs: list[EvidenceReferenceV1] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_attempt_outcome(self) -> "ExecutionAttemptV1":
        if self.ended_at < self.started_at:
            raise ValueError("execution attempt ended_at must not precede started_at")
        if any(ref.environment_id != self.environment_id for ref in self.evidence_refs):
            raise ValueError("execution attempt evidence must use the attempt environment")
        declared_gaps = set(self.canonical_source_gaps)
        actual_gaps = {"telemetry_event_ids"} if not self.telemetry_event_ids else set()
        if self.execution_receipt_kind == "route_execution":
            if not self.route_result_ids:
                actual_gaps.add("route_result_ids")
        else:
            if self.route_result_ids:
                raise ValueError(
                    "single-action execution receipts cannot claim route planning results"
                )
            terminal_step_ids = {
                step_id for ref in self.evidence_refs for step_id in ref.evidence_step_ids
            }
            action_run_ids = {run_id for ref in self.evidence_refs for run_id in ref.trace_run_ids}
            if not terminal_step_ids or not action_run_ids:
                raise ValueError(
                    "single-action execution receipts require EvidenceStep and action-run ids"
                )
        if declared_gaps != actual_gaps:
            raise ValueError(
                "canonical_source_gaps must exactly declare missing applicable execution receipts"
            )
        if self.outcome == "effective":
            if not self.business_state_changed or not self.business_change:
                raise ValueError("effective attempts require a described business state change")
        elif self.outcome == "no_change":
            if self.business_state_changed:
                raise ValueError("no_change attempts cannot claim a business state change")
        elif self.outcome == "rejected":
            if not self.rejection_reason or not self.rejection_category:
                raise ValueError("rejected attempts require category and reason")
            if self.business_state_changed:
                raise ValueError("rejected attempts cannot claim a business state change")
        elif self.outcome == "failed" and not self.failure_reason:
            raise ValueError("failed attempts require failure_reason")
        return self


class ExecutionAttemptLogV1(_StrictModel):
    schema_id: Literal["game-observatory.execution-attempt-log.v1"] = Field(
        default="game-observatory.execution-attempt-log.v1", alias="schema"
    )
    id: str = Field(min_length=1)
    game_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    task_id: str | None = Field(default=None, min_length=1)
    purpose: str = Field(min_length=1)
    benchmark_eligible: bool
    benchmark_exclusion_reason: str | None = Field(default=None, min_length=1)
    prediction_ids: list[str] = Field(default_factory=list)
    attempts: list[ExecutionAttemptV1] = Field(min_length=1)
    conflicting_assessment_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    updated_at: datetime

    @field_validator("prediction_ids", "conflicting_assessment_ids")
    @classmethod
    def validate_unique_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(not item.strip() for item in value):
            raise ValueError("execution attempt log references must be unique and non-empty")
        return value

    @model_validator(mode="after")
    def validate_log_scope(self) -> "ExecutionAttemptLogV1":
        if any(attempt.environment_id != self.environment_id for attempt in self.attempts):
            raise ValueError("all logged attempts must use the log environment")
        if any(attempt.session_id != self.session_id for attempt in self.attempts):
            raise ValueError("all logged attempts must use the log session")
        sequences = [attempt.sequence for attempt in self.attempts]
        if sequences != list(range(1, len(self.attempts) + 1)):
            raise ValueError("logged attempt sequences must be complete, ordered, and gap-free")
        attempt_ids = [attempt.id for attempt in self.attempts]
        if len(attempt_ids) != len(set(attempt_ids)):
            raise ValueError("logged attempt ids must be unique")
        if self.benchmark_eligible and self.benchmark_exclusion_reason:
            raise ValueError("benchmark-eligible logs cannot carry an exclusion reason")
        if not self.benchmark_eligible and not self.benchmark_exclusion_reason:
            raise ValueError("benchmark-ineligible logs require an exclusion reason")
        return self


class RecoveryEventV1(_StrictModel):
    id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    trigger_attempt_sequence: int = Field(ge=1)
    trigger: str = Field(min_length=1)
    recovery_action: str = Field(min_length=1)
    started_at: datetime
    ended_at: datetime
    outcome: Literal["recovered", "failed", "aborted"]
    pending_action_cleared: bool
    evidence_refs: list[EvidenceReferenceV1] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_recovery(self) -> "RecoveryEventV1":
        if self.ended_at < self.started_at:
            raise ValueError("recovery ended_at must not precede started_at")
        if any(ref.environment_id != self.environment_id for ref in self.evidence_refs):
            raise ValueError("recovery evidence must use the recovery environment")
        if self.outcome == "recovered" and not self.pending_action_cleared:
            raise ValueError("recovered events must clear their pending action")
        return self


class ExecutionSafetyReceiptV1(_StrictModel):
    source_guard_checked: bool
    source_guard_rejection_count: int = Field(ge=0)
    lease_id: str = Field(min_length=1)
    lease_state_at_terminal: Literal["held", "released", "expired", "lost"]
    heartbeat_observed: bool
    remaining_action_budget_at_start: int = Field(ge=0)
    remaining_action_budget_at_terminal: int = Field(ge=0)
    checkpoint_completed: bool
    pending_action_count_at_terminal: int = Field(ge=0)
    safety_violations: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceReferenceV1] = Field(min_length=1)

    def is_clean(self) -> bool:
        return (
            self.source_guard_checked
            and self.lease_state_at_terminal in {"held", "released"}
            and self.heartbeat_observed
            and self.checkpoint_completed
            and self.pending_action_count_at_terminal == 0
            and not self.safety_violations
        )


class ExecutionBatchV1(_StrictModel):
    id: str = Field(min_length=1)
    game_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    chain_kind: ChainKind
    route_id: str = Field(min_length=1)
    planned_action_count: int = Field(ge=1)
    canonical_attempt_count: int = Field(ge=1)
    legal_business_terminal_reached: bool = False
    branch_decisions: list[str] = Field(default_factory=list)
    knowledge_outcomes: list[str] = Field(default_factory=list)
    timing: FacilityTimingV1
    attempts: list[ExecutionAttemptV1] = Field(min_length=1)
    recovery_events: list[RecoveryEventV1] = Field(default_factory=list)
    safety: ExecutionSafetyReceiptV1
    effective_action_count: int = Field(ge=0)
    no_change_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    recovered_count: int = Field(ge=0)
    canonical_source_gap_count: int = Field(ge=0)
    expected_effect_hit_count: int = Field(ge=0)
    expected_effect_hit_rate: float = Field(ge=0, le=1)
    no_change_rate: float = Field(ge=0, le=1)
    effective_actions_per_minute: float = Field(ge=0)
    gate_status: GateStatus
    evidence_refs: list[EvidenceReferenceV1] = Field(min_length=1)

    @model_validator(mode="after")
    def derive_and_validate_metrics(self) -> "ExecutionBatchV1":
        if any(attempt.environment_id != self.environment_id for attempt in self.attempts):
            raise ValueError("all execution attempts must use the batch environment")
        if any(attempt.session_id != self.session_id for attempt in self.attempts):
            raise ValueError("all execution attempts must use the batch session")
        if any(attempt.route_id != self.route_id for attempt in self.attempts):
            raise ValueError("all execution attempts must use the batch route")
        if any(event.environment_id != self.environment_id for event in self.recovery_events):
            raise ValueError("all recovery events must use the batch environment")
        if any(event.session_id != self.session_id for event in self.recovery_events):
            raise ValueError("all recovery events must use the batch session")
        if any(ref.environment_id != self.environment_id for ref in self.evidence_refs):
            raise ValueError("batch evidence must use the batch environment")
        if any(ref.environment_id != self.environment_id for ref in self.safety.evidence_refs):
            raise ValueError("safety evidence must use the batch environment")

        sequences = [attempt.sequence for attempt in self.attempts]
        if sequences != list(range(1, len(self.attempts) + 1)):
            raise ValueError("attempt sequences must be complete, ordered, and gap-free")
        if self.canonical_attempt_count != len(self.attempts):
            raise ValueError("canonical_attempt_count must equal the complete attempt list")
        if any(
            attempt.started_at < self.timing.request_received_at
            or attempt.ended_at > self.timing.terminal_receipt_completed_at
            for attempt in self.attempts
        ):
            raise ValueError("attempts must remain inside the complete batch wall clock")
        if any(
            event.trigger_attempt_sequence > len(self.attempts) for event in self.recovery_events
        ):
            raise ValueError("recovery events must refer to a retained attempt")
        if any(
            event.started_at < self.timing.request_received_at
            or event.ended_at > self.timing.terminal_receipt_completed_at
            for event in self.recovery_events
        ):
            raise ValueError("recovery events must remain inside the complete batch wall clock")

        derived = {
            "effective_action_count": sum(a.outcome == "effective" for a in self.attempts),
            "no_change_count": sum(a.outcome == "no_change" for a in self.attempts),
            "rejected_count": sum(a.outcome == "rejected" for a in self.attempts),
            "failed_count": sum(a.outcome == "failed" for a in self.attempts),
            "recovered_count": sum(e.outcome == "recovered" for e in self.recovery_events),
            "canonical_source_gap_count": sum(
                bool(attempt.canonical_source_gaps) for attempt in self.attempts
            ),
            "expected_effect_hit_count": sum(
                a.expectation_met and a.business_state_changed for a in self.attempts
            ),
        }
        for field_name, expected in derived.items():
            if getattr(self, field_name) != expected:
                raise ValueError(f"{field_name} must be derived from the complete event list")
        source_rejections = sum(
            a.outcome == "rejected" and a.rejection_category == "source_guard"
            for a in self.attempts
        )
        if self.safety.source_guard_rejection_count != source_rejections:
            raise ValueError("source_guard_rejection_count must be derived from attempts")

        attempt_count = len(self.attempts)
        hit_rate = derived["expected_effect_hit_count"] / attempt_count
        no_change_rate = derived["no_change_count"] / attempt_count
        throughput = derived["effective_action_count"] * 60 / self.timing.wall_clock_seconds
        for field_name, expected in (
            ("expected_effect_hit_rate", hit_rate),
            ("no_change_rate", no_change_rate),
            ("effective_actions_per_minute", throughput),
        ):
            if not math.isclose(getattr(self, field_name), expected, abs_tol=1e-6):
                raise ValueError(f"{field_name} must use the complete retained sample")

        if self.chain_kind == "high_repetition":
            passed = (
                self.planned_action_count >= 10
                and throughput >= 10.0
                and hit_rate >= 0.95
                and no_change_rate <= 0.02
                and self.canonical_source_gap_count == 0
                and self.safety.is_clean()
            )
            expected_gate: GateStatus = "passed" if passed else "failed"
        elif self.chain_kind == "semi_fixed":
            passed = (
                self.planned_action_count >= 5
                and bool(self.branch_decisions)
                and throughput >= 5.0
                and hit_rate >= 0.90
                and no_change_rate <= 0.05
                and self.canonical_source_gap_count == 0
                and self.safety.is_clean()
            )
            expected_gate = "passed" if passed else "failed"
        else:
            expected_gate = "not_applicable"
            if not self.knowledge_outcomes:
                raise ValueError("unknown exploration batches require a knowledge outcome")
        if self.gate_status != expected_gate:
            raise ValueError(f"gate_status must be {expected_gate} for the measured batch")
        return self


class ExecutionBenchmarkV1(_StrictModel):
    schema_id: Literal["game-observatory.execution-benchmark.v1"] = Field(
        default="game-observatory.execution-benchmark.v1", alias="schema"
    )
    id: str = Field(min_length=1)
    game_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    workload_kind: ChainKind
    target_effective_actions_per_minute: float | None = Field(default=None, gt=0)
    consecutive_passes_required: int = Field(default=3, ge=3)
    batches: list[ExecutionBatchV1] = Field(min_length=1)
    gate_status: Literal["passed", "failed", "in_progress", "not_applicable"]
    notes: list[str] = Field(min_length=1)
    updated_at: datetime

    @model_validator(mode="after")
    def validate_consecutive_gate(self) -> "ExecutionBenchmarkV1":
        if any(batch.game_id != self.game_id for batch in self.batches):
            raise ValueError("benchmark batches must use the benchmark game")
        if any(batch.environment_id != self.environment_id for batch in self.batches):
            raise ValueError("benchmark batches must use the benchmark environment")
        if any(batch.chain_kind != self.workload_kind for batch in self.batches):
            raise ValueError("benchmark batches must use one workload kind")
        starts = [batch.timing.request_received_at for batch in self.batches]
        if starts != sorted(starts) or len(starts) != len(set(starts)):
            raise ValueError("benchmark batches must retain unique occurrence order")

        if self.workload_kind == "unknown_exploration":
            expected_status = "not_applicable"
            if self.target_effective_actions_per_minute is not None:
                raise ValueError("unknown exploration must not declare a throughput target")
        else:
            required_target = 10.0 if self.workload_kind == "high_repetition" else 5.0
            if self.target_effective_actions_per_minute != required_target:
                raise ValueError(
                    f"workload target must remain predeclared at {required_target:.2f}"
                )
            trailing_passes = 0
            for batch in reversed(self.batches):
                if batch.gate_status != "passed":
                    break
                trailing_passes += 1
            if trailing_passes >= self.consecutive_passes_required:
                expected_status = "passed"
            elif any(batch.gate_status == "failed" for batch in self.batches):
                expected_status = "failed"
            elif len(self.batches) < self.consecutive_passes_required:
                expected_status = "in_progress"
            else:
                expected_status = "failed"
        if self.gate_status != expected_status:
            raise ValueError(f"benchmark gate_status must be {expected_status}")
        return self


def iter_dossier_facts(dossier: GameSystemDossierV1) -> Iterable[FactRecordV1]:
    """Yield every fact retained by a dossier without changing its order."""

    yield from dossier.objective
    yield from dossier.entry_and_unlock
    for state in dossier.states:
        yield from state.facts
    for transition in dossier.transitions:
        yield from transition.facts
    for flow in dossier.resource_flows:
        yield from flow.facts
    for relation in dossier.relations:
        yield from relation.facts
    for boundary in dossier.failure_recovery_boundaries:
        yield from boundary.facts


class ContentMergeEventV1(_StrictModel):
    object_kind: Literal["entity", "relation"]
    object_id: str = Field(min_length=1)
    disposition: MergeDisposition
    explanation: str = Field(min_length=1)
    retained_fact_ids: list[str] = Field(default_factory=list)
    incoming_fact_ids: list[str] = Field(default_factory=list)


class ContentGraphMergeResultV1(_StrictModel):
    graph: GameContentGraphV1
    events: list[ContentMergeEventV1] = Field(min_length=1)

    def counts(self) -> dict[MergeDisposition, int]:
        return {
            status: sum(event.disposition == status for event in self.events)
            for status in ("confirmed", "extended", "corrected", "refuted", "unresolved")
        }


@dataclass(frozen=True)
class _MergeDecision:
    disposition: MergeDisposition
    explanation: str


def _fact_ids(facts: Sequence[FactRecordV1]) -> set[str]:
    return {fact.id for fact in facts}


def _decide_merge(
    current: ContentEntityV1 | ContentRelationV1,
    incoming: ContentEntityV1 | ContentRelationV1,
) -> _MergeDecision:
    current_payload = current.model_dump(mode="json", by_alias=True)
    incoming_payload = incoming.model_dump(mode="json", by_alias=True)
    if current_payload == incoming_payload:
        return _MergeDecision("confirmed", "incoming object exactly confirms the retained object")

    current_facts = _fact_ids(current.facts)
    incoming_facts = list(incoming.facts)
    contradicted = [
        fact
        for fact in incoming_facts
        if fact.status == "contradicted" and current_facts.intersection(fact.conflict_fact_ids)
    ]
    if contradicted:
        return _MergeDecision(
            "refuted", "incoming canonical evidence explicitly contradicts retained facts"
        )
    corrected = [
        fact for fact in incoming_facts if current_facts.intersection(fact.supersedes_fact_ids)
    ]
    if corrected:
        return _MergeDecision("corrected", "incoming facts explicitly supersede retained facts")

    immutable_fields = (
        ("kind",)
        if isinstance(current, ContentEntityV1)
        else ("source_entity_id", "relation_type", "target_entity_id")
    )
    immutable_fields = (*immutable_fields, "applicability")
    if any(getattr(current, field) != getattr(incoming, field) for field in immutable_fields):
        return _MergeDecision(
            "unresolved", "stable identity fields disagree; retained object was not overwritten"
        )
    if isinstance(current, ContentEntityV1):
        conflicting_attributes = {
            key
            for key in set(current.attributes).intersection(incoming.attributes)
            if current.attributes[key] != incoming.attributes[key]
        }
        if current.label != incoming.label or conflicting_attributes:
            return _MergeDecision(
                "unresolved",
                "labels or existing attributes disagree; retained values were preserved",
            )

    if _fact_ids(incoming.facts).issubset(current_facts):
        return _MergeDecision("confirmed", "incoming object repeats already retained facts")
    return _MergeDecision("extended", "incoming object adds facts or non-conflicting structure")


def _merge_facts(
    current: Sequence[FactRecordV1], incoming: Sequence[FactRecordV1]
) -> list[FactRecordV1]:
    merged = list(current)
    by_id = {fact.id: fact for fact in current}
    for fact in incoming:
        retained = by_id.get(fact.id)
        if retained is None:
            merged.append(fact)
            by_id[fact.id] = fact
        elif retained.model_dump(mode="json") != fact.model_dump(mode="json"):
            # Same-id disagreement is deliberately retained only in the merge event. A
            # caller must resolve it with a new immutable fact id instead of overwriting.
            continue
    return merged


def _merge_entity(current: ContentEntityV1, incoming: ContentEntityV1) -> ContentEntityV1:
    attributes = dict(current.attributes)
    for key, value in incoming.attributes.items():
        attributes.setdefault(key, value)
    aliases = list(dict.fromkeys([*current.aliases, *incoming.aliases]))
    return current.model_copy(
        update={
            "aliases": aliases,
            "attributes": attributes,
            "facts": _merge_facts(current.facts, incoming.facts),
        }
    )


def _merge_relation(current: ContentRelationV1, incoming: ContentRelationV1) -> ContentRelationV1:
    return current.model_copy(update={"facts": _merge_facts(current.facts, incoming.facts)})


def merge_content_graph(
    current: GameContentGraphV1,
    incoming: GameContentGraphV1,
    *,
    merged_at: datetime | None = None,
) -> ContentGraphMergeResultV1:
    """Merge new observations without silently overwriting conflicts."""

    if (current.game_id, current.environment_id) != (incoming.game_id, incoming.environment_id):
        raise ValueError("content graphs must share game_id and environment_id")

    events: list[ContentMergeEventV1] = []
    entities = {item.id: item for item in current.entities}
    entity_order = [item.id for item in current.entities]
    for item in incoming.entities:
        retained = entities.get(item.id)
        if retained is None:
            entities[item.id] = item
            entity_order.append(item.id)
            decision = _MergeDecision("extended", "incoming graph adds a new entity id")
        else:
            decision = _decide_merge(retained, item)
            if decision.disposition in {"confirmed", "extended", "corrected", "refuted"}:
                entities[item.id] = _merge_entity(retained, item)
        events.append(
            ContentMergeEventV1(
                object_kind="entity",
                object_id=item.id,
                disposition=decision.disposition,
                explanation=decision.explanation,
                retained_fact_ids=sorted(_fact_ids(retained.facts)) if retained else [],
                incoming_fact_ids=sorted(_fact_ids(item.facts)),
            )
        )

    relations = {item.id: item for item in current.relations}
    relation_order = [item.id for item in current.relations]
    for item in incoming.relations:
        retained = relations.get(item.id)
        if retained is None:
            relations[item.id] = item
            relation_order.append(item.id)
            decision = _MergeDecision("extended", "incoming graph adds a new relation id")
        else:
            decision = _decide_merge(retained, item)
            if decision.disposition in {"confirmed", "extended", "corrected", "refuted"}:
                relations[item.id] = _merge_relation(retained, item)
        events.append(
            ContentMergeEventV1(
                object_kind="relation",
                object_id=item.id,
                disposition=decision.disposition,
                explanation=decision.explanation,
                retained_fact_ids=sorted(_fact_ids(retained.facts)) if retained else [],
                incoming_fact_ids=sorted(_fact_ids(item.facts)),
            )
        )

    conflicts = list(current.conflicts)
    known_conflict_ids = {conflict.id for conflict in conflicts}
    for conflict in incoming.conflicts:
        if conflict.id not in known_conflict_ids:
            conflicts.append(conflict)
            known_conflict_ids.add(conflict.id)
    graph = GameContentGraphV1(
        schema=current.schema_id,
        id=current.id,
        game_id=current.game_id,
        environment_id=current.environment_id,
        entities=[entities[item_id] for item_id in entity_order],
        relations=[relations[item_id] for item_id in relation_order],
        conflicts=conflicts,
        source_dossier_ids=list(
            dict.fromkeys([*current.source_dossier_ids, *incoming.source_dossier_ids])
        ),
        updated_at=merged_at or max(current.updated_at, incoming.updated_at),
    )
    return ContentGraphMergeResultV1(graph=graph, events=events)


def _first_evidence_id(references: Sequence[EvidenceReferenceV1]) -> str:
    if not references:
        return "no canonical evidence"
    reference = references[0]
    for values in (
        reference.evidence_step_ids,
        reference.evidence_run_ids,
        reference.artifact_ids,
        reference.trace_run_ids,
        reference.source_ids,
    ):
        if values:
            return values[0]
    return "no canonical evidence"


def render_content_graph_markdown(graph: GameContentGraphV1) -> str:
    """Render an audit-friendly projection without inventing definite facts."""

    status_labels = {
        "verified": "已验证",
        "inferred": "推断",
        "unknown": "未知",
        "contradicted": "已冲突",
    }
    lines = [
        f"# {graph.game_id} · ReverseEngineeredGameDesignSpec",
        "",
        f"- 环境：`{graph.environment_id}`",
        f"- 模型：`{graph.id}`",
        f"- 更新时间：`{graph.updated_at.isoformat()}`",
        "- 说明：以下陈述逐条来自 canonical 模型，并显式保留事实等级。",
        "",
        "## 实体",
    ]
    for entity in graph.entities:
        lines.extend(["", f"### {entity.label} (`{entity.id}`)", f"- 类型：`{entity.kind}`"])
        if not entity.facts:
            lines.append("- 事实：尚无独立事实记录；结构化属性不视为已验证结论。")
        for fact in entity.facts:
            evidence = _first_evidence_id(fact.evidence_refs)
            lines.append(
                f"- **{status_labels[fact.status]}**：{fact.statement} （证据/依据：`{evidence}`）"
            )
    lines.extend(["", "## 关系"])
    for relation in graph.relations:
        lines.append(
            f"- `{relation.source_entity_id}` --`{relation.relation_type}`--> "
            f"`{relation.target_entity_id}`"
        )
        for fact in relation.facts:
            evidence = _first_evidence_id(fact.evidence_refs)
            lines.append(
                f"  - **{status_labels[fact.status]}**：{fact.statement} "
                f"（证据/依据：`{evidence}`）"
            )
    if graph.conflicts:
        lines.extend(["", "## 未消解冲突"])
        for conflict in graph.conflicts:
            lines.append(
                f"- `{conflict.id}`：{conflict.status}；facts="
                + ", ".join(f"`{item}`" for item in conflict.fact_ids)
            )
    return "\n".join(lines) + "\n"


SchemaModel: TypeAlias = type[BaseModel]
SchemaExport: TypeAlias = tuple[str, SchemaModel]
SCHEMA_EXPORTS: tuple[SchemaExport, ...] = (
    ("game_system_dossier.v1.schema.json", GameSystemDossierV1),
    ("game_content_graph.v1.schema.json", GameContentGraphV1),
    ("play_strategy_record.v1.schema.json", PlayStrategyRecordV1),
    ("execution_benchmark.v1.schema.json", ExecutionBenchmarkV1),
    ("execution_attempt_log.v1.schema.json", ExecutionAttemptLogV1),
    ("next_state_prediction.v1.schema.json", NextStatePredictionV1),
    ("prediction_adjudication.v1.schema.json", PredictionAdjudicationV1),
    ("new_session_preparation.v1.schema.json", NewSessionPreparationV1),
    ("recovery_route_preparation.v1.schema.json", RecoveryRoutePreparationV1),
)
MODEL_BY_SCHEMA_ID: dict[str, SchemaModel] = {
    "game-observatory.game-system-dossier.v1": GameSystemDossierV1,
    "game-observatory.game-content-graph.v1": GameContentGraphV1,
    "game-observatory.play-strategy-record.v1": PlayStrategyRecordV1,
    "game-observatory.execution-benchmark.v1": ExecutionBenchmarkV1,
    "game-observatory.execution-attempt-log.v1": ExecutionAttemptLogV1,
    "game-observatory.game-system-registry.v1": GameSystemRegistryV1,
    "game-observatory.next-state-prediction.v1": NextStatePredictionV1,
    "game-observatory.prediction-adjudication.v1": PredictionAdjudicationV1,
    "game-observatory.new-session-preparation.v1": NewSessionPreparationV1,
    "game-observatory.recovery-route-preparation.v1": RecoveryRoutePreparationV1,
}


def _append_all_of(target: dict[str, Any], clauses: Sequence[dict[str, Any]]) -> None:
    target.setdefault("allOf", []).extend(clauses)


def _inject_fact_constraints(target: dict[str, Any]) -> None:
    _append_all_of(
        target,
        (
            {
                "if": {"properties": {"status": {"const": "verified"}}, "required": ["status"]},
                "then": {
                    "properties": {"evidence_refs": {"minItems": 1}},
                    "required": ["evidence_refs"],
                },
            },
            {
                "if": {
                    "properties": {"status": {"enum": ["inferred", "unknown"]}},
                    "required": ["status"],
                },
                "then": {
                    "properties": {"rationale": {"type": "string", "minLength": 1}},
                    "required": ["rationale"],
                },
            },
            {
                "if": {"properties": {"status": {"const": "contradicted"}}, "required": ["status"]},
                "then": {
                    "properties": {
                        "evidence_refs": {"minItems": 1},
                        "conflict_fact_ids": {"minItems": 1},
                    },
                    "required": ["evidence_refs", "conflict_fact_ids"],
                },
            },
        ),
    )


def _inject_attempt_constraints(target: dict[str, Any]) -> None:
    _append_all_of(
        target,
        (
            {
                "if": {"properties": {"outcome": {"const": "effective"}}, "required": ["outcome"]},
                "then": {
                    "properties": {
                        "business_state_changed": {"const": True},
                        "business_change": {"type": "string", "minLength": 1},
                        "evidence_refs": {"minItems": 1},
                    },
                    "required": ["business_state_changed", "business_change", "evidence_refs"],
                },
            },
            {
                "if": {"properties": {"outcome": {"const": "rejected"}}, "required": ["outcome"]},
                "then": {
                    "properties": {
                        "rejection_category": {"type": "string"},
                        "rejection_reason": {"type": "string", "minLength": 1},
                        "business_state_changed": {"const": False},
                    },
                    "required": ["rejection_category", "rejection_reason"],
                },
            },
            {
                "if": {"properties": {"outcome": {"const": "failed"}}, "required": ["outcome"]},
                "then": {
                    "properties": {"failure_reason": {"type": "string", "minLength": 1}},
                    "required": ["failure_reason"],
                },
            },
            {
                "if": {
                    "properties": {"telemetry_event_ids": {"maxItems": 0}},
                    "required": ["telemetry_event_ids"],
                },
                "then": {
                    "properties": {
                        "canonical_source_gaps": {"contains": {"const": "telemetry_event_ids"}}
                    },
                    "required": ["canonical_source_gaps"],
                },
            },
            {
                "if": {
                    "properties": {
                        "canonical_source_gaps": {"contains": {"const": "telemetry_event_ids"}}
                    },
                    "required": ["canonical_source_gaps"],
                },
                "then": {"properties": {"telemetry_event_ids": {"maxItems": 0}}},
            },
            {
                "if": {
                    "properties": {
                        "execution_receipt_kind": {"const": "route_execution"},
                        "route_result_ids": {"maxItems": 0},
                    },
                    "required": ["execution_receipt_kind", "route_result_ids"],
                },
                "then": {
                    "properties": {
                        "canonical_source_gaps": {"contains": {"const": "route_result_ids"}}
                    },
                    "required": ["canonical_source_gaps"],
                },
            },
            {
                "if": {
                    "properties": {
                        "execution_receipt_kind": {"const": "route_execution"},
                        "canonical_source_gaps": {"contains": {"const": "route_result_ids"}},
                    },
                    "required": ["execution_receipt_kind", "canonical_source_gaps"],
                },
                "then": {"properties": {"route_result_ids": {"maxItems": 0}}},
            },
            {
                "if": {
                    "properties": {"execution_receipt_kind": {"const": "single_action_execution"}},
                    "required": ["execution_receipt_kind"],
                },
                "then": {
                    "properties": {
                        "route_result_ids": {"maxItems": 0},
                        "canonical_source_gaps": {
                            "not": {"contains": {"const": "route_result_ids"}}
                        },
                    }
                },
            },
        ),
    )


def _inject_guarded_route_constraints(target: dict[str, Any]) -> None:
    _append_all_of(
        target,
        (
            {
                "if": {
                    "properties": {"execution_status": {"const": "live_validated"}},
                    "required": ["execution_status"],
                },
                "then": {
                    "properties": {
                        "validation_evidence_refs": {"minItems": 1},
                        "validation_skill_run_ids": {"minItems": 1},
                    },
                    "required": ["validation_evidence_refs", "validation_skill_run_ids"],
                    "anyOf": [
                        {
                            "properties": {
                                "executable": {"const": True},
                                "execution_authorization": {"const": "benchmark"},
                                "blocking_reasons": {"maxItems": 0},
                            }
                        },
                        {
                            "properties": {
                                "executable": {"const": False},
                                "execution_authorization": {"const": "none"},
                                "blocking_reasons": {"minItems": 1},
                            }
                        },
                    ],
                },
            },
        ),
    )


def build_schema(model: SchemaModel) -> dict[str, Any]:
    """Build deterministic JSON Schema plus public cross-field conditions."""

    schema = model.model_json_schema(by_alias=True, mode="validation")
    definitions = schema.get("$defs", {})
    fact_schema = definitions.get("FactRecordV1")
    if isinstance(fact_schema, dict):
        _inject_fact_constraints(fact_schema)
    attempt_schema = definitions.get("ExecutionAttemptV1")
    if isinstance(attempt_schema, dict):
        _inject_attempt_constraints(attempt_schema)
    guarded_route_schema = definitions.get("GuardedBatchRoutePreparationV1")
    if isinstance(guarded_route_schema, dict):
        _inject_guarded_route_constraints(guarded_route_schema)
    if model is FactRecordV1:
        _inject_fact_constraints(schema)
    if model is ExecutionAttemptV1:
        _inject_attempt_constraints(schema)
    return {"$schema": JSON_SCHEMA_DIALECT, **schema}


def render_schema(model: SchemaModel) -> bytes:
    document = json.dumps(
        build_schema(model),
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    )
    return f"{document}\n".encode("utf-8")


def export_schemas(output_dir: str | Path = DEFAULT_SCHEMA_DIR) -> tuple[Path, ...]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, model in SCHEMA_EXPORTS:
        output_path = destination / filename
        output_path.write_bytes(render_schema(model))
        written.append(output_path)
    return tuple(written)


def validate_model_document(
    document: Mapping[str, Any], model: SchemaModel | None = None
) -> BaseModel:
    selected = model
    if selected is None:
        schema_id = document.get("schema")
        if not isinstance(schema_id, str) or schema_id not in MODEL_BY_SCHEMA_ID:
            raise ValueError("document schema does not identify a supported game model")
        selected = MODEL_BY_SCHEMA_ID[schema_id]
    return selected.model_validate(document)


def validate_model_path(path: str | Path, model: SchemaModel | None = None) -> BaseModel:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("game model document must contain one JSON object")
    return validate_model_document(document, model)
