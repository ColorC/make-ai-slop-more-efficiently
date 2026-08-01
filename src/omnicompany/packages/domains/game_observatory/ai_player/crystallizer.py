"""Create evidence-backed skill candidates from closed successful transition routes."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..models import EvidenceStep, NormalizedAction, SourcePixelRect, utc_now
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
from .skill_candidate_evidence import canonical_direct_live_step_confirms_edge
from .store import AIPlayerStore


class SkillCrystallizationRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_id: Literal[
        "game-observatory.ai-player.skill-crystallization-request.v1"
    ] = Field(
        default="game-observatory.ai-player.skill-crystallization-request.v1",
        alias="schema",
    )
    environment_id: str = Field(min_length=1)
    creator_id: str = Field(min_length=1)
    skill_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    level: Literal["L2", "L3", "L4"]
    transition_ids: list[str] = Field(min_length=1)
    supporting_transition_ids: list[str] = Field(default_factory=list)
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
    provisional_trial: bool = False

    @field_validator(
        "transition_ids",
        "supporting_transition_ids",
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

    @model_validator(mode="after")
    def validate_transition_roles(self) -> "SkillCrystallizationRequestV1":
        duplicates = set(self.transition_ids).intersection(self.supporting_transition_ids)
        if duplicates:
            raise ValueError(
                "a transition cannot be both a route step and supporting evidence: "
                + ", ".join(sorted(duplicates))
            )
        return self


def _evidence_union(edges: list[TransitionEdgeV1]) -> list[EvidenceReferenceV1]:
    unique: dict[str, EvidenceReferenceV1] = {}
    for edge in edges:
        for reference in edge.evidence_refs:
            unique.setdefault(reference.model_dump_json(by_alias=True), reference)
    return list(unique.values())


_SUCCESSFUL_TRANSITION_OUTCOMES = {
    "verified_transition",
    "verified_state_change",
    "verified_progress",
}


def _same_executable_transition(
    supporting: TransitionEdgeV1,
    route: TransitionEdgeV1,
) -> bool:
    """Match execution semantics while allowing evidence-specific descriptions."""

    return (
        supporting.from_state_id == route.from_state_id
        and supporting.to_state_id == route.to_state_id
        and supporting.action == route.action
        and supporting.target_bounds == route.target_bounds
    )


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


_FIXED_CHROME_PATTERN = re.compile(
    r"(?:返回|关闭|退出|菜单|导航|页签|标签|顶栏|底栏|侧栏|筛选)"
)
_DYNAMIC_WORLD_CONTEXT_PATTERN = re.compile(
    r"(?:城外|场景内|地图场景|世界地图).{0,24}"
    r"(?:地块|土地|资源地|空地|农田|林场|铁矿|石料|粮食|部队|城池|目标点)"
)
_DYNAMIC_WORLD_OBJECT_PATTERN = re.compile(
    r"(?:地块|土地|资源地|空地|农田|林场|铁矿|石料|粮食|部队|城池|目标点)"
)


def _matching_evidence_steps(
    store: AIPlayerStore,
    edges: list[TransitionEdgeV1],
) -> list[EvidenceStep]:
    """Resolve only evidence steps that actually demonstrated this locator action."""

    resolved: dict[str, EvidenceStep] = {}
    for edge in edges:
        for reference in edge.evidence_refs:
            for step_id in reference.evidence_step_ids:
                step = store.observatory_store.get_evidence_step(step_id)
                if (
                    step is not None
                    and step.action == edge.action
                    and step.target_bounds == edge.target_bounds
                ):
                    resolved.setdefault(step.id, step)
    return [resolved[step_id] for step_id in sorted(resolved)]


def _locator_mobility(
    edge: TransitionEdgeV1,
    evidence_steps: list[EvidenceStep],
) -> Literal["fixed_chrome", "fixed_surface", "dynamic_world_object"]:
    target_context = " ".join(
        [
            *(step.target_name or "" for step in evidence_steps),
            edge.expected_change,
            edge.observed_change,
        ]
    )
    if _FIXED_CHROME_PATTERN.search(target_context):
        return "fixed_chrome"
    has_dynamic_scene = any(
        getattr(step.stability, "dynamic_scene_profile", None) is not None
        for step in evidence_steps
    )
    if _DYNAMIC_WORLD_CONTEXT_PATTERN.search(target_context) or (
        has_dynamic_scene and _DYNAMIC_WORLD_OBJECT_PATTERN.search(target_context)
    ):
        return "dynamic_world_object"
    # An animated scene makes unclassified source pixels unsafe.  A clearly named
    # chrome control was handled above; unknown targets must re-localize visually.
    if has_dynamic_scene:
        return "dynamic_world_object"
    return "fixed_surface"


def _locator_selector(edge: TransitionEdgeV1, evidence_steps: list[EvidenceStep]) -> str:
    target_names = list(
        dict.fromkeys(
            name.strip()
            for step in evidence_steps
            if (name := step.target_name) is not None and name.strip()
        )
    )
    return " / ".join(target_names) if target_names else edge.expected_change


def _template_reference_step(evidence_steps: list[EvidenceStep]) -> EvidenceStep | None:
    return next((step for step in evidence_steps if step.before_frame_id is not None), None)


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
        self._require_crystallizable_edges(request, typed_edges)

        supporting_edges = [
            self.store.get_transition_edge(request.environment_id, transition_id)
            for transition_id in request.supporting_transition_ids
        ]
        if any(edge is None for edge in supporting_edges):
            raise SkillLifecycleError(
                "skill crystallization contains a missing supporting transition"
            )
        typed_supporting_edges = [edge for edge in supporting_edges if edge is not None]
        self._require_crystallizable_edges(request, typed_supporting_edges)
        if any(
            not any(
                _same_executable_transition(supporting, route)
                for route in typed_edges
            )
            for supporting in typed_supporting_edges
        ):
            raise SkillLifecycleError(
                "a supporting transition must match a route transition's endpoints, "
                "normalized action, and target bounds"
            )

        evidence_edges = [*typed_edges, *typed_supporting_edges]
        endpoint_ids = {
            state_id
            for edge in evidence_edges
            for state_id in (edge.from_state_id, edge.to_state_id)
            if state_id is not None
        }
        endpoints = {
            state_id: self.store.get_semantic_state(request.environment_id, state_id)
            for state_id in endpoint_ids
        }
        allowed_endpoint_statuses = (
            {"candidate", "accepted"} if request.provisional_trial else {"accepted"}
        )
        if any(
            state is None or state.status not in allowed_endpoint_statuses
            for state in endpoints.values()
        ):
            raise SkillLifecycleError(
                "provisional skill crystallization requires candidate or accepted endpoints"
                if request.provisional_trial
                else "skill crystallization requires accepted transition endpoints"
            )
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
                equivalent_edges = [
                    edge,
                    *(
                        supporting
                        for supporting in typed_supporting_edges
                        if _same_executable_transition(supporting, edge)
                    ),
                ]
                evidence_steps = _matching_evidence_steps(self.store, equivalent_edges)
                mobility = _locator_mobility(edge, evidence_steps)
                reference_step = _template_reference_step(evidence_steps)
                if mobility == "dynamic_world_object" and reference_step is None:
                    raise SkillLifecycleError(
                        "dynamic world object crystallization requires an evidence step "
                        "with a before-frame reference artifact"
                    )
                locators.append(
                    SkillLocatorV1(
                        id=locator_id,
                        step_index=len(steps),
                        strategy=(
                            "template"
                            if mobility == "dynamic_world_object"
                            else "source_pixel"
                        ),
                        selector=_locator_selector(edge, evidence_steps),
                        reference_bounds=edge.target_bounds,
                        mobility=mobility,
                        reference_artifact_id=(
                            reference_step.before_frame_id
                            if reference_step is not None
                            else None
                        ),
                        search_region=(
                            SourcePixelRect(
                                x=0,
                                y=0,
                                width=reference_step.viewport_width,
                                height=reference_step.viewport_height,
                            )
                            if mobility == "dynamic_world_object"
                            and reference_step is not None
                            else None
                        ),
                        match_threshold=(
                            0.82 if mobility == "dynamic_world_object" else None
                        ),
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
                    expected_state_id=edge.to_state_id,
                    idempotency="read_only",
                    side_effect="none",
                )
            )
            procedure_steps.append(
                f"步骤 {index + 1:02d}：{_action_phrase(edge.action)}，随后{objective}"
            )
            previous_assertion_id = assertion_id

        layer = {"L2": "atomic", "L3": "flow", "L4": "strategy"}[request.level]
        scope = {"L2": "interaction", "L3": "surface", "L4": "gameplay"}[
            request.level
        ]
        candidate = build_skill_version(
            id=f"{request.skill_id}.version.{version}",
            skill_id=request.skill_id,
            creator_id=request.creator_id,
            version=version,
            environment_id=request.environment_id,
            evidence_refs=_evidence_union(evidence_edges),
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
            source_transition_ids=[
                *request.transition_ids,
                *request.supporting_transition_ids,
            ],
            status="candidate",
            source_skill_version_id=latest.id if latest is not None else None,
            created_at=utc_now(),
        )
        if latest is not None and latest.content_sha256 == candidate.content_sha256:
            return latest
        return self.store.append_skill_version(candidate)

    def _require_crystallizable_edges(
        self,
        request: SkillCrystallizationRequestV1,
        edges: list[TransitionEdgeV1],
    ) -> None:
        """Keep normal crystallization strict while allowing evidence-backed trials.

        A provisional trial is executable only in validation mode.  It exists so the
        second encounter can validate the first successful demonstration instead of
        forcing another semantic Agent to repeat the same click.  Preferred promotion
        remains governed by the independent SkillRun lifecycle.
        """

        if not edges:
            return
        invalid = [
            edge
            for edge in edges
            if edge.outcome not in _SUCCESSFUL_TRANSITION_OUTCOMES
            and not (
                request.provisional_trial
                and edge.outcome == "deferred"
                and self._deferred_edge_has_confirmed_execution(edge)
            )
        ]
        if not invalid:
            return
        raise SkillLifecycleError(
            "only closed successful transitions may crystallize"
            if not request.provisional_trial
            else (
                "a provisional transition requires a confirmed, meaningful, "
                "evidence-complete execution"
            )
        )

    def _deferred_edge_has_confirmed_execution(self, edge: TransitionEdgeV1) -> bool:
        evidence_step_ids = {
            step_id
            for reference in edge.evidence_refs
            for step_id in reference.evidence_step_ids
        }
        if not evidence_step_ids or edge.to_state_id is None:
            return False
        evidence_steps = [
            self.store.observatory_store.get_evidence_step(step_id)
            for step_id in evidence_step_ids
        ]
        if any(
            step is None
            or step.action != edge.action
            or step.target_bounds != edge.target_bounds
            for step in evidence_steps
        ):
            return False
        samples = self.store.list_action_quality_samples(
            edge.environment_id,
            # Candidate discovery uses the bounded long-history window as well.
            # A confirmed path must not become invalid merely because 100 newer
            # interactions were recorded before its second visit.
            limit=max(10_000, len(evidence_step_ids) * 4),
        )
        confirmed_step_ids = {
            sample.evidence_step_id
            for sample in samples
            if sample.evidence_step_id is not None
            and sample.outcome == "confirmed"
            and sample.execution_disposition == "executed"
            and sample.evidence_complete
            and sample.meaningful_change
            and sample.expected_change_matched is True
            and not sample.invalid_target_execution
            and not sample.policy_violation
        }
        return evidence_step_ids.issubset(
            confirmed_step_ids
        ) or canonical_direct_live_step_confirms_edge(self.store, edge)
