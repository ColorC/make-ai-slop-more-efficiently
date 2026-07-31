"""Fail-closed deterministic export of one gameplay collection bundle.

The bundle is a projection of canonical AI-player entities and raw Observatory
evidence.  It is evidence for later design reconstruction, never a promotion of
an automatic gameplay candidate into confirmed design truth.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, ClassVar, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models import ArtifactRef, EvidenceRun, EvidenceStep, RunResult
from .contracts import (
    ActionQualitySampleV1,
    EnvironmentScopeV1,
    EvidenceReferenceV1,
    FrontierTaskV1,
    GameplayCandidateV1,
    SemanticStateV1,
    SkillRunV1,
    SkillVersionV1,
    StateAssignmentV1,
    StateObservationV1,
    TransitionEdgeV1,
)
from .store import AIPlayerStore


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_REQUIRED_FILENAMES = (
    "bundle_manifest.json",
    "state_map.json",
    "interfaces.json",
    "interaction_traces.jsonl",
    "gameplay_candidates.json",
    "skills.json",
    "coverage.json",
    "evidence_index.json",
)
_POINTER_ACTIONS = {"tap", "swipe", "pinch", "two_finger_swipe", "mouse_move"}
_KEY_ACTIONS = {
    "key",
    "back",
    "home",
    "text",
    "gamepad_button",
    "gamepad_axis",
}


class GameplayCollectionExportError(ValueError):
    """The requested scope cannot produce a complete evidence package."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class _HashedDocument(_StrictModel):
    content_sha256: str = Field(pattern=_SHA256_PATTERN)
    schema_id_value: ClassVar[str]

    @model_validator(mode="after")
    def content_hash_is_exact(self) -> "_HashedDocument":
        payload = self.model_dump(mode="json", by_alias=True)
        digest = payload.pop("content_sha256")
        if digest != _sha256(_canonical_json_bytes(payload)):
            raise ValueError("document content_sha256 does not match canonical content")
        return self


class GameplayBundleManifestV1(_HashedDocument):
    schema_id: Literal["game-observatory.ai-player.gameplay-bundle-manifest.v1"] = Field(
        default="game-observatory.ai-player.gameplay-bundle-manifest.v1",
        alias="schema",
    )
    bundle_id: str = Field(min_length=1)
    environment: dict[str, Any]
    source_scope: dict[str, Any]
    source_time_range: dict[str, str]
    sessions: list[dict[str, Any]]
    models: list[dict[str, Any]]
    config: dict[str, Any]
    files: list[dict[str, Any]] = Field(min_length=7, max_length=7)
    bundle_root_sha256: str = Field(pattern=_SHA256_PATTERN)
    truth_status: Literal["candidate_evidence_only"] = "candidate_evidence_only"


class GameplayStateMapV1(_HashedDocument):
    schema_id: Literal["game-observatory.ai-player.gameplay-state-map.v1"] = Field(
        default="game-observatory.ai-player.gameplay-state-map.v1",
        alias="schema",
    )
    environment_id: str
    states: list[dict[str, Any]] = Field(min_length=1)
    transitions: list[dict[str, Any]] = Field(min_length=1)
    entry_state_ids: list[str] = Field(min_length=1)
    exit_state_ids: list[str] = Field(min_length=1)
    recovery_points: list[dict[str, Any]]


class GameplayInterfacesV1(_HashedDocument):
    schema_id: Literal["game-observatory.ai-player.gameplay-interfaces.v1"] = Field(
        default="game-observatory.ai-player.gameplay-interfaces.v1",
        alias="schema",
    )
    environment_id: str
    interfaces: list[dict[str, Any]] = Field(min_length=1)


class GameplayCandidatesExportV1(_HashedDocument):
    schema_id: Literal["game-observatory.ai-player.gameplay-candidates-export.v1"] = Field(
        default="game-observatory.ai-player.gameplay-candidates-export.v1",
        alias="schema",
    )
    environment_id: str
    source_scope_status: Literal["pending_candidate", "explicit_unconfirmed_scope"]
    candidates: list[dict[str, Any]]
    confirmed_design_fact_count: Literal[0] = 0


class GameplaySkillsExportV1(_HashedDocument):
    schema_id: Literal["game-observatory.ai-player.gameplay-skills-export.v1"] = Field(
        default="game-observatory.ai-player.gameplay-skills-export.v1",
        alias="schema",
    )
    environment_id: str
    skill_versions: list[dict[str, Any]]
    skill_runs: list[dict[str, Any]]
    replay_summary: dict[str, Any]


class GameplayCoverageV1(_HashedDocument):
    schema_id: Literal["game-observatory.ai-player.gameplay-coverage.v1"] = Field(
        default="game-observatory.ai-player.gameplay-coverage.v1",
        alias="schema",
    )
    environment_id: str
    covered_state_ids: list[str]
    covered_transition_ids: list[str]
    covered_interaction_count: int = Field(ge=0)
    frontier_tasks: list[dict[str, Any]]
    blocked_tasks: list[dict[str, Any]]
    stale_items: list[dict[str, Any]]
    execution_efficiency: dict[str, Any]


