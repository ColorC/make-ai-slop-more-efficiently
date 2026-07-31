"""Resolve reviewed semantic surface anchors into fail-closed action inputs."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from typing import Any
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models import SourcePixelPoint, SourcePixelRect
from .contracts import NormalizedSurfaceRectV1
from .store import AIPlayerStore
from .visual_locator_service import CanonicalVisualLocatorService


class SurfaceAnchorActionPlanV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["game-observatory.ai-player.surface-anchor-action-plan.v1"] = Field(
        default="game-observatory.ai-player.surface-anchor-action-plan.v1",
        alias="schema",
    )
    environment_id: str = Field(min_length=1)
    state_id: str = Field(min_length=1)
    state_version: int = Field(ge=1)
    source_step_id: str = Field(min_length=1)
    source_observation_id: str = Field(min_length=1)
    source_artifact_id: str = Field(min_length=1)
    anchor_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    target_tokens: tuple[str, ...] = Field(min_length=1)
    action: Literal["tap"]
    mobility: Literal["fixed_chrome", "fixed_surface", "dynamic_world_object"]
    disposition: Literal["guarded_tap_ready", "visual_relocalization_required"]
    source_pixel_bounds: SourcePixelRect
    source_point: SourcePixelPoint | None = None
    reference_artifact_id: str | None = None
    requires_running_session: bool
    requires_visual_relocalization: bool
    production_route_ready: Literal[False] = False
    device_accessed: Literal[False] = False

    @model_validator(mode="after")
    def bind_disposition_to_mobility(self) -> "SurfaceAnchorActionPlanV1":
        if self.mobility == "dynamic_world_object":
            if (
                self.disposition != "visual_relocalization_required"
                or self.source_point is not None
                or not self.requires_visual_relocalization
            ):
                raise ValueError("dynamic anchors must stop for visual relocalization")
        elif (
            self.disposition != "guarded_tap_ready"
            or self.source_point is None
            or self.requires_visual_relocalization
        ):
            raise ValueError("fixed anchors must provide one guarded tap point")
        return self


class SurfaceAnchorActionError(ValueError):
    """Raised before device access when a reviewed anchor cannot be resolved safely."""


_INTERACTION_INTENT_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("claim_reward", ("领取", "领奖", "奖励", "claim reward", "reward claim")),
    ("upgrade", ("升级", "升至", "提升", "升星", "upgrade")),
    ("exit", ("关闭", "返回", "退出", "close", "back", "exit")),
    ("battle", ("挑战", "出征", "战斗", "攻占", "battle", "attack")),
    (
        "open",
        ("打开", "进入", "展开", "入口", "面板", "菜单", "查看", "open", "enter"),
    ),
)
_GENERIC_SURFACE_TERMS = {
    "入口",
    "按钮",
    "图标",
    "左侧",
    "右侧",
    "顶部",
    "底部",
    "面板",
    "页面",
    "界面",
    "点击",
    "打开",
    "进入",
}


def _normalized_relevance_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(part for part in re.split(r"[\s:_\-./]+", normalized) if part)


def _normalized_semantic_text(value: str) -> str:
    """Normalize common ordinal slots without binding semantics to one game."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    numerals = r"0-9零〇一二三四五六七八九十百千万两"
    normalized = re.sub(rf"第[{numerals}]+章", "章节", normalized)
    normalized = re.sub(rf"第[{numerals}]+关", "关卡", normalized)
    normalized = re.sub(rf"[{numerals}]+级", "等级", normalized)
    return " ".join(part for part in re.split(r"[\s:_\-./，。；、：:()（）]+", normalized) if part)


def _interaction_intents(value: str) -> tuple[str, ...]:
    normalized = _normalized_semantic_text(value)
    return tuple(
        intent
        for intent, markers in _INTERACTION_INTENT_MARKERS
        if any(marker in normalized for marker in markers)
    )


def _primary_task_intent(task_text: str) -> str | None:
    title, _separator, body = task_text.partition("\n")
    for section in (title, body):
        normalized = _normalized_semantic_text(section)
        matches = [
            (normalized.find(marker), intent)
            for intent, markers in _INTERACTION_INTENT_MARKERS
            for marker in markers
            if marker in normalized
        ]
        if matches:
            return min(matches, key=lambda item: (item[0], item[1]))[1]
    return None


