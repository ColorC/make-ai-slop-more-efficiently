"""Paired, evidence-first scoring for manual and hypothesis exploration paths."""

from __future__ import annotations

import json
import hashlib
import math
import statistics
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from PIL import Image, ImageDraw

from omnicompany.core.config import omni_workspace_root

from .models import SourcePixelPoint, SourcePixelRect, utc_now


def _fold(value: str) -> str:
    return "".join(value.casefold().split())


def _rect_overlap_ratio(
    left: dict[str, int],
    right: dict[str, int],
) -> float:
    """Return intersection over the smaller rectangle area."""
    lx, ly, lw, lh = (int(left[key]) for key in ("x", "y", "width", "height"))
    rx, ry, rw, rh = (int(right[key]) for key in ("x", "y", "width", "height"))
    if min(lw, lh, rw, rh) <= 0:
        return 0.0
    intersection_width = max(0, min(lx + lw, rx + rw) - max(lx, rx))
    intersection_height = max(0, min(ly + lh, ry + rh) - max(ly, ry))
    return (intersection_width * intersection_height) / min(lw * lh, rw * rh)


def build_coordinate_reference(
    source: Path,
    destination: Path,
    *,
    coordinate_space: Literal["normalized_1000", "source_pixels"] = "normalized_1000",
    normalized_step: int = 100,
    pixel_step: int = 100,
) -> dict[str, Any]:
    """Create a deterministic coordinate grid without changing source dimensions."""
    if normalized_step <= 0 or 1000 % normalized_step:
        raise ValueError("normalized_step must be a positive divisor of 1000")
    if pixel_step <= 0:
        raise ValueError("pixel_step must be positive")
    with Image.open(source) as opened:
        image = opened.convert("RGB")
    width, height = image.size
    draw = ImageDraw.Draw(image)
    if coordinate_space == "source_pixels":
        x_marks = [(pixel, f"X px{pixel}") for pixel in range(0, width, pixel_step)]
        y_marks = [(pixel, f"Y px{pixel}") for pixel in range(0, height, pixel_step)]
    else:
        x_marks = [
            (min(width - 1, round(normalized * width / 1000)), f"X n{normalized}")
            for normalized in range(0, 1000, normalized_step)
        ]
        y_marks = [
            (min(height - 1, round(normalized * height / 1000)), f"Y n{normalized}")
            for normalized in range(0, 1000, normalized_step)
        ]
    for x, x_label in x_marks:
        draw.line((x, 0, x, height - 1), fill=(255, 80, 80), width=1)
        draw.text(
            (x + 2, 2),
            x_label,
            fill=(255, 255, 255),
            stroke_width=2,
            stroke_fill=(0, 0, 0),
        )
    for y, y_label in y_marks:
        draw.line((0, y, width - 1, y), fill=(80, 255, 255), width=1)
        draw.text(
            (2, y + 2),
            y_label,
            fill=(255, 255, 255),
            stroke_width=2,
            stroke_fill=(0, 0, 0),
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG", optimize=True)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return {
        "source": str(source.resolve()),
        "path": str(destination.resolve()),
        "sha256": digest,
        "width": width,
        "height": height,
        "coordinate_space": coordinate_space,
        "step": pixel_step if coordinate_space == "source_pixels" else normalized_step,
    }


def build_banded_coordinate_reference(
    source: Path,
    destination: Path,
    *,
    bands: int = 4,
    pixel_step: int = 100,
) -> dict[str, Any]:
    """Arrange full-width source crops in a 2-column sheet with absolute coordinates."""
    if bands < 2 or bands % 2:
        raise ValueError("bands must be an even integer of at least 2")
    if pixel_step <= 0:
        raise ValueError("pixel_step must be positive")
    with Image.open(source) as opened:
        image = opened.convert("RGB")
    width, height = image.size
    band_height = math.ceil(height / bands)
    rows = bands // 2
    sheet = Image.new("RGB", (width * 2, band_height * rows), color=(16, 16, 16))

    for index in range(bands):
        source_y = index * band_height
        source_bottom = min(height, source_y + band_height)
        crop = image.crop((0, source_y, width, source_bottom))
        column = index % 2
        row = index // 2
        sheet_x = column * width
        sheet_y = row * band_height
        sheet.paste(crop, (sheet_x, sheet_y))
        draw = ImageDraw.Draw(sheet)
        for source_x in range(0, width, pixel_step):
            x = sheet_x + source_x
            draw.line(
                (x, sheet_y, x, sheet_y + crop.height - 1),
                fill=(255, 80, 80),
                width=1,
            )
            draw.text(
                (x + 2, sheet_y + 2),
                f"Xpx{source_x}",
                fill=(255, 255, 255),
                stroke_width=2,
                stroke_fill=(0, 0, 0),
            )
        first_y_mark = math.ceil(source_y / pixel_step) * pixel_step
        for absolute_y in range(first_y_mark, source_bottom, pixel_step):
            y = sheet_y + absolute_y - source_y
            draw.line(
                (sheet_x, y, sheet_x + width - 1, y),
                fill=(80, 255, 255),
                width=1,
            )
            draw.text(
                (sheet_x + 2, y + 2),
                f"Ypx{absolute_y}",
                fill=(255, 255, 255),
                stroke_width=2,
                stroke_fill=(0, 0, 0),
            )
        draw.rectangle(
            (sheet_x, sheet_y, sheet_x + width - 1, sheet_y + crop.height - 1),
            outline=(255, 255, 255),
            width=2,
        )
        draw.text(
            (sheet_x + width - 190, sheet_y + 4),
            f"SOURCE Y {source_y}..{source_bottom - 1}",
            fill=(255, 255, 0),
            stroke_width=2,
            stroke_fill=(0, 0, 0),
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination, format="PNG", optimize=True)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return {
        "source": str(source.resolve()),
        "path": str(destination.resolve()),
        "sha256": digest,
        "source_width": width,
        "source_height": height,
        "sheet_width": sheet.width,
        "sheet_height": sheet.height,
        "coordinate_space": "source_pixels",
        "layout": "source_bands_2_columns",
        "bands": bands,
        "pixel_step": pixel_step,
    }


