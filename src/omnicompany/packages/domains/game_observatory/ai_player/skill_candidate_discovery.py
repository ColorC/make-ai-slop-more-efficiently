"""Turn successful live interactions into immediately replayable skill trials.

The first confirmed demonstration creates an immutable candidate.  The next
encounter can execute that candidate through ``SkillRuntime`` with no per-step
model planning.  Independent replays still decide whether it becomes preferred.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .contracts import SkillVersionV1, TransitionEdgeV1
from .crystallizer import SkillCrystallizationRequestV1, SkillCrystallizer
from .store import AIPlayerStore


_SUCCESSFUL_OUTCOMES = {
    "verified_transition",
    "verified_state_change",
    "verified_progress",
}
_ENDPOINT_STATUSES = {"candidate", "accepted"}


@dataclass(frozen=True)
class SkillCandidateDiscoveryReport:
    eligible_signature_count: int
    candidate_version_ids: tuple[str, ...]
    skipped_preferred_skill_ids: tuple[str, ...]
    atomic_candidate_count: int = 0
    flow_candidate_count: int = 0


def _edge_signature(edge: TransitionEdgeV1) -> str:
    return json.dumps(
        {
            "from_state_id": edge.from_state_id,
            "to_state_id": edge.to_state_id,
            "action": edge.action.model_dump(mode="json"),
            "target_bounds": (
                edge.target_bounds.model_dump(mode="json")
                if edge.target_bounds is not None
                else None
            ),
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _route_signature(edges: list[TransitionEdgeV1]) -> str:
    return json.dumps(
        [_edge_signature(edge) for edge in edges],
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def _evidence_step_ids(edge: TransitionEdgeV1) -> set[str]:
    return {
        item
        for reference in edge.evidence_refs
        for item in reference.evidence_step_ids
    }


def _confirmed_step_ids(store: AIPlayerStore, environment_id: str) -> set[str]:
    return {
        sample.evidence_step_id
        for sample in store.list_action_quality_samples(environment_id, limit=10_000)
        if sample.evidence_step_id is not None
        and sample.outcome == "confirmed"
        and sample.execution_disposition == "executed"
        and sample.evidence_complete
        and sample.meaningful_change
        and sample.expected_change_matched is True
        and not sample.invalid_target_execution
        and not sample.policy_violation
    }


def _edge_is_replayable_trial(
    edge: TransitionEdgeV1,
    *,
    confirmed_step_ids: set[str],
    replayable_state_ids: set[str],
) -> bool:
    if edge.to_state_id is None:
        return False
    if edge.from_state_id not in replayable_state_ids or edge.to_state_id not in replayable_state_ids:
        return False
    if edge.outcome in _SUCCESSFUL_OUTCOMES:
        return True
    step_ids = _evidence_step_ids(edge)
    return edge.outcome == "deferred" and bool(step_ids) and step_ids.issubset(
        confirmed_step_ids
    )


def _skill_matches_route(skill: SkillVersionV1, edges: list[TransitionEdgeV1]) -> bool:
    if not edges or edges[0].from_state_id not in skill.applicability_scope.required_state_ids:
        return False
    action_steps = [step for step in skill.steps if step.kind == "action"]
    assertion_steps = [step for step in skill.steps if step.kind == "assert"]
    if len(action_steps) != len(edges) or len(assertion_steps) != len(edges):
        return False
    locators = {item.id: item for item in skill.locators}
    for edge, action_step, assertion_step in zip(
        edges,
        action_steps,
        assertion_steps,
        strict=True,
    ):
        if action_step.action != edge.action or assertion_step.expected_state_id != edge.to_state_id:
            return False
        if edge.target_bounds is None:
            if action_step.locator_id is not None:
                return False
        else:
            locator = locators.get(action_step.locator_id or "")
            if locator is None or locator.reference_bounds != edge.target_bounds:
                return False
    return True


def _automatic_skill_id(game_id: str, layer: str, signature: str) -> str:
    safe_game = re.sub(r"[^a-z0-9]+", "-", game_id.lower()).strip("-") or "game"
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
    return f"skill.auto.{safe_game}.{layer}.{digest}"


def _edge_labels(store: AIPlayerStore, edge: TransitionEdgeV1) -> tuple[str, str]:
    target = "执行已记录交互"
    expectation = edge.expected_change
    for reference in reversed(edge.evidence_refs):
        for step_id in reversed(reference.evidence_step_ids):
            step = store.observatory_store.get_evidence_step(step_id)
            if step is None:
                continue
            target = step.target_name or target
            run = store.observatory_store.get_evidence_run(step.evidence_run_id)
            if run is not None:
                expectation = str(
                    run.environment.get("pre_execution_expectation") or expectation
                )
            return target, expectation
    return target, expectation


def _default_safety(store: AIPlayerStore, edges: list[TransitionEdgeV1]) -> str:
    labels = " ".join(" ".join(_edge_labels(store, edge)) for edge in edges)
    high_effect_words = (
        "领取",
        "升级",
        "购买",
        "消耗",
        "招募",
        "征兵",
        "确认",
        "替换",
        "出征",
        "挑战",
    )
    if any(word in labels for word in high_effect_words):
        return "progression"
    if all(edge.action.type == "wait" for edge in edges):
        return "read_only"
    return "reversible"


def _flow_routes(
    store: AIPlayerStore,
    edges: list[TransitionEdgeV1],
) -> list[list[TransitionEdgeV1]]:
    """Return maximal, invocation-local continuous routes of two to six actions."""

    by_invocation: dict[str, list[TransitionEdgeV1]] = defaultdict(list)
    for edge in edges:
        invocation_id = None
        for reference in edge.evidence_refs:
            for step_id in reference.evidence_step_ids:
                step = store.observatory_store.get_evidence_step(step_id)
                if step is None:
                    continue
                run = store.observatory_store.get_evidence_run(step.evidence_run_id)
                if run is not None:
                    invocation_id = run.environment.get("external_agent_invocation_id")
                if invocation_id:
                    break
            if invocation_id:
                break
        if invocation_id:
            by_invocation[str(invocation_id)].append(edge)

    routes: list[list[TransitionEdgeV1]] = []
    for invocation_edges in by_invocation.values():
        ordered = sorted(invocation_edges, key=lambda item: (item.created_at, item.id))
        current: list[TransitionEdgeV1] = []
        for edge in ordered:
            if current and current[-1].to_state_id != edge.from_state_id:
                if len(current) >= 2:
                    routes.append(current[-6:])
                current = []
            current.append(edge)
        if len(current) >= 2:
            routes.append(current[-6:])
    unique: dict[str, list[TransitionEdgeV1]] = {}
    for route in routes:
        unique.setdefault(_route_signature(route), route)
    return list(unique.values())


def _candidate_request(
    store: AIPlayerStore,
    *,
    environment_id: str,
    game_id: str,
    edges: list[TransitionEdgeV1],
    level: str,
    skill_id: str,
    existing: SkillVersionV1 | None,
) -> SkillCrystallizationRequestV1:
    first_target, _first_expectation = _edge_labels(store, edges[0])
    _last_target, last_expectation = _edge_labels(store, edges[-1])
    title = first_target if len(edges) == 1 else f"{first_target} → {last_expectation}"
    return SkillCrystallizationRequestV1(
        environment_id=environment_id,
        creator_id="ai-player-program-layer.route-candidate-discovery",
        skill_id=skill_id,
        title=existing.title if existing is not None else title[:160],
        level=level,
        transition_ids=[edge.id for edge in edges],
        applicability=(
            existing.applicability
            if existing is not None
            else f"当前处于已记录起点，目标为：{last_expectation}。"
        ),
        safety_level=(
            existing.safety_level if existing is not None else _default_safety(store, edges)
        ),
        parameters_schema=existing.parameters_schema if existing is not None else {},
        success_checks=[f"终态重新识别为 {edges[-1].to_state_id}：{last_expectation}"],
        failure_checks=["任一步终态与记录状态不一致，立即停止并交回语义探索。"],
        recovery_skill_version_ids=(
            existing.recovery_skill_version_ids if existing is not None else []
        ),
        visual_variant_ids=[],
        executor_kind="normalized_actions",
        perception_tier=existing.perception_tier if existing is not None else "P2",
        provisional_trial=any(edge.outcome == "deferred" for edge in edges),
    )


def crystallize_repeated_atomic_skill_candidates(
    store: AIPlayerStore,
    environment_id: str,
    *,
    minimum_evidence_samples: int = 1,
    limit: int = 8,
) -> SkillCandidateDiscoveryReport:
    """Create one-step and invocation-level flow candidates after the first success.

    The historical function name stays stable for callers.  Repetition is gathered by
    signed SkillRun validation; it is no longer required before the first replay trial.
    """

    if minimum_evidence_samples < 1:
        raise ValueError("automatic skill discovery requires at least one evidence sample")
    environment = store.get_environment(environment_id)
    if environment is None:
        raise ValueError(f"unknown environment: {environment_id}")
    replayable_state_ids = {
        item.id
        for item in store.list_semantic_states(environment_id, statuses=_ENDPOINT_STATUSES)
    }
    confirmed_step_ids = _confirmed_step_ids(store, environment_id)
    replayable_edges = [
        edge
        for edge in store.list_transition_edges(environment_id)
        if _edge_is_replayable_trial(
            edge,
            confirmed_step_ids=confirmed_step_ids,
            replayable_state_ids=replayable_state_ids,
        )
    ]

    atomic_groups: dict[str, list[TransitionEdgeV1]] = defaultdict(list)
    for edge in replayable_edges:
        atomic_groups[_edge_signature(edge)].append(edge)
    atomic_routes = [
        sorted(edges, key=lambda item: (item.created_at, item.id))
        for edges in atomic_groups.values()
        if len(edges) >= minimum_evidence_samples
    ]
    flow_routes = _flow_routes(store, replayable_edges)
    route_specs: list[tuple[str, list[TransitionEdgeV1]]] = [
        *(('L3', route) for route in flow_routes),
        *(('L2', [route[0]]) for route in atomic_routes),
    ]
    route_specs.sort(
        key=lambda item: (-len(item[1]), item[1][-1].created_at, _route_signature(item[1]))
    )

    versions = store.list_skill_versions(environment_id)
    created: list[str] = []
    skipped_preferred: list[str] = []
    crystallizer = SkillCrystallizer(store)
    atomic_count = 0
    flow_count = 0
    for level, edges in route_specs[:limit]:
        signature = _route_signature(edges)
        matching = [item for item in versions if _skill_matches_route(item, edges)]
        matching.sort(key=lambda item: (item.version, item.created_at, item.id))
        latest = matching[-1] if matching else None
        if latest is not None and latest.status == "preferred":
            skipped_preferred.append(latest.skill_id)
            continue
        if latest is not None and latest.status == "candidate":
            created.append(latest.id)
            if level == "L3":
                flow_count += 1
            else:
                atomic_count += 1
            continue
        skill_id = (
            latest.skill_id
            if latest is not None
            else _automatic_skill_id(
                environment.game_id,
                "flow" if level == "L3" else "atomic",
                signature,
            )
        )
        candidate = crystallizer.crystallize(
            _candidate_request(
                store,
                environment_id=environment_id,
                game_id=environment.game_id,
                edges=edges,
                level=level,
                skill_id=skill_id,
                existing=latest,
            )
        )
        versions.append(candidate)
        created.append(candidate.id)
        if level == "L3":
            flow_count += 1
        else:
            atomic_count += 1

    return SkillCandidateDiscoveryReport(
        eligible_signature_count=len(route_specs),
        candidate_version_ids=tuple(dict.fromkeys(created)),
        skipped_preferred_skill_ids=tuple(dict.fromkeys(skipped_preferred)),
        atomic_candidate_count=atomic_count,
        flow_candidate_count=flow_count,
    )


__all__ = [
    "SkillCandidateDiscoveryReport",
    "crystallize_repeated_atomic_skill_candidates",
]