class GameplayEvidenceIndexV1(_HashedDocument):
    schema_id: Literal["game-observatory.ai-player.gameplay-evidence-index.v1"] = Field(
        default="game-observatory.ai-player.gameplay-evidence-index.v1",
        alias="schema",
    )
    environment_id: str
    entries: list[dict[str, Any]] = Field(min_length=1)
    critical_evidence_open_rate: Literal[1.0] = 1.0


class GameplayInteractionTraceV1(_HashedDocument):
    schema_id: Literal["game-observatory.ai-player.gameplay-interaction-trace.v1"] = Field(
        default="game-observatory.ai-player.gameplay-interaction-trace.v1",
        alias="schema",
    )
    trace_id: str
    environment_id: str
    transition_edge_id: str
    evidence_run_id: str
    evidence_step_id: str
    task_id: str
    source_state_id: str
    target_state_id: str
    before_artifact_id: str
    action: dict[str, Any]
    action_input: dict[str, Any]
    target_name: str | None
    target_bounds: dict[str, Any] | None
    after_artifact_id: str
    state_change: dict[str, Any]
    task_judgement: dict[str, Any]
    recovery: dict[str, Any]
    execution: dict[str, Any]
    failure_or_drift: dict[str, Any]
    evidence_refs: list[dict[str, Any]] = Field(min_length=1)


@dataclass(frozen=True)
class GameplayCollectionExportResult:
    bundle_id: str
    output_dir: Path
    manifest_sha256: str
    file_sha256: dict[str, str]
    idempotent_reuse: bool


DocumentT = TypeVar("DocumentT", bound=_HashedDocument)


def _canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _pretty_json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _seal_document(model: type[DocumentT], payload: dict[str, Any]) -> DocumentT:
    canonical = dict(payload)
    canonical["content_sha256"] = _sha256(_canonical_json_bytes(payload))
    return model.model_validate(canonical)


def _model_payload(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode="json", by_alias=True)


def _ordered_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_ms(started_at: str | None, ended_at: str | None) -> float | None:
    start = _parse_time(started_at)
    end = _parse_time(ended_at)
    if start is None or end is None or end < start:
        return None
    return round((end - start).total_seconds() * 1000, 3)


class _EvidenceCollector:
    def __init__(self, store: AIPlayerStore, environment: EnvironmentScopeV1) -> None:
        self.store = store
        self.environment = environment
        self.artifacts: dict[str, ArtifactRef] = {}
        self.evidence_runs: dict[str, EvidenceRun] = {}
        self.evidence_steps: dict[str, EvidenceStep] = {}
        self.trace_runs: dict[str, RunResult] = {}
        self.source_snapshots: dict[str, Any] = {}

    def add_references(self, references: list[EvidenceReferenceV1]) -> None:
        if not references:
            raise GameplayCollectionExportError("canonical entity has no evidence references")
        try:
            resolved = self.store.resolve_evidence_references(
                references,
                environment_scope=self.environment,
            )
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise GameplayCollectionExportError(str(exc)) from exc
        for artifact in resolved["artifact"]:
            self._add_artifact(artifact)
        for run in resolved["evidence_run"]:
            self.evidence_runs[run.id] = run
        for step in resolved["evidence_step"]:
            self.evidence_steps[step.id] = step
        for run in resolved["trace_run"]:
            self.trace_runs[run.id] = run
        for snapshot in resolved["source_snapshot"]:
            self.source_snapshots[snapshot.id] = snapshot

    def _add_artifact(self, artifact: ArtifactRef) -> None:
        path = Path(artifact.path)
        if not path.is_file():
            raise GameplayCollectionExportError(f"critical artifact is not openable: {artifact.id}")
        if _sha256(path.read_bytes()) != artifact.sha256:
            raise GameplayCollectionExportError(f"critical artifact hash mismatch: {artifact.id}")
        self.artifacts[artifact.id] = artifact

    def artifact(self, artifact_id: str) -> ArtifactRef:
        artifact = self.store.observatory_store.get_artifact(artifact_id)
        if artifact is None:
            raise GameplayCollectionExportError(f"critical artifact is missing: {artifact_id}")
        self._add_artifact(artifact)
        return artifact

    def action_step(self, step_id: str) -> tuple[EvidenceStep, EvidenceRun, RunResult]:
        step = self.store.observatory_store.get_evidence_step(step_id)
        if step is None:
            raise GameplayCollectionExportError(f"critical EvidenceStep is missing: {step_id}")
        run = self.store.observatory_store.get_evidence_run(step.evidence_run_id)
        if run is None:
            raise GameplayCollectionExportError(
                f"critical EvidenceRun is missing: {step.evidence_run_id}"
            )
        if (
            step.status != "passed"
            or not step.ended_at
            or not step.before_frame_id
            or not step.after_frame_id
            or not step.action_run_id
            or step.quality_issues
            or not step.stability.settled
            or run.status != "passed"
            or not run.ended_at
        ):
            raise GameplayCollectionExportError(
                f"action evidence is incomplete or unsettled: {step.id}"
            )
        action_run = self.store.observatory_store.get_run(step.action_run_id)
        if action_run is None:
            raise GameplayCollectionExportError(f"action trace run is missing: {step.action_run_id}")
        self.artifact(step.before_frame_id)
        self.artifact(step.after_frame_id)
        for artifact_id in step.artifact_ids:
            self.artifact(artifact_id)
        self.evidence_steps[step.id] = step
        self.evidence_runs[run.id] = run
        self.trace_runs[action_run.id] = action_run
        return step, run, action_run