def _semantic_match_terms(task_text: str, candidate_text: str) -> tuple[str, ...]:
    """Return maximal subject phrases shared by a task and a UI selector."""

    task = _normalized_semantic_text(task_text)
    candidate = _normalized_semantic_text(candidate_text)
    phrases: set[str] = set()
    for run in re.findall(r"[\u3400-\u9fff]+", candidate):
        for size in range(2, min(6, len(run)) + 1):
            for start in range(len(run) - size + 1):
                phrase = run[start : start + size]
                if phrase not in _GENERIC_SURFACE_TERMS and phrase in task:
                    phrases.add(phrase)
    for token in re.findall(r"[a-z0-9]+", candidate):
        if len(token) >= 3 and token in task:
            phrases.add(token)
    return tuple(
        sorted(
            (
                phrase
                for phrase in phrases
                if not any(phrase != other and phrase in other for other in phrases)
            ),
            key=lambda item: (-len(item), task.find(item), item),
        )
    )


def _task_relevance(
    *,
    task_text: str,
    candidate_text: str,
    operation_text: str,
) -> dict[str, object] | None:
    match_terms = _semantic_match_terms(task_text, candidate_text)
    if not match_terms:
        return None
    normalized_task = _normalized_semantic_text(task_text)
    primary_intent = _primary_task_intent(task_text)
    operation_intents = _interaction_intents(operation_text)
    if primary_intent is None or not operation_intents:
        alignment = "unspecified"
    elif primary_intent in operation_intents:
        alignment = "aligned"
    else:
        alignment = "deferred_phase"
    return {
        "match_terms": list(match_terms),
        "match_score": sum(len(item) for item in match_terms),
        "first_match_offset": min(normalized_task.find(item) for item in match_terms),
        "task_primary_intent": primary_intent,
        "operation_intents": list(operation_intents),
        "intent_alignment": alignment,
    }


def _surface_anchor_relevance_score(
    *,
    task_text: str,
    role: str,
    target_tokens: tuple[str, ...],
) -> int:
    task = _normalized_relevance_text(task_text)
    if not task:
        return 0
    terms = [*target_tokens, role]
    matches = {
        term
        for raw_term in terms
        if len(term := _normalized_relevance_text(raw_term)) >= 2 and term in task
    }
    return max((len(term) for term in matches), default=0)


def _successful_zero_model_runs_by_skill(
    skill_runs: Iterable[Any],
) -> dict[str, list[Any]]:
    successful: dict[str, list[Any]] = defaultdict(list)
    for run in skill_runs:
        if (
            getattr(run, "outcome", None) == "success"
            and getattr(run, "objective_success", False)
            and getattr(run, "validation_passed", False)
            and not getattr(run, "false_success", False)
            and getattr(run, "safety_violation_count", 0) == 0
            and getattr(run, "model_input_tokens", 0) == 0
        ):
            successful[str(run.skill_version_id)].append(run)
    return successful


def _operation_expectation(skill: Any, target_name: str) -> str:
    applicability = str(getattr(skill, "applicability", ""))
    match = re.search(r"目标为\s*[：:]\s*(.+?)(?:[。.]|$)", applicability)
    if match and (expectation := match.group(1).strip()):
        return expectation
    return f"点击后显示与{target_name}相关的可见界面变化"


