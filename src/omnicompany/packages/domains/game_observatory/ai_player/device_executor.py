"""DeviceGateway-backed executor for one pre-reserved autonomous action."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..gateway import DeviceGateway, GatewayError, PreReservedAIPlayerActionV1
from ..models import (
    ArtifactRef,
    EvidenceDynamicSceneProfile,
    EvidenceRun,
    EvidenceRunManifest,
    EvidenceStep,
    EvidenceTerminalCondition,
    RunResult,
    SourcePixelRect,
)
from .consolidation import CanonicalExecutionOutcomeV1
from .contracts import SemanticStateV1, SemanticSurfaceProfileV1
from .interaction_preflight import (
    InteractionExpectedChangeV1,
    InteractionPreflightError,
    validate_interaction_preflight,
)
from .orchestrator import (
    AutonomousExecutionCommandV1,
    AutonomousExecutorRequestV1,
    ExecutorPreflightReceiptV1,
    autonomous_request_sha256,
)
from .semantic_surface_profiles import attach_semantic_surface_profiles


class DeviceExecutorError(RuntimeError):
    pass


class StateResolutionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    before_state: SemanticStateV1
    after_state: SemanticStateV1
    observed_change: str = Field(min_length=1)


class StateResolver(Protocol):
    """Resolve semantic state only after the evidence bundle is terminal."""

    def resolve(
        self,
        *,
        request: AutonomousExecutorRequestV1,
        evidence_run: EvidenceRun,
        evidence_step: EvidenceStep,
        artifacts: list[ArtifactRef],
        action_run: RunResult,
    ) -> StateResolutionV1: ...


class DeviceExecutorConfigV1(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    target_id: str = Field(min_length=1)
    lease_token: str = Field(min_length=1)
    viewport_width: int = Field(gt=0)
    viewport_height: int = Field(gt=0)
    game_id: str | None = None
    build_scope_id: str | None = None
    environment: dict[str, Any] = Field(default_factory=dict)
    target_name: str | None = None
    target_bounds: SourcePixelRect | None = None
    settle_threshold: float = 0.01
    required_consecutive: int = Field(default=2, ge=1)
    settle_timeout_seconds: float = Field(default=4.0, gt=0)
    sample_interval_seconds: float = Field(default=0.25, gt=0)
    terminal_condition: EvidenceTerminalCondition | None = None
    dynamic_scene_profile: EvidenceDynamicSceneProfile | None = None
    capture_profile: Literal["full", "compact_static"] = "full"
    before_surface_profile: SemanticSurfaceProfileV1 | None = None
    after_surface_profile: SemanticSurfaceProfileV1 | None = None

    @model_validator(mode="after")
    def require_complete_surface_profile_pair(self) -> "DeviceExecutorConfigV1":
        if (self.before_surface_profile is None) != (self.after_surface_profile is None):
            raise ValueError(
                "device executor semantic surface profiles require both before and after"
            )
        return self


def _request_sha256(request: AutonomousExecutorRequestV1) -> str:
    return autonomous_request_sha256(request.command)


class DeviceExecutor:
    """Execute one action through the Gateway and return a canonical evidence bundle."""

    def __init__(
        self,
        gateway: DeviceGateway,
        state_resolver: StateResolver,
        config: DeviceExecutorConfigV1,
    ) -> None:
        self.gateway = gateway
        self.state_resolver = state_resolver
        self.config = config

    def _artifacts(self, artifact_ids: list[str]) -> list[ArtifactRef]:
        artifacts: list[ArtifactRef] = []
        for artifact_id in artifact_ids:
            artifact = self.gateway.store.get_artifact(artifact_id)
            if artifact is None:
                raise DeviceExecutorError(f"evidence artifact is missing: {artifact_id}")
            artifacts.append(artifact)
        if not artifacts:
            raise DeviceExecutorError("terminal evidence run contains no artifacts")
        return artifacts

    @staticmethod
    def _canonical_json(value: BaseModel) -> str:
        return json.dumps(
            value.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _ordered_unique(values: list[str | None]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value is not None))

    def _validate_terminal_bundle(
        self,
        *,
        opened_run_id: str,
        recorded: EvidenceStep,
        manifest: EvidenceRunManifest,
        environment_id: str,
    ) -> tuple[EvidenceRun, EvidenceStep, list[ArtifactRef], RunResult]:
        """Read and validate the complete canonical bundle without mutating the store."""

        stored_run = self.gateway.store.get_evidence_run(opened_run_id)
        if stored_run is None:
            raise DeviceExecutorError(f"terminal evidence run is missing: {opened_run_id}")
        stored_step = self.gateway.store.get_evidence_step(recorded.id)
        if stored_step is None:
            raise DeviceExecutorError(f"terminal evidence step is missing: {recorded.id}")
        if manifest.evidence_run_id != opened_run_id or stored_run.id != opened_run_id:
            raise DeviceExecutorError("manifest and stored run do not match the opened run")
        if stored_run.manifest_id != manifest.id:
            raise DeviceExecutorError("stored run manifest ID differs from the returned manifest")
        if self._canonical_json(manifest.run) != self._canonical_json(stored_run):
            raise DeviceExecutorError("manifest run differs from the canonical stored run")
        if recorded.id != stored_step.id or self._canonical_json(recorded) != self._canonical_json(
            stored_step
        ):
            raise DeviceExecutorError("recorded step differs from the canonical stored step")
        manifest_steps = [item for item in manifest.steps if item.id == stored_step.id]
        if len(manifest.steps) != 1 or len(manifest_steps) != 1:
            raise DeviceExecutorError("manifest must contain exactly the recorded terminal step")
        if self._canonical_json(manifest_steps[0]) != self._canonical_json(stored_step):
            raise DeviceExecutorError("manifest step differs from the canonical stored step")
        if stored_run.step_ids != [stored_step.id]:
            raise DeviceExecutorError("stored run step list is not the single recorded step")
        if stored_step.evidence_run_id != stored_run.id:
            raise DeviceExecutorError("stored step belongs to a different evidence run")
        if not stored_step.action_run_id:
            raise DeviceExecutorError("terminal evidence step has no action run")
        if stored_run.action_run_ids != [stored_step.action_run_id]:
            raise DeviceExecutorError("stored run action list does not match the terminal step")
        if manifest.action_run_ids != stored_run.action_run_ids:
            raise DeviceExecutorError("manifest action list differs from the canonical stored run")
        if manifest.observation_run_ids != stored_run.observation_run_ids or (
            stored_run.observation_run_ids != stored_step.observation_run_ids
        ):
            raise DeviceExecutorError("observation run lists are not canonically aligned")
        if stored_run.status in {"running", "paused"} or stored_step.status == "running":
            raise DeviceExecutorError("evidence run and step must be terminal")

        action_run = self.gateway.store.get_run(stored_step.action_run_id)
        if action_run is None:
            raise DeviceExecutorError(f"action run is missing: {stored_step.action_run_id}")
        if action_run.id != stored_step.action_run_id:
            raise DeviceExecutorError("stored action run ID differs from the terminal step")
        if stored_run.target_id != self.config.target_id:
            raise DeviceExecutorError("stored run target differs from the configured target")
        try:
            allowed_action_targets = self.gateway.canonical_adapter_target_ids(stored_run.target_id)
        except GatewayError as exc:
            raise DeviceExecutorError(f"invalid Gateway target binding: {exc}") from exc
        if action_run.target_id not in allowed_action_targets:
            raise DeviceExecutorError("action run target is not bound by the Gateway target record")
        if action_run.status == "running":
            raise DeviceExecutorError("action run must be terminal")

        reference_artifact_id = (
            stored_step.terminal_condition.visual_reference_artifact_id
            if stored_step.terminal_condition is not None
            else None
        )
        declared_artifact_ids = self._ordered_unique(
            [
                stored_step.before_frame_id,
                stored_step.before_ui_tree_id,
                *stored_step.intermediate_frame_ids,
                stored_step.after_frame_id,
                stored_step.after_ui_tree_id,
                reference_artifact_id,
                stored_step.video_artifact_id,
            ]
        )
        canonical_artifact_ids = list(manifest.artifact_ids)
        if not canonical_artifact_ids or len(canonical_artifact_ids) != len(
            set(canonical_artifact_ids)
        ):
            raise DeviceExecutorError("manifest artifact list must be nonempty and unique")
        if canonical_artifact_ids != declared_artifact_ids:
            raise DeviceExecutorError(
                "manifest artifact order differs from the terminal step role order"
            )
        if stored_run.artifact_ids != canonical_artifact_ids:
            raise DeviceExecutorError("stored run artifact list differs from the manifest")
        if stored_step.artifact_ids != canonical_artifact_ids:
            raise DeviceExecutorError("stored step artifact list differs from the manifest")
        required_roles = [stored_step.before_frame_id, stored_step.after_frame_id]
        if any(value is None for value in required_roles) or len(set(required_roles)) != 2:
            raise DeviceExecutorError("before and after artifacts must be distinct")
        if self.config.capture_profile == "full":
            if stored_step.video_artifact_id is None or stored_step.video_artifact_id in set(
                required_roles
            ):
                raise DeviceExecutorError("full capture requires a distinct video artifact")
        elif stored_step.video_artifact_id is not None:
            raise DeviceExecutorError("compact static capture must not retain a video artifact")
        if stored_step.metadata.get("capture_profile") != self.config.capture_profile:
            raise DeviceExecutorError("terminal evidence capture profile differs from config")
        if action_run.artifact_ids and action_run.artifact_ids != canonical_artifact_ids:
            raise DeviceExecutorError(
                "action run artifact binding differs from the terminal evidence bundle"
            )

        artifacts = self._artifacts(canonical_artifact_ids)
        artifact_root = self.gateway.store.artifact_root.resolve()
        allowed_current_run_ids = {stored_run.id, *stored_step.observation_run_ids}
        for artifact in artifacts:
            path = Path(artifact.path).resolve()
            if path == artifact_root or artifact_root not in path.parents:
                raise DeviceExecutorError(f"artifact path escapes canonical root: {artifact.id}")
            if not path.is_file():
                raise DeviceExecutorError(f"artifact file is missing: {artifact.id}")
            if hashlib.sha256(path.read_bytes()).hexdigest() != artifact.sha256:
                raise DeviceExecutorError(f"artifact file hash differs from canonical row: {artifact.id}")
            is_reference = artifact.id == reference_artifact_id
            if artifact.run_id is None:
                raise DeviceExecutorError(f"artifact has no run binding: {artifact.id}")
            if not is_reference and artifact.run_id not in allowed_current_run_ids:
                raise DeviceExecutorError(
                    f"artifact run binding conflicts with the terminal run: {artifact.id}"
                )
            metadata_run_id = artifact.metadata.get("evidence_run_id")
            metadata_step_id = artifact.metadata.get("evidence_step_id")
            if not is_reference and metadata_run_id != stored_run.id:
                raise DeviceExecutorError(
                    f"artifact metadata names another evidence run: {artifact.id}"
                )
            if not is_reference and metadata_step_id != stored_step.id:
                raise DeviceExecutorError(
                    f"artifact metadata names another evidence step: {artifact.id}"
                )
            metadata_environment_id = artifact.metadata.get("environment_id")
            if metadata_environment_id not in {None, environment_id}:
                raise DeviceExecutorError(
                    f"artifact metadata names another environment: {artifact.id}"
                )
        return stored_run, stored_step, artifacts, action_run

    def _bind_artifacts(
        self,
        artifacts: list[ArtifactRef],
        environment_id: str,
    ) -> list[ArtifactRef]:
        bound_artifacts: list[ArtifactRef] = []
        for artifact in artifacts:
            bound = artifact.model_copy(
                update={
                    "metadata": {
                        **artifact.metadata,
                        "environment_id": environment_id,
                    }
                }
            )
            self.gateway.store.save_artifact(bound)
            bound_artifacts.append(bound)
        return bound_artifacts

    def _validate_interaction_before_gateway(
        self,
        command: AutonomousExecutionCommandV1,
        target_bounds: SourcePixelRect | None,
    ) -> None:
        pointer_action_types = {
            "tap",
            "swipe",
            "pinch",
            "two_finger_swipe",
            "mouse_move",
            "mouse_button",
        }
        if command.action.type not in pointer_action_types:
            return
        preflight = command.interaction_preflight
        if preflight is None:
            raise DeviceExecutorError(
                "pointer action requires an evidence-bound interaction preflight"
            )
        try:
            validate_interaction_preflight(
                preflight,
                action=command.action,
                target_bounds=target_bounds,
                viewport_width=self.config.viewport_width,
                viewport_height=self.config.viewport_height,
                environment_id=command.environment_id,
                source_artifact=self.gateway.store.get_artifact(preflight.source_artifact_id),
                local_evidence_artifact=self.gateway.store.get_artifact(
                    preflight.local_evidence_artifact_id
                ),
            )
        except (InteractionPreflightError, ValueError) as exc:
            raise DeviceExecutorError(f"interaction preflight rejected: {exc}") from exc

    def validate_before_reservation(
        self,
        command: AutonomousExecutionCommandV1,
    ) -> ExecutorPreflightReceiptV1:
        """Fail before the orchestrator journals intent or consumes action budget."""

        if (
            command.target_bounds is not None
            and self.config.target_bounds is not None
            and command.target_bounds != self.config.target_bounds
        ):
            raise DeviceExecutorError(
                "command target bounds conflict with the device executor configuration"
            )
        target_bounds = command.target_bounds or self.config.target_bounds
        if target_bounds is not None and (
            target_bounds.x + target_bounds.width > self.config.viewport_width
            or target_bounds.y + target_bounds.height > self.config.viewport_height
        ):
            raise DeviceExecutorError("target bounds exceed the configured source viewport")
        self._validate_interaction_before_gateway(command, target_bounds)
        return ExecutorPreflightReceiptV1(
            command_id=command.command_id,
            request_sha256=autonomous_request_sha256(command),
            action_type=command.action.type,
            issuer="device_executor",
            pointer_preflight_checked=command.action.type
            in {
                "tap",
                "swipe",
                "pinch",
                "two_finger_swipe",
                "mouse_move",
                "mouse_button",
            },
        )

    @staticmethod
    def _expected_change_issue(
        expected: InteractionExpectedChangeV1 | None,
        *,
        resolution: StateResolutionV1,
        step: EvidenceStep,
        artifacts: list[ArtifactRef],
    ) -> str | None:
        if expected is None:
            return None
        if expected.kind == "semantic_state_change":
            if resolution.before_state.id == resolution.after_state.id:
                return "expected semantic state change was not observed; re-recognition is required"
            return None
        if expected.kind == "semantic_state":
            if resolution.after_state.id != expected.target_state_id:
                return (
                    "expected semantic destination was not observed; re-recognition is required"
                )
            return None
        if step.before_frame_id is None or step.after_frame_id is None:
            return "visual expected-change evidence is incomplete; re-recognition is required"
        by_id = {artifact.id: artifact for artifact in artifacts}
        before = by_id.get(step.before_frame_id)
        after = by_id.get(step.after_frame_id)
        if before is None or after is None or expected.region is None:
            return "visual expected-change evidence is incomplete; re-recognition is required"
        from ..evidence import (
            EvidenceRecorderError,
            regional_perceptual_frame_distance,
        )

        try:
            distance = regional_perceptual_frame_distance(
                before.path,
                after.path,
                expected.region,
            )
        except EvidenceRecorderError as exc:
            return f"visual expected-change could not be verified: {exc}"
        if expected.min_visual_change is None or distance < expected.min_visual_change:
            return (
                f"visual expected-change was not observed ({distance:.6f}); "
                "re-recognition is required"
            )
        return None

    @staticmethod
    def _failure_reason(step: EvidenceStep, issues: list[str]) -> str:
        reasons = [item for item in [step.error, *issues] if item]
        return "; ".join(dict.fromkeys(reasons)) or "evidence manifest is not publishable"

    def execute(
        self,
        request: AutonomousExecutorRequestV1,
    ) -> CanonicalExecutionOutcomeV1:
        command = request.command
        environment = {
            **self.config.environment,
            "environment_id": command.environment_id,
            "ai_player_session_id": command.session_id,
        }
        self.validate_before_reservation(command)
        target_bounds = command.target_bounds or self.config.target_bounds
        reservation = PreReservedAIPlayerActionV1(
            capsule_id=request.pending_capsule_id,
            command_id=command.command_id,
            request_sha256=_request_sha256(request),
            action=command.action,
        )
        opened = self.gateway.start_evidence_run(
            self.config.target_id,
            self.config.lease_token,
            viewport_width=self.config.viewport_width,
            viewport_height=self.config.viewport_height,
            game_id=self.config.game_id,
            build_scope_id=self.config.build_scope_id,
            scope_id=command.environment_id,
            environment=environment,
            pre_reserved_action=reservation,
        )
        recorded = self.gateway.record_evidence_step(
            opened.id,
            self.config.lease_token,
            command.action,
            target_name=self.config.target_name,
            target_bounds=target_bounds,
            settle_threshold=self.config.settle_threshold,
            required_consecutive=self.config.required_consecutive,
            settle_timeout_seconds=self.config.settle_timeout_seconds,
            sample_interval_seconds=self.config.sample_interval_seconds,
            terminal_condition=self.config.terminal_condition,
            dynamic_scene_profile=self.config.dynamic_scene_profile,
            capture_profile=self.config.capture_profile,
        )
        if (
            self.config.before_surface_profile is not None
            and self.config.after_surface_profile is not None
        ):
            recorded = attach_semantic_surface_profiles(
                recorded,
                before=self.config.before_surface_profile,
                after=self.config.after_surface_profile,
                producer="game-observatory.ai-player.device-executor.v1",
                actor=command.actor,
            )
            self.gateway.store.save_evidence_step(recorded)
        manifest = self.gateway.complete_evidence_run(
            opened.id,
            self.config.lease_token,
        )
        run, step, artifacts, action_run = self._validate_terminal_bundle(
            opened_run_id=opened.id,
            recorded=recorded,
            manifest=manifest,
            environment_id=command.environment_id,
        )
        canonical_artifact_ids = [artifact.id for artifact in artifacts]
        if action_run.task_id not in {None, request.task.id}:
            raise DeviceExecutorError("action run 绑定了其他任务，拒绝归并")
        action_run_updates: dict[str, object] = {}
        if not action_run.artifact_ids:
            action_run_updates["artifact_ids"] = canonical_artifact_ids
        if action_run.task_id is None:
            action_run_updates["task_id"] = request.task.id
        if action_run_updates:
            action_run = action_run.model_copy(update=action_run_updates)
            self.gateway.store.save_run(action_run)
        artifacts = self._bind_artifacts(artifacts, command.environment_id)
        resolution = self.state_resolver.resolve(
            request=request,
            evidence_run=run,
            evidence_step=step,
            artifacts=artifacts,
            action_run=action_run,
        )
        expected_change = (
            command.interaction_preflight.expected_change
            if command.interaction_preflight is not None
            else None
        )
        expected_change_issue = self._expected_change_issue(
            expected_change,
            resolution=resolution,
            step=step,
            artifacts=artifacts,
        )
        succeeded = (
            manifest.publishable
            and step.status == "passed"
            and expected_change_issue is None
        )
        return CanonicalExecutionOutcomeV1(
            environment_id=command.environment_id,
            command_id=command.command_id,
            task_id=request.task.id,
            status="succeeded" if succeeded else "failed",
            evidence_run=run,
            evidence_step=step,
            artifacts=artifacts,
            action_run=action_run,
            before_state=resolution.before_state,
            after_state=resolution.after_state,
            observed_change=resolution.observed_change,
            failure_reason=(
                None
                if succeeded
                else self._failure_reason(
                    step,
                    [*manifest.publication_issues, expected_change_issue or ""],
                )
            ),
        )
