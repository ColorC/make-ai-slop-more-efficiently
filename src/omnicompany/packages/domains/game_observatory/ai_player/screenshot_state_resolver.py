"""Production semantic-state resolver for terminal screenshot evidence bundles."""

from __future__ import annotations

import hashlib
from pathlib import Path

import cv2

from ..models import ArtifactRef, EvidenceRun, EvidenceStep, RunResult
from .contracts import EvidenceReferenceV1, StateObservationFeaturesV1
from .device_executor import StateResolutionV1
from .orchestrator import AutonomousExecutorRequestV1
from .state_recognition import SemanticStateRecognizer, build_state_observation
from .store import AIPlayerStore


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}.{hashlib.sha256(payload).hexdigest()[:32]}"


def _dhash(path: Path) -> str:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"cannot decode screenshot: {path}")
    resized = cv2.resize(image, (17, 16), interpolation=cv2.INTER_AREA)
    bits = (resized[:, 1:] >= resized[:, :-1]).flatten()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:064x}"


def _region_hashes(path: Path) -> dict[str, str]:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"cannot decode screenshot: {path}")
    height, width = image.shape[:2]
    output: dict[str, str] = {}
    for row in range(3):
        for column in range(3):
            crop = image[
                row * height // 3 : (row + 1) * height // 3,
                column * width // 3 : (column + 1) * width // 3,
            ]
            resized = cv2.resize(crop, (8, 8), interpolation=cv2.INTER_AREA)
            bits = (resized >= float(resized.mean())).flatten()
            value = 0
            for bit in bits:
                value = (value << 1) | int(bit)
            output[f"grid-{row}-{column}"] = f"{value:016x}"
    return output


class ScreenshotStateResolver:
    """Recognize the before and after frames without requiring a UI tree."""

    def __init__(self, player_store: AIPlayerStore) -> None:
        self.player_store = player_store

    @staticmethod
    def _frame(artifacts: list[ArtifactRef], artifact_id: str | None) -> ArtifactRef:
        frame = next((item for item in artifacts if item.id == artifact_id), None)
        if frame is None or frame.kind != "screenshot":
            raise ValueError(f"terminal screenshot artifact is missing: {artifact_id}")
        return frame

    @staticmethod
    def _features(frame: ArtifactRef, run: EvidenceRun, channel: str) -> StateObservationFeaturesV1:
        path = Path(frame.path).resolve()
        image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if image is None:
            raise ValueError(f"cannot decode screenshot: {frame.id}")
        height, width = image.shape[:2]
        if (width, height) != (run.viewport_width, run.viewport_height):
            raise ValueError(
                f"screenshot viewport mismatch: {frame.id} is {width}x{height}, "
                f"expected {run.viewport_width}x{run.viewport_height}"
            )
        return StateObservationFeaturesV1(
            screenshot_fingerprint=_dhash(path),
            runtime_tokens=[
                f"game:{run.game_id}",
                f"build:{run.build_scope_id}",
                f"channel:{channel}",
                f"adapter:{run.adapter}",
                f"viewport:{run.viewport_width}x{run.viewport_height}",
                f"orientation:{run.orientation}",
            ],
            region_fingerprints=_region_hashes(path),
        )

    def resolve(
        self,
        *,
        request: AutonomousExecutorRequestV1,
        evidence_run: EvidenceRun,
        evidence_step: EvidenceStep,
        artifacts: list[ArtifactRef],
        action_run: RunResult,
    ) -> StateResolutionV1:
        environment = self.player_store.get_environment(request.command.environment_id)
        if environment is None:
            raise ValueError("AI-player environment is missing during state resolution")
        if evidence_run.status != "passed" or evidence_step.status != "passed":
            raise ValueError("screenshot state resolution requires terminal-passed evidence")
        before_frame = self._frame(artifacts, evidence_step.before_frame_id)
        after_frame = self._frame(artifacts, evidence_step.after_frame_id)
        recognizer = SemanticStateRecognizer(self.player_store)
        states = []
        for role, frame in (("before", before_frame), ("after", after_frame)):
            reference = EvidenceReferenceV1(
                environment_id=environment.id,
                artifact_ids=[frame.id],
                evidence_run_ids=[evidence_run.id],
                evidence_step_ids=[evidence_step.id],
                trace_run_ids=[action_run.id],
                note=f"实机动作的 {role} 截图语义状态证据。",
            )
            captured_at = frame.captured_at or evidence_step.ended_at
            observation = build_state_observation(
                environment_id=environment.id,
                viewport_width=evidence_run.viewport_width,
                viewport_height=evidence_run.viewport_height,
                features=self._features(frame, evidence_run, environment.channel),
                evidence_refs=[reference],
                observation_id=_stable_id(
                    "observation.device-evidence",
                    environment.id,
                    evidence_run.id,
                    evidence_step.id,
                    role,
                ),
                captured_at=captured_at,
                created_at=captured_at,
            )
            decision = recognizer.recognize(observation)
            state = self.player_store.get_semantic_state(environment.id, decision.state_id)
            if state is None:
                raise ValueError(f"recognized semantic state is missing: {decision.state_id}")
            states.append(state)
        before_state, after_state = states
        observed_change = (
            f"动作后语义状态从 {before_state.id} 变为 {after_state.id}。"
            if before_state.id != after_state.id
            else f"动作前后仍归入同一语义状态 {before_state.id}。"
        )
        return StateResolutionV1(
            before_state=before_state,
            after_state=after_state,
            observed_change=observed_change,
        )


__all__ = ["ScreenshotStateResolver"]