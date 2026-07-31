"""Production bridge from verified normalized-action skills to guarded game actions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..models import SourcePixelRect
from .contracts import EvidenceReferenceV1, SkillStepV1, SkillVersionV1
from .known_route_program import _requires_dynamic_world_object_locator
from .skill_runtime import SkillStepExecutionResultV1
from .skills import SkillLifecycleError


class GuardedSkillActionReceiptV1(BaseModel):
    """Canonical terminal receipt returned by the existing guarded-action boundary."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    evidence_refs: list[EvidenceReferenceV1] = Field(min_length=1)
    observed_state_id: str | None = Field(default=None, min_length=1)
    action_count: int = Field(default=1, ge=1)
    model_input_tokens: int = Field(default=0, ge=0)
    decision_latency_ms: float = Field(default=0, ge=0)
    summary: str = Field(min_length=1)


class GuardedSkillObservationReceiptV1(BaseModel):
    """Evidence-backed semantic-state assignment for an assert step."""

    model_config = ConfigDict(extra="forbid")

    evidence_refs: list[EvidenceReferenceV1] = Field(min_length=1)
    observed_state_id: str | None = Field(default=None, min_length=1)
    verified_state_guard: bool = False
    model_input_tokens: int = Field(default=0, ge=0)
    decision_latency_ms: float = Field(default=0, ge=0)
    summary: str = Field(min_length=1)


GuardedActionCallback = Callable[
    [SkillVersionV1, SkillStepV1, SourcePixelRect | None, Mapping[str, Any]],
    GuardedSkillActionReceiptV1,
]
GuardedObservationCallback = Callable[
    [SkillVersionV1, SkillStepV1, Mapping[str, Any]],
    GuardedSkillObservationReceiptV1,
]


class GuardedSkillStepAdapter:
    """Execute one production-safe normalized-action skill through injected guards.

    The callback boundary keeps Click and device/session plumbing outside the domain
    package.  This adapter only accepts the executable subset whose terminal objective
    can be checked against a canonical semantic-state assignment.
    """

    requires_state_assignment_evidence = True
    supports_verified_state_guard_evidence = True

    def __init__(
        self,
        *,
        execute_guarded_action: GuardedActionCallback,
        observe_state: GuardedObservationCallback,
    ) -> None:
        self._execute_guarded_action = execute_guarded_action
        self._observe_state = observe_state

    def validate_skill(self, skill: SkillVersionV1) -> None:
        """Fail before device access when this production adapter cannot prove success."""

        if skill.executor_kind != "normalized_actions":
            raise SkillLifecycleError(
                "guarded production replay only accepts normalized-actions skills"
            )
        if _requires_dynamic_world_object_locator(skill):
            raise SkillLifecycleError(
                "dynamic_locator_required: movable world-map targets cannot replay "
                "a legacy source-pixel locator"
            )
        supported = {"action", "assert", "subskill", "recover"}
        unsupported = [step.id for step in skill.steps if step.kind not in supported]
        if unsupported:
            raise SkillLifecycleError(
                "guarded production replay has unsupported step kinds: "
                + ", ".join(unsupported)
            )
        unstructured = [
            step.id
            for step in skill.steps
            if step.kind == "assert" and step.expected_state_id is None
        ]
        if not any(step.kind == "assert" for step in skill.steps) or unstructured:
            suffix = ", ".join(unstructured) if unstructured else "missing assert"
            raise SkillLifecycleError(
                "production replay requires structured expected_state_id checks: " + suffix
            )
        locators = {item.id: item for item in skill.locators}
        invalid_locators = []
        for step in skill.steps:
            if step.kind != "action" or step.locator_id is None:
                continue
            locator = locators.get(step.locator_id)
            fixed_pixel = bool(
                locator is not None
                and locator.strategy == "source_pixel"
                and locator.reference_bounds is not None
                and locator.mobility != "dynamic_world_object"
            )
            dynamic_template = bool(
                locator is not None
                and locator.strategy == "template"
                and locator.mobility == "dynamic_world_object"
                and locator.reference_bounds is not None
                and locator.reference_artifact_id is not None
                and locator.search_region is not None
                and locator.match_threshold is not None
                and step.action is not None
                and step.action.type == "tap"
            )
            if not fixed_pixel and not dynamic_template:
                invalid_locators.append(step.id)
        if invalid_locators:
            raise SkillLifecycleError(
                "guarded production replay requires a fixed source-pixel locator "
                "or a complete dynamic template locator: "
                + ", ".join(invalid_locators)
            )

    def execute_step(
        self,
        skill: SkillVersionV1,
        step: SkillStepV1,
        parameters: Mapping[str, Any],
    ) -> SkillStepExecutionResultV1:
        if step.kind == "action":
            if step.action is None:
                raise SkillLifecycleError(f"action step lacks an action: {step.id}")
            locator = next(
                (item for item in skill.locators if item.id == step.locator_id),
                None,
            )
            bounds = locator.reference_bounds if locator is not None else None
            receipt = self._execute_guarded_action(skill, step, bounds, parameters)
            return SkillStepExecutionResultV1(
                step_id=step.id,
                outcome="success" if receipt.ok else "failed",
                claimed_success=receipt.ok,
                objective_success=receipt.ok,
                evidence_refs=receipt.evidence_refs,
                observed_state_id=receipt.observed_state_id,
                action_count=receipt.action_count,
                model_input_tokens=receipt.model_input_tokens,
                decision_latency_ms=receipt.decision_latency_ms,
                result_summary=receipt.summary,
            )
        if step.kind == "assert":
            if step.expected_state_id is None:
                raise SkillLifecycleError(
                    f"assert step lacks expected_state_id: {step.id}"
                )
            receipt = self._observe_state(skill, step, parameters)
            objective_success = receipt.observed_state_id == step.expected_state_id
            return SkillStepExecutionResultV1(
                step_id=step.id,
                outcome="success",
                claimed_success=True,
                objective_success=objective_success,
                evidence_refs=receipt.evidence_refs,
                observed_state_id=receipt.observed_state_id,
                verified_state_guard=receipt.verified_state_guard,
                model_input_tokens=receipt.model_input_tokens,
                decision_latency_ms=receipt.decision_latency_ms,
                result_summary=(
                    receipt.summary
                    if objective_success
                    else (
                        f"{receipt.summary}；预期状态 {step.expected_state_id}，"
                        f"实际状态 {receipt.observed_state_id or '未识别'}。"
                    )
                ),
            )
        raise SkillLifecycleError(
            f"step {step.id} must be executed by SkillRuntime dependency handling"
        )

    def execute_recovery(
        self,
        recovery_skill: SkillVersionV1,
        failed_skill: SkillVersionV1,
        failed_step: SkillStepV1,
        parameters: Mapping[str, Any],
    ) -> SkillStepExecutionResultV1:
        del recovery_skill, failed_skill, parameters
        raise SkillLifecycleError(
            f"recovery step {failed_step.id} must run as a verified dependency skill"
        )


__all__ = [
    "GuardedSkillActionReceiptV1",
    "GuardedSkillObservationReceiptV1",
    "GuardedSkillStepAdapter",
]
