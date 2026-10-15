# [OMNI] origin=codex created_by=codex intent="execute one bounded, save-verified Romantic Restaurant main-task route"
"""Bounded main-task navigation over surveyed Romantic Restaurant surfaces."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from .contracts import ActionRequestV1, ActionResultV1
from .navigation import ScreenIdV1, SurfaceRegistry, frame_to_rgb
from .popup_handler import (
    STORY_ADVANCE_FLOW_BUDGET,
    FlowPopupBudget,
    action_for_screen,
    handle_known_popups,
    story_advance_action,
)
from .semantics import derive_game_state_semantics


# 2026-09-16.  A day-9 task opens two cutscenes back to back, and a single skip
# left the second one on screen, which ended the segment as an unknown surface
# and cost a whole main task.  Every skip is preceded by a fresh observation
# that the surface still is the cutscene, so the budget only bounds how many
# such scenes one task may chain.
CUTSCENE_SKIP_BUDGET = 3
MAIN_TASK_PANEL_TAP_ATTEMPTS = 2
MAIN_TASK_PANEL_TIMEOUT_S = 3.0

MainTaskFlowStatus = Literal[
    "completed",
    "insufficient_coins",
    "precondition_failed",
    # 2026-09-11, the 18:22Z main-task drive (F4-10-main-task-S2-20260911T182232Z).
    # During the sea-winner event the competition HUD replaces the board's top
    # strip and the task notebook is hidden by game mechanism -- the main line
    # is unavailable, not broken.  Kept apart from precondition_failed, which
    # names missing authoritative data (a facility-shaped signal), so the
    # driver can end the shift cleanly instead of counting barren segments.
    "unavailable",
    "stopped_unknown_screen",
    "action_failed",
    "story_limit_reached",
    "verification_failed",
    "stopped_forbidden_surface",
]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)


class SaveStateSummaryV1(_FrozenModel):
    coins: int | None
    energy: int | None
    level: int | None
    main_task_id: str | None
    main_task_cost: int | None


class ScreenObservationV1(_FrozenModel):
    step: str
    observed_at: str
    frame_id: str
    name: str
    confidence: float = Field(ge=0, le=1)
    matched_anchors: list[str]
    capture_ms: float = Field(ge=0)


# F4-09 section 1.1.  Guard and flow actions whose effect on the account has to
# be measured against the authoritative save rather than asserted from the
# whitelist: a claim, and every close, because "the close granted nothing" is
# exactly the claim the red-line audit rests on.  Tutorial, selection, confirm
# and navigation taps stay out: they move between surfaces and their effects
# are the business actions the round already adjudicates.
CLAIM_SAVE_EVIDENCE_KINDS = frozenset({"free-claim", "close"})
CLAIM_SAVE_EVIDENCE_FIELDS = (
    "energy",
    "coins",
    "gems",
    "xp",
    "level",
    "board_energy_items",
)
ClaimEvidenceWindow = Literal[
    "round_before_to_post_guard",
    "flow_before_to_flow_after",
]


class PopupSaveDeltaV1(_FrozenModel):
    """What the authoritative save says a guard claim or close actually moved.

    F4-09 section 1.1.  The window is bounded by two save reads: the state the
    round already read before the guard acted, and one forced read taken once
    the guard has finished acting.  Every claim-class action inside that window
    shares the same measurement, so ``group_action_ids`` names them all and
    ``guard_actions_in_window`` names every guard action of any kind that could
    have contributed.  A grant that the game had already banked reads as an
    all-zero delta, which is the point: that is the evidence, not a failure.
    """

    available: bool
    reason: str | None = None
    window: ClaimEvidenceWindow = "round_before_to_post_guard"
    group_id: str | None = None
    group_action_ids: list[str] = Field(default_factory=list)
    guard_actions_in_window: list[str] = Field(default_factory=list)
    before: dict[str, int | None] = Field(default_factory=dict)
    after: dict[str, int | None] = Field(default_factory=dict)
    delta: dict[str, int] = Field(default_factory=dict)
    board_items_delta: dict[str, int] = Field(default_factory=dict)
    read_s: float = Field(default=0.0, ge=0)


class PopupHandlingRecordV1(_FrozenModel):
    screen: str
    kind: str
    element: str | None
    action_id: str
    save_delta: PopupSaveDeltaV1 | None = None


class MainTaskResultV1(_FrozenModel):
    status: MainTaskFlowStatus
    reason: str
    before: SaveStateSummaryV1
    after: SaveStateSummaryV1 | None
    observations: list[ScreenObservationV1]
    popup_actions: list[PopupHandlingRecordV1]
    action_results: list[ActionResultV1]
    story_taps: int = Field(ge=0, le=STORY_ADVANCE_FLOW_BUDGET)
    started_main_task: bool
    returned_to_board: bool
    before_state_payload: dict[str, Any]
    after_state_payload: dict[str, Any] | None
    elapsed_s: float = Field(ge=0)


def _board_item_counts(state: Any) -> dict[str, int]:
    """Count board occupants by raw item code, ignoring empty cells."""

    counts: dict[str, int] = {}
    for row in state.board.cells:
        for cell in row:
            code = getattr(cell, "raw_code", None)
            if code in {None, ""}:
                continue
            key = str(code)
            counts[key] = counts.get(key, 0) + 1
    return counts


def _claim_evidence_fields(state: Any) -> dict[str, int | None]:
    resources = state.resources
    return {
        "energy": resources.energy,
        "coins": resources.coins,
        "gems": resources.gems,
        "xp": resources.xp,
        "level": resources.level,
        "board_energy_items": len(state.board.energy_items),
    }


def build_claim_save_delta(
    before: Any,
    after: Any,
    *,
    group_id: str,
    group_action_ids: list[str],
    guard_actions_in_window: list[str],
    read_s: float,
    window: ClaimEvidenceWindow = "round_before_to_post_guard",
) -> PopupSaveDeltaV1:
    """Difference two authoritative reads over the resources a claim can move."""

    before_fields = _claim_evidence_fields(before)
    after_fields = _claim_evidence_fields(after)
    delta = {
        field: int(after_fields[field]) - int(before_fields[field])
        for field in CLAIM_SAVE_EVIDENCE_FIELDS
        if before_fields[field] is not None and after_fields[field] is not None
    }
    before_items = _board_item_counts(before)
    after_items = _board_item_counts(after)
    board_items_delta = {
        code: after_items.get(code, 0) - before_items.get(code, 0)
        for code in sorted(set(before_items) | set(after_items))
        if after_items.get(code, 0) != before_items.get(code, 0)
    }
    return PopupSaveDeltaV1(
        available=True,
        window=window,
        group_id=group_id,
        group_action_ids=group_action_ids,
        guard_actions_in_window=guard_actions_in_window,
        before=before_fields,
        after=after_fields,
        delta=delta,
        board_items_delta=board_items_delta,
        read_s=max(read_s, 0.0),
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _state_summary(state: Any, config_dir: Path) -> SaveStateSummaryV1:
    semantic = derive_game_state_semantics(state, config_dir)
    task = semantic.main_task
    raw_task = (
        state.get("main_task")
        if isinstance(state, Mapping)
        else getattr(state, "main_task", None)
    )
    raw_task_id: str | None = None
    raw_task_cost: int | None = None
    if isinstance(raw_task, Mapping):
        identity = raw_task.get("task_id", raw_task.get("curId"))
        current_cost = raw_task.get("curCost")
        if identity is None and isinstance(current_cost, str):
            identity = current_cost.split("##", maxsplit=1)[0]
        if identity is not None:
            raw_task_id = str(identity)
        if isinstance(current_cost, str) and "##" in current_cost:
            try:
                raw_task_cost = int(current_cost.rsplit("##", maxsplit=1)[1])
            except ValueError:
                raw_task_cost = None
    return SaveStateSummaryV1(
        coins=semantic.resources.coins,
        energy=semantic.resources.energy,
        level=semantic.resources.level,
        main_task_id=raw_task_id if task is None else task.task_id,
        main_task_cost=raw_task_cost if task is None else task.cost,
    )


class MainTaskFlow:
    """Execute at most one surveyed main task and verify its save delta."""

    def __init__(
        self,
        registry: SurfaceRegistry | None = None,
        *,
        config_dir: Path = Path(
            "data/domains/game_observatory/game_models/romantic-restaurant/config"
        ),
        story_tap_limit: int = STORY_ADVANCE_FLOW_BUDGET,
        transition_wait_s: float = 0.25,
        transition_poll_s: float = 0.2,
        transition_timeout_s: float = 8.0,
        sleep: Callable[[float], None] = time.sleep,
        identify: Callable[[Any], ScreenIdV1] | None = None,
    ) -> None:
        if not 1 <= story_tap_limit <= STORY_ADVANCE_FLOW_BUDGET:
            raise ValueError(
                f"story_tap_limit must be between 1 and {STORY_ADVANCE_FLOW_BUDGET}"
            )
        if transition_wait_s < 0:
            raise ValueError("transition_wait_s must be non-negative")
        if transition_poll_s <= 0 or transition_timeout_s <= 0:
            raise ValueError("transition polling values must be positive")
        self.registry = registry or SurfaceRegistry()
        # F4-07 open item 3: the guard's re-adjudication (tutorial-overlay with
        # no guide hand over an uncovered board reads as main-board; a covered
        # board reads as overlay-unknown) lives in ``game_loop`` and would form
        # an import cycle here, so the caller injects it.  Without the
        # injection the flow saw the raw identification and stopped on
        # "expected main-board, observed tutorial-overlay" (F4-06 segment L).
        self.identify: Callable[[Any], ScreenIdV1] = (
            identify if identify is not None else self.registry.identify_screen
        )
        self.config_dir = Path(config_dir)
        self.story_tap_limit = story_tap_limit
        self.transition_wait_s = transition_wait_s
        self.transition_poll_s = transition_poll_s
        self.transition_timeout_s = transition_timeout_s
        self.sleep = sleep

    def _capture(
        self,
        actuator: Any,
        run: Any,
        step: str,
    ) -> tuple[np.ndarray, ScreenIdV1, ScreenObservationV1]:
        if hasattr(actuator, "screenshot_raw"):
            payload, width, height, channels, capture_ms = actuator.screenshot_raw()
            channel_count = 4 if channels in {"RGBA", "BGRA"} else 3
            array = np.frombuffer(payload, dtype=np.uint8).reshape(
                int(height), int(width), channel_count
            )
            if channels == "BGRA":
                array = array[:, :, [2, 1, 0, 3]]
            rgb = frame_to_rgb(array)
            frame = run.add_frame_raw(
                payload,
                int(width),
                int(height),
                channels=channels,
                trigger="manual",
            )
        else:
            payload, capture_ms = actuator.screenshot()
            rgb = frame_to_rgb(payload)
            frame = run.add_frame(payload, trigger="manual")
        screen = self.identify(rgb)
        observation = ScreenObservationV1(
            step=step,
            observed_at=_utc_now(),
            frame_id=str(frame.frame_id),
            name=screen.name,
            confidence=screen.confidence,
            matched_anchors=screen.matched_anchors,
            capture_ms=max(float(capture_ms), 0.0),
        )
        return rgb, screen, observation

    def _tap_element(
        self,
        actuator: Any,
        surface: str,
        element: str,
        *,
        intent: str,
        frame_rgb: Any = None,
    ) -> ActionResultV1:
        x, y = self.registry.resolve_point(surface, element, frame_rgb)
        return actuator.act(
            ActionRequestV1(
                kind="tap",
                intent=intent,
                params={"x": x, "y": y},
                settle="stable",
            )
        )

    def _tap_registered_point(
        self,
        actuator: Any,
        surface: str,
        key: str,
        *,
        intent: str,
    ) -> ActionResultV1:
        value = self.registry.surfaces[surface].get(key)
        center = value.get("center") if isinstance(value, dict) else None
        if not isinstance(center, list) or len(center) != 2:
            raise ValueError(f"unregistered navigation point: {surface}/{key}")
        return actuator.act(
            ActionRequestV1(
                kind="tap",
                intent=intent,
                params={"x": int(center[0]), "y": int(center[1])},
                settle="stable",
            )
        )

    def _wait_for_action_surface(
        self,
        actuator: Any,
        run: Any,
        *,
        step: str,
        rgb: np.ndarray,
        screen: ScreenIdV1,
        observations: list[ScreenObservationV1],
        transient_names: frozenset[str] = frozenset({"transition", "unknown"}),
    ) -> tuple[np.ndarray, ScreenIdV1]:
        """Wait through transient or not-yet-identifiable post-action frames."""

        remaining_polls = max(
            1,
            int(self.transition_timeout_s / self.transition_poll_s),
        )
        extended_transition: str | None = None
        saw_registered_transition = self.registry.is_transition(screen.name)
        poll = 0
        while self._is_waiting_surface(screen, transient_names=transient_names):
            timeout_s = self.registry.transition_timeout_s(
                screen.name,
                default=self.transition_timeout_s,
            )
            if timeout_s > self.transition_timeout_s and screen.name != extended_transition:
                remaining_polls = max(
                    remaining_polls,
                    int(timeout_s / self.transition_poll_s),
                )
                extended_transition = screen.name
            if remaining_polls <= 0:
                break
            poll += 1
            remaining_polls -= 1
            self.sleep(self.transition_poll_s)
            rgb, screen, observed = self._capture(
                actuator,
                run,
                f"{step}:transition:{poll}",
            )
            observations.append(observed)
            saw_registered_transition = (
                saw_registered_transition or self.registry.is_transition(screen.name)
            )
        if saw_registered_transition and not self._is_waiting_surface(
            screen,
            transient_names=transient_names,
        ):
            refresh = getattr(actuator, "refresh_display_id", None)
            if callable(refresh):
                refresh(reason=f"transition-ended:{step}")
        return rgb, screen

    def _poll_for_surface(
        self,
        actuator: Any,
        run: Any,
        *,
        step: str,
        rgb: np.ndarray,
        screen: ScreenIdV1,
        observations: list[ScreenObservationV1],
        target: str,
    ) -> tuple[np.ndarray, ScreenIdV1]:
        """Poll a registered surface into view without issuing any action."""

        remaining_polls = max(
            1,
            int(MAIN_TASK_PANEL_TIMEOUT_S / self.transition_poll_s),
        )
        poll = 0
        while screen.name != target and remaining_polls > 0:
            poll += 1
            remaining_polls -= 1
            self.sleep(self.transition_poll_s)
            rgb, screen, observed = self._capture(
                actuator,
                run,
                f"{step}:poll:{poll}",
            )
            observations.append(observed)
        return rgb, screen

    def _is_waiting_surface(
        self,
        screen: ScreenIdV1,
        *,
        transient_names: frozenset[str] = frozenset(),
    ) -> bool:
        """Treat every registered transition and an unknown frame as wait-only."""

        return (
            screen.name == "unknown"
            or self.registry.is_transition(screen.name)
            or screen.name in transient_names
        )

    def _unresolved_wait_reason(self, screen: ScreenIdV1) -> str:
        timeout_s = self.registry.transition_timeout_s(
            screen.name,
            default=self.transition_timeout_s,
        )
        return (
            "transition or unknown frame did not resolve within "
            f"{timeout_s:g} seconds"
        )

    def _resolve_story_cutscene(
        self,
        actuator: Any,
        run: Any,
        *,
        rgb: np.ndarray,
        screen: ScreenIdV1,
        observations: list[ScreenObservationV1],
        action_results: list[ActionResultV1],
        skips_used: int,
    ) -> tuple[
        np.ndarray,
        ScreenIdV1,
        int,
        tuple[MainTaskFlowStatus, str] | None,
    ]:
        """Observe a cutscene for three seconds, then skip it within budget."""

        wait_action = action_for_screen(screen, self.registry, attempt=0)
        if wait_action is None or wait_action.request.kind != "wait":
            return (
                rgb,
                screen,
                skips_used,
                ("stopped_unknown_screen", "cutscene wait action is not registered"),
            )
        wait_result = actuator.act(wait_action.request)
        action_results.append(wait_result)
        if wait_result.error:
            return rgb, screen, skips_used, ("action_failed", "cutscene wait failed")
        rgb, screen, observed = self._capture(
            actuator,
            run,
            "story:cutscene:after-three-seconds",
        )
        observations.append(observed)
        rgb, screen = self._wait_for_action_surface(
            actuator,
            run,
            step="story:cutscene:after-wait",
            rgb=rgb,
            screen=screen,
            observations=observations,
        )
        if screen.name != "story-cutscene":
            return rgb, screen, skips_used, None
        if skips_used >= CUTSCENE_SKIP_BUDGET:
            return (
                rgb,
                screen,
                skips_used,
                (
                    "stopped_unknown_screen",
                    "story cutscene remained active after "
                    f"{CUTSCENE_SKIP_BUDGET} skips",
                ),
            )
        skip_action = action_for_screen(screen, self.registry, attempt=1)
        if skip_action is None or skip_action.request.kind != "tap":
            return (
                rgb,
                screen,
                skips_used,
                ("stopped_unknown_screen", "cutscene skip action is not registered"),
            )
        refresh = getattr(actuator, "refresh_display_id", None)
        if callable(refresh):
            try:
                refresh(reason="story-cutscene-before-skip")
            except Exception:
                return (
                    rgb,
                    screen,
                    skips_used,
                    ("action_failed", "cutscene input refresh failed"),
                )
        skip_result = actuator.act(skip_action.request)
        action_results.append(skip_result)
        skips_used += 1
        if skip_result.error:
            return rgb, screen, skips_used, ("action_failed", "cutscene skip failed")
        self.sleep(self.transition_wait_s)
        rgb, screen, observed = self._capture(
            actuator,
            run,
            "story:cutscene:after-skip",
        )
        observations.append(observed)
        rgb, screen = self._wait_for_action_surface(
            actuator,
            run,
            step="story:cutscene:exit",
            rgb=rgb,
            screen=screen,
            observations=observations,
            transient_names=frozenset(
                {"transition", "unknown", "story-cutscene"}
            ),
        )
        if self._is_waiting_surface(
            screen,
            transient_names=frozenset({"story-cutscene"}),
        ):
            if screen.name == "story-cutscene" and skips_used < CUTSCENE_SKIP_BUDGET:
                # Still the cutscene, and the budget has skips left: hand the
                # surface back so the caller observes it once more and spends
                # another one.  The two-stage chalkboard control needs its
                # second tap here, and a task that chains two scenes needs a
                # skip for each.
                return rgb, screen, skips_used, None
            reason = (
                f"story cutscene remained active after {skips_used} skips"
                if screen.name == "story-cutscene"
                else self._unresolved_wait_reason(screen)
            )
            return (
                rgb,
                screen,
                skips_used,
                (
                    "stopped_unknown_screen",
                    reason,
                ),
            )
        return rgb, screen, skips_used, None

    def _stamp_claim_save_evidence(
        self,
        records: list[PopupHandlingRecordV1],
        before_result: Any,
        after_result: Any | None,
    ) -> list[PopupHandlingRecordV1]:
        """Attach the flow's own before/after reads to its claims and closes.

        F4-09 section 1.1.  The flow walks a chain of surfaces, so a forced read
        between two of its steps would send the game HOME in the middle of a
        story or a panel; the two reads the flow already takes bound the whole
        route instead.  The window is named on the record so nobody mistakes it
        for the tight per-guard measurement, and every guard action inside it is
        listed so a shared difference stays attributable.
        """

        positions = [
            index
            for index, record in enumerate(records)
            if record.kind in CLAIM_SAVE_EVIDENCE_KINDS
        ]
        if not positions:
            return records
        group_action_ids = [records[index].action_id for index in positions]
        window = [
            f"{record.screen}:{record.kind}:{record.element or ''}"
            for record in records
        ]
        if after_result is None:
            delta = PopupSaveDeltaV1(
                available=False,
                reason="main-task flow ended without an authoritative after read",
                window="flow_before_to_flow_after",
                group_id=group_action_ids[0],
                group_action_ids=group_action_ids,
                guard_actions_in_window=window,
            )
        else:
            delta = build_claim_save_delta(
                derive_game_state_semantics(before_result.state, self.config_dir),
                derive_game_state_semantics(after_result.state, self.config_dir),
                group_id=group_action_ids[0],
                group_action_ids=group_action_ids,
                guard_actions_in_window=window,
                read_s=float(getattr(getattr(after_result, "timing", None), "total_s", 0.0) or 0.0),
                window="flow_before_to_flow_after",
            )
        updated = list(records)
        for index in positions:
            updated[index] = updated[index].model_copy(update={"save_delta": delta})
        return updated

    def run(self, save_channel: Any, actuator: Any, run: Any) -> MainTaskResultV1:
        """Run one main-task state machine; never guess on an unknown surface."""

        return self._run(save_channel, actuator, run, resume_from_story=False)

    def resume_from_story(
        self,
        save_channel: Any,
        actuator: Any,
        run: Any,
    ) -> MainTaskResultV1:
        """Resume an already-started task without tapping its panel or Start again."""

        return self._run(save_channel, actuator, run, resume_from_story=True)

    def _run(
        self,
        save_channel: Any,
        actuator: Any,
        run: Any,
        *,
        resume_from_story: bool,
    ) -> MainTaskResultV1:
        """Implement fresh and story-resume entry points with one state machine."""

        started = time.perf_counter()
        before_result = save_channel.read_state(flush="none", include_board=True)
        before_payload = before_result.state.model_dump(mode="json")
        before = _state_summary(before_result.state, self.config_dir)
        observations: list[ScreenObservationV1] = []
        popup_records: list[PopupHandlingRecordV1] = []
        action_results: list[ActionResultV1] = []
        story_taps = 0
        started_main_task = resume_from_story
        returned_to_board = False

        def finish(
            status: MainTaskFlowStatus,
            reason: str,
            *,
            after_result: Any | None = None,
        ) -> MainTaskResultV1:
            after_payload = (
                None
                if after_result is None
                else after_result.state.model_dump(mode="json")
            )
            after = (
                None
                if after_result is None
                else _state_summary(after_result.state, self.config_dir)
            )
            result = MainTaskResultV1(
                status=status,
                reason=reason,
                before=before,
                after=after,
                observations=observations,
                popup_actions=self._stamp_claim_save_evidence(
                    popup_records,
                    before_result,
                    after_result,
                ),
                action_results=action_results,
                story_taps=story_taps,
                started_main_task=started_main_task,
                returned_to_board=returned_to_board,
                before_state_payload=before_payload,
                after_state_payload=after_payload,
                elapsed_s=max(time.perf_counter() - started, 0.0),
            )
            if status != "completed":
                run.alert(f"main-task flow stopped: {status}")
            return result

        if before.main_task_id is None or before.main_task_cost is None:
            return finish("precondition_failed", "main-task id or cost is unavailable")
        if before.coins is None:
            return finish("precondition_failed", "authoritative coin balance is unavailable")
        if not resume_from_story:
            if before.coins is None or before.coins < before.main_task_cost:
                return finish(
                    "insufficient_coins",
                    "authoritative coin balance is below the current main-task cost",
                )

            rgb, screen, observed = self._capture(actuator, run, "board")
            observations.append(observed)
            if screen.name != "main-board":
                return finish(
                    "stopped_unknown_screen",
                    f"expected main-board, observed {screen.name}",
                )

            # F4-09 1.4.  The notebook can be covered rather than moved: with
            # five orders in the save the leading customer portrait stands over
            # it, no registered variant matches, and resolve_point falls back to
            # the base centre.  That fallback is what produced "expected
            # main-task-panel, observed main-board" -- two taps on a portrait,
            # sixteen polls, and a stopped flow.  Refuse to tap instead, and say
            # what the frame showed where the notebook should be.
            #
            # 2026-09-11 ruling after the 18:22Z drive: the same check also
            # fires when an event HUD (the sea-winner competition strip)
            # replaces the notebook outright.  That is the game hiding the
            # main line for the event's duration, so the status is
            # ``unavailable`` -- the driver's cue to end the shift cleanly --
            # and pushing the main line anyway (dismissing the HUD, hunting
            # for the notebook elsewhere) is explicitly not wanted.
            notebook_point = self.registry.visible_variant_point(
                "main-board", "task-notebook", rgb
            )
            if notebook_point is None:
                # 2026-09-15 live stream: some board layouts move the task
                # notebook into the restaurant scene.  The restaurant entry is
                # itself a measured multi-anchor variant; no fallback point is
                # allowed when either signature is absent.
                try:
                    entry_point = self.registry.visible_variant_point(
                        "main-board", "restaurant-entry", rgb
                    )
                except KeyError:
                    # Older injected registries do not know the measured
                    # restaurant route; preserve their fail-closed behaviour.
                    entry_point = None
                if entry_point is None:
                    return finish(
                        "unavailable",
                        "the task notebook is not visible at any registered "
                        "position and the measured restaurant entry is not "
                        "visible",
                    )
                action = self._tap_element(
                    actuator,
                    "main-board",
                    "restaurant-entry",
                    intent="main-task:open-restaurant",
                    frame_rgb=rgb,
                )
                action_results.append(action)
                if action.error:
                    return finish(
                        "action_failed",
                        "failed to open the restaurant scene",
                    )
                self.sleep(self.transition_wait_s)
                rgb, screen, observed = self._capture(
                    actuator,
                    run,
                    "restaurant_entry",
                )
                observations.append(observed)
                rgb, screen = self._wait_for_action_surface(
                    actuator,
                    run,
                    step="restaurant_entry",
                    rgb=rgb,
                    screen=screen,
                    observations=observations,
                )
                # A known popup may appear while the scene is loading.  Give
                # the shared popup handler its bounded close/claim action;
                # leave an unregistered frame untouched for the caller to
                # explore rather than guessing a coordinate.
                for popup_attempt in range(3):
                    if screen.name == "restaurant-scene":
                        break
                    popup = handle_known_popups(
                        rgb,
                        self.registry,
                        attempt=popup_attempt,
                    )
                    if popup is None:
                        break
                    action = actuator.act(popup.request)
                    action_results.append(action)
                    popup_records.append(
                        PopupHandlingRecordV1(
                            screen=popup.screen,
                            kind=popup.kind,
                            element=popup.element,
                            action_id=action.action_id,
                        )
                    )
                    if action.error:
                        return finish(
                            "action_failed",
                            "known popup action failed while opening the restaurant",
                        )
                    self.sleep(self.transition_wait_s)
                    rgb, screen, observed = self._capture(
                        actuator,
                        run,
                        f"restaurant_entry:popup:{popup_attempt}",
                    )
                    observations.append(observed)
                    rgb, screen = self._wait_for_action_surface(
                        actuator,
                        run,
                        step=f"restaurant_entry:popup:{popup_attempt}",
                        rgb=rgb,
                        screen=screen,
                        observations=observations,
                    )
                rgb, screen = self._poll_for_surface(
                    actuator,
                    run,
                    step="restaurant_entry",
                    rgb=rgb,
                    screen=screen,
                    observations=observations,
                    target="restaurant-scene",
                )
                if screen.name != "restaurant-scene":
                    return finish(
                        "stopped_unknown_screen",
                        f"expected restaurant-scene, observed {screen.name}",
                    )
                try:
                    scene_notebook = self.registry.visible_variant_point(
                        "restaurant-scene", "task-notebook", rgb
                    )
                except KeyError:
                    scene_notebook = None
                if scene_notebook is None:
                    return finish(
                        "unavailable",
                        "the restaurant scene task notebook is not visible at "
                        "its measured position",
                    )
                action = self._tap_element(
                    actuator,
                    "restaurant-scene",
                    "task-notebook",
                    intent="main-task:open-panel-from-restaurant",
                    frame_rgb=rgb,
                )
                action_results.append(action)
                if action.error:
                    return finish(
                        "action_failed",
                        "failed to open the main-task panel from the restaurant",
                    )
                self.sleep(self.transition_wait_s)
                rgb, screen, observed = self._capture(
                    actuator,
                    run,
                    "task_panel:restaurant",
                )
                observations.append(observed)
                rgb, screen = self._wait_for_action_surface(
                    actuator,
                    run,
                    step="task_panel:restaurant",
                    rgb=rgb,
                    screen=screen,
                    observations=observations,
                )
                rgb, screen = self._poll_for_surface(
                    actuator,
                    run,
                    step="task_panel:restaurant",
                    rgb=rgb,
                    screen=screen,
                    observations=observations,
                    target="main-task-panel",
                )
            else:
                # The first notebook tap after the save-flush foreground
                # return is sometimes swallowed, so the same registered point
                # is offered a bounded second attempt before giving up.
                for attempt in range(MAIN_TASK_PANEL_TAP_ATTEMPTS):
                    action = self._tap_element(
                        actuator,
                        "main-board",
                        "task-notebook",
                        intent="main-task:open-panel",
                        frame_rgb=rgb,
                    )
                    action_results.append(action)
                    if action.error:
                        return finish(
                            "action_failed",
                            "failed to open the main-task panel",
                        )
                    self.sleep(self.transition_wait_s)
                    rgb, screen, observed = self._capture(
                        actuator,
                        run,
                        f"task_panel:{attempt}",
                    )
                    observations.append(observed)
                    rgb, screen = self._wait_for_action_surface(
                        actuator,
                        run,
                        step=f"task_panel:{attempt}",
                        rgb=rgb,
                        screen=screen,
                        observations=observations,
                    )
                    rgb, screen = self._poll_for_surface(
                        actuator,
                        run,
                        step=f"task_panel:{attempt}",
                        rgb=rgb,
                        screen=screen,
                        observations=observations,
                        target="main-task-panel",
                    )
                    if screen.name == "main-task-panel":
                        break
                    if screen.name != "main-board":
                        break
            if screen.name != "main-task-panel":
                return finish(
                    "stopped_unknown_screen",
                    f"expected main-task-panel, observed {screen.name}",
                )

            action = self._tap_element(
                actuator,
                "main-task-panel",
                "start",
                intent=f"main-task:start:{before.main_task_id}",
            )
            action_results.append(action)
            started_main_task = action.error is None
            if action.error:
                return finish("action_failed", "failed to start the main task")
            self.sleep(self.transition_wait_s)
            rgb, screen, observed = self._capture(actuator, run, "story:0")
            observations.append(observed)
        else:
            rgb, screen, observed = self._capture(
                actuator,
                run,
                "resume:story",
            )
            observations.append(observed)

        rgb, screen = self._wait_for_action_surface(
            actuator,
            run,
            step="story:entry",
            rgb=rgb,
            screen=screen,
            observations=observations,
        )
        if self._is_waiting_surface(screen):
            return finish(
                "stopped_unknown_screen",
                self._unresolved_wait_reason(screen),
            )

        cutscene_skips_used = 0
        while story_taps < self.story_tap_limit:
            if screen.name == "story-cutscene":
                (
                    rgb,
                    screen,
                    cutscene_skips_used,
                    cutscene_failure,
                ) = self._resolve_story_cutscene(
                    actuator,
                    run,
                    rgb=rgb,
                    screen=screen,
                    observations=observations,
                    action_results=action_results,
                    skips_used=cutscene_skips_used,
                )
                if cutscene_failure is not None:
                    status, reason = cutscene_failure
                    return finish(status, reason)
                continue
            if screen.name != "story-dialogue":
                break
            advance = story_advance_action(screen, self.registry)
            action = actuator.act(
                advance.request.model_copy(
                    update={"intent": f"main-task:story:{story_taps + 1}"}
                )
            )
            action_results.append(action)
            story_taps += 1
            if action.error:
                return finish("action_failed", "story advance action failed")
            self.sleep(self.transition_wait_s)
            rgb, screen, observed = self._capture(
                actuator,
                run,
                f"story:{story_taps}",
            )
            observations.append(observed)
            rgb, screen = self._wait_for_action_surface(
                actuator,
                run,
                step=f"story:{story_taps}",
                rgb=rgb,
                screen=screen,
                observations=observations,
            )
            if self._is_waiting_surface(screen):
                return finish(
                    "stopped_unknown_screen",
                    self._unresolved_wait_reason(screen),
                )
        if screen.name == "story-dialogue":
            return finish(
                "story_limit_reached",
                "story remained active at the "
                f"{STORY_ADVANCE_FLOW_BUDGET}-tap safety limit",
            )

        # F4-12 Phase 0, canonical ruling 3: the allowances and counters below
        # live in popup_handler (FLOW_POPUP_RULES / FlowPopupBudget); the flow
        # only executes them.
        popup_budget = FlowPopupBudget()
        forbidden_closes: dict[str, int] = {}
        while popup_budget.session_open():
            newbie_gift_action = False
            rgb, screen = self._wait_for_action_surface(
                actuator,
                run,
                step="post_task",
                rgb=rgb,
                screen=screen,
                observations=observations,
            )
            if self._is_waiting_surface(screen):
                return finish(
                    "stopped_unknown_screen",
                    self._unresolved_wait_reason(screen),
                )
            if screen.name == "story-cutscene":
                (
                    rgb,
                    screen,
                    cutscene_skips_used,
                    cutscene_failure,
                ) = self._resolve_story_cutscene(
                    actuator,
                    run,
                    rgb=rgb,
                    screen=screen,
                    observations=observations,
                    action_results=action_results,
                    skips_used=cutscene_skips_used,
                )
                if cutscene_failure is not None:
                    status, reason = cutscene_failure
                    return finish(status, reason)
                continue
            if screen.name == "story-dialogue":
                if story_taps >= self.story_tap_limit:
                    return finish(
                        "story_limit_reached",
                        "story remained active at the "
                        f"{STORY_ADVANCE_FLOW_BUDGET}-tap safety limit",
                    )
                advance = story_advance_action(screen, self.registry)
                action = actuator.act(
                    advance.request.model_copy(
                        update={"intent": f"main-task:story:{story_taps + 1}"}
                    )
                )
                action_results.append(action)
                story_taps += 1
                if action.error:
                    return finish("action_failed", "story advance action failed")
                self.sleep(self.transition_wait_s)
                rgb, screen, observed = self._capture(
                    actuator,
                    run,
                    f"post_story:{story_taps}",
                )
                observations.append(observed)
                continue
            if screen.name == "main-board":
                returned_to_board = True
                break
            budget_stop = popup_budget.stop_reason(screen.name)
            if budget_stop is not None:
                return finish("stopped_unknown_screen", budget_stop)
            if screen.name == "restaurant-scene":
                action = self._tap_registered_point(
                    actuator,
                    "restaurant-scene",
                    "return_to_board",
                    intent="main-task:return-to-board",
                )
            else:
                attempt = popup_budget.attempt(screen.name)
                forbidden = self.registry.detect_forbidden_surface(
                    rgb,
                    screen=screen,
                )
                if forbidden is not None:
                    # A red-line surface may only be closed, and only once.
                    close_action = action_for_screen(screen, self.registry)
                    if (
                        close_action is None
                        or close_action.kind != "close"
                        or (
                            forbidden.close_element is not None
                            and close_action.element != forbidden.close_element
                        )
                        or forbidden_closes.get(forbidden.name, 0) >= 1
                    ):
                        return finish(
                            "stopped_forbidden_surface",
                            (
                                "forbidden surface "
                                f"{forbidden.name} "
                                f"({', '.join(forbidden.reasons)}) "
                                "could not be closed with its registered control"
                            ),
                        )
                    forbidden_closes[forbidden.name] = 1
                popup = handle_known_popups(
                    rgb,
                    self.registry,
                    attempt=attempt,
                )
                if popup is None:
                    return finish(
                        "stopped_unknown_screen",
                        f"unregistered surface observed: {screen.name}",
                    )
                if popup_budget.degrades_to_close(popup):
                    popup = action_for_screen(
                        screen.model_copy(update={"matched_anchors": []}),
                        self.registry,
                    )
                    if popup is None:
                        return finish(
                            "stopped_unknown_screen",
                            "shop close action was unavailable after free claim",
                        )
                action = actuator.act(popup.request)
                popup_budget.record(
                    observed=screen.name,
                    popup=popup,
                    attempt=attempt,
                )
                newbie_gift_action = popup.screen == "newbie-gift"
                popup_records.append(
                    PopupHandlingRecordV1(
                        screen=popup.screen,
                        kind=popup.kind,
                        element=popup.element,
                        action_id=action.action_id,
                    )
                )
            action_results.append(action)
            if action.error:
                return finish("action_failed", "registered navigation action failed")
            self.sleep(0.3 if newbie_gift_action else self.transition_wait_s)
            rgb, screen, observed = self._capture(
                actuator,
                run,
                f"post_task:{len(observations)}",
            )
            observations.append(observed)
        if not returned_to_board:
            return finish(
                "stopped_unknown_screen",
                "main-task route did not return to the board within the popup bound",
            )

        after_result = save_channel.read_state(
            flush="home_and_return",
            include_board=True,
        )
        after = _state_summary(after_result.state, self.config_dir)
        task_advanced = (
            after.main_task_id is not None
            and after.main_task_id != before.main_task_id
        )
        expected_coins = before.coins - before.main_task_cost
        coins_match = after.coins == expected_coins
        if not task_advanced or not coins_match:
            return finish(
                "verification_failed",
                (
                    "expected task advance and exact completion-time cost debit "
                    f"(coins={expected_coins}); "
                    f"observed task={after.main_task_id!r}, coins={after.coins!r}"
                ),
                after_result=after_result,
            )
        return finish(
            "completed",
            (
                f"main task advanced {before.main_task_id}->{after.main_task_id}; "
                f"coins {before.coins}->{after.coins}"
            ),
            after_result=after_result,
        )


__all__ = [
    "CLAIM_SAVE_EVIDENCE_FIELDS",
    "CLAIM_SAVE_EVIDENCE_KINDS",
    "ClaimEvidenceWindow",
    "MainTaskFlow",
    "MainTaskFlowStatus",
    "MainTaskResultV1",
    "PopupHandlingRecordV1",
    "PopupSaveDeltaV1",
    "build_claim_save_delta",
    "SaveStateSummaryV1",
    "ScreenObservationV1",
]