def build_visual_candidate_manifest(
    fixture: "ExplorationBenchmarkFixture",
    locator_result_path: Path,
) -> dict[str, Any]:
    """Turn locator output into an auditable, untrusted geometry shortlist.

    The locator is allowed to suggest where the Experimenter should look.  It
    does not establish clickability, control semantics, or benchmark truth.
    Structural flags keep large artwork and OCR text strips out of the primary
    review shortlist without deleting them from the retained evidence.
    """
    locator_result_path = locator_result_path.resolve()
    payload = json.loads(locator_result_path.read_text(encoding="utf-8"))
    image = payload.get("image") or {}
    expected = fixture.observation
    if image.get("sha256") != expected.sha256:
        raise ValueError("visual locator image hash does not match frozen observation")
    if (
        int(image.get("width") or 0) != expected.viewport_width
        or int(image.get("height") or 0) != expected.viewport_height
    ):
        raise ValueError("visual locator viewport does not match frozen observation")

    width = expected.viewport_width
    height = expected.viewport_height
    screen_area = width * height
    candidates: list[dict[str, Any]] = []
    for element in payload.get("elements") or []:
        if element.get("interaction_candidate") is not True:
            continue
        bounds = element.get("source_bounds") or {}
        try:
            x = int(bounds["x"])
            y = int(bounds["y"])
            box_width = int(bounds["width"])
            box_height = int(bounds["height"])
        except (KeyError, TypeError, ValueError):
            continue
        if (
            box_width <= 0
            or box_height <= 0
            or x < 0
            or y < 0
            or x + box_width > width
            or y + box_height > height
        ):
            continue
        area_ratio = (box_width * box_height) / screen_area
        aspect_ratio = box_width / box_height
        center_x = x + box_width / 2
        center_y = y + box_height / 2
        flags: list[str] = []
        if area_ratio >= 0.08:
            flags.append("large-region")
        if box_height <= height * 0.04 and aspect_ratio >= 3:
            flags.append("text-strip")
        if element.get("interactivity") is not True:
            flags.append("heuristic-only")
        edge_region = ""
        if center_x <= width * 0.2 and center_y >= height * 0.8:
            edge_region = "bottom-left"
        elif center_x >= width * 0.8 and center_y >= height * 0.8:
            edge_region = "bottom-right"
        elif center_x <= width * 0.2 and center_y <= height * 0.2:
            edge_region = "top-left"
        elif center_x >= width * 0.8 and center_y <= height * 0.2:
            edge_region = "top-right"
        candidates.append(
            {
                "id": str(element.get("id") or ""),
                "type": str(element.get("type") or ""),
                "source_bounds": {
                    "x": x,
                    "y": y,
                    "width": box_width,
                    "height": box_height,
                },
                "center": {"x": round(center_x), "y": round(center_y)},
                "area_ratio": round(area_ratio, 6),
                "aspect_ratio": round(aspect_ratio, 4),
                "edge_region": edge_region,
                "interactivity_source": str(element.get("interactivity_source") or ""),
                "content": str(element.get("content") or "")[:240],
                "structural_flags": flags,
                "aligned_row_count": 1,
                "recommended_for_review": False,
            }
        )

    for candidate in candidates:
        bounds = candidate["source_bounds"]
        center_y = candidate["center"]["y"]
        row = [
            other
            for other in candidates
            if "large-region" not in other["structural_flags"]
            and abs(other["center"]["y"] - center_y)
            <= max(32, min(bounds["height"], other["source_bounds"]["height"]) // 2)
        ]
        if row:
            horizontal_span = max(
                item["source_bounds"]["x"] + item["source_bounds"]["width"]
                for item in row
            ) - min(item["source_bounds"]["x"] for item in row)
            if len(row) >= 3 and horizontal_span >= width * 0.45:
                candidate["aligned_row_count"] = len(row)

    row_gap_candidates: list[dict[str, Any]] = []
    card_rows: list[list[dict[str, Any]]] = []
    card_like = [
        item
        for item in candidates
        if item["aligned_row_count"] >= 4
        and 0.5 <= item["aspect_ratio"] <= 1.2
        and item["source_bounds"]["height"] >= height * 0.08
        and "large-region" not in item["structural_flags"]
    ]
    for candidate in sorted(card_like, key=lambda item: item["center"]["y"]):
        matching_row = next(
            (
                row
                for row in card_rows
                if abs(
                    statistics.median(item["center"]["y"] for item in row)
                    - candidate["center"]["y"]
                )
                <= 32
            ),
            None,
        )
        if matching_row is None:
            card_rows.append([candidate])
        else:
            matching_row.append(candidate)

    for row_index, row in enumerate(card_rows):
        ordered = sorted(row, key=lambda item: item["center"]["x"])
        if len(ordered) < 4:
            continue
        centers = [item["center"]["x"] for item in ordered]
        spacings = [right - left for left, right in zip(centers, centers[1:])]
        spacing = float(statistics.median(spacings))
        if spacing <= 0 or max(abs(item - spacing) for item in spacings) > spacing * 0.12:
            continue
        median_width = round(
            statistics.median(item["source_bounds"]["width"] for item in ordered)
        )
        median_height = round(
            statistics.median(item["source_bounds"]["height"] for item in ordered)
        )
        median_y = round(
            statistics.median(item["source_bounds"]["y"] for item in ordered)
        )
        inferred_centers = [centers[0] - spacing, centers[-1] + spacing]
        for side_index, inferred_center in enumerate(inferred_centers):
            inferred_x = round(inferred_center - median_width / 2)
            inferred_bounds = {
                "x": inferred_x,
                "y": median_y,
                "width": median_width,
                "height": median_height,
            }
            if (
                inferred_x < 0
                or inferred_x + median_width > width
                or median_y < 0
                or median_y + median_height > height
            ):
                continue
            has_full_size_peer = any(
                _rect_overlap_ratio(
                    inferred_bounds,
                    item["source_bounds"],
                )
                >= 0.5
                and item["source_bounds"]["height"] >= median_height * 0.7
                for item in candidates
            )
            if has_full_size_peer:
                continue
            row_gap_candidates.append(
                {
                    "id": f"synthetic.row-gap.{row_index:02d}.{side_index:02d}",
                    "type": "aligned-row-gap-candidate",
                    "source_bounds": inferred_bounds,
                    "center": {
                        "x": round(inferred_center),
                        "y": median_y + round(median_height / 2),
                    },
                    "area_ratio": round(
                        (median_width * median_height) / screen_area,
                        6,
                    ),
                    "aspect_ratio": round(median_width / median_height, 4),
                    "edge_region": "",
                    "interactivity_source": "aligned-row-gap-inference",
                    "content": f"unconfirmed gap inferred from {len(ordered)} aligned peers",
                    "structural_flags": ["synthetic-row-gap"],
                    "aligned_row_count": len(ordered) + 1,
                    "recommended_for_review": False,
                }
            )
    candidates.extend(row_gap_candidates)

    directional_gap_candidates: list[dict[str, Any]] = []
    compact_icons = [
        item
        for item in candidates
        if item["type"] == "icon"
        and item["area_ratio"] <= 0.02
        and 0.65 <= item["aspect_ratio"] <= 1.5
        and "large-region" not in item["structural_flags"]
        and "text-strip" not in item["structural_flags"]
        and item["center"]["y"] >= height * 0.55
    ]
    directional_hypotheses: list[tuple[float, dict[str, Any]]] = []
    ordered_icons = sorted(compact_icons, key=lambda item: item["center"]["x"])
    for left_index, left in enumerate(ordered_icons):
        for right in ordered_icons[left_index + 1 :]:
            median_width = float(
                statistics.median(
                    (left["source_bounds"]["width"], right["source_bounds"]["width"])
                )
            )
            median_height = float(
                statistics.median(
                    (left["source_bounds"]["height"], right["source_bounds"]["height"])
                )
            )
            row_tolerance = max(24.0, median_height * 0.35)
            if abs(left["center"]["y"] - right["center"]["y"]) > row_tolerance:
                continue
            horizontal_gap = right["center"]["x"] - left["center"]["x"]
            if not median_width * 1.8 <= horizontal_gap <= median_width * 4.5:
                continue
            row_y = (left["center"]["y"] + right["center"]["y"]) / 2
            center_x = (left["center"]["x"] + right["center"]["x"]) / 2
            half_horizontal_gap = horizontal_gap / 2
            for top in ordered_icons:
                if top is left or top is right or top["center"]["y"] >= row_y:
                    continue
                vertical_gap = row_y - top["center"]["y"]
                if abs(top["center"]["x"] - center_x) > max(28.0, median_width * 0.55):
                    continue
                if not half_horizontal_gap * 0.65 <= vertical_gap <= half_horizontal_gap * 1.5:
                    continue
                inferred_width = round(
                    statistics.median(
                        (
                            left["source_bounds"]["width"],
                            right["source_bounds"]["width"],
                            top["source_bounds"]["width"],
                        )
                    )
                )
                inferred_height = round(
                    statistics.median(
                        (
                            left["source_bounds"]["height"],
                            right["source_bounds"]["height"],
                            top["source_bounds"]["height"],
                        )
                    )
                )
                inferred_center_y = row_y + vertical_gap
                inferred_bounds = {
                    "x": round(center_x - inferred_width / 2),
                    "y": round(inferred_center_y - inferred_height / 2),
                    "width": inferred_width,
                    "height": inferred_height,
                }
                if (
                    inferred_bounds["x"] < 0
                    or inferred_bounds["y"] < 0
                    or inferred_bounds["x"] + inferred_width > width
                    or inferred_bounds["y"] + inferred_height > height
                    or inferred_center_y < height * 0.7
                ):
                    continue
                if any(
                    _rect_overlap_ratio(inferred_bounds, item["source_bounds"]) >= 0.35
                    for item in candidates
                ):
                    continue
                symmetry_error = abs(top["center"]["x"] - center_x) / max(
                    median_width,
                    1,
                )
                spacing_error = abs(vertical_gap - half_horizontal_gap) / max(
                    half_horizontal_gap,
                    1,
                )
                directional_hypotheses.append(
                    (
                        symmetry_error + spacing_error,
                        {
                            "id": "synthetic.directional-pad-gap.pending",
                            "type": "directional-pad-gap-candidate",
                            "source_bounds": inferred_bounds,
                            "center": {
                                "x": round(center_x),
                                "y": round(inferred_center_y),
                            },
                            "area_ratio": round(
                                (inferred_width * inferred_height) / screen_area,
                                6,
                            ),
                            "aspect_ratio": round(inferred_width / inferred_height, 4),
                            "edge_region": "bottom-right"
                            if center_x >= width * 0.8 and inferred_center_y >= height * 0.8
                            else "",
                            "interactivity_source": "directional-pad-gap-inference",
                            "content": (
                                "unconfirmed fourth member inferred from a symmetric "
                                "three-icon directional pad"
                            ),
                            "structural_flags": ["synthetic-directional-pad-gap"],
                            "aligned_row_count": 4,
                            "recommended_for_review": False,
                            "inference_source_ids": [top["id"], left["id"], right["id"]],
                        },
                    )
                )

    for _, hypothesis in sorted(directional_hypotheses, key=lambda item: item[0]):
        if any(
            _rect_overlap_ratio(
                hypothesis["source_bounds"],
                item["source_bounds"],
            )
            >= 0.35
            for item in directional_gap_candidates
        ):
            continue
        hypothesis["id"] = (
            f"synthetic.directional-pad-gap.{len(directional_gap_candidates):02d}"
        )
        group_id = f"synthetic.directional-pad.{len(directional_gap_candidates):02d}"
        hypothesis["structural_group_id"] = group_id
        hypothesis["structural_role"] = "bottom"
        for role, source_id in zip(
            ("top", "left", "right"),
            hypothesis["inference_source_ids"],
        ):
            source_candidate = next(
                item for item in candidates if item["id"] == source_id
            )
            source_candidate["structural_group_id"] = group_id
            source_candidate["structural_role"] = role
            if "directional-pad-member" not in source_candidate["structural_flags"]:
                source_candidate["structural_flags"].append(
                    "directional-pad-member"
                )
            source_candidate["aligned_row_count"] = max(
                source_candidate["aligned_row_count"],
                4,
            )
        directional_gap_candidates.append(hypothesis)
    candidates.extend(directional_gap_candidates)

    for candidate in candidates:
        flags = set(candidate["structural_flags"])
        candidate["recommended_for_review"] = bool(
            "large-region" not in flags
            and "text-strip" not in flags
            and "heuristic-only" not in flags
            and (
                candidate["aligned_row_count"] >= 3
                or bool(candidate["edge_region"])
            )
        )

    candidates.sort(
        key=lambda item: (
            not item["recommended_for_review"],
            item["source_bounds"]["y"],
            item["source_bounds"]["x"],
        )
    )
    manifest_hash = hashlib.sha256(locator_result_path.read_bytes()).hexdigest()
    return {
        "schema": "game-observatory.visual-candidate-manifest.v1",
        "source_result_path": str(locator_result_path),
        "source_result_sha256": manifest_hash,
        "locator": str(payload.get("locator") or ""),
        "image_sha256": expected.sha256,
        "truth_status": "untrusted-geometry-hints",
        "candidate_count": len(candidates),
        "recommended_candidate_ids": [
            item["id"] for item in candidates if item["recommended_for_review"]
        ],
        "candidates": candidates,
    }


def write_visual_candidate_manifest(
    fixture: "ExplorationBenchmarkFixture",
    locator_result_path: Path,
    destination: Path,
) -> dict[str, Any]:
    """Persist a hash-addressed visual candidate manifest for shadow sessions."""
    manifest = build_visual_candidate_manifest(fixture, locator_result_path)
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "path": str(destination),
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "candidate_count": manifest["candidate_count"],
        "recommended_candidate_ids": manifest["recommended_candidate_ids"],
    }


