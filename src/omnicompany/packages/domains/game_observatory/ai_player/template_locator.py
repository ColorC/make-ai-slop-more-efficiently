"""Deterministic dynamic template location without model calls or device access.

The locator crops a control from a reference RGB screenshot and finds the same
control inside a bounded region of a current RGB screenshot.  It uses only
OpenCV grayscale and edge template matching.  Ambiguous, weak, degenerate, or
out-of-bounds inputs fail closed instead of returning a guessed coordinate.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import hypot, isfinite
from typing import TypeAlias

import cv2
import numpy as np
from numpy.typing import NDArray


RGBImage: TypeAlias = NDArray[np.uint8]
BoundsLike: TypeAlias = "PixelBounds | tuple[int, int, int, int]"


@dataclass(frozen=True, slots=True)
class PixelBounds:
    """A half-open pixel rectangle: ``[x, x + width) x [y, y + height)``."""

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height


@dataclass(frozen=True, slots=True)
class TemplateLocation:
    """A unique, accepted match in current-image pixel coordinates."""

    resolved_bounds: PixelBounds
    score: float
    second_score: float
    scale: float


class TemplateLocationError(RuntimeError):
    """Raised whenever the locator cannot prove one acceptable match."""

    def __init__(
        self,
        message: str,
        *,
        score: float | None = None,
        second_score: float | None = None,
    ) -> None:
        super().__init__(message)
        self.score = score
        self.second_score = second_score


@dataclass(frozen=True, slots=True)
class _Candidate:
    score: float
    x: int
    y: int
    width: int
    height: int
    scale: float

    @property
    def bounds(self) -> PixelBounds:
        return PixelBounds(self.x, self.y, self.width, self.height)


def locate_dynamic_template(
    reference_rgb: RGBImage,
    target_bounds: BoundsLike,
    current_rgb: RGBImage,
    *,
    search_region: BoundsLike | None = None,
    score_threshold: float = 0.82,
    ambiguity_margin: float = 0.04,
    scale_range: tuple[float, float] = (0.9, 1.1),
    scale_step: float = 0.025,
) -> TemplateLocation:
    """Locate one reference control in a current screenshot.

    ``target_bounds`` addresses the control in ``reference_rgb``.
    ``search_region`` addresses the only allowed search area in ``current_rgb``;
    it defaults to the complete current image.  The returned bounds are always
    global current-image coordinates.

    The best spatially distinct candidate must reach ``score_threshold`` and
    exceed the second candidate by at least ``ambiguity_margin``.  Candidates
    representing the same physical location at nearby scales are deduplicated
    before that comparison.
    """

    reference = _require_rgb(reference_rgb, "reference_rgb")
    current = _require_rgb(current_rgb, "current_rgb")
    target = _coerce_bounds(target_bounds, "target_bounds")
    _require_inside(target, reference.shape[1], reference.shape[0], "target_bounds")

    if search_region is None:
        search = PixelBounds(0, 0, current.shape[1], current.shape[0])
    else:
        search = _coerce_bounds(search_region, "search_region")
    _require_inside(search, current.shape[1], current.shape[0], "search_region")
    _validate_policy(score_threshold, ambiguity_margin, scale_range, scale_step)

    template_rgb = reference[target.y : target.bottom, target.x : target.right]
    search_rgb = current[search.y : search.bottom, search.x : search.right]
    template_gray = cv2.cvtColor(template_rgb, cv2.COLOR_RGB2GRAY)
    search_gray = cv2.cvtColor(search_rgb, cv2.COLOR_RGB2GRAY)
    search_edges = cv2.Canny(search_gray, 60, 160)

    if float(template_gray.std()) < 1.0:
        raise TemplateLocationError("reference template has no discriminating visual detail")

    candidates: list[_Candidate] = []
    for scale in _scale_values(scale_range, scale_step):
        scaled_gray = _resize_template(template_gray, scale)
        scaled_edges = cv2.Canny(scaled_gray, 60, 160)
        height, width = scaled_gray.shape
        if height > search.height or width > search.width:
            continue

        gray_response = cv2.matchTemplate(search_gray, scaled_gray, cv2.TM_CCOEFF_NORMED)
        edge_response = cv2.matchTemplate(search_edges, scaled_edges, cv2.TM_CCOEFF_NORMED)
        gray_response = np.nan_to_num(gray_response, nan=-1.0, posinf=-1.0, neginf=-1.0)
        edge_response = np.nan_to_num(edge_response, nan=-1.0, posinf=-1.0, neginf=-1.0)
        edge_weight = 0.35 if float(scaled_edges.std()) >= 1.0 else 0.0
        response = (1.0 - edge_weight) * gray_response + edge_weight * edge_response
        candidates.extend(
            _response_candidates(
                response,
                width=width,
                height=height,
                scale=scale,
            )
        )

    if not candidates:
        raise TemplateLocationError("no requested template scale fits inside search_region")

    unique = _deduplicate_candidates(candidates)
    best = unique[0]
    second_score = unique[1].score if len(unique) > 1 else -1.0
    if best.score < score_threshold:
        raise TemplateLocationError(
            f"best template score {best.score:.4f} is below threshold {score_threshold:.4f}",
            score=best.score,
            second_score=second_score,
        )
    if best.score - second_score < ambiguity_margin:
        raise TemplateLocationError(
            "template match is ambiguous: "
            f"best={best.score:.4f}, second={second_score:.4f}, "
            f"required_margin={ambiguity_margin:.4f}",
            score=best.score,
            second_score=second_score,
        )

    resolved = PixelBounds(
        search.x + best.x,
        search.y + best.y,
        best.width,
        best.height,
    )
    _require_inside(resolved, current.shape[1], current.shape[0], "resolved_bounds")
    if not _contains(search, resolved):
        raise TemplateLocationError("resolved bounds escape search_region")
    return TemplateLocation(
        resolved_bounds=resolved,
        score=best.score,
        second_score=second_score,
        scale=best.scale,
    )


def _require_rgb(image: RGBImage, name: str) -> RGBImage:
    if not isinstance(image, np.ndarray):
        raise TemplateLocationError(f"{name} must be a numpy array")
    if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
        raise TemplateLocationError(f"{name} must have uint8 RGB shape (height, width, 3)")
    if image.shape[0] == 0 or image.shape[1] == 0:
        raise TemplateLocationError(f"{name} cannot be empty")
    return np.ascontiguousarray(image)


def _coerce_bounds(bounds: BoundsLike, name: str) -> PixelBounds:
    if isinstance(bounds, PixelBounds):
        value = bounds
    elif isinstance(bounds, tuple) and len(bounds) == 4:
        if any(isinstance(item, bool) or not isinstance(item, int) for item in bounds):
            raise TemplateLocationError(f"{name} values must be integers")
        value = PixelBounds(*bounds)
    else:
        raise TemplateLocationError(f"{name} must be PixelBounds or a four-integer tuple")
    if value.width <= 0 or value.height <= 0:
        raise TemplateLocationError(f"{name} width and height must be positive")
    return value


def _require_inside(bounds: PixelBounds, width: int, height: int, name: str) -> None:
    if bounds.x < 0 or bounds.y < 0 or bounds.right > width or bounds.bottom > height:
        raise TemplateLocationError(
            f"{name}={bounds!r} is outside image bounds width={width}, height={height}"
        )


def _validate_policy(
    score_threshold: float,
    ambiguity_margin: float,
    scale_range: tuple[float, float],
    scale_step: float,
) -> None:
    if not isfinite(score_threshold) or not -1.0 <= score_threshold <= 1.0:
        raise TemplateLocationError("score_threshold must be finite and between -1 and 1")
    if not isfinite(ambiguity_margin) or not 0.0 <= ambiguity_margin <= 2.0:
        raise TemplateLocationError("ambiguity_margin must be finite and between 0 and 2")
    if len(scale_range) != 2:
        raise TemplateLocationError("scale_range must contain minimum and maximum")
    minimum, maximum = scale_range
    if (
        not isfinite(minimum)
        or not isfinite(maximum)
        or minimum <= 0
        or maximum < minimum
    ):
        raise TemplateLocationError("scale_range must be finite, positive, and ordered")
    if not isfinite(scale_step) or scale_step <= 0:
        raise TemplateLocationError("scale_step must be finite and positive")


def _scale_values(scale_range: tuple[float, float], scale_step: float) -> list[float]:
    minimum, maximum = scale_range
    count = int((maximum - minimum) / scale_step)
    values = [minimum + index * scale_step for index in range(count + 1)]
    values.append(maximum)
    if minimum <= 1.0 <= maximum:
        values.append(1.0)
    return sorted({round(value, 6) for value in values})


def _resize_template(
    gray: NDArray[np.uint8],
    scale: float,
) -> NDArray[np.uint8]:
    width = max(2, round(gray.shape[1] * scale))
    height = max(2, round(gray.shape[0] * scale))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    size = (width, height)
    return cv2.resize(gray, size, interpolation=interpolation)


def _response_candidates(
    response: NDArray[np.floating],
    *,
    width: int,
    height: int,
    scale: float,
    limit: int = 8,
) -> list[_Candidate]:
    working = np.asarray(response, dtype=np.float32).copy()
    candidates: list[_Candidate] = []
    suppress_x = max(2, width // 3)
    suppress_y = max(2, height // 3)
    for _ in range(limit):
        _, maximum, _, location = cv2.minMaxLoc(working)
        if not isfinite(maximum):
            break
        x, y = location
        candidates.append(_Candidate(float(maximum), x, y, width, height, scale))
        left = max(0, x - suppress_x)
        top = max(0, y - suppress_y)
        right = min(working.shape[1], x + suppress_x + 1)
        bottom = min(working.shape[0], y + suppress_y + 1)
        working[top:bottom, left:right] = -np.inf
    return candidates


def _deduplicate_candidates(candidates: list[_Candidate]) -> list[_Candidate]:
    accepted: list[_Candidate] = []
    for candidate in sorted(candidates, key=lambda item: item.score, reverse=True):
        if any(_same_physical_match(candidate, existing) for existing in accepted):
            continue
        accepted.append(candidate)
    return accepted


def _same_physical_match(left: _Candidate, right: _Candidate) -> bool:
    intersection_width = max(
        0,
        min(left.x + left.width, right.x + right.width) - max(left.x, right.x),
    )
    intersection_height = max(
        0,
        min(left.y + left.height, right.y + right.height) - max(left.y, right.y),
    )
    intersection = intersection_width * intersection_height
    smaller_area = min(left.width * left.height, right.width * right.height)
    if smaller_area > 0 and intersection / smaller_area >= 0.70:
        return True

    left_center = (left.x + left.width / 2.0, left.y + left.height / 2.0)
    right_center = (right.x + right.width / 2.0, right.y + right.height / 2.0)
    center_distance = hypot(
        left_center[0] - right_center[0],
        left_center[1] - right_center[1],
    )
    smaller_diagonal = hypot(min(left.width, right.width), min(left.height, right.height))
    return center_distance <= smaller_diagonal * 0.20


def _contains(outer: PixelBounds, inner: PixelBounds) -> bool:
    return (
        outer.x <= inner.x
        and outer.y <= inner.y
        and inner.right <= outer.right
        and inner.bottom <= outer.bottom
    )


__all__ = [
    "PixelBounds",
    "TemplateLocation",
    "TemplateLocationError",
    "locate_dynamic_template",
]