def _historical_operation_contract(
    *,
    anchor: Any,
    skill_versions: Iterable[Any],
    successful_runs_by_skill: dict[str, list[Any]],
) -> dict[str, object] | None:
    """Reuse proven semantics without reusing a dynamic object's old coordinates."""

    semantic_terms = {
        _normalized_relevance_text(value)
        for value in (*anchor.target_tokens, anchor.role)
        if _normalized_relevance_text(value)
    }
    candidates: list[tuple[tuple[int, int, int, str], Any, Any]] = []
    status_rank = {"candidate": 1, "validated": 2, "preferred": 3}
    for skill in skill_versions:
        if (
            getattr(skill, "status", None) not in status_rank
            or getattr(skill, "skill_layer", None) != "atomic"
            or getattr(skill, "executor_kind", None) != "normalized_actions"
            or getattr(skill, "safety_level", None) not in {"read_only", "reversible"}
            or _normalized_relevance_text(str(getattr(skill, "title", "")))
            not in semantic_terms
        ):
            continue
        action_steps = [step for step in getattr(skill, "steps", ()) if step.kind == "action"]
        assert_steps = [
            step
            for step in getattr(skill, "steps", ())
            if step.kind == "assert" and getattr(step, "expected_state_id", None)
        ]
        if (
            len(action_steps) != 1
            or len(assert_steps) != 1
            or getattr(getattr(action_steps[0], "action", None), "type", None)
            != anchor.action
        ):
            continue
        successful_runs = successful_runs_by_skill.get(str(skill.id), [])
        if not successful_runs:
            continue
        rank = (
            len(successful_runs),
            status_rank[str(skill.status)],
            int(getattr(skill, "version", 0)),
            str(skill.id),
        )
        candidates.append((rank, skill, assert_steps[0]))
    if not candidates:
        return None

    _rank, skill, assertion = max(candidates, key=lambda item: item[0])
    successful_runs = successful_runs_by_skill[str(skill.id)]
    target_name = str(anchor.target_tokens[0])
    return {
        "skill_version_id": str(skill.id),
        "skill_title": str(skill.title),
        "successful_zero_model_run_count": len(successful_runs),
        "expected_terminal_state_id": str(assertion.expected_state_id),
        "safety_level": str(skill.safety_level),
        "expectation_summary": _operation_expectation(skill, target_name),
        "reuse_mode": (
            "semantic_contract_with_visual_relocalization"
            if anchor.mobility == "dynamic_world_object"
            else "semantic_contract_with_guarded_anchor"
        ),
        "reuses_historical_coordinates": False,
    }


def materialize_normalized_surface_rect(
    rect: NormalizedSurfaceRectV1,
    *,
    viewport_width: int,
    viewport_height: int,
) -> SourcePixelRect:
    if viewport_width <= 0 or viewport_height <= 0:
        raise SurfaceAnchorActionError("surface anchor viewport must be positive")
    left = rect.x * viewport_width // 1000
    top = rect.y * viewport_height // 1000
    right = ((rect.x + rect.width) * viewport_width + 999) // 1000
    bottom = ((rect.y + rect.height) * viewport_height + 999) // 1000
    return SourcePixelRect(
        x=left,
        y=top,
        width=max(1, right - left),
        height=max(1, bottom - top),
    )


def _resolve_current_surface_source(
    player: AIPlayerStore,
    *,
    environment_id: str,
    state_id: str,
    source_step_id: str,
) -> tuple[Any, Any, Any, Any, Any, Any]:
    """Resolve one current accepted frame once for every surface operation hint."""

    environment = player.get_environment(environment_id)
    if environment is None:
        raise SurfaceAnchorActionError(
            f"surface anchor environment is missing: {environment_id}"
        )
    if not player.is_unique_current_environment_leaf(environment_id):
        raise SurfaceAnchorActionError("surface anchor requires the current environment leaf")
    state = player.get_semantic_state(environment_id, state_id)
    if state is None or state.status != "accepted":
        raise SurfaceAnchorActionError("surface anchor requires an accepted semantic state")

    store = player.observatory_store
    step = store.get_evidence_step(source_step_id)
    if step is None or step.status != "passed" or not step.ended_at:
        raise SurfaceAnchorActionError("surface anchor source step is not terminal passed")
    run = store.get_evidence_run(step.evidence_run_id)
    if run is None or run.status != "passed" or not run.ended_at:
        raise SurfaceAnchorActionError("surface anchor source run is not terminal passed")
    if run.scope_id != environment_id:
        raise SurfaceAnchorActionError("surface anchor source belongs to another environment")
    if (run.viewport_width, run.viewport_height) != (
        environment.viewport_width,
        environment.viewport_height,
    ):
        raise SurfaceAnchorActionError("surface anchor source viewport differs from environment")
    source = store.get_artifact(str(step.after_frame_id))
    if source is None or source.metadata.get("semantic_state_eligible") is not True:
        raise SurfaceAnchorActionError("surface anchor source is not semantic-state eligible")

    matching_observation = player.find_unique_current_state_observation_for_evidence(
        environment_id,
        state_id=state.id,
        evidence_step_id=source_step_id,
        artifact_id=source.id,
    )
    if matching_observation is None:
        raise SurfaceAnchorActionError(
            "surface anchor source does not map uniquely to the current state"
        )
    return environment, state, step, run, source, matching_observation


