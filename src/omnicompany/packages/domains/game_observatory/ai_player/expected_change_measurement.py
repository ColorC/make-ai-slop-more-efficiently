"""Deterministically compare explicit action expectations with canonical evidence."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict

from ..models import ArtifactRef
from ..store import ObservatoryStore
from .contracts import ActionQualitySampleV1, PlayerMetricDeltaV1

if TYPE_CHECKING:
    from .consolidation import CanonicalExecutionOutcomeV1
    from .orchestrator import AutonomousExecutionCommandV1


class ExpectedChangeMeasurementV1(BaseModel):
    """One recomputable answer; unavailable is explicit and never counts as success."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["measured", "unavailable"]
    matched: bool | None = None


def _unavailable() -> ExpectedChangeMeasurementV1:
    return ExpectedChangeMeasurementV1(status="unavailable", matched=None)


def _artifact_path(store: ObservatoryStore, artifact: ArtifactRef) -> Path:
    path = Path(artifact.path)
    if not path.is_absolute():
        path = store.root / path
    path = path.resolve()
    try:
        path.relative_to(store.root)
    except ValueError as error:
        raise ValueError("expected-change artifact escapes canonical root") from error
    if not path.is_file():
        raise ValueError("expected-change artifact file is missing")
    if hashlib.sha256(path.read_bytes()).hexdigest() != artifact.sha256:
        raise ValueError("expected-change artifact hash mismatch")
    return path


def _canonical_frame(
    store: ObservatoryStore,
    *,
    artifact_id: str | None,
    environment_id: str,
) -> tuple[ArtifactRef, Path] | None:
    if artifact_id is None:
        return None
    artifact = store.get_artifact(artifact_id)
    if artifact is None or artifact.kind != "screenshot":
        return None
    if artifact.metadata.get("environment_id") != environment_id:
        raise ValueError("expected-change frame belongs to another environment")
    return artifact, _artifact_path(store, artifact)


def _visual_change_fraction(before_path: Path, after_path: Path, region: object) -> float:
    with Image.open(before_path) as before_source, Image.open(after_path) as after_source:
        before = before_source.convert("RGB")
        after = after_source.convert("RGB")
        if before.size != after.size:
            raise ValueError("expected-change frames have different dimensions")
        left = int(getattr(region, "x"))
        top = int(getattr(region, "y"))
        right = left + int(getattr(region, "width"))
        bottom = top + int(getattr(region, "height"))
        if left < 0 or top < 0 or right > before.width or bottom > before.height:
            raise ValueError("expected-change region exceeds the canonical frame")
        before_bytes = before.crop((left, top, right, bottom)).tobytes()
        after_bytes = after.crop((left, top, right, bottom)).tobytes()
    pixels = (right - left) * (bottom - top)
    changed = sum(
        before_bytes[index : index + 3] != after_bytes[index : index + 3]
        for index in range(0, len(before_bytes), 3)
    )
    return changed / pixels


def measure_expected_change(
    store: ObservatoryStore,
    *,
    command: "AutonomousExecutionCommandV1",
    outcome: "CanonicalExecutionOutcomeV1",
    metric_deltas: list[PlayerMetricDeltaV1] | None = None,
) -> ExpectedChangeMeasurementV1:
    """Evaluate only the condition declared before execution."""

    preflight = command.interaction_preflight
    if preflight is None:
        return _unavailable()
    expected = preflight.expected_change
    if expected.kind == "semantic_state_change":
        return ExpectedChangeMeasurementV1(
            status="measured",
            matched=outcome.before_state.id != outcome.after_state.id,
        )
    if expected.kind == "semantic_state":
        return ExpectedChangeMeasurementV1(
            status="measured",
            matched=outcome.after_state.id == expected.target_state_id,
        )
    if expected.kind == "visual_region_change":
        before = _canonical_frame(
            store,
            artifact_id=outcome.evidence_step.before_frame_id,
            environment_id=command.environment_id,
        )
        after = _canonical_frame(
            store,
            artifact_id=outcome.evidence_step.after_frame_id,
            environment_id=command.environment_id,
        )
        if before is None or after is None:
            return _unavailable()
        try:
            fraction = _visual_change_fraction(before[1], after[1], expected.region)
        except (OSError, ValueError):
            return _unavailable()
        return ExpectedChangeMeasurementV1(
            status="measured",
            matched=fraction >= float(expected.min_visual_change),
        )

    matches = [
        item for item in metric_deltas or [] if item.metric_key == expected.metric_key
    ]
    if len(matches) != 1:
        return _unavailable()
    delta = matches[0].delta
    minimum = float(expected.min_numeric_delta)
    if expected.numeric_direction == "increase":
        matched = delta >= minimum and delta > 0
    elif expected.numeric_direction == "decrease":
        matched = -delta >= minimum and delta < 0
    else:
        matched = abs(delta) >= minimum
    return ExpectedChangeMeasurementV1(status="measured", matched=matched)


def attach_expected_change_measurement(
    store: ObservatoryStore,
    *,
    sample: ActionQualitySampleV1,
    command: "AutonomousExecutionCommandV1",
    outcome: "CanonicalExecutionOutcomeV1",
) -> ActionQualitySampleV1:
    measurement = measure_expected_change(
        store,
        command=command,
        outcome=outcome,
        metric_deltas=sample.account_metric_deltas,
    )
    return sample.model_copy(
        update={
            "expected_change_measurement_status": measurement.status,
            "expected_change_matched": measurement.matched,
        }
    )


__all__ = [
    "ExpectedChangeMeasurementV1",
    "attach_expected_change_measurement",
    "measure_expected_change",
]
