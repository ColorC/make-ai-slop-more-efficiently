"""Strict, versioned contracts for persistent AI-player state.

These contracts deliberately contain no storage or execution behavior. They freeze the
phase-0 boundary shared by future state recognition, task, skill, recovery, guide, and
account-policy components.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from ..models import NormalizedAction, SourcePixelRect, utc_now


class _StrictContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
    )


def _require_unique(values: list[str], field_name: str) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return values


class EvidenceReferenceV1(_StrictContract):
    schema_id: Literal["game-observatory.ai-player.evidence-reference.v1"] = Field(
        default="game-observatory.ai-player.evidence-reference.v1",
        alias="schema",
    )
    environment_id: str = Field(min_length=1)
    artifact_ids: list[str] = Field(default_factory=list)
    evidence_run_ids: list[str] = Field(default_factory=list)
    evidence_step_ids: list[str] = Field(default_factory=list)
    trace_run_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    note: str = Field(default="", max_length=2000)

    @field_validator(
        "artifact_ids",
        "evidence_run_ids",
        "evidence_step_ids",
        "trace_run_ids",
        "source_ids",
    )
    @classmethod
    def validate_reference_ids(cls, value: list[str], info: Any) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError(f"{info.field_name} must contain non-empty ids")
        return _require_unique(value, str(info.field_name))

    @model_validator(mode="after")
    def require_resolvable_reference(self) -> "EvidenceReferenceV1":
        if not any(
            (
                self.artifact_ids,
                self.evidence_run_ids,
                self.evidence_step_ids,
                self.trace_run_ids,
                self.source_ids,
            )
        ):
            raise ValueError("an evidence reference must contain at least one canonical id")
        return self


class _EnvironmentBoundContract(_StrictContract):
    environment_id: str = Field(min_length=1)
    evidence_refs: list[EvidenceReferenceV1] = Field(min_length=1)

    @model_validator(mode="after")
    def keep_evidence_inside_environment(self) -> "_EnvironmentBoundContract":
        mismatches = [
            reference.environment_id
            for reference in self.evidence_refs
            if reference.environment_id != self.environment_id
        ]
        if mismatches:
            raise ValueError("evidence references must belong to the entity environment")
        return self


class EnvironmentScopeV1(_StrictContract):
    schema_id: Literal["game-observatory.ai-player.environment-scope.v1"] = Field(
        default="game-observatory.ai-player.environment-scope.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    game_id: str = Field(min_length=1)
    game_id_aliases: list[str] = Field(default_factory=list)
    build_scope_id: str = Field(min_length=1)
    build_scope_id_aliases: list[str] = Field(default_factory=list)
    account_scope_id: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    device_scope_id: str = Field(min_length=1)
    device_scope_id_aliases: list[str] = Field(default_factory=list)
    server_scope_id: str | None = Field(default=None, min_length=1)
    world_scope_id: str | None = Field(default=None, min_length=1)
    locale: str = Field(min_length=1)
    viewport_width: int = Field(gt=0)
    viewport_height: int = Field(gt=0)
    identity_hash: str = Field(min_length=16)
    evidence_refs: list[EvidenceReferenceV1] = Field(min_length=1)
    created_at: str = Field(default_factory=utc_now)

    @field_validator("game_id_aliases", "build_scope_id_aliases", "device_scope_id_aliases")
    @classmethod
    def validate_scope_aliases(cls, value: list[str], info: Any) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError(f"{info.field_name} must contain non-empty values")
        return _require_unique(value, str(info.field_name))

    @model_validator(mode="after")
    def keep_identity_evidence_inside_environment(self) -> "EnvironmentScopeV1":
        if any(reference.environment_id != self.id for reference in self.evidence_refs):
            raise ValueError("environment evidence references must use the environment id")
        return self


class EnvironmentPromotionV1(_StrictContract):
    """Append-only proof that a known environment identity became more specific."""

    schema_id: Literal["game-observatory.ai-player.environment-promotion.v1"] = Field(
        default="game-observatory.ai-player.environment-promotion.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    parent_environment_id: str = Field(min_length=1)
    child_environment: EnvironmentScopeV1
    terminal_identity_evidence: EvidenceReferenceV1
    confirmed_account_scope_id: str = Field(min_length=1)
    confirmed_server_scope_id: str | None = Field(default=None, min_length=1)
    confirmed_world_scope_id: str | None = Field(default=None, min_length=1)
    reason: str = Field(min_length=1, max_length=4000)
    promoted_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_promotion_boundary(self) -> "EnvironmentPromotionV1":
        child = self.child_environment
        proof = self.terminal_identity_evidence
        if child.id == self.parent_environment_id:
            raise ValueError("promotion child must differ from its parent")
        if proof.environment_id != self.parent_environment_id:
            raise ValueError("terminal identity evidence must originate in the parent environment")
        if not proof.evidence_run_ids or not proof.evidence_step_ids:
            raise ValueError("promotion requires terminal EvidenceRun and EvidenceStep proof")
        if self.confirmed_account_scope_id != child.account_scope_id:
            raise ValueError("confirmed account identity must match the child environment")
        if self.confirmed_server_scope_id != child.server_scope_id:
            raise ValueError("confirmed server identity must match the child environment")
        if self.confirmed_world_scope_id != child.world_scope_id:
            raise ValueError("confirmed world identity must match the child environment")

        child_run_ids = {
            item for reference in child.evidence_refs for item in reference.evidence_run_ids
        }
        child_step_ids = {
            item for reference in child.evidence_refs for item in reference.evidence_step_ids
        }
        if not set(proof.evidence_run_ids).issubset(child_run_ids):
            raise ValueError("child identity evidence must retain every promotion EvidenceRun")
        if not set(proof.evidence_step_ids).issubset(child_step_ids):
            raise ValueError("child identity evidence must retain every promotion EvidenceStep")
        return self


class EnvironmentSelectionV1(_StrictContract):
    """The unique current leaf selected from one immutable environment lineage."""

    schema_id: Literal["game-observatory.ai-player.environment-selection.v1"] = Field(
        default="game-observatory.ai-player.environment-selection.v1",
        alias="schema",
    )
    requested_environment_id: str = Field(min_length=1)
    selected_environment_id: str = Field(min_length=1)
    selected_environment: EnvironmentScopeV1
    lineage_path: list[str] = Field(min_length=1)
    lineage_statuses: dict[str, Literal["superseded", "current"]] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_leaf_selection(self) -> "EnvironmentSelectionV1":
        if self.lineage_path[0] != self.requested_environment_id:
            raise ValueError("lineage path must begin at the requested environment")
        if self.lineage_path[-1] != self.selected_environment_id:
            raise ValueError("lineage path must end at the selected environment")
        if self.selected_environment.id != self.selected_environment_id:
            raise ValueError("selected environment body does not match its id")
        if set(self.lineage_statuses) != set(self.lineage_path):
            raise ValueError("lineage statuses must cover exactly the selected path")
        if self.lineage_statuses[self.selected_environment_id] != "current":
            raise ValueError("selected lineage leaf must be current")
        if any(
            self.lineage_statuses[item] != "superseded"
            for item in self.lineage_path[:-1]
        ):
            raise ValueError("every ancestor on the selected path must be superseded")
        return self


MemoryKind = Literal[
    "identity_environment",
    "working",
    "episodic",
    "semantic",
    "procedural",
    "task",
    "failure_forbidden",
]


class MemoryRecordV1(_EnvironmentBoundContract):
    schema_id: Literal["game-observatory.ai-player.memory-record.v1"] = Field(
        default="game-observatory.ai-player.memory-record.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    kind: MemoryKind
    subject_id: str = Field(min_length=1)
    payload: dict[str, Any] = Field(min_length=1)
    status: Literal["active", "superseded", "invalidated"] = "active"
    supersedes_id: str | None = Field(default=None, min_length=1)
    invalidation_reason: str | None = Field(default=None, min_length=1)
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def require_invalidation_reason(self) -> "MemoryRecordV1":
        if self.status == "invalidated" and not self.invalidation_reason:
            raise ValueError("invalidated memory requires an invalidation reason")
        return self


class NormalizedSurfaceRectV1(_StrictContract):
    """Viewport-independent UI region in a 1000 by 1000 coordinate space."""

    schema_id: Literal["game-observatory.ai-player.normalized-surface-rect.v1"] = Field(
        default="game-observatory.ai-player.normalized-surface-rect.v1",
        alias="schema",
    )
    x: int = Field(ge=0, le=999)
    y: int = Field(ge=0, le=999)
    width: int = Field(gt=0, le=1000)
    height: int = Field(gt=0, le=1000)

    @model_validator(mode="after")
    def keep_inside_normalized_viewport(self) -> "NormalizedSurfaceRectV1":
        if self.x + self.width > 1000 or self.y + self.height > 1000:
            raise ValueError("normalized surface rect exceeds the 1000x1000 viewport")
        return self


class SemanticSurfaceAnchorV1(_StrictContract):
    """Reviewed locator hint for one interaction role; never a production route."""

    schema_id: Literal["game-observatory.ai-player.semantic-surface-anchor.v1"] = Field(
        default="game-observatory.ai-player.semantic-surface-anchor.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    target_tokens: list[str] = Field(min_length=1)
    action: Literal["tap", "long_press", "swipe"] = "tap"
    mobility: Literal["fixed_chrome", "fixed_surface", "dynamic_world_object"]
    normalized_bounds: NormalizedSurfaceRectV1
    reference_artifact_id: str | None = Field(default=None, min_length=1)

    @field_validator("target_tokens")
    @classmethod
    def validate_target_tokens(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("target_tokens must contain non-empty values")
        return _require_unique(value, "target_tokens")

    @model_validator(mode="after")
    def require_dynamic_reference(self) -> "SemanticSurfaceAnchorV1":
        if self.mobility == "dynamic_world_object" and self.reference_artifact_id is None:
            raise ValueError("dynamic surface anchors require a reference artifact")
        return self


class SemanticSurfaceProfileV1(_StrictContract):
    """Reusable stable semantics for recognizing and operating one UI surface."""

    schema_id: Literal["game-observatory.ai-player.semantic-surface-profile.v1"] = Field(
        default="game-observatory.ai-player.semantic-surface-profile.v1",
        alias="schema",
    )
    page_identity_tokens: list[str] = Field(min_length=1)
    dynamic_field_names: list[str] = Field(default_factory=list)
    interaction_roles: list[str] = Field(default_factory=list)
    safe_exit_tokens: list[str] = Field(default_factory=list)
    risk_boundary_tokens: list[str] = Field(default_factory=list)

    @field_validator(
        "page_identity_tokens",
        "dynamic_field_names",
        "interaction_roles",
        "safe_exit_tokens",
        "risk_boundary_tokens",
    )
    @classmethod
    def validate_surface_tokens(cls, value: list[str], info: Any) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError(f"{info.field_name} must contain non-empty tokens")
        return _require_unique(value, str(info.field_name))


class SemanticStateV1(_EnvironmentBoundContract):
    schema_id: Literal["game-observatory.ai-player.semantic-state.v1"] = Field(
        default="game-observatory.ai-player.semantic-state.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    semantic_fingerprint: str = Field(min_length=1)
    observation_feature_hashes: list[str] = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    surface_profile: SemanticSurfaceProfileV1 | None = None
    surface_anchors: list[SemanticSurfaceAnchorV1] = Field(
        default_factory=list,
        exclude_if=lambda value: not value,
    )
    status: Literal["candidate", "accepted", "superseded", "invalidated"] = "candidate"
    supersedes_id: str | None = Field(default=None, min_length=1)
    created_at: str = Field(default_factory=utc_now)

    @field_validator("observation_feature_hashes", "aliases", "tags")
    @classmethod
    def validate_unique_values(cls, value: list[str], info: Any) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError(f"{info.field_name} must contain non-empty values")
        return _require_unique(value, str(info.field_name))

    @model_validator(mode="after")
    def validate_surface_anchors(self) -> "SemanticStateV1":
        if not self.surface_anchors:
            return self
        if self.surface_profile is None:
            raise ValueError("surface anchors require a semantic surface profile")
        anchor_ids = [anchor.id for anchor in self.surface_anchors]
        _require_unique(anchor_ids, "surface_anchors.id")
        declared_roles = set(self.surface_profile.interaction_roles)
        unknown_roles = [
            anchor.role for anchor in self.surface_anchors if anchor.role not in declared_roles
        ]
        if unknown_roles:
            raise ValueError(
                "surface anchor roles must be declared interaction roles: "
                + ", ".join(unknown_roles)
            )
        evidence_artifact_ids = {
            artifact_id
            for reference in self.evidence_refs
            for artifact_id in reference.artifact_ids
        }
        missing_artifact_ids = [
            anchor.reference_artifact_id
            for anchor in self.surface_anchors
            if anchor.reference_artifact_id is not None
            and anchor.reference_artifact_id not in evidence_artifact_ids
        ]
        if missing_artifact_ids:
            raise ValueError(
                "surface anchor reference artifacts must belong to state evidence: "
                + ", ".join(missing_artifact_ids)
            )
        return self


class StateObservationFeaturesV1(_StrictContract):
    """Stable and volatile signals captured from one game frame."""

    schema_id: Literal["game-observatory.ai-player.state-observation-features.v1"] = Field(
        default="game-observatory.ai-player.state-observation-features.v1",
        alias="schema",
    )
    screenshot_fingerprint: str | None = Field(default=None, min_length=1)
    ui_structure_tokens: list[str] = Field(default_factory=list)
    ui_text_tokens: list[str] = Field(default_factory=list)
    runtime_tokens: list[str] = Field(default_factory=list)
    selected_object_tokens: list[str] = Field(default_factory=list)
    overlay_tokens: list[str] = Field(default_factory=list)
    page_identity_tokens: list[str] = Field(
        default_factory=list,
        exclude_if=lambda value: not value,
        description=(
            "Stable semantic page identity, excluding selected entities and dynamic values."
        ),
    )
    dynamic_field_names: list[str] = Field(
        default_factory=list,
        exclude_if=lambda value: not value,
        description=(
            "Names or typed roles of values that must be reread, never their current values."
        ),
    )
    interaction_roles: list[str] = Field(
        default_factory=list,
        exclude_if=lambda value: not value,
        description="Stable semantic roles of visible controls and gestures.",
    )
    safe_exit_tokens: list[str] = Field(
        default_factory=list,
        exclude_if=lambda value: not value,
        description="Stable safe exits and their expected return destinations.",
    )
    risk_boundary_tokens: list[str] = Field(
        default_factory=list,
        exclude_if=lambda value: not value,
        description=(
            "Stable preview, write, consumption, payment, or irreversible action boundaries."
        ),
    )
    region_fingerprints: dict[str, str] = Field(default_factory=dict)
    critical_features: dict[str, str] = Field(default_factory=dict)
    volatile_tokens: list[str] = Field(default_factory=list)

    @field_validator(
        "ui_structure_tokens",
        "ui_text_tokens",
        "runtime_tokens",
        "selected_object_tokens",
        "overlay_tokens",
        "page_identity_tokens",
        "dynamic_field_names",
        "interaction_roles",
        "safe_exit_tokens",
        "risk_boundary_tokens",
        "volatile_tokens",
    )
    @classmethod
    def validate_feature_tokens(cls, value: list[str], info: Any) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError(f"{info.field_name} must contain non-empty tokens")
        return _require_unique(value, str(info.field_name))

    @field_validator("region_fingerprints", "critical_features")
    @classmethod
    def validate_feature_maps(cls, value: dict[str, str], info: Any) -> dict[str, str]:
        if any(not key.strip() or not item.strip() for key, item in value.items()):
            raise ValueError(f"{info.field_name} must contain non-empty keys and values")
        return value

    @model_validator(mode="after")
    def require_stable_signal(self) -> "StateObservationFeaturesV1":
        if not any(
            (
                self.screenshot_fingerprint,
                self.ui_structure_tokens,
                self.ui_text_tokens,
                self.runtime_tokens,
                self.selected_object_tokens,
                self.overlay_tokens,
                self.page_identity_tokens,
                self.dynamic_field_names,
                self.interaction_roles,
                self.safe_exit_tokens,
                self.risk_boundary_tokens,
                self.region_fingerprints,
                self.critical_features,
            )
        ):
            raise ValueError("state observation requires at least one stable signal")
        return self


class StateObservationV1(_EnvironmentBoundContract):
    schema_id: Literal["game-observatory.ai-player.state-observation.v1"] = Field(
        default="game-observatory.ai-player.state-observation.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    viewport_width: int = Field(gt=0)
    viewport_height: int = Field(gt=0)
    features: StateObservationFeaturesV1
    feature_hash: str = Field(min_length=64, max_length=64)
    captured_at: str = Field(default_factory=utc_now)
    created_at: str = Field(default_factory=utc_now)


class StateAssignmentV1(_EnvironmentBoundContract):
    schema_id: Literal["game-observatory.ai-player.state-assignment.v1"] = Field(
        default="game-observatory.ai-player.state-assignment.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    observation_id: str = Field(min_length=1)
    state_id: str = Field(min_length=1)
    method: Literal[
        "exact_fingerprint",
        "nearest_prototype",
        "source_state_guard",
        "expected_state_guard",
        "new_candidate",
        "adjudicated_merge",
        "adjudicated_split",
        "recovery_recheck",
    ]
    confidence: float = Field(ge=0, le=1)
    reasons: list[str] = Field(min_length=1)
    status: Literal["active", "superseded", "invalidated"] = "active"
    supersedes_id: str | None = Field(default=None, min_length=1)
    created_at: str = Field(default_factory=utc_now)

    @field_validator("reasons")
    @classmethod
    def validate_reasons(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("assignment reasons must contain non-empty values")
        return _require_unique(value, "reasons")


class StateMatchV1(_StrictContract):
    schema_id: Literal["game-observatory.ai-player.state-match.v1"] = Field(
        default="game-observatory.ai-player.state-match.v1",
        alias="schema",
    )
    state_id: str = Field(min_length=1)
    score: float = Field(ge=0, le=1)
    critical_conflicts: list[str] = Field(default_factory=list)


class StateRecognitionDecisionV1(_StrictContract):
    schema_id: Literal["game-observatory.ai-player.state-recognition-decision.v1"] = Field(
        default="game-observatory.ai-player.state-recognition-decision.v1",
        alias="schema",
    )
    environment_id: str = Field(min_length=1)
    observation_id: str = Field(min_length=1)
    state_id: str = Field(min_length=1)
    assignment_id: str = Field(min_length=1)
    disposition: Literal["recognized_existing", "created_candidate", "needs_adjudication"]
    confidence: float = Field(ge=0, le=1)
    ranked_matches: list[StateMatchV1] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


class TransitionEdgeV1(_EnvironmentBoundContract):
    schema_id: Literal["game-observatory.ai-player.transition-edge.v1"] = Field(
        default="game-observatory.ai-player.transition-edge.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    from_state_id: str = Field(min_length=1)
    to_state_id: str | None = Field(default=None, min_length=1)
    action: NormalizedAction
    target_bounds: SourcePixelRect | None = None
    expected_change: str = Field(min_length=1)
    observed_change: str = Field(min_length=1)
    outcome: Literal[
        "verified_transition",
        "verified_state_change",
        "verified_progress",
        "verified_no_change",
        "failed",
        "forbidden",
        "deferred",
    ]
    recovery_skill_version_id: str | None = Field(default=None, min_length=1)
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def require_destination_for_changed_state(self) -> "TransitionEdgeV1":
        if self.outcome in {"verified_transition", "verified_state_change"} and not self.to_state_id:
            raise ValueError("a verified state-changing edge requires a destination state")
        return self


class FrontierTaskV1(_EnvironmentBoundContract):
    schema_id: Literal["game-observatory.ai-player.frontier-task.v1"] = Field(
        default="game-observatory.ai-player.frontier-task.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    title: str = Field(min_length=1)
    source: Literal[
        "user_goal",
        "unknown_interaction",
        "missing_transition",
        "stale_memory",
        "interface_family_gap",
        "new_unlock",
        "guide_update",
        "failed_skill",
        "gameplay_candidate",
        "coverage_gap",
    ]
    reason: str = Field(min_length=1)
    status: Literal[
        "queued",
        "active",
        "cooldown",
        "blocked",
        "completed",
        "failed",
        "invalidated",
    ] = "queued"
    dependency_task_ids: list[str] = Field(default_factory=list)
    value_score: float = 0.0
    novelty_score: float = 0.0
    expected_coverage_gain: float = 0.0
    risk_score: float = Field(default=0.0, ge=0.0)
    action_budget: int = Field(ge=0)
    token_budget: int | None = Field(default=None, ge=0)
    time_budget_seconds: float = Field(gt=0)
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=1, ge=1)
    cooldown_until: str | None = Field(default=None, min_length=1)
    blocked_reason: str | None = Field(default=None, min_length=1)
    reactivation_condition: str | None = Field(default=None, min_length=1)
    created_at: str = Field(default_factory=utc_now)

    @field_validator("dependency_task_ids")
    @classmethod
    def validate_dependencies(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("dependency task ids must be non-empty")
        return _require_unique(value, "dependency_task_ids")

    @model_validator(mode="after")
    def validate_task_lifecycle(self) -> "FrontierTaskV1":
        if self.attempt_count > self.max_attempts:
            raise ValueError("attempt count cannot exceed max attempts")
        if self.status == "cooldown" and not self.cooldown_until:
            raise ValueError("a cooling task requires cooldown_until")
        if self.status == "blocked" and (
            not self.blocked_reason or not self.reactivation_condition
        ):
            raise ValueError("a blocked task requires a reason and reactivation condition")
        return self


class SkillApplicabilityScopeV1(_StrictContract):
    schema_id: Literal["game-observatory.ai-player.skill-applicability-scope.v1"] = Field(
        default="game-observatory.ai-player.skill-applicability-scope.v1",
        alias="schema",
    )
    game_id: str = Field(min_length=1)
    build_scope_ids: list[str] = Field(min_length=1)
    channel: str = Field(min_length=1)
    locale: str = Field(min_length=1)
    device_scope_ids: list[str] = Field(min_length=1)
    viewport_widths: list[int] = Field(min_length=1)
    viewport_heights: list[int] = Field(min_length=1)
    required_state_ids: list[str] = Field(min_length=1)
    account_scope_ids: list[str] = Field(default_factory=list)
    server_scope_ids: list[str] = Field(default_factory=list)
    world_scope_ids: list[str] = Field(default_factory=list)
    visual_variant_ids: list[str] = Field(default_factory=list)

    @field_validator(
        "build_scope_ids",
        "device_scope_ids",
        "required_state_ids",
        "account_scope_ids",
        "server_scope_ids",
        "world_scope_ids",
        "visual_variant_ids",
    )
    @classmethod
    def validate_scope_ids(cls, value: list[str], info: Any) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError(f"{info.field_name} must contain non-empty values")
        return _require_unique(value, str(info.field_name))

    @field_validator("viewport_widths", "viewport_heights")
    @classmethod
    def validate_viewports(cls, value: list[int], info: Any) -> list[int]:
        if any(item <= 0 for item in value):
            raise ValueError(f"{info.field_name} must contain positive values")
        if len(value) != len(set(value)):
            raise ValueError(f"{info.field_name} must not contain duplicates")
        return value


class SkillLocatorV1(_StrictContract):
    schema_id: Literal["game-observatory.ai-player.skill-locator.v1"] = Field(
        default="game-observatory.ai-player.skill-locator.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    step_index: int = Field(ge=0)
    strategy: Literal[
        "source_pixel",
        "ui_selector",
        "visible_text",
        "template",
        "semantic_state",
        "adapter_native",
    ]
    selector: str = Field(min_length=1)
    reference_bounds: SourcePixelRect | None = None
    mobility: Literal[
        "fixed_chrome",
        "fixed_surface",
        "dynamic_world_object",
    ] | None = None
    reference_artifact_id: str | None = Field(default=None, min_length=1)
    search_region: SourcePixelRect | None = None
    match_threshold: float | None = Field(default=None, ge=0, le=1)
    fallback_locator_ids: list[str] = Field(default_factory=list)

    @field_validator("fallback_locator_ids")
    @classmethod
    def validate_fallbacks(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("fallback locator ids must contain non-empty values")
        return _require_unique(value, "fallback_locator_ids")

    @model_validator(mode="after")
    def validate_explicit_visual_locator(self) -> "SkillLocatorV1":
        # Legacy locators intentionally remain readable with mobility=None.  Once a
        # producer declares mobility, dynamic targets must carry enough information
        # for visual re-localization instead of silently falling back to source pixels.
        if self.mobility == "dynamic_world_object" and self.strategy != "template":
            raise ValueError("dynamic world object locators must use template strategy")
        if (
            self.mobility is not None
            and self.strategy == "template"
            and self.reference_artifact_id is None
        ):
            raise ValueError(
                "explicit template locators require a reference artifact id"
            )
        return self

    @property
    def has_declared_mobility(self) -> bool:
        """Whether the producer declared mobility rather than relying on legacy inference."""

        return self.mobility is not None

    @property
    def requires_visual_relocalization(self) -> bool:
        """Whether execution must locate the target again in the current frame."""

        return self.mobility == "dynamic_world_object"


class SkillStepV1(_StrictContract):
    schema_id: Literal["game-observatory.ai-player.skill-step.v1"] = Field(
        default="game-observatory.ai-player.skill-step.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    kind: Literal[
        "action",
        "observe",
        "assert",
        "subskill",
        "branch",
        "checkpoint",
        "recover",
    ]
    depends_on_step_ids: list[str] = Field(default_factory=list)
    action: NormalizedAction | None = None
    locator_id: str | None = Field(default=None, min_length=1)
    observation: str | None = Field(default=None, min_length=1)
    assertion: str | None = Field(default=None, min_length=1)
    expected_state_id: str | None = Field(default=None, min_length=1)
    subskill_version_id: str | None = Field(default=None, min_length=1)
    condition: str | None = Field(default=None, min_length=1)
    when_true_step_ids: list[str] = Field(default_factory=list)
    when_false_step_ids: list[str] = Field(default_factory=list)
    checkpoint_name: str | None = Field(default=None, min_length=1)
    max_attempts: int = Field(default=1, ge=1, le=3)
    idempotency: Literal[
        "read_only",
        "idempotent",
        "verify_before_retry",
        "never_retry",
    ]
    side_effect: Literal[
        "none",
        "reversible",
        "progression",
        "social",
        "economic",
        "restricted",
    ]

    @field_validator(
        "depends_on_step_ids",
        "when_true_step_ids",
        "when_false_step_ids",
    )
    @classmethod
    def validate_step_refs(cls, value: list[str], info: Any) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError(f"{info.field_name} must contain non-empty values")
        return _require_unique(value, str(info.field_name))

    @model_validator(mode="after")
    def validate_step_shape(self) -> "SkillStepV1":
        requirements = {
            "action": self.action is not None,
            "observe": self.observation is not None,
            "assert": self.assertion is not None,
            "subskill": self.subskill_version_id is not None,
            "branch": self.condition is not None
            and bool(self.when_true_step_ids or self.when_false_step_ids),
            "checkpoint": self.checkpoint_name is not None,
            "recover": self.subskill_version_id is not None,
        }
        if not requirements[self.kind]:
            raise ValueError(f"skill step {self.kind} is missing its typed payload")
        if self.kind != "action" and self.action is not None:
            raise ValueError("only an action step may contain a normalized action")
        if self.expected_state_id is not None and self.kind != "assert":
            raise ValueError("only an assert step may declare expected_state_id")
        if self.max_attempts > 1 and self.idempotency == "never_retry":
            raise ValueError("a never-retry step cannot allow multiple attempts")
        if self.side_effect in {"economic", "restricted"} and self.idempotency in {
            "read_only",
            "idempotent",
        }:
            raise ValueError("economic or restricted steps require explicit retry verification")
        return self


class SkillVersionV1(_EnvironmentBoundContract):
    schema_id: Literal["game-observatory.ai-player.skill-version.v1"] = Field(
        default="game-observatory.ai-player.skill-version.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    skill_id: str = Field(min_length=1)
    creator_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    level: Literal["L0", "L1", "L2", "L3", "L4", "L5", "L6"]
    skill_layer: Literal["atomic", "flow", "strategy"]
    scope: Literal["interaction", "surface", "gameplay", "cross_game"]
    execution_mode: Literal["planned", "interpreted", "compiled"]
    perception_tier: Literal["P0", "P1", "P2", "P3", "P4", "P5"]
    title: str = Field(min_length=1)
    applicability: str = Field(min_length=1)
    applicability_scope: SkillApplicabilityScopeV1
    safety_level: Literal[
        "read_only",
        "reversible",
        "progression",
        "social",
        "economic",
        "restricted",
    ]
    parameters_schema: dict[str, Any] = Field(default_factory=dict)
    preconditions: list[str] = Field(min_length=1)
    executor_kind: Literal[
        "normalized_actions",
        "maa",
        "airtest",
        "mineflayer",
        "specialized_adapter",
    ]
    executor_ref: str = Field(min_length=1)
    procedure_steps: list[str] = Field(min_length=1)
    locators: list[SkillLocatorV1] = Field(default_factory=list)
    steps: list[SkillStepV1] = Field(min_length=1)
    success_checks: list[str] = Field(min_length=1)
    failure_checks: list[str] = Field(min_length=1)
    recovery_skill_version_ids: list[str] = Field(default_factory=list)
    source_transition_ids: list[str] = Field(min_length=1)
    status: Literal["candidate", "validated", "preferred", "degraded", "invalidated"] = (
        "candidate"
    )
    validation_run_ids: list[str] = Field(default_factory=list)
    validation_id: str | None = Field(default=None, min_length=1)
    source_skill_version_id: str | None = Field(default=None, min_length=1)
    content_sha256: str = Field(min_length=64, max_length=64)
    independent_reset_count: int = Field(default=0, ge=0)
    visual_variant_count: int = Field(default=0, ge=0)
    failure_recovery_verified: bool = False
    invalidation_reason: str | None = Field(default=None, min_length=1)
    created_at: str = Field(default_factory=utc_now)

    @field_validator(
        "preconditions",
        "procedure_steps",
        "success_checks",
        "failure_checks",
        "recovery_skill_version_ids",
        "source_transition_ids",
        "validation_run_ids",
    )
    @classmethod
    def validate_skill_lists(cls, value: list[str], info: Any) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError(f"{info.field_name} must contain non-empty values")
        return _require_unique(value, str(info.field_name))

    @model_validator(mode="after")
    def validate_skill_lifecycle(self) -> "SkillVersionV1":
        safety_order = {
            "read_only": 0,
            "reversible": 1,
            "progression": 2,
            "social": 3,
            "economic": 4,
            "restricted": 5,
        }
        step_effect_order = {
            "none": 0,
            "reversible": 1,
            "progression": 2,
            "social": 3,
            "economic": 4,
            "restricted": 5,
        }
        if any(
            step_effect_order[step.side_effect] > safety_order[self.safety_level]
            for step in self.steps
        ):
            raise ValueError("a skill cannot hide a step above its declared safety level")
        step_ids = [step.id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("skill step ids must be unique")
        known_steps = set(step_ids)
        known_locators = {locator.id for locator in self.locators}
        if len(known_locators) != len(self.locators):
            raise ValueError("skill locator ids must be unique")
        for step in self.steps:
            referenced_steps = {
                *step.depends_on_step_ids,
                *step.when_true_step_ids,
                *step.when_false_step_ids,
            }
            if not referenced_steps.issubset(known_steps):
                raise ValueError("a skill step references an unknown step")
            if step.id in step.depends_on_step_ids:
                raise ValueError("a skill step cannot depend on itself")
            if step.locator_id is not None and step.locator_id not in known_locators:
                raise ValueError("a skill step references an unknown locator")
        visited: set[str] = set()
        visiting: set[str] = set()
        dependencies = {step.id: set(step.depends_on_step_ids) for step in self.steps}

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError("skill step dependencies must form an acyclic graph")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in dependencies[step_id]:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in step_ids:
            visit(step_id)
        expected_content_hash = self.compute_content_sha256()
        if self.content_sha256 != expected_content_hash:
            raise ValueError("skill content_sha256 does not match executable content")
        if self.status == "preferred" and (
            self.independent_reset_count < 3
            or self.visual_variant_count < 2
            or not self.failure_recovery_verified
            or not self.validation_run_ids
            or not self.validation_id
        ):
            raise ValueError("a preferred skill must pass independent replay and recovery gates")
        if (
            self.status == "preferred"
            and self.executor_kind == "normalized_actions"
            and (
                not any(step.kind == "assert" for step in self.steps)
                or any(
                    step.kind == "assert" and step.expected_state_id is None
                    for step in self.steps
                )
            )
        ):
            raise ValueError(
                "a preferred normalized-actions skill requires structured terminal states"
            )
        if self.status in {"degraded", "invalidated"} and not self.invalidation_reason:
            raise ValueError("a degraded or invalidated skill requires an invalidation reason")
        return self

    def content_payload(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "level": self.level,
            "skill_layer": self.skill_layer,
            "scope": self.scope,
            "execution_mode": self.execution_mode,
            "perception_tier": self.perception_tier,
            "title": self.title,
            "applicability": self.applicability,
            "applicability_scope": self.applicability_scope.model_dump(by_alias=True),
            "safety_level": self.safety_level,
            "parameters_schema": self.parameters_schema,
            "preconditions": self.preconditions,
            "executor_kind": self.executor_kind,
            "executor_ref": self.executor_ref,
            "procedure_steps": self.procedure_steps,
            "locators": [item.model_dump(by_alias=True) for item in self.locators],
            "steps": [item.model_dump(by_alias=True) for item in self.steps],
            "success_checks": self.success_checks,
            "failure_checks": self.failure_checks,
            "recovery_skill_version_ids": self.recovery_skill_version_ids,
            "source_transition_ids": self.source_transition_ids,
        }

    def compute_content_sha256(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.content_payload(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()


class SkillRunAttestationV1(_StrictContract):
    schema_id: Literal["game-observatory.ai-player.skill-run-attestation.v1"] = Field(
        default="game-observatory.ai-player.skill-run-attestation.v1",
        alias="schema",
    )
    validator_id: str = Field(min_length=1)
    key_id: str = Field(min_length=1)
    payload_sha256: str = Field(min_length=64, max_length=64)
    signature_base64: str = Field(min_length=1)
    issued_at: str = Field(default_factory=utc_now)


class SkillRunV1(_EnvironmentBoundContract):
    schema_id: Literal["game-observatory.ai-player.skill-run.v1"] = Field(
        default="game-observatory.ai-player.skill-run.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    skill_version_id: str = Field(min_length=1)
    validator_id: str = Field(min_length=1)
    provenance_evidence_run_id: str = Field(min_length=1)
    provenance_evidence_step_ids: list[str] = Field(min_length=1)
    provenance_sha256: str = Field(min_length=64, max_length=64)
    validator_attestation: SkillRunAttestationV1 | None = None
    attempt_index: int = Field(ge=1)
    independent_reset_id: str = Field(min_length=1)
    visual_variant_id: str = Field(min_length=1)
    outcome: Literal[
        "success",
        "failed",
        "precondition_unmet",
        "interrupted",
        "safety_blocked",
        "false_success",
    ]
    precondition_satisfied: bool
    objective_success: bool
    validation_passed: bool
    false_success: bool = False
    safety_violation_count: int = Field(default=0, ge=0)
    recovery_attempted: bool = False
    recovery_succeeded: bool = False
    action_count: int = Field(ge=0)
    model_input_tokens: int = Field(ge=0)
    baseline_model_input_tokens: int = Field(ge=0)
    decision_latency_ms: float = Field(ge=0)
    baseline_decision_latency_ms: float = Field(ge=0)
    started_at: str = Field(default_factory=utc_now)
    finished_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_run_result(self) -> "SkillRunV1":
        if len(self.provenance_evidence_step_ids) != len(
            set(self.provenance_evidence_step_ids)
        ):
            raise ValueError("skill run provenance step ids must be unique")
        if self.outcome == "success" and (
            not self.precondition_satisfied
            or not self.objective_success
            or not self.validation_passed
            or self.false_success
            or self.safety_violation_count
        ):
            raise ValueError("a successful skill run requires a true, safe objective result")
        if self.false_success != (self.outcome == "false_success"):
            raise ValueError("false_success must agree with the run outcome")
        if self.outcome == "precondition_unmet" and self.precondition_satisfied:
            raise ValueError("a precondition-unmet run cannot satisfy its precondition")
        if self.outcome == "failed" and self.validation_passed:
            raise ValueError("a failed run cannot pass validation")
        if self.outcome == "interrupted" and self.validation_passed and (
            not self.recovery_attempted or not self.recovery_succeeded
        ):
            raise ValueError("a handled interruption requires successful recovery")
        if self.outcome == "safety_blocked" and self.validation_passed and (
            self.safety_violation_count
        ):
            raise ValueError("a correctly blocked run cannot contain a safety violation")
        if self.recovery_succeeded and not self.recovery_attempted:
            raise ValueError("recovery cannot succeed when it was not attempted")
        if (
            self.validator_attestation is not None
            and self.validator_attestation.validator_id != self.validator_id
        ):
            raise ValueError("skill run attestation validator must match the run validator")
        return self


class SkillValidationV1(_EnvironmentBoundContract):
    schema_id: Literal["game-observatory.ai-player.skill-validation.v1"] = Field(
        default="game-observatory.ai-player.skill-validation.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    skill_version_id: str = Field(min_length=1)
    skill_run_ids: list[str] = Field(min_length=1)
    evaluator: str = Field(min_length=1)
    status: Literal["passed", "failed"]
    total_run_count: int = Field(ge=1)
    successful_run_count: int = Field(ge=0)
    false_success_count: int = Field(ge=0)
    safety_violation_count: int = Field(ge=0)
    independent_reset_count: int = Field(ge=0)
    visual_variant_count: int = Field(ge=0)
    unmet_precondition_count: int = Field(ge=0)
    interruption_count: int = Field(ge=0)
    successful_recovery_count: int = Field(ge=0)
    evidence_reuse_count: int = Field(ge=0)
    success_rate: float = Field(ge=0, le=1)
    token_reduction_rate: float = Field(ge=-1, le=1)
    latency_reduction_rate: float = Field(ge=-1, le=1)
    reasons: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)

    @field_validator("skill_run_ids", "reasons")
    @classmethod
    def validate_validation_lists(cls, value: list[str], info: Any) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError(f"{info.field_name} must contain non-empty values")
        return _require_unique(value, str(info.field_name))

    @model_validator(mode="after")
    def validate_gate_result(self) -> "SkillValidationV1":
        if self.total_run_count != len(self.skill_run_ids):
            raise ValueError("total_run_count must equal the number of skill runs")
        if self.successful_run_count > self.total_run_count:
            raise ValueError("successful runs cannot exceed total runs")
        expected_rate = self.successful_run_count / self.total_run_count
        if abs(self.success_rate - expected_rate) > 1e-9:
            raise ValueError("success_rate must be derived from run counts")
        passed = (
            self.total_run_count >= 20
            and self.success_rate >= 0.95
            and self.false_success_count == 0
            and self.safety_violation_count == 0
            and self.independent_reset_count >= 3
            and self.visual_variant_count >= 2
            and self.unmet_precondition_count >= 1
            and self.interruption_count >= 1
            and self.successful_recovery_count >= 1
            and self.evidence_reuse_count == 0
            and self.token_reduction_rate >= 0.40
            and self.latency_reduction_rate >= 0.30
        )
        if self.status == "passed" and not passed:
            raise ValueError("a passed validation must satisfy every skill acceptance gate")
        if self.status == "failed" and not self.reasons:
            raise ValueError("a failed validation requires reasons")
        return self


class PendingActionV1(_EnvironmentBoundContract):
    schema_id: Literal["game-observatory.ai-player.pending-action.v1"] = Field(
        default="game-observatory.ai-player.pending-action.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    request_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    intent: str = Field(min_length=1)
    action: NormalizedAction
    issued_at: str = Field(default_factory=utc_now)
    action_run_id: str | None = Field(default=None, min_length=1)
    effect_status: Literal["unknown", "confirmed", "failed"] = "unknown"
    after_evidence_refs: list[EvidenceReferenceV1] = Field(default_factory=list)
    effect_checked_at: str | None = Field(default=None, min_length=1)
    result_summary: str | None = Field(default=None, min_length=1)
    blind_replay_allowed: Literal[False] = False
    resume_rule: Literal["reobserve_before_any_action", "do_not_replay"] = (
        "reobserve_before_any_action"
    )

    @model_validator(mode="after")
    def prevent_blind_replay(self) -> "PendingActionV1":
        if any(
            reference.environment_id != self.environment_id
            for reference in self.after_evidence_refs
        ):
            raise ValueError("after evidence must belong to the pending-action environment")
        if self.effect_status in {"confirmed", "failed"} and (
            not self.after_evidence_refs or not self.effect_checked_at
        ):
            raise ValueError("a resolved pending action requires checked after evidence")
        return self


class SessionCapsuleV1(_EnvironmentBoundContract):
    schema_id: Literal["game-observatory.ai-player.session-capsule.v1"] = Field(
        default="game-observatory.ai-player.session-capsule.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    last_confirmed_state_id: str | None = Field(default=None, min_length=1)
    active_task_ids: list[str] = Field(default_factory=list)
    subgoal_stack: list[str] = Field(default_factory=list)
    remaining_action_budget: int = Field(ge=0)
    remaining_token_budget: int | None = Field(default=None, ge=0)
    remaining_time_seconds: float = Field(ge=0)
    pending_frontier_task_ids: list[str] = Field(default_factory=list)
    known_external_side_effects: list[str] = Field(default_factory=list)
    device_lease_id: str | None = Field(default=None, min_length=1)
    pending_action: PendingActionV1 | None = None
    stop_reason: str = Field(min_length=1)
    created_at: str = Field(default_factory=utc_now)

    @field_validator(
        "active_task_ids",
        "subgoal_stack",
        "pending_frontier_task_ids",
        "known_external_side_effects",
    )
    @classmethod
    def validate_capsule_lists(cls, value: list[str], info: Any) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError(f"{info.field_name} must contain non-empty values")
        return _require_unique(value, str(info.field_name))

    @model_validator(mode="after")
    def keep_pending_action_inside_environment(self) -> "SessionCapsuleV1":
        if self.pending_action and self.pending_action.environment_id != self.environment_id:
            raise ValueError("pending action must belong to the capsule environment")
        return self


class NavigationFrameV1(_StrictContract):
    """One proven entry into a child surface in the current live session."""

    schema_id: Literal["game-observatory.ai-player.navigation-frame.v1"] = Field(
        default="game-observatory.ai-player.navigation-frame.v1",
        alias="schema",
    )
    caller_state_id: str = Field(min_length=1)
    entered_state_id: str = Field(min_length=1)
    forward_skill_version_id: str = Field(min_length=1)
    return_skill_version_id: str = Field(min_length=1)
    source_evidence_step_id: str = Field(min_length=1)
    terminal_evidence_step_id: str = Field(min_length=1)
    created_at: str = Field(default_factory=utc_now)


class NavigationStackV1(_StrictContract):
    """Persisted caller context for deterministic Back/close replay."""

    schema_id: Literal["game-observatory.ai-player.navigation-stack.v1"] = Field(
        default="game-observatory.ai-player.navigation-stack.v1",
        alias="schema",
    )
    environment_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    current_state_id: str = Field(min_length=1)
    frames: list[NavigationFrameV1] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now)


class GuideKnowledgeV1(_EnvironmentBoundContract):
    schema_id: Literal["game-observatory.ai-player.guide-knowledge.v1"] = Field(
        default="game-observatory.ai-player.guide-knowledge.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    url: HttpUrl
    platform: str = Field(min_length=1)
    author: str = Field(min_length=1)
    published_at: str | None = Field(default=None, min_length=1)
    updated_at: str | None = Field(default=None, min_length=1)
    retrieved_at: str = Field(min_length=1)
    fresh_until: datetime | None = None
    applicable_build_scope_id: str | None = Field(default=None, min_length=1)
    applicable_account_scope_id: str | None = Field(default=None, min_length=1)
    applicable_channel: str | None = Field(default=None, min_length=1)
    applicable_game_version: str | None = Field(default=None, min_length=1)
    season: str | None = Field(default=None, min_length=1)
    server_stage: str | None = Field(default=None, min_length=1)
    summary: str = Field(min_length=1)
    locators: list[str] = Field(min_length=1)
    status: Literal["current", "unverified", "stale", "contradicted"] = "unverified"
    missing_applicability_reason: str | None = Field(default=None, min_length=1)
    stale_reason: str | None = Field(default=None, min_length=1)
    contradiction_summary: str | None = Field(default=None, min_length=1)
    triggering_task_ids: list[str] = Field(default_factory=list)
    live_contradiction_evidence_refs: list[EvidenceReferenceV1] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)

    @field_validator("locators", "triggering_task_ids")
    @classmethod
    def validate_guide_lists(cls, value: list[str], info: Any) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError(f"guide {info.field_name} must contain non-empty values")
        return _require_unique(value, str(info.field_name))

    @model_validator(mode="after")
    def validate_source_and_applicability(self) -> "GuideKnowledgeV1":
        if not self.published_at and not self.updated_at:
            raise ValueError("guide knowledge requires a publication or update time")
        applicability = (
            self.applicable_build_scope_id,
            self.applicable_account_scope_id,
            self.applicable_channel,
            self.applicable_game_version,
            self.season,
            self.server_stage,
        )
        if any(item is None for item in applicability):
            if self.status != "unverified" or not self.missing_applicability_reason:
                raise ValueError(
                    "missing environment applicability requires unverified status and a reason"
                )
        if self.status == "stale" and not self.stale_reason:
            raise ValueError("stale guide knowledge requires a stale reason")
        if self.status == "current" and self.fresh_until is None:
            raise ValueError("current guide knowledge requires a freshness deadline")
        if self.fresh_until is not None and self.fresh_until.tzinfo is None:
            raise ValueError("guide freshness deadline must include a timezone")
        if self.fresh_until is not None:
            try:
                retrieved_at = datetime.fromisoformat(self.retrieved_at.replace("Z", "+00:00"))
            except ValueError as error:
                raise ValueError("guide retrieval time must use ISO-8601") from error
            if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
                raise ValueError("guide retrieval time must include a timezone")
            if self.fresh_until <= retrieved_at:
                raise ValueError("guide freshness deadline must be after retrieval time")
        if self.status == "contradicted" and (
            not self.contradiction_summary or not self.live_contradiction_evidence_refs
        ):
            raise ValueError("contradicted guide knowledge requires live contradictory evidence")
        if any(
            reference.environment_id != self.environment_id
            for reference in self.live_contradiction_evidence_refs
        ):
            raise ValueError("contradiction evidence must belong to the guide environment")
        return self


class GameplayCandidateV1(_EnvironmentBoundContract):
    schema_id: Literal["game-observatory.ai-player.gameplay-candidate.v1"] = Field(
        default="game-observatory.ai-player.gameplay-candidate.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    game_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    status: Literal["candidate", "scope_review", "closed", "invalidated"] = "candidate"
    triggering_task_ids: list[str] = Field(min_length=1)
    entry_state_ids: list[str] = Field(min_length=1)
    main_state_ids: list[str] = Field(min_length=1)
    transition_edge_ids: list[str] = Field(min_length=1)
    interface_family_ids: list[str] = Field(default_factory=list)
    rule_clues: list[str] = Field(min_length=1)
    resource_or_progression_clues: list[str] = Field(min_length=1)
    exit_state_ids: list[str] = Field(default_factory=list)
    adjacent_gameplay_candidate_ids: list[str] = Field(default_factory=list)
    adjacent_gameplay_labels: list[str] = Field(default_factory=list)
    boundary_summary: str = Field(min_length=1)
    created_at: str = Field(default_factory=utc_now)

    @field_validator(
        "triggering_task_ids",
        "entry_state_ids",
        "main_state_ids",
        "transition_edge_ids",
        "interface_family_ids",
        "rule_clues",
        "resource_or_progression_clues",
        "exit_state_ids",
        "adjacent_gameplay_candidate_ids",
        "adjacent_gameplay_labels",
    )
    @classmethod
    def validate_candidate_lists(cls, value: list[str], info: Any) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError(f"{info.field_name} must contain non-empty values")
        return _require_unique(value, str(info.field_name))

    @model_validator(mode="after")
    def require_exit_or_adjacency(self) -> "GameplayCandidateV1":
        if not (
            self.exit_state_ids
            or self.adjacent_gameplay_candidate_ids
            or self.adjacent_gameplay_labels
        ):
            raise ValueError("a gameplay candidate requires an exit or adjacent gameplay")
        return self


ProtectedAccountAction = Literal[
    "real_money_payment",
    "external_personal_identity_submission",
]

_REQUIRED_AUTONOMOUS_ACCOUNT_ACTIONS = {
    "virtual_resource_use",
    "recruitment",
    "character_growth",
    "inventory_and_equipment",
    "construction_and_research",
    "route_choice",
    "map_actions",
    "combat",
    "tasks_and_events",
    "in_game_mail",
    "alliance_join_leave",
    "alliance_collaboration",
    "normal_in_game_communication",
    "native_game_automation",
}
_REQUIRED_PROTECTED_ACCOUNT_ACTIONS = {
    "real_money_payment",
    "external_personal_identity_submission",
}


class AccountActionPolicyV1(_EnvironmentBoundContract):
    schema_id: Literal["game-observatory.ai-player.account-action-policy.v1"] = Field(
        default="game-observatory.ai-player.account-action-policy.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    ai_identity_label: str = Field(min_length=1)
    game_internal_action_default: Literal["autonomous"] = "autonomous"
    autonomous_actions: list[str] = Field(
        default_factory=lambda: sorted(_REQUIRED_AUTONOMOUS_ACCOUNT_ACTIONS)
    )
    explicit_authorization_actions: list[ProtectedAccountAction] = Field(
        default_factory=lambda: sorted(_REQUIRED_PROTECTED_ACCOUNT_ACTIONS)
    )
    authorization_required_per_action: Literal[True] = True
    impersonation_allowed: Literal[False] = False
    private_identity_access_allowed: Literal[False] = False
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def preserve_account_authority_boundary(self) -> "AccountActionPolicyV1":
        autonomous = set(self.autonomous_actions)
        protected = set(self.explicit_authorization_actions)
        if len(autonomous) != len(self.autonomous_actions):
            raise ValueError("autonomous account actions must not contain duplicates")
        if len(protected) != len(self.explicit_authorization_actions):
            raise ValueError("protected account actions must not contain duplicates")
        if any(not action.strip() for action in self.autonomous_actions):
            raise ValueError("autonomous account actions must be non-empty")
        if not _REQUIRED_AUTONOMOUS_ACCOUNT_ACTIONS.issubset(autonomous):
            raise ValueError("the AI account must retain all normal autonomous game actions")
        if protected != _REQUIRED_PROTECTED_ACCOUNT_ACTIONS:
            raise ValueError("payment and external identity submission require explicit authorization")
        if autonomous.intersection(protected):
            raise ValueError("protected account actions cannot be autonomous")
        return self


class SpeechIntentV1(_EnvironmentBoundContract):
    schema_id: Literal["game-observatory.ai-player.speech-intent.v1"] = Field(
        default="game-observatory.ai-player.speech-intent.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    policy_id: str = Field(min_length=1)
    triggering_task_id: str = Field(min_length=1)
    ai_identity_label: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    recipients: list[str] = Field(min_length=1)
    purpose: str = Field(min_length=1)
    message_text: str = Field(min_length=1)
    status: Literal["draft", "authorized", "blocked", "cancelled"] = "draft"
    policy_disposition: Literal["autonomous", "awaiting_authorization", "rejected"]
    authorization_action: ProtectedAccountAction | None = None
    created_at: str = Field(default_factory=utc_now)

    @field_validator("recipients")
    @classmethod
    def validate_recipients(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("speech recipients must contain non-empty values")
        return _require_unique(value, "recipients")

    @model_validator(mode="after")
    def preserve_draft_send_boundary(self) -> "SpeechIntentV1":
        if self.policy_disposition == "autonomous" and self.authorization_action is not None:
            raise ValueError("an autonomous speech intent cannot require external authorization")
        if self.policy_disposition == "awaiting_authorization" and self.authorization_action is None:
            raise ValueError("an awaiting speech intent requires an authorization action")
        if self.status == "authorized" and self.policy_disposition != "autonomous":
            raise ValueError("only an autonomous policy decision may authorize in-game speech")
        if self.status == "blocked" and self.policy_disposition == "autonomous":
            raise ValueError("an autonomous speech intent cannot be policy-blocked")
        return self


class SpeechEventV1(_EnvironmentBoundContract):
    schema_id: Literal["game-observatory.ai-player.speech-event.v1"] = Field(
        default="game-observatory.ai-player.speech-event.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    speech_intent_id: str = Field(min_length=1)
    speech_intent_version: int = Field(ge=1)
    status: Literal["sent", "failed", "blocked"]
    evidence_step_id: str | None = Field(default=None, min_length=1)
    action_run_id: str | None = Field(default=None, min_length=1)
    system_response: str = Field(min_length=1)
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def require_send_evidence(self) -> "SpeechEventV1":
        if self.status == "sent" and (not self.evidence_step_id or not self.action_run_id):
            raise ValueError("a sent speech event requires an evidence step and action run")
        return self


class PlayerMetricDeltaV1(_StrictContract):
    """One evidence-bound numeric change in the game account or its objectives."""

    schema_id: Literal["game-observatory.ai-player.player-metric-delta.v1"] = Field(
        default="game-observatory.ai-player.player-metric-delta.v1",
        alias="schema",
    )
    metric_key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    category: Literal[
        "account_progression",
        "resource",
        "objective",
        "coverage",
    ]
    before: float
    after: float
    delta: float
    unit: str = Field(min_length=1)
    favorable: bool
    evidence_step_ids: list[str] = Field(min_length=1)

    @field_validator("evidence_step_ids")
    @classmethod
    def validate_metric_evidence(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("metric evidence step ids must contain non-empty values")
        return _require_unique(value, "evidence_step_ids")

    @model_validator(mode="after")
    def verify_numeric_delta(self) -> "PlayerMetricDeltaV1":
        tolerance = max(1e-9, abs(self.after - self.before) * 1e-9)
        if abs((self.after - self.before) - self.delta) > tolerance:
            raise ValueError("metric delta must equal after minus before")
        return self


PlayerSoftSignalName = Literal[
    "tutorial_comprehension",
    "intent_coherence",
    "opportunity_awareness",
    "strategic_continuity",
    "curiosity_quality",
    "loop_avoidance",
    "player_naturalness",
]


class PlayerSoftSignalV1(_StrictContract):
    """Evidence-bound reviewer judgement that can trigger work but never pass a hard gate."""

    schema_id: Literal["game-observatory.ai-player.player-soft-signal.v1"] = Field(
        default="game-observatory.ai-player.player-soft-signal.v1",
        alias="schema",
    )
    signal: PlayerSoftSignalName
    score: int = Field(ge=1, le=5)
    rationale: str = Field(min_length=1)
    evidence_step_ids: list[str] = Field(min_length=1)

    @field_validator("evidence_step_ids")
    @classmethod
    def validate_soft_signal_evidence(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("soft-signal evidence step ids must contain non-empty values")
        return _require_unique(value, "evidence_step_ids")


PlayerSoftSignalReviewerRole = Literal[
    "independent_agent",
    "human",
    "runtime_critic",
]

PlayerSoftSignalReviewTrustScope = Literal[
    "formal_external",
    "development_only",
]


class PlayerSoftSignalReviewAttestationV1(_StrictContract):
    """Signed identity statement for one independent soft-signal review."""

    schema_id: Literal[
        "game-observatory.ai-player.player-soft-signal-review-attestation.v1"
    ] = Field(
        default="game-observatory.ai-player.player-soft-signal-review-attestation.v1",
        alias="schema",
    )
    reviewer_id: str = Field(min_length=1)
    reviewer_role: PlayerSoftSignalReviewerRole
    reviewer_run_id: str = Field(min_length=1)
    key_id: str = Field(min_length=1)
    payload_sha256: str = Field(min_length=64, max_length=64)
    signature_base64: str = Field(min_length=1)
    issued_at: str = Field(default_factory=utc_now)


class PlayerSoftSignalReviewV1(_EnvironmentBoundContract):
    """Append-only independent judgement over canonical action-quality samples."""

    schema_id: Literal["game-observatory.ai-player.player-soft-signal-review.v1"] = Field(
        default="game-observatory.ai-player.player-soft-signal-review.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    sample_ids: list[str] = Field(min_length=1)
    subject_session_ids: list[str] = Field(min_length=1)
    reviewer_id: str = Field(min_length=1)
    reviewer_role: PlayerSoftSignalReviewerRole
    trust_scope: PlayerSoftSignalReviewTrustScope = "formal_external"
    reviewer_session_id: str | None = Field(default=None, min_length=1)
    review_evidence_run_id: str = Field(min_length=1)
    review_evidence_step_id: str = Field(min_length=1)
    signals: list[PlayerSoftSignalV1] = Field(min_length=1)
    responds_to_request_id: str | None = Field(default=None, min_length=1)
    attestation: PlayerSoftSignalReviewAttestationV1 | None = None
    reviewed_at: str = Field(default_factory=utc_now)

    @field_validator("sample_ids", "subject_session_ids")
    @classmethod
    def validate_review_subject_ids(cls, value: list[str], info: Any) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError(f"{info.field_name} must contain non-empty ids")
        return _require_unique(value, str(info.field_name))

    def attestation_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"attestation"},
        )

    def compute_attestation_payload_sha256(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.attestation_payload(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    @model_validator(mode="after")
    def preserve_independent_attestation(self) -> "PlayerSoftSignalReviewV1":
        signal_names = [item.signal for item in self.signals]
        if len(signal_names) != len(set(signal_names)):
            raise ValueError("soft-signal review signals must be unique")
        if self.reviewer_session_id in set(self.subject_session_ids):
            raise ValueError("reviewer session cannot be one of the executing sessions")
        if self.attestation is None:
            return self
        if self.attestation.reviewer_id != self.reviewer_id:
            raise ValueError("soft-signal attestation reviewer id must match the review")
        if self.attestation.reviewer_role != self.reviewer_role:
            raise ValueError("soft-signal attestation reviewer role must match the review")
        if self.attestation.payload_sha256 != self.compute_attestation_payload_sha256():
            raise ValueError("soft-signal attestation payload hash does not match the review")
        return self


class PlayerSoftSignalReviewRequestV1(_EnvironmentBoundContract):
    """Non-device work item emitted by a low independent soft-signal score."""

    schema_id: Literal[
        "game-observatory.ai-player.player-soft-signal-review-request.v1"
    ] = Field(
        default="game-observatory.ai-player.player-soft-signal-review-request.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    trigger_review_id: str = Field(min_length=1)
    sample_ids: list[str] = Field(min_length=1)
    signal_names: list[PlayerSoftSignalName] = Field(min_length=1)
    reason: str = Field(min_length=1)
    execution_mode: Literal["non_device_review"] = "non_device_review"
    device_action_budget: Literal[0] = 0
    status: Literal["open"] = "open"
    created_at: str = Field(default_factory=utc_now)

    @field_validator("sample_ids", "signal_names")
    @classmethod
    def validate_review_request_lists(cls, value: list[str], info: Any) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError(f"{info.field_name} must not contain duplicates")
        return value


class ActionQualitySampleV1(_EnvironmentBoundContract):
    """One proposal/execution sample for the continuously improving AI player."""

    schema_id: Literal["game-observatory.ai-player.action-quality-sample.v1"] = Field(
        default="game-observatory.ai-player.action-quality-sample.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    task_id: str | None = Field(default=None, min_length=1)
    semantic_state_id: str | None = Field(default=None, min_length=1)
    command_id: str = Field(min_length=1)
    action_run_id: str | None = Field(default=None, min_length=1)
    evidence_step_id: str | None = Field(default=None, min_length=1)
    decision_mode: Literal["new_state", "known_state", "skill_replay", "recovery"]
    execution_disposition: Literal["executed", "rejected"]
    preflight_disposition: Literal["passed", "rejected", "not_applicable"]
    outcome: Literal[
        "confirmed",
        "rejected",
        "no_effect",
        "blocked_by_overlay",
        "wrong_target",
        "unsettled",
        "failed",
    ]
    expected_change: str = Field(min_length=1)
    expected_change_measurement_status: Literal["measured", "unavailable"] = (
        "unavailable"
    )
    expected_change_matched: bool | None = None
    adapter_call_count: int = Field(ge=0, le=1)
    invalid_target_execution: bool = False
    policy_violation: bool = False
    evidence_complete: bool
    meaningful_change: bool
    task_progress: bool
    objective_completed: bool = False
    information_gain_units: int = Field(default=0, ge=0)
    new_state_count: int = Field(default=0, ge=0)
    new_transition_count: int = Field(default=0, ge=0)
    new_interface_count: int = Field(default=0, ge=0)
    new_gameplay_count: int = Field(default=0, ge=0)
    new_rule_count: int = Field(default=0, ge=0)
    target_cluster_id: str | None = Field(default=None, min_length=1)
    prior_cluster_failures: int = Field(default=0, ge=0)
    recovery_succeeded: bool = False
    task_queue_falsely_empty: bool = False
    token_measurement_status: Literal["measured", "shared_batch", "unavailable"]
    model_input_tokens: int | None = Field(default=None, ge=0)
    model_output_tokens: int | None = Field(default=None, ge=0)
    model_usage_group_id: str | None = Field(default=None, min_length=1)
    model_usage_group_action_count: int | None = Field(default=None, ge=2)
    model_usage_group_input_tokens: int | None = Field(default=None, ge=0)
    model_usage_group_output_tokens: int | None = Field(default=None, ge=0)
    baseline_model_input_tokens: int | None = Field(default=None, gt=0)
    decision_latency_ms: int | None = Field(default=None, ge=0)
    baseline_decision_latency_ms: int | None = Field(default=None, gt=0)
    account_metric_deltas: list[PlayerMetricDeltaV1] = Field(default_factory=list)
    soft_signals: list[PlayerSoftSignalV1] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def preserve_action_quality_truth(self) -> "ActionQualitySampleV1":
        coverage_gain = sum(
            (
                self.new_state_count,
                self.new_transition_count,
                self.new_interface_count,
                self.new_gameplay_count,
                self.new_rule_count,
            )
        )
        if self.execution_disposition == "executed":
            if self.preflight_disposition == "rejected":
                raise ValueError("a rejected preflight cannot execute an adapter action")
            if self.adapter_call_count != 1 or not self.action_run_id or not self.evidence_step_id:
                raise ValueError("an executed sample requires one adapter call and terminal ids")
        else:
            if self.preflight_disposition != "rejected":
                raise ValueError("a rejected proposal requires a rejected preflight")
            if self.adapter_call_count != 0 or self.action_run_id or self.evidence_step_id:
                raise ValueError("a rejected proposal cannot claim device execution")
            if self.outcome != "rejected":
                raise ValueError("a rejected proposal must use the rejected outcome")
        shared_usage = (
            self.model_usage_group_id,
            self.model_usage_group_action_count,
            self.model_usage_group_input_tokens,
            self.model_usage_group_output_tokens,
        )
        if self.token_measurement_status == "measured":
            if self.model_input_tokens is None or self.model_output_tokens is None:
                raise ValueError("measured token telemetry requires input and output counts")
            if any(item is not None for item in shared_usage):
                raise ValueError("per-action token telemetry cannot also claim a shared batch")
        elif self.token_measurement_status == "shared_batch":
            if self.model_input_tokens is not None or self.model_output_tokens is not None:
                raise ValueError("shared-batch telemetry cannot claim per-action token counts")
            if any(item is None for item in shared_usage):
                raise ValueError("shared-batch telemetry requires a complete usage group")
        elif (
            self.model_input_tokens is not None
            or self.model_output_tokens is not None
            or any(item is not None for item in shared_usage)
        ):
            raise ValueError("unavailable token telemetry cannot contain token counts")
        if self.expected_change_measurement_status == "measured":
            if self.expected_change_matched is None:
                raise ValueError(
                    "measured expected change requires an explicit match result"
                )
        elif self.expected_change_matched is not None:
            raise ValueError(
                "unavailable expected-change measurement cannot claim a match result"
            )
        if self.outcome == "confirmed" and not any(
            (
                self.meaningful_change,
                self.task_progress,
                self.objective_completed,
                self.information_gain_units > 0,
                coverage_gain > 0,
                bool(self.account_metric_deltas),
            )
        ):
            raise ValueError("a confirmed action must contain an observable benefit")
        if self.outcome == "no_effect" and any(
            (
                self.meaningful_change,
                self.task_progress,
                self.objective_completed,
                self.information_gain_units > 0,
                coverage_gain > 0,
                bool(self.account_metric_deltas),
            )
        ):
            raise ValueError("a no-effect action cannot claim progress or information gain")
        if self.invalid_target_execution and self.execution_disposition != "executed":
            raise ValueError("invalid-target execution only applies after an adapter call")
        signal_names = [item.signal for item in self.soft_signals]
        if len(signal_names) != len(set(signal_names)):
            raise ValueError("soft signals must be unique within one action sample")
        parent_step_ids = {
            step_id
            for reference in self.evidence_refs
            for step_id in reference.evidence_step_ids
        }
        nested_step_ids = {
            step_id
            for item in [*self.account_metric_deltas, *self.soft_signals]
            for step_id in item.evidence_step_ids
        }
        if not nested_step_ids.issubset(parent_step_ids):
            raise ValueError("metric and soft-signal evidence must be included by the sample")
        if self.evidence_step_id and self.evidence_step_id not in parent_step_ids:
            raise ValueError("terminal evidence step must be included by the sample")
        return self


class _IterationTierAssessmentV1(_StrictContract):
    tier: Literal[1, 2, 3, 4]
    name: str = Field(min_length=1)
    status: Literal["passed", "failed", "insufficient_data", "not_evaluated"]
    metrics: dict[str, float | int | None] = Field(default_factory=dict)
    thresholds: dict[str, float | int] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    directive: str = Field(min_length=1)


class PlayerIterationAssessmentV1(_EnvironmentBoundContract):
    """Deterministic four-tier review; soft scores may request review but cannot pass tiers."""

    schema_id: Literal["game-observatory.ai-player.player-iteration-assessment.v1"] = Field(
        default="game-observatory.ai-player.player-iteration-assessment.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    window_kind: Literal["actions_10", "verified_task", "daily_close", "incident"]
    sample_ids: list[str] = Field(min_length=1)
    soft_signal_review_ids: list[str] = Field(default_factory=list)
    policy_version: Literal["player-iteration.v1"] = "player-iteration.v1"
    tiers: list[_IterationTierAssessmentV1] = Field(min_length=4, max_length=4)
    highest_contiguous_passed_tier: int = Field(ge=0, le=4)
    overall_status: Literal["passed", "failed", "insufficient_data"]
    directive: Literal[
        "continue",
        "shadow_only",
        "pause_physical_and_repair_perception_executor",
        "revise_planner_and_task_policy",
        "refresh_guides_and_reprioritize_objectives",
        "expand_discovery_frontier",
    ]
    soft_signal_averages: dict[PlayerSoftSignalName, float] = Field(default_factory=dict)
    soft_review_reasons: list[str] = Field(default_factory=list)
    window_started_at: str = Field(min_length=1)
    window_ended_at: str = Field(min_length=1)
    created_at: str = Field(default_factory=utc_now)

    @field_validator("sample_ids", "soft_signal_review_ids")
    @classmethod
    def validate_sample_ids(cls, value: list[str], info: Any) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError(f"assessment {info.field_name} must contain non-empty values")
        return _require_unique(value, str(info.field_name))

    @model_validator(mode="after")
    def preserve_iteration_hierarchy(self) -> "PlayerIterationAssessmentV1":
        if [item.tier for item in self.tiers] != [1, 2, 3, 4]:
            raise ValueError("iteration tiers must be ordered exactly from 1 through 4")
        contiguous = 0
        hierarchy_stopped = False
        for item in self.tiers:
            if hierarchy_stopped:
                if item.status != "not_evaluated":
                    raise ValueError(
                        "a higher iteration tier cannot be evaluated before every lower tier passes"
                    )
                continue
            if item.status == "passed":
                contiguous += 1
            else:
                hierarchy_stopped = True
        if self.highest_contiguous_passed_tier != contiguous:
            raise ValueError("highest passed tier must match the contiguous tier results")
        first_failure = next((item.tier for item in self.tiers if item.status == "failed"), None)
        expected_directive = {
            1: "pause_physical_and_repair_perception_executor",
            2: "revise_planner_and_task_policy",
            3: "refresh_guides_and_reprioritize_objectives",
            4: "expand_discovery_frontier",
        }.get(first_failure)
        if expected_directive and self.directive != expected_directive:
            raise ValueError("assessment directive must follow the first failed tier")
        if first_failure is None and self.tiers[0].status == "insufficient_data":
            if self.directive != "shadow_only" or self.overall_status != "insufficient_data":
                raise ValueError("insufficient basic evidence must stay in shadow-only mode")
        elif first_failure is None and all(
            item.status in {"passed", "not_evaluated"} for item in self.tiers
        ):
            if self.directive != "continue" or self.overall_status != "passed":
                raise ValueError("a non-failing assessment must continue")
        elif first_failure is not None and self.overall_status != "failed":
            raise ValueError("a failed tier must fail the assessment")
        return self
