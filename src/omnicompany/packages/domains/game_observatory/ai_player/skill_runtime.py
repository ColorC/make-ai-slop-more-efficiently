"""Execute one verified procedural-skill DAG without bypassing evidence or safety gates."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .contracts import (
    EvidenceReferenceV1,
    SkillRunV1,
    SkillStepV1,
    SkillVersionV1,
)
from .skills import SkillLifecycle, SkillLifecycleError, skill_is_applicable
from .store import AIPlayerStore


_SAFETY_ORDER = {
    "read_only": 0,
    "reversible": 1,
    "progression": 2,
    "social": 3,
    "economic": 4,
    "restricted": 5,
}


class SkillStepExecutionResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1)
    outcome: Literal["success", "failed", "interrupted", "safety_blocked"]
    claimed_success: bool
    objective_success: bool
    evidence_refs: list[EvidenceReferenceV1] = Field(min_length=1)
    branch_value: bool | None = None
    retry_allowed_after_observation: bool = False
    action_count: int = Field(default=0, ge=0)
    model_input_tokens: int = Field(default=0, ge=0)
    decision_latency_ms: float = Field(default=0, ge=0)
    result_summary: str = Field(min_length=1)


class SkillExecutionRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment_id: str = Field(min_length=1)
    skill_id: str = Field(min_length=1)
    skill_version_id: str | None = Field(default=None, min_length=1)
    validation_mode: bool = False
    run_id: str = Field(min_length=1)
    attempt_index: int = Field(ge=1)
    independent_reset_id: str = Field(min_length=1)
    visual_variant_id: str = Field(min_length=1)
    current_state_id: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    initial_evidence_refs: list[EvidenceReferenceV1] = Field(min_length=1)
    max_safety_level: Literal[
        "read_only",
        "reversible",
        "progression",
        "social",
        "economic",
        "restricted",
    ] = "economic"
    baseline_model_input_tokens: int = Field(ge=0)
    baseline_decision_latency_ms: float = Field(ge=0)

    @field_validator("initial_evidence_refs")
    @classmethod
    def validate_initial_evidence(
        cls,
        value: list[EvidenceReferenceV1],
        info: Any,
    ) -> list[EvidenceReferenceV1]:
        environment_id = info.data.get("environment_id")
        if any(reference.environment_id != environment_id for reference in value):
            raise ValueError("initial evidence must belong to the execution environment")
        return value


class SkillExecutionResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill: SkillVersionV1
    run: SkillRunV1
    step_results: list[SkillStepExecutionResultV1]


class SkillStepAdapter(Protocol):
    """Device or simulator bridge; every returned result must be evidence-backed."""

    def execute_step(
        self,
        skill: SkillVersionV1,
        step: SkillStepV1,
        parameters: Mapping[str, Any],
    ) -> SkillStepExecutionResultV1: ...

    def execute_recovery(
        self,
        recovery_skill: SkillVersionV1,
        failed_skill: SkillVersionV1,
        failed_step: SkillStepV1,
        parameters: Mapping[str, Any],
    ) -> SkillStepExecutionResultV1: ...


def _evidence_union(
    references: list[EvidenceReferenceV1],
) -> list[EvidenceReferenceV1]:
    unique: dict[str, EvidenceReferenceV1] = {}
    for reference in references:
        unique.setdefault(reference.model_dump_json(by_alias=True), reference)
    return list(unique.values())


class SkillRuntime:
    """Execute preferred skills in production and explicit candidates in validation mode."""

    def __init__(self, store: AIPlayerStore, adapter: SkillStepAdapter) -> None:
        self.store = store
        self.adapter = adapter
        self.lifecycle = SkillLifecycle(store)

    def execute(self, request: SkillExecutionRequestV1) -> SkillExecutionResultV1:
        skill = self._resolve_skill(request)
        environment = self.store.get_environment(request.environment_id)
        if environment is None:
            raise SkillLifecycleError(f"unknown environment: {request.environment_id}")
        if not skill_is_applicable(
            skill,
            environment,
            current_state_id=request.current_state_id,
            visual_variant_id=request.visual_variant_id,
        ):
            run = self._record_terminal_run(
                request,
                skill,
                outcome="precondition_unmet",
                precondition_satisfied=False,
                objective_success=False,
                validation_passed=True,
                evidence_refs=request.initial_evidence_refs,
            )
            return SkillExecutionResultV1(skill=skill, run=run, step_results=[])
        if _SAFETY_ORDER[skill.safety_level] > _SAFETY_ORDER[request.max_safety_level]:
            run = self._record_terminal_run(
                request,
                skill,
                outcome="safety_blocked",
                precondition_satisfied=True,
                objective_success=False,
                validation_passed=True,
                evidence_refs=request.initial_evidence_refs,
            )
            return SkillExecutionResultV1(skill=skill, run=run, step_results=[])

        results: list[SkillStepExecutionResultV1] = []
        completed: set[str] = set()
        skipped: set[str] = set()
        step_by_id = {step.id: step for step in skill.steps}
        remaining = list(skill.steps)
        failed_step: SkillStepV1 | None = None
        terminal_outcome: Literal["success", "failed", "interrupted", "false_success"] = (
            "success"
        )
        while remaining:
            ready = [
                step
                for step in remaining
                if step.id in skipped or set(step.depends_on_step_ids).issubset(completed | skipped)
            ]
            if not ready:
                raise SkillLifecycleError("skill runtime could not make progress through the DAG")
            for step in ready:
                remaining.remove(step)
                if step.id in skipped:
                    completed.add(step.id)
                    continue
                result = self._execute_step_with_retry(skill, step, request.parameters)
                self._validate_step_result(request, step, result)
                results.append(result)
                if step.kind == "branch":
                    if result.branch_value is None:
                        raise SkillLifecycleError("a branch step must return its selected branch")
                    skipped.update(
                        step.when_false_step_ids
                        if result.branch_value
                        else step.when_true_step_ids
                    )
                completed.add(step.id)
                if result.outcome != "success" or not result.objective_success:
                    failed_step = step
                    terminal_outcome = (
                        "false_success"
                        if result.claimed_success and not result.objective_success
                        else "interrupted"
                        if result.outcome == "interrupted"
                        else "failed"
                    )
                    remaining.clear()
                    break

        recovery_attempted = False
        recovery_succeeded = False
        if failed_step is not None and skill.recovery_skill_version_ids:
            recovery_attempted = True
            recovery = self.store.get_skill_version_by_id(
                request.environment_id,
                skill.recovery_skill_version_ids[0],
            )
            if recovery is None:
                raise SkillLifecycleError("the selected recovery skill is missing")
            recovery_result = self.adapter.execute_recovery(
                recovery,
                skill,
                failed_step,
                request.parameters,
            )
            self._validate_step_result(request, failed_step, recovery_result)
            results.append(recovery_result)
            recovery_succeeded = (
                recovery_result.outcome == "success" and recovery_result.objective_success
            )

        evidence_refs = _evidence_union(
            [
                *request.initial_evidence_refs,
                *(reference for result in results for reference in result.evidence_refs),
            ]
        )
        false_success = terminal_outcome == "false_success"
        objective_success = terminal_outcome == "success"
        validation_passed = objective_success or (
            terminal_outcome == "interrupted" and recovery_succeeded
        )
        run = self._record_terminal_run(
            request,
            skill,
            outcome=terminal_outcome,
            precondition_satisfied=True,
            objective_success=objective_success,
            validation_passed=validation_passed,
            false_success=false_success,
            recovery_attempted=recovery_attempted,
            recovery_succeeded=recovery_succeeded,
            evidence_refs=evidence_refs,
            action_count=sum(result.action_count for result in results),
            model_input_tokens=sum(result.model_input_tokens for result in results),
            decision_latency_ms=sum(result.decision_latency_ms for result in results),
        )
        return SkillExecutionResultV1(skill=skill, run=run, step_results=results)

    def _resolve_skill(self, request: SkillExecutionRequestV1) -> SkillVersionV1:
        if request.skill_version_id is not None:
            skill = self.store.get_skill_version_by_id(
                request.environment_id,
                request.skill_version_id,
            )
            if skill is None or skill.skill_id != request.skill_id:
                raise SkillLifecycleError("the requested skill version does not exist")
            if skill.status != "preferred" and not request.validation_mode:
                raise SkillLifecycleError("production execution requires a preferred skill")
            if skill.status in {"degraded", "invalidated"}:
                raise SkillLifecycleError("a degraded or invalidated skill cannot execute")
            return skill
        matches = self.lifecycle.select_preferred(
            request.environment_id,
            current_state_id=request.current_state_id,
            visual_variant_id=request.visual_variant_id,
            skill_id=request.skill_id,
        )
        if len(matches) != 1:
            raise SkillLifecycleError("exactly one applicable preferred skill is required")
        return matches[0]

    def _execute_step_with_retry(
        self,
        skill: SkillVersionV1,
        step: SkillStepV1,
        parameters: Mapping[str, Any],
    ) -> SkillStepExecutionResultV1:
        result = self.adapter.execute_step(skill, step, parameters)
        attempts = 1
        while (
            result.outcome == "failed"
            and attempts < step.max_attempts
            and step.idempotency != "never_retry"
            and result.retry_allowed_after_observation
        ):
            result = self.adapter.execute_step(skill, step, parameters)
            attempts += 1
        return result

    @staticmethod
    def _validate_step_result(
        request: SkillExecutionRequestV1,
        step: SkillStepV1,
        result: SkillStepExecutionResultV1,
    ) -> None:
        if result.step_id != step.id:
            raise SkillLifecycleError("the adapter returned a result for a different step")
        if any(
            reference.environment_id != request.environment_id
            for reference in result.evidence_refs
        ):
            raise SkillLifecycleError("skill step evidence crossed the execution environment")

    def _record_terminal_run(
        self,
        request: SkillExecutionRequestV1,
        skill: SkillVersionV1,
        *,
        outcome: Literal[
            "success",
            "failed",
            "precondition_unmet",
            "interrupted",
            "safety_blocked",
            "false_success",
        ],
        precondition_satisfied: bool,
        objective_success: bool,
        validation_passed: bool,
        evidence_refs: list[EvidenceReferenceV1],
        false_success: bool = False,
        recovery_attempted: bool = False,
        recovery_succeeded: bool = False,
        action_count: int = 0,
        model_input_tokens: int = 0,
        decision_latency_ms: float = 0,
    ) -> SkillRunV1:
        run = SkillRunV1(
            id=request.run_id,
            environment_id=request.environment_id,
            evidence_refs=evidence_refs,
            skill_version_id=skill.id,
            attempt_index=request.attempt_index,
            independent_reset_id=request.independent_reset_id,
            visual_variant_id=request.visual_variant_id,
            outcome=outcome,
            precondition_satisfied=precondition_satisfied,
            objective_success=objective_success,
            validation_passed=validation_passed,
            false_success=false_success,
            safety_violation_count=0,
            recovery_attempted=recovery_attempted,
            recovery_succeeded=recovery_succeeded,
            action_count=action_count,
            model_input_tokens=model_input_tokens,
            baseline_model_input_tokens=request.baseline_model_input_tokens,
            decision_latency_ms=decision_latency_ms,
            baseline_decision_latency_ms=request.baseline_decision_latency_ms,
        )
        return self.lifecycle.record_run(run)