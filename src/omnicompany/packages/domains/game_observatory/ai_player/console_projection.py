"""Read-only canonical projection for the local AI-player console."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from statistics import median
from typing import Any

from ..models import ArtifactRef, EvidenceStep
from .contracts import EvidenceReferenceV1
from .sanguo_daily_continuity import SANGUO_GAME_ID, SanguoDailyContinuityLedger
from .iteration_monitor import DEFAULT_ITERATION_POLICY
from .known_route_program import KnownRouteProgram
from .operation_memory import OperationMemory
from .physical_readiness import PhysicalReadinessEvaluator
from .remediation import resolve_iteration_remediation_gate
from .semantic_surface_profiles import resolve_canonical_state_surface_profile
from .session_control import AIPlayerSessionControl
from .store import AIPlayerStore
from .surface_anchor_action import (
    SurfaceAnchorActionError,
    build_task_relevant_consensus_locator_hints,
    build_task_relevant_surface_anchor_actions,
    materialize_normalized_surface_rect,
)
from .task_board import TaskBoard
from .task_focus import OPEN_DEVICE_ACTION_GATES, resolve_authoritative_task_focus


_OPEN_TASK_STATUSES = {"queued", "active", "cooldown", "blocked"}
_VISUAL_ARTIFACT_KINDS = {"screenshot", "video_frame", "annotated_plate"}
_CONSOLE_STATE_LIMIT = 48
_CONSOLE_TRANSITION_LIMIT = 72
_CONSOLE_SKILL_LIMIT = 80
_CONSOLE_SKILL_RUN_LIMIT = 120
_CONSOLE_SKILL_VALIDATION_LIMIT = 60
_MODEL_CANONICAL_SOURCES: dict[str, tuple[str, tuple[str, ...]]] = {
    "EvidenceRun": ("evidence_runs", ("id",)),
    "EvidenceStep": ("evidence_steps", ("id",)),
    "EnvironmentScopeV1": ("ai_player_environments", ("id",)),
    "MemoryRecordV1": ("ai_player_memory_records", ("environment_id", "id")),
    "SemanticStateV1": (
        "ai_player_semantic_states",
        ("environment_id", "id", "version"),
    ),
    "StateObservationV1": (
        "ai_player_state_observations",
        ("environment_id", "id"),
    ),
    "StateAssignmentV1": (
        "ai_player_state_assignments",
        ("environment_id", "id"),
    ),
    "TransitionEdgeV1": (
        "ai_player_transition_edges",
        ("environment_id", "id", "version"),
    ),
    "FrontierTaskV1": ("ai_player_frontier_tasks", ("environment_id", "id")),
    "SkillVersionV1": ("ai_player_skill_versions", ("environment_id", "id")),
    "SkillRunV1": ("ai_player_skill_runs", ("environment_id", "id")),
    "SkillValidationV1": (
        "ai_player_skill_validations",
        ("environment_id", "id"),
    ),
    "GameplayCandidateV1": (
        "ai_player_gameplay_candidates",
        ("environment_id", "id", "version"),
    ),
    "AccountActionPolicyV1": (
        "ai_player_account_policies",
        ("environment_id", "id", "version"),
    ),
    "SpeechIntentV1": (
        "ai_player_speech_intents",
        ("environment_id", "id", "version"),
    ),
    "SpeechEventV1": ("ai_player_speech_events", ("environment_id", "id")),
    "SessionCapsuleV1": ("ai_player_session_capsules", ("environment_id", "id")),
    "GuideKnowledgeV1": (
        "ai_player_guide_knowledge",
        ("environment_id", "id", "version"),
    ),
    "ActionQualitySampleV1": (
        "ai_player_action_quality_samples",
        ("environment_id", "id"),
    ),
    "PlayerIterationAssessmentV1": (
        "ai_player_iteration_assessments",
        ("environment_id", "id"),
    ),
    "AccountMetricDeltaDerivationV1": (
        "ai_player_account_metric_derivations",
        ("environment_id", "id"),
    ),
    "PlayerSoftSignalReviewV1": (
        "ai_player_soft_signal_reviews",
        ("environment_id", "id"),
    ),
    "PlayerSoftSignalReviewRequestV1": (
        "ai_player_soft_signal_review_requests",
        ("environment_id", "id"),
    ),
    "Tier1RemediationVerificationV1": (
        "ai_player_tier1_remediation_verifications",
        ("environment_id", "id"),
    ),
    "AIPlayerSessionV1": ("ai_player_sessions", ("id",)),
}


def _console_read_cache(player: AIPlayerStore) -> dict[tuple[str, str], Any]:
    cache = getattr(player, "_console_projection_read_cache", None)
    if cache is None:
        cache = {}
        setattr(player, "_console_projection_read_cache", cache)
    return cache


def _cached_artifact(player: AIPlayerStore, artifact_id: str) -> ArtifactRef | None:
    cache = _console_read_cache(player)
    key = ("artifact", artifact_id)
    if key not in cache:
        cache[key] = player.observatory_store.get_artifact(artifact_id)
    return cache[key]


def _cached_evidence_step(player: AIPlayerStore, step_id: str) -> EvidenceStep | None:
    cache = _console_read_cache(player)
    key = ("evidence_step", step_id)
    if key not in cache:
        cache[key] = player.observatory_store.get_evidence_step(step_id)
    return cache[key]


def _cached_evidence_steps(player: AIPlayerStore, run_id: str) -> tuple[EvidenceStep, ...]:
    cache = _console_read_cache(player)
    key = ("evidence_steps", run_id)
    if key not in cache:
        cache[key] = tuple(player.observatory_store.list_evidence_steps(run_id))
    return cache[key]


def _cached_evidence_run(player: AIPlayerStore, run_id: str) -> Any:
    cache = _console_read_cache(player)
    key = ("evidence_run", run_id)
    if key not in cache:
        cache[key] = player.observatory_store.get_evidence_run(run_id)
    return cache[key]


def _timestamp_key(value: str) -> float:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return float("-inf")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return float("-inf")
    return parsed.timestamp()


def _model(player: AIPlayerStore, value: Any) -> dict[str, Any]:
    payload = value.model_dump(mode="json", by_alias=True)
    source = _MODEL_CANONICAL_SOURCES.get(type(value).__name__)
    if source is None:
        return payload
    source_table, key_fields = source
    return player.project_canonical_record_payload(
        payload,
        source_table=source_table,
        record_key={field: payload[field] for field in key_fields},
    )


def project_semantic_state(player: AIPlayerStore, state: Any) -> dict[str, Any]:
    """Apply canonical text corrections to one resolved semantic state."""

    return _model(player, state)


def _artifact_view(artifact: ArtifactRef | None) -> dict[str, Any] | None:
    if artifact is None:
        return None
    visual = artifact.kind in _VISUAL_ARTIFACT_KINDS or bool(
        artifact.media_type and artifact.media_type.startswith("image/")
    )
    return {
        "id": artifact.id,
        "kind": artifact.kind,
        "media_type": artifact.media_type,
        "sha256": artifact.sha256,
        "is_visual": visual,
        "href": (
            f"/api/game-observatory/internal/artifacts/{artifact.id}/thumbnail"
            "?max_width=360&max_height=240"
            if visual
            else None
        ),
        "original_href": (
            f"/api/game-observatory/internal/artifacts/{artifact.id}" if visual else None
        ),
    }


def _evidence_references(entities: Iterable[Any]) -> list[EvidenceReferenceV1]:
    references: list[EvidenceReferenceV1] = []
    for entity in entities:
        references.extend(getattr(entity, "evidence_refs", []))
        references.extend(getattr(entity, "last_evidence_refs", []))
        pending = getattr(entity, "pending_action", None)
        if pending is not None:
            references.extend(pending.evidence_refs)
            references.extend(pending.after_evidence_refs)
    return references


def _entity_visuals(
    player: AIPlayerStore,
    references: list[EvidenceReferenceV1],
    *,
    limit: int = 2,
) -> list[dict[str, Any]]:
    """Resolve a compact image set while preserving canonical artifact identity."""

    artifact_ids: list[str] = []
    for reference in references:
        artifact_ids.extend(reference.artifact_ids)
        for step_id in reference.evidence_step_ids:
            step = _cached_evidence_step(player, step_id)
            if step is not None:
                artifact_ids.extend(
                    item for item in (step.after_frame_id, step.before_frame_id) if item
                )
        for run_id in reference.evidence_run_ids:
            steps = _cached_evidence_steps(player, run_id)
            if steps:
                artifact_ids.extend(
                    item
                    for item in (steps[-1].after_frame_id, steps[-1].before_frame_id)
                    if item
                )
    visuals: list[dict[str, Any]] = []
    seen: set[str] = set()
    for artifact_id in artifact_ids:
        if artifact_id in seen:
            continue
        seen.add(artifact_id)
        view = _artifact_view(_cached_artifact(player, artifact_id))
        if view and view["is_visual"]:
            visuals.append(view)
        if len(visuals) >= limit:
            break
    return visuals


def _counts(values: Iterable[Any], field: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        key = str(getattr(value, field))
        result[key] = result.get(key, 0) + 1
    return dict(sorted(result.items()))


def _path_reuse_health(
    skills: Iterable[Any],
    skill_runs: Iterable[Any],
    route_arcs: Iterable[Any],
    transition_edges: Iterable[Any] = (),
    *,
    detail_limit: int | None = 40,
) -> dict[str, Any]:
    """Summarize whether learned interaction paths are actually getting reused.

    Wall-clock latency includes device capture and settle time, so a small
    fluctuation is reported as stable. Model-token use stays strict: a fixed
    replay that consumes any model token is always an attention item.
    """

    skill_by_id = {item.id: item for item in skills}
    runs = list(skill_runs)
    arcs = list(route_arcs)
    edges = list(transition_edges)
    successful = [
        item
        for item in runs
        if item.outcome == "success"
        and item.objective_success
        and item.validation_passed
        and not item.false_success
        and item.safety_violation_count == 0
    ]
    successful_by_skill: defaultdict[str, list[Any]] = defaultdict(list)
    all_by_skill: defaultdict[str, list[Any]] = defaultdict(list)
    for item in runs:
        all_by_skill[item.skill_version_id].append(item)
    for item in successful:
        successful_by_skill[item.skill_version_id].append(item)

    repeated_rows: list[dict[str, Any]] = []
    faster_count = 0
    stable_count = 0
    slower_count = 0
    for skill_version_id, samples in successful_by_skill.items():
        if len(samples) < 2:
            continue
        first_latency = float(samples[0].decision_latency_ms)
        latest_latency = float(samples[-1].decision_latency_ms)
        change_rate = (
            (latest_latency - first_latency) / first_latency if first_latency > 0 else 0.0
        )
        if change_rate < -0.1:
            trend = "faster"
            faster_count += 1
        elif change_rate > 0.1:
            trend = "slower"
            slower_count += 1
        else:
            trend = "stable"
            stable_count += 1
        skill = skill_by_id.get(skill_version_id)
        repeated_rows.append(
            {
                "skill_version_id": skill_version_id,
                "title": getattr(skill, "title", skill_version_id),
                "replay_count": len(samples),
                "first_latency_ms": round(first_latency, 1),
                "latest_latency_ms": round(latest_latency, 1),
                "change_rate": round(change_rate, 4),
                "trend": trend,
            }
        )

    first_latencies = [item["first_latency_ms"] for item in repeated_rows]
    latest_latencies = [item["latest_latency_ms"] for item in repeated_rows]
    measured_baselines = [
        item
        for item in successful
        if item.baseline_decision_latency_ms > 0 and item.decision_latency_ms > 0
    ]
    baseline_speedups = [
        item.baseline_decision_latency_ms / item.decision_latency_ms
        for item in measured_baselines
    ]
    latest_decisive_failures = [
        samples[-1]
        for samples in all_by_skill.values()
        if samples[-1].outcome != "success"
        or not samples[-1].objective_success
        or not samples[-1].validation_passed
        or samples[-1].false_success
        or samples[-1].safety_violation_count > 0
    ]
    model_assisted_replay_count = sum(item.model_input_tokens > 0 for item in successful)
    production_skill_ids = {item.skill_version_id for item in arcs}
    def operation_key(skill: Any) -> tuple[Any, ...] | None:
        if (
            getattr(skill, "status", None) not in {"candidate", "validated", "preferred"}
            or getattr(skill, "skill_layer", None) != "atomic"
            or getattr(skill, "executor_kind", None) != "normalized_actions"
        ):
            return None
        action_steps = [step for step in getattr(skill, "steps", ()) if step.kind == "action"]
        assert_steps = [
            step
            for step in getattr(skill, "steps", ())
            if step.kind == "assert" and getattr(step, "expected_state_id", None)
        ]
        if len(action_steps) != 1 or len(assert_steps) != 1:
            return None
        action = getattr(action_steps[0], "action", None)
        locator = next(
            (
                item
                for item in getattr(skill, "locators", ())
                if item.id == getattr(action_steps[0], "locator_id", None)
            ),
            None,
        )
        bounds = getattr(locator, "reference_bounds", None)
        return (
            tuple(sorted(skill.applicability_scope.required_state_ids)),
            assert_steps[0].expected_state_id,
            tuple(
                getattr(action, field, None)
                for field in ("type", "x", "y", "x2", "y2", "keycode", "button")
            ),
            (
                getattr(bounds, "x", None),
                getattr(bounds, "y", None),
                getattr(bounds, "width", None),
                getattr(bounds, "height", None),
            ),
            getattr(locator, "strategy", None),
            getattr(locator, "mobility", None),
        )

    operation_groups: defaultdict[tuple[Any, ...], list[Any]] = defaultdict(list)
    for skill in skill_by_id.values():
        if (key := operation_key(skill)) is not None:
            operation_groups[key].append(skill)

    operation_rows: list[dict[str, Any]] = []
    for grouped_skills in operation_groups.values():
        grouped_runs = [
            run for skill in grouped_skills for run in all_by_skill.get(skill.id, ())
        ]
        grouped_successful = [
            run for skill in grouped_skills for run in successful_by_skill.get(skill.id, ())
        ]
        representative = max(
            grouped_skills,
            key=lambda item: (
                len(successful_by_skill.get(item.id, ())),
                len(all_by_skill.get(item.id, ())),
                getattr(item, "created_at", ""),
                item.id,
            ),
        )
        operation_rows.append(
            {
                "representative": representative,
                "skill_version_ids": sorted(item.id for item in grouped_skills),
                "run_count": len(grouped_runs),
                "successful_run_count": len(grouped_successful),
                "latest_latency_ms": (
                    round(float(grouped_successful[-1].decision_latency_ms), 1)
                    if grouped_successful
                    else None
                ),
            }
        )
    pending_second_use = [item for item in operation_rows if item["run_count"] == 0]
    failed_or_attempted_candidates = [
        item
        for item in operation_rows
        if item["run_count"] > 0 and item["successful_run_count"] == 0
    ]
    single_success_operations = [
        item for item in operation_rows if item["successful_run_count"] == 1
    ]
    warm_operations = [
        item for item in operation_rows if item["successful_run_count"] >= 2
    ]
    second_use_denominator = len(operation_rows)
    second_use_completed = sum(item["successful_run_count"] > 0 for item in operation_rows)
    third_use_completed = len(warm_operations)
    recorded_action_edges = [
        item
        for item in edges
        if (
            getattr(getattr(item, "action", None), "type", None)
            or getattr(item, "action_type", None)
        )
        != "wait"
        and getattr(item, "outcome", None) not in {"failed", "forbidden"}
    ]
    active_source_transition_ids = {
        transition_id
        for grouped_skills in operation_groups.values()
        for skill in grouped_skills
        for transition_id in getattr(skill, "source_transition_ids", ())
    }
    if not arcs:
        status = "learning"
    elif (
        model_assisted_replay_count
        or latest_decisive_failures
        or slower_count
        or pending_second_use
        or failed_or_attempted_candidates
        or single_success_operations
    ):
        status = "attention"
    else:
        status = "healthy"
    repeated_rows.sort(
        key=lambda item: (
            item["trend"] != "slower",
            -item["replay_count"],
            item["skill_version_id"],
        )
    )
    return {
        "schema": "game-observatory.ai-player.path-reuse-health.v1",
        "status": status,
        "production_route_arc_count": len(arcs),
        "production_skill_version_count": len(production_skill_ids),
        "known_source_state_count": len({item.from_state_id for item in arcs}),
        "known_terminal_state_count": len({item.to_state_id for item in arcs}),
        "successful_replay_count": len(successful),
        "successful_skill_version_count": len(successful_by_skill),
        "zero_model_replay_count": sum(item.model_input_tokens == 0 for item in successful),
        "model_assisted_replay_count": model_assisted_replay_count,
        "repeated_skill_version_count": len(repeated_rows),
        "faster_latest_count": faster_count,
        "stable_latest_count": stable_count,
        "slower_latest_count": slower_count,
        "median_first_latency_ms": (
            round(float(median(first_latencies)), 1) if first_latencies else None
        ),
        "median_latest_latency_ms": (
            round(float(median(latest_latencies)), 1) if latest_latencies else None
        ),
        "measured_baseline_replay_count": len(measured_baselines),
        "median_baseline_latency_ms": (
            round(float(median(item.baseline_decision_latency_ms for item in measured_baselines)), 1)
            if measured_baselines
            else None
        ),
        "median_baseline_speedup_ratio": (
            round(float(median(baseline_speedups)), 2) if baseline_speedups else None
        ),
        "latest_decisive_failure_skill_count": len(latest_decisive_failures),
        "recorded_action_edge_count": len(recorded_action_edges),
        "deferred_action_edge_count": sum(
            item.outcome == "deferred" for item in recorded_action_edges
        ),
        "skill_source_action_edge_count": sum(
            item.id in active_source_transition_ids for item in recorded_action_edges
        ),
        "active_automation_operation_count": len(operation_rows),
        "pending_second_use_operation_count": len(pending_second_use),
        "failed_candidate_operation_count": len(failed_or_attempted_candidates),
        "single_success_operation_count": len(single_success_operations),
        "warm_reused_operation_count": len(warm_operations),
        "second_use_completion_rate": (
            round(second_use_completed / second_use_denominator, 4)
            if second_use_denominator
            else None
        ),
        "third_use_completion_rate": (
            round(third_use_completed / second_use_denominator, 4)
            if second_use_denominator
            else None
        ),
        "pending_second_use_operations": [
            {
                "representative_skill_version_id": item["representative"].id,
                "title": item["representative"].title,
                "equivalent_skill_version_ids": item["skill_version_ids"],
                "required_state_ids": sorted(
                    item["representative"].applicability_scope.required_state_ids
                ),
                "expected_terminal_state_id": next(
                    step.expected_state_id
                    for step in item["representative"].steps
                    if step.kind == "assert" and step.expected_state_id
                ),
            }
            for item in pending_second_use[:detail_limit]
        ],
        "single_success_operations": [
            {
                "representative_skill_version_id": item["representative"].id,
                "title": item["representative"].title,
                "equivalent_skill_version_ids": item["skill_version_ids"],
                "required_state_ids": sorted(
                    item["representative"].applicability_scope.required_state_ids
                ),
                "expected_terminal_state_id": next(
                    step.expected_state_id
                    for step in item["representative"].steps
                    if step.kind == "assert" and step.expected_state_id
                ),
                "latest_latency_ms": item["latest_latency_ms"],
            }
            for item in single_success_operations[:detail_limit]
        ],
        "warm_reused_operations": [
            {
                "representative_skill_version_id": item["representative"].id,
                "title": item["representative"].title,
                "equivalent_skill_version_ids": item["skill_version_ids"],
                "required_state_ids": sorted(
                    item["representative"].applicability_scope.required_state_ids
                ),
                "expected_terminal_state_id": next(
                    step.expected_state_id
                    for step in item["representative"].steps
                    if step.kind == "assert" and step.expected_state_id
                ),
                "successful_run_count": item["successful_run_count"],
                "latest_latency_ms": item["latest_latency_ms"],
            }
            for item in warm_operations[:detail_limit]
        ],
        "repeated_paths": repeated_rows[:detail_limit],
    }


def _guide_view(player: AIPlayerStore, guide: Any) -> dict[str, Any]:
    """Expose provenance-rich guide candidates without promoting them to live truth."""

    projected = _model(player, guide)
    source_ids = sorted(
        {
            source_id
            for reference in guide.evidence_refs
            for source_id in reference.source_ids
        }
    )
    discovery_only = guide.status == "unverified" or bool(
        guide.missing_applicability_reason
    )
    return {
        "id": guide.id,
        "version": guide.version,
        "title": projected["summary"],
        "summary": projected["summary"],
        "status": guide.status,
        "usage_mode": "discovery_only" if discovery_only else "reference_only",
        "author": guide.author,
        "platform": guide.platform,
        "published_at": guide.published_at,
        "updated_at": guide.updated_at,
        "retrieved_at": guide.retrieved_at,
        "url": str(guide.url),
        "source_ids": source_ids,
        "locators": list(guide.locators),
        "triggering_task_ids": list(guide.triggering_task_ids),
        "missing_applicability_reason": projected["missing_applicability_reason"],
        "season": guide.season,
        "server_stage": guide.server_stage,
    }


def _environment_lineage_label(environment: Any, *, is_leaf: bool) -> str:
    if environment.server_scope_id or environment.world_scope_id:
        return "当前服务器 / 世界" if is_leaf else "服务器 / 世界环境"
    if "prelogin" in environment.account_scope_id.casefold():
        return "登录前环境"
    return "账号环境"


def _state_display_title(
    player: AIPlayerStore,
    state: Any,
    *,
    projected_title: str,
) -> str:
    """Give machine candidates an evidence-led label without claiming semantics."""

    if state.status != "candidate" or not state.title.startswith("待审状态 "):
        return projected_title
    for reference in state.evidence_refs:
        note = reference.note.casefold()
        role = "操作前" if "before" in note else "操作后" if "after" in note else "实录画面"
        for step_id in reference.evidence_step_ids:
            step = _cached_evidence_step(player, step_id)
            if step is not None and step.target_name and step.target_name.strip():
                return f"待裁决：{step.target_name.strip()}（{role}）"
    return "待裁决界面"


def _state_view(player: AIPlayerStore, state: Any) -> dict[str, Any]:
    projected = _model(player, state)
    return {
        **projected,
        "title": _state_display_title(
            player,
            state,
            projected_title=projected["title"],
        ),
        "visuals": _entity_visuals(player, state.evidence_refs),
    }


def _step_view(
    player: AIPlayerStore,
    step: EvidenceStep,
    corrections_by_step: dict[str, list[dict[str, str]]],
) -> dict[str, Any]:
    payload = {
        "id": step.id,
        "run_id": step.evidence_run_id,
        "step_index": step.step_index,
        "status": step.status,
        "started_at": step.started_at,
        "ended_at": step.ended_at,
        "target_name": step.target_name,
        "action": step.action.model_dump(mode="json"),
        "source_point": step.source_point.model_dump(mode="json") if step.source_point else None,
        "target_bounds": (
            step.target_bounds.model_dump(mode="json") if step.target_bounds else None
        ),
        "before": _artifact_view(_cached_artifact(player, step.before_frame_id))
        if step.before_frame_id
        else None,
        "after": _artifact_view(_cached_artifact(player, step.after_frame_id))
        if step.after_frame_id
        else None,
        "quality_issues": step.quality_issues,
        "error": step.error,
        "corrections": corrections_by_step.get(step.id, []),
    }
    return player.project_canonical_record_payload(
        payload,
        source_table="evidence_steps",
        record_key={"id": step.id},
    )


def _latest_evidence(
    player: AIPlayerStore,
    references: list[EvidenceReferenceV1],
    *,
    session_bound_run_ids: Iterable[str] = (),
    limit: int = 12,
    corrections_by_step: dict[str, list[dict[str, str]]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    bounded_references = references[-(limit * 16) :]

    def recent_ids(field: str, *, multiplier: int = 6) -> list[str]:
        ordered: dict[str, None] = {}
        for reference in bounded_references:
            for item in getattr(reference, field):
                ordered.setdefault(item, None)
        return list(ordered)[-(limit * multiplier) :]

    run_ids = recent_ids("evidence_run_ids")
    run_ids.extend(item for item in session_bound_run_ids if item not in run_ids)
    run_ids = run_ids[-(limit * 6) :]
    step_ids = recent_ids("evidence_step_ids")
    artifact_ids = recent_ids("artifact_ids", multiplier=4)
    steps: dict[str, EvidenceStep] = {}
    for step_id in step_ids:
        step = _cached_evidence_step(player, step_id)
        if step is not None:
            steps[step.id] = step
            if step.evidence_run_id not in run_ids:
                run_ids.append(step.evidence_run_id)
    runs = []
    for run_id in run_ids:
        run = _cached_evidence_run(player, run_id)
        if run is None:
            continue
        runs.append(run)
        for step in _cached_evidence_steps(player, run.id)[-2:]:
            steps[step.id] = step
    ordered_steps = sorted(
        steps.values(),
        key=lambda item: (item.ended_at or item.started_at, item.id),
        reverse=True,
    )[:limit]
    ordered_runs = sorted(
        runs,
        key=lambda item: (item.ended_at or item.started_at, item.id),
        reverse=True,
    )[:limit]
    direct_artifacts = [
        view
        for artifact_id in artifact_ids
        if (view := _artifact_view(_cached_artifact(player, artifact_id))) is not None
    ][:limit]
    return {
        "steps": [_step_view(player, step, corrections_by_step or {}) for step in ordered_steps],
        "runs": [_model(player, run) for run in ordered_runs],
        "artifacts": direct_artifacts,
    }


def _session_bound_evidence_run_ids(
    player: AIPlayerStore,
    *,
    environment_id: str,
    durable_sessions: Iterable[Any],
) -> list[str]:
    """Find recent runs explicitly bound to a durable session in this exact leaf."""

    session_ids = {session.id for session in durable_sessions}
    if not session_ids:
        return []
    run_ids: list[str] = []
    for run in player.observatory_store.list_evidence_runs(limit=240):
        bound_environment_id = str(run.environment.get("environment_id") or "").strip()
        bound_session_id = str(run.environment.get("ai_player_session_id") or "").strip()
        if bound_environment_id != environment_id or bound_session_id not in session_ids:
            continue
        run_ids.append(run.id)
    return run_ids


def resolve_current_semantic_state(
    player: AIPlayerStore,
    *,
    environment_id: str,
) -> tuple[Any | None, str | None]:
    """Resolve the current route origin without materializing the full console."""

    capsules = player.list_session_capsules(environment_id, limit=1)
    latest_capsule = capsules[0] if capsules else None
    latest_assignment = player.get_latest_active_state_assignment(environment_id)
    latest_assignment_observed_at = None
    if latest_assignment is not None:
        observation = player.get_state_observation(
            environment_id,
            latest_assignment.observation_id,
        )
        latest_assignment_observed_at = (
            observation.captured_at if observation is not None else latest_assignment.created_at
        )
    assignment_is_newer_than_capsule = (
        latest_assignment is not None
        and (
            latest_capsule is None
            or _timestamp_key(latest_assignment_observed_at)
            > _timestamp_key(latest_capsule.created_at)
        )
    )
    if (
        latest_capsule is not None
        and latest_capsule.last_confirmed_state_id
        and not assignment_is_newer_than_capsule
    ):
        state = player.get_semantic_state(
            environment_id,
            latest_capsule.last_confirmed_state_id,
        )
        if state is not None:
            return state, "confirmed_capsule"
    if latest_assignment is not None:
        state = player.get_semantic_state(environment_id, latest_assignment.state_id)
        if state is not None:
            return state, "latest_active_assignment"
    return None, None


def _surface_operation_projection(
    profile: Any | None,
    *,
    surface_anchors: list[Any] | tuple[Any, ...] = (),
    task_relevant_anchor_actions: tuple[dict[str, object], ...] = (),
    task_relevant_locator_hints: tuple[dict[str, object], ...] = (),
    viewport_width: int = 1000,
    viewport_height: int = 1000,
    outgoing_arcs: list[Any],
    detail_limit: int,
    execution_focus_present: bool,
    task_gate: str,
    device_action_gate: str,
) -> dict[str, Any]:
    """Compress recognition, safety and known operations into one read-only card."""

    missing_semantics = (
        [
            field_name
            for field_name in (
                "interaction_roles",
                "safe_exit_tokens",
                "risk_boundary_tokens",
            )
            if not getattr(profile, field_name, ())
        ]
        if profile is not None
        else [
            "page_identity_tokens",
            "interaction_roles",
            "safe_exit_tokens",
            "risk_boundary_tokens",
        ]
    )
    semantic_operation_ready = profile is not None and not missing_semantics
    deterministic_operation_ready = bool(semantic_operation_ready and outgoing_arcs)
    guarded_anchor_action_ready = bool(task_relevant_anchor_actions)
    supplemental_locator_hint_ready = bool(task_relevant_locator_hints)
    current_locator_action_ready = any(
        item.get("tap_element_ready") is True for item in task_relevant_locator_hints
    )
    meaningful_action_ready = bool(
        (
            deterministic_operation_ready
            or guarded_anchor_action_ready
            or current_locator_action_ready
        )
        and execution_focus_present
        and task_gate == "eligible"
        and device_action_gate in {"open", "not_applicable"}
    )
    if profile is None:
        status = "unavailable"
    elif missing_semantics and outgoing_arcs:
        status = "route_available_profile_incomplete"
    elif missing_semantics:
        status = "recognition_only"
    elif task_relevant_anchor_actions and current_locator_action_ready:
        status = "task_relevant_anchor_and_locator_action_ready"
    elif task_relevant_anchor_actions and task_relevant_locator_hints:
        status = "task_relevant_anchor_and_locator_hint_ready"
    elif task_relevant_anchor_actions:
        status = "task_relevant_anchor_action_ready"
    elif current_locator_action_ready:
        status = "task_relevant_locator_action_ready"
    elif task_relevant_locator_hints:
        status = "task_relevant_locator_hint_ready"
    elif not outgoing_arcs:
        status = "semantic_profile_ready_no_production_route"
    else:
        status = "deterministic_reuse_ready"
    anchored_roles = list(dict.fromkeys(anchor.role for anchor in surface_anchors))
    interaction_roles = list(profile.interaction_roles) if profile is not None else []
    unanchored_roles = [role for role in interaction_roles if role not in anchored_roles]

    recognition_candidates = [
        ("reviewed_anchor_action", item) for item in task_relevant_anchor_actions
    ] + [
        ("cross_state_locator_consensus", item) for item in task_relevant_locator_hints
    ]
    alignment_rank = {"aligned": 0, "unspecified": 1, "deferred_phase": 2}

    def recognition_rank(candidate: tuple[str, dict[str, object]]) -> tuple[object, ...]:
        kind, item = candidate
        relevance = item.get("task_relevance") or {}
        return (
            alignment_rank.get(str(relevance.get("intent_alignment")), 1),
            int(relevance.get("first_match_offset", 1_000_000)),
            -int(relevance.get("match_score", 0)),
            0 if kind == "reviewed_anchor_action" else 1,
            str(item.get("anchor_id") or item.get("selector") or ""),
        )

    recommended_recognition = None
    if recognition_candidates:
        kind, item = min(recognition_candidates, key=recognition_rank)
        recommended_recognition = {"candidate_kind": kind, **item}

    def source_pixel_bounds(anchor: Any) -> dict[str, int]:
        bounds = anchor.normalized_bounds
        left = bounds.x * viewport_width // 1000
        top = bounds.y * viewport_height // 1000
        right = (bounds.x + bounds.width) * viewport_width + 999
        bottom = (bounds.y + bounds.height) * viewport_height + 999
        return {
            "x": left,
            "y": top,
            "width": max(1, right // 1000 - left),
            "height": max(1, bottom // 1000 - top),
        }

    return {
        "status": status,
        "profile": profile.model_dump(mode="json", by_alias=True) if profile else None,
        "missing_semantics": missing_semantics,
        "dynamic_fields_to_reread": (
            list(profile.dynamic_field_names) if profile is not None else []
        ),
        "semantic_operation_ready": semantic_operation_ready,
        "deterministic_operation_ready": deterministic_operation_ready,
        "guarded_anchor_action_ready": guarded_anchor_action_ready,
        "supplemental_locator_hint_ready": supplemental_locator_hint_ready,
        "current_locator_action_ready": current_locator_action_ready,
        "meaningful_action_ready": meaningful_action_ready,
        "task_relevant_anchor_action_count": len(task_relevant_anchor_actions),
        "task_relevant_operation_contract_count": sum(
            item.get("operation_contract") is not None
            for item in task_relevant_anchor_actions
        ),
        "task_relevant_anchor_actions": list(task_relevant_anchor_actions),
        "task_relevant_locator_hint_count": len(task_relevant_locator_hints),
        "task_relevant_locator_hints": list(task_relevant_locator_hints),
        "recommended_next_recognition": recommended_recognition,
        "guarded_anchor_candidate_count": len(surface_anchors),
        "anchored_interaction_roles": anchored_roles,
        "unanchored_interaction_roles": unanchored_roles,
        "anchor_candidates": [
            {
                "id": anchor.id,
                "role": anchor.role,
                "target_tokens": list(anchor.target_tokens),
                "action": anchor.action,
                "mobility": anchor.mobility,
                "normalized_bounds": anchor.normalized_bounds.model_dump(
                    mode="json", by_alias=True
                ),
                "source_pixel_bounds": materialize_normalized_surface_rect(
                    anchor.normalized_bounds,
                    viewport_width=viewport_width,
                    viewport_height=viewport_height,
                ).model_dump(mode="json"),
                "reference_artifact_id": anchor.reference_artifact_id,
                "requires_visual_relocalization": (
                    anchor.mobility == "dynamic_world_object"
                ),
                "production_route_ready": False,
            }
            for anchor in surface_anchors[:detail_limit]
        ],
        "known_operation_cards": [
            {
                "skill_version_id": arc.skill_version_id,
                "skill_title": arc.skill_title,
                "to_state_id": arc.to_state_id,
                "action_count": arc.action_count,
                "successful_run_count": arc.successful_run_count,
                "safety_level": arc.safety_level,
                "median_decision_latency_ms": arc.median_decision_latency_ms,
                "median_baseline_decision_latency_ms": (
                    arc.median_baseline_decision_latency_ms
                ),
                "median_baseline_model_input_tokens": (
                    arc.median_baseline_model_input_tokens
                ),
            }
            for arc in outgoing_arcs[:detail_limit]
        ],
    }


def build_path_reuse_health_projection(
    player: AIPlayerStore,
    *,
    environment_id: str,
    detail_limit: int = 10,
) -> dict[str, Any]:
    """Build the narrow canonical read used to steer meaningful path reuse.

    This deliberately avoids the full console projection. It reads only the
    operation, route, current-state, and frontier-task records needed to decide
    whether a second or third use would advance the current business goal.
    """

    with player.read_session():
        return _build_path_reuse_health_projection(
            player,
            environment_id=environment_id,
            detail_limit=detail_limit,
        )


def _build_path_reuse_health_projection(
    player: AIPlayerStore,
    *,
    environment_id: str,
    detail_limit: int,
) -> dict[str, Any]:
    """Compose one compact projection inside the caller's bounded read session."""

    if not 0 <= detail_limit <= 40:
        raise ValueError("detail_limit must be between 0 and 40")
    environment = player.get_environment(environment_id)
    if environment is None:
        raise KeyError(f"unknown AI-player environment: {environment_id}")

    skills = player.list_path_reuse_skill_versions(environment_id)
    runs = player.list_skill_runs(environment_id)
    route_arcs = list(
        KnownRouteProgram(player).arcs(
            environment_id,
            require_successful_run=True,
            preloaded_skills=skills,
        )
    )
    compact_transition_reader = getattr(
        player,
        "list_transition_edge_projection_rows",
        None,
    )
    transitions = (
        compact_transition_reader(environment_id)
        if callable(compact_transition_reader)
        else player.list_transition_edges(environment_id)
    )
    health = _path_reuse_health(
        skills,
        runs,
        route_arcs,
        transitions,
        detail_limit=None,
    )
    if hasattr(player, "_connection"):
        health["operation_memory"] = OperationMemory(player).learning_health(
            environment_id,
            detail_limit=detail_limit,
        )
        health["reuse_measurement_authority"] = (
            "operation_memory.contextual_executions_with_legacy_skill_run_projection"
        )
    else:
        health["reuse_measurement_authority"] = "legacy_skill_run_projection"

    current_state, current_state_basis = resolve_current_semantic_state(
        player,
        environment_id=environment_id,
    )
    tasks = player.list_tasks(environment_id)
    tasks_by_id = {item.id: item for item in tasks}
    authoritative_focus = resolve_authoritative_task_focus(
        player,
        environment_id=environment_id,
    )
    task_board = TaskBoard()
    score_decision = task_board.select(tasks)
    task_decision = task_board.select(
        tasks,
        preferred_task_ids=authoritative_focus.preferred_task_ids,
        deferred_task_ids=authoritative_focus.deferred_task_ids,
        prefer_active=True,
        restrict_to_preferred=authoritative_focus.restrict_to_preferred,
    )
    selected_task = tasks_by_id.get(task_decision.selected_task_id)
    selected_disposition = (
        task_decision.disposition(task_decision.selected_task_id)
        if task_decision.selected_task_id
        else None
    )
    score_selected_task = tasks_by_id.get(score_decision.selected_task_id)
    score_selected_disposition = (
        score_decision.disposition(score_decision.selected_task_id)
        if score_decision.selected_task_id
        else None
    )
    score_selected_focus_disposition = (
        task_decision.disposition(score_decision.selected_task_id)
        if score_decision.selected_task_id
        else None
    )
    active_task = max(
        (item for item in tasks if item.status == "active"),
        key=lambda item: (item.created_at, item.id),
        default=None,
    )
    active_disposition = (
        task_decision.disposition(active_task.id) if active_task is not None else None
    )
    continuity_schedule = authoritative_focus.continuity_schedule
    daily_generated_task_ids = list(authoritative_focus.daily_generated_task_ids)
    daily_generated_task_basis = authoritative_focus.daily_generated_task_basis
    daily_generated_task_gate = authoritative_focus.daily_generated_task_gate
    daily_generated_task_gate_reason = (
        authoritative_focus.daily_generated_task_gate_reason
    )
    device_action_gate = authoritative_focus.device_action_gate
    device_action_gate_reason = authoritative_focus.device_action_gate_reason

    def task_view(task: Any | None, disposition: Any | None) -> dict[str, Any] | None:
        if task is None:
            return None
        return {
            "id": task.id,
            "title": task.title,
            "source": task.source,
            "status": task.status,
            "disposition": (
                disposition.disposition if disposition is not None else None
            ),
            "disposition_reason": disposition.reason if disposition is not None else None,
            "score": (
                disposition.score.model_dump(mode="json")
                if disposition is not None and disposition.score is not None
                else None
            ),
        }

    execution_focus_task = active_task or selected_task
    execution_focus_disposition = (
        active_disposition
        if active_task is not None
        else selected_disposition
    )
    execution_focus_basis = (
        "active_task"
        if active_task is not None
        else authoritative_focus.basis
        if selected_task is not None
        else "idle"
    )
    task_level_gate = (
        execution_focus_disposition.disposition
        if execution_focus_disposition is not None
        else "eligible"
        if execution_focus_task is not None
        else "idle"
    )
    execution_focus_gate = (
        device_action_gate
        if device_action_gate
        not in OPEN_DEVICE_ACTION_GATES
        else task_level_gate
    )

    all_pending = health["pending_second_use_operations"]
    all_single = health["single_success_operations"]
    all_warm = health["warm_reused_operations"]
    pending = all_pending[:detail_limit]
    single = all_single[:detail_limit]
    warm = all_warm[:detail_limit]
    repeated = health["repeated_paths"][:detail_limit]
    current_state_id = current_state.id if current_state is not None else None
    applicable_reuse = [
        {"reuse_stage": reuse_stage, **item}
        for reuse_stage, rows in (
            ("warm_reused", all_warm),
            ("single_success", all_single),
            ("pending_second_use", all_pending),
        )
        for item in rows
        if current_state_id is not None and current_state_id in item["required_state_ids"]
    ][:detail_limit]
    current_outgoing_arcs = [
        item for item in route_arcs if item.from_state_id == current_state_id
    ]
    current_incoming_arc_count = sum(item.to_state_id == current_state_id for item in route_arcs)
    current_outgoing_transitions = [
        item
        for item in transitions
        if item.from_state_id == current_state_id
        and item.outcome not in {"failed", "forbidden"}
    ]
    current_incoming_transition_count = sum(
        item.to_state_id == current_state_id
        and item.outcome not in {"failed", "forbidden"}
        for item in transitions
    )
    current_outgoing_candidate_skill_count = sum(
        current_state_id in skill.applicability_scope.required_state_ids
        for skill in skills
        if getattr(skill, "status", None) in {"candidate", "validated", "preferred"}
    )
    current_terminal_candidate_skill_count = sum(
        any(
            step.kind == "assert" and step.expected_state_id == current_state_id
            for step in getattr(skill, "steps", ())
        )
        for skill in skills
        if getattr(skill, "status", None) in {"candidate", "validated", "preferred"}
    )
    applicable_stages = {item["reuse_stage"] for item in applicable_reuse}
    if current_state_id is None:
        current_state_route_status = "unknown_current_state"
    elif "warm_reused" in applicable_stages:
        current_state_route_status = "warm_reuse_available"
    elif current_outgoing_arcs:
        current_state_route_status = "production_route_available"
    elif "single_success" in applicable_stages:
        current_state_route_status = "second_use_available"
    elif "pending_second_use" in applicable_stages:
        current_state_route_status = "pending_replay_available"
    elif current_outgoing_transitions or current_outgoing_candidate_skill_count:
        current_state_route_status = "observed_outgoing_without_production_route"
    elif (
        current_incoming_arc_count
        or current_incoming_transition_count
        or current_terminal_candidate_skill_count
    ):
        current_state_route_status = "known_terminal_without_outgoing_operation"
    else:
        current_state_route_status = "unmapped_current_state"

    current_surface_profile = (
        resolve_canonical_state_surface_profile(
            player,
            environment_id,
            state_id=current_state_id,
        )
        if current_state_id is not None
        else None
    )
    current_source_step_id = next(
        (
            step_id
            for reference in reversed(getattr(current_state, "evidence_refs", ()))
            for step_id in reversed(reference.evidence_step_ids)
        ),
        None,
    )
    task_relevant_anchor_actions: tuple[dict[str, object], ...] = ()
    task_relevant_locator_hints: tuple[dict[str, object], ...] = ()
    surface_anchor_action_error = None
    if (
        current_state is not None
        and execution_focus_task is not None
        and current_source_step_id is not None
    ):
        try:
            task_relevant_anchor_actions = build_task_relevant_surface_anchor_actions(
                player,
                environment_id=environment_id,
                state_id=current_state.id,
                source_step_id=current_source_step_id,
                session_id="<session_id>",
                task_text=(
                    f"{execution_focus_task.title}\n{execution_focus_task.reason}"
                ),
                limit=3,
                skill_versions=skills,
                skill_runs=runs,
            )
            task_relevant_locator_hints = build_task_relevant_consensus_locator_hints(
                player,
                environment_id=environment_id,
                state_id=current_state.id,
                source_step_id=current_source_step_id,
                session_id="<session_id>",
                task_text=(
                    f"{execution_focus_task.title}\n{execution_focus_task.reason}"
                ),
                limit=3,
                skill_versions=skills,
            )
        except SurfaceAnchorActionError as exc:
            surface_anchor_action_error = str(exc)
    current_surface_operation = _surface_operation_projection(
        current_surface_profile,
        surface_anchors=(getattr(current_state, "surface_anchors", ()) if current_state else ()),
        task_relevant_anchor_actions=task_relevant_anchor_actions,
        task_relevant_locator_hints=task_relevant_locator_hints,
        viewport_width=getattr(environment, "viewport_width", 1000),
        viewport_height=getattr(environment, "viewport_height", 1000),
        outgoing_arcs=current_outgoing_arcs,
        detail_limit=detail_limit,
        execution_focus_present=execution_focus_task is not None,
        task_gate=task_level_gate,
        device_action_gate=device_action_gate,
    )

    return {
        **health,
        "environment_id": environment.id,
        "detail_limit": detail_limit,
        "current_state_id": current_state_id,
        "current_state_basis": current_state_basis,
        "current_state_route_status": current_state_route_status,
        "current_outgoing_route_count": len(current_outgoing_arcs),
        "current_incoming_route_count": current_incoming_arc_count,
        "current_outgoing_transition_count": len(current_outgoing_transitions),
        "current_incoming_transition_count": current_incoming_transition_count,
        "current_outgoing_candidate_skill_count": current_outgoing_candidate_skill_count,
        "current_terminal_candidate_skill_count": current_terminal_candidate_skill_count,
        "current_surface_operation": current_surface_operation,
        "surface_anchor_action_error": surface_anchor_action_error,
        "current_outgoing_routes": [
            {
                "skill_version_id": item.skill_version_id,
                "to_state_id": item.to_state_id,
            }
            for item in current_outgoing_arcs[:detail_limit]
        ],
        "execution_focus_task": task_view(
            execution_focus_task,
            execution_focus_disposition,
        ),
        "execution_focus_basis": execution_focus_basis,
        "execution_focus_gate": execution_focus_gate,
        "execution_focus_task_gate": task_level_gate,
        "active_task": task_view(active_task, active_disposition),
        "selected_task": task_view(selected_task, selected_disposition),
        "task_selection_reason": task_decision.reason,
        "score_selected_task": task_view(
            score_selected_task,
            score_selected_disposition,
        ),
        "score_selected_task_role": (
            "execution_focus"
            if score_selected_task is not None
            and execution_focus_task is not None
            and score_selected_task.id == execution_focus_task.id
            else "unrestricted_score_diagnostic"
            if score_selected_task is not None
            else None
        ),
        "score_selected_task_execution_disposition": (
            score_selected_focus_disposition.disposition
            if score_selected_focus_disposition is not None
            else None
        ),
        "score_selection_reason": score_decision.reason,
        "daily_generated_task_ids": daily_generated_task_ids,
        "daily_generated_task_basis": daily_generated_task_basis,
        "daily_generated_task_gate": daily_generated_task_gate,
        "daily_generated_task_gate_reason": daily_generated_task_gate_reason,
        "daily_continuity_schedule": continuity_schedule,
        "device_action_gate": device_action_gate,
        "device_action_gate_reason": device_action_gate_reason,
        "eligible_task_count": sum(
            item.disposition == "eligible" for item in task_decision.dispositions
        ),
        "reuse_selection_rule": (
            "仅在推进执行焦点任务或到期账号职责时，顺带消化当前状态可用的单次成功或"
            "待二访操作。设备动作前必须通过自然日门，并先处理不可执行的 active 任务；"
            "不得只为提高复用计数而重复操作。"
        ),
        "current_applicable_reuse_candidates": applicable_reuse,
        "pending_second_use_operations": pending,
        "single_success_operations": single,
        "warm_reused_operations": warm,
        "repeated_paths": repeated,
    }


