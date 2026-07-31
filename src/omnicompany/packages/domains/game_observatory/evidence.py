from __future__ import annotations

import hashlib
import math
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Protocol

from .media_validation import artifact_file_issues
from .models import (
    ArtifactRef,
    EvidenceDynamicSceneProfile,
    EvidenceRun,
    EvidenceRunManifest,
    EvidenceStability,
    EvidenceStep,
    EvidenceTerminalCondition,
    EvidenceTerminalEvaluation,
    LIFECYCLE_ACTION_TYPES,
    NormalizedAction,
    ObservationBundle,
    RunResult,
    SourcePixelPoint,
    SourcePixelRect,
    utc_now,
)
from .store import ObservatoryStore


class EvidenceRecorderError(RuntimeError):
    pass


_LIFECYCLE_TRANSIENT_VISUAL_ISSUES = (
    "PNG screenshot is an all-black or near-black frame",
    "PNG screenshot is visually uniform",
)


class EvidenceAdapter(Protocol):
    def observe_frame(self, *, include_ui: bool = False) -> ObservationBundle: ...

    def observe_probe_frame(self) -> bytes: ...

    def act(self, action: NormalizedAction) -> dict[str, Any]: ...

    def begin_video_capture(self, *, max_seconds: int = 180) -> Any: ...

    def finish_video_capture(
        self,
        handle: Any,
        *,
        evidence_run_id: str,
        evidence_step_id: str,
    ) -> ArtifactRef: ...


def perceptual_frame_distance(first_path: str | Path, second_path: str | Path) -> float:
    """Return a normalized visual distance over a small grayscale analysis copy."""
    import cv2
    import numpy as np

    first = cv2.imread(str(first_path), cv2.IMREAD_GRAYSCALE)
    second = cv2.imread(str(second_path), cv2.IMREAD_GRAYSCALE)
    if first is None or second is None:
        raise EvidenceRecorderError("stability frame cannot be decoded")
    first = cv2.resize(first, (64, 64), interpolation=cv2.INTER_AREA)
    second = cv2.resize(second, (64, 64), interpolation=cv2.INTER_AREA)
    return float(np.mean(cv2.absdiff(first, second)) / 255.0)


def structural_frame_distance(first_path: str | Path, second_path: str | Path) -> float:
    """Compare full-screen edge structure while tolerating live content changes."""
    import cv2
    import numpy as np

    first = cv2.imread(str(first_path), cv2.IMREAD_GRAYSCALE)
    second = cv2.imread(str(second_path), cv2.IMREAD_GRAYSCALE)
    if first is None or second is None:
        raise EvidenceRecorderError("structural guard frame cannot be decoded")
    first = cv2.resize(first, (64, 64), interpolation=cv2.INTER_AREA)
    second = cv2.resize(second, (64, 64), interpolation=cv2.INTER_AREA)
    first_edges = cv2.Canny(first, 60, 180)
    second_edges = cv2.Canny(second, 60, 180)
    return float(np.mean(cv2.absdiff(first_edges, second_edges)) / 255.0)


def frame_layout_hash_similarities(
    first_path: str | Path,
    second_path: str | Path,
) -> tuple[float, tuple[float, ...]]:
    """Compare coarse full-frame and 3x3 layout hashes.

    The metric is intentionally separate from the ordinary pixel/edge distances.
    It is useful only when the caller already has independent environment, action,
    and expected-state guards, but a Unity surface changed live map content,
    lighting, or season art while retaining the same interface layout.
    """

    import cv2
    import numpy as np

    first = cv2.imread(str(first_path), cv2.IMREAD_GRAYSCALE)
    second = cv2.imread(str(second_path), cv2.IMREAD_GRAYSCALE)
    if first is None or second is None:
        raise EvidenceRecorderError("layout guard frame cannot be decoded")

    def bit_similarity(left: "np.ndarray", right: "np.ndarray") -> float:
        return 1.0 - float(np.count_nonzero(left != right) / left.size)

    def difference_hash(image: "np.ndarray") -> "np.ndarray":
        resized = cv2.resize(image, (17, 16), interpolation=cv2.INTER_AREA)
        return resized[:, 1:] >= resized[:, :-1]

    full_similarity = bit_similarity(difference_hash(first), difference_hash(second))
    regional_similarities: list[float] = []
    for row in range(3):
        for column in range(3):
            left = first[
                row * first.shape[0] // 3 : (row + 1) * first.shape[0] // 3,
                column * first.shape[1] // 3 : (column + 1) * first.shape[1] // 3,
            ]
            right = second[
                row * second.shape[0] // 3 : (row + 1) * second.shape[0] // 3,
                column * second.shape[1] // 3 : (column + 1) * second.shape[1] // 3,
            ]
            left_resized = cv2.resize(left, (8, 8), interpolation=cv2.INTER_AREA)
            right_resized = cv2.resize(right, (8, 8), interpolation=cv2.INTER_AREA)
            regional_similarities.append(
                bit_similarity(
                    left_resized >= float(left_resized.mean()),
                    right_resized >= float(right_resized.mean()),
                )
            )
    return full_similarity, tuple(regional_similarities)


