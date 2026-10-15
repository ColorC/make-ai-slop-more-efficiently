"""Versioned 7 x 9 board-to-source-pixel geometry for Romantic Restaurant."""

from __future__ import annotations

import hashlib
import io
import math
from datetime import datetime, timezone
from typing import Literal

import numpy as np
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models import SourcePixelRect


EXPECTED_SCREEN = (1080, 1920)
BOARD_ROWS = 9
BOARD_COLS = 7
CALIBRATION_METHOD = "edge_projection_grid_fit_v1"


class BoardGeometryCalibrationError(ValueError):
    """Raised when a screenshot cannot produce the fixed board geometry."""


class BoardGeometryEvidenceV1(BaseModel):
    """Reproducible measurements supporting one geometry calibration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    screenshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confidence: float = Field(ge=0, le=1)
    residual_px: float = Field(ge=0)
    x_residual_px: float = Field(ge=0)
    y_residual_px: float = Field(ge=0)
    detected_bounds: SourcePixelRect
    x_grid_lines: list[int] = Field(min_length=8, max_length=8)
    y_grid_lines: list[int] = Field(min_length=10, max_length=10)


class BoardGeometryV1(BaseModel):
    """A zero-based board lattice in 1080 x 1920 source-pixel coordinates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    origin_x: float = Field(ge=0)
    origin_y: float = Field(ge=0)
    cell_w: float = Field(gt=0)
    cell_h: float = Field(gt=0)
    rows: int = Field(default=BOARD_ROWS, ge=1)
    cols: int = Field(default=BOARD_COLS, ge=1)
    screen_w: int = Field(default=EXPECTED_SCREEN[0], ge=1)
    screen_h: int = Field(default=EXPECTED_SCREEN[1], ge=1)
    calibrated_at: str = Field(min_length=1)
    method: Literal["edge_projection_grid_fit_v1"] = CALIBRATION_METHOD
    evidence: BoardGeometryEvidenceV1

    @model_validator(mode="after")
    def validate_bounds(self) -> BoardGeometryV1:
        if self.origin_x + self.cols * self.cell_w > self.screen_w + 0.5:
            raise ValueError("board exceeds screen width")
        if self.origin_y + self.rows * self.cell_h > self.screen_h + 0.5:
            raise ValueError("board exceeds screen height")
        return self

    def _validate_cell(self, row: int, col: int) -> None:
        if isinstance(row, bool) or isinstance(col, bool):
            raise TypeError("row and col must be integers")
        if not isinstance(row, int) or not isinstance(col, int):
            raise TypeError("row and col must be integers")
        if not 0 <= row < self.rows or not 0 <= col < self.cols:
            raise IndexError(f"cell ({row}, {col}) is outside the board")

    def cell_center(self, row: int, col: int) -> tuple[int, int]:
        """Return the source-pixel center of a zero-based cell."""

        self._validate_cell(row, col)
        return (
            round(self.origin_x + (col + 0.5) * self.cell_w),
            round(self.origin_y + (row + 0.5) * self.cell_h),
        )

    def cell_rect(self, row: int, col: int) -> SourcePixelRect:
        """Return the source-pixel rectangle of a zero-based cell."""

        self._validate_cell(row, col)
        left = round(self.origin_x + col * self.cell_w)
        top = round(self.origin_y + row * self.cell_h)
        right = round(self.origin_x + (col + 1) * self.cell_w)
        bottom = round(self.origin_y + (row + 1) * self.cell_h)
        return SourcePixelRect(
            x=left,
            y=top,
            width=max(1, right - left),
            height=max(1, bottom - top),
        )

    def point_to_cell(self, x: float, y: float) -> tuple[int, int] | None:
        """Map one source pixel to a zero-based cell, or return ``None``."""

        right = self.origin_x + self.cols * self.cell_w
        bottom = self.origin_y + self.rows * self.cell_h
        if not self.origin_x <= x < right or not self.origin_y <= y < bottom:
            return None
        col = math.floor((x - self.origin_x) / self.cell_w)
        row = math.floor((y - self.origin_y) / self.cell_h)
        if not 0 <= row < self.rows or not 0 <= col < self.cols:
            return None
        return row, col