class FrozenExplorationObservation(BaseModel):
    artifact_id: str
    frame_path: str
    sha256: str
    viewport_width: int = Field(gt=0)
    viewport_height: int = Field(gt=0)

    def resolved_frame_path(self) -> Path:
        path = Path(self.frame_path)
        if not path.is_absolute():
            path = omni_workspace_root() / path
        return path.resolve()


class ExpectedExplorationProbe(BaseModel):
    id: str
    target_names: list[str] = Field(min_length=1)
    action_type: Literal[
        "tap", "swipe", "pinch", "two_finger_swipe", "wait", "back", "stop"
    ]
    target_bounds: SourcePixelRect | None = None
    importance: Literal["important", "normal"] = "normal"
    tags: list[str] = Field(default_factory=list)


class PriorVerifiedTarget(BaseModel):
    """A fact available to both paths before a paired exploration starts."""

    id: str
    target_name: str
    action_type: Literal["tap", "swipe", "pinch", "two_finger_swipe", "wait", "back"]
    action: dict[str, Any] = Field(default_factory=dict)
    target_bounds: SourcePixelRect | None = None
    observed_change: str
    evidence_ids: list[str] = Field(min_length=1)


class ExplorationBenchmarkFixture(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    schema_id: Literal["game-observatory.exploration-benchmark.v1"] = Field(
        default="game-observatory.exploration-benchmark.v1",
        alias="schema",
    )
    id: str
    title: str
    phase: Literal["calibration", "real", "holdout"]
    game_id: str
    build_scope_id: str
    start_state: str
    goal: str
    observation: FrozenExplorationObservation
    coordinate_space: Literal["normalized_1000", "source_pixels"] = "normalized_1000"
    allowed_action_types: list[
        Literal["tap", "swipe", "pinch", "two_finger_swipe", "wait", "back"]
    ]
    forbidden_target_terms: list[str] = Field(default_factory=list)
    max_suggestions: int = Field(default=20, ge=1, le=100)
    require_region_inspection: bool = False
    require_candidate_inventory: bool = False
    require_interaction_support: bool = False
    require_gesture_surface_separation: bool = False
    require_full_target_confirmation: bool = False
    require_expanding_target_confirmation: bool = False
    min_target_expansion_px: int = Field(default=20, ge=1, le=200)
    require_text_free_gesture_surface: bool = False
    min_region_inspections_per_candidate: int = Field(default=1, ge=1, le=2)
    max_region_inspections: int | None = Field(default=None, ge=1, le=300)
    required_inventory_candidate_ids: list[str] = Field(default_factory=list)
    prior_verified_targets: list[PriorVerifiedTarget] = Field(default_factory=list)
    expected_probes: list[ExpectedExplorationProbe] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    def shadow_scene(
        self,
        *,
        benchmark_run_id: str,
        output_root: Path,
        visual_locator_result: Path | None = None,
        prior_target_reference_manifest: Path | None = None,
    ) -> dict[str, Any]:
        frame = self.observation.resolved_frame_path()
        scene = {
            "mode": "shadow",
            "kind": "game-ui-exploration",
            "benchmark_run_id": benchmark_run_id,
            "fixture_id": self.id,
            "allowed_output_root": str(output_root.resolve()),
            "suggestion_ledger": str((output_root / "suggestions.jsonl").resolve()),
            "allowed_image_roots": [str(frame.parent)],
            "allowed_action_types": list(self.allowed_action_types),
            "coordinate_space": self.coordinate_space,
            "forbidden_target_terms": list(self.forbidden_target_terms),
            "max_suggestions": self.max_suggestions,
            "require_region_inspection": self.require_region_inspection,
            "require_candidate_inventory": self.require_candidate_inventory,
            "require_interaction_support": self.require_interaction_support,
            "require_gesture_surface_separation": self.require_gesture_surface_separation,
            "require_full_target_confirmation": self.require_full_target_confirmation,
            "require_expanding_target_confirmation": (
                self.require_expanding_target_confirmation
            ),
            "min_target_expansion_px": self.min_target_expansion_px,
            "require_text_free_gesture_surface": (
                self.require_text_free_gesture_surface
            ),
            "min_region_inspections_per_candidate": (
                self.min_region_inspections_per_candidate
            ),
            "required_inventory_candidate_ids": list(
                self.required_inventory_candidate_ids
            ),
            "prior_verified_targets": [
                item.model_dump(mode="json") for item in self.prior_verified_targets
            ],
            "observation": {
                "artifact_id": self.observation.artifact_id,
                "frame_path": str(frame),
                "sha256": self.observation.sha256,
                "viewport": {
                    "width": self.observation.viewport_width,
                    "height": self.observation.viewport_height,
                },
            },
        }
        if self.max_region_inspections is not None:
            scene["max_region_inspections"] = self.max_region_inspections
        if visual_locator_result is not None:
            scene["visual_candidate_manifest"] = build_visual_candidate_manifest(
                self,
                visual_locator_result,
            )
        if prior_target_reference_manifest is not None:
            reference = json.loads(
                prior_target_reference_manifest.resolve().read_text(encoding="utf-8")
            )
            if reference.get("fixture_id") != self.id:
                raise ValueError("prior target reference fixture_id does not match fixture")
            if reference.get("source_image_sha256") != self.observation.sha256:
                raise ValueError("prior target reference image hash does not match fixture")
            scene["prior_target_reference"] = reference
        return scene


def build_prior_target_reference(
    fixture: ExplorationBenchmarkFixture,
    destination: Path,
    *,
    padding: int = 56,
    columns: int = 2,
) -> dict[str, Any]:
    """Build an internal contact sheet separating target pixels from context."""

    if not fixture.prior_verified_targets:
        raise ValueError("fixture has no prior_verified_targets")
    if padding < 8:
        raise ValueError("padding must be at least 8 pixels")
    if columns < 1:
        raise ValueError("columns must be positive")
    source = fixture.observation.resolved_frame_path()
    if hashlib.sha256(source.read_bytes()).hexdigest() != fixture.observation.sha256:
        raise ValueError("source image hash does not match frozen observation")
    with Image.open(source) as opened:
        image = opened.convert("RGB")
    source_width, source_height = image.size
    cell_width = 560
    cell_height = 420
    label_height = 44
    rows = math.ceil(len(fixture.prior_verified_targets) / columns)
    sheet = Image.new(
        "RGB",
        (cell_width * columns, cell_height * rows),
        color=(18, 18, 18),
    )
    draw = ImageDraw.Draw(sheet)
    items: list[dict[str, Any]] = []
    for index, fact in enumerate(fixture.prior_verified_targets, 1):
        bounds = fact.target_bounds
        if bounds is None:
            raise ValueError(f"prior verified target has no bounds: {fact.id}")
        left = max(0, bounds.x - padding)
        top = max(0, bounds.y - padding)
        right = min(source_width, bounds.x + bounds.width + padding)
        bottom = min(source_height, bounds.y + bounds.height + padding)
        crop = image.crop((left, top, right, bottom))
        usable_width = cell_width - 24
        usable_height = cell_height - label_height - 24
        scale = min(usable_width / crop.width, usable_height / crop.height)
        scaled_size = (
            max(1, round(crop.width * scale)),
            max(1, round(crop.height * scale)),
        )
        scaled = crop.resize(scaled_size, Image.Resampling.LANCZOS)
        column = (index - 1) % columns
        row = (index - 1) // columns
        cell_x = column * cell_width
        cell_y = row * cell_height
        paste_x = cell_x + (cell_width - scaled.width) // 2
        paste_y = cell_y + label_height + (usable_height - scaled.height) // 2
        sheet.paste(scaled, (paste_x, paste_y))
        target_x = paste_x + round((bounds.x - left) * scale)
        target_y = paste_y + round((bounds.y - top) * scale)
        target_right = target_x + round(bounds.width * scale)
        target_bottom = target_y + round(bounds.height * scale)
        draw.rectangle(
            (target_x, target_y, target_right, target_bottom),
            outline=(255, 64, 64),
            width=5,
        )
        label = (
            f"TARGET {index:02d} | src x={bounds.x} y={bounds.y} "
            f"w={bounds.width} h={bounds.height}"
        )
        draw.text(
            (cell_x + 12, cell_y + 12),
            label,
            fill=(255, 255, 0),
            stroke_width=2,
            stroke_fill=(0, 0, 0),
        )
        draw.rectangle(
            (cell_x, cell_y, cell_x + cell_width - 1, cell_y + cell_height - 1),
            outline=(255, 255, 255),
            width=2,
        )
        items.append(
            {
                "index": index,
                "fact_id": fact.id,
                "target_name": fact.target_name,
                "source_bounds": bounds.model_dump(mode="json"),
                "source_crop": {
                    "x": left,
                    "y": top,
                    "width": right - left,
                    "height": bottom - top,
                },
                "sheet_target_bounds": {
                    "x": target_x,
                    "y": target_y,
                    "width": target_right - target_x,
                    "height": target_bottom - target_y,
                },
            }
        )
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination, format="PNG", optimize=True)
    result = {
        "schema": "game-observatory.prior-target-reference.v1",
        "fixture_id": fixture.id,
        "source_image": str(source),
        "source_image_sha256": fixture.observation.sha256,
        "path": str(destination),
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "sheet_width": sheet.width,
        "sheet_height": sheet.height,
        "columns": columns,
        "padding": padding,
        "items": items,
    }
    manifest = destination.with_suffix(".json")
    manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["manifest"] = str(manifest)
    return result


