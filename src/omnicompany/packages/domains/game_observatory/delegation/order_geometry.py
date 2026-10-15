# [OMNI] origin=codex created_by=codex intent="calibrate Romantic Restaurant order-card delivery points"
"""Versioned order-card geometry in 1080 x 1920 source pixels."""

from __future__ import annotations

import hashlib
import io
import math
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Literal

import numpy as np
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, model_validator


EXPECTED_SCREEN = (1080, 1920)
ORDER_SLOTS = 3
# F4-07 open item 4.  There are exactly three tray positions on screen and the
# save can hold more orders than that, so a slot is addressed by where it is
# *displayed*, never by its own number.  Two live frames show it directly:
#
#   F4-06 segment H (frames/f.2612193312000000.347354.webp): save slots 1-4,
#   trays read slot 4 (1 item, +24), slot 3 (3 items, +36), slot 2 (2 items,
#   +26); slot 1 is not on screen at all.
#   2026-09-06 13:47Z live frame: save slots 1-4, trays read slot 4 (1 item,
#   +14), slot 2 (2 items, +26), slot 1 (1 item); slot 3 is not on screen.
#
# So slot 4 has no coordinate of its own to calibrate: it borrows tray 1, 2 or
# 3 depending on where the game puts it, and any order past the third display
# position has no delivery point until the game moves it up.
DISPLAY_POSITIONS = 3
DELIVERY_Y = 355
CALIBRATION_METHOD = "neutral_tray_projection_v1"
DeliveryInteraction = Literal["unverified", "single_tap", "double_tap"]


class OrderGeometryCalibrationError(ValueError):
    """Raised when the three visible order cards cannot be calibrated."""


class OrderGeometryEvidenceV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    screenshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confidence: float = Field(ge=0, le=1)
    residual_px: float = Field(ge=0)
    detected_tray_bounds: list[tuple[int, int]] = Field(
        min_length=ORDER_SLOTS,
        max_length=ORDER_SLOTS,
    )
    detected_display_centers: list[tuple[int, int]] = Field(
        min_length=ORDER_SLOTS,
        max_length=ORDER_SLOTS,
    )
    source_ref: str


class DisplayOrderCandidateV1(BaseModel):
    """A display-sequence rule that survived the offline corpus, not yet adopted.

    F4-09 section 1.2.  A candidate is recorded, never used: the planner still
    addresses a tray by display rank and takes its delivery point from the green
    Complete button it can see.  Adopting one of these needs a live frame that
    separates it from the others, and ``discriminating_check`` says what that
    frame has to show.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule: str = Field(min_length=1)
    status: Literal["candidate_pending_live_check"] = "candidate_pending_live_check"
    button_pattern_hits: int = Field(ge=0)
    button_pattern_samples: int = Field(ge=0)
    confirmed_pairing_hits: int = Field(ge=0)
    confirmed_pairing_samples: int = Field(ge=0)
    hand_read_hits: int = Field(ge=0)
    hand_read_samples: int = Field(ge=0)
    discriminating_check: str = Field(min_length=1)
    evidence_ref: str = Field(min_length=1)


class RowShiftCandidateV1(BaseModel):
    """A whole-row horizontal offset seen on real frames, not yet addressed by.

    F6-05 section 2.  ``buttons_off_calibrated_tray`` in the F4-09 forensics
    report counted 42 green Complete buttons that landed on no calibrated tray.
    Measuring the offset per frame explains all 42 and leaves none over.  F4-10
    registered the effect as "four orders or more shifts the row"; the corpus
    refutes that reading, so ``triggered_by`` records what was actually found
    instead.  Nothing in the addressing path reads this list: the planner still
    takes its delivery point from the button it can see.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    shift_px: int
    status: Literal["observed_pending_cause"] = "observed_pending_cause"
    frames_with_this_shift: int = Field(ge=0)
    frames_scored: int = Field(ge=0)
    order_counts_seen: list[int] = Field(default_factory=list)
    triggered_by: str = Field(min_length=1)
    evidence_ref: str = Field(min_length=1)