def resolve_surface_anchor_action_plan(
    player: AIPlayerStore,
    *,
    environment_id: str,
    state_id: str,
    source_step_id: str,
    anchor_id: str,
) -> SurfaceAnchorActionPlanV1:
    environment, state, step, run, source, matching_observation = (
        _resolve_current_surface_source(
            player,
            environment_id=environment_id,
            state_id=state_id,
            source_step_id=source_step_id,
        )
    )
    anchors = [item for item in state.surface_anchors if item.id == anchor_id]
    if len(anchors) != 1:
        raise SurfaceAnchorActionError("surface anchor is absent or ambiguous in current state")
    anchor = anchors[0]
    if anchor.action != "tap":
        raise SurfaceAnchorActionError("tap-anchor only accepts reviewed tap anchors")

    bounds = materialize_normalized_surface_rect(
        anchor.normalized_bounds,
        viewport_width=run.viewport_width,
        viewport_height=run.viewport_height,
    )
    dynamic = anchor.mobility == "dynamic_world_object"
    point = (
        None
        if dynamic
        else SourcePixelPoint(
            x=bounds.x + bounds.width // 2,
            y=bounds.y + bounds.height // 2,
        )
    )
    return SurfaceAnchorActionPlanV1(
        environment_id=environment_id,
        state_id=state.id,
        state_version=state.version,
        source_step_id=step.id,
        source_observation_id=matching_observation.id,
        source_artifact_id=source.id,
        anchor_id=anchor.id,
        role=anchor.role,
        target_tokens=tuple(anchor.target_tokens),
        action="tap",
        mobility=anchor.mobility,
        disposition=(
            "visual_relocalization_required" if dynamic else "guarded_tap_ready"
        ),
        source_pixel_bounds=bounds,
        source_point=point,
        reference_artifact_id=anchor.reference_artifact_id,
        requires_running_session=not dynamic,
        requires_visual_relocalization=dynamic,
    )


def guard_locator_element_for_surface_anchor(
    player: AIPlayerStore,
    *,
    environment_id: str,
    state_id: str,
    source_step_id: str,
    anchor_id: str,
    element_bounds: SourcePixelRect,
) -> SurfaceAnchorActionPlanV1:
    """Bind one locator element to the reviewed dynamic anchor before device access."""

    plan = resolve_surface_anchor_action_plan(
        player,
        environment_id=environment_id,
        state_id=state_id,
        source_step_id=source_step_id,
        anchor_id=anchor_id,
    )
    if not plan.requires_visual_relocalization:
        raise SurfaceAnchorActionError(
            "locator element binding only accepts dynamic surface anchors"
        )
    center = SourcePixelPoint(
        x=element_bounds.x + element_bounds.width // 2,
        y=element_bounds.y + element_bounds.height // 2,
    )
    if not plan.source_pixel_bounds.contains(center):
        raise SurfaceAnchorActionError(
            "locator element center is outside the reviewed surface anchor"
        )
    return plan