def _scope(
    store: AIPlayerStore,
    environment_id: str,
    *,
    gameplay_candidate_id: str | None,
    state_ids: list[str] | None,
    transition_edge_ids: list[str] | None,
) -> tuple[
    EnvironmentScopeV1,
    GameplayCandidateV1 | None,
    list[SemanticStateV1],
    list[TransitionEdgeV1],
]:
    environment = store.get_environment(environment_id)
    if environment is None:
        raise GameplayCollectionExportError(f"unknown environment: {environment_id}")
    explicit = bool(state_ids or transition_edge_ids)
    if bool(gameplay_candidate_id) == explicit:
        raise GameplayCollectionExportError(
            "provide exactly one gameplay candidate or one explicit state/transition scope"
        )
    candidate = None
    if gameplay_candidate_id is not None:
        candidate = store.get_gameplay_candidate(environment_id, gameplay_candidate_id)
        if candidate is None:
            raise GameplayCollectionExportError(
                f"unknown gameplay candidate: {gameplay_candidate_id}"
            )
        selected_state_ids = _ordered_unique(
            [*candidate.entry_state_ids, *candidate.main_state_ids, *candidate.exit_state_ids]
        )
        selected_edge_ids = list(candidate.transition_edge_ids)
    else:
        selected_state_ids = _ordered_unique(list(state_ids or []))
        selected_edge_ids = _ordered_unique(list(transition_edge_ids or []))
        if not selected_state_ids or not selected_edge_ids:
            raise GameplayCollectionExportError(
                "explicit gameplay scope requires both states and transitions"
            )
    states = []
    for state_id in selected_state_ids:
        state = store.get_semantic_state(environment_id, state_id)
        if state is None:
            raise GameplayCollectionExportError(f"scope state is missing: {state_id}")
        states.append(state)
    edges = []
    state_set = set(selected_state_ids)
    for edge_id in selected_edge_ids:
        edge = store.get_transition_edge(environment_id, edge_id)
        if edge is None or edge.to_state_id is None:
            raise GameplayCollectionExportError(f"scope transition is missing a state: {edge_id}")
        if edge.from_state_id not in state_set or edge.to_state_id not in state_set:
            raise GameplayCollectionExportError(
                f"scope transition escapes selected states: {edge_id}"
            )
        edges.append(edge)
    return environment, candidate, states, edges


def _action_input(step: EvidenceStep) -> dict[str, Any]:
    action = step.action
    if action.type in _POINTER_ACTIONS:
        if step.source_point is None:
            raise GameplayCollectionExportError(f"pointer action has no source coordinate: {step.id}")
        if action.type in {"swipe", "two_finger_swipe"} and step.source_end_point is None:
            raise GameplayCollectionExportError(f"gesture has no end coordinate: {step.id}")
        if action.type in {"tap", "pinch", "two_finger_swipe"} and step.target_bounds is None:
            raise GameplayCollectionExportError(f"pointer action has no target bounds: {step.id}")
        if step.target_bounds is not None and not step.target_bounds.contains(step.source_point):
            raise GameplayCollectionExportError(f"pointer coordinate escapes target bounds: {step.id}")
        return {
            "kind": "source_pixel_coordinates",
            "start": _model_payload(step.source_point),
            "end": _model_payload(step.source_end_point) if step.source_end_point else None,
            "viewport": [step.viewport_width, step.viewport_height],
        }
    if action.type in _KEY_ACTIONS:
        return {
            "kind": "key_or_text_input",
            "action_type": action.type,
            "keycode": action.keycode,
            "text": action.text,
            "button": action.button,
        }
    raise GameplayCollectionExportError(
        f"action has neither reconstructable coordinates nor a supported key input: {step.id}"
    )


