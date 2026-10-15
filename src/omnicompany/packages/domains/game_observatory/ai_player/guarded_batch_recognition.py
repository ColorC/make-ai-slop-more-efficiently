# [OMNI] origin=codex created_by=codex intent="typed guarded batch recognition and business adjudication"
"""Typed current-screen recognition and business-state adjudication for guarded batches."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models import (
    ArtifactRef,
    EvidenceRun,
    EvidenceStability,
    EvidenceStep,
    NormalizedAction,
    SourcePixelRect,
)
from ..evidence import regional_perceptual_frame_distance
from ..recognition_service import RecognitionRequestV1, RecognitionService
from .contracts import EvidenceReferenceV1, utc_now
from .guarded_batch_route import GuardedBatchLivePreflightV1
from .operation_memory import RuntimeTelemetryEventV1


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)


class EnergyRecognitionProfileV1(_StrictModel):
    region: SourcePixelRect
    minimum_confidence: float = Field(default=0.95, ge=0, le=1)
    allow_adjacent_digit_composition: bool = False
    maximum_continuity_visual_distance: float = Field(default=0.001, ge=0, le=1)


class BoardCellProfileV1(_StrictModel):
    row: int = Field(ge=0)
    column: int = Field(ge=0)


class BoardGridRecognitionProfileV1(_StrictModel):
    origin_x: int = Field(ge=0)
    origin_y: int = Field(ge=0)
    cell_width: int = Field(gt=0)
    cell_height: int = Field(gt=0)
    rows: int = Field(gt=0)
    columns: int = Field(gt=0)
    sample_inset: int = Field(default=0, ge=0)
    visual_signature_inset: int = Field(default=0, ge=0)
    playable_cells: tuple[BoardCellProfileV1, ...] = Field(min_length=1)
    empty_color_lower: tuple[int, int, int]
    empty_color_upper: tuple[int, int, int]
    color_space: Literal["bgr", "hsv"] = "hsv"
    minimum_empty_coverage_ratio: float = Field(default=0.95, ge=0, le=1)
    maximum_empty_edge_density: float = Field(default=0.02, ge=0, le=1)

    @model_validator(mode="after")
    def validate_cells(self) -> "BoardGridRecognitionProfileV1":
        identities = {(item.row, item.column) for item in self.playable_cells}
        if len(identities) != len(self.playable_cells):
            raise ValueError("playable_cells must be unique")
        if any(row >= self.rows or column >= self.columns for row, column in identities):
            raise ValueError("playable cell is outside the declared grid")
        if self.sample_inset * 2 >= min(self.cell_width, self.cell_height):
            raise ValueError("sample_inset leaves no measurable cell area")
        if self.visual_signature_inset * 2 >= min(self.cell_width, self.cell_height):
            raise ValueError("visual_signature_inset leaves no identity area")
        return self


class TemplateIdentityProfileV1(_StrictModel):
    region: SourcePixelRect
    threshold: float = Field(ge=0, le=1)
    minimum_luminance_ratio: float | None = Field(default=None, gt=0)


class ColorCoverageSignalProfileV1(_StrictModel):
    region: SourcePixelRect
    color_space: Literal["bgr", "hsv"] = "hsv"
    color_lower: tuple[int, int, int]
    color_upper: tuple[int, int, int]
    minimum_coverage_ratio: float = Field(ge=0, le=1)


class GuardedBatchRecognitionProfileV1(_StrictModel):
    schema_id: Literal["game-observatory.guarded-batch-recognition-profile.v1"] = Field(
        default="game-observatory.guarded-batch-recognition-profile.v1", alias="schema"
    )
    game_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    viewport_width: int = Field(gt=0)
    viewport_height: int = Field(gt=0)
    reference_artifact_id: str = Field(min_length=1)
    reference_artifact_sha256: str = Field(min_length=64, max_length=64)
    energy: EnergyRecognitionProfileV1
    board: BoardGridRecognitionProfileV1
    surface: TemplateIdentityProfileV1
    generator: TemplateIdentityProfileV1
    generator_selected: ColorCoverageSignalProfileV1


class BoardCellMeasurementV1(_StrictModel):
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    coverage_ratio: float = Field(ge=0, le=1)
    edge_density: float = Field(ge=0, le=1)
    empty: bool
    visual_signature: str | None = Field(default=None, min_length=16, max_length=16)


class GuardedBatchBusinessMeasurementV1(_StrictModel):
    schema_id: Literal["game-observatory.guarded-batch-business-measurement.v1"] = Field(
        default="game-observatory.guarded-batch-business-measurement.v1", alias="schema"
    )
    environment_id: str
    artifact_id: str
    artifact_sha256: str = Field(min_length=64, max_length=64)
    energy: int = Field(ge=0)
    energy_confidence: float = Field(ge=0, le=1)
    board_cells: tuple[BoardCellMeasurementV1, ...]
    empty_cells: tuple[BoardCellProfileV1, ...]
    surface_score: float = Field(ge=0, le=1)
    surface_allowed: bool
    generator_score: float = Field(ge=0, le=1)
    generator_visible: bool = False
    generator_selected_coverage_ratio: float = Field(default=0.0, ge=0, le=1)
    generator_selected: bool = False
    generator_next_tap_candidate: bool = False
    # Compatibility projection only. It now means the same fail-closed candidate
    # predicate as generator_next_tap_candidate; it no longer means "visible".
    generator_ready: bool = False
    board_ready: bool
    surface_luminance_ratio: float = Field(default=1.0, ge=0)
    forbidden_surface_signals: tuple[str, ...] = ()
    measured_at: str = Field(default_factory=utc_now)


class GuardedBatchBusinessMeasurementReceiptV1(_StrictModel):
    schema_id: Literal["game-observatory.guarded-batch-business-measurement-receipt.v1"] = Field(
        default="game-observatory.guarded-batch-business-measurement-receipt.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    environment_id: str
    session_id: str
    evidence_step_id: str
    before: GuardedBatchBusinessMeasurementV1 | None = None
    after: GuardedBatchBusinessMeasurementV1 | None = None
    action_kind: Literal["generator", "merge", "relocate"] = "generator"
    source_cell: BoardCellProfileV1 | None = None
    target_cell: BoardCellProfileV1 | None = None
    energy_delta: int | None = None
    energy_evidence_mode: Literal["canonical_ocr", "visual_continuity"] = "canonical_ocr"
    energy_visual_distance: float | None = Field(default=None, ge=0, le=1)
    empty_to_occupied: tuple[BoardCellProfileV1, ...] = ()
    occupied_to_empty: tuple[BoardCellProfileV1, ...] = ()
    target_visual_distance: int | None = Field(default=None, ge=0, le=64)
    telemetry_event_ids: tuple[str, ...] = ()
    business_state_changed: bool = False
    business_expectation_met: bool = False
    business_change: str | None = Field(default=None, min_length=1)
    measurement_mode: Literal[
        "canonical_full", "incremental_checkpoint_with_canonical_terminal"
    ] = "canonical_full"
    gaps: tuple[str, ...] = ()
    recorded_at: str = Field(default_factory=utc_now)


class GuardedBatchPreActionGuardReceiptV1(_StrictModel):
    schema_id: Literal["game-observatory.guarded-batch-pre-action-guard-receipt.v1"] = Field(
        default="game-observatory.guarded-batch-pre-action-guard-receipt.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    environment_id: str
    session_id: str
    action_id: str = Field(min_length=1)
    action_index: int = Field(ge=0)
    action_kind: Literal["generator", "merge", "relocate"]
    current_artifact_id: str = Field(min_length=1)
    measurement: GuardedBatchBusinessMeasurementV1
    source_cell: BoardCellProfileV1 | None = None
    target_cell: BoardCellProfileV1 | None = None
    reference_artifact_id: str | None = Field(default=None, min_length=1)
    current_pair_visual_distance: int | None = Field(default=None, ge=0, le=64)
    reference_pair_visual_distance: int | None = Field(default=None, ge=0, le=64)
    source_reference_visual_distance: int | None = Field(default=None, ge=0, le=64)
    target_reference_visual_distance: int | None = Field(default=None, ge=0, le=64)
    guard_checks: tuple[str, ...] = ()
    allowed: bool
    gaps: tuple[str, ...] = ()
    evidence_refs: tuple[EvidenceReferenceV1, ...] = ()
    recorded_at: str = Field(default_factory=utc_now)


def load_guarded_batch_recognition_profile(path: str | Path) -> GuardedBatchRecognitionProfileV1:
    return GuardedBatchRecognitionProfileV1.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def _artifact_path(store: Any, artifact: Any) -> Path:
    path = Path(str(artifact.path))
    return path.resolve() if path.is_absolute() else (Path(store.root) / path).resolve()


def verify_screenshot_artifact(
    store: Any,
    artifact_id: str,
    *,
    environment_id: str,
    viewport_width: int,
    viewport_height: int,
    expected_sha256: str | None = None,
) -> tuple[Any, Path]:
    artifact = store.get_artifact(artifact_id)
    if artifact is None:
        raise ValueError(f"unknown screenshot artifact: {artifact_id}")
    if artifact.kind != "screenshot":
        raise ValueError(f"artifact is not a screenshot: {artifact_id}")
    metadata = dict(artifact.metadata or {})
    if metadata.get("environment_id") != environment_id:
        raise ValueError(f"artifact environment drift: {artifact_id}")
    path = _artifact_path(store, artifact)
    if not path.is_file():
        raise FileNotFoundError(f"artifact file is missing: {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != artifact.sha256:
        raise ValueError(f"artifact sha256 mismatch: {artifact_id}")
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError(f"artifact does not match the frozen reference hash: {artifact_id}")
    expected_viewport = {"width": viewport_width, "height": viewport_height}
    for key in ("native_viewport", "declared_run_viewport"):
        if metadata.get(key) != expected_viewport:
            raise ValueError(f"artifact {key} drift: {artifact_id}")
    try:
        from PIL import Image
    except ImportError as error:  # pragma: no cover - deployment boundary
        raise RuntimeError("artifact verification requires Pillow") from error
    with Image.open(path) as image:
        if image.size != (viewport_width, viewport_height):
            raise ValueError(f"decoded artifact viewport drift: {artifact_id}")
    return artifact, path


def _single_energy(
    result: Any,
    minimum_confidence: float,
    *,
    allow_adjacent_digit_composition: bool = False,
) -> tuple[int, float]:
    numeric_candidates: list[tuple[str, float, SourcePixelRect | None]] = []
    for detection in result.detections:
        text = str(detection.text or "").strip()
        if re.fullmatch(r"\d+", text):
            numeric_candidates.append((text, float(detection.score), detection.bounds))
    if len(numeric_candidates) == 1:
        text, confidence, _bounds = numeric_candidates[0]
        if confidence < minimum_confidence:
            raise ValueError(
                "energy OCR confidence is below the configured minimum: "
                f"{confidence} < {minimum_confidence}"
            )
        return int(text), confidence
    if not allow_adjacent_digit_composition:
        raise ValueError(
            f"energy OCR requires exactly one digits-only candidate; got {len(numeric_candidates)}"
        )
    if not 2 <= len(numeric_candidates) <= 3:
        raise ValueError(
            "energy OCR adjacent-digit composition requires two or three candidates; "
            f"got {len(numeric_candidates)}"
        )
    if any(len(text) != 1 for text, _score, _bounds in numeric_candidates):
        raise ValueError("energy OCR adjacent-digit candidates must each contain one digit")
    if any(bounds is None for _text, _score, bounds in numeric_candidates):
        raise ValueError("energy OCR adjacent-digit candidates require bounds")
    low_confidence = min(score for _text, score, _bounds in numeric_candidates)
    if low_confidence < minimum_confidence:
        raise ValueError(
            "energy OCR confidence is below the configured minimum: "
            f"{low_confidence} < {minimum_confidence}"
        )
    ordered = sorted(
        numeric_candidates,
        key=lambda candidate: (
            candidate[2].x if candidate[2] is not None else -1,
            candidate[2].y if candidate[2] is not None else -1,
        ),
    )
    bounds = [candidate[2] for candidate in ordered]
    if any(item is None for item in bounds):  # pragma: no cover - guarded above
        raise ValueError("energy OCR adjacent-digit candidates require bounds")
    resolved_bounds = [item for item in bounds if item is not None]
    heights = [item.height for item in resolved_bounds]
    if min(heights) / max(heights) < 0.75:
        raise ValueError("energy OCR adjacent-digit candidates have incompatible heights")
    centers_y = [item.y + item.height / 2 for item in resolved_bounds]
    if max(centers_y) - min(centers_y) > max(heights) * 0.25:
        raise ValueError("energy OCR adjacent-digit candidates are not on one line")
    for left, right in zip(resolved_bounds, resolved_bounds[1:]):
        if right.x <= left.x:
            raise ValueError("energy OCR adjacent-digit candidates are not left-to-right")
        horizontal_gap = right.x - (left.x + left.width)
        if horizontal_gap < -min(left.width, right.width) * 0.25:
            raise ValueError("energy OCR adjacent-digit candidates overlap ambiguously")
        if horizontal_gap > max(left.height, right.height) * 0.35:
            raise ValueError("energy OCR adjacent-digit candidates are too far apart")
    return int("".join(text for text, _score, _bounds in ordered)), low_confidence


def _template_score(result: Any) -> float:
    return max((float(item.score) for item in result.detections), default=0.0)


def _region_mean_luminance(path: Path, region: SourcePixelRect) -> float:
    try:
        from PIL import Image, ImageStat
    except ImportError as error:  # pragma: no cover - deployment boundary
        raise RuntimeError("guarded batch luminance measurement requires Pillow") from error
    with Image.open(path) as source:
        image = source.convert("L")
        right = region.x + region.width
        bottom = region.y + region.height
        if region.x < 0 or region.y < 0 or right > image.width or bottom > image.height:
            raise ValueError(
                "luminance region is outside the screenshot: "
                f"({region.x},{region.y},{region.width},{region.height}) "
                f"for {image.width}x{image.height}"
            )
        return float(ImageStat.Stat(image.crop((region.x, region.y, right, bottom))).mean[0])


def _luminance_ratio(
    *,
    image_path: Path,
    reference_path: Path,
    region: SourcePixelRect,
) -> float:
    reference_luminance = _region_mean_luminance(reference_path, region)
    if reference_luminance <= 0:
        raise ValueError("reference luminance must be greater than zero")
    return _region_mean_luminance(image_path, region) / reference_luminance


def _cell_region(
    profile: BoardGridRecognitionProfileV1, cell: BoardCellProfileV1
) -> SourcePixelRect:
    inset = profile.sample_inset
    return SourcePixelRect(
        x=profile.origin_x + cell.column * profile.cell_width + inset,
        y=profile.origin_y + cell.row * profile.cell_height + inset,
        width=profile.cell_width - inset * 2,
        height=profile.cell_height - inset * 2,
    )


def _visual_signature_region(
    profile: BoardGridRecognitionProfileV1, cell: BoardCellProfileV1
) -> SourcePixelRect:
    inset = profile.visual_signature_inset
    return SourcePixelRect(
        x=profile.origin_x + cell.column * profile.cell_width + inset,
        y=profile.origin_y + cell.row * profile.cell_height + inset,
        width=profile.cell_width - inset * 2,
        height=profile.cell_height - inset * 2,
    )


def _visual_signature_image(path: Path) -> Any:
    try:
        from PIL import Image
    except ImportError as error:  # pragma: no cover - deployment boundary
        raise RuntimeError("guarded batch cell signature requires Pillow") from error
    with Image.open(path) as source:
        return source.convert("L")


def _cell_visual_signature(image: Any, region: SourcePixelRect) -> str:
    if isinstance(image, (str, Path)):
        image = _visual_signature_image(Path(image))
    right = region.x + region.width
    bottom = region.y + region.height
    if right > image.width or bottom > image.height:
        raise ValueError("board cell is outside the screenshot")
    sample = image.crop((region.x, region.y, right, bottom)).resize((9, 8))
    pixels = list(sample.get_flattened_data())
    bits = 0
    for row in range(8):
        offset = row * 9
        for column in range(8):
            bits = (bits << 1) | int(pixels[offset + column] > pixels[offset + column + 1])
    return f"{bits:016x}"


def _measure_board_cell(
    *,
    service: RecognitionService,
    image_path: Path,
    signature_image: Any,
    profile: BoardGridRecognitionProfileV1,
    cell: BoardCellProfileV1,
) -> BoardCellMeasurementV1:
    color = service.recognize(
        RecognitionRequestV1(
            kind="color_match",
            image_path=str(image_path),
            region=_cell_region(profile, cell),
            color_lower=(profile.empty_color_lower,),
            color_upper=(profile.empty_color_upper,),
            color_space=profile.color_space,
            threshold=profile.minimum_empty_coverage_ratio,
        )
    )
    if len(color.detections) != 1:
        raise ValueError(f"cell ({cell.row},{cell.column}) returned ambiguous color results")
    raw = color.detections[0].raw
    coverage = float(raw.get("coverage_ratio", -1))
    edge_density = float(raw.get("edge_density", -1))
    is_empty = (
        coverage >= profile.minimum_empty_coverage_ratio
        and edge_density <= profile.maximum_empty_edge_density
    )
    return BoardCellMeasurementV1(
        row=cell.row,
        column=cell.column,
        coverage_ratio=coverage,
        edge_density=edge_density,
        empty=is_empty,
        visual_signature=_cell_visual_signature(
            signature_image, _visual_signature_region(profile, cell)
        ),
    )


def measure_guarded_batch_artifact(
    *,
    store: Any,
    artifact_id: str,
    profile: GuardedBatchRecognitionProfileV1,
    recognition: RecognitionService | None = None,
) -> GuardedBatchBusinessMeasurementV1:
    service = recognition or RecognitionService()
    artifact, path = verify_screenshot_artifact(
        store,
        artifact_id,
        environment_id=profile.environment_id,
        viewport_width=profile.viewport_width,
        viewport_height=profile.viewport_height,
    )
    _reference, reference_path = verify_screenshot_artifact(
        store,
        profile.reference_artifact_id,
        environment_id=profile.environment_id,
        viewport_width=profile.viewport_width,
        viewport_height=profile.viewport_height,
        expected_sha256=profile.reference_artifact_sha256,
    )
    energy_result = service.recognize(
        RecognitionRequestV1(
            kind="ocr",
            image_path=str(path),
            region=profile.energy.region,
        )
    )
    energy, energy_confidence = _single_energy(
        energy_result,
        profile.energy.minimum_confidence,
        allow_adjacent_digit_composition=(profile.energy.allow_adjacent_digit_composition),
    )
    signature_image = _visual_signature_image(path)
    cells: list[BoardCellMeasurementV1] = []
    empty_cells: list[BoardCellProfileV1] = []
    for cell in profile.board.playable_cells:
        measurement = _measure_board_cell(
            service=service,
            image_path=path,
            signature_image=signature_image,
            profile=profile.board,
            cell=cell,
        )
        cells.append(measurement)
        if measurement.empty:
            empty_cells.append(cell)
    surface_result = service.recognize(
        RecognitionRequestV1(
            kind="template_match",
            image_path=str(path),
            region=profile.surface.region,
            template_paths=(str(reference_path),),
            template_region=profile.surface.region,
            threshold=profile.surface.threshold,
        )
    )
    generator_result = service.recognize(
        RecognitionRequestV1(
            kind="template_match",
            image_path=str(path),
            region=profile.generator.region,
            template_paths=(str(reference_path),),
            template_region=profile.generator.region,
            threshold=profile.generator.threshold,
        )
    )
    generator_selected_result = service.recognize(
        RecognitionRequestV1(
            kind="color_match",
            image_path=str(path),
            region=profile.generator_selected.region,
            color_lower=(profile.generator_selected.color_lower,),
            color_upper=(profile.generator_selected.color_upper,),
            color_space=profile.generator_selected.color_space,
            threshold=profile.generator_selected.minimum_coverage_ratio,
        )
    )
    if len(generator_selected_result.detections) != 1:
        raise ValueError("generator selected signal returned ambiguous color results")
    generator_selected_coverage_ratio = float(
        generator_selected_result.detections[0].raw.get("coverage_ratio", -1)
    )
    surface_score = _template_score(surface_result)
    generator_score = _template_score(generator_result)
    surface_luminance_ratio = _luminance_ratio(
        image_path=path,
        reference_path=reference_path,
        region=profile.surface.region,
    )
    luminance_allowed = (
        profile.surface.minimum_luminance_ratio is None
        or surface_luminance_ratio >= profile.surface.minimum_luminance_ratio
    )
    forbidden_surface_signals: list[str] = []
    if not luminance_allowed:
        forbidden_surface_signals.append("surface-luminance-below-minimum")
    surface_allowed = surface_score >= profile.surface.threshold and luminance_allowed
    generator_visible = generator_score >= profile.generator.threshold
    generator_selected = (
        generator_selected_coverage_ratio >= profile.generator_selected.minimum_coverage_ratio
    )
    board_ready = len(cells) == len(profile.board.playable_cells)
    generator_next_tap_candidate = (
        generator_visible
        and generator_selected
        and surface_allowed
        and board_ready
        and bool(empty_cells)
        and energy >= 1
    )
    return GuardedBatchBusinessMeasurementV1(
        environment_id=profile.environment_id,
        artifact_id=artifact_id,
        artifact_sha256=artifact.sha256,
        energy=energy,
        energy_confidence=energy_confidence,
        board_cells=tuple(cells),
        empty_cells=tuple(empty_cells),
        surface_score=surface_score,
        surface_allowed=surface_allowed,
        generator_score=generator_score,
        generator_visible=generator_visible,
        generator_selected_coverage_ratio=generator_selected_coverage_ratio,
        generator_selected=generator_selected,
        generator_next_tap_candidate=generator_next_tap_candidate,
        generator_ready=generator_next_tap_candidate,
        board_ready=board_ready,
        surface_luminance_ratio=surface_luminance_ratio,
        forbidden_surface_signals=tuple(forbidden_surface_signals),
    )


def _orientation(width: int, height: int) -> str:
    if height > width:
        return "portrait"
    if width > height:
        return "landscape"
    return "square"


def _bind_guarded_batch_preflight_artifact(
    *,
    player: Any,
    route: Any,
    runtime: Any,
    source_artifact_id: str,
    session_id: str,
    task_id: str | None,
) -> str:
    store = player.observatory_store
    environment = player.get_environment(str(route.environment_id))
    if environment is None:
        raise ValueError(f"unknown environment: {route.environment_id}")
    source = store.get_artifact(source_artifact_id)
    if source is None:
        raise ValueError(f"current-screen artifact is missing: {source_artifact_id}")
    if source.kind != "screenshot":
        raise ValueError(f"current-screen artifact is not a screenshot: {source_artifact_id}")
    source_path = _artifact_path(store, source)
    if not source_path.is_file():
        raise FileNotFoundError(f"current-screen artifact file is missing: {source_path}")
    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    if digest != source.sha256:
        raise ValueError(f"current-screen artifact sha256 mismatch: {source_artifact_id}")
    try:
        from PIL import Image
    except ImportError as error:  # pragma: no cover - deployment boundary
        raise RuntimeError("guarded batch preflight binding requires Pillow") from error
    with Image.open(source_path) as image:
        width, height = image.size
    expected = (int(environment.viewport_width), int(environment.viewport_height))
    if (width, height) != expected:
        raise ValueError(
            f"current-screen viewport drift: {width}x{height} != {expected[0]}x{expected[1]}"
        )

    evidence_run_id = f"evidence.run.guarded-batch-preflight.{uuid.uuid4().hex}"
    evidence_step_id = f"evidence.step.guarded-batch-preflight.{uuid.uuid4().hex}"
    binding_suffix = hashlib.sha256(
        f"{environment.id}:{source.id}:{session_id}:{task_id or ''}".encode("utf-8")
    ).hexdigest()[:16]
    viewport = {"width": width, "height": height}
    orientation = _orientation(width, height)
    bound = ArtifactRef.model_validate(source).model_copy(
        update={
            "id": f"art.guarded-batch-preflight.{binding_suffix}",
            "metadata": {
                **dict(source.metadata or {}),
                "environment_id": environment.id,
                "source_artifact_id": source.id,
                "session_id": session_id,
                "task_id": task_id,
                "semantic_state_eligible": True,
                "observation_only": True,
                "evidence_run_id": evidence_run_id,
                "evidence_step_id": evidence_step_id,
                "evidence_role": "guarded_batch_preflight",
                "native_viewport": viewport,
                "declared_run_viewport": viewport,
                "native_orientation": orientation,
                "declared_run_orientation": orientation,
                "viewport_relation": "exact",
            },
        }
    )
    store.save_artifact(bound)
    stamp = str(source.captured_at or utc_now())
    evidence_step = EvidenceStep(
        id=evidence_step_id,
        evidence_run_id=evidence_run_id,
        step_index=1,
        status="passed",
        started_at=stamp,
        ended_at=stamp,
        before_frame_id=bound.id,
        action=NormalizedAction(type="wait", seconds=0),
        target_name="guarded batch current-screen preflight",
        viewport_width=width,
        viewport_height=height,
        after_frame_id=bound.id,
        artifact_ids=[bound.id],
        observation_run_ids=[source.run_id] if source.run_id else [],
        stability=EvidenceStability(
            required_consecutive=1,
            observed_consecutive=1,
            final_distance=0,
            sample_distances=[0],
            sampled_frames=1,
            settled=True,
        ),
        quality_advisories=[
            "Read-only guarded-batch preflight; this does not represent a game mutation."
        ],
        metadata={
            "observation_only": True,
            "guarded_batch_preflight": True,
            "session_id": session_id,
            "task_id": task_id,
        },
    )
    evidence_run = EvidenceRun(
        id=evidence_run_id,
        target_id=str(runtime.target_id),
        adapter="adb",
        status="passed",
        game_id=environment.game_id,
        build_scope_id=environment.build_scope_id,
        scope_id=environment.id,
        viewport_width=width,
        viewport_height=height,
        orientation=orientation,
        environment={
            "environment_id": environment.id,
            "game_id": environment.game_id,
            "build_scope_id": environment.build_scope_id,
            "account_scope_id": environment.account_scope_id,
            "device_scope_id": environment.device_scope_id,
            "channel": environment.channel,
        },
        started_at=stamp,
        ended_at=stamp,
        step_ids=[evidence_step.id],
        artifact_ids=[bound.id],
        observation_run_ids=[source.run_id] if source.run_id else [],
    )
    store.save_evidence_run(evidence_run)
    store.save_evidence_step(evidence_step)
    return bound.id


def capture_guarded_batch_current_screen(
    *,
    runtime: Any,
    route: Any,
    player: Any,
    session_id: str,
    task_id: str | None = None,
    **_kwargs: Any,
) -> str:
    if runtime.lease is None or runtime.target_id is None:
        raise ValueError("DeviceGateway lease is required before current-screen capture")
    capture = runtime.gateway.capture_stream(
        runtime.target_id,
        runtime.lease.token,
        frame_count=1,
    )
    if capture.status != "passed" or len(capture.frame_artifact_ids) != 1:
        raise ValueError(
            "current-screen capture failed: "
            f"status={capture.status}, frames={len(capture.frame_artifact_ids)}"
        )
    return _bind_guarded_batch_preflight_artifact(
        player=player,
        route=route,
        runtime=runtime,
        source_artifact_id=str(capture.frame_artifact_ids[0]),
        session_id=session_id,
        task_id=task_id,
    )


def _evidence_reference(artifact: Any) -> EvidenceReferenceV1:
    metadata = dict(artifact.metadata or {})
    evidence_run_id = metadata.get("evidence_run_id")
    evidence_step_id = metadata.get("evidence_step_id")
    return EvidenceReferenceV1(
        environment_id=str(metadata["environment_id"]),
        artifact_ids=[artifact.id],
        evidence_run_ids=[str(evidence_run_id)] if evidence_run_id else [],
        evidence_step_ids=[str(evidence_step_id)] if evidence_step_id else [],
        note="guarded batch current-screen recognition measurement",
    )


class RomanticRestaurantGuardedBatchPreflightProvider:
    def __init__(
        self,
        profile: GuardedBatchRecognitionProfileV1,
        *,
        recognition: RecognitionService | None = None,
        measurement_cache: dict[str, GuardedBatchBusinessMeasurementV1] | None = None,
    ) -> None:
        self.profile = profile
        self.recognition = recognition or RecognitionService()
        self._measurement_cache = measurement_cache if measurement_cache is not None else {}

    def _measure(self, player: Any, artifact_id: str) -> GuardedBatchBusinessMeasurementV1:
        cached = self._measurement_cache.get(artifact_id)
        if cached is not None:
            return cached
        measured = measure_guarded_batch_artifact(
            store=player.observatory_store,
            artifact_id=artifact_id,
            profile=self.profile,
            recognition=self.recognition,
        )
        self._measurement_cache[artifact_id] = measured
        return measured

    def __call__(
        self,
        *,
        player: Any,
        current_artifact_id: str,
        **_kwargs: Any,
    ) -> GuardedBatchLivePreflightV1:
        measurement = self._measure(player, current_artifact_id)
        artifact = player.observatory_store.get_artifact(current_artifact_id)
        if artifact is None:  # verified above; protects static type and future stores
            raise ValueError(f"unknown screenshot artifact: {current_artifact_id}")
        return GuardedBatchLivePreflightV1(
            environment_id=self.profile.environment_id,
            current_artifact_id=current_artifact_id,
            evidence_refs=[_evidence_reference(artifact)],
            source_state_matches_reference=measurement.surface_allowed,
            empty_cells=len(measurement.empty_cells),
            energy=measurement.energy,
            generator_visible=measurement.generator_visible,
            generator_selected=measurement.generator_selected,
            generator_next_tap_candidate=measurement.generator_next_tap_candidate,
            generator_ready=measurement.generator_next_tap_candidate,
            board_ready=measurement.board_ready,
            detected_forbidden_surface_signals=list(measurement.forbidden_surface_signals),
        )


def _load_reserve_telemetry(
    *,
    player: Any,
    environment_id: str,
    session_id: str,
    evidence_step_id: str,
) -> RuntimeTelemetryEventV1:
    telemetry_id = f"runtime-telemetry.session.gateway-action-reserve.{evidence_step_id}"
    uri = f"file:{Path(player.db_path).resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT body_json FROM ai_player_runtime_telemetry_events WHERE id=?",
            (telemetry_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError(f"missing reserve telemetry: {telemetry_id}")
    event = RuntimeTelemetryEventV1.model_validate_json(row["body_json"])
    if event.id != telemetry_id:
        raise ValueError(f"telemetry identity drift: {telemetry_id}")
    if event.environment_id != environment_id:
        raise ValueError(f"telemetry environment drift: {telemetry_id}")
    if event.session_id != session_id:
        raise ValueError(f"telemetry session drift: {telemetry_id}")
    if event.event_type != "session.checkpointed":
        raise ValueError(f"unexpected reserve telemetry type: {telemetry_id}")
    if evidence_step_id not in event.evidence_step_ids:
        raise ValueError(f"telemetry EvidenceStep drift: {telemetry_id}")
    return event


def _write_business_receipt(player: Any, receipt: GuardedBatchBusinessMeasurementReceiptV1) -> Path:
    directory = Path(player.observatory_store.export_root) / "guarded_batch_business_measurements"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{receipt.id}.json"
    payload = (
        json.dumps(receipt.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2)
        + "\n"
    )
    with path.open("x", encoding="utf-8") as handle:
        handle.write(payload)
    return path


def _write_pre_action_guard_receipt(
    player: Any, receipt: GuardedBatchPreActionGuardReceiptV1
) -> Path:
    directory = Path(player.observatory_store.export_root) / "guarded_batch_pre_action_guards"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{receipt.id}.json"
    payload = (
        json.dumps(receipt.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2)
        + "\n"
    )
    with path.open("x", encoding="utf-8") as handle:
        handle.write(payload)
    return path


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _planned_action_kind(
    planned_action: Any | None,
) -> Literal["generator", "merge", "relocate"]:
    action_type = str(_field(_field(planned_action, "action"), "type") or "tap")
    if action_type == "tap":
        return "generator"
    if action_type == "swipe":
        if "empty-target-relocation" in tuple(
            _field(planned_action, "lightweight_guard_checks") or ()
        ):
            return "relocate"
        return "merge"
    raise ValueError(f"unsupported guarded-batch business action type: {action_type}")


def _point_board_cell(
    profile: BoardGridRecognitionProfileV1, *, x: Any, y: Any
) -> BoardCellProfileV1:
    if x is None or y is None:
        raise ValueError("merge action requires source and target coordinates")
    x_value = int(x)
    y_value = int(y)
    relative_x = x_value - profile.origin_x
    relative_y = y_value - profile.origin_y
    if relative_x < 0 or relative_y < 0:
        raise ValueError("merge coordinate is outside the board")
    column = relative_x // profile.cell_width
    row = relative_y // profile.cell_height
    if row >= profile.rows or column >= profile.columns:
        raise ValueError("merge coordinate is outside the board")
    known = {(item.row, item.column) for item in profile.playable_cells}
    if (row, column) not in known:
        raise ValueError(f"merge coordinate resolves to non-playable cell ({row},{column})")
    return BoardCellProfileV1(row=row, column=column)


def _cell_measurement(
    measurement: GuardedBatchBusinessMeasurementV1,
    cell: BoardCellProfileV1,
) -> BoardCellMeasurementV1 | None:
    return next(
        (
            item
            for item in measurement.board_cells
            if item.row == cell.row and item.column == cell.column
        ),
        None,
    )


def _signature_distance(before: str | None, after: str | None) -> int | None:
    if before is None or after is None:
        return None
    try:
        return (int(before, 16) ^ int(after, 16)).bit_count()
    except ValueError:
        return None


class RomanticRestaurantGuardedBatchPreActionGuard:
    """Fail-closed action-specific guard evaluated before every device mutation."""

    def __init__(
        self,
        profile: GuardedBatchRecognitionProfileV1,
        *,
        recognition: RecognitionService | None = None,
        measurement_cache: dict[str, GuardedBatchBusinessMeasurementV1] | None = None,
        maximum_merge_pair_visual_distance: int = 6,
        maximum_merge_reference_visual_distance: int = 8,
    ) -> None:
        self.profile = profile
        self.recognition = recognition or RecognitionService()
        self.maximum_merge_pair_visual_distance = maximum_merge_pair_visual_distance
        self.maximum_merge_reference_visual_distance = maximum_merge_reference_visual_distance
        self._measurement_cache = measurement_cache if measurement_cache is not None else {}

    def _measure(self, player: Any, artifact_id: str) -> GuardedBatchBusinessMeasurementV1:
        cached = self._measurement_cache.get(artifact_id)
        if cached is not None:
            return cached
        measured = measure_guarded_batch_artifact(
            store=player.observatory_store,
            artifact_id=artifact_id,
            profile=self.profile,
            recognition=self.recognition,
        )
        self._measurement_cache[artifact_id] = measured
        return measured

    def __call__(
        self,
        *,
        route: Any,
        planned_action: Any,
        action_index: int,
        player: Any,
        session_id: str,
        current_artifact_id: str,
        current_evidence_refs: list[EvidenceReferenceV1],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        action_kind = _planned_action_kind(planned_action)
        measurement = self._measure(player, current_artifact_id)
        gaps: list[str] = []
        source_cell: BoardCellProfileV1 | None = None
        target_cell: BoardCellProfileV1 | None = None
        reference_artifact_id: str | None = None
        current_pair_distance: int | None = None
        reference_pair_distance: int | None = None
        source_reference_distance: int | None = None
        target_reference_distance: int | None = None
        evidence_refs = list(current_evidence_refs)

        if measurement.environment_id != str(route.environment_id):
            gaps.append("pre-action measurement belongs to another environment")
        if not measurement.surface_allowed or not measurement.board_ready:
            gaps.append("board or surface is not allowed before action")
        if measurement.forbidden_surface_signals:
            gaps.append("forbidden surface signals detected before action")

        if action_kind == "generator":
            if not measurement.generator_visible:
                gaps.append("generator is not visible before action")
            if not measurement.generator_selected:
                gaps.append("generator is not selected before action")
            if not measurement.generator_next_tap_candidate:
                gaps.append("generator next tap is not a legal candidate before action")
            if not measurement.empty_cells:
                gaps.append("no empty board cell is available before generator action")
            if measurement.energy < 1:
                gaps.append("energy is exhausted before generator action")
        else:
            action = _field(planned_action, "action")
            try:
                source_cell = _point_board_cell(
                    self.profile.board,
                    x=_field(action, "x"),
                    y=_field(action, "y"),
                )
                target_cell = _point_board_cell(
                    self.profile.board,
                    x=_field(action, "x2"),
                    y=_field(action, "y2"),
                )
            except Exception as exc:
                gaps.append(f"merge locator mapping failed: {exc}")
            if source_cell is not None and target_cell is not None:
                if source_cell == target_cell:
                    gaps.append("merge source and target resolve to the same board cell")
                current_source = _cell_measurement(measurement, source_cell)
                current_target = _cell_measurement(measurement, target_cell)
                if current_source is None or current_target is None:
                    gaps.append("merge source or target cell measurement is missing")
                else:
                    if current_source.empty:
                        gaps.append("merge source cell is empty before action")
                    if current_target.empty and action_kind != "relocate":
                        gaps.append("merge target cell is empty before action")
                    if action_kind == "relocate":
                        if not current_target.empty:
                            gaps.append("relocation target cell is occupied before action")
                    else:
                        current_pair_distance = _signature_distance(
                            current_source.visual_signature,
                            current_target.visual_signature,
                        )
                        if current_pair_distance is None:
                            gaps.append("merge current visual signatures are unavailable")
                        elif current_pair_distance > self.maximum_merge_pair_visual_distance:
                            gaps.append(
                                "merge current pair visual distance is "
                                f"{current_pair_distance}, expected <= "
                                f"{self.maximum_merge_pair_visual_distance}"
                            )

                locator_id = str(_field(planned_action, "locator_id") or "")
                locator = next(
                    (item for item in route.locators if str(item.id) == locator_id),
                    None,
                )
                if locator is None:
                    gaps.append(f"merge locator is missing from route: {locator_id}")
                elif _field(locator, "kind") == "runtime_recognized_board_cell":
                    # The locator provenance freezes where to inspect, while the current
                    # typed pair measurement proves a pair created earlier in this batch.
                    # Requiring the source artifact itself to contain that future pair
                    # would make runtime-recognized locators impossible by construction.
                    required_runtime_check = (
                        "empty-target-relocation"
                        if action_kind == "relocate"
                        else "same-level-pair"
                    )
                    if required_runtime_check not in tuple(
                        _field(planned_action, "lightweight_guard_checks") or ()
                    ):
                        gaps.append(
                            "runtime-recognized swipe requires the "
                            f"{required_runtime_check} guard"
                        )
                elif action_kind == "merge":
                    reference_artifact_ids = list(locator.source_artifact_ids)
                    if not reference_artifact_ids:
                        gaps.append("merge locator has no frozen reference artifact")
                    else:
                        reference_artifact_id = str(reference_artifact_ids[0])
                        try:
                            reference = self._measure(player, reference_artifact_id)
                        except Exception as exc:
                            gaps.append(f"merge frozen reference measurement failed: {exc}")
                        else:
                            evidence_refs.append(
                                EvidenceReferenceV1(
                                    environment_id=self.profile.environment_id,
                                    artifact_ids=reference_artifact_ids,
                                    evidence_step_ids=list(locator.source_evidence_step_ids),
                                    note=(
                                        "frozen merge source/target identity reference for "
                                        f"{_field(planned_action, 'id')}"
                                    ),
                                )
                            )
                            reference_source = _cell_measurement(reference, source_cell)
                            reference_target = _cell_measurement(reference, target_cell)
                            if reference_source is None or reference_target is None:
                                gaps.append("merge frozen source or target measurement is missing")
                            else:
                                if reference_source.empty or reference_target.empty:
                                    gaps.append("merge frozen reference pair is not fully occupied")
                                reference_pair_distance = _signature_distance(
                                    reference_source.visual_signature,
                                    reference_target.visual_signature,
                                )
                                source_reference_distance = _signature_distance(
                                    current_source.visual_signature if current_source else None,
                                    reference_source.visual_signature,
                                )
                                target_reference_distance = _signature_distance(
                                    current_target.visual_signature if current_target else None,
                                    reference_target.visual_signature,
                                )
                                for label, distance, maximum in (
                                    (
                                        "merge frozen pair visual distance",
                                        reference_pair_distance,
                                        self.maximum_merge_pair_visual_distance,
                                    ),
                                    (
                                        "merge source/reference visual distance",
                                        source_reference_distance,
                                        self.maximum_merge_reference_visual_distance,
                                    ),
                                    (
                                        "merge target/reference visual distance",
                                        target_reference_distance,
                                        self.maximum_merge_reference_visual_distance,
                                    ),
                                ):
                                    if distance is None:
                                        gaps.append(f"{label} is unavailable")
                                    elif distance > maximum:
                                        gaps.append(f"{label} is {distance}, expected <= {maximum}")

        receipt = GuardedBatchPreActionGuardReceiptV1(
            id=f"pre-action-guard.{uuid.uuid4().hex}",
            environment_id=self.profile.environment_id,
            session_id=session_id,
            action_id=str(_field(planned_action, "id") or f"action-{action_index + 1}"),
            action_index=action_index,
            action_kind=action_kind,
            current_artifact_id=current_artifact_id,
            measurement=measurement,
            source_cell=source_cell,
            target_cell=target_cell,
            reference_artifact_id=reference_artifact_id,
            current_pair_visual_distance=current_pair_distance,
            reference_pair_visual_distance=reference_pair_distance,
            source_reference_visual_distance=source_reference_distance,
            target_reference_visual_distance=target_reference_distance,
            guard_checks=tuple(_field(planned_action, "lightweight_guard_checks") or ()),
            allowed=not gaps,
            gaps=tuple(gaps),
            evidence_refs=tuple(evidence_refs),
        )
        path = _write_pre_action_guard_receipt(player, receipt)
        return {
            "allowed": receipt.allowed,
            "rejection_reasons": list(receipt.gaps),
            "evidence_refs": list(receipt.evidence_refs),
            "receipt": receipt.model_dump(mode="json", by_alias=True),
            "receipt_path": str(path),
        }


class RomanticRestaurantGuardedBatchBusinessJudge:
    def __init__(
        self,
        profile: GuardedBatchRecognitionProfileV1,
        *,
        recognition: RecognitionService | None = None,
        measurement_cache: dict[str, GuardedBatchBusinessMeasurementV1] | None = None,
        minimum_generator_output_visual_distance: int = 4,
        minimum_merge_target_visual_distance: int = 4,
        maximum_relocation_item_visual_distance: int = 8,
    ) -> None:
        self.profile = profile
        self.recognition = recognition or RecognitionService()
        self._measurement_cache = measurement_cache if measurement_cache is not None else {}
        self._verified_reference_path: Path | None = None
        self.minimum_generator_output_visual_distance = (
            minimum_generator_output_visual_distance
        )
        self.minimum_merge_target_visual_distance = minimum_merge_target_visual_distance
        self.maximum_relocation_item_visual_distance = (
            maximum_relocation_item_visual_distance
        )

    def _measure(self, player: Any, artifact_id: str) -> GuardedBatchBusinessMeasurementV1:
        cached = self._measurement_cache.get(artifact_id)
        if cached is not None:
            return cached
        measured = measure_guarded_batch_artifact(
            store=player.observatory_store,
            artifact_id=artifact_id,
            profile=self.profile,
            recognition=self.recognition,
        )
        self._measurement_cache[artifact_id] = measured
        return measured

    def _measure_incremental_relocation(
        self,
        *,
        player: Any,
        before: GuardedBatchBusinessMeasurementV1,
        artifact_id: str,
        source_cell: BoardCellProfileV1,
        target_cell: BoardCellProfileV1,
    ) -> tuple[GuardedBatchBusinessMeasurementV1, float]:
        """Measure two cells plus unchanged energy and safety surface; terminal stays full."""

        artifact, path = verify_screenshot_artifact(
            player.observatory_store,
            artifact_id,
            environment_id=self.profile.environment_id,
            viewport_width=self.profile.viewport_width,
            viewport_height=self.profile.viewport_height,
        )
        _before_artifact, before_path = verify_screenshot_artifact(
            player.observatory_store,
            before.artifact_id,
            environment_id=self.profile.environment_id,
            viewport_width=self.profile.viewport_width,
            viewport_height=self.profile.viewport_height,
            expected_sha256=before.artifact_sha256,
        )
        energy_visual_distance = regional_perceptual_frame_distance(
            before_path,
            path,
            self.profile.energy.region,
        )
        if (
            energy_visual_distance
            > self.profile.energy.maximum_continuity_visual_distance
        ):
            raise ValueError(
                "energy region changed during incremental relocation: "
                f"{energy_visual_distance:.6f} > "
                f"{self.profile.energy.maximum_continuity_visual_distance:.6f}"
            )
        if self._verified_reference_path is None:
            _reference, self._verified_reference_path = verify_screenshot_artifact(
                player.observatory_store,
                self.profile.reference_artifact_id,
                environment_id=self.profile.environment_id,
                viewport_width=self.profile.viewport_width,
                viewport_height=self.profile.viewport_height,
                expected_sha256=self.profile.reference_artifact_sha256,
            )
        reference_path = self._verified_reference_path
        signature_image = _visual_signature_image(path)
        replacements = {
            (cell.row, cell.column): _measure_board_cell(
                service=self.recognition,
                image_path=path,
                signature_image=signature_image,
                profile=self.profile.board,
                cell=cell,
            )
            for cell in (source_cell, target_cell)
        }
        cells = tuple(
            replacements.get((cell.row, cell.column), cell) for cell in before.board_cells
        )
        empty_cells = tuple(
            BoardCellProfileV1(row=cell.row, column=cell.column)
            for cell in cells
            if cell.empty
        )
        surface_result = self.recognition.recognize(
            RecognitionRequestV1(
                kind="template_match",
                image_path=str(path),
                region=self.profile.surface.region,
                template_paths=(str(reference_path),),
                template_region=self.profile.surface.region,
                threshold=self.profile.surface.threshold,
            )
        )
        surface_score = _template_score(surface_result)
        surface_luminance_ratio = _luminance_ratio(
            image_path=path,
            reference_path=reference_path,
            region=self.profile.surface.region,
        )
        luminance_allowed = (
            self.profile.surface.minimum_luminance_ratio is None
            or surface_luminance_ratio >= self.profile.surface.minimum_luminance_ratio
        )
        forbidden_surface_signals = (
            () if luminance_allowed else ("surface-luminance-below-minimum",)
        )
        surface_allowed = surface_score >= self.profile.surface.threshold and luminance_allowed
        board_ready = len(cells) == len(self.profile.board.playable_cells)
        generator_next_tap_candidate = bool(
            before.generator_visible
            and before.generator_selected
            and surface_allowed
            and board_ready
            and empty_cells
            and before.energy >= 1
        )
        measured = GuardedBatchBusinessMeasurementV1(
            environment_id=self.profile.environment_id,
            artifact_id=artifact.id,
            artifact_sha256=artifact.sha256,
            energy=before.energy,
            energy_confidence=before.energy_confidence,
            board_cells=cells,
            empty_cells=empty_cells,
            surface_score=surface_score,
            surface_allowed=surface_allowed,
            generator_score=before.generator_score,
            generator_visible=before.generator_visible,
            generator_selected_coverage_ratio=before.generator_selected_coverage_ratio,
            generator_selected=before.generator_selected,
            generator_next_tap_candidate=generator_next_tap_candidate,
            generator_ready=generator_next_tap_candidate,
            board_ready=board_ready,
            surface_luminance_ratio=surface_luminance_ratio,
            forbidden_surface_signals=forbidden_surface_signals,
        )
        self._measurement_cache[artifact_id] = measured
        return measured, energy_visual_distance

    def __call__(
        self,
        *,
        player: Any,
        session_id: str,
        result: dict[str, Any],
        route: Any | None = None,
        planned_action: Any | None = None,
        action_index: int = 0,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        gaps: list[str] = []
        before = after = None
        source_cell = target_cell = None
        measurement_mode: Literal[
            "canonical_full", "incremental_checkpoint_with_canonical_terminal"
        ] = "canonical_full"
        energy_evidence_mode: Literal["canonical_ocr", "visual_continuity"] = (
            "canonical_ocr"
        )
        energy_visual_distance = None
        evidence_step_id = str(result.get("evidence_step_id") or "")
        before_artifact_id = str(result.get("before_artifact_id") or "")
        after_artifact_id = str(result.get("after_artifact_id") or "")
        try:
            action_kind = _planned_action_kind(planned_action)
        except Exception as exc:
            action_kind = "generator"
            gaps.append(str(exc))
        if action_kind == "relocate":
            action = _field(planned_action, "action")
            try:
                source_cell = _point_board_cell(
                    self.profile.board,
                    x=_field(action, "x"),
                    y=_field(action, "y"),
                )
                target_cell = _point_board_cell(
                    self.profile.board,
                    x=_field(action, "x2"),
                    y=_field(action, "y2"),
                )
            except Exception as exc:
                gaps.append(str(exc))
        if not evidence_step_id:
            gaps.append("missing EvidenceStep id")
        planned_actions = tuple(_field(route, "planned_actions") or ())
        incremental_relocation = bool(
            action_kind == "relocate"
            and source_cell is not None
            and target_cell is not None
            and _field(route, "execution_business_measurement_mode")
            == "incremental_checkpoints_with_canonical_terminal"
            and action_index + 1 < len(planned_actions)
        )
        if incremental_relocation:
            measurement_mode = "incremental_checkpoint_with_canonical_terminal"
            try:
                guard = dict(result.get("pre_action_guard") or {})
                guard_receipt = dict(guard.get("receipt") or {})
                before = GuardedBatchBusinessMeasurementV1.model_validate(
                    guard_receipt.get("measurement")
                )
            except Exception as exc:
                gaps.append(f"before incremental measurement failed: {exc}")
            if before is not None:
                try:
                    after, energy_visual_distance = self._measure_incremental_relocation(
                        player=player,
                        before=before,
                        artifact_id=after_artifact_id,
                        source_cell=source_cell,
                        target_cell=target_cell,
                    )
                    energy_evidence_mode = "visual_continuity"
                except Exception as exc:
                    gaps.append(f"after incremental measurement failed: {exc}")
        else:
            try:
                before = self._measure(player, before_artifact_id)
            except Exception as exc:
                gaps.append(f"before measurement failed: {exc}")
            try:
                after = self._measure(player, after_artifact_id)
            except Exception as exc:
                gaps.append(f"after measurement failed: {exc}")
        telemetry_ids: list[str] = []
        if evidence_step_id:
            try:
                telemetry = _load_reserve_telemetry(
                    player=player,
                    environment_id=self.profile.environment_id,
                    session_id=session_id,
                    evidence_step_id=evidence_step_id,
                )
                telemetry_ids.append(telemetry.id)
            except Exception as exc:
                gaps.append(str(exc))
        energy_delta = None
        empty_to_occupied: list[BoardCellProfileV1] = []
        occupied_to_empty: list[BoardCellProfileV1] = []
        target_visual_distance = None
        business_state_changed = False
        business_change = None
        if before is not None and after is not None:
            energy_delta = before.energy - after.energy
            before_empty = {(item.row, item.column) for item in before.empty_cells}
            after_empty = {(item.row, item.column) for item in after.empty_cells}
            empty_to_occupied = [
                BoardCellProfileV1(row=row, column=column)
                for row, column in sorted(before_empty - after_empty)
            ]
            occupied_to_empty = [
                BoardCellProfileV1(row=row, column=column)
                for row, column in sorted(after_empty - before_empty)
            ]
            if action_kind == "generator":
                action = _field(planned_action, "action")
                generator_output_staged = False
                try:
                    source_cell = _point_board_cell(
                        self.profile.board,
                        x=_field(action, "x"),
                        y=_field(action, "y"),
                    )
                except Exception:
                    # Legacy landed-output receipts did not require the generator to be
                    # declared as a playable board cell.  Only the staged-output branch
                    # depends on this typed locator.
                    source_cell = None
                if source_cell is not None:
                    before_generator = _cell_measurement(before, source_cell)
                    after_generator = _cell_measurement(after, source_cell)
                    target_visual_distance = _signature_distance(
                        before_generator.visual_signature if before_generator else None,
                        after_generator.visual_signature if after_generator else None,
                    )
                    target_effect = result.get("target_effect") or {}
                    generator_output_staged = bool(
                        not empty_to_occupied
                        and target_visual_distance is not None
                        and target_visual_distance
                        >= self.minimum_generator_output_visual_distance
                        and bool(target_effect.get("passed"))
                    )
                if energy_delta != 1:
                    gaps.append(f"energy delta is {energy_delta}, expected 1")
                if len(empty_to_occupied) != 1 and not generator_output_staged:
                    gaps.append(
                        "empty-to-occupied transition count is "
                        f"{len(empty_to_occupied)}, expected 1 or a typed staged output"
                    )
                if occupied_to_empty:
                    gaps.append("occupied-to-empty transitions are not allowed")
                if not after.generator_visible:
                    gaps.append("generator is not visible after action")
                business_state_changed = bool(
                    energy_delta == 1
                    and (len(empty_to_occupied) == 1 or generator_output_staged)
                    and not occupied_to_empty
                )
                if business_state_changed:
                    if empty_to_occupied:
                        changed_cell = empty_to_occupied[0]
                        business_change = (
                            f"energy {before.energy} -> {after.energy}; "
                            f"cell ({changed_cell.row},{changed_cell.column}) empty -> occupied"
                        )
                    else:
                        business_change = (
                            f"energy {before.energy} -> {after.energy}; generator output staged "
                            f"at cell ({source_cell.row},{source_cell.column}) with visual "
                            f"distance={target_visual_distance}"
                        )
            elif action_kind == "relocate":
                if source_cell == target_cell and source_cell is not None:
                    gaps.append("relocation source and target resolve to the same board cell")
                if energy_delta != 0:
                    gaps.append(f"energy delta is {energy_delta}, expected 0 for relocation")
                if source_cell is not None and target_cell is not None:
                    source_key = (source_cell.row, source_cell.column)
                    target_key = (target_cell.row, target_cell.column)
                    empty_to_occupied_keys = {
                        (item.row, item.column) for item in empty_to_occupied
                    }
                    occupied_to_empty_keys = {
                        (item.row, item.column) for item in occupied_to_empty
                    }
                    if source_key in before_empty:
                        gaps.append("relocation source cell was empty before action")
                    if target_key not in before_empty:
                        gaps.append("relocation target cell was occupied before action")
                    if occupied_to_empty_keys != {source_key}:
                        gaps.append("relocation did not empty exactly the declared source cell")
                    if empty_to_occupied_keys != {target_key}:
                        gaps.append("relocation did not occupy exactly the declared target cell")
                    before_source = _cell_measurement(before, source_cell)
                    after_target = _cell_measurement(after, target_cell)
                    target_visual_distance = _signature_distance(
                        before_source.visual_signature if before_source else None,
                        after_target.visual_signature if after_target else None,
                    )
                    if target_visual_distance is None:
                        gaps.append("relocated item visual signature is unavailable")
                    # Exact declared occupancy transitions plus unchanged energy are
                    # the canonical relocation proof.  The cell dHash is retained as
                    # a diagnostic, but cannot gate the action: the game's selection
                    # brackets move with the item and can change this signature even
                    # when the before/after frames visibly show the same piece.
                    business_state_changed = bool(
                        energy_delta == 0
                        and occupied_to_empty_keys == {source_key}
                        and empty_to_occupied_keys == {target_key}
                        and target_visual_distance is not None
                    )
                    if business_state_changed:
                        identity_note = ""
                        if (
                            target_visual_distance
                            > self.maximum_relocation_item_visual_distance
                        ):
                            identity_note = (
                                "; selection-sensitive visual identity diagnostic "
                                f"exceeds advisory threshold "
                                f"{self.maximum_relocation_item_visual_distance}"
                            )
                        business_change = (
                            f"relocate cell ({source_cell.row},{source_cell.column}) "
                            f"occupied -> empty; target ({target_cell.row},"
                            f"{target_cell.column}) empty -> occupied; moved-item visual "
                            f"distance={target_visual_distance}; energy unchanged at "
                            f"{after.energy}{identity_note}"
                        )
            else:
                action = _field(planned_action, "action")
                try:
                    source_cell = _point_board_cell(
                        self.profile.board,
                        x=_field(action, "x"),
                        y=_field(action, "y"),
                    )
                    target_cell = _point_board_cell(
                        self.profile.board,
                        x=_field(action, "x2"),
                        y=_field(action, "y2"),
                    )
                except Exception as exc:
                    gaps.append(str(exc))
                if source_cell == target_cell and source_cell is not None:
                    gaps.append("merge source and target resolve to the same board cell")
                if energy_delta != 0:
                    gaps.append(f"energy delta is {energy_delta}, expected 0 for merge")
                if empty_to_occupied:
                    gaps.append("empty-to-occupied transitions are not allowed for merge")
                if source_cell is not None and target_cell is not None:
                    source_key = (source_cell.row, source_cell.column)
                    target_key = (target_cell.row, target_cell.column)
                    if source_key in before_empty:
                        gaps.append("merge source cell was empty before action")
                    if target_key in before_empty:
                        gaps.append("merge target cell was empty before action")
                    if source_key not in after_empty:
                        gaps.append("merge source cell did not become empty")
                    if target_key in after_empty:
                        gaps.append("merge target cell became empty")
                    if {(item.row, item.column) for item in occupied_to_empty} != {source_key}:
                        gaps.append("merge did not release exactly the declared source cell")
                    before_target = _cell_measurement(before, target_cell)
                    after_target = _cell_measurement(after, target_cell)
                    target_visual_distance = _signature_distance(
                        before_target.visual_signature if before_target else None,
                        after_target.visual_signature if after_target else None,
                    )
                    if target_visual_distance is None:
                        gaps.append("merge target visual signature is unavailable")
                    elif target_visual_distance < self.minimum_merge_target_visual_distance:
                        gaps.append(
                            "merge target visual distance is "
                            f"{target_visual_distance}, expected >= "
                            f"{self.minimum_merge_target_visual_distance}"
                        )
                    business_state_changed = bool(
                        energy_delta == 0
                        and not empty_to_occupied
                        and {(item.row, item.column) for item in occupied_to_empty} == {source_key}
                        and target_key not in after_empty
                        and target_visual_distance is not None
                        and target_visual_distance >= self.minimum_merge_target_visual_distance
                    )
                    if business_state_changed:
                        business_change = (
                            f"merge cell ({source_cell.row},{source_cell.column}) occupied -> empty; "
                            f"target ({target_cell.row},{target_cell.column}) remained occupied "
                            f"and visual distance={target_visual_distance}; energy unchanged at "
                            f"{after.energy}"
                        )
            if not after.surface_allowed or not after.board_ready:
                gaps.append("board or surface is not allowed after action")
            if after.forbidden_surface_signals:
                gaps.append("forbidden surface signals detected after action")
        business_expectation_met = business_state_changed and not gaps
        receipt = GuardedBatchBusinessMeasurementReceiptV1(
            id=f"business-measurement.{uuid.uuid4().hex}",
            environment_id=self.profile.environment_id,
            session_id=session_id,
            evidence_step_id=evidence_step_id or "unavailable",
            before=before,
            after=after,
            action_kind=action_kind,
            source_cell=source_cell,
            target_cell=target_cell,
            energy_delta=energy_delta,
            energy_evidence_mode=energy_evidence_mode,
            energy_visual_distance=energy_visual_distance,
            empty_to_occupied=tuple(empty_to_occupied),
            occupied_to_empty=tuple(occupied_to_empty),
            target_visual_distance=target_visual_distance,
            telemetry_event_ids=tuple(telemetry_ids),
            business_state_changed=business_state_changed,
            business_expectation_met=business_expectation_met,
            business_change=business_change,
            measurement_mode=measurement_mode,
            gaps=tuple(gaps),
        )
        path = _write_business_receipt(player, receipt)
        return {
            "business_state_changed": business_state_changed,
            "business_state_judged": True,
            "business_expectation_met": business_expectation_met,
            "business_change": business_change,
            "business_measurement_mode": measurement_mode,
            "business_energy_evidence_mode": energy_evidence_mode,
            "business_energy_visual_distance": energy_visual_distance,
            "telemetry_event_ids": telemetry_ids,
            "business_measurement_receipt": receipt.model_dump(mode="json", by_alias=True),
            "business_measurement_receipt_path": str(path),
            "business_measurement_gaps": gaps,
        }


__all__ = [
    "BoardCellMeasurementV1",
    "BoardCellProfileV1",
    "BoardGridRecognitionProfileV1",
    "EnergyRecognitionProfileV1",
    "GuardedBatchBusinessMeasurementReceiptV1",
    "GuardedBatchPreActionGuardReceiptV1",
    "GuardedBatchBusinessMeasurementV1",
    "GuardedBatchRecognitionProfileV1",
    "RomanticRestaurantGuardedBatchBusinessJudge",
    "RomanticRestaurantGuardedBatchPreActionGuard",
    "RomanticRestaurantGuardedBatchPreflightProvider",
    "TemplateIdentityProfileV1",
    "capture_guarded_batch_current_screen",
    "load_guarded_batch_recognition_profile",
    "measure_guarded_batch_artifact",
    "verify_screenshot_artifact",
]