def _skill_scope_matches_environment(skill: Any, environment: Any) -> bool:
    scope = getattr(skill, "applicability_scope", None)
    if scope is None or getattr(skill, "environment_id", None) != environment.id:
        return False
    game_ids = {environment.game_id, *getattr(environment, "game_id_aliases", ())}
    build_ids = {
        environment.build_scope_id,
        *getattr(environment, "build_scope_id_aliases", ()),
    }
    device_ids = {
        environment.device_scope_id,
        *getattr(environment, "device_scope_id_aliases", ()),
    }
    if (
        scope.game_id not in game_ids
        or not build_ids.intersection(scope.build_scope_ids)
        or scope.channel != environment.channel
        or scope.locale != environment.locale
        or not device_ids.intersection(scope.device_scope_ids)
        or environment.viewport_width not in scope.viewport_widths
        or environment.viewport_height not in scope.viewport_heights
    ):
        return False
    optional_scope_checks = (
        (scope.account_scope_ids, environment.account_scope_id),
        (scope.server_scope_ids, environment.server_scope_id),
        (scope.world_scope_ids, environment.world_scope_id),
    )
    return all(not allowed or current in allowed for allowed, current in optional_scope_checks)


def _load_current_source_locator_result(
    player: AIPlayerStore,
    *,
    source: Any,
    environment_id: str,
    source_step_id: str,
    evidence_run_id: str,
    width: int,
    height: int,
) -> dict[str, Any] | None:
    store = player.observatory_store
    if getattr(store, "root", None) is None:
        return None
    try:
        return CanonicalVisualLocatorService(store).load_for_source(
            source=source,
            environment_id=environment_id,
            source_step_id=source_step_id,
            evidence_run_id=evidence_run_id,
            width=width,
            height=height,
        )
    except Exception as exc:  # noqa: BLE001 - corrupt canonical cache must fail closed
        raise SurfaceAnchorActionError(
            f"current surface locator result is invalid: {exc}"
        ) from exc


def _unique_interaction_element_inside(
    locator_result: dict[str, Any] | None,
    bounds: SourcePixelRect,
) -> dict[str, object] | None:
    if locator_result is None:
        return None
    matches: list[tuple[dict[str, Any], SourcePixelRect]] = []
    for element in locator_result.get("elements", ()):
        if not isinstance(element, dict) or element.get("interaction_candidate") is not True:
            continue
        try:
            element_bounds = SourcePixelRect.model_validate(
                element.get("bounds") or element.get("source_bounds")
            )
        except ValueError as exc:
            raise SurfaceAnchorActionError(
                "current surface locator result contains invalid element bounds"
            ) from exc
        center = SourcePixelPoint(
            x=element_bounds.x + element_bounds.width // 2,
            y=element_bounds.y + element_bounds.height // 2,
        )
        if bounds.contains(center):
            matches.append((element, element_bounds))
    if len(matches) != 1:
        return None
    element, element_bounds = matches[0]
    element_id = str(element.get("element_id") or element.get("id") or "").strip()
    result_id = str(locator_result.get("id") or "").strip()
    if not element_id or not result_id:
        raise SurfaceAnchorActionError(
            "current surface locator result has no stable result or element id"
        )
    return {
        "locator_result_id": result_id,
        "element_id": element_id,
        "element_bounds": element_bounds.model_dump(mode="json"),
        "content": str(element.get("content") or ""),
        "type": str(element.get("type") or "unknown"),
        "unique_interaction_candidate": True,
    }