def _trace_records(
    store: AIPlayerStore,
    environment: EnvironmentScopeV1,
    collector: _EvidenceCollector,
    edges: list[TransitionEdgeV1],
    allowed_task_ids: set[str] | None,
) -> tuple[list[GameplayInteractionTraceV1], dict[str, FrontierTaskV1]]:
    quality_by_step = {
        sample.evidence_step_id: sample
        for sample in store.list_action_quality_samples(environment.id, limit=10_000)
        if sample.evidence_step_id
    }
    raw: list[dict[str, Any]] = []
    tasks: dict[str, FrontierTaskV1] = {}
    for edge in sorted(edges, key=lambda item: (item.created_at, item.id)):
        collector.add_references(edge.evidence_refs)
        step_ids = _ordered_unique(
            [
                step_id
                for reference in edge.evidence_refs
                for step_id in reference.evidence_step_ids
            ]
        )
        if not step_ids:
            raise GameplayCollectionExportError(
                f"transition has no canonical action EvidenceStep: {edge.id}"
            )
        for step_id in step_ids:
            step, run, _action_run = collector.action_step(step_id)
            if step.action != edge.action or step.target_bounds != edge.target_bounds:
                raise GameplayCollectionExportError(
                    f"transition action differs from raw EvidenceStep: {edge.id}"
                )
            task_id = run.environment.get("task_id")
            if not isinstance(task_id, str) or not task_id:
                raise GameplayCollectionExportError(f"action has no task judgement: {step.id}")
            task = store.get_task(environment.id, task_id)
            if task is None:
                raise GameplayCollectionExportError(f"action task is missing: {task_id}")
            if allowed_task_ids is not None and task_id not in allowed_task_ids:
                raise GameplayCollectionExportError(
                    f"action task escapes gameplay candidate boundary: {task_id}"
                )
            collector.add_references(task.evidence_refs)
            tasks[task.id] = task
            quality: ActionQualitySampleV1 | None = quality_by_step.get(step.id)
            skill_version_id = run.environment.get("skill_replay_version_id")
            if isinstance(skill_version_id, str) and skill_version_id:
                if store.get_skill_version_by_id(environment.id, skill_version_id) is None:
                    raise GameplayCollectionExportError(
                        f"skill replay references an unknown canonical version: {skill_version_id}"
                    )
            fixed_duration = _duration_ms(step.action_started_at, step.action_ended_at)
            failure_reasons = []
            # ``deferred`` means the semantic transition still awaits
            # adjudication.  It is not evidence that the physical action
            # failed or that the interface drifted.
            if edge.outcome in {"failed", "forbidden"}:
                failure_reasons.append(f"transition_outcome:{edge.outcome}")
            if quality is not None and quality.outcome not in {"confirmed"}:
                failure_reasons.append(f"action_quality:{quality.outcome}")
            fallback_recorded = bool(
                run.environment.get("fallback_to_semantic_layer")
                or run.environment.get("semantic_fallback")
            )
            raw.append(
                {
                    "edge": edge,
                    "step": step,
                    "run": run,
                    "task": task,
                    "quality": quality,
                    "skill_version_id": (
                        str(skill_version_id) if isinstance(skill_version_id, str) else None
                    ),
                    "fixed_duration": fixed_duration,
                    "failure_reasons": failure_reasons,
                    "fallback_recorded": fallback_recorded,
                }
            )
    replay_ordinals: defaultdict[str, int] = defaultdict(int)
    traces = []
    for item in sorted(
        raw,
        key=lambda value: (
            value["step"].started_at,
            value["step"].id,
            value["edge"].id,
        ),
    ):
        edge: TransitionEdgeV1 = item["edge"]
        step: EvidenceStep = item["step"]
        run: EvidenceRun = item["run"]
        task: FrontierTaskV1 = item["task"]
        quality: ActionQualitySampleV1 | None = item["quality"]
        skill_version_id = item["skill_version_id"]
        execution_kind = "unclassified_non_skill_action"
        if skill_version_id:
            execution_kind = "fixed_skill_replay"
        elif quality is not None and quality.decision_mode == "new_state":
            execution_kind = "semantic_exploration"
        elif quality is not None:
            execution_kind = f"quality_mode:{quality.decision_mode}"
        reuse_ordinal = None
        if skill_version_id:
            replay_ordinals[skill_version_id] += 1
            reuse_ordinal = replay_ordinals[skill_version_id]
        payload = {
            "schema": "game-observatory.ai-player.gameplay-interaction-trace.v1",
            "trace_id": f"trace.{edge.id}.{step.id}",
            "environment_id": environment.id,
            "transition_edge_id": edge.id,
            "evidence_run_id": run.id,
            "evidence_step_id": step.id,
            "task_id": task.id,
            "source_state_id": edge.from_state_id,
            "target_state_id": edge.to_state_id,
            "before_artifact_id": step.before_frame_id,
            "action": _model_payload(step.action),
            "action_input": _action_input(step),
            "target_name": step.target_name,
            "target_bounds": _model_payload(step.target_bounds) if step.target_bounds else None,
            "after_artifact_id": step.after_frame_id,
            "state_change": {
                "expected": edge.expected_change,
                "observed": edge.observed_change,
                "outcome": edge.outcome,
                "judgement_source": f"transition_edge:{edge.id}",
            },
            "task_judgement": {
                "task_id": task.id,
                "title": task.title,
                "reason": task.reason,
                "status": task.status,
                "judgement_source": f"evidence_run:{run.id}.environment.task_id",
            },
            "recovery": {
                "recovery_skill_version_id": edge.recovery_skill_version_id,
                "quality_decision_mode": quality.decision_mode if quality else None,
                "recovery_succeeded": quality.recovery_succeeded if quality else None,
            },
            "execution": {
                "kind": execution_kind,
                "skill_version_id": skill_version_id,
                "reuse_ordinal_in_scope": reuse_ordinal,
                "first_exploration_recorded": (
                    quality is not None and quality.decision_mode == "new_state"
                ),
                "decision_mode": quality.decision_mode if quality else None,
                "fixed_action_duration_ms": item["fixed_duration"],
                "fixed_action_duration_unavailable_reason": (
                    None
                    if item["fixed_duration"] is not None
                    else "EvidenceStep 未记录 action_started_at/action_ended_at"
                ),
                "decision_latency_ms": quality.decision_latency_ms if quality else None,
                "model_input_tokens": quality.model_input_tokens if quality else None,
                "action_quality_sample_id": quality.id if quality else None,
            },
            "failure_or_drift": {
                "detected": bool(item["failure_reasons"]),
                "reasons": item["failure_reasons"],
                "fallback_to_semantic_layer_recorded": item["fallback_recorded"],
                "fallback_source": (
                    f"evidence_run:{run.id}.environment"
                    if item["fallback_recorded"]
                    else "not_recorded"
                ),
            },
            "evidence_refs": [_model_payload(reference) for reference in edge.evidence_refs],
        }
        traces.append(_seal_document(GameplayInteractionTraceV1, payload))
    return traces, tasks