def _edge_projection(rgb: np.ndarray, *, axis: Literal["x", "y"]) -> np.ndarray:
    if axis == "x":
        crop = rgb[500:1745, :, :]
        return np.abs(np.diff(crop, axis=1)).mean(axis=(0, 2))
    crop = rgb[:, 50:1030, :]
    return np.abs(np.diff(crop, axis=0)).mean(axis=(1, 2))


def _strongest_boundary(projection: np.ndarray, start: int, stop: int) -> int:
    """Return a pixel boundary; projection index i lies between pixels i and i+1."""

    window = projection[start:stop]
    if window.size == 0:
        raise BoardGeometryCalibrationError("empty board-boundary search window")
    return start + int(np.argmax(window)) + 1


def _nearest_lines(
    projection: np.ndarray,
    *,
    origin: float,
    step: float,
    count: int,
    radius: int = 7,
) -> tuple[list[int], float]:
    observed: list[int] = []
    squared_error = 0.0
    for index in range(count):
        expected = origin + index * step
        center = round(expected) - 1
        start = max(0, center - radius)
        stop = min(len(projection), center + radius + 1)
        line = start + int(np.argmax(projection[start:stop])) + 1
        observed.append(line)
        squared_error += (line - expected) ** 2
    return observed, math.sqrt(squared_error / count)


def calibrate_from_screenshot(png_bytes: bytes) -> BoardGeometryV1:
    """Fit the fixed board lattice using regularly spaced screenshot edges."""

    try:
        with Image.open(io.BytesIO(png_bytes)) as source:
            source.load()
            if source.size != EXPECTED_SCREEN:
                raise BoardGeometryCalibrationError(
                    "board calibration requires a 1080x1920 screenshot; "
                    f"got {source.width}x{source.height}"
                )
            rgb = np.asarray(source.convert("RGB"), dtype=np.float32)
    except BoardGeometryCalibrationError:
        raise
    except (OSError, UnidentifiedImageError) as exc:
        raise BoardGeometryCalibrationError("invalid screenshot image") from exc

    x_edges = _edge_projection(rgb, axis="x")
    y_edges = _edge_projection(rgb, axis="y")

    left = _strongest_boundary(x_edges, 50, 90)
    right = _strongest_boundary(x_edges, 990, 1030)
    top = _strongest_boundary(y_edges, 500, 535)
    bottom = _strongest_boundary(y_edges, 1705, 1745)
    if right <= left or bottom <= top:
        raise BoardGeometryCalibrationError("detected board bounds are invalid")

    cell_w = (right - left) / BOARD_COLS
    cell_h = (bottom - top) / BOARD_ROWS
    if not 125 <= cell_w <= 145 or not 125 <= cell_h <= 145:
        raise BoardGeometryCalibrationError("detected board cell size is implausible")

    x_lines, x_residual = _nearest_lines(
        x_edges,
        origin=float(left),
        step=cell_w,
        count=BOARD_COLS + 1,
    )
    y_lines, y_residual = _nearest_lines(
        y_edges,
        origin=float(top),
        step=cell_h,
        count=BOARD_ROWS + 1,
    )
    residual = math.sqrt((x_residual**2 + y_residual**2) / 2)
    confidence = max(0.0, min(1.0, 1.0 - residual / 16.0))
    evidence = BoardGeometryEvidenceV1(
        screenshot_sha256=hashlib.sha256(png_bytes).hexdigest(),
        confidence=round(confidence, 6),
        residual_px=round(residual, 6),
        x_residual_px=round(x_residual, 6),
        y_residual_px=round(y_residual, 6),
        detected_bounds=SourcePixelRect(
            x=left,
            y=top,
            width=right - left,
            height=bottom - top,
        ),
        x_grid_lines=x_lines,
        y_grid_lines=y_lines,
    )
    return BoardGeometryV1(
        origin_x=float(left),
        origin_y=float(top),
        cell_w=round(cell_w, 6),
        cell_h=round(cell_h, 6),
        calibrated_at=datetime.now(timezone.utc).isoformat(),
        evidence=evidence,
    )


__all__ = [
    "BOARD_COLS",
    "BOARD_ROWS",
    "BoardGeometryCalibrationError",
    "BoardGeometryEvidenceV1",
    "BoardGeometryV1",
    "calibrate_from_screenshot",
]
