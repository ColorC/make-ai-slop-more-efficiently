# [OMNI] origin=codex created_by=codex intent="identify Romantic Restaurant navigation surfaces from registered RGB anchors"
"""Registered, deterministic screen signatures for Romantic Restaurant."""

from __future__ import annotations

import io
import json
import math
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field


DEFAULT_NAVIGATION_CONFIG = Path(
    "config/game_observatory/romantic_restaurant/navigation.v1.json"
)
DEFAULT_RGB_DISTANCE_THRESHOLD = 18.0
DEFAULT_AMBIGUITY_MARGIN = 1.0
ANCHOR_SIZE = 40
TRANSITION_DARK_PIXEL_FRACTION = 0.95
TRANSITION_DARK_LUMINANCE = 20.0
TRANSITION_MAX_VARIANCE = 2.0
TRANSITION_CURTAIN_BLUE_PIXEL_FRACTION = 0.95
TRANSITION_CURTAIN_MAX_EDGE_DELTA = 1.0
TRANSITION_CURTAIN_MIN_PERIODIC_ENERGY = 0.4
TUTORIAL_OVERLAY_NAME = "tutorial-overlay"
STORY_CUTSCENE_NAME = "story-cutscene"
STORY_DIALOGUE_NAME = "story-dialogue"
LOADING_SPLASH_NAME = "loading-splash"
FORCED_SHOP_TUTORIAL_NAME = "forced-shop-tutorial"
INTRO_CHEST_REAL_MONEY_NAME = "intro-chest-real-money-offer"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ScreenIdV1(_FrozenModel):
    name: str
    confidence: float = Field(ge=0, le=1)
    matched_anchors: list[str] = Field(default_factory=list)


class ForbiddenSurfaceV1(_FrozenModel):
    """A registered surface that carries a red-line control."""

    name: str
    confidence: float = Field(ge=0, le=1)
    matched_anchors: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    close_element: str | None = None
    basis: str


class VerifiedTapTransitionV1(_FrozenModel):
    source: str
    target: str
    x: int
    y: int
    status: str


@dataclass(frozen=True)
class _ShapeAnchor:
    """A registered template, the window it may appear in, and its cutoff.

    F4-11, planner ruling of 2026-09-07.  A colour anchor measures a 40x40 mean
    and cannot tell the tutorial glove from the white order tray beneath it; a
    template match on the same window can.  The window is measured, not
    assumed: every verified hit in a 3146-frame sample sat within two pixels of
    one point, so a full-screen search would cost twenty times as much and find
    nothing more.
    """

    name: str
    templates: tuple[np.ndarray, ...]
    window: tuple[int, int, int, int]
    minimum_score: float


def _template_path(raw: str, config_path: Path) -> Path | None:
    """Resolve a registered template against the places it can sensibly live.

    The registry writes a repository-relative path, which works from the
    repository root; a test or a tool that loads a registry copy from
    somewhere else gets the same file resolved beside its own config.
    """

    candidate = Path(raw)
    options = [candidate, config_path.parent / candidate.name]
    for parent in config_path.resolve().parents:
        options.append(parent / candidate)
    for option in options:
        if option.is_file():
            return option
    return None


def _shape_anchors(
    value: Any,
    *,
    prefix: str,
    config_path: Path,
) -> tuple[_ShapeAnchor, ...]:
    if not isinstance(value, dict):
        return ()
    found: list[_ShapeAnchor] = []
    for name, entry in value.items():
        if not isinstance(entry, dict):
            continue
        template_path = entry.get("template")
        window = entry.get("window")
        if not template_path or not isinstance(window, list) or len(window) != 4:
            continue
        path = _template_path(str(template_path), config_path)
        if path is None:
            continue
        with Image.open(path) as handle:
            grey = np.asarray(handle.convert("L"))
        scales = entry.get("scales") or [1.0]
        pyramid = []
        for scale in scales:
            width = max(int(grey.shape[1] * float(scale)), 8)
            height = max(int(grey.shape[0] * float(scale)), 8)
            with Image.fromarray(grey).resize((width, height)) as resized:
                pyramid.append(np.asarray(resized))
        found.append(
            _ShapeAnchor(
                name=f"{prefix}/{name}",
                templates=tuple(pyramid),
                window=(int(window[0]), int(window[1]), int(window[2]), int(window[3])),
                minimum_score=float(entry.get("minimum_score", 0.65)),
            )
        )
    return tuple(found)


@dataclass(frozen=True)
class _Anchor:
    name: str
    x: int
    y: int
    rgb: tuple[float, float, float]