def _skill_projection(
    store: AIPlayerStore,
    environment_id: str,
    collector: _EvidenceCollector,
    transition_ids: set[str],
) -> tuple[list[SkillVersionV1], list[SkillRunV1]]:
    skills = [
        skill
        for skill in store.list_skill_versions(environment_id, latest_only=False)
        if transition_ids.intersection(skill.source_transition_ids)
    ]
    runs: list[SkillRunV1] = []
    for skill in skills:
        collector.add_references(skill.evidence_refs)
        for run in store.list_skill_runs(environment_id, skill_version_id=skill.id):
            collector.add_references(run.evidence_refs)
            runs.append(run)
    return (
        sorted(skills, key=lambda item: (item.skill_id, item.version, item.id)),
        sorted(runs, key=lambda item: (item.started_at, item.id)),
    )


def _state_interfaces(
    store: AIPlayerStore,
    environment_id: str,
    collector: _EvidenceCollector,
    states: list[SemanticStateV1],
    traces: list[GameplayInteractionTraceV1],
    candidate: GameplayCandidateV1 | None,
) -> list[dict[str, Any]]:
    targets_by_state: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    screenshots_by_state: defaultdict[str, set[str]] = defaultdict(set)
    ui_trees_by_state: defaultdict[str, set[str]] = defaultdict(set)
    for trace in traces:
        step = collector.evidence_steps[trace.evidence_step_id]
        targets_by_state[trace.source_state_id].append(
            {
                "target_name": trace.target_name,
                "target_bounds": trace.target_bounds,
                "action_input": trace.action_input,
                "transition_edge_id": trace.transition_edge_id,
                "evidence_step_id": trace.evidence_step_id,
            }
        )
        screenshots_by_state[trace.source_state_id].add(trace.before_artifact_id)
        screenshots_by_state[trace.target_state_id].add(trace.after_artifact_id)
        if step.before_ui_tree_id:
            collector.artifact(step.before_ui_tree_id)
            ui_trees_by_state[trace.source_state_id].add(step.before_ui_tree_id)
        if step.after_ui_tree_id:
            collector.artifact(step.after_ui_tree_id)
            ui_trees_by_state[trace.target_state_id].add(step.after_ui_tree_id)
    interfaces = []
    for state in sorted(states, key=lambda item: item.id):
        collector.add_references(state.evidence_refs)
        state_artifact_ids = {
            artifact_id for reference in state.evidence_refs for artifact_id in reference.artifact_ids
        }
        assignments: list[StateAssignmentV1] = store.list_state_assignments(
            environment_id,
            state_id=state.id,
        )
        variants = []
        for assignment in assignments:
            observation: StateObservationV1 | None = store.get_state_observation(
                environment_id,
                assignment.observation_id,
            )
            if observation is None:
                raise GameplayCollectionExportError(
                    f"state assignment observation is missing: {assignment.observation_id}"
                )
            collector.add_references(assignment.evidence_refs)
            collector.add_references(observation.evidence_refs)
            state_artifact_ids.update(
                artifact_id
                for reference in (*assignment.evidence_refs, *observation.evidence_refs)
                for artifact_id in reference.artifact_ids
            )
            variants.append(
                {
                    "assignment": _model_payload(assignment),
                    "observation": _model_payload(observation),
                }
            )
        for artifact_id in state_artifact_ids:
            artifact = collector.artifact(artifact_id)
            if artifact.kind in {"screenshot", "video_frame"}:
                screenshots_by_state[state.id].add(artifact.id)
            elif artifact.kind == "ui_tree":
                ui_trees_by_state[state.id].add(artifact.id)
        screenshot_ids = sorted(screenshots_by_state[state.id])
        ui_tree_ids = sorted(ui_trees_by_state[state.id])
        if not screenshot_ids:
            raise GameplayCollectionExportError(
                f"interface state has no canonical screenshot: {state.id}"
            )
        interfaces.append(
            {
                "interface_id": state.id,
                "semantic_state": _model_payload(state),
                "variants": variants,
                "screenshot_artifact_ids": screenshot_ids,
                "ui_tree_artifact_ids": ui_tree_ids,
                "ui_tree_unavailable_reason": (
                    None if ui_tree_ids else "canonical EvidenceStep 未提供 UI tree artifact"
                ),
                "interactive_objects": sorted(
                    targets_by_state[state.id],
                    key=lambda item: (item["transition_edge_id"], item["evidence_step_id"]),
                ),
                "tags": sorted(
                    {
                        *state.tags,
                        state.status,
                        *(candidate.interface_family_ids if candidate else []),
                    }
                ),
                "gameplay_candidate_ids": [candidate.id] if candidate else [],
            }
        )
    return interfaces