def perceptual_probe_distance(first_png: bytes, second_png: bytes) -> float:
    """Compare transient PNG probes without registering or retaining artifacts."""
    import cv2
    import numpy as np

    first = cv2.imdecode(np.frombuffer(first_png, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    second = cv2.imdecode(np.frombuffer(second_png, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if first is None or second is None:
        raise EvidenceRecorderError("stability probe cannot be decoded")
    first = cv2.resize(first, (64, 64), interpolation=cv2.INTER_AREA)
    second = cv2.resize(second, (64, 64), interpolation=cv2.INTER_AREA)
    return float(np.mean(cv2.absdiff(first, second)) / 255.0)


def perceptual_frame_probe_distance(
    first_path: str | Path,
    second_png: bytes,
    region: SourcePixelRect | None = None,
) -> float:
    """Compare a retained frame with an in-memory probe without retaining the probe."""
    import cv2
    import numpy as np

    first = cv2.imread(str(first_path), cv2.IMREAD_GRAYSCALE)
    second = cv2.imdecode(np.frombuffer(second_png, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if first is None or second is None:
        raise EvidenceRecorderError("terminal condition probe cannot be decoded")
    if region is not None:
        first_height, first_width = first.shape[:2]
        second_height, second_width = second.shape[:2]
        right = region.x + region.width
        bottom = region.y + region.height
        if (
            right > first_width
            or bottom > first_height
            or right > second_width
            or bottom > second_height
        ):
            raise EvidenceRecorderError(
                "terminal condition region is outside one or more compared frames"
            )
        first = first[region.y:bottom, region.x:right]
        second = second[region.y:bottom, region.x:right]
    first = cv2.resize(first, (64, 64), interpolation=cv2.INTER_AREA)
    second = cv2.resize(second, (64, 64), interpolation=cv2.INTER_AREA)
    return float(np.mean(cv2.absdiff(first, second)) / 255.0)


def regional_perceptual_frame_distance(
    first_path: str | Path,
    second_path: str | Path,
    region: SourcePixelRect,
) -> float:
    """Compare a declared source-pixel region without losing its audit geometry."""
    import cv2
    import numpy as np

    first = cv2.imread(str(first_path), cv2.IMREAD_GRAYSCALE)
    second = cv2.imread(str(second_path), cv2.IMREAD_GRAYSCALE)
    if first is None or second is None:
        raise EvidenceRecorderError("terminal condition frame cannot be decoded")
    first_height, first_width = first.shape[:2]
    second_height, second_width = second.shape[:2]
    right = region.x + region.width
    bottom = region.y + region.height
    if (
        right > first_width
        or bottom > first_height
        or right > second_width
        or bottom > second_height
    ):
        raise EvidenceRecorderError(
            "terminal condition region is outside one or more compared frames"
        )
    first_crop = first[region.y:bottom, region.x:right]
    second_crop = second[region.y:bottom, region.x:right]
    first_crop = cv2.resize(first_crop, (64, 64), interpolation=cv2.INTER_AREA)
    second_crop = cv2.resize(second_crop, (64, 64), interpolation=cv2.INTER_AREA)
    return float(np.mean(cv2.absdiff(first_crop, second_crop)) / 255.0)


def regional_structural_frame_distance(
    first_path: str | Path,
    second_path: str | Path,
    region: SourcePixelRect,
) -> float:
    """Compare local edge structure while tolerating animated light and color."""
    import cv2
    import numpy as np

    first = cv2.imread(str(first_path), cv2.IMREAD_GRAYSCALE)
    second = cv2.imread(str(second_path), cv2.IMREAD_GRAYSCALE)
    if first is None or second is None:
        raise EvidenceRecorderError("structural guard frame cannot be decoded")
    first_height, first_width = first.shape[:2]
    second_height, second_width = second.shape[:2]
    right = region.x + region.width
    bottom = region.y + region.height
    if (
        right > first_width
        or bottom > first_height
        or right > second_width
        or bottom > second_height
    ):
        raise EvidenceRecorderError(
            "structural guard region is outside one or more compared frames"
        )
    first_crop = first[region.y:bottom, region.x:right]
    second_crop = second[region.y:bottom, region.x:right]
    first_crop = cv2.resize(first_crop, (64, 64), interpolation=cv2.INTER_AREA)
    second_crop = cv2.resize(second_crop, (64, 64), interpolation=cv2.INTER_AREA)
    first_edges = cv2.Canny(first_crop, 60, 180)
    second_edges = cv2.Canny(second_crop, 60, 180)
    return float(np.mean(cv2.absdiff(first_edges, second_edges)) / 255.0)


def _ordered_unique(values: list[str | None]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _bounded_dynamic_window(
    sample_distances: list[float],
    profile: EvidenceDynamicSceneProfile,
) -> tuple[bool, list[float], int, int]:
    """Evaluate only a recent, fixed-size motion window with a bounded threshold."""
    window_size = profile.analysis_window_frames
    required = math.ceil(window_size * profile.required_inlier_ratio)
    if len(sample_distances) < window_size:
        return False, list(sample_distances), 0, required
    window = sample_distances[-window_size:]
    inliers = sum(
        distance <= profile.max_inlier_frame_distance for distance in window
    )
    return inliers >= required, window, inliers, required


class EvidenceRecorder:
    """Fail-closed recorder for one ordered, replayable game interaction run."""

    def __init__(
        self,
        store: ObservatoryStore,
        adapter: EvidenceAdapter | None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        frame_distance: Callable[[str | Path, str | Path], float] = perceptual_frame_distance,
    ) -> None:
        self.store = store
        self.adapter = adapter
        self.sleep = sleep
        self.frame_distance = frame_distance

    def start_run(
        self,
        *,
        target_id: str,
        adapter: str,
        viewport_width: int,
        viewport_height: int,
        game_id: str | None = None,
        build_scope_id: str | None = None,
        scope_id: str | None = None,
        environment: dict[str, Any] | None = None,
    ) -> EvidenceRun:
        if viewport_width <= 0 or viewport_height <= 0:
            raise EvidenceRecorderError("viewport dimensions must be positive")
        if viewport_width == viewport_height:
            orientation = "square"
        elif viewport_width > viewport_height:
            orientation = "landscape"
        else:
            orientation = "portrait"
        run = EvidenceRun(
            id=f"evidence.run.{uuid.uuid4().hex}",
            target_id=target_id,
            adapter=adapter,
            game_id=game_id,
            build_scope_id=build_scope_id,
            scope_id=scope_id,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            orientation=orientation,
            environment=dict(environment or {}),
        )
        self.store.save_evidence_run(run)
        self.store.append_event(
            run.id,
            "evidence_run_started",
            {
                "target_id": target_id,
                "viewport": [viewport_width, viewport_height],
                "game_id": game_id,
                "build_scope_id": build_scope_id,
                "scope_id": scope_id,
            },
        )
        return run

    def _run(self, evidence_run_id: str) -> EvidenceRun:
        run = self.store.get_evidence_run(evidence_run_id)
        if not run:
            raise EvidenceRecorderError(f"unknown evidence run: {evidence_run_id}")
        if run.status != "running":
            raise EvidenceRecorderError(
                f"evidence run is not accepting steps while status is {run.status}"
            )
        return run

    def pause_run(self, evidence_run_id: str) -> EvidenceRun:
        run = self._run(evidence_run_id)
        paused = run.model_copy(update={"status": "paused"})
        self.store.save_evidence_run(paused)
        self.store.append_event(
            run.id,
            "evidence_run_paused",
            {"step_count": len(self.store.list_evidence_steps(run.id))},
        )
        return paused

    def resume_run(self, evidence_run_id: str) -> EvidenceRun:
        run = self.store.get_evidence_run(evidence_run_id)
        if not run:
            raise EvidenceRecorderError(f"unknown evidence run: {evidence_run_id}")
        if run.status != "paused":
            raise EvidenceRecorderError(
                f"evidence run cannot resume from status {run.status}"
            )
        resumed = run.model_copy(update={"status": "running"})
        self.store.save_evidence_run(resumed)
        self.store.append_event(
            run.id,
            "evidence_run_resumed",
            {"step_count": len(self.store.list_evidence_steps(run.id))},
        )
        return resumed

    def stop_run(self, evidence_run_id: str, *, reason: str) -> EvidenceRun:
        """Settle a partial run after its owning execution process has ended."""

        run = self.store.get_evidence_run(evidence_run_id)
        if not run:
            raise EvidenceRecorderError(f"unknown evidence run: {evidence_run_id}")
        if run.status not in {"running", "paused"}:
            return run
        ended_at = utc_now()
        steps = self.store.list_evidence_steps(run.id)
        stopped_step_ids: list[str] = []
        for step in steps:
            if step.status != "running":
                continue
            stopped = step.model_copy(
                update={
                    "status": "stopped",
                    "ended_at": ended_at,
                    "error": step.error or reason,
                }
            )
            self.store.save_evidence_step(stopped)
            stopped_step_ids.append(stopped.id)
            self.store.append_event(
                run.id,
                "evidence_step_stopped",
                {"step_id": stopped.id, "reason": reason},
            )
        stopped_run = run.model_copy(
            update={
                "status": "stopped",
                "ended_at": ended_at,
                "step_ids": [item.id for item in steps],
                "error": run.error or reason,
            }
        )
        self.store.save_evidence_run(stopped_run)
        self.store.append_event(
            run.id,
            "evidence_run_stopped",
            {"reason": reason, "stopped_step_ids": stopped_step_ids},
        )
        return stopped_run

    def _annotate_artifact(
        self,
        artifact: ArtifactRef,
        *,
        run: EvidenceRun,
        step: EvidenceStep,
        role: str,
        captured_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        environment_id = run.environment.get("environment_id")
        annotated = artifact.model_copy(
            update={
                "captured_at": artifact.captured_at or captured_at,
                "metadata": {
                    **artifact.metadata,
                    **(
                        {"environment_id": environment_id}
                        if isinstance(environment_id, str) and environment_id
                        else {}
                    ),
                    "evidence_run_id": run.id,
                    "evidence_step_id": step.id,
                    "evidence_step_index": step.step_index,
                    "evidence_role": role,
                    **(metadata or {}),
                }
            }
        )
        self.store.save_artifact(annotated)
        return annotated

    def _capture(
        self,
        *,
        run: EvidenceRun,
        step: EvidenceStep,
        role: str,
        include_ui: bool,
        native_viewports: dict[str, list[int]],
    ) -> tuple[ArtifactRef, ArtifactRef | None]:
        observation = self.adapter.observe_frame(include_ui=include_ui)
        return self._capture_observation(
            observation,
            run=run,
            step=step,
            role=role,
            native_viewports=native_viewports,
        )

    def _capture_observation(
        self,
        observation: ObservationBundle,
        *,
        run: EvidenceRun,
        step: EvidenceStep,
        role: str,
        native_viewports: dict[str, list[int]],
    ) -> tuple[ArtifactRef, ArtifactRef | None]:
        import cv2

        decoded = cv2.imread(observation.frame.path, cv2.IMREAD_UNCHANGED)
        if decoded is None:
            raise EvidenceRecorderError(
                f"{observation.frame.id}: captured frame cannot be decoded"
            )
        height, width = decoded.shape[:2]
        native_viewports[role] = [width, height]
        declared = (run.viewport_width, run.viewport_height)
        native = (width, height)
        lifecycle_action = step.action.type in LIFECYCLE_ACTION_TYPES
        if native == declared:
            viewport_relation = "exact"
        elif lifecycle_action and native == (declared[1], declared[0]):
            viewport_relation = "transposed_lifecycle"
        else:
            viewport_relation = "mismatch"
        capture_metadata = {
            "native_viewport": {"width": width, "height": height},
            "declared_run_viewport": {
                "width": run.viewport_width,
                "height": run.viewport_height,
            },
            "native_orientation": self._orientation(width, height),
            "declared_run_orientation": run.orientation,
            "viewport_relation": viewport_relation,
            "lifecycle_action": step.action.type if lifecycle_action else None,
            "semantic_state_eligible": not lifecycle_action,
            "real_image_holdout_eligible": not lifecycle_action,
        }
        frame = self._annotate_artifact(
            observation.frame,
            run=run,
            step=step,
            role=role,
            captured_at=observation.captured_at,
            metadata=capture_metadata,
        )
        if viewport_relation == "mismatch":
            raise EvidenceRecorderError(
                f"{frame.id}: frame is {width}x{height}, expected "
                f"{run.viewport_width}x{run.viewport_height} source pixels"
            )
        ui_tree = None
        if observation.ui_tree:
            ui_tree = self._annotate_artifact(
                observation.ui_tree,
                run=run,
                step=step,
                role=f"{role}_ui_tree",
                captured_at=observation.captured_at,
                metadata=capture_metadata,
            )
        return frame, ui_tree

    def _persist_probe_observation(
        self,
        raw: bytes,
        *,
        run: EvidenceRun,
    ) -> ObservationBundle:
        """Promote the final in-memory stability probe without another device capture."""

        if not raw.startswith(b"\x89PNG"):
            raise EvidenceRecorderError("terminal stability probe is not PNG")
        stamp = int(time.time() * 1000)
        captured_at = utc_now()
        artifact_id = f"art.probe-terminal.{stamp}.{uuid.uuid4().hex[:8]}"
        path = self.store.artifact_root / f"{artifact_id}.png"
        path.write_bytes(raw)
        observation_run_id = f"run.observe-probe.{stamp}.{uuid.uuid4().hex[:8]}"
        artifact = ArtifactRef(
            id=artifact_id,
            kind="screenshot",
            path=str(path),
            sha256=hashlib.sha256(raw).hexdigest(),
            captured_at=captured_at,
            run_id=observation_run_id,
            media_type="image/png",
            metadata={"transient_probe_promoted": True},
        )
        self.store.save_artifact(artifact)
        self.store.save_run(
            RunResult(
                id=observation_run_id,
                adapter=run.adapter,
                target_id=run.target_id,
                status="passed",
                ended_at=utc_now(),
                artifact_ids=[artifact.id],
            )
        )
        self.store.append_event(
            observation_run_id,
            "observe_probe_promoted",
            {"artifact_id": artifact.id, "evidence_run_id": run.id},
        )
        return ObservationBundle(
            target_id=run.target_id,
            captured_at=captured_at,
            frame=artifact,
            metadata={"transient_probe_promoted": True},
        )

    @staticmethod
    def _orientation(width: int, height: int) -> str:
        if width == height:
            return "square"
        return "landscape" if width > height else "portrait"

    def _assert_reusable_route_boundary(
        self,
        artifact: ArtifactRef,
        *,
        run: EvidenceRun,
    ) -> None:
        """Fail before device mutation unless this is the prior published After."""

        path = Path(artifact.path)
        if (
            not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != artifact.sha256
        ):
            raise EvidenceRecorderError(
                "reused route-boundary Before artifact hash does not match"
            )
        prior_run_id = str(artifact.metadata.get("evidence_run_id") or "")
        prior_step_id = str(artifact.metadata.get("evidence_step_id") or "")
        prior_run = self.store.get_evidence_run(prior_run_id)
        prior_step = self.store.get_evidence_step(prior_step_id)
        prior_manifest = self.store.get_evidence_manifest(prior_run_id)
        if (
            artifact.metadata.get("evidence_role") != "after"
            or prior_run is None
            or prior_run.target_id != run.target_id
            or prior_run.status != "passed"
            or not prior_run.ended_at
            or prior_step is None
            or prior_step.evidence_run_id != prior_run.id
            or prior_step.status != "passed"
            or not prior_step.ended_at
            or prior_step.after_frame_id != artifact.id
            or artifact.id not in prior_step.artifact_ids
            or artifact.id not in prior_run.artifact_ids
            or not prior_step.action_run_id
            or prior_step.action_run_id not in prior_run.action_run_ids
            or prior_manifest is None
            or not prior_manifest.publishable
        ):
            raise EvidenceRecorderError(
                "reused route-boundary Before is not a published prior After"
            )
        prior_lease_id = prior_run.environment.get("device_gateway_lease_id")
        current_lease_id = run.environment.get("device_gateway_lease_id")
        if (
            not isinstance(prior_lease_id, str)
            or not prior_lease_id
            or prior_lease_id != current_lease_id
        ):
            raise EvidenceRecorderError(
                "reused route-boundary Before crosses a device lease boundary"
            )

    def _step_capture_metadata(
        self,
        *,
        run: EvidenceRun,
        step: EvidenceStep,
        native_viewports: dict[str, list[int]],
    ) -> dict[str, Any]:
        lifecycle_action = step.action.type in LIFECYCLE_ACTION_TYPES
        before = native_viewports.get("before")
        after = native_viewports.get("after")
        orientation_transition = {
            "lifecycle_action": step.action.type if lifecycle_action else None,
            "before_native_viewport": before,
            "after_native_viewport": after,
            "before_orientation": self._orientation(*before) if before else None,
            "after_orientation": self._orientation(*after) if after else None,
            "changed": bool(before and after and before != after),
        }
        return {
            **step.metadata,
            "capture_contract": {
                "declared_run_viewport": {
                    "width": run.viewport_width,
                    "height": run.viewport_height,
                },
                "declared_run_orientation": run.orientation,
                "action_class": "lifecycle" if lifecycle_action else "interaction",
                "allows_exact_transposed_native_viewport": lifecycle_action,
                "native_viewports_by_role": dict(native_viewports),
            },
            "orientation_transition": orientation_transition,
            "semantic_state_eligible": not lifecycle_action,
            "real_image_holdout_eligible": not lifecycle_action,
        }

    def _finalize_artifact_orientation_metadata(
        self,
        artifacts: list[ArtifactRef],
        *,
        step: EvidenceStep,
        step_metadata: dict[str, Any],
    ) -> list[ArtifactRef]:
        finalized: list[ArtifactRef] = []
        for artifact in artifacts:
            if artifact.metadata.get("evidence_step_id") != step.id:
                finalized.append(artifact)
                continue
            annotated = artifact.model_copy(
                update={
                    "metadata": {
                        **artifact.metadata,
                        "capture_contract": step_metadata["capture_contract"],
                        "orientation_transition": step_metadata[
                            "orientation_transition"
                        ],
                        "semantic_state_eligible": step_metadata[
                            "semantic_state_eligible"
                        ],
                        "real_image_holdout_eligible": step_metadata[
                            "real_image_holdout_eligible"
                        ],
                    }
                }
            )
            self.store.save_artifact(annotated)
            finalized.append(annotated)
        return finalized

    @staticmethod
    def _is_lifecycle_transient_visual_issue(
        *,
        step: EvidenceStep,
        artifact: ArtifactRef,
        issue: str,
        require_marked: bool = False,
    ) -> bool:
        role = str(artifact.metadata.get("evidence_role") or "")
        if not (
            step.action.type in LIFECYCLE_ACTION_TYPES
            and artifact.kind == "screenshot"
            and artifact.metadata.get("evidence_step_id") == step.id
            and role.startswith("intermediate_")
            and any(issue.endswith(expected) for expected in _LIFECYCLE_TRANSIENT_VISUAL_ISSUES)
        ):
            return False
        if require_marked:
            declared_frames = step.metadata.get("transient_frames")
            declared = isinstance(declared_frames, list) and any(
                isinstance(item, dict) and item.get("artifact_id") == artifact.id
                for item in declared_frames
            )
            if not (
                declared
                and artifact.metadata.get("transient_frame") is True
                and artifact.metadata.get("transient_reason")
                == "lifecycle_orientation_transition"
                and artifact.metadata.get("semantic_state_eligible") is False
                and artifact.metadata.get("real_image_holdout_eligible") is False
                and artifact.metadata.get("quality_advisories")
            ):
                return False
        return True

    def _apply_artifact_quality_policy(
        self,
        artifacts: list[ArtifactRef],
        *,
        step: EvidenceStep,
    ) -> tuple[list[ArtifactRef], list[str], list[str], list[dict[str, Any]]]:
        finalized: list[ArtifactRef] = []
        hard_issues: list[str] = []
        advisories: list[str] = []
        transient_frames: list[dict[str, Any]] = []
        for artifact in artifacts:
            artifact_advisories: list[str] = []
            for issue in artifact_file_issues(artifact):
                if self._is_lifecycle_transient_visual_issue(
                    step=step,
                    artifact=artifact,
                    issue=issue,
                ):
                    advisory = (
                        f"{artifact.id}: 生命周期动作的中间过渡帧为黑屏或均匀画面，"
                        f"原图已保留且不进入语义状态或留出集；原始检查：{issue}"
                    )
                    artifact_advisories.append(advisory)
                    advisories.append(advisory)
                    transient_frames.append(
                        {
                            "artifact_id": artifact.id,
                            "evidence_role": artifact.metadata.get("evidence_role"),
                            "native_viewport": artifact.metadata.get("native_viewport"),
                            "advisory": advisory,
                        }
                    )
                else:
                    hard_issues.append(issue)
            if artifact_advisories:
                artifact = artifact.model_copy(
                    update={
                        "metadata": {
                            **artifact.metadata,
                            "transient_frame": True,
                            "transient_reason": "lifecycle_orientation_transition",
                            "quality_advisories": artifact_advisories,
                            "semantic_state_eligible": False,
                            "real_image_holdout_eligible": False,
                        }
                    }
                )
                self.store.save_artifact(artifact)
            finalized.append(artifact)
        return finalized, hard_issues, advisories, transient_frames

    def _terminal_distance(
        self,
        first_path: str | Path,
        second_path: str | Path,
        region: SourcePixelRect | None,
    ) -> float:
        if region is None:
            return self.frame_distance(first_path, second_path)
        return regional_perceptual_frame_distance(first_path, second_path, region)

    def _evaluate_terminal_condition(
        self,
        condition: EvidenceTerminalCondition,
        *,
        before: ArtifactRef,
        after: ArtifactRef,
        observed_seconds: float,
    ) -> tuple[EvidenceTerminalEvaluation, ArtifactRef | None]:
        issues: list[str] = []
        change_from_before: float | None = None
        distance_from_reference: float | None = None
        reference: ArtifactRef | None = None

        if observed_seconds + 1e-9 < condition.min_observation_seconds:
            issues.append(
                "observed action aftermath for "
                f"{observed_seconds:.3f}s, below declared minimum "
                f"{condition.min_observation_seconds:.3f}s"
            )
        if condition.min_visual_change_from_before is not None:
            try:
                change_from_before = self._terminal_distance(
                    before.path,
                    after.path,
                    condition.region,
                )
                if change_from_before < condition.min_visual_change_from_before:
                    issues.append(
                        "visual change from before "
                        f"{change_from_before:.6f} is below declared minimum "
                        f"{condition.min_visual_change_from_before:.6f}"
                    )
            except EvidenceRecorderError as exc:
                issues.append(str(exc))
        if condition.visual_reference_artifact_id is not None:
            reference = self.store.get_artifact(condition.visual_reference_artifact_id)
            if reference is None:
                issues.append(
                    "visual reference artifact does not exist: "
                    f"{condition.visual_reference_artifact_id}"
                )
            else:
                try:
                    distance_from_reference = self._terminal_distance(
                        reference.path,
                        after.path,
                        condition.region,
                    )
                    if (
                        distance_from_reference
                        > condition.max_visual_distance_from_reference
                    ):
                        issues.append(
                            "visual distance from reference "
                            f"{distance_from_reference:.6f} exceeds declared maximum "
                            f"{condition.max_visual_distance_from_reference:.6f}"
                        )
                except EvidenceRecorderError as exc:
                    issues.append(str(exc))

        return (
            EvidenceTerminalEvaluation(
                condition=condition,
                passed=not issues,
                observed_seconds=observed_seconds,
                visual_change_from_before=change_from_before,
                visual_distance_from_reference=distance_from_reference,
                issues=issues,
            ),
            reference,
        )

    def _evaluate_terminal_probe_condition(
        self,
        condition: EvidenceTerminalCondition,
        *,
        before: ArtifactRef,
        probe: bytes,
        observed_seconds: float,
    ) -> EvidenceTerminalEvaluation:
        """Evaluate an early-stop terminal against a transient compact probe."""
        issues: list[str] = []
        change_from_before: float | None = None
        distance_from_reference: float | None = None

        if observed_seconds + 1e-9 < condition.min_observation_seconds:
            issues.append(
                "observed action aftermath for "
                f"{observed_seconds:.3f}s, below declared minimum "
                f"{condition.min_observation_seconds:.3f}s"
            )
        if condition.min_visual_change_from_before is not None:
            try:
                change_from_before = perceptual_frame_probe_distance(
                    before.path,
                    probe,
                    condition.region,
                )
                if change_from_before < condition.min_visual_change_from_before:
                    issues.append(
                        "visual change from before "
                        f"{change_from_before:.6f} is below declared minimum "
                        f"{condition.min_visual_change_from_before:.6f}"
                    )
            except EvidenceRecorderError as exc:
                issues.append(str(exc))
        if condition.visual_reference_artifact_id is not None:
            reference = self.store.get_artifact(condition.visual_reference_artifact_id)
            if reference is None:
                issues.append(
                    "visual reference artifact does not exist: "
                    f"{condition.visual_reference_artifact_id}"
                )
            else:
                try:
                    distance_from_reference = perceptual_frame_probe_distance(
                        reference.path,
                        probe,
                        condition.region,
                    )
                    if (
                        distance_from_reference
                        > condition.max_visual_distance_from_reference
                    ):
                        issues.append(
                            "visual distance from reference "
                            f"{distance_from_reference:.6f} exceeds declared maximum "
                            f"{condition.max_visual_distance_from_reference:.6f}"
                        )
                except EvidenceRecorderError as exc:
                    issues.append(str(exc))

        return EvidenceTerminalEvaluation(
            condition=condition,
            passed=not issues,
            observed_seconds=observed_seconds,
            visual_change_from_before=change_from_before,
            visual_distance_from_reference=distance_from_reference,
            issues=issues,
        )

    def record_step(
        self,
        evidence_run_id: str,
        action: NormalizedAction,
        *,
        target_name: str | None = None,
        target_bounds: SourcePixelRect | None = None,
        settle_threshold: float = 0.01,
        required_consecutive: int = 2,
        settle_timeout_seconds: float = 4.0,
        sample_interval_seconds: float = 0.25,
        terminal_condition: EvidenceTerminalCondition | None = None,
        dynamic_scene_profile: EvidenceDynamicSceneProfile | None = None,
        before_action: Callable[[EvidenceStep], None] | None = None,
        capture_profile: Literal["full", "compact_static"] = "full",
        action_task_id: str | None = None,
        reused_before_artifact_id: str | None = None,
        trusted_terminal_reference_artifact_id: str | None = None,
        trusted_terminal_max_visual_distance: float = 0.012,
    ) -> EvidenceStep:
        run = self._run(evidence_run_id)
        if self.adapter is None:
            raise EvidenceRecorderError("recording a step requires a connected adapter")
        if reused_before_artifact_id is not None and (
            capture_profile != "compact_static" or dynamic_scene_profile is not None
        ):
            raise EvidenceRecorderError(
                "route-boundary Before reuse requires compact static capture"
            )
        if dynamic_scene_profile is not None and terminal_condition is None:
            raise EvidenceRecorderError(
                "dynamic scene profile requires an explicit terminal condition"
            )
        if trusted_terminal_reference_artifact_id is not None and (
            capture_profile != "compact_static" or dynamic_scene_profile is not None
        ):
            raise EvidenceRecorderError(
                "trusted terminal reference requires compact static capture"
            )
        trusted_terminal_reference = None
        trusted_terminal_maximum = max(
            0.000001,
            min(trusted_terminal_max_visual_distance, 1.0),
        )
        if trusted_terminal_reference_artifact_id is not None:
            trusted_terminal_reference = self.store.get_artifact(
                trusted_terminal_reference_artifact_id
            )
            if (
                trusted_terminal_reference is None
                or trusted_terminal_reference.kind != "screenshot"
                or not Path(trusted_terminal_reference.path).is_file()
            ):
                raise EvidenceRecorderError(
                    "trusted terminal reference screenshot is missing"
                )
        if dynamic_scene_profile is not None and terminal_condition is not None:
            requested_interval = max(0.05, min(sample_interval_seconds, 10.0))
            requested_timeout = max(
                requested_interval,
                min(settle_timeout_seconds, 60.0),
            )
            minimum_window_seconds = (
                dynamic_scene_profile.analysis_window_frames * requested_interval
            )
            minimum_required_seconds = max(
                minimum_window_seconds,
                terminal_condition.min_observation_seconds,
            )
            if requested_timeout + 1e-9 < minimum_required_seconds:
                raise EvidenceRecorderError(
                    "dynamic scene timeout cannot collect its declared motion window "
                    "and terminal observation minimum"
                )
        point = (
            SourcePixelPoint(x=action.x, y=action.y)
            if action.x is not None and action.y is not None
            else None
        )
        end_point = (
            SourcePixelPoint(x=action.x2, y=action.y2)
            if action.x2 is not None and action.y2 is not None
            else None
        )
        step = self.store.create_evidence_step(
            run.id,
            lambda step_index: EvidenceStep(
                id=f"evidence.step.{uuid.uuid4().hex}",
                evidence_run_id=run.id,
                step_index=step_index,
                action=action,
                target_name=target_name,
                source_point=point,
                source_end_point=end_point,
                target_bounds=target_bounds,
                viewport_width=run.viewport_width,
                viewport_height=run.viewport_height,
                stability=EvidenceStability(
                    profile=(
                        "bounded-motion-terminal"
                        if dynamic_scene_profile is not None
                        else (
                            "trusted-reference-or-static"
                            if trusted_terminal_reference is not None
                            else "static-consecutive"
                        )
                    ),
                    threshold=(
                        dynamic_scene_profile.max_inlier_frame_distance
                        if dynamic_scene_profile is not None
                        else max(0.0, min(settle_threshold, 1.0))
                    ),
                    required_consecutive=max(1, min(required_consecutive, 20)),
                    dynamic_scene_profile=dynamic_scene_profile,
                    trusted_reference_artifact_id=(
                        trusted_terminal_reference.id
                        if trusted_terminal_reference is not None
                        else None
                    ),
                    trusted_reference_max_distance=(
                        trusted_terminal_maximum
                        if trusted_terminal_reference is not None
                        else None
                    ),
                ),
                terminal_condition=terminal_condition,
                metadata={"capture_profile": capture_profile},
            ),
        )
        self.store.append_event(
            run.id,
            "evidence_step_started",
            {
                "step_id": step.id,
                "step_index": step.step_index,
                "action": action.model_dump(mode="json"),
            },
        )

        artifacts: list[ArtifactRef] = []
        observation_run_ids: list[str | None] = []
        quality_issues: list[str] = []
        error: str | None = None
        video_handle: Any = None
        action_attempted = False
        before: ArtifactRef | None = None
        before_ui: ArtifactRef | None = None
        after: ArtifactRef | None = None
        after_ui: ArtifactRef | None = None
        video: ArtifactRef | None = None
        action_run_id: str | None = None
        action_started_at: str | None = None
        action_ended_at: str | None = None
        intermediate: list[ArtifactRef] = []
        stability = step.stability
        terminal_evaluation: EvidenceTerminalEvaluation | None = None
        native_viewports: dict[str, list[int]] = {}

        try:
            if capture_profile == "full":
                video_handle = self.adapter.begin_video_capture(
                    max_seconds=max(10, min(180, math.ceil(settle_timeout_seconds) + 30))
                )
            if reused_before_artifact_id is not None:
                import cv2

                before = self.store.get_artifact(reused_before_artifact_id)
                if before is None or before.kind != "screenshot":
                    raise EvidenceRecorderError(
                        "reused route-boundary Before screenshot is missing"
                    )
                self._assert_reusable_route_boundary(before, run=run)
                decoded = cv2.imread(before.path, cv2.IMREAD_UNCHANGED)
                if decoded is None:
                    raise EvidenceRecorderError(
                        "reused route-boundary Before cannot be decoded"
                    )
                height, width = decoded.shape[:2]
                if (width, height) != (run.viewport_width, run.viewport_height):
                    raise EvidenceRecorderError(
                        "reused route-boundary Before viewport does not match the route"
                    )
                native_viewports["before"] = [width, height]
                step = step.model_copy(
                    update={
                        "metadata": {
                            **step.metadata,
                            "reused_route_boundary_before": True,
                            "reused_before_artifact_id": before.id,
                        }
                    }
                )
            else:
                before, before_ui = self._capture(
                    run=run,
                    step=step,
                    role="before",
                    include_ui=capture_profile == "full",
                    native_viewports=native_viewports,
                )
            artifacts.extend([before] + ([before_ui] if before_ui else []))
            observation_run_ids.extend(
                [before.run_id, before_ui.run_id if before_ui else None]
            )
            step = step.model_copy(
                update={
                    "before_frame_id": before.id,
                    "before_ui_tree_id": before_ui.id if before_ui else None,
                    "artifact_ids": _ordered_unique([item.id for item in artifacts]),
                    "observation_run_ids": _ordered_unique(observation_run_ids),
                    "metadata": self._step_capture_metadata(
                        run=run,
                        step=step,
                        native_viewports=native_viewports,
                    ),
                }
            )
            self.store.save_evidence_step(step)

            if before_action:
                before_action(step)
            action_attempted = True
            action_started_at = utc_now()
            # Persist the dispatch boundary before invoking the adapter.  Timeout
            # cleanup may safely refund a gateway reservation only when this
            # marker is absent; once present, the external device side effect is
            # conservatively treated as possible even if the provider dies.
            step = step.model_copy(
                update={
                    "action_started_at": action_started_at,
                    "metadata": {
                        **step.metadata,
                        "action_dispatch_started": True,
                    },
                }
            )
            self.store.save_evidence_step(step)
            self.store.append_event(
                run.id,
                "evidence_action_dispatch_started",
                {
                    "step_id": step.id,
                    "action_started_at": action_started_at,
                },
            )
            try:
                if action_task_id is None:
                    result = self.adapter.act(action)
                else:
                    task_bound_act = getattr(self.adapter, "act_with_task", None)
                    if not callable(task_bound_act):
                        raise EvidenceRecorderError(
                            "task-bound evidence action requires adapter support"
                        )
                    result = task_bound_act(action, task_id=action_task_id)
                action_run_id = str(result.get("run_id") or "") or None
                if result.get("ok") is not True:
                    error = str(result.get("error") or "action adapter returned ok=false")
            except Exception as exc:
                result = getattr(exc, "result", None)
                if isinstance(result, dict):
                    action_run_id = str(result.get("run_id") or "") or None
                error = str(exc)
            action_ended_at = utc_now()

            interval = max(0.05, min(sample_interval_seconds, 10.0))
            timeout = max(interval, min(settle_timeout_seconds, 60.0))
            max_frames = max(
                stability.required_consecutive + 1,
                math.floor(timeout / interval) + 1,
            )
            consecutive = 0
            final_distance: float | None = None
            sample_distances: list[float] = []
            analysis_window_distances: list[float] = []
            analysis_inlier_count = 0
            analysis_required_inliers = 0
            dynamic_motion_passed = False
            trusted_reference_distance: float | None = None
            trusted_reference_matched = False
            previous: ArtifactRef | None = None
            previous_probe: bytes | None = None
            probe_capture = getattr(self.adapter, "observe_probe_frame", None)
            use_transient_probes = capture_profile == "compact_static" and callable(
                probe_capture
            )
            sampled_frame_count = 0
            for sample_index in range(max_frames):
                frame: ArtifactRef | None = None
                probe: bytes | None = None
                if use_transient_probes:
                    probe = probe_capture()
                    if not isinstance(probe, bytes) or not probe.startswith(b"\x89PNG"):
                        raise EvidenceRecorderError(
                            "transient stability probe did not return PNG bytes"
                        )
                else:
                    frame, _ = self._capture(
                        run=run,
                        step=step,
                        role=f"intermediate_{sample_index + 1}",
                        include_ui=False,
                        native_viewports=native_viewports,
                    )
                    intermediate.append(frame)
                    artifacts.append(frame)
                    observation_run_ids.append(frame.run_id)
                sampled_frame_count += 1
                if (
                    trusted_terminal_reference is not None
                    and use_transient_probes
                    and probe is not None
                ):
                    trusted_reference_distance = perceptual_frame_probe_distance(
                        trusted_terminal_reference.path,
                        probe,
                    )
                    if trusted_reference_distance <= trusted_terminal_maximum:
                        trusted_reference_matched = True
                        previous_probe = probe
                        break
                has_previous = (
                    previous_probe is not None if use_transient_probes else previous is not None
                )
                if has_previous:
                    final_distance = (
                        perceptual_probe_distance(previous_probe, probe)
                        if use_transient_probes
                        and previous_probe is not None
                        and probe is not None
                        else self.frame_distance(previous.path, frame.path)
                    )
                    sample_distances.append(final_distance)
                    consecutive = consecutive + 1 if final_distance <= stability.threshold else 0
                    observed_seconds = sample_index * interval
                    if dynamic_scene_profile is not None:
                        (
                            dynamic_motion_passed,
                            analysis_window_distances,
                            analysis_inlier_count,
                            analysis_required_inliers,
                        ) = _bounded_dynamic_window(
                            sample_distances,
                            dynamic_scene_profile,
                        )
                        if (
                            dynamic_motion_passed
                            and terminal_condition is not None
                            and before is not None
                            and observed_seconds + 1e-9
                            >= terminal_condition.min_observation_seconds
                        ):
                            if use_transient_probes and probe is not None:
                                sampled_terminal = self._evaluate_terminal_probe_condition(
                                    terminal_condition,
                                    before=before,
                                    probe=probe,
                                    observed_seconds=observed_seconds,
                                )
                            elif frame is not None:
                                sampled_terminal, _ = self._evaluate_terminal_condition(
                                    terminal_condition,
                                    before=before,
                                    after=frame,
                                    observed_seconds=observed_seconds,
                                )
                            else:
                                sampled_terminal = None
                            if sampled_terminal is not None and sampled_terminal.passed:
                                if use_transient_probes and probe is not None:
                                    previous_probe = probe
                                break
                    elif consecutive >= stability.required_consecutive and (
                        terminal_condition is None
                        or observed_seconds + 1e-9
                        >= terminal_condition.min_observation_seconds
                    ):
                        break
                if use_transient_probes:
                    previous_probe = probe
                else:
                    previous = frame
                if sample_index + 1 < max_frames:
                    self.sleep(interval)
            motion_settled = (
                dynamic_motion_passed
                if dynamic_scene_profile is not None
                else (
                    trusted_reference_matched
                    or consecutive >= stability.required_consecutive
                )
            )
            stability = stability.model_copy(
                update={
                    "observed_consecutive": consecutive,
                    "final_distance": final_distance,
                    "sample_distances": sample_distances,
                    "sampled_frames": sampled_frame_count,
                    "waited_seconds": round(max(0, sampled_frame_count - 1) * interval, 3),
                    "analysis_window_distances": analysis_window_distances,
                    "analysis_inlier_count": analysis_inlier_count,
                    "analysis_required_inliers": analysis_required_inliers,
                    "trusted_reference_distance": trusted_reference_distance,
                    "trusted_reference_matched": trusted_reference_matched,
                    "settled": motion_settled,
                }
            )

            if (
                capture_profile == "compact_static"
                and motion_settled
                and previous_probe is not None
            ):
                after, after_ui = self._capture_observation(
                    self._persist_probe_observation(previous_probe, run=run),
                    run=run,
                    step=step,
                    role="after",
                    native_viewports=native_viewports,
                )
            else:
                after, after_ui = self._capture(
                    run=run,
                    step=step,
                    role="after",
                    include_ui=capture_profile == "full",
                    native_viewports=native_viewports,
                )
            artifacts.extend([after] + ([after_ui] if after_ui else []))
            observation_run_ids.extend([after.run_id, after_ui.run_id if after_ui else None])
            if terminal_condition and before:
                terminal_evaluation, reference = self._evaluate_terminal_condition(
                    terminal_condition,
                    before=before,
                    after=after,
                    observed_seconds=stability.waited_seconds,
                )
                if reference:
                    artifacts.append(reference)
                if not terminal_evaluation.passed:
                    terminal_error = (
                        "terminal condition not satisfied: "
                        + "; ".join(terminal_evaluation.issues)
                    )
                    quality_issues.append(terminal_error)
                    error = error or terminal_error
            settled = motion_settled and (
                dynamic_scene_profile is None
                or bool(terminal_evaluation and terminal_evaluation.passed)
            )
            stability = stability.model_copy(update={"settled": settled})
            if not motion_settled:
                if dynamic_scene_profile is not None:
                    quality_issues.append(
                        "dynamic terminal motion exceeded the bounded recent-window policy"
                    )
                else:
                    quality_issues.append("after state did not settle before timeout")
        except Exception as exc:
            error = error or str(exc)
        finally:
            if video_handle is not None:
                try:
                    video = self.adapter.finish_video_capture(
                        video_handle,
                        evidence_run_id=run.id,
                        evidence_step_id=step.id,
                    )
                    video = self._annotate_artifact(
                        video,
                        run=run,
                        step=step,
                        role="action_window_video",
                        metadata={
                            "declared_run_viewport": {
                                "width": run.viewport_width,
                                "height": run.viewport_height,
                            },
                            "declared_run_orientation": run.orientation,
                            "lifecycle_action": (
                                step.action.type
                                if step.action.type in LIFECYCLE_ACTION_TYPES
                                else None
                            ),
                        },
                    )
                    artifacts.append(video)
                except Exception as exc:
                    quality_issues.append(f"video capture failed: {exc}")
            elif capture_profile == "full":
                quality_issues.append("video capture did not start")

        step_metadata = self._step_capture_metadata(
            run=run,
            step=step,
            native_viewports=native_viewports,
        )
        artifacts = self._finalize_artifact_orientation_metadata(
            artifacts,
            step=step,
            step_metadata=step_metadata,
        )
        artifacts, artifact_issues, quality_advisories, transient_frames = (
            self._apply_artifact_quality_policy(artifacts, step=step)
        )
        quality_issues.extend(artifact_issues)
        step_metadata = {
            **step_metadata,
            "transient_frames": transient_frames,
            "quality_advisories": quality_advisories,
            "transient_stability_probe_count": (
                stability.sampled_frames
                if capture_profile == "compact_static" and not intermediate
                else 0
            ),
        }
        if not action_attempted:
            quality_issues.append("action was not executed because the pre-action gate failed")
        if action_attempted and not action_run_id:
            quality_issues.append("action has no durable action run")
        if error:
            quality_issues.append(f"action or capture error: {error}")

        candidate = step.model_copy(
            update={
                "status": "passed",
                "ended_at": utc_now(),
                "before_frame_id": before.id if before else None,
                "before_ui_tree_id": before_ui.id if before_ui else None,
                "action_run_id": action_run_id,
                "action_started_at": action_started_at,
                "action_ended_at": action_ended_at,
                "intermediate_frame_ids": [item.id for item in intermediate],
                "after_frame_id": after.id if after else None,
                "after_ui_tree_id": after_ui.id if after_ui else None,
                "video_artifact_id": video.id if video else None,
                "artifact_ids": _ordered_unique([item.id for item in artifacts]),
                "observation_run_ids": _ordered_unique(observation_run_ids),
                "stability": stability,
                "terminal_evaluation": terminal_evaluation,
                "quality_issues": _ordered_unique(quality_issues),
                "quality_advisories": _ordered_unique(quality_advisories),
                "error": error,
                "metadata": step_metadata,
            }
        )
        status = "passed" if not candidate.publication_issues() and not error else "failed"
        completed = candidate.model_copy(update={"status": status})
        if completed.action_run_id:
            action_run = self.store.get_run(completed.action_run_id)
            if action_run is not None:
                self.store.save_run(
                    action_run.model_copy(
                        update={
                            "artifact_ids": _ordered_unique(
                                [*action_run.artifact_ids, *completed.artifact_ids]
                            )
                        }
                    )
                )
        self.store.save_evidence_step(completed)
        self.store.append_event(
            run.id,
            "evidence_step_finished",
            {
                "step_id": completed.id,
                "step_index": completed.step_index,
                "status": completed.status,
                "action_run_id": completed.action_run_id,
                "artifact_ids": completed.artifact_ids,
                "error": completed.error,
            },
        )
        return completed

    def complete_run(self, evidence_run_id: str) -> EvidenceRunManifest:
        run = self._run(evidence_run_id)
        steps = self.store.list_evidence_steps(run.id)
        issues: list[str] = []
        if not steps:
            issues.append(f"{run.id}: evidence run has no steps")
        indices = [item.step_index for item in steps]
        if indices != list(range(1, len(steps) + 1)):
            issues.append(f"{run.id}: evidence steps are missing or out of order")
        for step in steps:
            if step.evidence_run_id != run.id:
                issues.append(f"{step.id}: evidence run reference does not match")
            issues.extend(step.publication_issues())

        artifact_ids = _ordered_unique(
            [artifact_id for step in steps for artifact_id in step.artifact_ids]
        )
        action_run_ids = _ordered_unique([step.action_run_id for step in steps])
        observation_run_ids = _ordered_unique(
            [run_id for step in steps for run_id in step.observation_run_ids]
        )
        owning_steps = {
            artifact_id: step
            for step in steps
            for artifact_id in step.artifact_ids
            if artifact_id
        }
        for artifact_id in artifact_ids:
            artifact = self.store.get_artifact(artifact_id)
            if not artifact:
                issues.append(f"{artifact_id}: artifact reference is dead")
                continue
            owner = owning_steps.get(artifact_id)
            for artifact_issue in artifact_file_issues(artifact):
                if owner and self._is_lifecycle_transient_visual_issue(
                    step=owner,
                    artifact=artifact,
                    issue=artifact_issue,
                    require_marked=True,
                ):
                    continue
                issues.append(artifact_issue)
        for action_run_id in action_run_ids:
            if not self.store.get_run(action_run_id):
                issues.append(f"{action_run_id}: action run reference is dead")
        for observation_run_id in observation_run_ids:
            if not self.store.get_run(observation_run_id):
                issues.append(f"{observation_run_id}: observation run reference is dead")
        issues = _ordered_unique(issues)

        manifest_id = f"evidence.manifest.{run.id.removeprefix('evidence.run.')}"
        completed_run = run.model_copy(
            update={
                "status": "passed" if not issues else "failed",
                "ended_at": utc_now(),
                "step_ids": [item.id for item in steps],
                "artifact_ids": artifact_ids,
                "action_run_ids": action_run_ids,
                "observation_run_ids": observation_run_ids,
                "manifest_id": manifest_id,
                "error": "; ".join(issues) if issues else None,
            }
        )
        manifest = EvidenceRunManifest(
            id=manifest_id,
            evidence_run_id=run.id,
            run=completed_run,
            steps=steps,
            artifact_ids=artifact_ids,
            action_run_ids=action_run_ids,
            observation_run_ids=observation_run_ids,
            publication_issues=issues,
            publishable=not issues,
        )
        self.store.save_evidence_run(completed_run)
        self.store.save_evidence_manifest(manifest)
        self.store.append_event(
            run.id,
            "evidence_run_finished",
            {
                "manifest_id": manifest.id,
                "status": completed_run.status,
                "steps": len(steps),
                "publishable": manifest.publishable,
                "issues": issues,
            },
        )
        return manifest