class OrderGeometryV1(BaseModel):
    """Map save slots to delivery controls, with optional live display sorting."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    slot_centers: dict[int, tuple[int, int]]
    screen_w: int = Field(default=EXPECTED_SCREEN[0], ge=1)
    screen_h: int = Field(default=EXPECTED_SCREEN[1], ge=1)
    calibrated_at: str = Field(min_length=1)
    method: Literal["neutral_tray_projection_v1"] = CALIBRATION_METHOD
    delivery_interaction: DeliveryInteraction = "unverified"
    display_positions: int = Field(default=DISPLAY_POSITIONS, ge=1)
    slot_addressing: Literal["display_rank"] = "display_rank"
    # Recorded by F4-09 1.2 and read by nobody in the hot path yet.
    display_order_candidates: list[DisplayOrderCandidateV1] = Field(
        default_factory=list
    )
    # Recorded by F6-05 section 2 and, like the candidates above, read by nobody
    # in the hot path.
    row_shift_candidates: list[RowShiftCandidateV1] = Field(default_factory=list)
    evidence: OrderGeometryEvidenceV1

    @model_validator(mode="after")
    def validate_slots_and_bounds(self) -> OrderGeometryV1:
        if set(self.slot_centers) != set(range(1, ORDER_SLOTS + 1)):
            raise ValueError("slot_centers must contain exactly slots 1, 2, and 3")
        ordered = [self.slot_centers[slot] for slot in range(1, ORDER_SLOTS + 1)]
        if any(not (0 <= x < self.screen_w and 0 <= y < self.screen_h) for x, y in ordered):
            raise ValueError("order-card center is outside the source screen")
        if any(ordered[index][0] >= ordered[index + 1][0] for index in range(2)):
            raise ValueError("default slot centers must increase from left to right")
        return self

    def order_card_center(
        self,
        slot: int,
        *,
        slot_order: Sequence[int] | None = None,
    ) -> tuple[int, int]:
        """Return a delivery point for the tray this slot is displayed on.

        ``slot_order`` is the live display sequence, left to right.  Without it
        the only safe reading of a slot number is the tray of the same index,
        which is why a save slot past the visible positions has no point.
        """

        if isinstance(slot, bool) or not isinstance(slot, int):
            raise TypeError("slot must be an integer")
        if slot_order is None:
            if slot not in self.slot_centers:
                raise IndexError(f"order slot {slot} is not on screen")
            return self.slot_centers[slot]
        order = list(slot_order)
        if not order or len(order) != len(set(order)) or any(
            item < 1 for item in order
        ):
            raise ValueError("slot_order must be unique positive slot numbers")
        if slot not in order:
            raise IndexError(f"order slot {slot} is not in the display order")
        display_index = order.index(slot) + 1
        if display_index > self.display_positions:
            raise IndexError(
                f"order slot {slot} is displayed at position {display_index}, "
                f"past the {self.display_positions} visible trays"
            )
        return self.slot_centers[display_index]


def _active_bounds(mask: np.ndarray, start: int, stop: int) -> tuple[int, int]:
    scores = mask[:, start:stop].mean(axis=0)
    active = np.flatnonzero(scores >= 0.2)
    if active.size < 40:
        raise OrderGeometryCalibrationError("order tray did not have enough neutral pixels")
    return start + int(active[0]), start + int(active[-1]) + 1


def calibrate_from_screenshot(png_bytes: bytes) -> OrderGeometryV1:
    """Measure the three white order trays and derive their delivery x centers."""

    try:
        with Image.open(io.BytesIO(png_bytes)) as source:
            source.load()
            if source.size != EXPECTED_SCREEN:
                raise OrderGeometryCalibrationError(
                    "order calibration requires a 1080x1920 screenshot; "
                    f"got {source.width}x{source.height}"
                )
            rgb = np.asarray(source.convert("RGB"), dtype=np.int16)
    except OrderGeometryCalibrationError:
        raise
    except (OSError, UnidentifiedImageError) as exc:
        raise OrderGeometryCalibrationError("invalid screenshot image") from exc

    tray = rgb[392:448]
    neutral = (tray.min(axis=2) >= 190) & (np.ptp(tray, axis=2) <= 45)
    lanes = ((170, 490), (490, 820), (820, 1080))
    bounds = [_active_bounds(neutral, start, stop) for start, stop in lanes]
    full_width = float(np.median([right - left for left, right in bounds[:2]]))
    if not 240 <= full_width <= 330:
        raise OrderGeometryCalibrationError("detected order tray width is implausible")
    observed_x = [
        (left + right) / 2 if right < EXPECTED_SCREEN[0] else left + full_width / 2
        for left, right in bounds
    ]
    spacing = float(np.median(np.diff(observed_x)))
    if not 270 <= spacing <= 360:
        raise OrderGeometryCalibrationError("detected order-card spacing is implausible")
    fitted_x = [observed_x[0] + index * spacing for index in range(ORDER_SLOTS)]
    residual = math.sqrt(
        sum((observed - fitted) ** 2 for observed, fitted in zip(observed_x, fitted_x))
        / ORDER_SLOTS
    )
    confidence = max(0.0, min(1.0, 1.0 - residual / 24.0))
    centers = [(round(value), DELIVERY_Y) for value in fitted_x]
    evidence = OrderGeometryEvidenceV1(
        screenshot_sha256=hashlib.sha256(png_bytes).hexdigest(),
        confidence=round(confidence, 6),
        residual_px=round(residual, 6),
        detected_tray_bounds=bounds,
        detected_display_centers=centers,
        source_ref="calibration input screenshot identified by screenshot_sha256",
    )
    return OrderGeometryV1(
        slot_centers={index + 1: center for index, center in enumerate(centers)},
        calibrated_at=datetime.now(timezone.utc).isoformat(),
        evidence=evidence,
    )


__all__ = [
    "CALIBRATION_METHOD",
    "DisplayOrderCandidateV1",
    "DISPLAY_POSITIONS",
    "DELIVERY_Y",
    "EXPECTED_SCREEN",
    "ORDER_SLOTS",
    "OrderGeometryCalibrationError",
    "OrderGeometryEvidenceV1",
    "OrderGeometryV1",
    "calibrate_from_screenshot",
]