def _evidence_index_entries(collector: _EvidenceCollector) -> list[dict[str, Any]]:
    entries = []
    for artifact in collector.artifacts.values():
        entries.append(
            {
                "kind": artifact.kind,
                "id": artifact.id,
                "canonical_path": artifact.path,
                "sha256": artifact.sha256,
                "openable": True,
                "run_id": artifact.run_id,
                "media_type": artifact.media_type,
            }
        )
    for run in collector.evidence_runs.values():
        payload = _model_payload(run)
        entries.append(
            {
                "kind": "evidence_run",
                "id": run.id,
                "canonical_table": "evidence_runs",
                "sha256": _sha256(_canonical_json_bytes(payload)),
                "openable": True,
                "artifact_ids": sorted(run.artifact_ids),
            }
        )
    for step in collector.evidence_steps.values():
        payload = _model_payload(step)
        entries.append(
            {
                "kind": "evidence_step",
                "id": step.id,
                "canonical_table": "evidence_steps",
                "sha256": _sha256(_canonical_json_bytes(payload)),
                "openable": True,
                "artifact_ids": sorted(step.artifact_ids),
            }
        )
    for run in collector.trace_runs.values():
        payload = _model_payload(run)
        entries.append(
            {
                "kind": "trace_run",
                "id": run.id,
                "canonical_table": "runs",
                "sha256": _sha256(_canonical_json_bytes(payload)),
                "openable": True,
                "artifact_ids": sorted(run.artifact_ids),
            }
        )
    for snapshot in collector.source_snapshots.values():
        entries.append(
            {
                "kind": "source_snapshot",
                "id": snapshot.id,
                "canonical_table": "source_snapshots",
                "sha256": snapshot.content_sha256,
                "openable": True,
                "source_id": snapshot.source_id,
            }
        )
    return sorted(entries, key=lambda item: (item["kind"], item["id"]))


def _document_bytes(document: _HashedDocument) -> bytes:
    return _pretty_json_bytes(document.model_dump(mode="json", by_alias=True))