class ExplorationProbeRecord(BaseModel):
    schema_id: str = Field(alias="schema")
    id: str
    session_id: str
    iteration: int = Field(ge=0)
    benchmark_run_id: str | None = None
    proposed_at: str
    status: str
    executed: bool
    eligible_for_execution: bool
    observation: dict[str, Any]
    target_name: str
    visible_cue: str = ""
    action: dict[str, Any]
    target_bounds: dict[str, Any] | None = None
    expected_change: str
    rationale: str
    risk_flags: list[str] = Field(default_factory=list)
    policy_issues: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    generator: dict[str, Any] = Field(default_factory=dict)
    adjudication: dict[str, Any] = Field(default_factory=dict)


class ProbeAdjudication(BaseModel):
    probe_id: str
    visible_target_supported: bool | None = None
    geometry_supported: bool | None = None
    safety_violation: bool = False
    unsupported_fact: bool = False
    verified_novel_transition: bool = False
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)


class ExplorationAdjudicationSet(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    schema_id: Literal["game-observatory.exploration-adjudication.v1"] = Field(
        default="game-observatory.exploration-adjudication.v1",
        alias="schema",
    )
    fixture_id: str
    benchmark_run_id: str
    adjudicator: str
    generated_at: str = Field(default_factory=utc_now)
    items: list[ProbeAdjudication]


class ExplorationBenchmarkScore(BaseModel):
    fixture_id: str
    path: Literal["manual", "hypothesis"]
    session_id: str | None = None
    generated_at: str = Field(default_factory=utc_now)
    proposal_count: int
    eligible_proposal_count: int
    matched_expected_ids: list[str]
    missing_expected_ids: list[str]
    matched_important_ids: list[str]
    missing_important_ids: list[str]
    expected_recall: float
    important_recall: float
    precision: float
    evidence_complete_rate: float
    duplicate_rate: float
    safety_violation_count: int
    geometry_failure_count: int
    unsupported_fact_count: int
    verified_novel_transition_count: int
    human_intervention_seconds: float = Field(ge=0)
    elapsed_seconds: float = Field(ge=0)
    issues: list[str] = Field(default_factory=list)


class PairedExplorationVerdict(BaseModel):
    fixture_id: str
    manual: ExplorationBenchmarkScore
    hypothesis: ExplorationBenchmarkScore
    quality_floor_passed: bool
    material_gains: list[str]
    regressions: list[str]
    strict_dominance: bool


def load_fixture(path: Path) -> ExplorationBenchmarkFixture:
    return ExplorationBenchmarkFixture.model_validate_json(path.read_text(encoding="utf-8"))


def load_probe_ledger(path: Path) -> tuple[list[ExplorationProbeRecord], list[str]]:
    records: list[ExplorationProbeRecord] = []
    issues: list[str] = []
    if not path.is_file():
        return records, [f"ledger is missing: {path}"]
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(ExplorationProbeRecord.model_validate_json(line))
        except ValueError as exc:
            issues.append(f"line {line_number}: {exc}")
    return records, issues


def load_adjudications(path: Path) -> ExplorationAdjudicationSet:
    return ExplorationAdjudicationSet.model_validate_json(path.read_text(encoding="utf-8"))


def _geometry_matches(record: ExplorationProbeRecord, expected: ExpectedExplorationProbe) -> bool:
    if expected.target_bounds is None:
        return True
    action = record.action
    record_bounds = record.target_bounds
    if not isinstance(record_bounds, dict):
        return False
    expected_bounds = expected.target_bounds.model_dump()
    try:
        if _rect_overlap_ratio(record_bounds, expected_bounds) < 0.7:
            return False
    except (KeyError, TypeError, ValueError):
        return False

    action_type = str(action.get("type") or "")

    def inside(x_key: str, y_key: str, *, offset_x: int = 0, offset_y: int = 0) -> bool:
        try:
            point = SourcePixelPoint(
                x=int(action.get(x_key)) + offset_x,
                y=int(action.get(y_key)) + offset_y,
            )
        except (TypeError, ValueError):
            return False
        return expected.target_bounds.contains(point)

    if action_type in {"tap", "pinch"}:
        return inside("x", "y")
    if action_type == "swipe":
        return inside("x", "y") and inside("x2", "y2")
    if action_type == "two_finger_swipe":
        try:
            offset_x = int(action.get("two_finger_offset_x") or 0)
            offset_y = int(action.get("two_finger_offset_y") or 0)
        except (TypeError, ValueError):
            return False
        return (
            inside("x", "y")
            and inside("x2", "y2")
            and inside("x", "y", offset_x=offset_x, offset_y=offset_y)
            and inside("x2", "y2", offset_x=offset_x, offset_y=offset_y)
        )
    return True


def _match_expected(
    record: ExplorationProbeRecord,
    expected: list[ExpectedExplorationProbe],
) -> str | None:
    folded_target = _fold(record.target_name)
    action_type = str(record.action.get("type") or "")
    geometric_matches = [
        item
        for item in expected
        if item.action_type == action_type
        and item.target_bounds is not None
        and _geometry_matches(record, item)
    ]
    if len(geometric_matches) == 1:
        return geometric_matches[0].id
    for item in expected:
        if item.action_type != action_type:
            continue
        aliases = [_fold(name) for name in item.target_names]
        if not any(alias in folded_target or folded_target in alias for alias in aliases):
            continue
        if _geometry_matches(record, item):
            return item.id
    return None


def _evidence_complete(
    record: ExplorationProbeRecord,
    fixture: ExplorationBenchmarkFixture,
) -> bool:
    observation = record.observation
    viewport = observation.get("viewport") or {}
    base = (
        record.executed is False
        and observation.get("artifact_id") == fixture.observation.artifact_id
        and observation.get("sha256") == fixture.observation.sha256
        and viewport.get("width") == fixture.observation.viewport_width
        and viewport.get("height") == fixture.observation.viewport_height
        and fixture.observation.artifact_id in record.evidence_ids
        and bool(record.target_name.strip())
        and (
            record.schema_id != "game-observatory.exploration-probe.v2"
            or bool(record.visible_cue.strip())
        )
        and bool(record.expected_change.strip())
        and bool(record.rationale.strip())
        and bool(record.action.get("type"))
    )
    if record.action.get("type") in {"tap", "pinch"}:
        base = base and isinstance(record.target_bounds, dict)
    if record.action.get("type") == "swipe":
        base = base and all(record.action.get(key) is not None for key in ("x", "y", "x2", "y2"))
    if record.action.get("type") == "pinch":
        base = base and all(
            record.action.get(key) is not None
            for key in ("x", "y", "pinch_direction", "pinch_percent", "pinch_steps")
        )
    if record.action.get("type") == "two_finger_swipe":
        base = base and all(
            record.action.get(key) is not None
            for key in (
                "x",
                "y",
                "x2",
                "y2",
                "two_finger_offset_x",
                "two_finger_offset_y",
                "two_finger_steps",
            )
        ) and bool(
            record.action.get("two_finger_offset_x")
            or record.action.get("two_finger_offset_y")
        )
    return bool(base)


def score_probe_ledger(
    fixture: ExplorationBenchmarkFixture,
    ledger_path: Path,
    *,
    path: Literal["manual", "hypothesis"],
    session_id: str | None = None,
    adjudication_path: Path | None = None,
    human_intervention_seconds: float = 0,
    elapsed_seconds: float = 0,
) -> ExplorationBenchmarkScore:
    records, issues = load_probe_ledger(ledger_path)
    available_session_ids = sorted({record.session_id for record in records})
    if session_id is not None:
        records = [record for record in records if record.session_id == session_id]
        if not records:
            issues.append(f"ledger does not contain requested session_id: {session_id}")
    elif len(available_session_ids) > 1:
        issues.append(
            "ledger contains multiple sessions; pass session_id to select exactly one run: "
            + ", ".join(available_session_ids)
        )
        records = []
    adjudications: dict[str, ProbeAdjudication] = {}
    if adjudication_path is not None:
        try:
            adjudication_set = load_adjudications(adjudication_path)
        except (OSError, ValueError) as exc:
            issues.append(f"adjudication could not be loaded: {exc}")
        else:
            if adjudication_set.fixture_id != fixture.id:
                issues.append("adjudication fixture_id does not match fixture")
            adjudications = {item.probe_id: item for item in adjudication_set.items}
            record_ids = {item.id for item in records}
            for probe_id in adjudications:
                if probe_id not in record_ids:
                    issues.append(f"adjudication references unknown probe: {probe_id}")
    expected_by_id = {item.id: item for item in fixture.expected_probes}
    matched_ids: list[str] = []
    eligible_count = 0
    safety_violations = 0
    geometry_failures = 0
    unsupported = 0
    novel = 0
    complete = 0
    proposal_keys: list[tuple[str, str]] = []

    for record in records:
        if _evidence_complete(record, fixture):
            complete += 1
        else:
            issues.append(f"{record.id}: evidence is incomplete")
        target_folded = _fold(record.target_name)
        forbidden = any(_fold(term) in target_folded for term in fixture.forbidden_target_terms)
        hard_policy_issues = [
            issue for issue in record.policy_issues
            if issue != "proposal declares risk flags"
        ]
        adjudication = dict(record.adjudication)
        if record.id in adjudications:
            adjudication.update(adjudications[record.id].model_dump())
        safety_failed = (
            bool(hard_policy_issues)
            or forbidden
            or adjudication.get("safety_violation") is True
        )
        if safety_failed:
            safety_violations += 1
        geometry_rejected = adjudication.get("geometry_supported") is False
        interaction_rejected = adjudication.get("visible_target_supported") is False
        if (
            record.eligible_for_execution
            and not safety_failed
            and not geometry_rejected
            and not interaction_rejected
        ):
            eligible_count += 1
            matched = _match_expected(record, fixture.expected_probes)
            if matched:
                matched_ids.append(matched)
        proposal_keys.append((target_folded, str(record.action.get("type") or "")))
        if adjudication.get("unsupported_fact") is True:
            unsupported += 1
        if adjudication.get("geometry_supported") is False:
            geometry_failures += 1
        if adjudication.get("verified_novel_transition") is True:
            novel += 1

    matched_unique = list(dict.fromkeys(matched_ids))
    expected_ids = list(expected_by_id)
    important_ids = [item.id for item in fixture.expected_probes if item.importance == "important"]
    matched_important = [item for item in matched_unique if item in important_ids]
    duplicate_count = len(proposal_keys) - len(set(proposal_keys))
    proposal_count = len(records)
    matched_eligible_count = len([item for item in matched_ids])
    return ExplorationBenchmarkScore(
        fixture_id=fixture.id,
        path=path,
        session_id=session_id or (available_session_ids[0] if len(available_session_ids) == 1 else None),
        proposal_count=proposal_count,
        eligible_proposal_count=eligible_count,
        matched_expected_ids=matched_unique,
        missing_expected_ids=[item for item in expected_ids if item not in matched_unique],
        matched_important_ids=matched_important,
        missing_important_ids=[item for item in important_ids if item not in matched_important],
        expected_recall=(len(matched_unique) / len(expected_ids)) if expected_ids else 1.0,
        important_recall=(len(matched_important) / len(important_ids)) if important_ids else 1.0,
        precision=(matched_eligible_count / eligible_count) if eligible_count else 0.0,
        evidence_complete_rate=(complete / proposal_count) if proposal_count else 0.0,
        duplicate_rate=(duplicate_count / proposal_count) if proposal_count else 0.0,
        safety_violation_count=safety_violations,
        geometry_failure_count=geometry_failures,
        unsupported_fact_count=unsupported,
        verified_novel_transition_count=novel,
        human_intervention_seconds=human_intervention_seconds,
        elapsed_seconds=elapsed_seconds,
        issues=list(dict.fromkeys(issues)),
    )


def compare_paired_scores(
    manual: ExplorationBenchmarkScore,
    hypothesis: ExplorationBenchmarkScore,
) -> PairedExplorationVerdict:
    if manual.fixture_id != hypothesis.fixture_id:
        raise ValueError("paired scores must use the same fixture")
    regressions: list[str] = []
    if hypothesis.evidence_complete_rate < 1:
        regressions.append("hypothesis evidence completeness is below 100%")
    if hypothesis.safety_violation_count:
        regressions.append("hypothesis has safety violations")
    if hypothesis.geometry_failure_count:
        regressions.append("hypothesis has source-coordinate binding failures")
    if hypothesis.unsupported_fact_count:
        regressions.append("hypothesis has unsupported factual claims")
    if hypothesis.expected_recall < manual.expected_recall:
        regressions.append("hypothesis expected-probe recall is below manual baseline")
    if hypothesis.important_recall < manual.important_recall:
        regressions.append("hypothesis important-probe recall is below manual baseline")
    if hypothesis.precision < manual.precision:
        regressions.append("hypothesis precision is below manual baseline")
    if hypothesis.duplicate_rate > manual.duplicate_rate:
        regressions.append("hypothesis duplicate rate is above manual baseline")

    gains: list[str] = []
    if hypothesis.verified_novel_transition_count > manual.verified_novel_transition_count:
        gains.append("more verified novel transitions")
    if (
        hypothesis.expected_recall >= manual.expected_recall
        and manual.proposal_count > 0
        and hypothesis.proposal_count <= math.floor(manual.proposal_count * 0.85)
    ):
        gains.append("at least 15% fewer proposals at equal recall")
    if (
        manual.human_intervention_seconds > 0
        and hypothesis.human_intervention_seconds <= manual.human_intervention_seconds * 0.8
    ):
        gains.append("at least 20% less human intervention")
    if hypothesis.expected_recall >= manual.expected_recall + 0.05:
        gains.append("at least 5 percentage points higher recall")

    quality_floor = not regressions
    return PairedExplorationVerdict(
        fixture_id=manual.fixture_id,
        manual=manual,
        hypothesis=hypothesis,
        quality_floor_passed=quality_floor,
        material_gains=gains,
        regressions=regressions,
        strict_dominance=quality_floor and bool(gains),
    )


def write_score(path: Path, value: BaseModel) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.model_dump_json(indent=2), encoding="utf-8")
    return path


__all__ = [
    "ExplorationBenchmarkFixture",
    "ExplorationBenchmarkScore",
    "ExplorationAdjudicationSet",
    "ExplorationProbeRecord",
    "ExpectedExplorationProbe",
    "FrozenExplorationObservation",
    "PairedExplorationVerdict",
    "PriorVerifiedTarget",
    "ProbeAdjudication",
    "compare_paired_scores",
    "build_visual_candidate_manifest",
    "write_visual_candidate_manifest",
    "build_coordinate_reference",
    "build_prior_target_reference",
    "load_fixture",
    "load_adjudications",
    "build_banded_coordinate_reference",
    "load_probe_ledger",
    "score_probe_ledger",
    "write_score",
]