def frame_to_rgb(frame: Any) -> np.ndarray:
    """Normalize encoded bytes, raw arrays, or PIL images to uint8 RGB."""

    if isinstance(frame, np.ndarray):
        array = frame
    elif isinstance(frame, Image.Image):
        array = np.asarray(frame.convert("RGB"))
    elif isinstance(frame, (bytes, bytearray, memoryview)):
        with Image.open(io.BytesIO(bytes(frame))) as image:
            array = np.asarray(image.convert("RGB"))
    else:
        raise TypeError("frame must be encoded image bytes, a PIL image, or an array")
    if array.ndim != 3 or array.shape[2] not in {3, 4}:
        raise ValueError("frame must have RGB or RGBA channels")
    if array.shape[2] == 4:
        array = array[:, :, :3]
    if array.dtype != np.uint8:
        array = np.clip(array, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(array)


def _is_identification_evidence(value: dict[str, Any]) -> bool:
    """Red-line controls never serve as identification evidence.

    ``bottom-right-warehouse`` is a dark brown control flagged
    ``forbidden_for_navigation``.  Its registered colour is close enough to the
    dim layer that monetization modals paint over the board that it matched the
    2026-09-06 real-money offer frame at distance 11 and made that popup look
    like ``main-board``.  Controls we are forbidden to tap must not be able to
    prove a surface either.
    """

    return not (
        value.get("forbidden") is True
        or value.get("forbidden_for_navigation") is True
    )


# The surveyed mean colour of an anchor patch.  Two spellings are in the
# registry: element anchors were surveyed at 40x40 and say so in the key, while
# signature anchors carry their patch size on the signature itself and use the
# plain name.  Reading only the first spelling silently dropped two registered
# red-line signatures (F4-07 item 1.6).
_ANCHOR_RGB_KEYS = ("average_rgb_40x40", "average_rgb")

# How a forbidden surface says, in its `detection` field, that it really does
# carry a pixel signature.  Matched as prefixes rather than by searching for the
# word, because the opposite claim ("surface name only; no multi-anchor
# signature surveyed yet") contains that word too.
_SIGNATURE_CLAIM_PREFIXES = (
    "registered multi-anchor signature",
    "price-tier-independent signature",
    # F4-09 1.3.  A new wording of the same claim has to be caught by the same
    # guard, or a surface can advertise pixel detection and register nothing
    # simply by phrasing it differently.
    "price-independent multi-anchor signature",
)


def _anchor_rgb(value: dict[str, Any]) -> list[Any] | None:
    for key in _ANCHOR_RGB_KEYS:
        candidate = value.get(key)
        if isinstance(candidate, list) and len(candidate) == 3:
            return candidate
    return None


def _anchors(value: Any, *, prefix: str = "") -> list[_Anchor]:
    found: list[_Anchor] = []
    if isinstance(value, dict):
        center = value.get("center")
        average = _anchor_rgb(value)
        if (
            isinstance(center, list)
            and len(center) == 2
            and isinstance(average, list)
            and len(average) == 3
            and _is_identification_evidence(value)
        ):
            found.append(
                _Anchor(
                    name=prefix.strip("/"),
                    x=int(center[0]),
                    y=int(center[1]),
                    rgb=tuple(float(item) for item in average),
                )
            )
        for key, nested in value.items():
            found.extend(_anchors(nested, prefix=f"{prefix}/{key}"))
    return found


class SurfaceRegistry:
    """Read-only registry backed by ``navigation.v1.json``."""

    def __init__(
        self,
        config_path: Path = DEFAULT_NAVIGATION_CONFIG,
        *,
        rgb_distance_threshold: float = DEFAULT_RGB_DISTANCE_THRESHOLD,
        ambiguity_margin: float = DEFAULT_AMBIGUITY_MARGIN,
    ) -> None:
        if rgb_distance_threshold <= 0:
            raise ValueError("rgb_distance_threshold must be positive")
        if ambiguity_margin < 0:
            raise ValueError("ambiguity_margin must be non-negative")
        self.config_path = Path(config_path)
        payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        surfaces = payload.get("surfaces") if isinstance(payload, dict) else None
        if not isinstance(surfaces, dict):
            raise ValueError("navigation config must contain a surfaces object")
        self.payload = payload
        self.surfaces: dict[str, dict[str, Any]] = {
            str(name): value
            for name, value in surfaces.items()
            if isinstance(value, dict)
        }
        self.rgb_distance_threshold = float(rgb_distance_threshold)
        self.ambiguity_margin = float(ambiguity_margin)
        self._surface_anchors = {
            name: (
                []
                if name
                in {
                    TUTORIAL_OVERLAY_NAME,
                    STORY_CUTSCENE_NAME,
                    LOADING_SPLASH_NAME,
                }
                or surface.get("kind") in {"tutorial", "tutorial-overlay"}
                else _anchors(surface, prefix=name)
            )
            for name, surface in self.surfaces.items()
        }
        # 2026-09-17: a surface whose signature sets
        # ``required_for_identification`` is only named by the anchor scoring
        # below when at least ``minimum_matching_anchors`` of its signature
        # anchors match.  Without it one anchor decides, and the energy guide's
        # bubble-white and portrait-hair patches alone named a room cutscene
        # and the Gerald order tutorial as that guide, stopping two segments.
        #
        # 2026-09-21: a signature that asks for a quorum now gets one whether or
        # not it also spelled the flag.  Twenty-two surfaces wrote
        # ``minimum_matching_anchors`` and were still decided by a single
        # anchor, because only the flag was read -- which is how one cream
        # panel-body pixel let order-piece-chest-choice claim the ad-failure
        # compensation dialog and cost 83 minutes of play.  A number nobody
        # honours is worse than no number: it reads like a guarantee.
        self._signature_quorum: dict[str, tuple[frozenset[str], int]] = {}
        for name, surface in self.surfaces.items():
            quorum_signature = surface.get("signature")
            if not (
                isinstance(quorum_signature, dict)
                and isinstance(quorum_signature.get("anchors"), dict)
            ):
                continue
            declared = quorum_signature.get("minimum_matching_anchors")
            if quorum_signature.get("required_for_identification") is not True and not (
                isinstance(declared, int)
                and not isinstance(declared, bool)
                and declared >= 2
            ):
                continue
            self._signature_quorum[name] = (
                frozenset(
                    f"{name}/signature/anchors/{key}"
                    for key in quorum_signature["anchors"]
                ),
                int(quorum_signature.get("minimum_matching_anchors", 2)),
            )
        self._reward_continuation = self.surfaces.get("reward-panel", {}).get("continuation_signature", {})
        self._reward_continuation_shapes = _shape_anchors(
            self._reward_continuation.get("shape_anchors", {}),
            prefix="reward-panel/continuation_signature/shape_anchors",
            config_path=self.config_path,
        )
        self._recognition_variants = [
            (surface, variant, _shape_anchors(
                specification.get("shape_anchors", {}),
                prefix=f"{surface}/recognition_variants/{variant}",
                config_path=self.config_path,
            ))
            for surface, value in self.surfaces.items()
            for variant, specification in value.get("recognition_variants", {}).items()
        ]
        tutorial = self.surfaces.get(TUTORIAL_OVERLAY_NAME, {})
        signature = tutorial.get("signature", {})
        if not isinstance(signature, dict):
            signature = {}
        self._tutorial_overlay_anchors = _anchors(
            signature.get("anchors", {}),
            prefix=f"{TUTORIAL_OVERLAY_NAME}/signature/anchors",
        )
        self._tutorial_overlay_shape_anchors = _shape_anchors(
            signature.get("shape_anchors", {}),
            prefix=f"{TUTORIAL_OVERLAY_NAME}/signature/shape_anchors",
            config_path=self.config_path,
        )
        self._tutorial_overlay_threshold = float(
            signature.get("rgb_distance_threshold", 45.0)
        )
        required = signature.get("required_anchors", [])
        self._tutorial_overlay_required_anchors = (
            [str(name) for name in required] if isinstance(required, list) else []
        )
        self._tutorial_overlay_minimum_matches = int(
            signature.get("minimum_matching_anchors", 2)
        )
        self._tutorial_overlay_minimum_confidence = float(
            signature.get("minimum_confidence", 0.85)
        )
        forced_shop = self.surfaces.get(FORCED_SHOP_TUTORIAL_NAME, {})
        forced_shop_signature = forced_shop.get("signature", {})
        if not isinstance(forced_shop_signature, dict):
            forced_shop_signature = {}
        self._forced_shop_tutorial_anchors = _anchors(
            forced_shop_signature.get("anchors", {}),
            prefix=f"{FORCED_SHOP_TUTORIAL_NAME}/signature/anchors",
        )
        self._forced_shop_tutorial_anchor_size = int(
            forced_shop_signature.get("anchor_size", ANCHOR_SIZE)
        )
        self._forced_shop_tutorial_threshold = float(
            forced_shop_signature.get("rgb_distance_threshold", 25.0)
        )
        self._forced_shop_tutorial_minimum_matches = int(
            forced_shop_signature.get("minimum_matching_anchors", 2)
        )
        self._forced_shop_tutorial_minimum_confidence = float(
            forced_shop_signature.get("minimum_confidence", 0.85)
        )
        self._forbidden_surfaces = self._load_forbidden_surfaces()
        self._forbidden_signatures = self._load_forbidden_signatures()
        # A red-line surface that owns a registered signature is proved by that
        # signature alone.  Leaving its element anchors in the generic score
        # lets one weak anchor act on a monetization surface, which is how a
        # rave-review reward animation once bought a close tap meant for the
        # app-store update modal.
        for name, _anchor_list, _size, _threshold, _minimum in (
            self._forbidden_signatures
        ):
            self._surface_anchors[name] = []
        loading = self.surfaces.get(LOADING_SPLASH_NAME, {})
        loading_signature = loading.get("signature", {})
        if not isinstance(loading_signature, dict):
            loading_signature = {}
        self._loading_splash_anchors = _anchors(
            loading_signature.get("anchors", {}),
            prefix=f"{LOADING_SPLASH_NAME}/signature/anchors",
        )
        self._loading_splash_threshold = float(
            loading_signature.get("rgb_distance_threshold", 24.0)
        )
        self._loading_splash_minimum_matches = int(
            loading_signature.get("minimum_matching_anchors", 2)
        )
        loading_no_hud_region = loading_signature.get("no_hud_region", [])
        self._loading_splash_no_hud_region = (
            tuple(int(value) for value in loading_no_hud_region)
            if isinstance(loading_no_hud_region, list)
            and len(loading_no_hud_region) == 4
            else None
        )
        self._loading_splash_bright_luminance = float(
            loading_signature.get("bright_luminance_threshold", 200.0)
        )
        self._loading_splash_max_bright_fraction = float(
            loading_signature.get("maximum_bright_pixel_fraction", 0.05)
        )
        cutscene = self.surfaces.get(STORY_CUTSCENE_NAME, {})
        cutscene_signature = cutscene.get("signature", {})
        if not isinstance(cutscene_signature, dict):
            cutscene_signature = {}
        self._story_cutscene_anchors: list[tuple[_Anchor, int]] = []
        self._story_cutscene_variant_recognition: dict[str, dict[str, Any]] = {}
        cutscene_anchors: list[tuple[str, Any]] = [
            ("default", cutscene_signature.get("skip_control_anchor", {}))
        ]
        configured_variants = cutscene_signature.get("skip_control_variants", {})
        if isinstance(configured_variants, dict):
            cutscene_anchors.extend(configured_variants.items())
        for variant, cutscene_anchor in cutscene_anchors:
            if not isinstance(cutscene_anchor, dict):
                continue
            center = cutscene_anchor.get("center")
            average = cutscene_anchor.get("average_rgb")
            if not (
                isinstance(center, list)
                and len(center) == 2
                and isinstance(average, list)
                and len(average) == 3
            ):
                continue
            anchor = _Anchor(
                name=(
                    f"{STORY_CUTSCENE_NAME}/signature/"
                    f"skip-control/{variant}"
                ),
                x=int(center[0]),
                y=int(center[1]),
                rgb=tuple(float(value) for value in average),
            )
            self._story_cutscene_anchors.append(
                (
                    anchor,
                    int(cutscene_anchor.get("anchor_size", 8)),
                )
            )
            recognition = cutscene_anchor.get("recognition")
            if isinstance(recognition, dict):
                self._story_cutscene_variant_recognition[anchor.name] = recognition
        self._story_cutscene_threshold = float(
            cutscene_signature.get("rgb_distance_threshold", 45.0)
        )
        # F4-08.  The cutscene test is an 8x8 skip-control patch plus a dark
        # top strip, and at the far edge of its distance threshold that pair
        # also describes a decoration panel drawn over a dim restaurant.  It
        # ran before the registered-surface scoring, so a marginal cutscene
        # match beat an exact decoration-choice match and the main-task flow
        # spent forty polls trying to skip a colour picker.  Across 900 random
        # replay frames the signature fired 52 times: the four hits below 0.023
        # were all decoration panels, and the weakest genuine cutscene scored
        # 0.267.  A floor of 0.1 sits between them with room on both sides, and
        # a frame below it falls through to the ordinary anchor scoring.
        # 2026-09-08: the floor moved to 0.2 (set in navigation.v1.json).  The
        # 21:43Z segment opened on the lit restaurant's decoration-choice
        # panel, whose sun-glare top-right patch sits at distance 24.8-25.1 of
        # 30 from the delivery-photo skip anchor -- confidence 0.164-0.173,
        # above the old floor.  The guard spent the cutscene ladder on the
        # panel (observe, then two skip taps at (990, 75)) and stopped
        # not_on_board at zero rounds.  Every one of the segment's 22 frames
        # scores decoration-choice at 1.000 under the ordinary anchor scoring,
        # the new false positives top out at 0.1733, and the weakest pinned
        # genuine cutscene is 0.2671, so 0.2 separates the populations.
        self._story_cutscene_min_confidence = float(
            cutscene_signature.get("minimum_confidence", 0.1)
        )
        region = cutscene_signature.get("no_hud_region", [])
        self._story_cutscene_no_hud_region = (
            tuple(int(value) for value in region)
            if isinstance(region, list) and len(region) == 4
            else None
        )
        self._story_cutscene_bright_luminance = float(
            cutscene_signature.get("bright_luminance_threshold", 200.0)
        )
        self._story_cutscene_max_bright_fraction = float(
            cutscene_signature.get("maximum_bright_pixel_fraction", 0.05)
        )
        dialogue = self.surfaces.get(STORY_DIALOGUE_NAME, {})
        dialogue_signature = dialogue.get("signature", {})
        if not isinstance(dialogue_signature, dict):
            dialogue_signature = {}
        dialogue_variants = dialogue_signature.get("variants", {})
        cream_rgb = dialogue_signature.get("cream_rgb")
        self._story_dialogue_cream_rgb = (
            np.asarray(cream_rgb, dtype=np.float32)
            if isinstance(cream_rgb, list) and len(cream_rgb) == 3
            else None
        )
        self._story_dialogue_cream_sq_lut = (
            np.square(
                np.arange(256, dtype=np.float32)[:, None]
                - self._story_dialogue_cream_rgb[None, :]
            )
            if self._story_dialogue_cream_rgb is not None
            else None
        )
        self._story_dialogue_cream_threshold = float(
            dialogue_signature.get("cream_distance_threshold", 12.0)
        )
        self._story_dialogue_min_cream_fraction = float(
            dialogue_signature.get("minimum_cream_pixel_fraction", 0.05)
        )
        skip_region = dialogue_signature.get("skip_control_region")
        self._story_dialogue_skip_region = (
            tuple(int(value) for value in skip_region)
            if isinstance(skip_region, list) and len(skip_region) == 4
            else None
        )
        self._story_dialogue_skip_bright_luminance = float(
            dialogue_signature.get("skip_bright_luminance_threshold", 180.0)
        )
        self._story_dialogue_min_skip_bright_fraction = float(
            dialogue_signature.get("minimum_skip_bright_pixel_fraction", 0.04)
        )
        self._story_dialogue_variants: list[
            tuple[str, list[_Anchor], int, float, int]
        ] = []
        if isinstance(dialogue_variants, dict):
            for variant_name, variant in dialogue_variants.items():
                if not isinstance(variant, dict):
                    continue
                variant_anchors: list[_Anchor] = []
                anchors = variant.get("anchors", {})
                if isinstance(anchors, dict):
                    for anchor_name, anchor in anchors.items():
                        if not isinstance(anchor, dict):
                            continue
                        center = anchor.get("center")
                        average = anchor.get("average_rgb")
                        if (
                            not isinstance(center, list)
                            or len(center) != 2
                            or not isinstance(average, list)
                            or len(average) != 3
                        ):
                            continue
                        variant_anchors.append(
                            _Anchor(
                                name=(
                                    f"{STORY_DIALOGUE_NAME}/signature/variants/"
                                    f"{variant_name}/anchors/{anchor_name}"
                                ),
                                x=int(center[0]),
                                y=int(center[1]),
                                rgb=tuple(float(value) for value in average),
                            )
                        )
                self._story_dialogue_variants.append(
                    (
                        str(variant_name),
                        variant_anchors,
                        int(variant.get("anchor_size", ANCHOR_SIZE)),
                        float(
                            variant.get(
                                "rgb_distance_threshold",
                                self.rgb_distance_threshold,
                            )
                        ),
                        int(variant.get("minimum_matching_anchors", 2)),
                    )
                )

    def element(self, surface: str, element: str) -> dict[str, Any]:
        value = self.surfaces[surface].get("elements", {}).get(element)
        if not isinstance(value, dict):
            raise KeyError(f"unregistered element: {surface}/{element}")
        return value

    def point(self, surface: str, element: str) -> tuple[int, int]:
        value = self.element(surface, element).get("center")
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError(f"registered element has no center: {surface}/{element}")
        return int(value[0]), int(value[1])

    def is_transition(self, screen_name: str) -> bool:
        """Return whether an identified screen is a waiting-only transition."""

        if screen_name == "transition":
            return True
        surface = self.surfaces.get(screen_name)
        return isinstance(surface, dict) and surface.get("kind") == "transition"

    def transition_timeout_s(
        self,
        screen_name: str,
        *,
        default: float,
    ) -> float:
        """Return the registered wait ceiling for a transition surface."""

        surface = self.surfaces.get(screen_name)
        if not isinstance(surface, dict) or surface.get("kind") != "transition":
            return default
        value = surface.get("wait_timeout_s", default)
        try:
            timeout = float(value)
        except (TypeError, ValueError):
            return default
        return timeout if timeout > 0 else default

    def verified_tap_path(
        self,
        source: str,
        target: str = "main-board",
        *,
        max_hops: int = 3,
    ) -> list[VerifiedTapTransitionV1]:
        """Return the shortest configured verified tap path, within ``max_hops``."""

        if max_hops < 0:
            raise ValueError("max_hops must be non-negative")
        transitions = self.payload.get("transitions")
        if not isinstance(transitions, list):
            return []
        adjacency: dict[str, list[VerifiedTapTransitionV1]] = {}
        for value in transitions:
            if not isinstance(value, dict):
                continue
            status = str(value.get("status", ""))
            action = str(value.get("action", ""))
            center = value.get("center")
            source_name = value.get("from")
            target_name = value.get("expected")
            if (
                not status.startswith("verified")
                or not action.startswith("tap")
                or not isinstance(source_name, str)
                or not isinstance(target_name, str)
                or not isinstance(center, list)
                or len(center) != 2
            ):
                continue
            hop = VerifiedTapTransitionV1(
                source=source_name,
                target=target_name,
                x=int(center[0]),
                y=int(center[1]),
                status=status,
            )
            adjacency.setdefault(source_name, []).append(hop)
        queue = deque([(source, [])])
        visited = {source}
        while queue:
            screen, path = queue.popleft()
            if screen == target:
                return path
            if len(path) >= max_hops:
                continue
            for hop in adjacency.get(screen, []):
                if hop.target in visited:
                    continue
                next_path = [*path, hop]
                if hop.target == target:
                    return next_path
                visited.add(hop.target)
                queue.append((hop.target, next_path))
        return []

    def identify_screen(self, frame_rgb: Any) -> ScreenIdV1:
        image = frame_to_rgb(frame_rgb)
        height, width = image.shape[:2]
        forbidden = self._identify_forbidden_signature(image)
        if forbidden is not None:
            return ScreenIdV1(
                name=forbidden.name,
                confidence=forbidden.confidence,
                matched_anchors=forbidden.matched_anchors,
            )
        reward = self._identify_reward_continuation(image)
        if reward is not None:
            return reward
        # Text/shape evidence distinguishes surveyed informational dialogs
        # from broad colour patches (e.g. cream panels mistaken for dialogue).
        variants = []
        for surface, variant, shapes in self._recognition_variants:
            scores = [self._shape_anchor_score(image, shape) for shape in shapes]
            if shapes and all(score >= shape.minimum_score for score, shape in zip(scores, shapes)):
                variants.append(ScreenIdV1(
                    name=surface, confidence=min(scores),
                    matched_anchors=[f"{surface}/recognition_variants/{variant}"] + [shape.name for shape in shapes],
                ))
        if variants:
            return max(variants, key=lambda match: (len(match.matched_anchors), match.confidence))
        loading_splash = self._identify_loading_splash(image)
        if loading_splash is not None:
            return loading_splash
        forced_shop_tutorial = self._identify_forced_shop_tutorial(image)
        if forced_shop_tutorial is not None:
            return forced_shop_tutorial
        story_dialogue = self._identify_story_dialogue(image)
        if story_dialogue is not None:
            return story_dialogue
        story_cutscene = self._identify_story_cutscene(image)
        if story_cutscene is not None:
            return story_cutscene
        scored: list[tuple[float, str, list[str]]] = []
        radius = ANCHOR_SIZE // 2
        for surface, anchors in self._surface_anchors.items():
            distances: list[tuple[float, str]] = []
            for anchor in anchors:
                if not (
                    radius <= anchor.x <= width - radius
                    and radius <= anchor.y <= height - radius
                ):
                    continue
                observed = image[
                    anchor.y - radius : anchor.y + radius,
                    anchor.x - radius : anchor.x + radius,
                ].mean(axis=(0, 1))
                distance = math.dist(observed, anchor.rgb)
                distances.append((distance, anchor.name))
            if distances:
                best = min(distance for distance, _name in distances)
                matched = sorted(
                    name
                    for distance, name in distances
                    if distance <= self.rgb_distance_threshold
                )
                quorum = self._signature_quorum.get(surface)
                if quorum is not None and len(quorum[0].intersection(matched)) < quorum[1]:
                    continue
                scored.append((best, surface, matched))
        if not scored:
            return _transition_or_unknown(image)
        ordered = sorted(scored, key=lambda item: (item[0], item[1]))
        distance, surface, matched = ordered[0]
        if distance > self.rgb_distance_threshold:
            return _transition_or_unknown(image)
        if len(ordered) > 1 and ordered[1][0] - distance < self.ambiguity_margin:
            return _transition_or_unknown(image)
        confidence = max(0.0, 1.0 - distance / self.rgb_distance_threshold)
        identified = ScreenIdV1(
            name=surface,
            confidence=confidence,
            matched_anchors=matched,
        )
        if surface == "main-board":
            tutorial_overlay = self._identify_tutorial_overlay(image)
            if tutorial_overlay is not None:
                return tutorial_overlay
        return identified

    def _load_forbidden_surfaces(self) -> dict[str, dict[str, Any]]:
        """Read the registered set of surfaces that carry red-line controls."""

        safety = self.payload.get("safety", {})
        declared = (
            safety.get("forbidden_surfaces", {}) if isinstance(safety, dict) else {}
        )
        forbidden: dict[str, dict[str, Any]] = {}
        if isinstance(declared, dict):
            for name, entry in declared.items():
                if not isinstance(entry, dict):
                    continue
                reasons = entry.get("reasons")
                forbidden[str(name)] = {
                    "reasons": (
                        [str(item) for item in reasons]
                        if isinstance(reasons, list)
                        else []
                    ),
                    "close_element": (
                        str(entry["close_element"])
                        if isinstance(entry.get("close_element"), str)
                        else None
                    ),
                    # Kept so the loader can refuse a surface that advertises a
                    # signature it does not actually register (F4-07 item 1.6).
                    "detection": (
                        str(entry["detection"])
                        if isinstance(entry.get("detection"), str)
                        else ""
                    ),
                    # 2026-09-17: the one owner-authorized purchase, if any.
                    "authorized_purchase": (
                        dict(entry["authorized_purchase"])
                        if isinstance(entry.get("authorized_purchase"), dict)
                        else None
                    ),
                }
        for name, surface in self.surfaces.items():
            if surface.get("commercial_touchpoint") is True and name not in forbidden:
                forbidden[name] = {
                    "reasons": ["commercial_touchpoint"],
                    "close_element": None,
                    "detection": "",
                }
        return forbidden

    def _load_forbidden_signatures(
        self,
    ) -> list[tuple[str, list[_Anchor], int, float, int]]:
        """Compile the positive signature of every registered forbidden surface."""

        compiled: list[tuple[str, list[_Anchor], int, float, int]] = []
        for name in sorted(self._forbidden_surfaces):
            surface = self.surfaces.get(name, {})
            declared_signature = surface.get("signature", {})
            if not isinstance(declared_signature, dict):
                continue
            signatures: list[tuple[str | None, dict[str, Any]]] = [(None, declared_signature)]
            variants = declared_signature.get("variants")
            if isinstance(variants, list):
                signatures.extend(
                    (str(variant.get("name", index)), variant)
                    for index, variant in enumerate(variants)
                    if isinstance(variant, dict)
                )
            for variant_name, signature in signatures:
                prefix = (
                    f"{name}/signature/anchors"
                    if variant_name is None
                    else f"{name}/signature/variants/{variant_name}/anchors"
                )
                anchors = _anchors(signature.get("anchors", {}), prefix=prefix)
                if not anchors:
                    if variant_name is not None:
                        continue
                # A red-line surface that advertises a signature and compiles
                # none is worse than one with no signature at all: the registry
                # looks complete while pixel detection never runs for it.  Two
                # surfaces were in exactly that state until F4-07 item 1.6, so
                # this refuses to load instead of failing open.
                    if signature.get("anchors"):
                        raise ValueError(
                            f"forbidden surface {name} declares signature anchors that "
                            "compiled to nothing; every anchor needs center and one of "
                            + " or ".join(_ANCHOR_RGB_KEYS)
                        )
                    declared = str(
                        self._forbidden_surfaces[name].get("detection", "")
                    )
                    if declared.startswith(_SIGNATURE_CLAIM_PREFIXES):
                        raise ValueError(
                            f"forbidden surface {name} declares detection {declared!r} "
                            "but registers no signature anchors"
                        )
                    continue
                compiled.append(
                    (
                        name,
                        anchors,
                        int(signature.get("anchor_size", ANCHOR_SIZE)),
                        float(
                            signature.get(
                                "rgb_distance_threshold",
                                self.rgb_distance_threshold,
                            )
                        ),
                        int(signature.get("minimum_matching_anchors", 3)),
                    )
                )
        return compiled

    @property
    def forbidden_surface_names(self) -> frozenset[str]:
        """Names of registered surfaces that may only ever be closed."""

        return frozenset(self._forbidden_surfaces)

    def is_forbidden_surface(self, name: str) -> bool:
        """Report whether a surface name is registered as a red-line surface."""

        return name in self._forbidden_surfaces

    def authorized_purchase(self, name: str) -> dict[str, Any] | None:
        """The one purchase a red-line surface has been explicitly authorized for.

        2026-09-17.  A red-line surface still takes only its close from every
        other path; this is the single, owner-authorized exception, and it
        carries its own daily limit for the caller to enforce.
        """

        entry = self._forbidden_surfaces.get(name, {})
        purchase = entry.get("authorized_purchase") if isinstance(entry, dict) else None
        if not isinstance(purchase, dict):
            return None
        if not isinstance(purchase.get("intent"), str) or not isinstance(
            purchase.get("element"), str
        ):
            return None
        return dict(purchase)

    @property
    def authorized_purchase_intents(self) -> dict[str, str]:
        """Surface name -> the purchase intent (or intent prefix) it lets through.

        A red-line surface registers one exact intent.  The shop, which is not a
        red-line surface, registers a prefix ending in ':' (one intent per item
        on its shelf).  Nothing else may send a ``purchase:`` intent anywhere.
        """

        intents: dict[str, str] = {}
        for name in self._forbidden_surfaces:
            purchase = self.authorized_purchase(name)
            if purchase is not None:
                intents[name] = purchase["intent"]
        for name, surface in self.surfaces.items():
            purchase = surface.get("authorized_purchase") if isinstance(surface, dict) else None
            prefix = purchase.get("intent_prefix") if isinstance(purchase, dict) else None
            if name not in intents and isinstance(prefix, str) and prefix.endswith(":"):
                intents[name] = prefix
        return intents

    def purchase_layout(self, name: str) -> dict[str, Any] | None:
        """The registered button layout a purchase flow taps on a surface."""

        surface = self.surfaces.get(name)
        layout = surface.get("purchase_layout") if isinstance(surface, dict) else None
        return dict(layout) if isinstance(layout, dict) else None

    def forbidden_close_element(self, name: str) -> str | None:
        """Return the only element a forbidden surface allows us to tap."""

        entry = self._forbidden_surfaces.get(name, {})
        return entry.get("close_element")

    def resolve_point(
        self,
        surface: str,
        element: str,
        frame_rgb: Any = None,
    ) -> tuple[int, int]:
        """Return the registered element point for the observed HUD layout.

        Some free navigation controls, notably the task notebook, sit at a
        different height once the order-card row is present.  Every accepted
        position is registered with its own surveyed colour, and the variant
        that matches the current frame wins; nothing outside the registry is
        ever tapped.
        """

        matched = self._matched_variant_point(surface, element, frame_rgb)
        return self.point(surface, element) if matched is None else matched

    def _matched_variant_point(
        self,
        surface: str,
        element: str,
        frame_rgb: Any,
    ) -> tuple[int, int] | None:
        """The registered variant this frame matches, or ``None`` for no match."""

        if frame_rgb is None:
            return None
        metadata = self.element(surface, element)
        variants = metadata.get("observed_variants")
        if not isinstance(variants, list):
            return None
        image = frame_to_rgb(frame_rgb)
        height, width = image.shape[:2]
        radius = ANCHOR_SIZE // 2
        best: tuple[float, tuple[int, int]] | None = None
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            signature_anchors = variant.get("anchors")
            if isinstance(signature_anchors, dict):
                try:
                    variant_size = int(variant.get("anchor_size", ANCHOR_SIZE))
                    variant_threshold = float(
                        variant.get(
                            "rgb_distance_threshold", self.rgb_distance_threshold
                        )
                    )
                    minimum_matches = int(
                        variant.get("minimum_matching_anchors", 2)
                    )
                except (TypeError, ValueError):
                    continue
                if variant_size <= 0 or variant_threshold <= 0 or minimum_matches < 1:
                    continue
                radius = variant_size // 2
                distances: list[float] = []
                matching = 0
                first_center: tuple[int, int] | None = None
                for anchor in signature_anchors.values():
                    if not isinstance(anchor, dict):
                        continue
                    center = anchor.get("center")
                    average = _anchor_rgb(anchor)
                    if not (
                        isinstance(center, list)
                        and len(center) == 2
                        and average is not None
                    ):
                        continue
                    x, y = int(center[0]), int(center[1])
                    if first_center is None:
                        first_center = (x, y)
                    if not (
                        radius <= x <= width - radius
                        and radius <= y <= height - radius
                    ):
                        continue
                    observed = image[
                        y - radius : y + radius,
                        x - radius : x + radius,
                    ].mean(axis=(0, 1))
                    distance = math.dist(
                        observed, [float(item) for item in average]
                    )
                    distances.append(distance)
                    if distance <= variant_threshold:
                        matching += 1
                if matching < minimum_matches or not distances:
                    continue
                center = variant.get("center")
                if not (
                    isinstance(center, list)
                    and len(center) == 2
                ):
                    center = first_center
                if center is None:
                    continue
                point = (int(center[0]), int(center[1]))
                score = sum(sorted(distances)[:minimum_matches]) / minimum_matches
                if best is None or score < best[0]:
                    best = (score, point)
                continue
            center = variant.get("center")
            average = variant.get("average_rgb_40x40")
            if not (
                isinstance(center, list)
                and len(center) == 2
                and isinstance(average, list)
                and len(average) == 3
            ):
                continue
            x, y = int(center[0]), int(center[1])
            if not (
                radius <= x <= width - radius and radius <= y <= height - radius
            ):
                continue
            observed = image[y - radius : y + radius, x - radius : x + radius].mean(
                axis=(0, 1)
            )
            distance = math.dist(observed, [float(item) for item in average])
            if distance > self.rgb_distance_threshold:
                continue
            if best is None or distance < best[0]:
                best = (distance, (x, y))
        return None if best is None else best[1]

    def visible_variant_point(
        self,
        surface: str,
        element: str,
        frame_rgb: Any,
    ) -> tuple[int, int] | None:
        """The variant point this frame actually shows, or ``None`` if none does.

        F4-09 section 1.4.  ``resolve_point`` falls back to the base centre when
        no registered variant matches, which is right for a control whose base
        centre is always correct and wrong for one that can be covered.  The
        task notebook is the second kind: with five orders in the save the
        leading customer portrait stands over it, no variant matches, and the
        fallback tap lands on the portrait and does nothing at all.  A caller
        that must not tap blind asks this and stops when it returns ``None``.
        An element with no registered variants has nothing to be uncertain
        about and returns its base centre.
        """

        metadata = self.element(surface, element)
        variants = metadata.get("observed_variants")
        if not isinstance(variants, list) or not variants:
            return self.point(surface, element)
        return self._matched_variant_point(surface, element, frame_rgb)

    def detect_forbidden_surface(
        self,
        frame_rgb: Any = None,
        *,
        screen: ScreenIdV1 | None = None,
    ) -> ForbiddenSurfaceV1 | None:
        """Positively detect a red-line surface from pixels, then from a name.

        Pixel detection is deliberately independent of screen identification so
        a monetization popup cannot be reclassified as a business surface by a
        stray element anchor that happens to match its dim layer.
        """

        if frame_rgb is not None:
            hit = self._identify_forbidden_signature(frame_to_rgb(frame_rgb))
            if hit is not None:
                return hit
        if screen is not None and self.is_forbidden_surface(screen.name):
            entry = self._forbidden_surfaces[screen.name]
            return ForbiddenSurfaceV1(
                name=screen.name,
                confidence=screen.confidence,
                matched_anchors=list(screen.matched_anchors),
                reasons=list(entry["reasons"]),
                close_element=entry.get("close_element"),
                basis="identified surface is registered as a forbidden surface",
            )
        return None

    def _identify_forbidden_signature(
        self,
        image: np.ndarray,
    ) -> ForbiddenSurfaceV1 | None:
        """Match every registered forbidden signature before any HUD anchor."""

        height, width = image.shape[:2]
        best: ForbiddenSurfaceV1 | None = None
        best_normalized = 1.0
        for (
            name,
            anchors,
            anchor_size,
            threshold,
            minimum_matches,
        ) in self._forbidden_signatures:
            radius = anchor_size // 2
            matches: list[tuple[float, str]] = []
            for anchor in anchors:
                if not (
                    radius <= anchor.x <= width - radius
                    and radius <= anchor.y <= height - radius
                ):
                    continue
                observed = image[
                    anchor.y - radius : anchor.y + radius,
                    anchor.x - radius : anchor.x + radius,
                ].mean(axis=(0, 1))
                distance = math.dist(observed, anchor.rgb)
                if distance <= threshold:
                    matches.append((distance, anchor.name))
            if len(matches) < minimum_matches:
                continue
            matches.sort()
            normalized = matches[minimum_matches - 1][0] / threshold
            if best is not None and normalized >= best_normalized:
                continue
            entry = self._forbidden_surfaces[name]
            best_normalized = normalized
            best = ForbiddenSurfaceV1(
                name=name,
                confidence=max(0.0, min(1.0, 1.0 - normalized)),
                matched_anchors=[anchor_name for _distance, anchor_name in matches],
                reasons=list(entry["reasons"]),
                close_element=entry.get("close_element"),
                basis="registered forbidden-surface signature matched the frame",
            )
        return best

    def _identify_loading_splash(self, image: np.ndarray) -> ScreenIdV1 | None:
        """Require the registered bottom loader structure and absence of HUD."""

        if not self._loading_splash_anchors or self._loading_splash_no_hud_region is None:
            return None
        height, width = image.shape[:2]
        radius = ANCHOR_SIZE // 2
        matches: list[tuple[float, str]] = []
        for anchor in self._loading_splash_anchors:
            if not (
                radius <= anchor.x <= width - radius
                and radius <= anchor.y <= height - radius
            ):
                continue
            observed = image[
                anchor.y - radius : anchor.y + radius,
                anchor.x - radius : anchor.x + radius,
            ].mean(axis=(0, 1))
            distance = math.dist(observed, anchor.rgb)
            if distance <= self._loading_splash_threshold:
                matches.append((distance, anchor.name))
        if len(matches) < self._loading_splash_minimum_matches:
            return None
        left, top, right, bottom = self._loading_splash_no_hud_region
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            return None
        no_hud = image[top:bottom, left:right].astype(np.float32).mean(axis=2)
        bright_fraction = float(
            np.mean(no_hud > self._loading_splash_bright_luminance)
        )
        if bright_fraction > self._loading_splash_max_bright_fraction:
            return None
        matches.sort()
        required_distance = matches[self._loading_splash_minimum_matches - 1][0]
        return ScreenIdV1(
            name=LOADING_SPLASH_NAME,
            confidence=max(
                0.0,
                min(
                    1.0 - required_distance / self._loading_splash_threshold,
                    1.0
                    - bright_fraction / self._loading_splash_max_bright_fraction,
                ),
            ),
            matched_anchors=[
                *(name for _distance, name in matches),
                f"{LOADING_SPLASH_NAME}/no-hud",
            ],
        )

    def _identify_story_cutscene(self, image: np.ndarray) -> ScreenIdV1 | None:
        """Require the surveyed skip control and absence of the normal HUD."""

        if not self._story_cutscene_anchors or self._story_cutscene_no_hud_region is None:
            return None
        height, width = image.shape[:2]
        candidates: list[tuple[float, _Anchor]] = []
        for anchor, anchor_size in self._story_cutscene_anchors:
            radius = anchor_size // 2
            if not (
                radius <= anchor.x <= width - radius
                and radius <= anchor.y <= height - radius
            ):
                continue
            observed = image[
                anchor.y - radius : anchor.y + radius,
                anchor.x - radius : anchor.x + radius,
            ].mean(axis=(0, 1))
            candidates.append((math.dist(observed, anchor.rgb), anchor))
        if not candidates:
            return None
        distance, anchor = min(candidates, key=lambda item: item[0])
        if distance > self._story_cutscene_threshold:
            return None
        variant_recognition = self._story_cutscene_variant_recognition.get(anchor.name)
        if variant_recognition is not None:
            region = variant_recognition.get("no_hud_region")
            if not isinstance(region, list) or len(region) != 4:
                return None
            left, top, right, bottom = (int(value) for value in region)
            if not (0 <= left < right <= width and 0 <= top < bottom <= height):
                return None
            luminance = image[top:bottom, left:right].astype(np.float32).mean(axis=2)
            dark_threshold = float(
                variant_recognition.get("dark_luminance_threshold", 80.0)
            )
            max_dark_fraction = float(
                variant_recognition.get("maximum_dark_pixel_fraction", 0.02)
            )
            if max_dark_fraction <= 0:
                return None
            dark_fraction = float(np.mean(luminance < dark_threshold))
            if dark_fraction > max_dark_fraction:
                return None
            confidence = max(
                0.0,
                min(
                    1.0 - distance / self._story_cutscene_threshold,
                    1.0 - dark_fraction / max_dark_fraction,
                ),
            )
            if confidence < float(variant_recognition.get("minimum_confidence", 0.2)):
                return None
            variant_name = anchor.name.rsplit("/", 1)[-1]
            return ScreenIdV1(
                name=STORY_CUTSCENE_NAME,
                confidence=confidence,
                matched_anchors=[
                    anchor.name,
                    f"{STORY_CUTSCENE_NAME}/{variant_name}/no-hud",
                ],
            )
        left, top, right, bottom = self._story_cutscene_no_hud_region
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            return None
        no_hud = image[top:bottom, left:right].astype(np.float32).mean(axis=2)
        bright_fraction = float(
            np.mean(no_hud > self._story_cutscene_bright_luminance)
        )
        if bright_fraction > self._story_cutscene_max_bright_fraction:
            return None
        confidence = max(
            0.0,
            min(
                1.0 - distance / self._story_cutscene_threshold,
                1.0 - bright_fraction / self._story_cutscene_max_bright_fraction,
            ),
        )
        if confidence < self._story_cutscene_min_confidence:
            return None
        return ScreenIdV1(
            name=STORY_CUTSCENE_NAME,
            confidence=confidence,
            matched_anchors=[anchor.name, f"{STORY_CUTSCENE_NAME}/no-hud"],
        )

    def _identify_story_dialogue(self, image: np.ndarray) -> ScreenIdV1 | None:
        """Recognize registered dialogue layout variants using multiple anchors."""

        height, width = image.shape[:2]
        if (
            self._story_dialogue_cream_rgb is not None
            and self._story_dialogue_skip_region is not None
        ):
            if (
                image.dtype == np.uint8
                and self._story_dialogue_cream_sq_lut is not None
            ):
                cream_distance = np.sqrt(
                    self._story_dialogue_cream_sq_lut[image[..., 0], 0]
                    + self._story_dialogue_cream_sq_lut[image[..., 1], 1]
                    + self._story_dialogue_cream_sq_lut[image[..., 2], 2]
                )
                cream_fraction = float(
                    np.mean(cream_distance < self._story_dialogue_cream_threshold)
                )
            else:
                cream_distance = np.linalg.norm(
                    image.astype(np.float32) - self._story_dialogue_cream_rgb,
                    axis=2,
                )
                cream_fraction = float(
                    np.mean(cream_distance < self._story_dialogue_cream_threshold)
                )
            left, top, right, bottom = self._story_dialogue_skip_region
            if 0 <= left < right <= width and 0 <= top < bottom <= height:
                skip_luminance = image[top:bottom, left:right].astype(
                    np.float32
                ).mean(axis=2)
                skip_bright_fraction = float(
                    np.mean(
                        skip_luminance
                        > self._story_dialogue_skip_bright_luminance
                    )
                )
                if (
                    cream_fraction >= self._story_dialogue_min_cream_fraction
                    and skip_bright_fraction
                    >= self._story_dialogue_min_skip_bright_fraction
                ):
                    matched_anchors = [
                        f"{STORY_DIALOGUE_NAME}/cream-speech-bubbles",
                        f"{STORY_DIALOGUE_NAME}/skip-control",
                        *self._matched_story_continue_anchors(image),
                    ]
                    return ScreenIdV1(
                        name=STORY_DIALOGUE_NAME,
                        confidence=min(
                            cream_fraction
                            / self._story_dialogue_min_cream_fraction,
                            skip_bright_fraction
                            / self._story_dialogue_min_skip_bright_fraction,
                            1.0,
                        ),
                        matched_anchors=matched_anchors,
                    )
        candidates: list[tuple[float, list[str]]] = []
        for _name, anchors, anchor_size, threshold, minimum_matches in (
            self._story_dialogue_variants
        ):
            radius = anchor_size // 2
            distances: list[tuple[float, str]] = []
            for anchor in anchors:
                if not (
                    radius <= anchor.x <= width - radius
                    and radius <= anchor.y <= height - radius
                ):
                    continue
                observed = image[
                    anchor.y - radius : anchor.y + radius,
                    anchor.x - radius : anchor.x + radius,
                ].mean(axis=(0, 1))
                distances.append((math.dist(observed, anchor.rgb), anchor.name))
            matched = sorted(
                (distance, name)
                for distance, name in distances
                if distance <= threshold
            )
            if len(matched) < minimum_matches:
                continue
            worst_required_distance = matched[minimum_matches - 1][0]
            candidates.append(
                (
                    worst_required_distance / threshold,
                    [name for _distance, name in matched],
                )
            )
        if not candidates:
            return None
        normalized_distance, matched_anchors = min(candidates, key=lambda item: item[0])
        return ScreenIdV1(
            name=STORY_DIALOGUE_NAME,
            confidence=max(0.0, min(1.0 - normalized_distance, 1.0)),
            matched_anchors=[
                *matched_anchors,
                *self._matched_story_continue_anchors(image),
            ],
        )

    def _matched_story_continue_anchors(self, image: np.ndarray) -> list[str]:
        """Name the dialogue's final Continue button when it is on screen.

        2026-09-21.  ``popup_handler.story_advance_action`` prefers
        ``final-continue-button`` whenever this anchor is among the matched
        ones and falls back to ``advance-open-area`` otherwise, but only the
        cream/skip branch above ever looked for it.  A dialogue recognized by
        its bubble variants therefore always advanced by tapping the open
        background -- which the final screen of a dialogue ignores, because
        there the button is the only control.  The 04:14Z segment spent its
        whole 20-tap budget on ``(540, 1650)`` against a Quinn dialogue whose
        Continue button sat right there at ``(540, 1780)``, stopped
        ``tutorial_follow_stalled`` at zero rounds, and the agent aborted with
        ``surface_already_registered``.  Both branches now run the same test.
        """

        if self._surface_anchors.get(STORY_DIALOGUE_NAME) is None:
            return []
        height, width = image.shape[:2]
        radius = ANCHOR_SIZE // 2
        matched: list[str] = []
        for anchor in self._surface_anchors.get(STORY_DIALOGUE_NAME, []):
            if not anchor.name.endswith("final-continue-button"):
                continue
            if not (
                radius <= anchor.x <= width - radius
                and radius <= anchor.y <= height - radius
            ):
                continue
            observed = image[
                anchor.y - radius : anchor.y + radius,
                anchor.x - radius : anchor.x + radius,
            ].mean(axis=(0, 1))
            if math.dist(observed, anchor.rgb) <= self.rgb_distance_threshold:
                matched.append(anchor.name)
        return matched

    def _identify_tutorial_overlay(self, image: np.ndarray) -> ScreenIdV1 | None:
        """Require a main-board match plus multiple surveyed tutorial anchors."""

        height, width = image.shape[:2]
        radius = ANCHOR_SIZE // 2
        matches: list[tuple[float, str]] = []
        for anchor in self._tutorial_overlay_anchors:
            if not (
                radius <= anchor.x <= width - radius
                and radius <= anchor.y <= height - radius
            ):
                continue
            observed = image[
                anchor.y - radius : anchor.y + radius,
                anchor.x - radius : anchor.x + radius,
            ].mean(axis=(0, 1))
            distance = math.dist(observed, anchor.rgb)
            if distance <= self._tutorial_overlay_threshold:
                matches.append((distance, anchor.name))
        # F4-11.  The shape anchors join the same pool.  A template hit is
        # binary by construction -- the corpus separates cleanly, 79 hand
        # frames above the cutoff and 400 hand-free frames below it -- so a hit
        # enters at distance zero rather than being scaled into a colour
        # distance it does not have.
        for shape in self._tutorial_overlay_shape_anchors:
            if self._shape_anchor_score(image, shape) >= shape.minimum_score:
                matches.append((0.0, shape.name))
        if len(matches) < self._tutorial_overlay_minimum_matches:
            return None
        # F4-08.  A tutorial we have to follow highlights a target; a plain hint
        # banner over a playable board does not.  The registry can name the
        # anchors that carry that distinction, and without one of them the
        # frame falls through to the ordinary scoring, where it reads as the
        # main board it is.  The 2026-09-06 17:59Z board carried the banner
        # "tap a piece to read its details" and no highlighted target at all,
        # and the guide-hand anchor at (130,440) matched the white order tray
        # underneath it; the guard tapped a stale coordinate five times and the
        # segment ended on not_on_board with the board sitting there playable.
        if self._tutorial_overlay_required_anchors:
            matched_names = {name for _distance, name in matches}
            if not any(
                any(name.endswith(f"/{required}") for name in matched_names)
                for required in self._tutorial_overlay_required_anchors
            ):
                return None
        selected = sorted(matches)[: self._tutorial_overlay_minimum_matches]
        mean_distance = sum(distance for distance, _name in selected) / len(selected)
        confidence = max(
            0.0,
            1.0 - mean_distance / self._tutorial_overlay_threshold,
        )
        if confidence < self._tutorial_overlay_minimum_confidence:
            return None
        return ScreenIdV1(
            name=TUTORIAL_OVERLAY_NAME,
            confidence=confidence,
            matched_anchors=sorted(name for _distance, name in matches),
        )

    def _identify_reward_continuation(self, image: np.ndarray) -> ScreenIdV1 | None:
        """Recognize the shared continue caption and reward cards, not their contents."""
        signature = self._reward_continuation
        region = signature.get("reward_card_region", [])
        if not self._reward_continuation_shapes or len(region) != 4:
            return None
        x0, y0, x1, y1 = map(int, region)
        cards = image[max(0, y0):y1, max(0, x0):x1]
        if cards.size == 0:
            return None
        bright = np.all(cards >= np.asarray(signature.get("card_minimum_rgb", [235, 230, 210])), axis=2)
        if float(bright.mean()) < float(signature.get("minimum_card_fraction", 0.13)):
            return None
        for shape in self._reward_continuation_shapes:
            score = self._shape_anchor_score(image, shape)
            if score >= shape.minimum_score:
                matched = [shape.name, "reward-panel/continuation_signature/reward-cards"]
                # Preserve observed variant anchors: some reward overlays have
                # a separately verified continuation point.
                radius = ANCHOR_SIZE // 2
                height, width = image.shape[:2]
                for anchor in self._surface_anchors.get("reward-panel", []):
                    if radius <= anchor.x <= width - radius and radius <= anchor.y <= height - radius:
                        patch = image[anchor.y-radius:anchor.y+radius, anchor.x-radius:anchor.x+radius]
                        if math.dist(patch.mean(axis=(0, 1)), anchor.rgb) <= self.rgb_distance_threshold:
                            matched.append(anchor.name)
                return ScreenIdV1(
                    name="reward-panel", confidence=min(1.0, score), matched_anchors=matched
                )
        return None

    def _shape_anchor_score(self, image: np.ndarray, shape: _ShapeAnchor) -> float:
        """Best normalized correlation for this template inside its window."""

        import cv2  # imported here so a registry that never looks stays light

        x0, y0, x1, y1 = shape.window
        height, width = image.shape[:2]
        window = image[
            max(y0, 0) : min(y1, height), max(x0, 0) : min(x1, width)
        ]
        if window.size == 0:
            return -1.0
        grey = cv2.cvtColor(window.astype(np.uint8), cv2.COLOR_RGB2GRAY)
        best = -1.0
        for template in shape.templates:
            if template.shape[0] > grey.shape[0] or template.shape[1] > grey.shape[1]:
                continue
            result = cv2.matchTemplate(grey, template, cv2.TM_CCOEFF_NORMED)
            best = max(best, float(result.max()))
        return best

    def _identify_forced_shop_tutorial(
        self,
        image: np.ndarray,
    ) -> ScreenIdV1 | None:
        """Reject the persistent cart icon unless the surveyed guide also matches."""

        radius = self._forced_shop_tutorial_anchor_size // 2
        height, width = image.shape[:2]
        matches: list[tuple[float, str]] = []
        for anchor in self._forced_shop_tutorial_anchors:
            if not (
                radius <= anchor.x <= width - radius
                and radius <= anchor.y <= height - radius
            ):
                continue
            observed = image[
                anchor.y - radius : anchor.y + radius,
                anchor.x - radius : anchor.x + radius,
            ].mean(axis=(0, 1))
            distance = math.dist(observed, anchor.rgb)
            if distance <= self._forced_shop_tutorial_threshold:
                matches.append((distance, anchor.name))
        if len(matches) < self._forced_shop_tutorial_minimum_matches:
            return None
        selected = sorted(matches)[: self._forced_shop_tutorial_minimum_matches]
        mean_distance = sum(distance for distance, _name in selected) / len(selected)
        confidence = max(
            0.0,
            1.0 - mean_distance / self._forced_shop_tutorial_threshold,
        )
        if confidence < self._forced_shop_tutorial_minimum_confidence:
            return None
        return ScreenIdV1(
            name=FORCED_SHOP_TUTORIAL_NAME,
            confidence=confidence,
            matched_anchors=sorted(name for _distance, name in matches),
        )


def _transition_or_unknown(image: np.ndarray) -> ScreenIdV1:
    luminance = image.astype(np.float32).mean(axis=2)
    dark_fraction = float(np.mean(luminance < TRANSITION_DARK_LUMINANCE))
    variance = float(np.var(image.astype(np.float32)))
    if dark_fraction > TRANSITION_DARK_PIXEL_FRACTION:
        return ScreenIdV1(
            name="transition",
            confidence=dark_fraction,
            matched_anchors=["transition/dark-frame"],
        )
    curtain_confidence = _blue_striped_curtain_confidence(image)
    if curtain_confidence is not None:
        return ScreenIdV1(
            name="transition",
            confidence=curtain_confidence,
            matched_anchors=["scene-transition-curtain/blue-striped-no-hud"],
        )
    if variance < TRANSITION_MAX_VARIANCE:
        confidence = max(0.0, 1.0 - variance / TRANSITION_MAX_VARIANCE)
        return ScreenIdV1(
            name="transition",
            confidence=confidence,
            matched_anchors=["transition/near-monochrome-frame"],
        )
    return ScreenIdV1(name="unknown", confidence=0.0)


def _blue_striped_curtain_confidence(image: np.ndarray) -> float | None:
    """Recognize the surveyed blue, vertically striped scene-loading curtain."""

    if image.shape[1] < 16:
        return None
    pixels = image.astype(np.float32)
    red, green, blue = np.moveaxis(pixels, 2, 0)
    maximum = pixels.max(axis=2)
    minimum = pixels.min(axis=2)
    saturation = (maximum - minimum) / np.maximum(maximum, 1.0)
    blue_hue = (
        (blue == maximum)
        & ((blue - red) >= 20.0)
        & ((blue - green) >= 8.0)
        & (saturation >= 0.12)
    )
    blue_fraction = float(np.mean(blue_hue))
    if blue_fraction < TRANSITION_CURTAIN_BLUE_PIXEL_FRACTION:
        return None

    # A curtain has almost no local HUD texture, but its vertical bands leave a
    # strong periodic signal in the per-column mean after removing linear drift.
    horizontal_edge_delta = float(np.abs(np.diff(pixels, axis=1)).mean())
    if horizontal_edge_delta > TRANSITION_CURTAIN_MAX_EDGE_DELTA:
        return None
    column_mean = pixels.mean(axis=(0, 2))
    x_axis = np.linspace(-1.0, 1.0, column_mean.size, dtype=np.float32)
    centered = column_mean - column_mean.mean()
    denominator = float(np.dot(x_axis, x_axis))
    if denominator > 0:
        centered = centered - x_axis * float(np.dot(centered, x_axis) / denominator)
    energy = np.square(np.abs(np.fft.rfft(centered)))
    energy[: min(3, energy.size)] = 0
    total_energy = float(energy.sum())
    if total_energy <= 0:
        return None
    periodic_energy = float(energy.max() / total_energy)
    if periodic_energy < TRANSITION_CURTAIN_MIN_PERIODIC_ENERGY:
        return None
    return min(
        1.0,
        blue_fraction,
        periodic_energy / 0.55,
        1.0 - horizontal_edge_delta / TRANSITION_CURTAIN_MAX_EDGE_DELTA,
    )


def identify_screen(frame_rgb: Any) -> ScreenIdV1:
    """Identify a screen with the default registered surface map."""

    return SurfaceRegistry().identify_screen(frame_rgb)


__all__ = [
    "ANCHOR_SIZE",
    "DEFAULT_AMBIGUITY_MARGIN",
    "DEFAULT_NAVIGATION_CONFIG",
    "DEFAULT_RGB_DISTANCE_THRESHOLD",
    "ForbiddenSurfaceV1",
    "INTRO_CHEST_REAL_MONEY_NAME",
    "ScreenIdV1",
    "STORY_CUTSCENE_NAME",
    "SurfaceRegistry",
    "TRANSITION_DARK_LUMINANCE",
    "TRANSITION_DARK_PIXEL_FRACTION",
    "TRANSITION_CURTAIN_BLUE_PIXEL_FRACTION",
    "TRANSITION_CURTAIN_MAX_EDGE_DELTA",
    "TRANSITION_CURTAIN_MIN_PERIODIC_ENERGY",
    "TRANSITION_MAX_VARIANCE",
    "TUTORIAL_OVERLAY_NAME",
    "VerifiedTapTransitionV1",
    "frame_to_rgb",
    "identify_screen",
]