def _commit_bundle(output_dir: Path, files: dict[str, bytes]) -> bool:
    destination = output_dir.resolve()
    if destination.exists():
        if not destination.is_dir():
            raise GameplayCollectionExportError(f"bundle output is not a directory: {destination}")
        existing = {path.name: path.read_bytes() for path in destination.iterdir() if path.is_file()}
        if existing == files:
            return True
        raise GameplayCollectionExportError(
            "bundle output already exists with different canonical content"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    bundle_root = _sha256(
        _canonical_json_bytes(
            [{"path": name, "sha256": _sha256(content)} for name, content in sorted(files.items())]
        )
    )
    temporary = destination.parent / f".{destination.name}.tmp-{bundle_root[:16]}"
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    try:
        for name, content in files.items():
            (temporary / name).write_bytes(content)
        temporary.replace(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return False


def export_gameplay_collection_bundle(
    store: AIPlayerStore,
    environment_id: str,
    output_dir: Path,
    *,
    gameplay_candidate_id: str | None = None,
    state_ids: list[str] | None = None,
    transition_edge_ids: list[str] | None = None,
) -> GameplayCollectionExportResult:
    """Export the eight-file C-09/G-09 package without mutating canonical state."""

    environment, candidate, states, edges = _scope(
        store,
        environment_id,
        gameplay_candidate_id=gameplay_candidate_id,
        state_ids=state_ids,
        transition_edge_ids=transition_edge_ids,
    )
    collector = _EvidenceCollector(store, environment)
    collector.add_references(environment.evidence_refs)
    if candidate is not None:
        collector.add_references(candidate.evidence_refs)
    allowed_task_ids = set(candidate.triggering_task_ids) if candidate else None
    traces, tasks = _trace_records(
        store,
        environment,
        collector,
        edges,
        allowed_task_ids,
    )
    if not traces:
        raise GameplayCollectionExportError("gameplay scope has no complete action traces")
    if candidate is not None and set(tasks) != set(candidate.triggering_task_ids):
        raise GameplayCollectionExportError(
            "gameplay candidate task boundary is not fully represented by raw actions"
        )
    transition_ids = {edge.id for edge in edges}
    skills, skill_runs = _skill_projection(
        store,
        environment_id,
        collector,
        transition_ids,
    )
    interfaces = _state_interfaces(
        store,
        environment_id,
        collector,
        states,
        traces,
        candidate,
    )
    entry_ids = candidate.entry_state_ids if candidate else [edges[0].from_state_id]
    exit_ids = candidate.exit_state_ids if candidate else [edges[-1].to_state_id or ""]
    state_map = _seal_document(
        GameplayStateMapV1,
        {
            "schema": "game-observatory.ai-player.gameplay-state-map.v1",
            "environment_id": environment_id,
            "states": [_model_payload(state) for state in sorted(states, key=lambda item: item.id)],
            "transitions": [_model_payload(edge) for edge in sorted(edges, key=lambda item: item.id)],
            "entry_state_ids": sorted(entry_ids),
            "exit_state_ids": sorted(exit_ids),
            "recovery_points": sorted(
                [
                    {
                        "transition_edge_id": edge.id,
                        "recovery_skill_version_id": edge.recovery_skill_version_id,
                    }
                    for edge in edges
                    if edge.recovery_skill_version_id
                ],
                key=lambda item: item["transition_edge_id"],
            ),
        },
    )
    interfaces_document = _seal_document(
        GameplayInterfacesV1,
        {
            "schema": "game-observatory.ai-player.gameplay-interfaces.v1",
            "environment_id": environment_id,
            "interfaces": interfaces,
        },
    )
    candidates_document = _seal_document(
        GameplayCandidatesExportV1,
        {
            "schema": "game-observatory.ai-player.gameplay-candidates-export.v1",
            "environment_id": environment_id,
            "source_scope_status": (
                "pending_candidate" if candidate else "explicit_unconfirmed_scope"
            ),
            "candidates": (
                [
                    {
                        **_model_payload(candidate),
                        "closure_status": (
                            "closed" if candidate.status == "closed" else "pending_review"
                        ),
                        "export_does_not_confirm_candidate": True,
                    }
                ]
                if candidate
                else []
            ),
            "confirmed_design_fact_count": 0,
        },
    )
    replay_counts = Counter(
        trace.execution.get("skill_version_id")
        for trace in traces
        if trace.execution.get("skill_version_id")
    )
    fixed_durations = [
        float(value)
        for trace in traces
        if (value := trace.execution.get("fixed_action_duration_ms")) is not None
        and trace.execution.get("kind") == "fixed_skill_replay"
    ]
    skills_document = _seal_document(
        GameplaySkillsExportV1,
        {
            "schema": "game-observatory.ai-player.gameplay-skills-export.v1",
            "environment_id": environment_id,
            "skill_versions": [_model_payload(skill) for skill in skills],
            "skill_runs": [_model_payload(run) for run in skill_runs],
            "replay_summary": {
                "replay_count_by_skill_version": dict(sorted(replay_counts.items())),
                "fixed_replay_duration_ms_median": (
                    round(median(fixed_durations), 3) if fixed_durations else None
                ),
                "fixed_replay_duration_sample_count": len(fixed_durations),
                "first_exploration_trace_count": sum(
                    trace.execution.get("first_exploration_recorded") is True for trace in traces
                ),
            },
        },
    )
    relevant_tasks = sorted(tasks.values(), key=lambda item: item.id)
    stale_items = [
        {"kind": "skill", "id": skill.id, "status": skill.status}
        for skill in skills
        if skill.status in {"degraded", "invalidated"}
    ]
    coverage_document = _seal_document(
        GameplayCoverageV1,
        {
            "schema": "game-observatory.ai-player.gameplay-coverage.v1",
            "environment_id": environment_id,
            "covered_state_ids": sorted(state.id for state in states),
            "covered_transition_ids": sorted(edge.id for edge in edges),
            "covered_interaction_count": len(traces),
            "frontier_tasks": [
                _model_payload(task)
                for task in relevant_tasks
                if task.status in {"queued", "active", "cooldown"}
            ],
            "blocked_tasks": [
                _model_payload(task) for task in relevant_tasks if task.status == "blocked"
            ],
            "stale_items": stale_items,
            "execution_efficiency": {
                "semantic_exploration_count": sum(
                    trace.execution.get("first_exploration_recorded") is True for trace in traces
                ),
                "unclassified_non_skill_action_count": sum(
                    trace.execution.get("kind") == "unclassified_non_skill_action"
                    for trace in traces
                ),
                "fixed_skill_replay_count": sum(
                    trace.execution.get("kind") == "fixed_skill_replay" for trace in traces
                ),
                "reuse_count_by_skill_version": dict(sorted(replay_counts.items())),
                "fixed_replay_duration_ms_median": (
                    round(median(fixed_durations), 3) if fixed_durations else None
                ),
                "failure_or_drift_count": sum(
                    trace.failure_or_drift.get("detected") is True for trace in traces
                ),
                "semantic_fallback_recorded_count": sum(
                    trace.failure_or_drift.get("fallback_to_semantic_layer_recorded") is True
                    for trace in traces
                ),
            },
        },
    )
    evidence_document = _seal_document(
        GameplayEvidenceIndexV1,
        {
            "schema": "game-observatory.ai-player.gameplay-evidence-index.v1",
            "environment_id": environment_id,
            "entries": _evidence_index_entries(collector),
            "critical_evidence_open_rate": 1.0,
        },
    )
    trace_bytes = b"".join(
        _canonical_json_bytes(trace.model_dump(mode="json", by_alias=True)) + b"\n"
        for trace in traces
    )
    files_without_manifest = {
        "state_map.json": _document_bytes(state_map),
        "interfaces.json": _document_bytes(interfaces_document),
        "interaction_traces.jsonl": trace_bytes,
        "gameplay_candidates.json": _document_bytes(candidates_document),
        "skills.json": _document_bytes(skills_document),
        "coverage.json": _document_bytes(coverage_document),
        "evidence_index.json": _document_bytes(evidence_document),
    }
    file_refs = [
        {"path": name, "sha256": _sha256(content), "bytes": len(content)}
        for name, content in sorted(files_without_manifest.items())
    ]
    bundle_root = _sha256(_canonical_json_bytes(file_refs))
    all_times = sorted(
        [
            value
            for trace in traces
            for value in (
                collector.evidence_steps[trace.evidence_step_id].started_at,
                collector.evidence_steps[trace.evidence_step_id].ended_at,
            )
            if value
        ]
    )
    run_envs = [collector.evidence_runs[trace.evidence_run_id].environment for trace in traces]
    sessions = sorted(
        {
            str(value)
            for env in run_envs
            for key in ("external_agent_session_id", "ai_player_session_id")
            if (value := env.get(key))
        }
    )
    models = sorted(
        {
            (str(env.get("provider") or "unreported"), str(env.get("model_selector") or "unreported"))
            for env in run_envs
        }
    )
    manifest = _seal_document(
        GameplayBundleManifestV1,
        {
            "schema": "game-observatory.ai-player.gameplay-bundle-manifest.v1",
            "bundle_id": f"gameplay-bundle.{environment.game_id}.{bundle_root[:24]}",
            "environment": _model_payload(environment),
            "source_scope": {
                "kind": "gameplay_candidate" if candidate else "explicit_scope",
                "gameplay_candidate_id": candidate.id if candidate else None,
                "state_ids": sorted(state.id for state in states),
                "transition_edge_ids": sorted(edge.id for edge in edges),
                "task_ids": sorted(tasks),
            },
            "source_time_range": {"started_at": all_times[0], "ended_at": all_times[-1]},
            "sessions": [{"session_id": value} for value in sessions],
            "models": [
                {"provider": provider, "model_selector": model}
                for provider, model in models
            ],
            "config": {
                "build_scope_id": environment.build_scope_id,
                "account_scope_id": environment.account_scope_id,
                "server_scope_id": environment.server_scope_id,
                "world_scope_id": environment.world_scope_id,
                "device_scope_id": environment.device_scope_id,
                "channel": environment.channel,
                "locale": environment.locale,
                "viewport": [environment.viewport_width, environment.viewport_height],
            },
            "files": file_refs,
            "bundle_root_sha256": bundle_root,
            "truth_status": "candidate_evidence_only",
        },
    )
    manifest_bytes = _document_bytes(manifest)
    files = {"bundle_manifest.json": manifest_bytes, **files_without_manifest}
    if set(files) != set(_REQUIRED_FILENAMES):
        raise AssertionError("gameplay collection bundle file set is incomplete")
    reused = _commit_bundle(output_dir, files)
    return GameplayCollectionExportResult(
        bundle_id=manifest.bundle_id,
        output_dir=output_dir.resolve(),
        manifest_sha256=_sha256(manifest_bytes),
        file_sha256={name: _sha256(content) for name, content in sorted(files.items())},
        idempotent_reuse=reused,
    )


__all__ = [
    "GameplayBundleManifestV1",
    "GameplayCandidatesExportV1",
    "GameplayCollectionExportError",
    "GameplayCollectionExportResult",
    "GameplayCoverageV1",
    "GameplayEvidenceIndexV1",
    "GameplayInteractionTraceV1",
    "GameplayInterfacesV1",
    "GameplaySkillsExportV1",
    "GameplayStateMapV1",
    "export_gameplay_collection_bundle",
]
