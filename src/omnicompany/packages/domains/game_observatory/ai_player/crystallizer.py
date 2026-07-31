"""Create evidence-backed skill candidates from closed successful transition routes."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..models import NormalizedAction, utc_now
from .contracts import (
    EvidenceReferenceV1,
    SkillLocatorV1,
    SkillStepV1,
    SkillVersionV1,
    TransitionEdgeV1,
)
from .skills import (
    SkillLifecycleError,
    applicability_scope_from_environment,
    build_skill_version,
)
from .store import AIPlayerStore


class SkillCrystallizationRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment_id: str = Field(min_length=1)
    skill_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    level: Literal["L2", "L3", "L4"]
    transition_ids: list[str] = Field(min_length=1)
    applicability: str = Field(min_length=1)
    safety_level: Literal[
        "read_only",
        "reversible",
        "progression",
        "social",
        "economic",
        "restricted",
    ]
    parameters_schema: dict[str, Any] = Field(default_factory=dict)
    success_checks: list[str] = Field(min_length=1)
    failure_checks: list[str] = Field(min_length=1)
    recovery_skill_version_ids: list[str] = Field(default_factory=list)
    visual_variant_ids: list[str] = Field(default_factory=list)
    executor_kind: Literal[
        "normalized_actions",
        "maa",
        "airtest",
        "mineflayer",
        "specialized_adapter",
    ] = "normalized_actions"
    executor_ref: str | None = Field(default=None, min_length=1)
    perception_tier: Literal["P0", "P1", "P2", "P3", "P4", "P5"] = "P2"

    @field_validator(
        "transition_ids",
        "success_checks",
        "failure_checks",
        "recovery_skill_version_ids",
        "visual_variant_ids",
    )
    @classmethod
    def validate_lists(cls, value: list[str], info: Any) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError(f"{info.field_name} must contain non-empty values")
        if len(value) != len(set(value)):
            raise ValueError(f"{info.field_name} must not contain duplicates")
        return value


def _evidence_union(edges: list[TransitionEdgeV1]) -> list[EvidenceReferenceV1]:
    unique: dict[str, EvidenceReferenceV1] = {}
    for edge in edges:
        for reference in edge.evidence_refs:
            unique.setdefault(reference.model_dump_json(by_alias=True), reference)
    return list(unique.values())


def _action_phrase(action: NormalizedAction) -> str:
    if action.type == "tap":
        return f"点击源图坐标 ({action.x}, {action.y})"
    if action.type == "swipe":
        return f"从 ({action.x}, {action.y}) 滑动到 ({action.x2}, {action.y2})"
    if action.type == "back":
        return "触发系统返回"
    if action.type == "wait":
        return f"等待 {action.seconds:g} 秒"
    if action.type == "text":
        return "输入已参数化文本"
    if action.type == "key":
        return f"发送键码 {action.keycode}"
    return f"执行归一化动作 {action.type}"


def _side_effect(safety_level: str) -> str:
    return {
        "read_only": "none",
        "reversible": "reversible",
        "progression": "progression",
        "social": "social",
        "economic": "economic",
        "restricted": "restricted",
    }[safety_level]


class SkillCrystallizer:
    """Propose immutable candidates; this component has no promotion authority."""

    def __init__(self, store: AIPlayerStore) -> None:
        self.store = store

    def crystallize(self, request: SkillCrystallizationRequestV1) -> SkillVersionV1:
        environment = self.store.get_environment(request.environment_id)
        if environment is None:
            raise SkillLifecycleError(f"unknown environment: {request.environment_id}")
        edges = [
            self.store.get_transition_edge(request.environment_id, transition_id)
            for transition_id in request.transition_ids
        ]
        if any(edge is None for edge in edges):
            raise SkillLifecycleError("a crystallization route contains a missing transition")
        typed_edges = [edge for edge in edges if edge is not None]
        if any(
            edge.outcome not in {"verified_transition", "verified_state_change"}
            for edge in typed_edges
        ):
            raise SkillLifecycleError("only closed successful transitions may crystallize")
        for previous, current in zip(typed_edges, typed_edges[1:], strict=False):
            if previous.to_state_id != current.from_state_id:
                raise SkillLifecycleError("a crystallization route must be continuous")

        latest = self.store.get_skill_version(request.environment_id, request.skill_id)
        version = 1 if latest is None else latest.version + 1
        locators: list[SkillLocatorV1] = []
        steps: list[SkillStepV1] = []
        procedure_steps: list[str] = []
        previous_assertion_id: str | None = None
        side_effect = _side_effect(request.safety_level)
        for index, edge in enumerate(typed_edges):
            locator_id = None
            if edge.target_bounds is not None:
                locator_id = f"locator.{index + 1}"
                locators.append(
                    SkillLocatorV1(
                        id=locator_id,
                        step_index=len(steps),
                        strategy="source_pixel",
                        selector=json.dumps(
                            edge.target_bounds.model_dump(),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        reference_bounds=edge.target_bounds,
                    )
                )
            action_id = f"step.{index + 1}.action"
            assertion_id = f"step.{index + 1}.assert"
            idempotency = (
                "read_only"
                if edge.action.type == "wait"
                else "never_retry"
                if side_effect in {"economic", "restricted"}
                else "verify_before_retry"
            )
            steps.append(
                SkillStepV1(
                    id=action_id,
                    kind="action",
                    depends_on_step_ids=(
                        [previous_assertion_id] if previous_assertion_id is not None else []
                    ),
                    action=edge.action,
                    locator_id=locator_id,
                    max_attempts=1,
                    idempotency=idempotency,
                    side_effect=side_effect,
                )
            )
            objective = (
                f"确认进入状态 {edge.to_state_id}；观测变化：{edge.observed_change}"
            )
            steps.append(
                SkillStepV1(
                    id=assertion_id,
                    kind="assert",
                    depends_on_step_ids=[action_id],
                    assertion=objective,
                    idempotency="read_only",
                    side_effect="none",
                )
            )
            procedure_steps.append(f"{_action_phrase(edge.action)}，随后{objective}")
            previous_assertion_id = assertion_id

        layer = {"L2": "atomic", "L3": "flow", "L4": "strategy"}[request.level]
        scope = {"L2": "interaction", "L3": "surface", "L4": "gameplay"}[
            request.level
        ]
        candidate = build_skill_version(
            id=f"{request.skill_id}.version.{version}",
            skill_id=request.skill_id,
            version=version,
            environment_id=request.environment_id,
            evidence_refs=_evidence_union(typed_edges),
            level=request.level,
            skill_layer=layer,
            scope=scope,
            execution_mode=(
                "interpreted"
                if request.executor_kind == "normalized_actions"
                else "compiled"
            ),
            perception_tier=request.perception_tier,
            title=request.title,
            applicability=request.applicability,
            applicability_scope=applicability_scope_from_environment(
                environment,
                required_state_ids=[typed_edges[0].from_state_id],
                visual_variant_ids=request.visual_variant_ids,
            ),
            safety_level=request.safety_level,
            parameters_schema=request.parameters_schema,
            preconditions=[
                f"当前语义状态为 {typed_edges[0].from_state_id}",
                "环境身份与技能适用域完全匹配",
                "每个动作前均可重新观察且不存在未结算动作",
            ],
            executor_kind=request.executor_kind,
            executor_ref=request.executor_ref
            or f"skill://{request.skill_id}/version/{version}",
            procedure_steps=procedure_steps,
            locators=locators,
            steps=steps,
            success_checks=request.success_checks,
            failure_checks=request.failure_checks,
            recovery_skill_version_ids=request.recovery_skill_version_ids,
            source_transition_ids=request.transition_ids,
            status="candidate",
            source_skill_version_id=latest.id if latest is not None else None,
            created_at=utc_now(),
        )
        if latest is not None and latest.content_sha256 == candidate.content_sha256:
            return latest
        return self.store.append_skill_version(candidate)