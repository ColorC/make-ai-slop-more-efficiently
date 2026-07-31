"""Fail-closed contract between visual candidate discovery and device execution.

Discovery is intentionally recall-oriented and may emit heuristic candidates.  This
module defines the narrower proof that a pointer action must carry before it can
reach ``DeviceGateway``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models import ArtifactRef, NormalizedAction, SourcePixelPoint, SourcePixelRect


class InteractionExpectedChangeV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "semantic_state_change",
        "semantic_state",
        "visual_region_change",
        "numeric_metric_change",
    ]
    target_state_id: str | None = Field(default=None, min_length=1)
    region: SourcePixelRect | None = None
    min_visual_change: float | None = Field(default=None, gt=0, le=1)
    metric_key: str | None = Field(default=None, min_length=1)
    numeric_direction: Literal["increase", "decrease", "any"] | None = None
    min_numeric_delta: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def require_kind_specific_proof(self) -> "InteractionExpectedChangeV1":
        if self.kind == "semantic_state" and self.target_state_id is None:
            raise ValueError("semantic_state expected change requires target_state_id")
        if self.kind != "semantic_state" and self.target_state_id is not None:
            raise ValueError("target_state_id is only valid for semantic_state")
        if self.kind == "visual_region_change":
            if self.region is None or self.min_visual_change is None:
                raise ValueError(
                    "visual_region_change requires region and min_visual_change"
                )
        elif self.region is not None or self.min_visual_change is not None:
            raise ValueError(
                "region and min_visual_change are only valid for visual_region_change"
            )
        if self.kind == "numeric_metric_change":
            if (
                self.metric_key is None
                or self.numeric_direction is None
                or self.min_numeric_delta is None
            ):
                raise ValueError(
                    "numeric_metric_change requires metric_key, numeric_direction, "
                    "and min_numeric_delta"
                )
        elif any(
            value is not None
            for value in (
                self.metric_key,
                self.numeric_direction,
                self.min_numeric_delta,
            )
        ):
            raise ValueError(
                "numeric metric fields are only valid for numeric_metric_change"
            )
        return self


class InteractionPreflightV1(BaseModel):
    """Action-local, evidence-bound decision produced after candidate discovery."""

    model_config = ConfigDict(extra="forbid")

    source_artifact_id: str = Field(min_length=1)
    local_evidence_artifact_id: str = Field(min_length=1)
    source_viewport_width: int = Field(gt=0)
    source_viewport_height: int = Field(gt=0)
    candidate_bounds: SourcePixelRect
    candidate_kind: Literal[
        "actionable_control",
        "navigation",
        "selection_control",
        "text_input",
        "gesture_region",
    ]
    interactivity: Literal["confirmed", "heuristic", "non_interactive"]
    interactivity_evidence: list[
        Literal[
            "ui_tree_action",
            "omniparser_interactivity",
            "ocr_label_match",
            "template_match",
            "verified_transition",
            "temporal_probe",
            "visual_heuristic",
        ]
    ] = Field(min_length=1)
    selection_state: Literal["unselected", "selected", "not_applicable", "unknown"]
    layer_state: Literal["topmost", "obscured", "outside_active_overlay", "unknown"]
    recognition_observation_id: str = Field(min_length=1)
    captured_state_id: str = Field(min_length=1)
    expected_change: InteractionExpectedChangeV1
    consecutive_no_change_count: int = Field(default=0, ge=0)
    max_consecutive_no_change_before_rerecognition: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def keep_local_evidence_distinct(self) -> "InteractionPreflightV1":
        if self.source_artifact_id == self.local_evidence_artifact_id:
            raise ValueError("local interaction evidence must be distinct from the source frame")
        return self


class InteractionPreflightError(ValueError):
    pass


_STRONG_INTERACTIVITY_EVIDENCE = frozenset(
    {
        "ui_tree_action",
        "verified_transition",
        "temporal_probe",
    }
)


def _points_for_action(action: NormalizedAction) -> list[SourcePixelPoint]:
    points: list[SourcePixelPoint] = []
    if action.x is not None and action.y is not None:
        points.append(SourcePixelPoint(x=action.x, y=action.y))
    if action.x2 is not None and action.y2 is not None:
        points.append(SourcePixelPoint(x=action.x2, y=action.y2))
    if action.type == "two_finger_swipe" and len(points) == 2:
        points.extend(
            [
                SourcePixelPoint(
                    x=points[0].x + action.two_finger_offset_x,
                    y=points[0].y + action.two_finger_offset_y,
                ),
                SourcePixelPoint(
                    x=points[1].x + action.two_finger_offset_x,
                    y=points[1].y + action.two_finger_offset_y,
                ),
            ]
        )
    return points


def _artifact_path_is_valid(artifact: ArtifactRef) -> bool:
    from hashlib import sha256
    from pathlib import Path

    path = Path(artifact.path)
    return path.is_file() and sha256(path.read_bytes()).hexdigest() == artifact.sha256


def validate_interaction_preflight(
    preflight: InteractionPreflightV1,
    *,
    action: NormalizedAction,
    target_bounds: SourcePixelRect | None,
    viewport_width: int,
    viewport_height: int,
    environment_id: str,
    source_artifact: ArtifactRef | None,
    local_evidence_artifact: ArtifactRef | None,
) -> None:
    """Validate all static interaction proof before any Gateway call."""

    if preflight.interactivity != "confirmed":
        raise InteractionPreflightError("interaction candidate is not confirmed interactable")
    evidence = set(preflight.interactivity_evidence)
    has_structural_or_historical_proof = bool(
        _STRONG_INTERACTIVITY_EVIDENCE.intersection(evidence)
    )
    has_two_source_visual_proof = "omniparser_interactivity" in evidence and bool(
        {"ocr_label_match", "template_match"}.intersection(evidence)
    )
    if not (has_structural_or_historical_proof or has_two_source_visual_proof):
        raise InteractionPreflightError(
            "interaction candidate lacks structural proof or two-source visual proof"
        )
    if preflight.layer_state != "topmost":
        raise InteractionPreflightError("interaction candidate is not on the topmost active layer")
    if preflight.selection_state in {"selected", "unknown"} or (
        preflight.candidate_kind in {"navigation", "selection_control"}
        and preflight.selection_state != "unselected"
    ):
        raise InteractionPreflightError(
            "interaction candidate selection state does not prove a useful action"
        )
    if preflight.consecutive_no_change_count >= (
        preflight.max_consecutive_no_change_before_rerecognition
    ):
        raise InteractionPreflightError(
            "consecutive no-change limit reached; re-recognition is required"
        )
    if (preflight.source_viewport_width, preflight.source_viewport_height) != (
        viewport_width,
        viewport_height,
    ):
        raise InteractionPreflightError(
            "interaction source frame dimensions differ from the executor viewport"
        )
    if target_bounds is None or target_bounds != preflight.candidate_bounds:
        raise InteractionPreflightError(
            "command target bounds differ from the interaction candidate bounds"
        )
    if (
        target_bounds.x + target_bounds.width > viewport_width
        or target_bounds.y + target_bounds.height > viewport_height
    ):
        raise InteractionPreflightError("interaction candidate bounds exceed source viewport")
    points = _points_for_action(action)
    if not points:
        raise InteractionPreflightError("pointer action has no source-pixel point")
    if any(
        point.x >= viewport_width
        or point.y >= viewport_height
        or not target_bounds.contains(point)
        for point in points
    ):
        raise InteractionPreflightError(
            "pointer action point is outside the candidate bounds or source viewport"
        )

    if source_artifact is None:
        raise InteractionPreflightError("interaction source frame artifact is missing")
    if local_evidence_artifact is None:
        raise InteractionPreflightError("interaction local evidence artifact is missing")
    if source_artifact.kind not in {"screenshot", "video_frame"}:
        raise InteractionPreflightError("interaction source artifact is not a source frame")
    if local_evidence_artifact.kind not in {
        "screenshot",
        "video_frame",
        "annotated_plate",
    }:
        raise InteractionPreflightError("interaction local evidence has an invalid kind")
    for artifact in (source_artifact, local_evidence_artifact):
        if artifact.metadata.get("environment_id") != environment_id:
            raise InteractionPreflightError(
                "interaction preflight artifact belongs to another environment"
            )
        if not _artifact_path_is_valid(artifact):
            raise InteractionPreflightError(
                "interaction preflight artifact file or hash is invalid"
            )

    import cv2

    source = cv2.imread(source_artifact.path, cv2.IMREAD_UNCHANGED)
    if source is None:
        raise InteractionPreflightError("interaction source frame cannot be decoded")
    actual_height, actual_width = source.shape[:2]
    if (actual_width, actual_height) != (viewport_width, viewport_height):
        raise InteractionPreflightError(
            "decoded interaction source frame dimensions differ from source coordinates"
        )
    local = cv2.imread(local_evidence_artifact.path, cv2.IMREAD_UNCHANGED)
    expected_local = source[
        target_bounds.y : target_bounds.y + target_bounds.height,
        target_bounds.x : target_bounds.x + target_bounds.width,
    ]
    if (
        local is None
        or local.shape != expected_local.shape
        or not (local == expected_local).all()
    ):
        raise InteractionPreflightError(
            "interaction local evidence pixels do not match the source-frame crop"
        )
    if local_evidence_artifact.metadata.get("source_artifact_id") != source_artifact.id:
        raise InteractionPreflightError(
            "interaction local evidence is not bound to the source frame"
        )
    if local_evidence_artifact.metadata.get("source_bounds") != target_bounds.model_dump(
        mode="json"
    ):
        raise InteractionPreflightError(
            "interaction local evidence is not bound to the candidate bounds"
        )
    expected_decision = {
        "candidate_bounds": preflight.candidate_bounds.model_dump(mode="json"),
        "candidate_kind": preflight.candidate_kind,
        "interactivity": preflight.interactivity,
        "interactivity_evidence": list(preflight.interactivity_evidence),
        "selection_state": preflight.selection_state,
        "layer_state": preflight.layer_state,
        "recognition_observation_id": preflight.recognition_observation_id,
        "captured_state_id": preflight.captured_state_id,
        "expected_change": preflight.expected_change.model_dump(mode="json"),
        "consecutive_no_change_count": preflight.consecutive_no_change_count,
        "max_consecutive_no_change_before_rerecognition": (
            preflight.max_consecutive_no_change_before_rerecognition
        ),
    }
    if local_evidence_artifact.metadata.get("producer") != (
        "interaction-preflight-producer.v1"
    ):
        raise InteractionPreflightError(
            "interaction local evidence was not created by the canonical producer"
        )
    if local_evidence_artifact.metadata.get("source_sha256") != source_artifact.sha256:
        raise InteractionPreflightError(
            "interaction local evidence is not bound to the source frame hash"
        )
    if local_evidence_artifact.metadata.get("candidate_decision") != expected_decision:
        raise InteractionPreflightError(
            "interaction decision is not bound to the hashed local evidence"
        )


__all__ = [
    "InteractionExpectedChangeV1",
    "InteractionPreflightError",
    "InteractionPreflightV1",
    "validate_interaction_preflight",
]