def build_task_relevant_consensus_locator_hints(
    player: AIPlayerStore,
    *,
    environment_id: str,
    state_id: str,
    source_step_id: str,
    session_id: str,
    task_text: str,
    skill_versions: Iterable[Any] = (),
    limit: int = 3,
) -> tuple[dict[str, object], ...]:
    """Expose cross-state fixed locator consensus as current-frame validation work.

    A hint is deliberately weaker than a reviewed state anchor and never emits a
    coordinate tap.  Two distinct source states and EvidenceRuns must agree on
    selector, bounds, mobility and action before a bounded current-frame locator
    command is exposed.
    """

    if limit < 1:
        return ()
    environment, state, step, run, source, _observation = (
        _resolve_current_surface_source(
            player,
            environment_id=environment_id,
            state_id=state_id,
            source_step_id=source_step_id,
        )
    )
    current_locator_result = _load_current_source_locator_result(
        player,
        source=source,
        environment_id=environment_id,
        source_step_id=step.id,
        evidence_run_id=run.id,
        width=run.viewport_width,
        height=run.viewport_height,
    )
    reviewed_bounds = {
        tuple(
            materialize_normalized_surface_rect(
                anchor.normalized_bounds,
                viewport_width=environment.viewport_width,
                viewport_height=environment.viewport_height,
            ).model_dump().values()
        )
        for anchor in state.surface_anchors
    }
    groups: dict[tuple[object, ...], list[dict[str, object]]] = defaultdict(list)
    for skill in skill_versions:
        if (
            getattr(skill, "status", None) not in {"candidate", "validated", "preferred"}
            or getattr(skill, "skill_layer", None) != "atomic"
            or getattr(skill, "executor_kind", None) != "normalized_actions"
            or getattr(skill, "safety_level", None) not in {"read_only", "reversible"}
            or not _skill_scope_matches_environment(skill, environment)
        ):
            continue
        action_steps = [step for step in getattr(skill, "steps", ()) if step.kind == "action"]
        if len(action_steps) != 1:
            continue
        action_step = action_steps[0]
        if getattr(getattr(action_step, "action", None), "type", None) != "tap":
            continue
        locators = [
            locator
            for locator in getattr(skill, "locators", ())
            if locator.id == getattr(action_step, "locator_id", None)
        ]
        if len(locators) != 1:
            continue
        locator = locators[0]
        bounds = getattr(locator, "reference_bounds", None)
        if (
            getattr(locator, "mobility", None) not in {"fixed_chrome", "fixed_surface"}
            or bounds is None
            or not getattr(locator, "reference_artifact_id", None)
        ):
            continue
        evidence_refs = tuple(getattr(skill, "evidence_refs", ()))
        artifact_ids = {
            str(artifact_id)
            for reference in evidence_refs
            for artifact_id in getattr(reference, "artifact_ids", ())
        }
        evidence_run_ids = {
            str(run_id)
            for reference in evidence_refs
            for run_id in getattr(reference, "evidence_run_ids", ())
        }
        evidence_step_ids = {
            str(step_id)
            for reference in evidence_refs
            for step_id in getattr(reference, "evidence_step_ids", ())
        }
        if (
            str(locator.reference_artifact_id) not in artifact_ids
            or not evidence_run_ids
            or not evidence_step_ids
        ):
            continue
        selector = str(locator.selector).strip()
        relevance = _task_relevance(
            task_text=task_text,
            candidate_text=f"{selector} {getattr(skill, 'title', '')}",
            operation_text=" ".join(
                [
                    selector,
                    str(getattr(skill, "title", "")),
                    str(getattr(skill, "applicability", "")),
                    *[str(item) for item in getattr(skill, "success_checks", ())],
                ]
            ),
        )
        if relevance is None:
            continue
        bounds_key = (bounds.x, bounds.y, bounds.width, bounds.height)
        key = (
            _normalized_relevance_text(selector),
            *bounds_key,
            str(locator.mobility),
            "tap",
        )
        groups[key].append(
            {
                "skill_id": str(skill.id),
                "source_state_ids": set(skill.applicability_scope.required_state_ids),
                "source_transition_ids": set(getattr(skill, "source_transition_ids", ())),
                "evidence_run_ids": evidence_run_ids,
                "evidence_step_ids": evidence_step_ids,
                "reference_artifact_ids": {str(locator.reference_artifact_id)},
                "selector": selector,
                "bounds": bounds,
                "mobility": str(locator.mobility),
                "operation_text": " ".join(
                    [
                        selector,
                        str(getattr(skill, "title", "")),
                        str(getattr(skill, "applicability", "")),
                        *[str(item) for item in getattr(skill, "success_checks", ())],
                    ]
                ),
                "relevance": relevance,
            }
        )

    ranked: list[tuple[tuple[int, int, int, int, str], dict[str, object]]] = []
    alignment_rank = {"aligned": 0, "unspecified": 1, "deferred_phase": 2}
    for entries in groups.values():
        skill_ids = {str(entry["skill_id"]) for entry in entries}
        source_state_ids = {
            str(item)
            for entry in entries
            for item in entry["source_state_ids"]  # type: ignore[union-attr]
        }
        evidence_run_ids = {
            str(item)
            for entry in entries
            for item in entry["evidence_run_ids"]  # type: ignore[union-attr]
        }
        evidence_step_ids = {
            str(item)
            for entry in entries
            for item in entry["evidence_step_ids"]  # type: ignore[union-attr]
        }
        reference_artifact_ids = {
            str(item)
            for entry in entries
            for item in entry["reference_artifact_ids"]  # type: ignore[union-attr]
        }
        if (
            len(skill_ids) < 2
            or len(source_state_ids) < 2
            or len(evidence_run_ids) < 2
            or len(reference_artifact_ids) < 2
        ):
            continue
        exemplar = entries[0]
        bounds = exemplar["bounds"]
        bounds_key = (bounds.x, bounds.y, bounds.width, bounds.height)  # type: ignore[union-attr]
        if bounds_key in reviewed_bounds:
            continue
        selector = str(exemplar["selector"])
        relevance = _task_relevance(
            task_text=task_text,
            candidate_text=selector,
            operation_text=" ".join(str(entry["operation_text"]) for entry in entries),
        )
        if relevance is None:
            continue
        expectation = f"显示与{selector}对应的当前界面，并产生可见状态变化"
        current_locator = _unique_interaction_element_inside(
            current_locator_result,
            bounds,  # type: ignore[arg-type]
        )
        tap_element_ready = current_locator is not None
        locator_result_id = (
            str(current_locator["locator_result_id"])
            if current_locator is not None
            else "<locator_result_id>"
        )
        element_id = (
            str(current_locator["element_id"])
            if current_locator is not None
            else "<element_id>"
        )
        hint: dict[str, object] = {
            "selector": selector,
            "mobility": str(exemplar["mobility"]),
            "disposition": (
                "current_frame_locator_ready"
                if tap_element_ready
                else "current_frame_visual_validation_required"
            ),
            "source_bounds": bounds.model_dump(mode="json"),  # type: ignore[union-attr]
            "task_relevance": relevance,
            "consensus": {
                "skill_version_ids": sorted(skill_ids),
                "independent_source_state_ids": sorted(source_state_ids),
                "evidence_run_ids": sorted(evidence_run_ids),
                "evidence_step_ids": sorted(evidence_step_ids),
                "reference_artifact_ids": sorted(reference_artifact_ids),
                "agreement_fields": ["selector", "bounds", "mobility", "action"],
            },
            "command": (
                "omni game player --json --agent-brief observe locate "
                f"--environment {environment_id} --source-step {source_step_id} "
                f"--region {bounds.x} {bounds.y} {bounds.width} {bounds.height} "  # type: ignore[union-attr]
                "--candidate-only --timeout 90"
            ),
            "next_command": (
                "omni game player --json --agent-brief act tap-element "
                f"{locator_result_id} {element_id} "
                f"--environment {environment_id} --session {session_id} "
                f'--target "{selector}" --expect "{expectation}"'
            ),
            "current_locator": current_locator,
            "tap_element_ready": tap_element_ready,
            "tap_requires_current_locator_selection": not tap_element_ready,
            "production_route_ready": False,
            "reuses_historical_coordinates": False,
            "device_accessed": False,
        }
        ranked.append(
            (
                (
                    alignment_rank[str(relevance["intent_alignment"])],
                    int(relevance["first_match_offset"]),
                    -int(relevance["match_score"]),
                    -len(skill_ids),
                    selector,
                ),
                hint,
            )
        )
    return tuple(item for _rank, item in sorted(ranked, key=lambda item: item[0])[:limit])


