"""Run one bounded, expectation-driven AI-player action through DeviceGateway.

This is the thin physical execution entry used by gameplay workers.  Planning stays
outside the process and is supplied as a persisted JSON request; the command performs
exactly one action, records canonical evidence, checks the declared visual result, and
releases the device lease.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..evidence import (
    EvidenceRecorder,
    perceptual_frame_distance,
    regional_perceptual_frame_distance,
    regional_structural_frame_distance,
)
from ..gateway import DeviceGateway
from ..models import (
    DeviceLease,
    EvidenceDynamicSceneProfile,
    EvidenceLiveEvaluation,
    EvidenceStep,
    EvidenceTargetEffectEvaluation,
    EvidenceTerminalCondition,
    NormalizedAction,
    SourcePixelRect,
)
from ..runtime import GameObservatory
from .account_policy import AccountActionIntentV1, evaluate_account_action
from .contracts import EvidenceReferenceV1, FrontierTaskV1, SemanticSurfaceProfileV1
from .external_agent_runtime import (
    EXTERNAL_AGENT_INVOCATION_ID_ENV,
    EXTERNAL_AGENT_INVOCATION_SEQUENCE_ENV,
    EXTERNAL_AGENT_SESSION_ID_ENV,
)
from .session_control import (
    AIPlayerSessionCommand,
    AIPlayerSessionControl,
    CreateAIPlayerSessionCommand,
)
from .semantic_surface_profiles import attach_semantic_surface_profiles
from .store import AIPlayerStore


_POINTER_ACTION_TYPES = {
    "tap",
    "swipe",
    "pinch",
    "two_finger_swipe",
    "mouse_move",
    "mouse_button",
}

_TARGET_EFFECT_PADDING_RATIO = 0.03
_TARGET_EFFECT_MIN_PADDING_PIXELS = 16
_TARGET_EFFECT_MAX_PADDING_PIXELS = 64
_TARGET_EFFECT_MIN_STRUCTURAL_DISTANCE = 0.04
_TARGET_EFFECT_STRONG_VISUAL_DISTANCE = 0.08
_TARGET_EFFECT_STRONG_VISUAL_MULTIPLIER = 3.0
_TARGET_EFFECT_STRONG_VISUAL_MIN_STRUCTURAL_DISTANCE = 0.03
TARGET_EFFECT_EVALUATOR_VERSION = "target-aware-live-effect.v2"
_STATIC_SETTLE_TIMEOUT_ISSUE = "after state did not settle before timeout"
_BOUNDED_MOTION_RECLASSIFICATION_ADVISORY = (
    "static settle reclassified by bounded motion plus passed target effect"
)


@dataclass
class LiveStepRouteRuntime:
    """Reuse one facility, gateway, and device lease across a known route.

    Evidence runs remain action-scoped.  The reusable boundary only removes
    repeated process-local setup and lease churn between already-known actions.
    """

    facility: GameObservatory
    player: AIPlayerStore
    gateway: DeviceGateway
    lease: DeviceLease | None = None
    target_id: str | None = None
    holder: str | None = None
    environment_id: str | None = None
    session_id: str | None = None
    terminal_after_artifact_id: str | None = None
    last_released_lease: DeviceLease | None = None
    closed: bool = False

    @classmethod
    def create(
        cls,
        *,
        root: Path | None = None,
        facility: GameObservatory | None = None,
        player: AIPlayerStore | None = None,
    ) -> "LiveStepRouteRuntime":
        resolved_facility = facility or GameObservatory(root)
        resolved_player = player or AIPlayerStore(resolved_facility.store)
        return cls(
            facility=resolved_facility,
            player=resolved_player,
            gateway=resolved_facility.device_gateway(),
        )

    def ensure_lease(
        self,
        request: "LiveStepRequestV1",
        *,
        owner_context: dict[str, object],
    ) -> DeviceLease:
        if self.closed:
            raise ValueError("live-step route runtime is already closed")
        if self.lease is not None:
            expected = (
                self.target_id,
                self.holder,
                self.environment_id,
                self.session_id,
            )
            actual = (
                request.target_id,
                request.holder,
                request.environment_id,
                request.session_id,
            )
            if actual != expected:
                raise ValueError("live-step route runtime cannot cross target or session")
            self.lease = self.gateway.validate(request.target_id, self.lease.token)
            return self.lease

        self.gateway.refresh(target_id=request.target_id)
        target = self.facility.store.get_target(request.target_id)
        if target is None or target.status != "online":
            raise ValueError(f"target is not online: {request.target_id}")
        self.lease = self.gateway.acquire(
            request.target_id,
            request.holder,
            ttl_seconds=request.lease_ttl_seconds,
            owner_context=owner_context,
        )
        self.target_id = request.target_id
        self.holder = request.holder
        self.environment_id = request.environment_id
        self.session_id = request.session_id
        return self.lease

    def yield_lease(self) -> DeviceLease | None:
        """Release request-scoped device ownership while keeping warm adapters.

        A route may contain several fixed skills, so the lease remains shared
        until the outer navigate request ends.  Independent CLI requests must
        then be able to acquire the device immediately.  Terminal frame reuse
        is reset with the lease because another command may change the screen
        before this runtime serves its next request.
        """

        if self.closed:
            return self.last_released_lease
        released = None
        if self.lease is not None:
            released = self.gateway.release(self.lease.token)
            self.last_released_lease = released
        self.lease = None
        self.target_id = None
        self.holder = None
        self.environment_id = None
        self.session_id = None
        self.terminal_after_artifact_id = None
        return released

    def close(self) -> DeviceLease | None:
        if self.closed:
            return self.last_released_lease
        released = self.yield_lease()
        self.closed = True
        return released


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)


class LiveStepExpectationV1(_StrictModel):
    summary: str = Field(min_length=1)
    kind: Literal["visual_change", "visual_no_change"]
    min_visual_distance: float = Field(default=0.02, ge=0, le=1)
    region: SourcePixelRect | None = None
    stop_conditions: list[str] = Field(min_length=1)


class LiveStepRequestV1(_StrictModel):
    schema_id: Literal["game-observatory.ai-player.live-step-request.v1"] = Field(
        default="game-observatory.ai-player.live-step-request.v1",
        alias="schema",
    )
    target_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    task_id: str | None = Field(default=None, min_length=1)
    bootstrap_session: bool = False
    initial_evidence: EvidenceReferenceV1 | None = None
    viewport_width: int = Field(gt=0)
    viewport_height: int = Field(gt=0)
    game_id: str = Field(min_length=1)
    build_scope_id: str = Field(min_length=1)
    action: NormalizedAction
    target_name: str | None = Field(default=None, min_length=1)
    target_bounds: SourcePixelRect | None = None
    account_action_intent: AccountActionIntentV1
    expectation: LiveStepExpectationV1
    actor: str = Field(default="ai-player-live-step", min_length=1)
    holder: str = Field(default="ai-player-live-step", min_length=1)
    lease_ttl_seconds: int = Field(default=120, ge=30, le=300)
    max_runtime_seconds: int = Field(default=60, ge=10, le=60)
    settle_threshold: float = Field(default=0.01, gt=0, le=1)
    required_consecutive: int = Field(default=2, ge=1, le=4)
    settle_timeout_seconds: float = Field(default=4.0, gt=0, le=10)
    sample_interval_seconds: float = Field(default=0.25, gt=0, le=2)
    capture_profile: Literal["full", "compact_static"] = "compact_static"
    source_state_max_visual_distance: float = Field(default=0.08, gt=0, le=1)
    source_semantic_state_id: str | None = Field(default=None, min_length=1)
    source_state_observation_id: str | None = Field(default=None, min_length=1)
    expected_semantic_state_id: str | None = Field(default=None, min_length=1)
    skill_replay_version_id: str | None = Field(default=None, min_length=1)
    expected_state_max_visual_distance: float = Field(default=0.012, gt=0, le=0.03)
    expected_state_reference_artifact_id: str | None = Field(default=None, min_length=1)
    dynamic_scene_profile: EvidenceDynamicSceneProfile | None = None
    locator_resolution: dict[str, Any] | None = None
    before_surface_profile: SemanticSurfaceProfileV1 | None = None
    after_surface_profile: SemanticSurfaceProfileV1 | None = None
    defer_semantic_sedimentation: bool = False

    @model_validator(mode="after")
    def keep_pointer_geometry_complete(self) -> "LiveStepRequestV1":
        if self.action.type in _POINTER_ACTION_TYPES and self.target_bounds is None:
            raise ValueError("pointer live step requires target_bounds")
        if self.account_action_intent.involves_real_money:
            raise ValueError("live step cannot execute real-money actions")
        if self.account_action_intent.submits_external_personal_identity:
            raise ValueError("live step cannot submit external personal identity")
        if self.dynamic_scene_profile is not None and self.expectation.kind != "visual_change":
            raise ValueError("dynamic live step requires a visual-change expectation")
        if self.dynamic_scene_profile is not None and (
            self.action.type not in _POINTER_ACTION_TYPES or self.target_bounds is None
        ):
            raise ValueError("dynamic live step requires a bounded pointer target")
        if self.bootstrap_session and (self.task_id is None or self.initial_evidence is None):
            raise ValueError("session bootstrap requires task_id and initial_evidence")
        if (
            self.initial_evidence is not None
            and self.initial_evidence.environment_id != self.environment_id
        ):
            raise ValueError("initial evidence belongs to another environment")
        if (self.source_semantic_state_id is None) != (self.source_state_observation_id is None):
            raise ValueError(
                "source semantic state and source observation must be supplied together"
            )
        if self.source_semantic_state_id is not None and self.skill_replay_version_id is None:
            raise ValueError("direct source-state binding is restricted to skill replay")
        if (
            self.expected_state_reference_artifact_id is not None
            and self.expected_semantic_state_id is None
        ):
            raise ValueError("expected-state reference requires an expected semantic state")
        if self.locator_resolution is not None and self.skill_replay_version_id is None:
            raise ValueError("locator resolution provenance requires a skill replay version")
        if (self.before_surface_profile is None) != (self.after_surface_profile is None):
            raise ValueError("semantic surface profiles must describe both before and after roles")
        return self


def _target_effect_bounds(
    target_bounds: SourcePixelRect,
    *,
    viewport_width: int,
    viewport_height: int,
) -> tuple[SourcePixelRect, int]:
    padding = max(
        _TARGET_EFFECT_MIN_PADDING_PIXELS,
        min(
            _TARGET_EFFECT_MAX_PADDING_PIXELS,
            round(min(viewport_width, viewport_height) * _TARGET_EFFECT_PADDING_RATIO),
        ),
    )
    left = max(0, target_bounds.x - padding)
    top = max(0, target_bounds.y - padding)
    right = min(viewport_width, target_bounds.x + target_bounds.width + padding)
    bottom = min(viewport_height, target_bounds.y + target_bounds.height + padding)
    return (
        SourcePixelRect(
            x=left,
            y=top,
            width=right - left,
            height=bottom - top,
        ),
        padding,
    )


def _target_effect_min_structural_distance(
    *,
    visual_distance: float,
    min_visual_distance: float,
) -> float:
    strong_visual_threshold = max(
        _TARGET_EFFECT_STRONG_VISUAL_DISTANCE,
        min_visual_distance * _TARGET_EFFECT_STRONG_VISUAL_MULTIPLIER,
    )
    if visual_distance >= strong_visual_threshold:
        return _TARGET_EFFECT_STRONG_VISUAL_MIN_STRUCTURAL_DISTANCE
    return _TARGET_EFFECT_MIN_STRUCTURAL_DISTANCE


def evaluate_live_effect(
    *,
    before_path: str | Path,
    after_path: str | Path,
    expectation: LiveStepExpectationV1,
    target_bounds: SourcePixelRect | None,
    viewport_width: int,
    viewport_height: int,
    elapsed_seconds: float,
    runtime_limit_seconds: int,
) -> EvidenceLiveEvaluation:
    """Measure the declared visual effect globally and around a bounded target."""

    global_visual_distance = round(
        perceptual_frame_distance(before_path, after_path),
        6,
    )
    visual_distance = (
        global_visual_distance
        if expectation.region is None
        else round(
            regional_perceptual_frame_distance(
                before_path,
                after_path,
                expectation.region,
            ),
            6,
        )
    )
    target_effect = None
    if target_bounds is not None:
        evaluation_bounds, padding = _target_effect_bounds(
            target_bounds,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
        )
        local_visual_distance = round(
            regional_perceptual_frame_distance(
                before_path,
                after_path,
                evaluation_bounds,
            ),
            6,
        )
        local_structural_distance = round(
            regional_structural_frame_distance(
                before_path,
                after_path,
                evaluation_bounds,
            ),
            6,
        )
        min_structural_distance = _target_effect_min_structural_distance(
            visual_distance=local_visual_distance,
            min_visual_distance=expectation.min_visual_distance,
        )
        target_effect = EvidenceTargetEffectEvaluation(
            target_bounds=target_bounds,
            evaluation_bounds=evaluation_bounds,
            padding_pixels=padding,
            visual_distance=local_visual_distance,
            structural_distance=local_structural_distance,
            min_visual_distance=expectation.min_visual_distance,
            min_structural_distance=min_structural_distance,
            passed=(
                local_visual_distance >= expectation.min_visual_distance
                and local_structural_distance >= min_structural_distance
            ),
        )

    primary_change = visual_distance >= expectation.min_visual_distance
    local_change = bool(target_effect and target_effect.passed)
    observed_change = primary_change or local_change
    expectation_met = (
        observed_change if expectation.kind == "visual_change" else not observed_change
    )
    if not expectation_met:
        evaluation_source = "unmet"
    elif expectation.kind == "visual_no_change":
        evaluation_source = "visual_no_change"
    elif primary_change:
        evaluation_source = "primary_visual_distance"
    elif local_change:
        evaluation_source = "target_context_visual_and_structure"
    else:  # pragma: no cover - expectation_met proves one of the branches above
        raise RuntimeError("live-effect verdict has no successful evaluation source")
    return EvidenceLiveEvaluation(
        expectation_met=expectation_met,
        stop_recommended=not expectation_met,
        visual_distance=visual_distance,
        expected_min_visual_distance=expectation.min_visual_distance,
        elapsed_seconds=round(elapsed_seconds, 3),
        runtime_limit_seconds=runtime_limit_seconds,
        global_visual_distance=global_visual_distance,
        evaluation_region=expectation.region,
        target_effect=target_effect,
        evaluation_source=evaluation_source,
        evaluator_version=TARGET_EFFECT_EVALUATOR_VERSION,
    )


def _promote_bounded_motion_terminal_if_proven(
    step: EvidenceStep,
    evaluation: EvidenceLiveEvaluation,
) -> EvidenceStep:
    """Recover a successful local action from a static-settle false negative.

    The fallback is deliberately narrow: the action must have no execution error,
    the target-local visual and structural effect must pass, and the final probe
    window must remain within a bounded-motion policy.  Global motion alone never
    promotes a failed action.
    """

    if (
        step.status != "failed"
        or step.error is not None
        or step.metadata.get("capture_profile") != "compact_static"
        or step.quality_issues != [_STATIC_SETTLE_TIMEOUT_ISSUE]
        or not evaluation.expectation_met
        or evaluation.target_effect is None
        or not evaluation.target_effect.passed
    ):
        return step

    profile = EvidenceDynamicSceneProfile(
        max_inlier_frame_distance=0.12,
        analysis_window_frames=5,
        required_inlier_ratio=0.80,
    )
    window = step.stability.sample_distances[-profile.analysis_window_frames :]
    if len(window) < profile.analysis_window_frames or not all(
        math.isfinite(distance) for distance in window
    ):
        return step
    inlier_count = sum(distance <= profile.max_inlier_frame_distance for distance in window)
    required_inliers = math.ceil(profile.analysis_window_frames * profile.required_inlier_ratio)
    if inlier_count < required_inliers:
        return step

    stability = step.stability.model_copy(
        update={
            "profile": "bounded-motion-terminal",
            "dynamic_scene_profile": profile,
            "analysis_window_distances": window,
            "analysis_inlier_count": inlier_count,
            "analysis_required_inliers": required_inliers,
            "settled": True,
        }
    )
    return step.model_copy(
        update={
            "status": "passed",
            "stability": stability,
            "quality_issues": [],
            "quality_advisories": [
                *step.quality_advisories,
                _BOUNDED_MOTION_RECLASSIFICATION_ADVISORY,
            ],
        }
    )


def _stable_command_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"command.live-step.{prefix}.{digest}"


def _bootstrap_live_turn(
    request: LiveStepRequestV1,
    player: AIPlayerStore,
) -> None:
    if not request.bootstrap_session:
        return
    if request.task_id is None or request.initial_evidence is None:
        raise ValueError("session bootstrap contract is incomplete")
    sessions = AIPlayerSessionControl(player)
    existing = sessions.get_session(request.environment_id, request.session_id)
    if existing is not None:
        return
    task = player.get_task(request.environment_id, request.task_id)
    if task is None:
        task = player.enqueue_task(
            FrontierTaskV1(
                id=request.task_id,
                environment_id=request.environment_id,
                title=request.target_name or request.expectation.summary,
                source="user_goal",
                reason="执行一个有预期、有终态检查的短回合。",
                action_budget=1,
                time_budget_seconds=float(request.max_runtime_seconds),
                evidence_refs=[request.initial_evidence],
            )
        )
    if task.status == "queued":
        task = player.compare_and_swap_task_status(
            request.environment_id,
            task.id,
            "queued",
            "active",
            expected_version=task.version,
        )
        if task is None:
            raise ValueError("live turn task changed while it was being activated")
    if task.status != "active":
        raise ValueError(f"live turn task is not active: {task.status}")
    created = sessions.create_session(
        CreateAIPlayerSessionCommand(
            command_id=_stable_command_id("create", request.session_id),
            requested_environment_id=request.environment_id,
            objective=request.expectation.summary,
            action_budget=1,
            time_budget_seconds=float(request.max_runtime_seconds + 30),
            active_task_ids=[task.id],
            last_evidence_refs=[request.initial_evidence],
            actor=request.actor,
            reason="为单动作实机回合自动建立会话。",
            session_id=request.session_id,
        )
    )
    sessions.start(
        created.id,
        AIPlayerSessionCommand(
            command_id=_stable_command_id("start", request.session_id),
            environment_id=request.environment_id,
            expected_version=created.version,
            actor=request.actor,
            reason="开始单动作实机回合。",
            lease_holder=request.holder,
            lease_ttl_seconds=request.lease_ttl_seconds,
        ),
    )


def _close_bootstrapped_live_turn(
    request: LiveStepRequestV1,
    player: AIPlayerStore,
    *,
    completed: bool,
) -> str | None:
    if not request.bootstrap_session:
        return None
    sessions = AIPlayerSessionControl(player)
    session = sessions.get_session(request.environment_id, request.session_id)
    if session is not None and session.state == "running":
        command = AIPlayerSessionCommand(
            command_id=_stable_command_id(
                "complete" if completed else "safe-stop",
                request.session_id,
            ),
            environment_id=request.environment_id,
            expected_version=session.version,
            actor=request.actor,
            reason=(
                "单动作回合完成并通过预期检查。"
                if completed
                else "单动作回合未通过预期检查，已安全停止。"
            ),
        )
        session = (
            sessions.complete(session.id, command)
            if completed
            else sessions.safe_stop(session.id, command)
        )
    if request.task_id is not None:
        task = player.get_task(request.environment_id, request.task_id)
        if task is not None and task.status == "active":
            player.compare_and_swap_task_status(
                request.environment_id,
                task.id,
                "active",
                "completed" if completed else "failed",
                expected_version=task.version,
            )
    return session.state if session is not None else None


def _configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def _artifact_bytes(facility: GameObservatory, artifact_ids: list[str]) -> int:
    total = 0
    for artifact_id in artifact_ids:
        artifact = facility.store.get_artifact(artifact_id)
        if artifact is not None and Path(artifact.path).is_file():
            total += os.path.getsize(artifact.path)
    return total


def _settle_open_evidence_run_after_exception(
    facility: GameObservatory,
    evidence_run_id: str | None,
    error: Exception,
) -> None:
    """Close an action-scoped run when execution exits before manifestation."""

    if evidence_run_id is None:
        return
    run = facility.store.get_evidence_run(evidence_run_id)
    if run is None or run.status not in {"running", "paused"}:
        return
    reason = (
        f"AI player live step aborted before evidence completion: {type(error).__name__}: {error}"
    )
    # Keep the canonical ledger finite even if an adapter or a guard raises
    # between start_evidence_run and complete_evidence_run.  Process-kill cases
    # remain covered by invocation reconciliation; this closes the normal
    # in-process exception path immediately.
    EvidenceRecorder(facility.store, None).stop_run(
        evidence_run_id,
        reason=reason[:1000],
    )


def _route_boundary_before_artifact_id(
    request: LiveStepRequestV1,
    runtime: LiveStepRouteRuntime,
    *,
    owns_route_runtime: bool,
) -> str | None:
    """Reuse a prior After only when the next capture contract permits it."""

    if (
        owns_route_runtime
        or request.capture_profile != "compact_static"
        or request.dynamic_scene_profile is not None
    ):
        return None
    return runtime.terminal_after_artifact_id


def run_live_step(
    request: LiveStepRequestV1,
    *,
    root: Path | None = None,
    route_runtime: LiveStepRouteRuntime | None = None,
) -> dict[str, object]:
    started = time.perf_counter()
    owns_route_runtime = route_runtime is None
    runtime = route_runtime or LiveStepRouteRuntime.create(root=root)
    facility = runtime.facility
    player = runtime.player
    environment = player.get_environment(request.environment_id)
    if environment is None:
        raise ValueError(f"unknown AI-player environment: {request.environment_id}")
    policy = player.get_account_policy(request.environment_id)
    if policy is None:
        raise ValueError("current environment has no account action policy")
    account_decision = evaluate_account_action(request.account_action_intent, policy)
    if account_decision.disposition != "autonomous":
        raise ValueError(f"account action is not autonomous: {account_decision.reason}")

    sessions = AIPlayerSessionControl(player)
    session = sessions.assert_session_can_act(request.environment_id, request.session_id)
    if session.environment_id != request.environment_id:
        raise ValueError("AI-player session belongs to another environment")

    external_invocation_id = os.environ.get(EXTERNAL_AGENT_INVOCATION_ID_ENV)
    external_session_id = os.environ.get(EXTERNAL_AGENT_SESSION_ID_ENV)
    external_sequence = os.environ.get(EXTERNAL_AGENT_INVOCATION_SEQUENCE_ENV)
    if external_invocation_id and (
        external_session_id != request.session_id or not external_sequence
    ):
        raise ValueError("external live step invocation ownership is incomplete")
    if external_invocation_id and request.task_id is None:
        raise ValueError("external live step requires one canonical task binding")
    if request.task_id is not None:
        task = player.get_task(request.environment_id, request.task_id)
        if task is None:
            raise ValueError(f"live step task is missing: {request.task_id}")
        if request.task_id not in session.active_task_ids:
            raise ValueError("live step task is outside the canonical session")
        if task.status != "active":
            raise ValueError(f"live step task is not active: {task.status}")

    gateway = runtime.gateway

    external_owner_context = (
        {
            "external_agent_invocation_id": external_invocation_id,
            "external_agent_session_id": external_session_id,
            "external_agent_invocation_sequence": external_sequence,
        }
        if external_invocation_id
        and external_session_id == request.session_id
        and external_sequence
        else {}
    )
    opened = None
    manifest = None
    source_state_guard = None
    if request.action.type in _POINTER_ACTION_TYPES:
        source_artifact_ids = (
            request.initial_evidence.artifact_ids if request.initial_evidence is not None else []
        )
        if not source_artifact_ids:
            raise ValueError("pointer live step requires a source-state artifact")
        source_artifact = facility.store.get_artifact(source_artifact_ids[-1])
        if source_artifact is None:
            raise ValueError("pointer live step source-state artifact is missing")
        source_state_guard = {
            "artifact_id": source_artifact.id,
            "artifact_sha256": source_artifact.sha256,
            "max_visual_distance": request.source_state_max_visual_distance,
        }
        if request.source_semantic_state_id is not None:
            source_state_guard.update(
                {
                    "semantic_state_id": request.source_semantic_state_id,
                    "observation_id": request.source_state_observation_id,
                    "binding_method": "verified_skill_source_assignment",
                }
            )
        if request.dynamic_scene_profile is not None:
            source_state_guard["dynamic_target_guard"] = {
                "target_bounds": request.target_bounds.model_dump(mode="json"),
                "max_global_visual_distance": 0.40,
                "max_target_visual_distance": 0.08,
                "max_context_structural_distance": 0.08,
                "context_padding_pixels": 48,
            }
    terminal_condition = None
    if request.dynamic_scene_profile is not None:
        terminal_region = request.expectation.region
        if terminal_region is None and request.target_bounds is not None:
            terminal_region, _padding = _target_effect_bounds(
                request.target_bounds,
                viewport_width=request.viewport_width,
                viewport_height=request.viewport_height,
            )
        terminal_condition = EvidenceTerminalCondition(
            min_observation_seconds=(
                request.dynamic_scene_profile.analysis_window_frames
                * request.sample_interval_seconds
            ),
            min_visual_change_from_before=request.expectation.min_visual_distance,
            region=terminal_region,
        )
    lease = runtime.ensure_lease(
        request,
        owner_context={
            "environment_id": request.environment_id,
            "ai_player_session_id": request.session_id,
            "task_id": request.task_id,
            **external_owner_context,
        },
    )
    released = None
    try:
        opened = gateway.start_evidence_run(
            request.target_id,
            lease.token,
            viewport_width=request.viewport_width,
            viewport_height=request.viewport_height,
            game_id=request.game_id,
            build_scope_id=request.build_scope_id,
            scope_id=request.environment_id,
            environment={
                "caller": "ai-player-live-step",
                "environment_id": request.environment_id,
                "ai_player_session_id": request.session_id,
                "task_id": request.task_id,
                "actor": request.actor,
                "pre_execution_expectation": request.expectation.summary,
                "expected_change_kind": request.expectation.kind,
                "expected_min_visual_distance": request.expectation.min_visual_distance,
                "stop_conditions": request.expectation.stop_conditions,
                "account_action_intent": request.account_action_intent.model_dump(mode="json"),
                "max_runtime_seconds": request.max_runtime_seconds,
                "source_state_guard": source_state_guard,
                "expected_semantic_state_id": request.expected_semantic_state_id,
                "skill_replay_version_id": request.skill_replay_version_id,
                "locator_resolution": request.locator_resolution,
                "defer_semantic_sedimentation": request.defer_semantic_sedimentation,
                "expected_state_max_visual_distance": (request.expected_state_max_visual_distance),
                "expected_state_reference_artifact_id": (
                    request.expected_state_reference_artifact_id
                ),
                "expected_state_target_bounds": (
                    request.target_bounds.model_dump(mode="json")
                    if request.expected_semantic_state_id is not None
                    and request.target_bounds is not None
                    else None
                ),
                "device_gateway_lease_id": lease.id,
                "device_gateway_lease_holder": lease.holder,
                **external_owner_context,
            },
        )
        step = gateway.record_evidence_step(
            opened.id,
            lease.token,
            request.action,
            target_name=request.target_name,
            target_bounds=request.target_bounds,
            settle_threshold=request.settle_threshold,
            required_consecutive=request.required_consecutive,
            settle_timeout_seconds=request.settle_timeout_seconds,
            sample_interval_seconds=request.sample_interval_seconds,
            terminal_condition=terminal_condition,
            dynamic_scene_profile=request.dynamic_scene_profile,
            capture_profile=request.capture_profile,
            action_task_id=request.task_id,
            reused_before_artifact_id=_route_boundary_before_artifact_id(
                request,
                runtime,
                owns_route_runtime=owns_route_runtime,
            ),
            trusted_terminal_reference_artifact_id=(request.expected_state_reference_artifact_id),
            trusted_terminal_max_visual_distance=(request.expected_state_max_visual_distance),
        )
        before = facility.store.get_artifact(str(step.before_frame_id))
        after = facility.store.get_artifact(str(step.after_frame_id))
        if before is None or after is None:
            details = step.error or "; ".join(step.quality_issues) or "unknown capture failure"
            raise ValueError(
                "terminal live step lacks before/after screenshot artifacts: "
                f"step={step.id}; {details}"
            )
        live_evaluation = evaluate_live_effect(
            before_path=before.path,
            after_path=after.path,
            expectation=request.expectation,
            target_bounds=request.target_bounds,
            viewport_width=request.viewport_width,
            viewport_height=request.viewport_height,
            elapsed_seconds=time.perf_counter() - started,
            runtime_limit_seconds=request.max_runtime_seconds,
        )
        metadata = dict(step.metadata)
        if request.locator_resolution is not None:
            metadata["locator_resolution"] = request.locator_resolution
        step = step.model_copy(update={"live_evaluation": live_evaluation, "metadata": metadata})
        if request.before_surface_profile is not None and request.after_surface_profile is not None:
            step = attach_semantic_surface_profiles(
                step,
                before=request.before_surface_profile,
                after=request.after_surface_profile,
                producer="game-observatory.ai-player.live-step-request.v1",
                actor=request.actor,
            )
        step = _promote_bounded_motion_terminal_if_proven(step, live_evaluation)
        facility.store.save_evidence_step(step)
        manifest = gateway.complete_evidence_run(opened.id, lease.token)
        if not owns_route_runtime and step.status == "passed":
            runtime.terminal_after_artifact_id = str(step.after_frame_id)
    except Exception as exc:
        try:
            _settle_open_evidence_run_after_exception(
                facility,
                opened.id if opened is not None else None,
                exc,
            )
        except Exception:  # noqa: BLE001 - never hide the primary action failure
            pass
        raise
    finally:
        if owns_route_runtime:
            released = runtime.close()

    current_session = sessions.get_session(request.environment_id, request.session_id)
    artifacts = [facility.store.get_artifact(artifact_id) for artifact_id in step.artifact_ids]
    artifact_kinds: dict[str, int] = {}
    for artifact in artifacts:
        if artifact is not None:
            artifact_kinds[artifact.kind] = artifact_kinds.get(artifact.kind, 0) + 1
    execution_completed = step.status != "running" and manifest is not None
    result_ok = bool(
        execution_completed
        and step.status == "passed"
        and manifest.publishable
        and live_evaluation.expectation_met
        and live_evaluation.elapsed_seconds <= request.max_runtime_seconds
    )
    return {
        "schema": "game-observatory.ai-player.live-step-result.v1",
        "ok": result_ok,
        "execution_completed": execution_completed,
        "expectation_met": live_evaluation.expectation_met,
        "stop_recommended": live_evaluation.stop_recommended,
        "elapsed_seconds": live_evaluation.elapsed_seconds,
        "runtime_limit_seconds": request.max_runtime_seconds,
        "visual_distance": live_evaluation.visual_distance,
        "global_visual_distance": live_evaluation.global_visual_distance,
        "expected_min_visual_distance": live_evaluation.expected_min_visual_distance,
        "evaluation_source": live_evaluation.evaluation_source,
        "effect_scope": live_evaluation.effect_scope,
        "target_effect": (
            live_evaluation.target_effect.model_dump(mode="json")
            if live_evaluation.target_effect is not None
            else None
        ),
        "account_disposition": account_decision.disposition,
        "evidence_run_id": opened.id if opened is not None else None,
        "evidence_step_id": step.id,
        "action_run_id": step.action_run_id,
        "before_artifact_id": step.before_frame_id,
        "after_artifact_id": step.after_frame_id,
        "video_artifact_id": step.video_artifact_id,
        "artifact_count": len(step.artifact_ids),
        "artifact_kinds": artifact_kinds,
        "artifact_bytes": _artifact_bytes(facility, step.artifact_ids),
        "capture_profile": request.capture_profile,
        "locator_resolution": request.locator_resolution,
        "semantic_surface_profiles_persisted": (request.before_surface_profile is not None),
        "step_status": step.status,
        "manifest_publishable": manifest.publishable,
        "publication_issues": manifest.publication_issues,
        "session_remaining_actions": (
            current_session.remaining_action_budget if current_session is not None else None
        ),
        "task_id": request.task_id,
        "device_lease_status": (released or lease).status,
    }


def run_live_turn(
    request: LiveStepRequestV1,
    *,
    root: Path | None = None,
) -> dict[str, object]:
    """Bootstrap, execute, and close one short-lived gameplay turn."""

    facility = GameObservatory(root)
    player = AIPlayerStore(facility.store)
    _bootstrap_live_turn(request, player)
    try:
        result = run_live_step(request, root=root)
    except Exception:
        _close_bootstrapped_live_turn(request, player, completed=False)
        raise
    session_state = _close_bootstrapped_live_turn(
        request,
        player,
        completed=bool(result["ok"]),
    )
    result["session_bootstrapped"] = request.bootstrap_session
    result["terminal_session_state"] = session_state
    result["task_id"] = request.task_id
    return result


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_stdio()
    parser = argparse.ArgumentParser(prog="game-observatory-ai-player-live-step")
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--root", type=Path)
    args = parser.parse_args(argv)
    request = LiveStepRequestV1.model_validate_json(args.file.read_text(encoding="utf-8"))
    result = run_live_turn(request, root=args.root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
