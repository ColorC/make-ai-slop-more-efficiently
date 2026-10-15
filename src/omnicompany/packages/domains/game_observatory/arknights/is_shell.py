# [OMNI] origin=codex domain=game_observatory/arknights ts=2026-08-10T02:03:00+08:00 type=runtime-contract status=active
"""Integrated Strategies non-battle semantic interaction shell.

The contestant chooses *what* to do (a visible node or option id).  Screen
coordinates remain private to the trusted I/O adapter, so contestants never
need to author click positions.  The shell deliberately carries no future
route, event outcome, recommendation, or strategy score.

This module does not control MuMu by itself.  It is the fail-closed contract
between a current-frame recognizer and the already-audited direct tap adapter.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, Sequence

__all__ = [
    "ActionReceiptV1",
    "DirectGestureKind",
    "ISActionKind",
    "ISInteractionDriver",
    "ISInteractionError",
    "ISObservationV1",
    "ISScreenKind",
    "PixelRectV1",
    "PublicISObservationV1",
    "SemanticISActionV1",
    "StaleISObservationError",
    "VisibleTargetV1",
    "build_observation",
    "legal_actions_for_screen",
]


class ISScreenKind(StrEnum):
    UNKNOWN = "unknown"
    ENTRY = "entry"
    ROUTE = "route"
    EVENT = "event"
    RECRUIT = "recruit"
    SHOP = "shop"
    SQUAD = "squad"
    RELIC = "relic"
    REWARD = "reward"
    DIALOG = "dialog"


class ISActionKind(StrEnum):
    ENTER = "is.enter"
    RESUME = "is.resume"
    SELECT_ROUTE_NODE = "route.select_visible_node"
    SCROLL_ROUTE = "route.scroll"
    CHOOSE_EVENT_OPTION = "event.choose_option"
    SCROLL_EVENT_OPTIONS = "event.scroll_options"
    CONFIRM_EVENT = "event.confirm"
    CHOOSE_RECRUIT = "recruit.choose"
    SCROLL_RECRUIT = "recruit.scroll"
    CONFIRM_RECRUIT = "recruit.confirm"
    CANCEL_RECRUIT = "recruit.cancel"
    BUY_SHOP_ITEM = "shop.buy"
    SCROLL_SHOP = "shop.scroll"
    CONFIRM_SHOP_PURCHASE = "shop.confirm_purchase"
    INVEST_SHOP = "shop.invest"
    REFRESH_SHOP = "shop.refresh"
    LEAVE_SHOP = "shop.leave"
    EDIT_SQUAD = "squad.edit"
    SCROLL_SQUAD = "squad.scroll"
    CONFIRM_SQUAD = "squad.confirm"
    CHOOSE_RELIC = "relic.choose"
    SCROLL_RELIC = "relic.scroll"
    CONFIRM_RELIC = "relic.confirm"
    CHOOSE_REWARD = "reward.choose"
    CONFIRM_REWARD = "reward.confirm"
    CONFIRM_DIALOG = "dialog.confirm"
    GO_BACK = "dialog.back"


class DirectGestureKind(StrEnum):
    TAP = "tap"
    SWIPE = "swipe"


_ALLOWED_BY_SCREEN: dict[ISScreenKind, frozenset[ISActionKind]] = {
    ISScreenKind.ENTRY: frozenset({ISActionKind.ENTER, ISActionKind.RESUME}),
    ISScreenKind.ROUTE: frozenset(
        {ISActionKind.SELECT_ROUTE_NODE, ISActionKind.SCROLL_ROUTE, ISActionKind.GO_BACK}
    ),
    ISScreenKind.EVENT: frozenset(
        {
            ISActionKind.CHOOSE_EVENT_OPTION,
            ISActionKind.SCROLL_EVENT_OPTIONS,
            ISActionKind.CONFIRM_EVENT,
            ISActionKind.GO_BACK,
        }
    ),
    ISScreenKind.RECRUIT: frozenset(
        {
            ISActionKind.CHOOSE_RECRUIT,
            ISActionKind.SCROLL_RECRUIT,
            ISActionKind.CONFIRM_RECRUIT,
            ISActionKind.CANCEL_RECRUIT,
            ISActionKind.GO_BACK,
        }
    ),
    ISScreenKind.SHOP: frozenset(
        {
            ISActionKind.BUY_SHOP_ITEM,
            ISActionKind.SCROLL_SHOP,
            ISActionKind.CONFIRM_SHOP_PURCHASE,
            ISActionKind.INVEST_SHOP,
            ISActionKind.REFRESH_SHOP,
            ISActionKind.LEAVE_SHOP,
            ISActionKind.GO_BACK,
        }
    ),
    ISScreenKind.SQUAD: frozenset(
        {
            ISActionKind.EDIT_SQUAD,
            ISActionKind.SCROLL_SQUAD,
            ISActionKind.CONFIRM_SQUAD,
            ISActionKind.GO_BACK,
        }
    ),
    ISScreenKind.RELIC: frozenset(
        {
            ISActionKind.CHOOSE_RELIC,
            ISActionKind.SCROLL_RELIC,
            ISActionKind.CONFIRM_RELIC,
            ISActionKind.GO_BACK,
        }
    ),
    ISScreenKind.REWARD: frozenset(
        {
            ISActionKind.CHOOSE_REWARD,
            ISActionKind.CONFIRM_REWARD,
            ISActionKind.GO_BACK,
        }
    ),
    ISScreenKind.DIALOG: frozenset({ISActionKind.CONFIRM_DIALOG, ISActionKind.GO_BACK}),
}


def legal_actions_for_screen(screen: ISScreenKind) -> frozenset[ISActionKind]:
    """Return the complete semantic I/O surface for one current screen.

    This is interaction capability, not a recommendation: the returned set
    says what the trusted adapter can execute after observing the current
    frame.  It carries neither a preferred action nor any hidden result.
    """

    return _ALLOWED_BY_SCREEN.get(screen, frozenset())


class ISInteractionError(ValueError):
    """The requested semantic action is not legal on the current frame."""


class StaleISObservationError(ISInteractionError):
    """The visible screen changed after the observation was issued."""


@dataclass(frozen=True, slots=True)
class PixelRectV1:
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if min(self.x, self.y) < 0 or self.width <= 0 or self.height <= 0:
            raise ValueError("pixel rectangle must be non-negative and non-empty")

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


@dataclass(frozen=True, slots=True)
class VisibleTargetV1:
    """A target observed on the current screen.

    ``rect`` is trusted-I/O data.  :meth:`ISObservationV1.public_view` removes
    it before the observation reaches a contestant.
    """

    target_id: str
    action: ISActionKind
    label: str
    rect: PixelRectV1
    enabled: bool = True
    gesture: DirectGestureKind = DirectGestureKind.TAP
    end_rect: PixelRectV1 | None = None
    duration_ms: int = 300

    def __post_init__(self) -> None:
        if not self.target_id.strip():
            raise ValueError("target_id must not be blank")
        if not self.label.strip():
            raise ValueError("visible target label must not be blank")
        if self.gesture is DirectGestureKind.SWIPE and self.end_rect is None:
            raise ValueError("swipe target requires a trusted end rectangle")
        if self.gesture is DirectGestureKind.TAP and self.end_rect is not None:
            raise ValueError("tap target must not carry swipe end geometry")
        if not 1 <= self.duration_ms <= 5_000:
            raise ValueError("gesture duration must be between 1 and 5000 ms")


@dataclass(frozen=True, slots=True)
class PublicTargetV1:
    target_id: str
    action: ISActionKind
    label: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class PublicISObservationV1:
    schema: str
    screen: ISScreenKind
    frame_sha256: str
    visible_text: tuple[str, ...]
    legal_targets: tuple[PublicTargetV1, ...]
    observation_id: str | None = None


@dataclass(frozen=True, slots=True)
class ISObservationV1:
    schema: str
    screen: ISScreenKind
    frame_sha256: str
    visible_text: tuple[str, ...]
    targets: tuple[VisibleTargetV1, ...]
    observation_id: str | None = None

    def __post_init__(self) -> None:
        if self.schema != "omnicompany.arknights.is-shell-observation.v1":
            raise ValueError(f"unsupported IS observation schema: {self.schema}")
        if len(self.frame_sha256) != 64:
            raise ValueError("frame_sha256 must be a sha256 hex digest")
        if self.observation_id is not None and not self.observation_id.strip():
            raise ValueError("observation_id must not be blank")
        if len({target.target_id for target in self.targets}) != len(self.targets):
            raise ValueError("visible target ids must be unique within one frame")
        allowed = _ALLOWED_BY_SCREEN.get(self.screen, frozenset())
        invalid = [target.action for target in self.targets if target.action not in allowed]
        if invalid:
            raise ValueError(f"targets contain actions illegal for {self.screen}: {invalid}")

    def public_view(self) -> PublicISObservationV1:
        """Return only current visible choices; coordinates never cross trust boundary."""

        return PublicISObservationV1(
            schema="omnicompany.arknights.is-shell-public-observation.v1",
            screen=self.screen,
            frame_sha256=self.frame_sha256,
            visible_text=self.visible_text,
            legal_targets=tuple(
                PublicTargetV1(
                    target_id=target.target_id,
                    action=target.action,
                    label=target.label,
                    enabled=target.enabled,
                )
                for target in self.targets
            ),
            observation_id=self.observation_id,
        )


@dataclass(frozen=True, slots=True)
class SemanticISActionV1:
    action: ISActionKind
    target_id: str
    expected_frame_sha256: str
    observation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.target_id.strip():
            raise ValueError("target_id must not be blank")
        if len(self.expected_frame_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.expected_frame_sha256
        ):
            raise ValueError("expected_frame_sha256 must be a lowercase sha256 digest")
        if self.observation_id is not None and not self.observation_id.strip():
            raise ValueError("observation_id must not be blank")


@dataclass(frozen=True, slots=True)
class ActionReceiptV1:
    schema: str
    sequence: int
    action: ISActionKind
    target_id: str
    gesture: DirectGestureKind
    before_frame_sha256: str
    after_frame_sha256: str
    previous_receipt_sha256: str | None
    receipt_sha256: str


class DirectScreenIO(Protocol):
    """Trusted adapter implemented by MAA/ADB direct screen operations."""

    def current_frame_sha256(self) -> str: ...

    def tap(self, x: int, y: int) -> None: ...

    def swipe(
        self,
        x: int,
        y: int,
        x2: int,
        y2: int,
        duration_ms: int,
    ) -> None: ...

    def capture_after_action_sha256(self) -> str: ...


def _canonical_json(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


class ISInteractionDriver:
    """Resolve a semantic choice to a current-frame direct tap and hash receipt."""

    def __init__(self, io: DirectScreenIO) -> None:
        self._io = io
        self._sequence = 0
        self._previous_receipt_sha256: str | None = None

    def execute(
        self,
        observation: ISObservationV1,
        action: SemanticISActionV1,
    ) -> ActionReceiptV1:
        allowed = _ALLOWED_BY_SCREEN.get(observation.screen, frozenset())
        if action.action not in allowed:
            raise ISInteractionError(f"{action.action} is not legal on {observation.screen}")
        if action.expected_frame_sha256 != observation.frame_sha256:
            raise StaleISObservationError("action was created from a different frame")
        actual_frame = self._io.current_frame_sha256()
        if actual_frame != observation.frame_sha256:
            raise StaleISObservationError("screen changed after observation")

        candidates = [
            target
            for target in observation.targets
            if target.target_id == action.target_id and target.action == action.action
        ]
        if len(candidates) != 1:
            raise ISInteractionError("target is absent or does not support requested action")
        target = candidates[0]
        if not target.enabled:
            raise ISInteractionError("target is currently disabled")

        if target.gesture is DirectGestureKind.TAP:
            self._io.tap(*target.rect.center)
        else:
            if target.end_rect is None:  # protected by VisibleTargetV1 validation
                raise ISInteractionError("swipe target is missing end geometry")
            self._io.swipe(
                *target.rect.center,
                *target.end_rect.center,
                target.duration_ms,
            )
        after_frame = self._io.capture_after_action_sha256()
        self._sequence += 1
        unsigned: dict[str, object] = {
            "schema": "omnicompany.arknights.is-shell-action-receipt.v1",
            "sequence": self._sequence,
            "action": action.action.value,
            "target_id": action.target_id,
            "gesture": target.gesture.value,
            "before_frame_sha256": observation.frame_sha256,
            "after_frame_sha256": after_frame,
            "previous_receipt_sha256": self._previous_receipt_sha256,
        }
        receipt_hash = hashlib.sha256(_canonical_json(unsigned)).hexdigest()
        receipt = ActionReceiptV1(
            schema=str(unsigned["schema"]),
            sequence=self._sequence,
            action=action.action,
            target_id=action.target_id,
            gesture=target.gesture,
            before_frame_sha256=observation.frame_sha256,
            after_frame_sha256=after_frame,
            previous_receipt_sha256=self._previous_receipt_sha256,
            receipt_sha256=receipt_hash,
        )
        self._previous_receipt_sha256 = receipt_hash
        return receipt


def build_observation(
    *,
    screen: ISScreenKind,
    frame_bytes: bytes,
    visible_text: Sequence[str],
    targets: Sequence[VisibleTargetV1],
) -> ISObservationV1:
    """Build an immutable current-frame observation from recognizer output."""

    return ISObservationV1(
        schema="omnicompany.arknights.is-shell-observation.v1",
        screen=screen,
        frame_sha256=hashlib.sha256(frame_bytes).hexdigest(),
        visible_text=tuple(visible_text),
        targets=tuple(targets),
    )