def build_task_relevant_surface_anchor_actions(
    player: AIPlayerStore,
    *,
    environment_id: str,
    state_id: str,
    source_step_id: str,
    session_id: str,
    task_text: str,
    limit: int = 3,
    skill_versions: Iterable[Any] = (),
    skill_runs: Iterable[Any] = (),
) -> tuple[dict[str, object], ...]:
    """Build compact exact commands only for reviewed anchors relevant to this task."""

    if limit < 1:
        return ()
    state = player.get_semantic_state(environment_id, state_id)
    if state is None or state.status != "accepted":
        raise SurfaceAnchorActionError(
            "task-relevant surface anchors require an accepted semantic state"
        )
    ranked: list[tuple[int, int, str]] = []
    for index, anchor in enumerate(state.surface_anchors):
        score = _surface_anchor_relevance_score(
            task_text=task_text,
            role=anchor.role,
            target_tokens=tuple(anchor.target_tokens),
        )
        if score:
            ranked.append((-score, index, anchor.id))

    successful_runs_by_skill = _successful_zero_model_runs_by_skill(skill_runs)
    reusable_skills = tuple(skill_versions)
    actions: list[tuple[tuple[int, int, int, int, str], dict[str, object]]] = []
    alignment_rank = {"aligned": 0, "unspecified": 1, "deferred_phase": 2}
    for negative_score, index, anchor_id in sorted(ranked):
        plan = resolve_surface_anchor_action_plan(
            player,
            environment_id=environment_id,
            state_id=state_id,
            source_step_id=source_step_id,
            anchor_id=anchor_id,
        )
        bounds = plan.source_pixel_bounds
        operation_contract = _historical_operation_contract(
            anchor=next(item for item in state.surface_anchors if item.id == anchor_id),
            skill_versions=reusable_skills,
            successful_runs_by_skill=successful_runs_by_skill,
        )
        target_name = str(plan.target_tokens[0])
        expectation_summary = (
            str(operation_contract["expectation_summary"])
            if operation_contract is not None
            else f"点击后显示与{target_name}相关的可见界面变化"
        )
        task_relevance = _task_relevance(
            task_text=task_text,
            candidate_text=" ".join([plan.role, *plan.target_tokens]),
            operation_text=" ".join(
                [
                    plan.role,
                    *plan.target_tokens,
                    expectation_summary,
                    (
                        str(operation_contract["skill_title"])
                        if operation_contract is not None
                        else ""
                    ),
                ]
            ),
        ) or {
            "match_terms": list(plan.target_tokens),
            "match_score": -negative_score,
            "first_match_offset": index,
            "task_primary_intent": _primary_task_intent(task_text),
            "operation_intents": [],
            "intent_alignment": "unspecified",
        }
        item: dict[str, object] = {
            "anchor_id": plan.anchor_id,
            "role": plan.role,
            "target_tokens": list(plan.target_tokens),
            "mobility": plan.mobility,
            "disposition": plan.disposition,
            "source_bounds": bounds.model_dump(mode="json"),
            "device_accessed": False,
            "operation_contract": operation_contract,
            "task_relevance": task_relevance,
        }
        if plan.requires_visual_relocalization:
            item.update(
                {
                    "command": (
                        "omni game player --json --agent-brief observe locate "
                        f"--environment {environment_id} --source-step {source_step_id} "
                        f"--region {bounds.x} {bounds.y} {bounds.width} {bounds.height} "
                        "--candidate-only --timeout 90"
                    ),
                    "next_command": (
                        "omni game player --json --agent-brief act tap-element "
                        "<locator_result_id> <element_id> "
                        f"--environment {environment_id} --session {session_id} "
                        f"--anchor-state {plan.state_id} --anchor {plan.anchor_id} "
                        f'--target "{target_name}" '
                        f'--expect "{expectation_summary}"'
                    ),
                }
            )
        else:
            item["command"] = (
                "omni game player --json --agent-brief act tap-anchor "
                f"{plan.anchor_id} --environment {environment_id} --session {session_id} "
                f"--source-step {source_step_id} "
                f'--target "{target_name}" --expect "{expectation_summary}"'
            )
        actions.append(
            (
                (
                    alignment_rank[str(task_relevance["intent_alignment"])],
                    int(task_relevance["first_match_offset"]),
                    -int(task_relevance["match_score"]),
                    index,
                    anchor_id,
                ),
                item,
            )
        )
    return tuple(item for _rank, item in sorted(actions, key=lambda entry: entry[0])[:limit])


__all__ = [
    "SurfaceAnchorActionError",
    "SurfaceAnchorActionPlanV1",
    "build_task_relevant_consensus_locator_hints",
    "build_task_relevant_surface_anchor_actions",
    "guard_locator_element_for_surface_anchor",
    "materialize_normalized_surface_rect",
    "resolve_surface_anchor_action_plan",
]