def build_ai_player_console_projection(
    player: AIPlayerStore,
    *,
    environment_id: str | None = None,
) -> dict[str, Any]:
    """Project one selected environment inside one bounded canonical read phase."""

    with player.read_session():
        return _build_ai_player_console_projection_in_read_session(
            player,
            environment_id=environment_id,
        )


def _build_ai_player_console_projection_in_read_session(
    player: AIPlayerStore,
    *,
    environment_id: str | None = None,
) -> dict[str, Any]:
    """Build the console payload while both canonical store connections are reused."""

    setattr(player, "_console_projection_read_cache", {})
    selections = player.list_current_environment_selections()
    if not selections:
        return player.project_current_text_payload({
            "schema": "game-observatory.ai-player.console.v1",
            "environment_options": [],
            "selection": None,
            "lineage": [],
            "identity": None,
            "current_state": None,
            "current_state_basis": None,
            "memories": [],
            "states": [],
            "state_map": {"nodes": [], "edges": [], "current_state_id": None},
            "tasks": [],
            "frontier": [],
            "coverage": {
                "observed_count": 0,
                "assigned_count": 0,
                "semantic_state_latest_entity_count": 0,
                "semantic_state_raw_version_row_count": 0,
                "visible_semantic_state_count": 0,
                "pending_adjudication_count": 0,
                "missing_transition_count": 0,
                "blocked_frontier_count": 0,
                "states_by_status": {},
                "transitions_by_outcome": {},
                "visible_transition_count": 0,
                "skill_version_count": 0,
                "visible_skill_version_count": 0,
                "skill_run_count": 0,
                "tasks_by_source": {},
                "tasks_by_status": {},
            },
            "skills": [],
            "skill_runs": [],
            "skill_validations": [],
            "path_reuse_health": _path_reuse_health([], [], []),
            "gameplay_candidates": [],
            "account_policy": None,
            "speech_intents": [],
            "speech_events": [],
            "capsules": [],
            "guide_knowledge": [],
            "budget": None,
            "blockers": [],
            "advisories": [],
            "evidence": {"steps": [], "runs": [], "artifacts": []},
            "daily_continuity": None,
            "physical_readiness_gate": None,
            "iteration_monitoring": {
                "policy_version": DEFAULT_ITERATION_POLICY.version,
                "session_id": None,
                "recent_samples": [],
                "latest_assessment": None,
                "assessment_history": [],
                "account_metric_derivations": [],
                "soft_signal_reviews": [],
                "development_soft_signal_reviews": [],
                "open_soft_signal_review_requests": [],
                "remediation_gate": None,
                "remediation_verifications": [],
            },
        })

    if environment_id is None:
        selection = max(
            selections,
            key=lambda item: (
                item.selected_environment.created_at,
                item.selected_environment_id,
            ),
        )
    else:
        selection = player.select_environment_lineage(environment_id)
    environment = selection.selected_environment
    physical_readiness_gate = PhysicalReadinessEvaluator().evaluate(environment)
    memories = player.list_memories(environment.id)
    latest_states = player.list_semantic_states(environment.id, latest_only=True)
    raw_state_versions = player.list_semantic_states(environment.id, latest_only=False)
    states = [
        state for state in latest_states if state.status in {"candidate", "accepted"}
    ]
    observations = player.list_state_observations(environment.id)
    assignments = player.list_state_assignments(environment.id)
    transitions = player.list_transition_edges(environment.id)
    tasks = player.list_tasks(environment.id)
    skills = player.list_skill_versions(environment.id)
    skill_runs = player.list_skill_runs(environment.id)
    skill_validations = player.list_skill_validations(environment.id)
    route_arcs = KnownRouteProgram(player).arcs(
        environment.id,
        require_successful_run=True,
    )
    path_reuse_health = _path_reuse_health(skills, skill_runs, route_arcs, transitions)
    gameplay_candidates = player.list_gameplay_candidates(environment.id)
    account_policy = player.get_account_policy(environment.id)
    speech_intents = player.list_speech_intents(environment.id)
    speech_events = player.list_speech_events(environment.id)
    capsules = player.list_session_capsules(environment.id)
    guides = player.list_guide_knowledge(environment.id)
    durable_sessions = AIPlayerSessionControl(player).list_sessions(environment.id, limit=20)
    monitoring_session = next(
        (
            session
            for session in durable_sessions
            if session.state in {"created", "running", "paused"}
        ),
        durable_sessions[0] if durable_sessions else None,
    )
    action_quality_samples = player.list_action_quality_samples(
        environment.id,
        session_id=monitoring_session.id if monitoring_session is not None else None,
        limit=30,
    )
    account_metric_derivations = player.list_account_metric_derivations(
        environment.id,
        limit=20,
    )
    soft_signal_reviews = player.list_soft_signal_reviews(
        environment.id,
        trust_scope="formal_external",
        limit=30,
    )
    development_soft_signal_reviews = player.list_soft_signal_reviews(
        environment.id,
        trust_scope="development_only",
        limit=30,
    )
    open_soft_signal_review_requests = player.list_open_soft_signal_review_requests(
        environment.id
    )
    monitoring_sample_ids = {item.id for item in action_quality_samples}
    iteration_assessments = [
        item
        for item in player.list_iteration_assessments(environment.id, limit=100)
        if set(item.sample_ids).issubset(monitoring_sample_ids)
    ][:20]
    gate_assessments = player.list_iteration_assessments(environment.id, limit=1)
    gate_assessment = gate_assessments[0] if gate_assessments else None
    remediation_verifications = player.list_tier1_remediation_verifications(
        environment.id,
        failed_assessment_id=gate_assessment.id if gate_assessment is not None else None,
    )
    remediation_gate = resolve_iteration_remediation_gate(
        player,
        environment_id=environment.id,
        assessment=gate_assessment,
    )
    session_bound_run_ids = _session_bound_evidence_run_ids(
        player,
        environment_id=environment.id,
        durable_sessions=durable_sessions,
    )
    latest_capsule = capsules[0] if capsules else None
    open_tasks = [task for task in tasks if task.status in _OPEN_TASK_STATUSES]
    current_state, current_state_basis = resolve_current_semantic_state(
        player,
        environment_id=environment.id,
    )
    visible_states = list(states[-_CONSOLE_STATE_LIMIT:])
    if (
        current_state is not None
        and current_state.status in {"candidate", "accepted"}
        and all(item.id != current_state.id for item in visible_states)
    ):
        visible_states = [current_state, *visible_states[-(_CONSOLE_STATE_LIMIT - 1) :]]
    visible_state_ids = {item.id for item in visible_states}
    visible_transitions = [
        item
        for item in transitions
        if item.from_state_id in visible_state_ids
        and (item.to_state_id is None or item.to_state_id in visible_state_ids)
    ][-_CONSOLE_TRANSITION_LIMIT:]
    recent_skill_runs = skill_runs[-_CONSOLE_SKILL_RUN_LIMIT:]
    recent_skill_validations = skill_validations[-_CONSOLE_SKILL_VALIDATION_LIMIT:]
    recent_run_skill_ids = {item.skill_version_id for item in recent_skill_runs}
    priority_skills = [
        item
        for item in skills
        if item.status in {"validated", "preferred", "degraded"}
        or item.id in recent_run_skill_ids
    ]
    visible_skills_by_id = {
        item.id: item for item in priority_skills[-_CONSOLE_SKILL_LIMIT:]
    }
    for item in reversed(skills):
        if len(visible_skills_by_id) >= _CONSOLE_SKILL_LIMIT:
            break
        visible_skills_by_id.setdefault(item.id, item)
    visible_skills = sorted(
        visible_skills_by_id.values(),
        key=lambda item: (item.created_at, item.id, item.version),
    )

    blockers: list[dict[str, Any]] = []
    for task in tasks:
        if task.status == "blocked":
            projected_task = _model(player, task)
            blockers.append(
                {
                    "kind": "task",
                    "id": task.id,
                    "title": projected_task["title"],
                    "reason": projected_task["blocked_reason"],
                    "reactivation_condition": projected_task["reactivation_condition"],
                    "evidence_refs": projected_task["evidence_refs"],
                }
            )
    advisories = [
        {
            "id": memory.id,
            "kind": memory.kind,
            "payload": _model(player, memory)["payload"],
            "evidence_refs": _model(player, memory)["evidence_refs"],
        }
        for memory in memories
        if memory.kind == "failure_forbidden" and memory.status == "active"
    ]
    corrections_by_step: dict[str, list[dict[str, str]]] = {}
    for memory in memories:
        if memory.kind != "failure_forbidden" or memory.status != "active":
            continue
        for reference in _model(player, memory)["evidence_refs"]:
            note = str(reference["note"])
            if not note.strip():
                continue
            for step_id in reference["evidence_step_ids"]:
                corrections_by_step.setdefault(step_id, []).append(
                    {
                        "memory_id": memory.id,
                        "note": note,
                    }
                )
    if latest_capsule:
        if latest_capsule.pending_action is not None:
            projected_pending_action = _model(player, latest_capsule)["pending_action"]
            blockers.append(
                {
                    "kind": "pending_action",
                    "id": latest_capsule.pending_action.id,
                    "title": "动作结果待确认",
                    "reason": projected_pending_action["intent"],
                    "reactivation_condition": "恢复后先重新观察，禁止盲目重放",
                    "evidence_refs": projected_pending_action["evidence_refs"],
                }
            )

    state_map = {
        "nodes": [_state_view(player, state) for state in visible_states],
        "edges": [
            {
                **_model(player, edge),
                "visuals": _entity_visuals(player, edge.evidence_refs),
            }
            for edge in visible_transitions
        ],
        "current_state_id": current_state.id if current_state else None,
    }
    coverage = {
        "observed_count": len(observations),
        "assigned_count": len(assignments),
        "semantic_state_latest_entity_count": len(latest_states),
        "semantic_state_raw_version_row_count": len(raw_state_versions),
        "visible_semantic_state_count": len(visible_states),
        "pending_adjudication_count": sum(
            state.status == "candidate" for state in latest_states
        ),
        "missing_transition_count": sum(
            task.source == "missing_transition" and task.status in _OPEN_TASK_STATUSES
            for task in tasks
        ),
        "blocked_frontier_count": sum(task.status == "blocked" for task in open_tasks),
        "states_by_status": _counts(latest_states, "status"),
        "transitions_by_outcome": _counts(transitions, "outcome"),
        "visible_transition_count": len(visible_transitions),
        "skill_version_count": len(skills),
        "visible_skill_version_count": len(visible_skills),
        "skill_run_count": len(skill_runs),
        "tasks_by_source": _counts(open_tasks, "source"),
        "tasks_by_status": _counts(open_tasks, "status"),
    }
    evidence_entities = [
        environment,
        *memories,
        *visible_states,
        *observations[-60:],
        *assignments[-60:],
        *visible_transitions,
        *tasks,
        *visible_skills,
        *recent_skill_runs,
        *recent_skill_validations,
        *gameplay_candidates,
        *([account_policy] if account_policy is not None else []),
        *speech_intents,
        *speech_events,
        *capsules,
        *guides,
        *action_quality_samples,
        *iteration_assessments,
        *account_metric_derivations,
        *soft_signal_reviews,
        *development_soft_signal_reviews,
        *open_soft_signal_review_requests,
        *remediation_verifications,
        *durable_sessions,
    ]
    references = _evidence_references(evidence_entities)
    active_durable_session = next(
        (
            session
            for session in durable_sessions
            if session.state in {"created", "running", "paused"}
        ),
        None,
    )
    budget = (
        {
            "source": "durable_session",
            "session_id": active_durable_session.id,
            "capsule_id": active_durable_session.last_capsule_id,
            "actions_remaining": active_durable_session.remaining_action_budget,
            "tokens_remaining": active_durable_session.remaining_token_budget,
            "seconds_remaining": active_durable_session.remaining_time_seconds,
        }
        if active_durable_session
        else
        {
            "source": "session_capsule",
            "session_id": None,
            "capsule_id": latest_capsule.id,
            "actions_remaining": latest_capsule.remaining_action_budget,
            "tokens_remaining": latest_capsule.remaining_token_budget,
            "seconds_remaining": latest_capsule.remaining_time_seconds,
        }
        if latest_capsule
        else {
            "source": "open_task_total",
            "session_id": None,
            "capsule_id": None,
            "actions_remaining": sum(task.action_budget for task in open_tasks),
            "tokens_remaining": (
                sum(task.token_budget or 0 for task in open_tasks)
                if any(task.token_budget is not None for task in open_tasks)
                else None
            ),
            "seconds_remaining": sum(task.time_budget_seconds for task in open_tasks),
        }
    )
    option_views = [
        {
            "requested_environment_id": item.requested_environment_id,
            "environment_id": item.selected_environment_id,
            "game_id": item.selected_environment.game_id,
            "account_scope_id": item.selected_environment.account_scope_id,
            "build_scope_id": item.selected_environment.build_scope_id,
            "channel": item.selected_environment.channel,
            "created_at": item.selected_environment.created_at,
        }
        for item in selections
    ]
    lineage_view = [
        {
            "environment_id": lineage_environment_id,
            "label": _environment_lineage_label(
                lineage_environment,
                is_leaf=lineage_environment_id == selection.selected_environment_id,
            ),
            "status": selection.lineage_statuses[lineage_environment_id],
        }
        for lineage_environment_id in selection.lineage_path
        if (lineage_environment := player.get_environment(lineage_environment_id)) is not None
    ]
    daily_continuity = None
    if environment.game_id == SANGUO_GAME_ID:
        daily_ledger = SanguoDailyContinuityLedger(player)
        daily_runs = []
        for continuity_run_id in daily_ledger.list_run_ids(environment.id):
            schedule = daily_ledger.schedule(environment.id, continuity_run_id)
            assessment = daily_ledger.assess(environment.id, continuity_run_id)
            daily_runs.append(
                {
                    "continuity_run_id": continuity_run_id,
                    "schedule": _model(player, schedule),
                    "assessment": _model(player, assessment),
                }
            )
        daily_continuity = {
            "schema": "game-observatory.ai-player.daily-continuity-console.v1",
            "runs": daily_runs,
            "latest": daily_runs[0] if daily_runs else None,
        }
    return player.project_current_text_payload({
        "schema": "game-observatory.ai-player.console.v1",
        "environment_options": option_views,
        "selection": _model(player, selection),
        "lineage": lineage_view,
        "identity": _model(player, environment),
        "current_state": _model(player, current_state) if current_state else None,
        "current_state_basis": current_state_basis,
        "memories": [_model(player, item) for item in memories],
        "states": [_model(player, item) for item in visible_states],
        "state_map": state_map,
        "tasks": [_model(player, item) for item in tasks],
        "frontier": [_model(player, item) for item in open_tasks],
        "coverage": coverage,
        "skills": [
            {
                **_model(player, item),
                "run_count": sum(run.skill_version_id == item.id for run in skill_runs),
                "latest_validation": next(
                    (
                        _model(player, validation)
                        for validation in reversed(skill_validations)
                        if validation.skill_version_id == item.id
                    ),
                    None,
                ),
            }
            for item in visible_skills
        ],
        "skill_runs": [_model(player, item) for item in recent_skill_runs],
        "skill_validations": [_model(player, item) for item in recent_skill_validations],
        "path_reuse_health": path_reuse_health,
        "gameplay_candidates": [_model(player, item) for item in gameplay_candidates],
        "account_policy": (
            _model(player, account_policy) if account_policy is not None else None
        ),
        "speech_intents": [_model(player, item) for item in speech_intents],
        "speech_events": [_model(player, item) for item in speech_events],
        "capsules": [_model(player, item) for item in capsules],
        "guide_knowledge": [_guide_view(player, item) for item in guides],
        "budget": budget,
        "blockers": blockers,
        "advisories": advisories,
        "evidence": _latest_evidence(
            player,
            references,
            session_bound_run_ids=session_bound_run_ids,
            corrections_by_step=corrections_by_step,
        ),
        "daily_continuity": daily_continuity,
        "physical_readiness_gate": _model(player, physical_readiness_gate),
        "iteration_monitoring": {
            "policy_version": DEFAULT_ITERATION_POLICY.version,
            "review_every_actions": DEFAULT_ITERATION_POLICY.actions_per_review,
            "session_id": monitoring_session.id if monitoring_session is not None else None,
            "recent_samples": [_model(player, item) for item in action_quality_samples],
            "latest_assessment": (
                _model(player, iteration_assessments[0])
                if iteration_assessments
                else None
            ),
            "assessment_history": [
                _model(player, item) for item in iteration_assessments
            ],
            "account_metric_derivations": [
                _model(player, item) for item in account_metric_derivations
            ],
            "soft_signal_reviews": [
                _model(player, item) for item in soft_signal_reviews
            ],
            "development_soft_signal_reviews": [
                _model(player, item) for item in development_soft_signal_reviews
            ],
            "open_soft_signal_review_requests": [
                _model(player, item) for item in open_soft_signal_review_requests
            ],
            "remediation_gate": _model(player, remediation_gate),
            "remediation_verifications": [
                _model(player, item) for item in remediation_verifications
            ],
        },
    })
