# [OMNI] origin=codex created_by=codex intent="close the deterministic Romantic Restaurant play loop"
"""Authoritative save-to-plan-to-action loop with replay adjudication."""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from .behavior_frame import (
    BehaviorClocks,
    BehaviorCounters,
    BehaviorFrame,
    BehaviorPolicy,
    BehaviorSignals,
)
from .contracts import ActionRequestV1, ActionResultV1
from .gem_economy import SHOP_SURFACE, GemOffer, choose_offer, free_shop_offers, price_offers
from .exhaustion import (
    APP_RELAUNCH_LIMIT,
    APP_RELAUNCH_WINDOW_S,
    FORBIDDEN_RECURRENCE_LIMIT,
    FORBIDDEN_RECURRENCE_WINDOW_S,
    ExhaustionDetectorV1,
    ExhaustionReportV0,
)
from .main_task_flow import (
    CLAIM_SAVE_EVIDENCE_FIELDS,
    CLAIM_SAVE_EVIDENCE_KINDS,
    MainTaskFlow,
    MainTaskResultV1,
    PopupHandlingRecordV1,
    PopupSaveDeltaV1,
    build_claim_save_delta,
)
from .navigation import (
    ForbiddenSurfaceV1,
    ScreenIdV1,
    SurfaceRegistry,
    frame_to_rgb,
)
from .order_geometry import DISPLAY_POSITIONS as ORDER_DISPLAY_POSITIONS
from .planner import (
    ChestRuntimeStateV1,
    PlanV1,
    PlannedActionV1,
    PolicyV1,
    SUPPORTED_FREE_COLLECT_REWARD_TYPES,
    PredictionV1,
    VisualCellObservationV1,
    VisualOccupancyCoverageV1,
    _chain_distance,
    is_free_chest_type,
    _chest_states,
    _configured_item_metadata,
    _delivery_request,
    _display_slot_order,
    _drag_request,
    _first_cache_item,
    _outstanding_required_counts,
    _request_status,
    _required_cells,
    _sell_control_request,
    _tap_position_request,
    _tap_request,
    plan_batch,
)
from .popup_handler import (
    STORY_ADVANCE_GUARD_BUDGET,
    STORY_DIALOGUE_SURFACE,
    action_for_screen,
    guard_departure_action,
)
from .play_control import ControlStateError, read_control_state
from .save_channel import SaveChannelError
from .save_reader import SaveReaderError
from .semantics import (
    BOARD_COLUMNS,
    BOARD_ROWS,
    ENERGY_RECOVERY_SECONDS,
    ORDER_BOOST_ACTIVITY,
    CellPositionV1,
    GameStateSemanticV1,
    _segment,
    derive_game_state_semantics,
)


AdjudicationStatus = Literal["confirmed", "confirmed_with_deviation", "refuted"]
# F4-11 section 1.  What a segment does before its first business input.
#
# The asymmetry the arms exist to test: the post-foreground settle is applied
# only on the round that reuses the previous read.  A round that takes its own
# read -- which every segment does on round one, and every round does after a
# read failure -- comes out of the flush, which backgrounds and re-foregrounds
# the app, and goes straight to tapping.  Cold-start first rounds refuse
# generations at 29.55% against 6.14% for later rounds (F6-05 section 1.4).
#
#   status_quo  the shape above, unchanged
#   warm_tap    one selection tap on a plain non-generator item first, then
#               the business inputs (F4-05 probe method: selection costs
#               nothing and produces nothing)
#   settle      hold the same post-foreground settle the reuse path gets, at
#               cold_start_settle_ms, before the first business input
ColdStartPolicy = Literal["status_quo", "warm_tap", "settle"]
COLD_START_POLICIES = frozenset({"status_quo", "warm_tap", "settle"})
BackendName = Literal["mumu_ipc", "maa_adb"]
LoopStopReason = Literal[
    "max_rounds",
    "unknown_screen",
    # F4-09 1.5.  The authoritative save could not be read.  Kept apart from
    # unknown_screen because the two ask for opposite things: an unknown screen
    # needs a surface registered, a channel failure needs the device or the
    # host looked at.  Reporting one as the other stopped an 8 hour run and
    # sent an operator hunting for a surface while the frame beside the alert
    # showed an ordinary board.
    "save_channel_failed",
    # F4-10.  The app went away and was relaunched too many times inside one
    # hour to keep calling it a transient.  A single death is a recoverable
    # device event and the loop relaunches through it; this is the point at
    # which the build or the emulator is what needs looking at.
    "app_crash_recurring",
    "input_dead_unrecovered",
    "not_on_board",
    # F4-13.  A registered surface still marked ``unexplored`` in the
    # navigation registry.  Only reachable when the loop was armed with the
    # agent handler; the guard leaves the surface untouched so the handler's
    # exploration segment sees it exactly as the run found it.
    "unexplored_surface",
    "refuted_streak",
    "exhausted_v0",
    "resource_wait",
    "max_wait_s",
    "main_task_completed",
    "main_task_stopped",
    # 2026-09-11 ruling after the 18:22Z main-task drive.  The flow found the
    # task notebook hidden by the event HUD -- the main line is unavailable
    # by game mechanism for the event's duration.  The segment ends on this
    # reason and the driver ends the shift cleanly; it is neither progress
    # lost nor a facility fault.
    "main_task_unavailable",
    "target_level_reached",
    "control_handed_over",
    "control_state_invalid",
    "shop_entry_stalled",
    "forbidden_surface",
    # F4-09: the same red-line surface kept returning and being closed.  See
    # FORBIDDEN_RECURRENCE_LIMIT.
    "forbidden_surface_recurring",
    # F4-09: a registered guide was tapped repeatedly and the screen did not
    # move.  Following it harder cannot help; a human has to look.
    "tutorial_follow_stalled",
    "waiting_for_energy",
]
INTER_GENERATOR_ACTION_DELAY_S = 1.0
MAX_SCREEN_GUARD_STEPS = 8
MAX_SCREEN_GUARD_NAVIGATION_HOPS = 3
MAX_TUTORIAL_SEQUENCE_STEPS = 5
# F4-11, planner ruling of 2026-09-07.  A story dialogue is not a guide the run
# is following: advancing it is play, and a dialogue can be many bubbles long,
# so it gets its own budget instead of the five-step tutorial one.  Twenty per
# dialogue, counted across rounds and reset the moment the surface changes; the
# three-unchanged-frames stall test applies to it unchanged.  F4-12 Phase 0
# ruling 1: the constant itself lives in popup_handler next to the one advance
# implementation, and the flow's per-session budget sits beside it.
MAX_STORY_ADVANCE_TAPS = STORY_ADVANCE_GUARD_BUDGET
# 2026-09-08.  Registered panels the guard has a wired departure action for.
# When a segment opens on one of them (the 13:35Z/13:40Z main-task-panel
# stops) or a round's step budget runs out while one is still up (the 13:40Z
# stop on decoration-confirmation, after seven story advances and the
# selection tap spent all eight guard steps), the next round goes back to the
# guard instead of stopping not_on_board -- the same shape as the
# story-dialogue continuation above.  Each surface gets at most
# MAX_GUARD_HANDOFF_ROUNDS extra rounds; reaching the cap with the panel
# still up stops the run for a ruling exactly as before.
# 2026-09-09: restaurant-scene joins.  The 20:40Z cold start spent six of
# its eight guard steps advancing the opening story dialogue; the one
# return-to-board hop it then fired did land, but a board-story-prompt and
# the level-up reward underneath it intercepted the transition and the last
# guard step went to claiming the reward, so the hop never settled and the
# loop stopped not_on_board at zero rounds -- on a surface SETTLED_SURFACES
# already declares carries a verified route home.  The next round goes back
# to the guard, which retries the (980, 1860) hop against a now-clear scene.
# 2026-09-17: newbie-gift joins.  Main task 10_2's 07:07Z guard pass spent its
# eight steps on the cutscene wait, two skips, two return hops and the level-up
# claim, leaving two of the gift's registered open-gift taps (it opens after
# about five), and the run stopped not_on_board on a free-claim surface twice
# in a row -- enough for the supervisor to hold the whole line.  The gift's own
# twelve-tap limit stays the flow's; here the handoff cap bounds it.
GUARD_HANDOFF_SURFACES = frozenset(
    {
        "decoration-choice",
        "decoration-confirmation",
        "main-task-panel",
        "newbie-gift",
        "restaurant-scene",
    }
)
MAX_GUARD_HANDOFF_ROUNDS = 2
# Registered guided-tutorial groups, in detection order.  Each entry names a
# `board_actions` key holding `steps`, and the stable intent segment used for
# that group so already-recorded intents never change.
BOARD_TUTORIAL_GROUPS: tuple[tuple[str, str], ...] = (
    ("cobweb_unlock_tutorial", "cobweb-unlock-coffee-machine"),
    ("rave_reviews_event_tutorial", "rave-reviews-event-badge"),
    ("bubble_item_tutorial", "bubble-wrapped-item"),
)
SCREEN_GUARD_POLL_S = 0.2
SCREEN_GUARD_TIMEOUT_S = 8.0
# 2026-09-09, planner ruling after the two real input-death incidents (12:29Z
# MuMu IPC single-channel death, 18:39Z whole-process wedge).  An automatic
# force-stop is only offered to the guardian when no business action from this
# many seconds before the stop is still without its adjudication.
INPUT_DEAD_IN_FLIGHT_WINDOW_S = 60.0
# F4-09 1.6.  How many rounds a refused move is held out of planning.  The
# 2026-09-06 20:53Z segment re-predicted one merge on three consecutive rounds
# against a board that never changed, and spent the whole segment on it; three
# rounds is long enough for the board to become a different board and short
# enough that a genuinely available move is not shelved for a segment.
REFUSED_MOVE_HOLD_ROUNDS = 3
# F4-09.  How many times the guard may tap one guided target and get a
# byte-identical frame back before it stops following that guide.  Three,
# because the 2026-09-07 00:53Z deadlock tapped the registered target five
# times against frames that never changed by a byte, and two identical frames
# can still be an animation that has not started.
GUIDED_TARGET_STALL_TAPS = 3
# F4-10, planner ruling A of 2026-09-07.  One tap, not three, for the left-rail
# slot.  Three is calibrated for a guide that might be mid-animation; this slot
# is not that.  It holds whatever item is queued -- a locked chest on
# 2026-09-07 01:15Z, coffee beans on 2026-09-06 01:15Z, a coffee cup on
# 2026-09-07 03:58Z -- and when its tap does nothing it does nothing on the
# first try as well as the third.  The 03:58Z stall spent five taps and ended
# an eight-hour run seven hours early.
LEFT_RAIL_GUIDED_TARGET_STALL_TAPS = 1
# The stall keys that budget applies to: screen, element and point, exactly as
# `_guided_target_stalls` spells them.
LEFT_RAIL_STALL_KEYS = frozenset({"tutorial-overlay:guided-target:100,380"})
TUTORIAL_OVERLAY_SURFACE = "tutorial-overlay"


def guided_target_stall_budget(stall_key: str) -> int:
    """How many unchanged taps this guide gets before the run stops following."""

    if stall_key in LEFT_RAIL_STALL_KEYS:
        return LEFT_RAIL_GUIDED_TARGET_STALL_TAPS
    return GUIDED_TARGET_STALL_TAPS
# F4-09.  How many free cells the run frees before it taps the reward chest
# again.  MainTaskConfig lists only experience in its rewards column, so the
# number of items the chest drops is not in the configuration and the badge is
# the only statement of it; the planner ruling fixes the fallback at five,
# which is what the 2026-09-07 00:53Z badge read.
BOARD_FULL_UNDER_GUIDE_DROP_CELLS = 5
# The only actions allowed while the board is full under an unfollowable
# guide.  Merging and selling are the two that free a cell; generating fills
# one, delivering and panel taps are business the run has no evidence it can
# perform in this state.
BOARD_FULL_UNDER_GUIDE_INTENTS = (
    "merge:",
    "clear-cobweb:merge:",
    "sell:",
    "select-sell-item:",
)
# F4-10, planner ruling of 2026-09-07.  What the run may do on a frame that
# reads as a tutorial overlay but is showing a deliverable order.
DELIVER_UNDER_GUIDE_INTENTS = ("deliver:",)
# F4-10, planner ruling of 2026-09-07.  A verdict that stops the run or sends
# it following a guide has to hold across two frames at least half a second
# apart before it counts.  The 05:26Z run stopped because one frame was
# sampled in the middle of a panel fading out and the blend read as a story
# dialogue; the scene underneath it had a verified way back to the board.
SURFACE_DEBOUNCE_POLL_S = 0.2
SURFACE_DEBOUNCE_HOLD_S = 0.5
SURFACE_DEBOUNCE_TIMEOUT_S = 3.0
# Verdicts that need no confirming: the board is where play happens, and the
# restaurant scene carries a verified route back to it, so neither one stops
# the run or starts a follow.
SETTLED_SURFACES = frozenset({"main-board", "restaurant-scene"})
BUSINESS_PREDICTION_KINDS = frozenset(
    {
        "merge",
        "generate",
        "deliver",
        "start_main_task",
        "open_chest",
        "sell",
        "collect_energy",
        "collect_reward",
        "claim_reward_queue",
    }
)
# F4-11 section 3.  Predictions whose outcome can put energy back in the purse:
# a delivery pays energy while the energy-party activity runs, and collecting a
# board piece or opening a chest can pay energy at any time.
ENERGY_INCOME_PREDICTION_KINDS = frozenset({"deliver", "collect_energy", "open_chest"})
# One background recovery tick can land inside a round and hide the point a
# generation spent; the collect_energy verdict already tolerates the same tick.
ENERGY_RECOVERY_TICK_TOLERANCE = 1
# EnergyModel.DefaultRecoveryDuration: one point every 120 s.  A round is a few
# seconds, so at most one background tick can land inside one collection.
# F4-12 Phase 0 ruling 5: the constant itself is defined once, in
# semantics.py, and imported at the top of this module.
VISUAL_EMPTY_CROP_SIZE_PX = 40
VISUAL_EMPTY_REFERENCE_RGB = (143, 184, 241)
VISUAL_EMPTY_MAX_CHANNEL_DELTA = 16
VISUAL_EMPTY_MIN_BACKGROUND_FRACTION = 0.95
DELIVERY_CONTROL_Y = 355


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StopConditionsV1(_FrozenModel):
    max_wait_s: int | None = Field(default=None, ge=0)
    max_consecutive_refuted: int = Field(default=3, ge=1)
    stop_on_exhaustion: bool = True
    stop_on_resource_wait: bool = False


class StateSummaryV1(_FrozenModel):
    energy: int | None
    coins: int | None
    level: int | None
    # F4-08 section 3.2: the progress curve is read back off round summaries,
    # so experience and the energy pieces lying on the board have to survive
    # into the report the same way energy and coins already do.
    xp: int | None = None
    gems: int | None = None
    energy_generate_time: int | None = None
    cache_item_count: int | None = Field(default=None, ge=0)
    board_energy_items: int = Field(default=0, ge=0)
    mergeable_pairs: int = Field(ge=0)
    empty_cells: int = Field(ge=0)
    board_matrix_7x9: list[list[str | None]]
    orders: list[dict[str, Any]] = Field(default_factory=list)
    main_task: dict[str, Any] | None = None
    generator_runtime: list[dict[str, Any]] = Field(default_factory=list)
    # 2026-09-16.  The summary above only projects cells whose kind is
    # "generator", so a board full of chests came back looking empty of them
    # and "why did nothing open a chest" could not be answered from a report at
    # all.  Chests carry the same spread runtime; give them their own list.
    chest_runtime: list[dict[str, Any]] = Field(default_factory=list)
    # F4-11 section 3, planner ruling of 2026-09-07.  The energy-party activity
    # row, five scalars: it scores produced items rather than deliveries, pays
    # its tier reward automatically, and resets at UTC midnight, so the only
    # way to see it happen is to watch these numbers across rounds.
    activity_trigger_plane: dict[str, Any] | None = None
    # 2026-09-21.  宝藏乱斗 (PK100) pays nothing unless you sign up first and
    # then finish seven timed orders, and its sign-up window is the only way
    # in.  The account has carried SignUpWindowPop: 0 for days and the event
    # has no entry on either the board or the restaurant scene, so there is
    # nothing to join yet -- but the moment that number moves, a segment
    # report will say so instead of the window opening and closing unseen.
    activity_pk100: dict[str, Any] | None = None


class GenerateDeviationV1(_FrozenModel):
    """What a generation that produced something unpredicted actually did.

    F4-11 section 2.  The strict verdict asks for an item of a predicted type in
    a cell the plan expected to be free.  F6-05 counted twelve refused single
    generates whose save deltas said the generator had in fact produced: the
    inventory dropped and the board gained an item, only not the one the planner
    named.  That is a scoring miss, not a device miss, so it gets its own status
    and this record of what really landed.
    """

    generator_cell: CellPositionV1 | None
    inventory_before: int | None
    inventory_after: int | None
    # Every cell the plan expected to stay free that now holds something, and
    # the types in them.  The relaxation cannot say which landing belongs to
    # which generation, so it records the whole evidence set rather than
    # guessing an assignment.
    landed_cells: list[CellPositionV1]
    landed_item_types: list[str]
    # True when one of those landings is exactly what this prediction asked
    # for.  Then the only thing that was off was the energy arithmetic -- a
    # report written before the recovery clock was recorded cannot show the
    # tick that paid the round back, and the strict verdict caps confirmations
    # by visible spend.
    matched_prediction: bool
    energy_delta: int | None
    energy_check: str


class PredictionAdjudicationV1(_FrozenModel):
    prediction_id: str
    kind: str
    status: AdjudicationStatus
    reason: str
    counts_toward_refuted_streak: bool = True
    deviation: GenerateDeviationV1 | None = None


class ExecutedActionV1(_FrozenModel):
    action: PlannedActionV1
    result: ActionResultV1


class RoundTimingV1(_FrozenModel):
    screenshot_ms: float = Field(ge=0)
    read_before_s: float = Field(ge=0)
    plan_ms: float = Field(ge=0)
    act_ms: float = Field(ge=0)
    read_after_s: float = Field(ge=0)
    adjudicate_ms: float = Field(ge=0)
    total_s: float = Field(ge=0)
    read_reused: bool = False
    foreground_settle_ms: float = Field(default=0.0, ge=0)


class RoundRecordV1(_FrozenModel):
    round: int = Field(ge=1)
    screenshot_frame_id: str
    screenshot_sha256: str
    screenshot_changed_from_previous: bool | None
    state_before: StateSummaryV1
    plan: PlanV1
    executed: list[ExecutedActionV1]
    adjudication: list[PredictionAdjudicationV1]
    state_after: StateSummaryV1
    timing: RoundTimingV1
    exhaustion: ExhaustionReportV0
    main_task_results: list[MainTaskResultV1] = Field(default_factory=list)
    screen_guard_actions: list[PopupHandlingRecordV1] = Field(default_factory=list)
    screen_guard_action_results: list[ActionResultV1] = Field(default_factory=list)
    # F4-11 section 1.  What the cold-start arm did before this round acted,
    # for the rounds that took their own read.  None on every reused round and
    # under the status_quo arm, which is what every run does by default.
    cold_start: dict[str, Any] | None = None
    # F4-12 Phase 0.  The round's explicit behavior frame (save summary,
    # adjudicated surface, counters, clocks), serialized by
    # behavior_frame.BehaviorFrame.to_record.  Purely additive: the judging
    # logic still reads the fields above until Phase 1.
    behavior_frame: dict[str, Any] | None = None


class LoopReportV1(_FrozenModel):
    started_at: str
    ended_at: str
    stop_reason: LoopStopReason
    rounds: list[RoundRecordV1]
    confirmed_predictions: int = Field(ge=0)
    refuted_predictions: int = Field(ge=0)
    # F4-11 section 2.  Predictions the device carried out in a shape the plan
    # did not name.  Counted apart from both: they are not refutations, and
    # folding them into confirmations would hide how often the planner is wrong
    # about where a generated item lands.
    deviation_predictions: int = Field(default=0, ge=0)
    deviation_rate: float = Field(default=0.0, ge=0, le=1)
    refuted_rate: float = Field(ge=0, le=1)
    game_actions: int = Field(ge=0)
    wall_seconds: float = Field(ge=0)
    effective_apm: float = Field(ge=0)
    exhaustion: ExhaustionReportV0
    input_restart_recoveries: int = Field(default=0, ge=0)
    cold_start_policy: str = "status_quo"
    cold_start_warmups: int = Field(default=0, ge=0)
    # F4-11 section 4.  How many rounds were voided because the game process
    # had died and the loop brought it back.  It was counted on the loop from
    # F4-10 on but never left the process, so the daily report could not show
    # it next to the save-channel failures it tends to arrive with.
    app_relaunch_recoveries: int = Field(default=0, ge=0)
    # 2026-09-09, planner ruling after the two real input-death incidents.  On
    # an input_dead_unrecovered stop this carries the evidence the guardian's
    # automatic recovery dispatches on: the last adjudicated identification,
    # how many business actions from the last sixty seconds never reached an
    # adjudication, and the actuator's adb contrast tap.  None on every other
    # stop.
    input_dead_context: dict[str, Any] | None = None
    # F4-13.  On an unexplored_surface stop, the registered surface the guard
    # left standing for the agent handler.  None on every other stop.
    unexplored_surface: str | None = None
    target_level: int | None = Field(default=None, ge=1)


class _SemanticGeometryAdapter:
    """Translate F1-05's one-based semantic positions to F2-03's zero-based grid."""

    def __init__(self, geometry: Any) -> None:
        self.geometry = geometry

    def cell_center(self, row: int, col: int) -> tuple[int, int]:
        if row < 1 or col < 1:
            raise IndexError("semantic row and col must be one-based positive integers")
        return self.geometry.cell_center(row - 1, col - 1)


def _cell(state: GameStateSemanticV1, position: CellPositionV1 | None):
    if position is None:
        return None
    return state.board.cells[position.row - 1][position.col - 1]


TRIGGER_PLANE_FIELDS = (
    "energyConsumed",
    "score",
    "level",
    "dailyStatus",
    "isMaxOut",
)


def _trigger_plane(state: GameStateSemanticV1) -> dict[str, Any] | None:
    row = (state.activity or {}).get("triggerPlane")
    if not isinstance(row, dict):
        return None
    return {key: row.get(key) for key in TRIGGER_PLANE_FIELDS if key in row}


PK100_FIELDS = ("SignUpWindowPop", "FinishOrder")


def _pk100(state: GameStateSemanticV1) -> dict[str, Any] | None:
    """宝藏乱斗's two scalars: whether the sign-up window is offered, and progress."""

    row = (state.activity or {}).get("PK100")
    if not isinstance(row, dict):
        return None
    return {key: row.get(key) for key in PK100_FIELDS if key in row}


# How long a refused chest stays held.  2026-09-16: the hold was released only
# when the chest's save evidence changed, and a held chest is never tapped, so
# its evidence could not change -- six chests sat closed with full stock for
# hours behind a hold laid down by a bug that has since been fixed (a timer
# start was sent as one tap when the game needs two).  A hold that cannot
# expire turns any one-off misjudgement into a permanent one.  Chest timers run
# 480-1200 s, so a quarter hour is long enough to stop tap-spam and short
# enough to heal itself.
CHEST_HOLD_TTL_S = 15 * 60

# 2026-09-17, by the account owner: 允许每天购买2次体力（点击体力右边的加号）.
# The panel is still a red-line surface for every other path; the purchase is
# the one registered door (safety.forbidden_surfaces.energy-supply-panel.
# authorized_purchase), which carries the daily limit and the gem price.  Buying
# is only worth it once the bar is nearly empty: energy above the cap does not
# regenerate, so topping up a half-full bar pauses the free recovery clock.
ENERGY_SUPPLY_SURFACE = "energy-supply-panel"
# How long a purchase waits for the panel its entry tap opens.
PANEL_OPEN_TIMEOUT_S = 4.0
ENERGY_PURCHASE_TRIGGER_ENERGY = 5
# A shop visit that claims nothing is not retried for this long.
SHOP_VISIT_COOLDOWN_S = 10 * 60
# A paid shop purchase the save did not show is held off this long; one that
# took more gems than priced stops shop purchases for the rest of the UTC day.
SHOP_NOT_BOUGHT_HOLD_S = 60 * 60


def _summary(state: GameStateSemanticV1) -> StateSummaryV1:
    return StateSummaryV1(
        activity_trigger_plane=_trigger_plane(state),
        activity_pk100=_pk100(state),
        energy=state.resources.energy,
        coins=state.resources.coins,
        level=state.resources.level,
        xp=state.resources.xp,
        gems=state.resources.gems,
        energy_generate_time=state.resources.energy_generate_time,
        cache_item_count=(
            None if state.cache_items is None else len(state.cache_items)
        ),
        board_energy_items=len(state.board.energy_items),
        mergeable_pairs=len(state.board.mergeable_pairs),
        empty_cells=len(state.board.empty_cells),
        board_matrix_7x9=[[cell.raw_code for cell in row] for row in state.board.cells],
        orders=[order.model_dump(mode="json") for order in state.orders],
        main_task=(None if state.main_task is None else state.main_task.model_dump(mode="json")),
        generator_runtime=[
            {
                "row": cell.row,
                "col": cell.col,
                "item_type": cell.item_type,
                "spreadItemRestNumber": cell.generator.item_rest,
                "spreadStorageRestNumber": cell.generator.storage_rest,
                "spreadState": cell.generator.spread_state,
                "spreadStartTimer": cell.generator.recovery_started_at,
                "inventory_exhausted": cell.generator.inventory_exhausted,
                "recovery_wait_seconds": cell.generator.recovery_wait_seconds,
            }
            for row in state.board.cells
            for cell in row
            if cell.kind == "generator" and cell.generator is not None
        ],
        chest_runtime=[
            {
                "row": cell.row,
                "col": cell.col,
                "item_type": cell.item_type,
                "spreadItemRestNumber": cell.generator.item_rest,
                "spreadStorageRestNumber": cell.generator.storage_rest,
                "spreadState": cell.generator.spread_state,
                "spreadStateName": cell.generator.spread_state_name,
                "spreadStartTimer": cell.generator.recovery_started_at,
                "cooldownRemainingSeconds": cell.generator.cooldown_remaining_s,
            }
            for row in state.board.cells
            for cell in row
            if cell.kind == "chest" and cell.generator is not None
        ],
    )


def _cell_item_epoch_ms(cell: Any) -> int | None:
    """The creation stamp the save writes on a board item, in milliseconds.

    Every item the account creates carries an ``item_id`` that is an epoch in
    milliseconds; the pre-tutorial rows kept small integers and are never
    recent by this test.
    """

    extra = getattr(cell, "extra", None)
    if not isinstance(extra, Mapping):
        return None
    raw = extra.get("item_id")
    text = "" if raw is None else str(raw)
    if not text.isdigit() or len(text) < 12:
        return None
    return int(text)


def just_landed_positions(
    raw_state: Any,
    since_epoch_ms: int | None,
) -> frozenset[tuple[int, int]]:
    """One-based positions of board items created since the previous read.

    F4-09 1.6.  A piece that has only just landed is still animating, and every
    refuted merge of the 2026-09-06 20:53Z segment involved one.  Holding it for
    a round costs a round; merging into an animation costs a segment.
    """

    if since_epoch_ms is None:
        return frozenset()
    board = getattr(raw_state, "board", None)
    if not isinstance(board, list):
        return frozenset()
    landed: set[tuple[int, int]] = set()
    for row_index, row in enumerate(board, start=1):
        if not isinstance(row, list):
            continue
        for col_index, cell in enumerate(row, start=1):
            stamp = _cell_item_epoch_ms(cell)
            if stamp is not None and stamp > since_epoch_ms:
                landed.add((row_index, col_index))
    return frozenset(landed)


def latest_item_epoch_ms(raw_state: Any) -> int | None:
    """The newest board item creation stamp this read carries."""

    board = getattr(raw_state, "board", None)
    if not isinstance(board, list):
        return None
    stamps = [
        stamp
        for row in board
        if isinstance(row, list)
        for stamp in (_cell_item_epoch_ms(cell) for cell in row)
        if stamp is not None
    ]
    return max(stamps) if stamps else None


def _epoch_to_iso(epoch: int | None) -> str:
    """Render an epoch second as UTC ISO-8601, or "unknown" when absent."""

    if epoch is None:
        return "unknown"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _board_is_valid(state: GameStateSemanticV1) -> bool:
    return len(state.board.cells) == 9 and all(len(row) == 7 for row in state.board.cells)


def _runtime_chest_states(
    raw_state: Any,
    semantic: GameStateSemanticV1,
    config_dir: Path,
) -> tuple[ChestRuntimeStateV1, ...]:
    board = (
        raw_state.get("board")
        if isinstance(raw_state, Mapping)
        else getattr(raw_state, "board", None)
    )
    if not isinstance(board, list):
        return ()

    result: list[ChestRuntimeStateV1] = []
    for row in semantic.board.cells:
        for cell in row:
            if not is_free_chest_type(cell.item_type, config_dir):
                continue
            try:
                raw_cell = board[cell.row - 1][cell.col - 1]
            except (IndexError, TypeError):
                continue
            extra = (
                raw_cell.get("extra", {})
                if isinstance(raw_cell, Mapping)
                else getattr(raw_cell, "extra", {})
            )
            if not isinstance(extra, Mapping):
                extra = {}

            def optional_int(name: str) -> int | None:
                value = extra.get(name)
                if value is None or isinstance(value, bool):
                    return None
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None

            pairs = extra.get("spreadCodeWeightPairs")
            possible_types = [
                value.rpartition("-")[0]
                for value in str(pairs or "").split(";")
                if value.rpartition("-")[0]
            ]
            result.append(
                ChestRuntimeStateV1(
                    row=cell.row,
                    col=cell.col,
                    item_type=cell.item_type,
                    spread_state=optional_int("spreadState"),
                    item_rest=optional_int("spreadItemRestNumber"),
                    storage_rest=optional_int("spreadStorageRestNumber"),
                    possible_types=possible_types,
                )
            )
    return tuple(result)


def _matches_registered_signature(rgb: np.ndarray, signature: Any) -> bool:
    if not isinstance(signature, Mapping):
        return False
    anchors = signature.get("anchors", {})
    if not isinstance(anchors, Mapping):
        return False
    crop_size = int(signature.get("anchor_size", 40))
    threshold = float(signature.get("rgb_distance_threshold", 25.0))
    minimum = int(signature.get("minimum_matching_anchors", 3))
    half = crop_size // 2
    matches = 0
    for anchor in anchors.values():
        if not isinstance(anchor, Mapping):
            continue
        center = anchor.get("center")
        expected = anchor.get("average_rgb")
        if (
            not isinstance(center, list)
            or len(center) != 2
            or not isinstance(expected, list)
            or len(expected) != 3
        ):
            continue
        x, y = int(center[0]), int(center[1])
        crop = rgb[max(y - half, 0) : y + half, max(x - half, 0) : x + half]
        if crop.shape[:2] != (crop_size, crop_size):
            continue
        actual = crop.astype(np.float32).mean(axis=(0, 1))
        distance = np.linalg.norm(actual - np.asarray(expected, dtype=np.float32))
        if float(distance) <= threshold:
            matches += 1
    return matches >= minimum


def _registered_board_tutorial_request(
    frame_rgb: Any,
    registry: SurfaceRegistry,
    screen_name: str | None = None,
) -> ActionRequestV1 | None:
    rgb = frame_to_rgb(frame_rgb)
    board_actions = registry.payload.get("board_actions", {})
    if not isinstance(board_actions, Mapping):
        return None
    for config_key, group in BOARD_TUTORIAL_GROUPS:
        config = board_actions.get(config_key, {})
        steps = config.get("steps", []) if isinstance(config, Mapping) else []
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, Mapping):
                continue
            target = step.get("guided_target")
            name = step.get("name")
            # 2026-09-17: a step whose signature sits on a permanent board
            # element names the surface it belongs to.  Unknown screen fails
            # closed: the step is simply not offered.
            required = step.get("requires_screen")
            if isinstance(required, list) and screen_name not in required:
                continue
            if (
                _matches_registered_signature(rgb, step.get("signature"))
                and isinstance(target, list)
                and len(target) == 2
                and isinstance(name, str)
            ):
                return ActionRequestV1(
                    kind="tap",
                    intent=f"popup:tutorial-follow:{group}:{name}",
                    params={"x": int(target[0]), "y": int(target[1])},
                    settle="stable",
                )
    return None


def _registered_board_panel_request(
    frame_rgb: Any,
    registry: SurfaceRegistry,
) -> ActionRequestV1 | None:
    """Return the sole approved close action for a board item-detail overlay."""

    rgb = frame_to_rgb(frame_rgb)
    config = registry.payload.get("board_actions", {}).get(
        "coffee_machine_detail_panel",
        {},
    )
    if not isinstance(config, Mapping) or not _matches_registered_signature(
        rgb,
        config.get("signature"),
    ):
        return None
    close = config.get("close_control")
    if not isinstance(close, list) or len(close) != 2:
        return None
    return ActionRequestV1(
        kind="tap",
        intent="popup:close:coffee-machine-detail-panel:close-only",
        params={"x": int(close[0]), "y": int(close[1])},
        settle="stable",
    )


def _main_board_is_uncovered(
    frame_rgb: Any,
    registry: SurfaceRegistry,
) -> bool:
    """Reject a board positive when any central probe looks like a flat overlay."""

    config = registry.payload.get("board_actions", {}).get(
        "main_board_overlay_negative_guard",
        {},
    )
    if not isinstance(config, Mapping):
        return False
    points = config.get("sample_points")
    if not isinstance(points, list) or len(points) < 6:
        return False
    rgb = frame_to_rgb(frame_rgb)
    sample_size = int(config.get("sample_size", 40))
    half = sample_size // 2
    board_rgb = np.asarray(
        config.get("board_background_rgb", [143, 184, 241]),
        dtype=np.float32,
    )
    board_delta = float(config.get("board_background_max_channel_delta", 20.0))
    board_fraction_minimum = float(
        config.get("board_background_minimum_fraction", 0.15)
    )
    texture_minimum = float(
        config.get("item_texture_minimum_luminance_std", 8.0)
    )
    chroma_minimum = float(config.get("item_color_minimum_chroma_fraction", 0.22))
    value_range = config.get("item_color_value_range", [100.0, 245.0])
    if not isinstance(value_range, list) or len(value_range) != 2:
        return False
    minimum_value, maximum_value = float(value_range[0]), float(value_range[1])
    minimum_board_points = int(config.get("board_background_minimum_points", 0))
    bright_texture_minimum = float(
        config.get("item_bright_texture_minimum_luminance_std", float("inf"))
    )
    bright_chroma_minimum = float(
        config.get("item_bright_minimum_chroma_fraction", float("inf"))
    )
    board_points = 0
    for point in points:
        if not isinstance(point, list) or len(point) != 2:
            return False
        x, y = int(point[0]), int(point[1])
        crop = rgb[max(y - half, 0) : y + half, max(x - half, 0) : x + half]
        if crop.shape[:2] != (sample_size, sample_size):
            return False
        pixels = crop.astype(np.float32)
        board_fraction = float(
            np.mean(np.max(np.abs(pixels - board_rgb), axis=2) <= board_delta)
        )
        luminance = pixels.mean(axis=2)
        mean_rgb = pixels.mean(axis=(0, 1))
        value = float(mean_rgb.max())
        chroma = float(mean_rgb.max() - mean_rgb.min()) / max(value, 1.0)
        deviation = float(luminance.std())
        item_like = (
            deviation >= texture_minimum
            or (chroma >= chroma_minimum and minimum_value <= value <= maximum_value)
            # A merge flash is brighter than an item but still textured and
            # coloured; registered overlay panels are flat instead.
            or (
                deviation >= bright_texture_minimum
                and chroma >= bright_chroma_minimum
            )
        )
        if board_fraction >= board_fraction_minimum:
            board_points += 1
        elif not item_like:
            return False
    # Textured popup art passes the item-like test at every probe, so the flat
    # overlay test alone cleared the 2026-09-06 real-money offer frame.  Real
    # board frames always keep at least one probe on the blue background.
    return board_points >= minimum_board_points


def forbidden_surface_for_frame(
    frame_rgb: Any,
    registry: SurfaceRegistry,
    screen: ScreenIdV1 | None = None,
) -> ForbiddenSurfaceV1 | None:
    """Detect a registered red-line surface before any tap is issued."""

    return registry.detect_forbidden_surface(frame_rgb, screen=screen)


def _identify_guard_screen(
    frame_rgb: Any,
    registry: SurfaceRegistry,
) -> ScreenIdV1:
    forbidden = registry.detect_forbidden_surface(frame_rgb)
    if forbidden is not None:
        return ScreenIdV1(
            name=forbidden.name,
            confidence=forbidden.confidence,
            matched_anchors=[*forbidden.matched_anchors, "forbidden-surface-guard"],
        )
    screen = registry.identify_screen(frame_rgb)
    if screen.name == "tutorial-overlay" and not any(
        anchor.endswith("/guide-hand") for anchor in screen.matched_anchors
    ):
        if _main_board_is_uncovered(frame_rgb, registry):
            return ScreenIdV1(
                name="main-board",
                confidence=screen.confidence,
                matched_anchors=[
                    *screen.matched_anchors,
                    "tutorial-rejected-without-guide-hand",
                ],
            )
    if screen.name == "main-board" and not _main_board_is_uncovered(frame_rgb, registry):
        return ScreenIdV1(
            name="overlay-unknown",
            confidence=0.0,
            matched_anchors=[*screen.matched_anchors, "negative-overlay-guard"],
        )
    if screen.name == "main-board" and registry.detect_forbidden_surface(
        frame_rgb,
        screen=screen,
    ):
        return ScreenIdV1(
            name="overlay-unknown",
            confidence=0.0,
            matched_anchors=[*screen.matched_anchors, "forbidden-surface-guard"],
        )
    return screen


def detect_delivery_control_centers(frame_rgb: Any) -> list[int]:
    """The x centres of the green Complete buttons drawn on the order row.

    Extracted from ``_delivery_control_overrides`` so the offline order-row
    forensics measures the same pixels the live delivery path does, with no
    second implementation to drift.  A button appears only on an order the game
    considers fulfillable, so the count and position of these centres are the
    observable side of the display order.
    """

    rgb = frame_to_rgb(frame_rgb)
    band = rgb[300:410].astype(np.int16)
    red, green, blue = (band[:, :, index] for index in range(3))
    green_mask = (
        (green > 100)
        & (green * 100 > red * 125)
        & (green * 100 > blue * 115)
    )
    coverage = green_mask.mean(axis=0)
    active = np.flatnonzero(coverage > 0.2)
    if not active.size:
        return []
    narrow_segments: list[tuple[int, int]] = []
    start = previous = int(active[0])
    for raw_x in active[1:]:
        x = int(raw_x)
        if x > previous + 1:
            if previous - start + 1 >= 20:
                narrow_segments.append((start, previous + 1))
            start = x
        previous = x
    if previous - start + 1 >= 20:
        narrow_segments.append((start, previous + 1))

    grouped: list[tuple[int, int]] = []
    for start, stop in narrow_segments:
        if grouped and start - grouped[-1][1] <= 40:
            grouped[-1] = (grouped[-1][0], stop)
        else:
            grouped.append((start, stop))
    return [
        (start + stop - 1) // 2
        for start, stop in grouped
        if stop - start >= 90 and float(coverage[start:stop].max()) >= 0.45
    ]


def _delivery_control_overrides(
    state: GameStateSemanticV1,
    frame_rgb: Any,
) -> dict[int, tuple[int, int]]:
    """Locate visible green Complete buttons; old slot geometry can shift by layout."""

    # F4-07 open item 4: pair the green buttons with the fulfillable orders in
    # the row's own display sequence, not in save-slot order.  A save can hold
    # more orders than the three trays, and the leading tray is often the
    # highest slot, so filtering to slots 1-3 dropped exactly the order the
    # leftmost button belonged to.
    display_order = _display_slot_order(state)[:ORDER_DISPLAY_POSITIONS]
    fulfillable = {
        order.slot for order in state.orders if order.fulfillable_now
    }
    fulfillable_slots = [slot for slot in display_order if slot in fulfillable]
    centers = detect_delivery_control_centers(frame_rgb)
    if not centers:
        return {}
    if len(fulfillable_slots) != len(centers) and len(fulfillable) == len(centers) == 1:
        # F4-08 section 2.  _display_slot_order says a type 2 order is not on
        # the row at all, and the 2026-09-06 16:53Z frame refutes that: with
        # save slots 1(t0) 2(t0) 3(t2) 4(t3), the leftmost tray was slot 3 --
        # two items, +24 coins, and its own green Complete button, which the
        # game only draws on a fulfillable order.  The type model dropped the
        # one deliverable order and the delivery came out blocked.
        #
        # A Complete button belongs to a fulfillable order by construction, so
        # exactly one of each is a forced pairing whatever the row ordering is.
        # That is what this does, and only that: it does not invent a new
        # ordering, and with any other count it falls through to the model.
        return {next(iter(fulfillable)): (centers[0], DELIVERY_CONTROL_Y)}
    if not fulfillable_slots and len(centers) >= len(fulfillable) >= 1:
        # 2026-09-21 03:20Z.  The type model in _display_slot_order was read off
        # two frames from 2026-09-06, when this account only ever held order
        # types 0, 2 and 3.  By level 15 the save carries eight orders of types
        # 1, 2, 3, 4, 5, 9 and 12, the model recognises none of them, and its
        # display order came out as a single slot -- so every fulfillable order
        # was blocked_missing_geometry and the run spent eleven-minute segments
        # stashing and waiting on a full board with two deliverable orders on
        # screen.
        #
        # What the frame says instead: three green buttons at 338, 662 and 987,
        # which are the three calibrated tray centres, while the save called two
        # orders fulfillable -- the extra button belongs to the coin-race widget
        # parked on the leftmost tray, not to an order.  Tapping the rightmost
        # button delivered save slot 5 and paid its 32 coins (coins 1 -> 33),
        # which is the discriminating check order_geometry has been carrying as
        # an open question: the row runs by descending save slot, not by
        # descending creation time (slot 10's order is the older one and sits
        # left of slot 5's newer one).
        #
        # So the orders take the right-hand buttons, highest slot first.  A
        # mis-pairing here still taps a Complete button, which the game only
        # draws on an order it will complete, so the cost of being wrong is a
        # refuted prediction, not a wrong-surface tap.
        ordered = sorted(fulfillable, reverse=True)
        return {
            slot: (x, DELIVERY_CONTROL_Y)
            for slot, x in zip(ordered, centers[len(centers) - len(ordered):])
        }
    if not fulfillable_slots:
        return {}
    return {
        slot: (x, DELIVERY_CONTROL_Y)
        for slot, x in zip(fulfillable_slots, centers, strict=False)
    }


def _visual_occupancy_coverage(
    state: GameStateSemanticV1,
    frame_rgb: np.ndarray,
    geometry: Any,
) -> VisualOccupancyCoverageV1:
    """Fail closed when a save-empty cell does not look like calibrated blue board."""

    observations: list[VisualCellObservationV1] = []
    visually_occupied: list[CellPositionV1] = []
    radius = VISUAL_EMPTY_CROP_SIZE_PX // 2
    reference = np.asarray(VISUAL_EMPTY_REFERENCE_RGB, dtype=np.int16)
    height, width = frame_rgb.shape[:2]
    for cell in sorted(state.board.empty_cells, key=lambda item: (item.row, item.col)):
        x, y = geometry.cell_center(cell.row - 1, cell.col - 1)
        left, right = int(x) - radius, int(x) + radius
        top, bottom = int(y) - radius, int(y) + radius
        crop = frame_rgb[max(0, top) : min(height, bottom), max(0, left) : min(width, right)]
        if crop.shape[:2] != (VISUAL_EMPTY_CROP_SIZE_PX, VISUAL_EMPTY_CROP_SIZE_PX):
            background_fraction = 0.0
        else:
            delta = np.abs(crop.astype(np.int16) - reference)
            background_fraction = float(
                np.mean(np.max(delta, axis=2) <= VISUAL_EMPTY_MAX_CHANNEL_DELTA)
            )
        visually_empty = background_fraction >= VISUAL_EMPTY_MIN_BACKGROUND_FRACTION
        observation = VisualCellObservationV1(
            row=cell.row,
            col=cell.col,
            background_fraction=background_fraction,
            visually_empty=visually_empty,
        )
        observations.append(observation)
        if not visually_empty:
            visually_occupied.append(CellPositionV1(row=cell.row, col=cell.col))
    return VisualOccupancyCoverageV1(
        checked_saved_empty_cells=len(observations),
        visually_empty_cells=sum(item.visually_empty for item in observations),
        visually_occupied_cells=visually_occupied,
        crop_size_px=VISUAL_EMPTY_CROP_SIZE_PX,
        reference_rgb=VISUAL_EMPTY_REFERENCE_RGB,
        max_channel_delta=VISUAL_EMPTY_MAX_CHANNEL_DELTA,
        minimum_background_fraction=VISUAL_EMPTY_MIN_BACKGROUND_FRACTION,
        observations=observations,
    )


def visually_occupied_board_point(
    frame_rgb: np.ndarray,
    geometry: Any,
    *,
    rows: int = 9,
    cols: int = 7,
) -> tuple[int, int] | None:
    """Centre of the first board cell that does not look like empty blue board.

    F4-07 open item 2 uses this to pick a harmless "select an ordinary piece"
    tap for verifying that input landed again after an app restart, without
    needing a save read.  The same calibrated blue reference the visual
    occupancy check uses decides "empty"; a cell that fails it holds a piece.
    Returns ``None`` when no cell qualifies, so the caller declines to tap.
    """

    radius = VISUAL_EMPTY_CROP_SIZE_PX // 2
    reference = np.asarray(VISUAL_EMPTY_REFERENCE_RGB, dtype=np.int16)
    height, width = frame_rgb.shape[:2]
    for row in range(rows):
        for col in range(cols):
            x, y = geometry.cell_center(row, col)
            left, right = int(x) - radius, int(x) + radius
            top, bottom = int(y) - radius, int(y) + radius
            if left < 0 or top < 0 or right > width or bottom > height:
                continue
            crop = frame_rgb[top:bottom, left:right]
            if crop.shape[:2] != (VISUAL_EMPTY_CROP_SIZE_PX, VISUAL_EMPTY_CROP_SIZE_PX):
                continue
            delta = np.abs(crop.astype(np.int16) - reference)
            background_fraction = float(
                np.mean(np.max(delta, axis=2) <= VISUAL_EMPTY_MAX_CHANNEL_DELTA)
            )
            if background_fraction < VISUAL_EMPTY_MIN_BACKGROUND_FRACTION:
                return int(x), int(y)
    return None


def _read_total_s(result: Any, measured_s: float) -> float:
    timing = getattr(result, "timing", None)
    value = getattr(timing, "total_s", None)
    return measured_s if value is None else max(float(value), 0.0)


def _source_after_is_allowed(
    prediction: PredictionV1,
    plan: PlanV1,
    after: GameStateSemanticV1,
) -> bool:
    source = _cell(after, prediction.source_cell)
    if source is None:
        return False
    if prediction.source_after is None and source.kind == "empty":
        return True
    for later in plan.predictions:
        if later.kind != "generate":
            continue
        possible = set(later.possible_target_types)
        if later.target_after is not None:
            possible.add(later.target_after)
        if source.item_type in possible:
            return True
    return source.item_type == prediction.source_after


def _target_after_is_allowed(
    prediction: PredictionV1,
    plan: PlanV1,
    after: GameStateSemanticV1,
) -> bool:
    target = _cell(after, prediction.target_cell)
    if target is None:
        return False
    expected = _expected_target_after(prediction, plan)
    if expected is None and target.kind == "empty":
        return True
    if expected is None:
        possible_generated_types = {
            item_type
            for later in plan.predictions
            if later.kind == "generate"
            for item_type in (
                *later.possible_target_types,
                later.target_after,
            )
            if item_type is not None
        }
        return target.item_type in possible_generated_types
    return target.item_type == expected


def _expected_target_after(
    prediction: PredictionV1,
    plan: PlanV1,
) -> str | None:
    expected = prediction.target_after
    reached_prediction = False
    for later in plan.predictions:
        if later.prediction_id == prediction.prediction_id:
            reached_prediction = True
            continue
        if not reached_prediction or later.kind != "merge":
            continue
        if later.source_cell == prediction.target_cell:
            expected = later.source_after
        if later.target_cell == prediction.target_cell:
            expected = later.target_after
    return expected


def _cell_observation(
    state: GameStateSemanticV1,
    position: CellPositionV1 | None,
) -> tuple[int | None, int | None, str | None]:
    cell = _cell(state, position)
    if position is None or cell is None:
        return None, None, None
    observed = cell.item_type if cell.item_type is not None else cell.kind
    return position.row, position.col, observed


def _merge_reason(
    prediction: PredictionV1,
    plan: PlanV1,
    after: GameStateSemanticV1,
    *,
    confirmed: bool,
) -> str:
    source_expected = (
        None
        if prediction.source_cell is None
        else (
            prediction.source_cell.row,
            prediction.source_cell.col,
            prediction.source_after,
        )
    )
    target_expected = (
        None
        if prediction.target_cell is None
        else (
            prediction.target_cell.row,
            prediction.target_cell.col,
            _expected_target_after(prediction, plan),
        )
    )
    outcome = (
        "merge source/target match the recursively predicted board"
        if confirmed
        else "merge source or target differs from prediction"
    )
    return (
        f"expected src={source_expected}, dst={target_expected}; "
        f"actual src={_cell_observation(after, prediction.source_cell)}, "
        f"dst={_cell_observation(after, prediction.target_cell)}; {outcome}"
    )


def _stabilize_chain_dependencies(plan: PlanV1) -> PlanV1:
    dependency_ids = {action.depends_on for action in plan.actions if action.depends_on is not None}
    if not dependency_ids:
        return plan
    actions = [
        action.model_copy(
            update={"request": action.request.model_copy(update={"settle": "stable"})}
        )
        if action.kind == "drag"
        and action.prediction.prediction_id in dependency_ids
        and action.request is not None
        else action
        for action in plan.actions
    ]
    return plan.model_copy(
        update={
            "actions": actions,
            "predictions": [action.prediction for action in actions],
        }
    )


def _limit_business_predictions(plan: PlanV1, limit: int) -> PlanV1:
    """Do not execute past the prediction that could trip the refuted streak."""

    if limit < 1:
        raise ValueError("business prediction limit must be positive")
    business_count = 0
    actions: list[PlannedActionV1] = []
    for action in plan.actions:
        if action.prediction.kind in BUSINESS_PREDICTION_KINDS:
            if business_count >= limit:
                break
            business_count += 1
        actions.append(action)
    if len(actions) == len(plan.actions):
        return plan
    return plan.model_copy(
        update={
            "actions": actions,
            "predictions": [action.prediction for action in actions],
        }
    )


def _update_business_refuted_streak(
    adjudication: list[PredictionAdjudicationV1],
    current: int,
    limit: int,
) -> tuple[int, bool]:
    """Count only consecutive business outcomes; wait/select never clears the streak."""

    streak = current
    for item in adjudication:
        if (
            item.kind not in BUSINESS_PREDICTION_KINDS
            or not item.counts_toward_refuted_streak
        ):
            continue
        streak = streak + 1 if item.status == "refuted" else 0
        if streak >= limit:
            return streak, True
    return streak, False


def _has_input_dead(results: list[ActionResultV1]) -> bool:
    return any(
        result.error is not None and "MuMuIpcInputDeadError" in result.error for result in results
    )


def _has_input_restart_recovery(results: list[ActionResultV1]) -> bool:
    """F4-07 open item 2: input was dead, the app restart brought it back.

    The action that hit the freeze was lost and the app has been relaunched, so
    the round is abandoned and re-observed.  It is not a stop reason.
    """

    return any(
        result.error is not None and "MuMuIpcInputRestartRecoveredError" in result.error
        for result in results
    )


def _observed_generation_count(
    plan: PlanV1,
    before: GameStateSemanticV1,
    after: GameStateSemanticV1,
) -> int:
    predicted_board = [[cell.item_type for cell in row] for row in before.board.cells]
    possible_types: set[str] = set()
    for prediction in plan.predictions:
        if prediction.kind == "merge":
            if prediction.source_cell is not None:
                predicted_board[prediction.source_cell.row - 1][prediction.source_cell.col - 1] = (
                    prediction.source_after
                )
            if prediction.target_cell is not None:
                predicted_board[prediction.target_cell.row - 1][prediction.target_cell.col - 1] = (
                    prediction.target_after
                )
        elif prediction.kind == "generate":
            possible_types.update(prediction.possible_target_types)
            if prediction.target_after is not None:
                possible_types.add(prediction.target_after)
    appeared = sum(
        predicted_board[row][col] is None
        and after.board.cells[row][col].item_type in possible_types
        for row in range(9)
        for col in range(7)
    )
    return min(
        appeared,
        (_energy_spent(plan, before, after) or 0)
        + _free_generation_capacity(plan, before, after),
    )


def _energy_recovery_ticks(
    before: GameStateSemanticV1,
    after: GameStateSemanticV1,
) -> int:
    """How many energy points the recovery clock granted inside this round.

    ``EnergyModel._RestoreEnergy`` moves ``generateTime`` forward by one
    ``DefaultRecoveryDuration`` for every point it grants, so the distance the
    clock travelled is the count.  F4-11 section 2: without this term a round
    that spends one point and is handed one back looks free, and every
    generation in it is refused even though the save shows the generator one
    item lighter.  All twelve of the F6-05 "the inventory dropped but the
    verdict missed it" rounds are this and nothing else -- the four of them
    recorded after the clock landed in the report show the clock exactly one
    tick further on.
    """

    before_clock = before.resources.energy_generate_time
    after_clock = after.resources.energy_generate_time
    if before_clock is None or after_clock is None:
        return 0
    return max(after_clock - before_clock, 0) // ENERGY_RECOVERY_SECONDS


def _energy_spent(
    plan: PlanV1,
    before: GameStateSemanticV1,
    after: GameStateSemanticV1,
) -> int | None:
    """Energy this round paid out, with the recovery tick added back.

    The tick credit is capped at the number of generations the plan asked for:
    a round that resumes after a long sleep can carry thirty ticks, and none of
    them should buy a confirmation the board cannot show.
    """

    energy_before = before.resources.energy
    energy_after = after.resources.energy
    if energy_before is None or energy_after is None:
        return None
    generates = sum(prediction.kind == "generate" for prediction in plan.predictions)
    credited = min(_energy_recovery_ticks(before, after), generates)
    return max(energy_before - energy_after + credited, 0)


def _predicted_board(plan: PlanV1, before: GameStateSemanticV1) -> list[list[str | None]]:
    """The board the plan expects to be looking at once its merges have run."""

    board = [[cell.item_type for cell in row] for row in before.board.cells]
    for prediction in plan.predictions:
        if prediction.kind != "merge":
            continue
        if prediction.source_cell is not None:
            board[prediction.source_cell.row - 1][prediction.source_cell.col - 1] = (
                prediction.source_after
            )
        if prediction.target_cell is not None:
            board[prediction.target_cell.row - 1][prediction.target_cell.col - 1] = (
                prediction.target_after
            )
    return board


def _appeared_cells(
    plan: PlanV1,
    before: GameStateSemanticV1,
    after: GameStateSemanticV1,
) -> list[tuple[CellPositionV1, str]]:
    """Cells the plan expected to be free that now hold an item of any type."""

    predicted = _predicted_board(plan, before)
    landed: list[tuple[CellPositionV1, str]] = []
    for row in range(BOARD_ROWS):
        for col in range(BOARD_COLUMNS):
            if predicted[row][col] is not None:
                continue
            item_type = after.board.cells[row][col].item_type
            if item_type is not None:
                landed.append((CellPositionV1(row=row + 1, col=col + 1), item_type))
    return landed


def _generator_item_rest(
    state: GameStateSemanticV1,
    position: CellPositionV1 | None,
) -> int | None:
    cell = _cell(state, position)
    if cell is None or cell.generator is None:
        return None
    return cell.generator.item_rest


def _generator_energy_cost(
    state: GameStateSemanticV1,
    position: CellPositionV1 | None,
) -> int | None:
    cell = _cell(state, position)
    if cell is None or cell.generator is None:
        return None
    return cell.generator.cost_energy


def _inventory_drop(
    plan: PlanV1,
    before: GameStateSemanticV1,
    after: GameStateSemanticV1,
) -> int:
    """How many items the generators this plan tapped are missing afterwards.

    A generator restocks on its own timer, so a drop is a floor on what it
    produced, never a ceiling; the restock can only make a real production look
    smaller.  That is the direction this relaxation can afford to be wrong in.
    """

    seen: set[tuple[int, int]] = set()
    total = 0
    for prediction in plan.predictions:
        if prediction.kind != "generate" or prediction.source_cell is None:
            continue
        key = (prediction.source_cell.row, prediction.source_cell.col)
        if key in seen:
            continue
        seen.add(key)
        rest_before = _generator_item_rest(before, prediction.source_cell)
        rest_after = _generator_item_rest(after, prediction.source_cell)
        if rest_before is None or rest_after is None:
            continue
        total += max(rest_before - rest_after, 0)
    return total


def _free_generation_capacity(
    plan: PlanV1,
    before: GameStateSemanticV1,
    after: GameStateSemanticV1,
) -> int:
    """How many pulls a free producer can pay for without spending energy.

    ``Spread_CostEnergy == 0`` producers -- the coin/gem box, the scratch card,
    the energy crate -- charge nothing, so ``_energy_spent`` reads zero for a
    round made of them and the energy-backed count refuses every pull that in
    fact landed.  Their evidence is the producer standing one item lighter
    afterwards, which is the same floor ``_inventory_drop`` relies on.
    """

    seen: set[tuple[int, int]] = set()
    total = 0
    for prediction in plan.predictions:
        if prediction.kind != "generate" or prediction.source_cell is None:
            continue
        if _generator_energy_cost(before, prediction.source_cell) != 0:
            continue
        key = (prediction.source_cell.row, prediction.source_cell.col)
        if key in seen:
            continue
        seen.add(key)
        rest_before = _generator_item_rest(before, prediction.source_cell)
        rest_after = _generator_item_rest(after, prediction.source_cell)
        if rest_before is None or rest_after is None:
            continue
        total += max(rest_before - rest_after, 0)
    return total


def _round_can_earn_energy(plan: PlanV1) -> bool:
    """Whether anything in this round could hand energy back.

    F4-11 section 3.  The energy party pays energy for a delivery, and board
    pieces and chests pay energy when collected, so in a round that contains one
    of those the net energy delta stops being a clean measure of what the
    generations spent.
    """

    return any(
        prediction.kind in ENERGY_INCOME_PREDICTION_KINDS
        or (
            prediction.kind == "collect_reward"
            and any(reward.type == "energy" for reward in prediction.collect_rewards)
        )
        for prediction in plan.predictions
    )


def _adapt_generator_selection(
    plan: PlanV1,
    *,
    selected_generator: tuple[int, int] | None,
) -> tuple[PlanV1, tuple[int, int] | None]:
    """Represent the first tap after a drag as selection, not production."""

    actions: list[PlannedActionV1] = []
    selected = selected_generator
    for action in plan.actions:
        adapted = action
        if action.kind == "drag":
            selected = None
        elif action.kind in {"deliver", "open_chest", "sell"}:
            selected = None
        elif action.kind == "tap" and action.prediction.kind != "generate":
            selected = None
        elif action.kind == "tap" and action.prediction.kind == "generate":
            source = action.prediction.source_cell
            source_key = None if source is None else (source.row, source.col)
            if source_key is not None and selected == source_key:
                actions.append(adapted)
                continue
            prediction = action.prediction.model_copy(
                update={
                    "kind": "wait",
                    "energy_delta": 0,
                    "target_cell": None,
                    "target_before": None,
                    "target_after": None,
                    "possible_target_types": [],
                }
            )
            request = (
                None
                if action.request is None
                else action.request.model_copy(
                    update={"intent": "select-generator:before-production"}
                )
            )
            adapted = action.model_copy(
                update={
                    "intent": "select-generator:before-production",
                    "prediction": prediction,
                    "reason": "select the requested generator before production",
                    "request": request,
                }
            )
            selected = source_key
        actions.append(adapted)
    return (
        plan.model_copy(
            update={
                "actions": actions,
                "predictions": [action.prediction for action in actions],
            }
        ),
        selected,
    )


def _rave_reviews_reward_earned(state: GameStateSemanticV1 | None) -> bool:
    """True when 好评如潮 has reached its cap in the authoritative save.

    The orders carry the activity's own counter (``curScore``/``MaxScore`` as
    ``progress``/``goal``).  At the cap the settlement panel's grant is earned,
    which is the evidence the guard needs before taking it instead of closing
    it (user ruling, 2026-09-16).
    """

    if state is None:
        return False
    for order in state.orders:
        bonus = getattr(order, "event_bonus", None)
        if bonus is None or bonus.activity != ORDER_BOOST_ACTIVITY:
            continue
        progress, goal = bonus.progress, bonus.goal
        if isinstance(progress, int) and isinstance(goal, int) and goal > 0 and progress >= goal:
            return True
    return False


def _chest_evidence(raw_state: Any, cell: Any) -> dict[str, Any]:
    """Stable, item-local evidence; countdown display time is deliberately absent."""
    if cell is None:
        return {}
    generator = cell.generator
    evidence = {
        "item_type": cell.item_type,
        "item_id": None,
        "spreadState": None if generator is None else generator.spread_state,
        "spreadItemRestNumber": None if generator is None else generator.item_rest,
        "spreadStorageRestNumber": None if generator is None else generator.storage_rest,
        "spreadStartTimer": None if generator is None else generator.recovery_started_at,
    }
    board = raw_state.get("board") if isinstance(raw_state, Mapping) else getattr(raw_state, "board", None)
    extra: Mapping[str, Any] = {}
    if isinstance(board, list):
        try:
            raw_cell = board[cell.row - 1][cell.col - 1]
            value = raw_cell.get("extra", {}) if isinstance(raw_cell, Mapping) else getattr(raw_cell, "extra", {})
            if isinstance(value, Mapping):
                extra = value
        except (IndexError, TypeError):
            pass
    elif isinstance(raw_state, Mapping):
        extra = next((item for item in raw_state.get("generator_runtime", [])
                      if isinstance(item, Mapping) and item.get("row") == cell.row
                      and item.get("col") == cell.col), {})
    for key in evidence:
        if key == "item_type" or key not in extra:
            continue
        value = extra[key]
        if key == "item_id":
            evidence[key] = None if value is None else str(value)
        else:
            try:
                evidence[key] = None if value is None or isinstance(value, bool) else int(value)
            except (ValueError, TypeError):
                evidence[key] = None
    return evidence


def adjudicate_predictions(
    plan: PlanV1,
    before: GameStateSemanticV1,
    after: GameStateSemanticV1,
    *,
    before_raw: Any = None,
    after_raw: Any = None,
) -> list[PredictionAdjudicationV1]:
    """Compare each explainable prediction with the authoritative post-save state."""

    observed_generations = _observed_generation_count(plan, before, after)
    assigned_generations = 0
    # F4-11 section 2: the relaxed evidence, computed once for the round.  The
    # budget is what the generators are missing and the board gained, over and
    # above what the strict verdict already accounted for; the relaxation never
    # hands out more confirmations than the save can pay for.
    landed = _appeared_cells(plan, before, after)
    inventory_drop = _inventory_drop(plan, before, after)
    deviation_budget = max(min(len(landed), inventory_drop) - observed_generations, 0)
    deviation_assigned = 0
    income_present = _round_can_earn_energy(plan)
    energy_before_round = before.resources.energy
    energy_after_round = after.resources.energy
    energy_delta_round = (
        None
        if energy_before_round is None or energy_after_round is None
        else energy_after_round - energy_before_round
    )
    results: list[PredictionAdjudicationV1] = []
    for prediction in plan.predictions:
        confirmed = False
        deviation: GenerateDeviationV1 | None = None
        # A mismatch that still proved its business effect is recorded as a
        # refutation -- the prediction was wrong -- but it must not spend the
        # refuted-streak budget that exists to stop blind repetition.
        streak_exempt = False
        reason: str
        if prediction.kind == "merge":
            target_matches = _target_after_is_allowed(prediction, plan, after)
            source_matches = _source_after_is_allowed(prediction, plan, after)
            confirmed = target_matches and source_matches
            reason = _merge_reason(
                prediction,
                plan,
                after,
                confirmed=confirmed,
            )
            if target_matches and not source_matches:
                # The merged item is on the target cell, so the drag landed.
                # The source cell holding something else means a later action
                # of this same round refilled it, which is ordinary play.
                streak_exempt = True
                reason += (
                    "; target carries the merged item, so the merge landed and the "
                    "freed source cell was refilled within the same round"
                )
        elif prediction.kind == "generate":
            confirmed = assigned_generations < observed_generations
            if confirmed:
                assigned_generations += 1
                reason = (
                    "energy spend and a predicted-type item appeared in an available cell"
                )
            else:
                configured_cost = _generator_energy_cost(
                    before, prediction.source_cell
                )
                cost = (
                    configured_cost
                    if configured_cost is not None
                    else (abs(prediction.energy_delta) or 1)
                )
                accounted = assigned_generations + deviation_assigned + 1
                spent = _energy_spent(plan, before, after)
                if cost == 0:
                    # A free producer spends nothing, so there is no energy
                    # arithmetic to close; the inventory drop above is the
                    # whole of the evidence.
                    energy_check = "free_producer_inventory_backed"
                    energy_ok = True
                elif income_present:
                    # The inventory drop below is itself the energy evidence:
                    # the game charges before it produces, so a generator that
                    # is one item lighter was paid for.  Reading the net delta
                    # instead would refuse every generation in a round that also
                    # delivered while the energy party is on.
                    energy_check = "income_present_inventory_backed"
                    energy_ok = True
                elif spent is None:
                    energy_check = "energy_unknown"
                    energy_ok = False
                elif spent + ENERGY_RECOVERY_TICK_TOLERANCE >= cost * accounted:
                    energy_check = "consistent"
                    energy_ok = True
                else:
                    energy_check = f"short_by_{cost * accounted - spent}"
                    energy_ok = False
                if deviation_assigned < deviation_budget and energy_ok:
                    deviation_assigned += 1
                    possible = set(prediction.possible_target_types)
                    if prediction.target_after is not None:
                        possible.add(prediction.target_after)
                    matched = any(
                        item in possible
                        and (
                            prediction.target_cell is None
                            or (position.row, position.col)
                            == (prediction.target_cell.row, prediction.target_cell.col)
                        )
                        for position, item in landed
                    )
                    deviation = GenerateDeviationV1(
                        generator_cell=prediction.source_cell,
                        inventory_before=_generator_item_rest(
                            before, prediction.source_cell
                        ),
                        inventory_after=_generator_item_rest(
                            after, prediction.source_cell
                        ),
                        landed_cells=[position for position, _ in landed],
                        landed_item_types=sorted({item for _, item in landed}),
                        matched_prediction=matched,
                        energy_delta=energy_delta_round,
                        energy_check=energy_check,
                    )
                    reason = (
                        "generator inventory fell and the predicted item is on the "
                        "board; only the energy arithmetic did not close "
                        f"({energy_check})"
                        if matched
                        else "generator inventory fell and the board gained an item "
                        "the prediction did not name: production happened, the "
                        f"prediction of where or what did not ({energy_check})"
                    )
                else:
                    reason = (
                        "no remaining energy-backed generated item matches prediction"
                    )
        elif prediction.kind == "open_chest":
            source_before = _cell(before, prediction.source_cell)
            source_after = _cell(after, prediction.source_cell)
            before_energy = before.resources.energy
            after_energy = after.resources.energy
            resource_deltas = {
                name: None if getattr(before.resources, name) is None or getattr(after.resources, name) is None
                else getattr(after.resources, name) - getattr(before.resources, name)
                for name in ("energy", "coins", "gems")
            }
            free = all(value is not None and value >= 0 for value in resource_deltas.values())
            source_matches = bool(
                source_before is not None
                and source_before.item_type == prediction.source_before
                and source_after is not None
                and source_after.item_type in {prediction.source_after, None}
            )
            prior = _chest_evidence(before_raw, source_before)
            current = _chest_evidence(after_raw, source_after)
            possible = set(prediction.possible_target_types)
            if prediction.target_after is not None:
                possible.add(prediction.target_after)
            appeared_in_empty = [
                CellPositionV1(row=before_cell.row, col=before_cell.col)
                for before_row in before.board.cells
                for before_cell in before_row
                if before_cell.kind == "empty"
                and (_cell(after, CellPositionV1(row=before_cell.row, col=before_cell.col)))
                is not None
                and (
                    _cell(after, CellPositionV1(row=before_cell.row, col=before_cell.col))
                ).item_type
                in possible
            ]
            same_item = prior.get("item_id") is None or current.get("item_id") == prior["item_id"]
            depleted = source_after is not None and source_after.kind == "empty"
            stock_fell = any(
                isinstance(prior.get(key), int) and isinstance(current.get(key), int)
                and current[key] < prior[key]
                for key in ("spreadItemRestNumber", "spreadStorageRestNumber")
            )
            timer_started = (
                same_item and prior.get("spreadState") == 1
                and current.get("spreadState") in {2, 3}
                and (current.get("spreadState") == 3 or (
                    isinstance(current.get("spreadStartTimer"), int)
                    and current["spreadStartTimer"] > 0
                    and current["spreadStartTimer"] != prior.get("spreadStartTimer")
                ))
            )
            output_proven = bool(appeared_in_empty) and (depleted or (same_item and stock_fell))
            effect_proven = output_proven or (prediction.target_cell is None and timer_started)
            confirmed = free and source_matches and effect_proven
            reason = (
                f"free chest effect: timer_started={timer_started}, stock_fell={stock_fell}, "
                f"depleted={depleted}, before={prior}, after={current}, "
                f"possible_output_cells={appeared_in_empty}, "
                f"resource_deltas={resource_deltas}, "
                f"energy_delta={None if before_energy is None or after_energy is None else after_energy - before_energy}"
            )
        elif prediction.kind == "pick_chest_choice":
            # 订单棋子宝箱: the chest is consumed and the picked piece appears in
            # the chest's own cell, so there is no empty cell to look at and no
            # spread lifecycle to read.  The proof is the cell itself plus the
            # free-resource rule every chest action shares.
            source_before = _cell(before, prediction.source_cell)
            source_after = _cell(after, prediction.source_cell)
            resource_deltas = {
                name: None
                if getattr(before.resources, name) is None or getattr(after.resources, name) is None
                else getattr(after.resources, name) - getattr(before.resources, name)
                for name in ("energy", "coins", "gems")
            }
            free = all(value is not None and value >= 0 for value in resource_deltas.values())
            possible = set(prediction.possible_target_types)
            chest_was_there = (
                source_before is not None
                and source_before.item_type == prediction.source_before
            )
            became_choice = (
                source_after is not None and source_after.item_type in possible
            )
            confirmed = free and chest_was_there and became_choice
            reason = (
                f"order-piece chest pick: chest_before={None if source_before is None else source_before.item_type}, "
                f"cell_after={None if source_after is None else source_after.item_type}, "
                f"expected_one_of={sorted(possible)}, resource_deltas={resource_deltas}"
            )
        elif prediction.kind == "retrieve_from_warehouse":
            # The parked piece is back on the board: one more occupied cell of
            # the expected type, and nothing paid for it.
            expected = prediction.source_after
            before_count = sum(
                1
                for row in before.board.cells
                for cell in row
                if cell.item_type == expected
            )
            after_count = sum(
                1
                for row in after.board.cells
                for cell in row
                if cell.item_type == expected
            )
            resource_deltas = {
                name: None
                if getattr(before.resources, name) is None or getattr(after.resources, name) is None
                else getattr(after.resources, name) - getattr(before.resources, name)
                for name in ("energy", "coins", "gems")
            }
            free = all(value is not None and value >= 0 for value in resource_deltas.values())
            confirmed = free and expected is not None and after_count > before_count
            reason = (
                f"retrieve from warehouse: expected={expected}, board count "
                f"{before_count}->{after_count}, resource_deltas={resource_deltas}"
            )
        elif prediction.kind == "stash_to_warehouse":
            # Nothing is spent and nothing is destroyed: the cell must empty and
            # the free resources must not fall.  The piece itself is still in
            # the warehouse, one tap from coming back.
            source_after = _cell(after, prediction.source_cell)
            resource_deltas = {
                name: None
                if getattr(before.resources, name) is None or getattr(after.resources, name) is None
                else getattr(after.resources, name) - getattr(before.resources, name)
                for name in ("energy", "coins", "gems")
            }
            free = all(value is not None and value >= 0 for value in resource_deltas.values())
            cell_clear = source_after is not None and source_after.kind == "empty"
            confirmed = free and cell_clear
            reason = (
                f"stash to warehouse: source={prediction.source_cell} cleared={cell_clear}, "
                f"resource_deltas={resource_deltas}"
            )
        elif prediction.kind == "sell":
            source_after = _cell(after, prediction.source_cell)
            coins_before = before.resources.coins
            coins_after = after.resources.coins
            actual_coin_delta = (
                None
                if coins_before is None or coins_after is None
                else coins_after - coins_before
            )
            cell_clear = source_after is not None and source_after.kind == "empty"
            confirmed = cell_clear and actual_coin_delta == prediction.coins_delta
            reason = (
                f"expected source={prediction.source_cell} empty and "
                f"coins_delta={prediction.coins_delta}; actual cell_clear={cell_clear}, "
                f"coins_delta={actual_coin_delta}"
            )
        elif prediction.kind == "claim_reward_queue":
            before_count = (
                len(before.cache_items) if before.cache_items is not None else None
            )
            after_count = (
                len(after.cache_items) if after.cache_items is not None else None
            )
            gained = [
                after_cell
                for before_row in before.board.cells
                for before_cell in before_row
                if before_cell.kind == "empty"
                and (after_cell := _cell(
                    after, CellPositionV1(row=before_cell.row, col=before_cell.col)
                )) is not None
                and after_cell.kind != "empty"
            ]
            expected_code = prediction.cache_item_code_before
            before_head_id = prediction.cache_queue_head_id_before
            before_head = _first_cache_item(before.cache_items)
            after_ids = {
                str(row.get("id"))
                for row in (after.cache_items or [])
                if row.get("id") is not None
            }
            after_ids_complete = all(
                row.get("id") is not None for row in (after.cache_items or [])
            )
            head_removed = (
                before_head_id is not None
                and before_head is not None
                and after_ids_complete
                and before_head_id == (
                    None
                    if before_head.get("id") is None
                    else str(before_head.get("id"))
                )
                and before_head_id not in after_ids
            )
            code_matches = (
                expected_code is not None
                and bool(gained)
                and any(cell.item_type == expected_code for cell in gained)
            )
            code_known = expected_code is not None
            head_known = before_head_id is not None and before_head is not None
            confirmed = (
                before_count is not None
                and after_count is not None
                and prediction.cache_items_before == before_count
                and after_count == before_count - 1
                and head_known
                and head_removed
                and code_known
                and code_matches
            )
            reason = (
                f"expected one free CacheItem head and output code={expected_code!r}; "
                f"cache_count={before_count}->{after_count}, head_removed={head_removed}, "
                f"new_cells={[cell.item_type for cell in gained]}, code_matches={code_matches}"
                if code_known and head_known
                else (
                    "undetermined: CacheItem head id or code could not be decoded; "
                    "queue claim is not confirmed"
                )
            )
        elif prediction.kind in {"collect_energy", "collect_reward"}:
            source_after = _cell(after, prediction.source_cell)
            cell_clear = source_after is not None and source_after.kind == "empty"
            expected = {
                reward.type: reward.amount for reward in prediction.collect_rewards
            }
            if not expected and prediction.kind == "collect_energy":
                expected = {"energy": prediction.energy_delta}
            actual: dict[str, int | None] = {}
            for reward_type in expected:
                resource_name = {
                    "gold": "coins",
                    "gem": "gems",
                    "energy": "energy",
                }.get(reward_type, reward_type)
                before_value = getattr(before.resources, resource_name, None)
                after_value = getattr(after.resources, resource_name, None)
                actual[reward_type] = (
                    None
                    if before_value is None or after_value is None
                    else after_value - before_value
                )
            deltas_match = all(
                actual[reward_type] is not None
                and expected_amount
                <= actual[reward_type]
                <= expected_amount + (1 if reward_type == "energy" else 0)
                for reward_type, expected_amount in expected.items()
            )
            confirmed = cell_clear and bool(expected) and deltas_match
            reason = (
                f"expected source={prediction.source_cell} empty and rewards={expected} "
                f"(+1 energy recovery tick tolerated); actual cell_clear={cell_clear}, "
                f"resource_deltas={actual}; energy_delta={actual.get('energy')}"
            )
        elif prediction.kind in {"wait", "generator_inventory_wait"}:
            confirmed = True
            reason = (
                "generator selection requires no authoritative save delta"
                if prediction.kind == "wait" and prediction.source_cell is not None
                else "generator inventory is exhausted; bounded recovery wait is non-refuting"
                if prediction.kind == "generator_inventory_wait"
                else "bounded wait completed"
            )
        elif prediction.kind == "deliver":
            before_order = next(
                (order for order in before.orders if order.slot == prediction.order_slot),
                None,
            )
            after_order = next(
                (order for order in after.orders if order.slot == prediction.order_slot),
                None,
            )
            coins_before = before.resources.coins
            coins_after = after.resources.coins
            actual_coin_delta = (
                None if coins_before is None or coins_after is None else coins_after - coins_before
            )
            coins_match = actual_coin_delta == prediction.coins_delta
            cells_clear = all(
                (_cell(after, position) is not None) and _cell(after, position).kind == "empty"
                for position in prediction.required_cells
            )
            order_changed = (
                before_order is not None
                and before_order.order_id == prediction.order_before
                and (after_order is None or after_order.order_id != prediction.order_before)
            )
            confirmed = coins_match and cells_clear and order_changed
            after_order_id = None if after_order is None else after_order.order_id
            reason = (
                f"expected coins_delta={prediction.coins_delta}, "
                f"required_cells_clear={prediction.required_cells}, "
                f"slot={prediction.order_slot} order_change={prediction.order_before!r}; "
                f"actual coins_delta={actual_coin_delta}, cells_clear={cells_clear}, "
                f"order_after={after_order_id!r}"
            )
        else:
            before_task = before.main_task
            after_task = after.main_task
            before_coins = before.resources.coins
            after_coins = after.resources.coins
            actual_coin_delta = (
                None if before_coins is None or after_coins is None else after_coins - before_coins
            )
            task_advanced = (
                before_task is not None
                and before_task.task_id == prediction.main_task_before
                and after_task is not None
                and after_task.task_id != prediction.main_task_before
            )
            confirmed = task_advanced and actual_coin_delta == prediction.coins_delta
            reason = (
                f"expected task advance from {prediction.main_task_before!r} and "
                f"coins_delta={prediction.coins_delta}; actual task_after="
                f"{None if after_task is None else after_task.task_id!r}, "
                f"coins_delta={actual_coin_delta}"
            )
        if confirmed:
            status: AdjudicationStatus = "confirmed"
        elif deviation is not None:
            status = "confirmed_with_deviation"
        else:
            status = "refuted"
        results.append(
            PredictionAdjudicationV1(
                prediction_id=prediction.prediction_id,
                kind=prediction.kind,
                status=status,
                reason=reason,
                deviation=deviation,
                counts_toward_refuted_streak=(
                    prediction.kind in BUSINESS_PREDICTION_KINDS
                    and (
                        prediction.kind != "generate"
                        or prediction.generator_inventory_available is True
                    )
                    and not streak_exempt
                ),
            )
        )
    return results


class GameLoop:
    """Run deterministic bounded rounds over authoritative save state."""

    def __init__(
        self,
        save_channel: Any,
        actuator: Any,
        geometry: Any,
        run: Any,
        policy: PolicyV1,
        *,
        backend: BackendName | None = None,
        reuse_read: bool = True,
        post_foreground_settle_ms: int = 500,
        order_geometry: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        app_alive_probe: Callable[[], bool] | None = None,
        app_relaunch: Callable[[], Any] | None = None,
        cold_start_policy: ColdStartPolicy = "status_quo",
        cold_start_settle_ms: int = 600,
        use_behavior_sm: bool = False,
        agent_handler_enabled: bool = False,
        target_level: int | None = None,
        control_state_file: Path | None = None,
    ) -> None:
        if backend not in {None, "mumu_ipc", "maa_adb"}:
            raise ValueError("backend must be mumu_ipc or maa_adb")
        if post_foreground_settle_ms < 0:
            raise ValueError("post_foreground_settle_ms must be non-negative")
        if cold_start_policy not in COLD_START_POLICIES:
            raise ValueError(
                f"cold_start_policy must be one of {sorted(COLD_START_POLICIES)}"
            )
        if cold_start_settle_ms < 0:
            raise ValueError("cold_start_settle_ms must be non-negative")
        if target_level is not None and target_level < 1:
            raise ValueError("target_level must be a positive integer")
        self.save_channel = save_channel
        self.actuator = actuator
        self.geometry = geometry
        self.replay = run
        self.policy = policy
        self.backend: BackendName = (
            "mumu_ipc"
            if backend is None and type(actuator).__name__ == "MuMuIpcActuator"
            else backend or "maa_adb"
        )
        self.reuse_read = reuse_read
        self.post_foreground_settle_ms = post_foreground_settle_ms
        self.cold_start_policy: ColdStartPolicy = cold_start_policy
        self.cold_start_settle_ms = cold_start_settle_ms
        self.cold_start_warmups = 0
        # F4-12 Phase 2: opt-in behavior state machine.  False keeps the
        # legacy plan_batch path byte-identical.
        self.use_behavior_sm = use_behavior_sm
        # F4-13: opt-in agent-handler arming.  False keeps the guard's
        # behaviour byte-identical; True adds the unexplored-surface stop for
        # registered surfaces the registry still marks ``unexplored``.
        self.agent_handler_enabled = agent_handler_enabled
        self.target_level = target_level
        self.control_state_file = (
            None if control_state_file is None else Path(control_state_file)
        )
        self._unexplored_surface_stop: str | None = None
        self.order_geometry = order_geometry
        self.sleep = sleep
        self.exhaustion = ExhaustionDetectorV1()
        # The screen guard is unconditional: the forbidden-surface gate, the
        # overlay negative check and the popup whitelist are safety rules, so
        # they must not hang off the unrelated main-task switch (F4-05 open
        # item 2 — an unguarded probe run sent two board taps on a story
        # prompt).  Only the main-task *flow* stays behind the switch.
        self.surface_registry = SurfaceRegistry()
        self._last_semantic_state: GameStateSemanticV1 | None = None
        self._last_raw_state: Any = None
        self.main_task_flow = (
            MainTaskFlow(
                self.surface_registry,
                config_dir=policy.config_dir,
                sleep=sleep,
                # F4-07 open item 3: hand the flow the guard's adjudicated
                # identification so both see the same screen.  Injected as a
                # callable because ``main_task_flow`` cannot import this module.
                identify=self._identify_screen,
            )
            if policy.enable_main_task
            else None
        )
        self._last_frame_rgb: np.ndarray | None = None
        self._forbidden_surface_stop: ForbiddenSurfaceV1 | None = None
        # F4-07 open item 2: how many times a renderer input freeze was fixed by
        # relaunching the app during this loop.  Not an action count.
        self.input_restart_recoveries = 0
        # F4-09 section 1.1: the authoritative state the next claim-class guard
        # action is measured against.  The round sets it from its own read and
        # each forced evidence read advances it, so consecutive claims inside
        # one round get consecutive windows rather than one cumulative one.
        self._claim_evidence_before: GameStateSemanticV1 | None = None
        self._claim_evidence_reads = 0
        # F4-09 1.6: move -> the round number after which it may be planned
        # again.  Keyed by an unordered cell pair for a merge and by the
        # generator cell for a production.
        self._suppressed_merge_pairs: dict[
            tuple[tuple[int, int], tuple[int, int]], int
        ] = {}
        self._suppressed_generator_cells: dict[tuple[int, int], int] = {}
        # The round after which a refused warehouse parking may be tried again.
        self._warehouse_stash_hold_until: int = 0
        self._chest_holds_path = (
            None if self.control_state_file is None else self.control_state_file.parent / "chest-holds.json"
        )
        self._suppressed_chest_cells: dict[tuple[int, int], dict[str, Any]] = {}
        self._energy_purchases_path = (
            None
            if self.control_state_file is None
            else self.control_state_file.parent / "energy-purchases.json"
        )
        self._gem_purchases_path = (
            None
            if self.control_state_file is None
            else self.control_state_file.parent / "gem-purchases.json"
        )
        # 2026-09-21.  A segment that starts on the leftover energy the boost
        # cannot pay for never gets to measure the boost: its first pull is
        # refused, so no confirmed round teaches it anything, and the game's
        # purchase panel stops the run instead.  The measurement therefore
        # outlives the segment, next to the other ledgers.
        self._generate_energy_path = (
            None
            if self.control_state_file is None
            else self.control_state_file.parent / "generate-energy.json"
        )
        self._generate_energy_multiplier = self._load_generate_energy_multiplier()
        self._last_shop_visit_epoch = 0.0
        if self._chest_holds_path is not None and self._chest_holds_path.exists():
            saved_holds = json.loads(self._chest_holds_path.read_text(encoding="utf-8"))
            now = time.time()
            for hold in saved_holds.get("holds", []):
                evidence = hold["evidence"]
                held_at = (
                    evidence.get("held_at_epoch") if isinstance(evidence, Mapping) else None
                )
                # A hold written before holds carried a timestamp, or one older
                # than the TTL, is dropped rather than inherited forever.
                if not isinstance(held_at, int) or now - held_at >= CHEST_HOLD_TTL_S:
                    continue
                self._suppressed_chest_cells[(int(hold["row"]), int(hold["col"]))] = evidence
        # What this run has parked in the warehouse.  Nothing else records it:
        # the save exposes no warehouse inventory, so a confirmed stash adds one
        # here and a confirmed retrieval takes one away, and the count survives
        # a segment restart alongside the chest holds.
        self._warehouse_holds_path = (
            None
            if self.control_state_file is None
            else self.control_state_file.parent / "warehouse-holds.json"
        )
        self._warehouse_parked: list[str] = []
        if self._warehouse_holds_path is not None and self._warehouse_holds_path.exists():
            try:
                parked = json.loads(self._warehouse_holds_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                parked = {}
            items = parked.get("parked") if isinstance(parked, dict) else None
            if isinstance(items, list):
                self._warehouse_parked = [str(item) for item in items]
        self._previous_read_epoch_ms: int | None = None
        # F4-09: when each registered red-line surface was closed in this run,
        # so a surface that keeps coming back stops the run instead of being
        # closed forever.
        self._forbidden_close_times: dict[str, list[float]] = {}
        self._forbidden_recurring_stop: str | None = None
        # F4-09: guided target -> how many taps in a row left the frame
        # byte-identical.  Reset by any tap that changes something.
        self._guided_target_stalls: dict[str, int] = {}
        # F4-11: advances spent on the story dialogue the run is currently in.
        # Reset when the guard sees something that is not that dialogue.
        self._story_advance_taps = 0
        # 2026-09-08: extra guard rounds already handed to each handoff
        # surface (GUARD_HANDOFF_SURFACES).  Cleared by any round that leaves
        # the panel, so the budget binds per continuous occupation.
        self._guard_handoff_rounds: dict[str, int] = {}
        self._tutorial_follow_stalled: str | None = None
        # Which popup kind stalled.  Defaults to the tutorial follow because
        # that was the only thing that could stall before F4-11.
        self._stalled_kind: str = "tutorial-follow"
        # F4-09: the board is full, a guide is pointing at the reward chest and
        # will not answer, and the save proves both.  In this state the planner
        # may only merge and sell, until there is room for the chest to drop
        # into.
        # F4-10.  Whether the game process is still there, and how to bring it
        # back.  Both optional: without them a dead app is still reported as a
        # channel failure, which is what it was before the ruling.
        self.app_alive_probe = app_alive_probe
        self.app_relaunch = app_relaunch
        # When the app was relaunched after dying, so a run that keeps losing
        # the app stops instead of relaunching all day.
        self._app_relaunch_times: list[float] = []
        self.app_relaunch_recoveries = 0
        self._board_full_under_guide = False
        self._board_full_allowance = False
        self._deliver_allowance = False
        self._board_full_guide_target: ActionRequestV1 | None = None
        self._last_screenshot_sha: str | None = None
        # The shop badge is a one-shot board probe for this GameLoop.  Once
        # checked (with or without a matching three-anchor signature), it is
        # never retried by another guard pass in the same loop.
        self._shop_entry_checked = False

    def _control_stop_reason(self) -> LoopStopReason | None:
        """Return the stop reason when the control file closes the input gate."""

        if self.control_state_file is None:
            return None
        try:
            state = read_control_state(self.control_state_file)
        except ControlStateError as exc:
            # A configured but invalid file is fail-closed: it is never a
            # device/input failure and no actuator call may follow it.
            self.replay.alert(f"control_state_invalid: {exc}")
            return "control_state_invalid"
        if state.mode in {"manual", "paused"}:
            self.replay.note(f"control_handed_over: control state is {state.mode}")
            return "control_handed_over"
        return None

    def _check_shop_red_badge_entry(
        self,
        frame: Any,
        screenshot_sha: str,
        screenshot_ms: float,
    ) -> tuple[
        Any,
        str,
        float,
        PopupHandlingRecordV1 | None,
        ActionResultV1 | None,
        bool,
    ]:
        """Probe and, once, open the registered free-reward shop entry.

        The registry owns the positive cart-plus-red-badge signature.  This
        helper only acts on the returned point; missing or malformed
        registration therefore fails closed without an input.  The caller
        records a transition round and replans from a fresh authoritative
        read, so a shop frame is never handed to the board planner.
        """

        if self._shop_entry_checked:
            return frame, screenshot_sha, screenshot_ms, None, None, False
        self._shop_entry_checked = True
        try:
            point = self.surface_registry.visible_variant_point(
                "main-board", "shop-red-badge-entry", self._last_frame_rgb
            )
        except (KeyError, TypeError, ValueError):
            point = None
        if point is None:
            return frame, screenshot_sha, screenshot_ms, None, None, False
        x, y = int(point[0]), int(point[1])
        request = ActionRequestV1(
            kind="tap",
            intent="navigation:main-board:shop-red-badge-entry",
            params={"x": x, "y": y},
            capture_pre=True,
            capture_post=True,
            settle="stable",
        )
        try:
            result = self.actuator.act(request)
        except Exception as exc:  # noqa: BLE001 - stop safely after a probe
            self.replay.alert(
                "shop_entry_stalled: registered red-badge entry input failed "
                f"({type(exc).__name__})"
            )
            return frame, screenshot_sha, screenshot_ms, None, None, True
        record = PopupHandlingRecordV1(
            screen="main-board",
            kind="navigation",
            element="shop-red-badge-entry",
            action_id=result.action_id,
        )
        after_frame, after_sha, capture_ms = self._capture_round_frame()
        self._last_screenshot_sha = after_sha
        screenshot_ms += capture_ms
        if result.error or after_sha == screenshot_sha:
            self.replay.alert(
                "shop_entry_stalled: registered red-badge entry produced no "
                "visible transition; holding without retry"
            )
            return after_frame, after_sha, screenshot_ms, record, result, True
        self.replay.note(
            "shop_entry: registered red-badge cart entry opened a new surface; "
            "the next round will run the existing shop/reward guard"
        )
        return after_frame, after_sha, screenshot_ms, record, result, False

    def _target_reached(self, state: GameStateSemanticV1) -> bool:
        """Check only the authoritative save level; never synthesize progress."""

        return (
            self.target_level is not None
            and state.resources.level is not None
            and state.resources.level >= self.target_level
        )

    def _identify_screen(self, frame_rgb: Any) -> ScreenIdV1:
        """The one adjudicated identification the loop and the flow both use."""

        return _identify_guard_screen(frame_rgb, self.surface_registry)

    def _unexplored_registered_surface(self, screen_name: str) -> str | None:
        """The surface name when the guard must stop for the agent handler.

        F4-13.  Only when the loop was armed (``agent_handler_enabled``): a
        registered, settled, non-transition surface the navigation registry
        still marks ``unexplored: true`` stops the guard where it stands, so
        the handler's exploration segment sees the surface untouched.  When
        the arm is off this always returns None and the guard is unchanged.
        """

        if not self.agent_handler_enabled or self.surface_registry is None:
            return None
        if screen_name in SETTLED_SURFACES or screen_name in {"unknown", "overlay-unknown"}:
            return None
        if self.surface_registry.is_transition(screen_name):
            return None
        surface = self.surface_registry.surfaces.get(screen_name)
        if isinstance(surface, dict) and surface.get("unexplored") is True:
            return screen_name
        return None

    def _enter_board_full_under_guide(
        self,
        state: GameStateSemanticV1,
    ) -> bool:
        """Enter the full-board state, but only on all three of its conditions.

        F4-09, planner ruling of 2026-09-07.  Pixels cannot separate this from a
        real tutorial -- the same chest and arrow appear in genuine tutorial
        frames, measured at a nearest-negative distance of 0.07 -- so the third
        condition is the authoritative save, which is unambiguous.  All three
        are required: the guard is on a tutorial overlay, the guide has been
        tapped three times without the frame moving, and the save says there is
        nowhere on the board for anything to land.
        """

        if self._tutorial_follow_stalled is None:
            return False
        if self._stalled_kind != "tutorial-follow":
            # F4-11.  The first condition in the paragraph above was never
            # actually checked, because until the story-dialogue ruling only a
            # tutorial guide could stall.  Now one more surface can, and this
            # state must not fire on it: it would let merges through while a
            # dialogue covers the screen.
            return False
        if len(state.board.empty_cells) != 0:
            return False
        if self._board_full_under_guide:
            self._tutorial_follow_stalled = None
            return True
        stalled_at = self._tutorial_follow_stalled
        self._board_full_under_guide = True
        # The stall has been handled by entering the state; leaving the flag
        # set makes the pre-action guard stop the very round the state exists
        # to run, which is what the 01:33Z run did twice.
        self._tutorial_follow_stalled = None
        self._set_actuator_board_full_allowance(True)
        self.replay.note(
            "board_full_under_guide: the guide at "
            f"{stalled_at} will not answer and the save "
            "reports 0 free cells; merging and selling only until "
            f"{BOARD_FULL_UNDER_GUIDE_DROP_CELLS} cells are free"
        )
        return True

    def _set_actuator_board_full_allowance(self, allowed: bool) -> None:
        """Tell the actuator whether merging and selling may cross the guide."""

        self._board_full_allowance = allowed
        self._push_actuator_allowance()

    def _set_actuator_deliver_allowance(self, allowed: bool) -> None:
        """Tell the actuator whether a delivery may cross the guide.

        F4-10, planner ruling of 2026-09-07.  A frame can read as a tutorial
        overlay and still be showing an order with its Complete button up; the
        guided action then is the delivery, not whatever the overlay's
        registered target happens to be.  Delivery is already a whitelisted
        business action with a save adjudication behind it, so the allowance
        only lets it reach a frame the pre-frame rule would otherwise refuse.
        """

        self._deliver_allowance = allowed
        self._push_actuator_allowance()

    def _push_actuator_allowance(self) -> None:
        """The union of the allowances currently in force."""

        if not hasattr(self.actuator, "board_full_under_guide_intents"):
            return
        allowed: tuple[str, ...] = ()
        if getattr(self, "_board_full_allowance", False):
            allowed += BOARD_FULL_UNDER_GUIDE_INTENTS
        if getattr(self, "_deliver_allowance", False):
            allowed += DELIVER_UNDER_GUIDE_INTENTS
        self.actuator.board_full_under_guide_intents = allowed

    def _leave_board_full_under_guide(self, reason: str) -> None:
        """Return to the normal loop and take the allowance back with it."""

        if not self._board_full_under_guide:
            return
        self._board_full_under_guide = False
        self._board_full_guide_target = None
        self._tutorial_follow_stalled = None
        self._guided_target_stalls.clear()
        self._set_actuator_board_full_allowance(False)
        self.replay.note(f"board_full_under_guide: left because {reason}")

    def _load_generate_energy_multiplier(self) -> int:
        path = self._generate_energy_path
        if path is None or not path.exists():
            return 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return 1
        value = payload.get("multiplier") if isinstance(payload, Mapping) else None
        if not isinstance(value, int) or isinstance(value, bool):
            return 1
        return min(max(value, 1), 4)

    def _save_generate_energy_multiplier(self) -> None:
        path = self._generate_energy_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "version": 1,
                    "multiplier": self._generate_energy_multiplier,
                    "measured_at_epoch": int(time.time()),
                    "why": (
                        "能量助力 charges this multiple of the item config's "
                        "Spread_CostEnergy; measured from rounds whose energy "
                        "went entirely on confirmed pulls."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _observe_generate_energy_multiplier(
        self,
        plan: PlanV1,
        before: GameStateSemanticV1,
        after: GameStateSemanticV1,
        adjudication: list[PredictionAdjudicationV1],
    ) -> None:
        """Learn what a pull really charges, as a multiple of the table value.

        2026-09-21.  ``Spread_CostEnergy`` is static, and the level 15 boost
        doubles the charge without touching it.  The 08:32Z segment's energy
        walked 32-30-28-26 on one generate a round while the planner modelled
        1 each, so at energy 1 it kept planning a pull it could not afford: the
        tap was refused, the game answered with its energy purchase panel, and
        the guard closing that red-line panel twice stopped the run for a
        person.  Only rounds whose energy went entirely on confirmed pulls are
        read, and the multiplier only ever rises within a run -- a round that
        happens to look cheap must not hand the planner back its old mistake.
        """

        statuses = {
            verdict.prediction_id: verdict.status for verdict in adjudication
        }
        modelled = 0
        for prediction in plan.predictions:
            status = statuses.get(prediction.prediction_id)
            if prediction.kind != "generate":
                # Anything else in the round may move energy too (a delivery
                # pays it back, the energy party changes the arithmetic), and
                # then the division below means nothing.
                if prediction.energy_delta:
                    return
                continue
            if status not in {"confirmed", "confirmed_with_deviation"}:
                return
            cost = _generator_energy_cost(before, prediction.source_cell)
            modelled += cost if cost is not None else 1
        if modelled <= 0:
            return
        spent = _energy_spent(plan, before, after)
        if spent is None or spent <= 0:
            return
        observed = int(round(spent / modelled))
        if observed <= self._generate_energy_multiplier:
            return
        self._generate_energy_multiplier = min(observed, 4)
        self._save_generate_energy_multiplier()
        self.replay.note(
            f"generate_energy_multiplier: {modelled} energy of pulls actually "
            f"cost {spent}; planning at x{self._generate_energy_multiplier} "
            "from here (能量助力 doubles the charge without changing the item "
            "config)"
        )

    def _open_chest_when_room_exists(
        self,
        state: GameStateSemanticV1,
    ) -> PopupHandlingRecordV1 | None:
        """Once there is room, tap the guide's own target once and measure it.

        The tap is the registered guided target and nothing else.  If the frame
        moves the state is over; if it does not, the run stops rather than
        tapping a fourth, fifth and sixth time, which is what it did before.
        """

        if not self._board_full_under_guide:
            return None
        if len(state.board.empty_cells) < BOARD_FULL_UNDER_GUIDE_DROP_CELLS:
            return None
        request = self._board_full_guide_target
        if request is None:
            return None
        before_sha = self._last_screenshot_sha
        result = self.actuator.act(request)
        frame, screenshot_sha, _capture_ms = self._capture_round_frame()
        self._last_screenshot_sha = screenshot_sha
        record = PopupHandlingRecordV1(
            screen="board-full-under-guide",
            kind="free-claim",
            element="open-main-task-reward-chest",
            action_id=result.action_id,
        )
        if screenshot_sha == before_sha:
            self.replay.alert(
                "tutorial_follow_stalled: room was made and the reward chest "
                "still did not answer"
            )
            self._leave_board_full_under_guide(
                "the reward chest did not answer even with room on the board"
            )
            # Set after leaving, because leaving clears it: the run stops on
            # this rather than tapping a fourth, fifth and sixth time.
            self._tutorial_follow_stalled = "open-main-task-reward-chest"
            return record
        # The claim needs a number, like every other claim: one forced read
        # against the state this round already had in hand.
        try:
            _result, after, read_s = self._read_semantic(flush="home_and_return")
        except Exception as exc:  # pragma: no cover - device failure path
            record = record.model_copy(
                update={
                    "save_delta": PopupSaveDeltaV1(
                        available=False,
                        reason=f"forced save read failed: {type(exc).__name__}",
                        group_id=result.action_id,
                        group_action_ids=[result.action_id],
                        guard_actions_in_window=[
                            "board-full-under-guide:free-claim:"
                            "open-main-task-reward-chest"
                        ],
                    )
                }
            )
        else:
            record = record.model_copy(
                update={
                    "save_delta": build_claim_save_delta(
                        state,
                        after,
                        group_id=result.action_id,
                        group_action_ids=[result.action_id],
                        guard_actions_in_window=[
                            "board-full-under-guide:free-claim:"
                            "open-main-task-reward-chest"
                        ],
                        read_s=read_s,
                    )
                }
            )
            self._claim_evidence_before = after
        self._leave_board_full_under_guide(
            "the reward chest answered once there was room"
        )
        return record

    def _note_forbidden_close(self, name: str) -> bool:
        """Record one close and say whether this surface has recurred too often."""

        now = time.monotonic()
        history = [
            stamp
            for stamp in self._forbidden_close_times.get(name, [])
            if now - stamp <= FORBIDDEN_RECURRENCE_WINDOW_S
        ]
        history.append(now)
        self._forbidden_close_times[name] = history
        return len(history) >= FORBIDDEN_RECURRENCE_LIMIT

    def _unavailable_merge_pairs(
        self,
        state: GameStateSemanticV1,
        held: frozenset[tuple[int, int]],
    ) -> frozenset[tuple[tuple[int, int], tuple[int, int]]]:
        """Board pairs the planner is holding out, for the detector to discount.

        F4-10, planner ruling B of 2026-09-07.  The board shows every pair that
        could merge; the planner uses a smaller set, because a pair whose merge
        was refuted is held for a few rounds and a pair with a piece that has
        only just landed waits a round.  The exhaustion detector was reading the
        board set and concluding that play still had a move, which stopped it
        naming energy as the blocker and cost the driver the resume time.
        """

        unavailable = set(self._suppressed_merge_pairs)
        for pair in state.board.mergeable_pairs:
            left = (pair.first.row, pair.first.col)
            right = (pair.second.row, pair.second.col)
            if left in held or right in held:
                unavailable.add(tuple(sorted((left, right))))
        return frozenset(unavailable)

    def _expire_refused_moves(self, round_number: int) -> None:
        """Drop holds whose rounds have run out, before this round plans."""

        self._suppressed_merge_pairs = {
            key: until
            for key, until in self._suppressed_merge_pairs.items()
            if until >= round_number
        }
        self._suppressed_generator_cells = {
            key: until
            for key, until in self._suppressed_generator_cells.items()
            if until >= round_number
        }
        if self._warehouse_stash_hold_until < round_number:
            self._warehouse_stash_hold_until = 0

    def _save_warehouse_holds(self) -> None:
        if self._warehouse_holds_path is None:
            return
        path = self._warehouse_holds_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"version": 1, "parked": list(self._warehouse_parked)}, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def _save_chest_holds(self) -> None:
        if self._chest_holds_path is None:
            return
        path = self._chest_holds_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 1, "holds": [
            {"row": row, "col": col, "evidence": evidence}
            for (row, col), evidence in sorted(self._suppressed_chest_cells.items())
        ]}, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _refresh_chest_holds(self, raw_state: Any, state: GameStateSemanticV1) -> None:
        now = time.time()

        def _still_held(position: tuple[int, int], evidence: Any) -> bool:
            held_at = evidence.get("held_at_epoch") if isinstance(evidence, Mapping) else None
            if not isinstance(held_at, int) or now - held_at >= CHEST_HOLD_TTL_S:
                return False
            current = _chest_evidence(
                raw_state, _cell(state, CellPositionV1(row=position[0], col=position[1]))
            )
            comparable = {
                key: value for key, value in evidence.items() if key != "held_at_epoch"
            }
            return comparable == current

        retained = {
            position: evidence
            for position, evidence in self._suppressed_chest_cells.items()
            if _still_held(position, evidence)
        }
        if retained != self._suppressed_chest_cells:
            self._suppressed_chest_cells = retained
            self._save_chest_holds()

    def _record_refused_moves(
        self,
        plan: PlanV1,
        adjudication: list[PredictionAdjudicationV1],
        round_number: int,
        *,
        after: GameStateSemanticV1 | None = None,
        after_raw: Any = None,
    ) -> None:
        """Hold every move the device just declined out of the next few rounds."""

        refuted = {
            item.prediction_id for item in adjudication if item.status == "refuted"
        }
        if not refuted:
            return
        until = round_number + REFUSED_MOVE_HOLD_ROUNDS
        for prediction in plan.predictions:
            if prediction.prediction_id not in refuted:
                continue
            source = prediction.source_cell
            target = prediction.target_cell
            if prediction.kind == "merge" and source is not None and target is not None:
                key = tuple(
                    sorted(((source.row, source.col), (target.row, target.col)))
                )
                self._suppressed_merge_pairs[key] = until
            elif prediction.kind == "generate" and source is not None:
                self._suppressed_generator_cells[(source.row, source.col)] = until
            elif prediction.kind == "stash_to_warehouse":
                self._warehouse_stash_hold_until = until
            elif prediction.kind == "open_chest" and source is not None and after is not None:
                evidence = dict(
                    _chest_evidence(after_raw, _cell(after, source)) or {}
                )
                evidence["held_at_epoch"] = int(time.time())
                self._suppressed_chest_cells[(source.row, source.col)] = evidence
                self._save_chest_holds()

    def _gem_purchase_ledger(self) -> dict[str, Any]:
        path = self._gem_purchases_path
        empty: dict[str, Any] = {"version": 1, "purchases": [], "holds": {}}
        if path is None or not path.exists():
            return empty
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return empty
        if not isinstance(payload, dict) or not isinstance(payload.get("purchases"), list):
            return empty
        payload.setdefault("holds", {})
        return payload

    def _save_gem_purchase_ledger(self, ledger: dict[str, Any]) -> None:
        path = self._gem_purchases_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _await_screen(self, expected: str) -> str:
        """Poll until ``expected`` is on screen or PANEL_OPEN_TIMEOUT_S passes.

        2026-09-17 07:34Z: the cart tap settled "stable" on the board frame
        before the shop's slide-in started, one capture still read main-board,
        the visit gave up, and the shop opened a second later and stayed up.
        Only the board, an unknown frame or a registered transition is waited
        out; any other surface is the answer at once.  Returns the last screen
        seen.
        """

        deadline = time.monotonic() + PANEL_OPEN_TIMEOUT_S
        while True:
            self._capture_round_frame()
            name = self._identify_screen(self._last_frame_rgb).name
            still_opening = name in {"main-board", "unknown", "overlay-unknown"} or (
                self.surface_registry is not None and self.surface_registry.is_transition(name)
            )
            if name == expected or not still_opening or time.monotonic() >= deadline:
                return name
            self.sleep(SCREEN_GUARD_POLL_S)

    def _verify_pending_shop_purchase(self, state: GameStateSemanticV1) -> None:
        """Check the latest unverified paid shop purchase against this read's gems.

        The pending purchase is found in the persisted ledger rather than held
        in memory: every segment is its own process, and 2026-09-17 a 508 bought
        at the end of one segment was never checked because the next purchase
        came from the next segment.  Every gem spend goes through
        _try_gem_spend, which verifies first, so at most one paid purchase is
        ever pending when this runs.
        """

        gems_now = state.resources.gems
        if gems_now is None:
            return
        ledger = self._gem_purchase_ledger()
        entries = ledger.get("purchases", [])
        pending = [
            index for index, item in enumerate(entries)
            if isinstance(item, dict) and item.get("outcome") is None
            and (item.get("cost") or 0) > 0 and isinstance(item.get("gems_before"), int)
        ]
        if not pending:
            return
        for index in pending[:-1]:
            entries[index]["outcome"] = "unverified"
        entry = entries[pending[-1]]
        pending = {"gems_before": entry["gems_before"], "cost": entry["cost"], "item_type": entry.get("item_type")}
        spent = int(pending["gems_before"]) - int(gems_now)
        now = time.time()
        if spent <= 0:
            entry["outcome"] = "not_bought"
            ledger["holds"]["shop_paid_until_epoch"] = now + SHOP_NOT_BOUGHT_HOLD_S
            self.replay.alert(
                f"gem_spend: {pending['item_type']} was tapped but gems did not fall "
                f"({pending['gems_before']} -> {gems_now}); shop purchases held for an hour"
            )
        elif spent > int(pending["cost"]):
            entry["outcome"] = "unexpected_spend"
            tomorrow = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            ledger["holds"]["shop_paid_until_epoch"] = tomorrow.timestamp() + 86400
            self.replay.alert(
                f"gem_spend: {pending['item_type']} priced {pending['cost']} took {spent} gems; "
                "shop purchases held for the rest of the UTC day"
            )
        else:
            entry["outcome"] = "verified"
        entry["gems_after"] = gems_now
        self._save_gem_purchase_ledger(ledger)

    def _try_gem_spend(self, state: GameStateSemanticV1 | None) -> bool:
        """Spend gems where they buy the most energy, at an energy wait.

        2026-09-17, by the account owner: gems are for resources, valued in
        energy (delegation/gem_economy.py).  Free shelf items are claimed first;
        then the single best offer -- an energy purchase or a shop item -- is
        taken if it clears the rate bar.  Returns True when an input was sent,
        so the caller replans on a fresh read.
        """

        if state is None or self.surface_registry is None:
            return False
        self._verify_pending_shop_purchase(state)
        raw = self._last_raw_state
        now = time.time()
        free = free_shop_offers(raw) if raw is not None else []
        if free and now - self._last_shop_visit_epoch >= SHOP_VISIT_COOLDOWN_S:
            if self._run_shop_visit(free, state):
                return True
        energy = state.resources.energy
        if energy is None or energy > ENERGY_PURCHASE_TRIGGER_ENERGY or raw is None:
            return False
        today = datetime.now(timezone.utc).date().isoformat()
        energy_spec = self.surface_registry.authorized_purchase(ENERGY_SUPPLY_SURFACE) or {}
        energy_limit = energy_spec.get("daily_limit") if isinstance(energy_spec.get("daily_limit"), int) else 0
        offers = price_offers(
            state,
            raw,
            self.policy.config_dir,
            energy_purchases_today=self._energy_purchases_on(self._energy_purchase_ledger(), today),
            energy_daily_limit=energy_limit,
        )
        hold_until = self._gem_purchase_ledger().get("holds", {}).get("shop_paid_until_epoch") or 0
        if now < float(hold_until):
            offers = [offer for offer in offers if offer.kind != "shop-item"]
        choice = choose_offer(offers, state.resources.gems)
        if choice is None:
            return False
        self.replay.note(
            f"gem_spend: chose {choice.kind} {choice.item_type or ''} for {choice.cost} gems, "
            f"worth {choice.energy_value:.0f} energy ({choice.energy_per_gem:.2f}/gem): {choice.reason}"
        )
        if choice.kind == "energy-purchase":
            return self._try_energy_purchase(state, price=choice.cost)
        return self._run_shop_visit([choice], state)

    def _run_shop_visit(self, offers: list[GemOffer], state: GameStateSemanticV1) -> bool:
        """Open the shop, scroll to its end, tap each offer's slot, leave."""

        registry = self.surface_registry
        layout = registry.purchase_layout(SHOP_SURFACE) if registry is not None else None
        prefix = registry.authorized_purchase_intents.get(SHOP_SURFACE) if registry is not None else None
        if layout is None or not isinstance(prefix, str):
            return False
        buttons = layout.get("slot_buttons_after_scroll_to_bottom") or {}
        scroll = layout.get("scroll_to_bottom") or {}
        points: list[tuple[int, int]] = []
        for offer in offers:
            row = buttons.get(str(offer.shop_type)) or []
            if offer.slot_index is None or not 0 <= offer.slot_index < len(row):
                return False
            points.append((int(row[offer.slot_index][0]), int(row[offer.slot_index][1])))
        if self._control_stop_reason() is not None:
            return False
        self._last_shop_visit_epoch = time.time()
        self._capture_round_frame()
        if self._identify_screen(self._last_frame_rgb).name != "main-board":
            return False
        try:
            cart_x, cart_y = registry.point("main-board", "shop-cart")
            close_x, close_y = registry.point(SHOP_SURFACE, "close")
            continue_x, continue_y = registry.point("reward-panel", "safe-continue")
        except (KeyError, ValueError):
            return False
        opened = self.actuator.act(
            ActionRequestV1(kind="tap", intent="navigation:main-board:shop-cart",
                            params={"x": cart_x, "y": cart_y}, settle="stable")
        )
        if opened.error:
            return False
        opened_screen = self._await_screen(SHOP_SURFACE)
        if opened_screen != SHOP_SURFACE:
            self.replay.alert(
                f"gem_spend: the cart opened {opened_screen}, not the shop, within "
                f"{PANEL_OPEN_TIMEOUT_S:.0f} s; nothing was bought"
            )
            return False
        start, end = scroll.get("from"), scroll.get("to")
        if isinstance(start, list) and isinstance(end, list):
            self.actuator.act(
                ActionRequestV1(
                    kind="drag",
                    intent="navigation:shop:scroll-to-bottom",
                    params={"x1": int(start[0]), "y1": int(start[1]), "x2": int(end[0]), "y2": int(end[1]),
                            "duration_ms": int(scroll.get("duration_ms", 450))},
                    settle="stable",
                )
            )
        ledger = self._gem_purchase_ledger()
        gems = state.resources.gems
        sent = 0
        for offer, (x, y) in zip(offers, points):
            ledger.setdefault("purchases", []).append(
                {
                    "utc_day": datetime.now(timezone.utc).date().isoformat(),
                    "at_epoch": int(time.time()),
                    "source": "automatic",
                    "item_type": offer.item_type,
                    "shop_type": offer.shop_type,
                    "slot_index": offer.slot_index,
                    "shop_item_id": offer.shop_item_id,
                    "cost": offer.cost,
                    "energy_value": round(offer.energy_value, 1),
                    "energy_per_gem": None if offer.cost == 0 else round(offer.energy_per_gem, 2),
                    "reason": offer.reason,
                    "gems_before": gems,
                    "run_id": getattr(self.replay, "run_id", None),
                    **({"outcome": "free-claim"} if offer.cost == 0 else {}),
                }
            )
            index = len(ledger["purchases"]) - 1
            self._save_gem_purchase_ledger(ledger)
            tapped = self.actuator.act(
                ActionRequestV1(
                    kind="tap",
                    intent=f"{prefix}{offer.shop_type}:{offer.slot_index}:{offer.item_type}",
                    params={"x": x, "y": y},
                    settle="stable",
                )
            )
            sent += 1
            if tapped.error:
                # The tap never reached the game (e.g. the red-line guard refused
                # it), so nothing was bought and there is nothing to verify.
                # 2026-09-17: a loop that picked up the shop door before the
                # service did waited an hour on a purchase it knew was refused.
                ledger["purchases"][index]["outcome"] = "refused"
                ledger["purchases"][index]["error"] = tapped.error
                ledger.setdefault("holds", {})["shop_paid_until_epoch"] = time.time() + SHOP_VISIT_COOLDOWN_S
                self._save_gem_purchase_ledger(ledger)
                self.replay.alert(f"gem_spend: the {offer.item_type} tap was refused ({tapped.error})")
                break
            if offer.cost > 0 and gems is not None:
                gems -= offer.cost
            self._capture_round_frame()
            screen = self._identify_screen(self._last_frame_rgb).name
            # Observed 2026-09-17: the 购买成功 overlay reads as the reward panel
            # (its 点击任意位置继续 strip), whose registered continue is harmless
            # on the shop underneath if the overlay has already gone.
            if screen == "reward-panel":
                self.actuator.act(
                    ActionRequestV1(kind="tap", intent="popup:free-claim:reward-panel:safe-continue",
                                    params={"x": continue_x, "y": continue_y}, settle="stable")
                )
            elif screen != SHOP_SURFACE:
                self.replay.alert(f"gem_spend: after buying {offer.item_type} the screen is {screen}")
                break
        self._capture_round_frame()
        if self._identify_screen(self._last_frame_rgb).name == SHOP_SURFACE:
            self.actuator.act(
                ActionRequestV1(kind="tap", intent=f"popup:close:{SHOP_SURFACE}:close",
                                params={"x": close_x, "y": close_y}, settle="stable")
            )
        self.replay.note(
            "gem_spend: shop visit tapped "
            + ", ".join(f"{offer.item_type}({offer.cost})" for offer in offers[:sent])
        )
        return sent > 0

    def _energy_purchase_ledger(self) -> dict[str, Any]:
        path = self._energy_purchases_path
        empty: dict[str, Any] = {"version": 1, "purchases": []}
        if path is None or not path.exists():
            return empty
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return empty
        if not isinstance(payload, dict) or not isinstance(payload.get("purchases"), list):
            return empty
        return payload

    def _save_energy_purchase_ledger(self, ledger: dict[str, Any]) -> None:
        path = self._energy_purchases_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _energy_purchases_on(ledger: dict[str, Any], utc_day: str) -> int:
        return sum(
            1
            for entry in ledger.get("purchases", [])
            if isinstance(entry, dict) and entry.get("utc_day") == utc_day
        )

    def _try_energy_purchase(self, state: GameStateSemanticV1 | None, *, price: int | None = None) -> bool:
        """Buy energy with gems through the owner-authorized door, if allowed now.

        Returns True only when the purchase tap was sent, so the caller replans
        on a fresh read instead of stopping on the energy wait.  The purchase
        is written to the ledger before the tap: a purchase that landed but
        could not be verified must still count, or a retry could buy a third.
        """

        if state is None or self.surface_registry is None or self._energy_purchases_path is None:
            return False
        spec = self.surface_registry.authorized_purchase(ENERGY_SUPPLY_SURFACE)
        if spec is None:
            return False
        limit = spec.get("daily_limit")
        # The price doubles through the day (20 -> 40 -> 80); the caller passes
        # the live one from the save's shop.energyBuyCost.
        cost = price if isinstance(price, int) and price > 0 else spec.get("gem_cost")
        if not isinstance(limit, int) or limit <= 0 or not isinstance(cost, int):
            return False
        energy = state.resources.energy
        gems = state.resources.gems
        if energy is None or energy > ENERGY_PURCHASE_TRIGGER_ENERGY:
            return False
        if gems is None or gems < cost:
            return False
        today = datetime.now(timezone.utc).date().isoformat()
        ledger = self._energy_purchase_ledger()
        if self._energy_purchases_on(ledger, today) >= limit:
            return False
        if self._control_stop_reason() is not None:
            return False

        self._capture_round_frame()
        if self._identify_screen(self._last_frame_rgb).name != "main-board":
            return False
        entry_element = str(spec.get("entry_element") or "energy-plus")
        try:
            plus_x, plus_y = self.surface_registry.point("main-board", entry_element)
        except (KeyError, ValueError):
            return False
        opened = self.actuator.act(
            ActionRequestV1(
                kind="tap",
                intent=f"navigation:main-board:{entry_element}",
                params={"x": plus_x, "y": plus_y},
                settle="stable",
            )
        )
        if opened.error:
            self.replay.alert(f"energy_purchase: the {entry_element} tap failed ({opened.error})")
            return False
        opened_screen = self._await_screen(ENERGY_SUPPLY_SURFACE)
        if opened_screen != ENERGY_SUPPLY_SURFACE:
            # Whatever did open is the screen guard's to leave next round.
            self.replay.alert(
                f"energy_purchase: {entry_element} opened {opened_screen}, not "
                f"{ENERGY_SUPPLY_SURFACE}; nothing was bought"
            )
            return False

        # 2026-09-21: the panel grew a second offer -- a free +15 behind an
        # advertisement -- and the paid row moved 121 px down, so the surveyed
        # centre landed on blank panel.  Two purchases on 09-20 tapped nothing
        # (both ledger rows read gems_before 13 at 10 gems each, and the balance
        # never moved), which burned that day's whole allowance.  Ask the frame
        # where the button is now, and refuse to tap when no registered layout
        # matches: a red-line surface is never tapped blind.
        self._capture_round_frame()
        buy_point = self.surface_registry.visible_variant_point(
            ENERGY_SUPPLY_SURFACE, spec["element"], self._last_frame_rgb
        )
        if buy_point is None:
            self.replay.alert(
                f"energy_purchase: no registered layout of {spec['element']} matches this "
                f"panel; nothing was bought and nothing was tapped"
            )
            return False
        buy_x, buy_y = buy_point
        ledger.setdefault("purchases", []).append(
            {
                "utc_day": today,
                "at_epoch": int(time.time()),
                "source": "automatic",
                "energy_before": energy,
                "gems_before": gems,
                "gem_cost": cost,
                "buy_point": [buy_x, buy_y],
                "run_id": getattr(self.replay, "run_id", None),
            }
        )
        self._save_energy_purchase_ledger(ledger)
        bought = self.actuator.act(
            ActionRequestV1(
                kind="tap",
                intent=str(spec["intent"]),
                params={"x": buy_x, "y": buy_y},
                settle="stable",
            )
        )
        self._capture_round_frame()
        if self._identify_screen(self._last_frame_rgb).name == ENERGY_SUPPLY_SURFACE:
            # Observed 2026-09-17: the panel closes itself after a purchase.
            # If it did not, leave it the only way the red line allows.
            close_element = self.surface_registry.forbidden_close_element(ENERGY_SUPPLY_SURFACE)
            if close_element is not None:
                close_x, close_y = self.surface_registry.point(ENERGY_SUPPLY_SURFACE, close_element)
                self.actuator.act(
                    ActionRequestV1(
                        kind="tap",
                        intent=f"popup:close:{ENERGY_SUPPLY_SURFACE}:{close_element}",
                        params={"x": close_x, "y": close_y},
                        settle="stable",
                    )
                )
        # The ledger is written before the tap so an unverifiable purchase
        # still counts.  A purchase that provably did not happen is a different
        # thing: if the save says the gems are still there, the tap bought
        # nothing, and charging the day's allowance for it would throw away the
        # energy this account is actually short of.  Release it and say so.
        spent = self._observed_gem_spend(gems)
        if spent is False:
            entry = ledger["purchases"].pop()
            self._save_energy_purchase_ledger(ledger)
            self.replay.alert(
                f"energy_purchase: tapped {entry['buy_point']} and the gem balance stayed at "
                f"{gems}; nothing was bought, so the allowance is not spent "
                f"({self._energy_purchases_on(ledger, today)}/{limit} today, UTC)"
            )
            return False
        self.replay.note(
            f"energy_purchase: bought +{spec.get('energy_gain')} energy for {cost} gems "
            f"at energy {energy}, gems {gems} "
            f"({self._energy_purchases_on(ledger, today)}/{limit} today, UTC)"
            + ("" if bought.error is None else f"; tap reported {bought.error}")
        )
        return bought.error is None

    def _observed_gem_spend(self, gems_before: int) -> bool | None:
        """Did the gem balance actually drop?  ``None`` when the save cannot say."""

        try:
            _result, state, _seconds = self._read_semantic(flush="none")
        except Exception as error:  # a read failure is not evidence either way
            self.replay.note(f"energy_purchase: gem balance unreadable ({error})")
            return None
        gems_after = getattr(getattr(state, "resources", None), "gems", None)
        if not isinstance(gems_after, int):
            return None
        return gems_after < gems_before

    def _note_energy_wait(self) -> LoopStopReason:
        """Record the legal energy wait with the time play can resume."""

        report = self.exhaustion.report
        self.replay.note(
            "planner stopped on a legal energy wait: needs "
            f"{report.energy_needed} energy, actionable again at "
            f"{_epoch_to_iso(report.energy_enough_at)}, full at "
            f"{_epoch_to_iso(report.energy_full_at)}"
        )
        return "waiting_for_energy"

    def _app_is_gone(self) -> bool:
        """Whether the game process has disappeared, if anything can tell us."""

        probe = self.app_alive_probe
        if probe is None:
            return False
        try:
            return not probe()
        except Exception:
            return False

    def _last_input_dead_adb_contrast(self) -> dict[str, Any] | None:
        """The adb contrast tap from the actuator's most recent input-dead event.

        The contrast is the mode discriminator the guardian's automatic
        recovery dispatches on: a frame that still moved under the adb tap
        means the game was alive and only the MuMu IPC channel had died; a
        frame that did not move under either channel means the process itself
        had wedged.
        """

        events = getattr(self.actuator, "input_health_events", None)
        if not isinstance(events, list):
            return None
        for event in reversed(events):
            if isinstance(event, dict) and event.get("outcome") == "input_dead_unrecovered":
                contrast = event.get("adb_contrast")
                if isinstance(contrast, dict):
                    return dict(contrast)
                return None
        return None

    def _relaunch_after_crash(self, now: float) -> LoopStopReason | None:
        """Bring the app back, or stop if it keeps needing bringing back.

        F4-10, planner ruling of 2026-09-07.  A game process that goes away is
        a recoverable device event: the relaunch sends the launcher intent and
        waits for the board, and no business input goes out while it waits.
        The round it interrupted is void.  Three of these inside an hour is not
        a transient any more, and the run says so instead of relaunching again.
        """

        self._app_relaunch_times = [
            stamp
            for stamp in self._app_relaunch_times
            if now - stamp < APP_RELAUNCH_WINDOW_S
        ]
        if len(self._app_relaunch_times) >= APP_RELAUNCH_LIMIT:
            self.replay.alert(
                "app_crash_recurring: the app was relaunched "
                f"{len(self._app_relaunch_times)} times inside "
                f"{int(APP_RELAUNCH_WINDOW_S)} s"
            )
            return "app_crash_recurring"
        relaunch = self.app_relaunch
        if relaunch is None:
            return None
        self._app_relaunch_times.append(now)
        try:
            outcome = relaunch()
        except Exception as exc:
            self.replay.alert(
                f"app_relaunch failed: {type(exc).__name__}: {exc}"
            )
            return "app_crash_recurring"
        self.app_relaunch_recoveries += 1
        self.replay.note(
            "app_relaunch: the game process was gone and was brought back "
            f"({outcome}); this round is void"
        )
        return None

    def _handle_read_failure(
        self, exc: BaseException, *, now: float
    ) -> LoopStopReason | None:
        """What a failed read means, and whether the run can carry on.

        ``None`` means the app had died, was brought back, and the round it
        interrupted is void: the loop takes a fresh read and keeps playing.
        """

        stop_reason = self._read_failure_stop(exc)
        if stop_reason == "save_channel_failed" and self._app_is_gone():
            crash_stop = self._relaunch_after_crash(now)
            if crash_stop is None:
                return None
            self.replay.alert(f"{crash_stop}: {type(exc).__name__}")
            return crash_stop
        self.replay.alert(f"{stop_reason}: {type(exc).__name__}")
        return stop_reason

    def _read_failure_stop(self, exc: BaseException) -> LoopStopReason:
        """Name what actually failed: the save channel, or the screen.

        F4-09 1.5 ruled that a save read that fails is a channel failure, not
        an unregistered surface, because reporting it as one sends a reader
        looking for a screen to register while the frame beside it shows an
        ordinary board.  That ruling was implemented for ``SaveChannelError``
        only, and the rest of the reader's own failures -- a snapshot that will
        not pull, a decode that fails, a flush the app does not come back from
        -- still arrived as ``unknown_screen``.

        The 2026-09-07 04:48Z segment is the second time that has ended a run:
        sixteen healthy rounds, thirty-nine of forty predictions confirmed,
        three deliveries, and then ``unknown_screen: FlushRecoveryError``.  The
        whole reader family maps onto the channel verdict, which is the one
        with a retry and an exit code that says to look at the device.
        """

        return (
            "save_channel_failed"
            if isinstance(exc, (SaveChannelError, SaveReaderError))
            else "unknown_screen"
        )

    def _plain_item_cell(
        self, state: GameStateSemanticV1
    ) -> CellPositionV1 | None:
        """A plain, interactable, non-generator item: one tap only selects it.

        Generators are excluded the way the F4-05 probe excludes them: a
        selected generator produces on the next tap and spends energy, and this
        tap exists precisely to cost nothing.
        """

        for row in state.board.cells:
            for cell in row:
                if cell.kind != "item" or cell.generator is not None:
                    continue
                if not cell.interactable or cell.item_type is None:
                    continue
                return CellPositionV1(row=cell.row, col=cell.col)
        return None

    def _apply_cold_start_policy(
        self,
        state: GameStateSemanticV1,
        read_ready_at: float,
    ) -> dict[str, Any] | None:
        """Do whatever this arm asks for before the first business input.

        F4-11 section 1.2.  Only rounds that took their own read reach this:
        every segment round one, and every round after a read failure.  Returns
        the record of what it did, or None for the arm that does nothing.
        """

        policy = self.cold_start_policy
        if policy == "status_quo":
            return None
        if policy == "settle":
            ready_after = read_ready_at + self.cold_start_settle_ms / 1000
            remaining_s = max(ready_after - time.perf_counter(), 0.0)
            if remaining_s:
                self.sleep(remaining_s)
            return {
                "policy": policy,
                "settle_ms": remaining_s * 1000,
                "requested_settle_ms": self.cold_start_settle_ms,
            }
        position = self._plain_item_cell(state)
        if position is None:
            return {"policy": policy, "warm_tap": False, "reason": "no plain item cell"}
        x, y = self.geometry.cell_center(position.row - 1, position.col - 1)
        request = ActionRequestV1(
            kind="tap",
            intent="warmup:select-plain-item",
            params={"x": int(x), "y": int(y)},
            capture_pre=True,
            capture_post=True,
            settle="stable",
        )
        started = time.perf_counter()
        try:
            result = self.actuator.act(request)
        except Exception as exc:  # noqa: BLE001 - a warm-up must never end a run
            self.replay.note(f"cold_start_warm_tap failed: {type(exc).__name__}: {exc}")
            return {"policy": policy, "warm_tap": False, "reason": type(exc).__name__}
        self.cold_start_warmups += 1
        return {
            "policy": policy,
            "warm_tap": True,
            "cell": [position.row, position.col],
            "tap": [int(x), int(y)],
            "settled": bool(getattr(result, "settled", False)),
            "elapsed_ms": (time.perf_counter() - started) * 1000,
            "action_id": getattr(result, "action_id", None),
        }

    def _read_semantic(
        self,
        *,
        flush: Literal["home_and_return", "none"],
    ) -> tuple[Any, GameStateSemanticV1, float]:
        started = time.perf_counter()
        result = self.save_channel.read_state(
            flush=flush,
            include_board=True,
        )
        elapsed = time.perf_counter() - started
        semantic = derive_game_state_semantics(result.state, self.policy.config_dir)
        if not _board_is_valid(semantic):
            raise ValueError("board matrix is empty or structurally invalid")
        # The overlay guard runs between reads and has no state of its own; it
        # needs the last authoritative one to tell an earned 好评如潮 grant from
        # a panel that merely appeared.
        self._last_semantic_state = semantic
        # The raw read, for the tables the semantic model does not carry (the
        # shop): gem spending prices its offers from it.
        self._last_raw_state = getattr(result, "state", None)
        return result, semantic, _read_total_s(result, elapsed)

    def _capture_round_frame(self) -> tuple[Any, str, float]:
        if self.backend == "mumu_ipc":
            pixels, width, height, channels, screenshot_ms = self.actuator.screenshot_raw()
            channel_count = 4 if channels in {"RGBA", "BGRA"} else 3
            if len(pixels) == int(width) * int(height) * channel_count:
                array = np.frombuffer(pixels, dtype=np.uint8).reshape(
                    int(height), int(width), channel_count
                )
                if channels == "BGRA":
                    array = array[:, :, [2, 1, 0, 3]]
                self._last_frame_rgb = frame_to_rgb(array)
            screenshot_sha = hashlib.sha256(pixels).hexdigest()
            frame = self.replay.add_frame_raw(
                pixels,
                width,
                height,
                channels=channels,
                trigger="manual",
            )
            return frame, screenshot_sha, screenshot_ms
        screenshot, screenshot_ms = self.actuator.screenshot()
        if self.surface_registry is not None:
            self._last_frame_rgb = frame_to_rgb(screenshot)
        screenshot_sha = hashlib.sha256(screenshot).hexdigest()
        frame = self.replay.add_frame(screenshot, trigger="manual")
        return frame, screenshot_sha, screenshot_ms

    def _act(self, action: PlannedActionV1) -> ActionResultV1:
        if self.backend == "mumu_ipc":
            return self.actuator.act(action.request)
        return self.actuator.act(action.request)

    def _guard_main_board(
        self,
        frame: Any,
        screenshot_sha: str,
        screenshot_ms: float,
    ) -> tuple[
        Any,
        str,
        float,
        str,
        list[PopupHandlingRecordV1],
        list[ActionResultV1],
    ]:
        """Run the guard, then measure what its claims and closes moved."""

        (
            frame,
            screenshot_sha,
            screenshot_ms,
            screen_name,
            records,
            results,
        ) = self._guard_main_board_inner(frame, screenshot_sha, screenshot_ms)
        return (
            frame,
            screenshot_sha,
            screenshot_ms,
            screen_name,
            self._attach_claim_save_evidence(records),
            results,
        )

    def _attach_claim_save_evidence(
        self,
        records: list[PopupHandlingRecordV1],
    ) -> list[PopupHandlingRecordV1]:
        """Stamp one forced save read on every claim-class action of this guard.

        F4-09 section 1.1.  The read is taken after the guard has finished
        acting, never between the steps of one surface, so a HOME-and-return
        cannot land in the middle of a claim sequence.  Everything the guard did
        inside the window is named on the record, so a shared delta is
        attributable rather than silently assigned to one action.
        """

        positions = [
            index
            for index, record in enumerate(records)
            if record.kind in CLAIM_SAVE_EVIDENCE_KINDS
        ]
        if not positions:
            return records
        group_action_ids = [records[index].action_id for index in positions]
        group_id = group_action_ids[0]
        window = [
            f"{record.screen}:{record.kind}:{record.element or ''}"
            for record in records
        ]
        before = self._claim_evidence_before
        delta: PopupSaveDeltaV1
        if before is None:
            delta = PopupSaveDeltaV1(
                available=False,
                reason="no authoritative read preceded the guard in this round",
                group_id=group_id,
                group_action_ids=group_action_ids,
                guard_actions_in_window=window,
            )
        else:
            try:
                _result, after, read_s = self._read_semantic(flush="home_and_return")
            except Exception as exc:  # pragma: no cover - device failure path
                delta = PopupSaveDeltaV1(
                    available=False,
                    reason=f"forced save read failed: {type(exc).__name__}",
                    group_id=group_id,
                    group_action_ids=group_action_ids,
                    guard_actions_in_window=window,
                )
            else:
                self._claim_evidence_reads += 1
                self._claim_evidence_before = after
                delta = build_claim_save_delta(
                    before,
                    after,
                    group_id=group_id,
                    group_action_ids=group_action_ids,
                    guard_actions_in_window=window,
                    read_s=read_s,
                )
        updated = list(records)
        for index in positions:
            updated[index] = updated[index].model_copy(update={"save_delta": delta})
        return updated

    def _guard_main_board_inner(
        self,
        frame: Any,
        screenshot_sha: str,
        screenshot_ms: float,
    ) -> tuple[
        Any,
        str,
        float,
        str,
        list[PopupHandlingRecordV1],
        list[ActionResultV1],
    ]:
        """Resolve registered overlays/routes and wait through transition frames."""

        self._forbidden_surface_stop = None
        self._unexplored_surface_stop = None
        if self.surface_registry is None or self._last_frame_rgb is None:
            return frame, screenshot_sha, screenshot_ms, "unknown", [], []
        records: list[PopupHandlingRecordV1] = []
        results: list[ActionResultV1] = []
        attempts: dict[str, int] = {}
        navigation_hops = 0
        tutorial_sequence_active = False
        tutorial_sequence_steps = 0
        handled_board_tutorial_intents: set[str] = set()
        forbidden_closes: dict[str, int] = {}
        screen = self._identify_screen(self._last_frame_rgb)
        board_tutorial = _registered_board_tutorial_request(
            self._last_frame_rgb,
            self.surface_registry,
            screen.name,
        )
        board_panel = _registered_board_panel_request(
            self._last_frame_rgb,
            self.surface_registry,
        )
        if board_tutorial is None and board_panel is None and (
            screen.name in {"unknown", "overlay-unknown"}
            or self.surface_registry.is_transition(screen.name)
        ):
            (
                frame,
                screenshot_sha,
                screenshot_ms,
                screen,
            ) = self._wait_for_guard_surface(
                frame,
                screenshot_sha,
                screenshot_ms,
                screen,
            )
        (
            frame,
            screenshot_sha,
            screenshot_ms,
            screen,
        ) = self._stable_screen(frame, screenshot_sha, screenshot_ms, screen)
        for _step in range(MAX_SCREEN_GUARD_STEPS):
            if screen.name != STORY_DIALOGUE_SURFACE:
                # A different surface means the dialogue this budget was
                # counting is over, whether it ended or the run left it.
                self._story_advance_taps = 0
            board_tutorial = _registered_board_tutorial_request(
                self._last_frame_rgb,
                self.surface_registry,
                screen.name,
            )
            board_panel = _registered_board_panel_request(
                self._last_frame_rgb,
                self.surface_registry,
            )
            if (
                board_tutorial is not None
                and board_tutorial.intent in handled_board_tutorial_intents
            ):
                board_tutorial = None
            forbidden = self.surface_registry.detect_forbidden_surface(
                self._last_frame_rgb,
                screen=screen,
            )
            if forbidden is not None:
                # A registered red-line surface may only ever be closed once.
                board_tutorial = None
                board_panel = None
                close_action = action_for_screen(
                    screen,
                    self.surface_registry,
                    attempt=0,
                )
                if (
                    close_action is None
                    or close_action.kind != "close"
                    or (
                        forbidden.close_element is not None
                        and close_action.element != forbidden.close_element
                    )
                    or forbidden_closes.get(forbidden.name, 0) >= 1
                ):
                    self._forbidden_surface_stop = forbidden
                    break
                forbidden_closes[forbidden.name] = 1
                if self._note_forbidden_close(forbidden.name):
                    self._forbidden_recurring_stop = forbidden.name
                    break
            unexplored = self._unexplored_registered_surface(screen.name)
            if unexplored is not None:
                # F4-13: this surface is registered but still marked
                # unexplored.  The guard spends no wired departure on it --
                # it stays on screen exactly as found so the agent handler's
                # exploration segment starts from this frame.
                self._unexplored_surface_stop = unexplored
                break
            if (
                screen.name == TUTORIAL_OVERLAY_SURFACE
                and board_panel is None
                and not self._board_full_under_guide
                and detect_delivery_control_centers(self._last_frame_rgb)
            ):
                # F4-10, planner ruling of 2026-09-07.  The order row is showing
                # a Complete button, so the action the game is waiting for is
                # that delivery, whatever the overlay's registered target is.
                # The 03:58Z frame that ended an eight-hour run is exactly this:
                # a deliverable order at +14 and a guide the run spent five taps
                # on instead.  Hand the round back to the planner, which ranks
                # delivery first, and judge the frame again next round.
                self._set_actuator_deliver_allowance(True)
                self.replay.note(
                    "deliver_before_following: the order row shows a Complete "
                    "button, so the delivery goes first and the guide is judged "
                    "again after it"
                )
                return (
                    frame,
                    screenshot_sha,
                    screenshot_ms,
                    "main-board",
                    records,
                    results,
                )
            self._set_actuator_deliver_allowance(False)
            if (
                self._board_full_under_guide
                and screen.name == TUTORIAL_OVERLAY_SURFACE
                and board_tutorial is None
                and board_panel is None
            ):
                # F4-09: this guide is already known not to answer, and the
                # save has already proved the board is behind it.  Spending the
                # tutorial budget on it again every round would leave no round
                # for the merging and selling that ends the state.
                return (
                    frame,
                    screenshot_sha,
                    screenshot_ms,
                    "main-board",
                    records,
                    results,
                )
            if (
                screen.name == "main-board"
                and board_tutorial is None
                and board_panel is None
            ):
                return (
                    frame,
                    screenshot_sha,
                    screenshot_ms,
                    screen.name,
                    records,
                    results,
                )
            if board_tutorial is None and board_panel is None and (
                screen.name in {"unknown", "overlay-unknown"}
                or self.surface_registry.is_transition(screen.name)
            ):
                break
            attempt = attempts.get(screen.name, 0)
            popup = (
                None
                if board_tutorial is not None or board_panel is not None
                else action_for_screen(
                    screen,
                    self.surface_registry,
                    attempt=attempt,
                    rave_reviews_reward_earned=_rave_reviews_reward_earned(
                        self._last_semantic_state
                    ),
                )
            )
            if (
                popup is None
                and board_tutorial is None
                and board_panel is None
                and attempt == 0
            ):
                # 2026-09-08.  Guard-only departure wiring (see
                # popup_handler.guard_departure_action): closing the main-task
                # panel a dead main-task session left standing.  Attempt 0
                # only -- one close per guard pass, and the handoff budget in
                # run() bounds the rounds a panel may survive.
                popup = guard_departure_action(screen, self.surface_registry)
            if board_tutorial is not None:
                request = board_tutorial
                record_kind = "tutorial-follow"
                record_element = request.intent.rsplit(":", maxsplit=1)[-1]
            elif board_panel is not None:
                request = board_panel
                record_kind = "close"
                record_element = "close-only"
            elif popup is not None:
                attempts[screen.name] = attempt + 1
                request = popup.request
                record_kind = popup.kind
                record_element = popup.element
            else:
                remaining_hops = MAX_SCREEN_GUARD_NAVIGATION_HOPS - navigation_hops
                path = self.surface_registry.verified_tap_path(
                    screen.name,
                    max_hops=remaining_hops,
                )
                if not path:
                    break
                hop = path[0]
                navigation_hops += 1
                request = ActionRequestV1(
                    kind="tap",
                    intent=f"navigation:return-to-board:{hop.source}->{hop.target}",
                    params={"x": hop.x, "y": hop.y},
                    settle="stable",
                )
                record_kind = "navigation"
                record_element = f"{hop.source}->{hop.target}"
            if tutorial_sequence_active and tutorial_sequence_steps >= MAX_TUTORIAL_SEQUENCE_STEPS:
                # F4-09.  The budget is spent and the guide still has the
                # screen.  The 2026-09-07 01:22Z run showed why the
                # byte-identical test alone is not enough: the arrow over the
                # reward chest animates, so consecutive frames differ while
                # nothing about the situation does.  What has not moved is the
                # surface.
                # Any surface, not only the tutorial overlay: the 2026-09-07
                # 01:42Z stop was a board-story-prompt whose bubble points at
                # the event badge, and five taps on the registered advance
                # point never dismissed it.  The run should say that rather
                # than report the vaguer not_on_board.
                self._tutorial_follow_stalled = (
                    f"{screen.name}:{record_element}:"
                    f"{request.params.get('x')},{request.params.get('y')}"
                )
                self._stalled_kind = record_kind
                if screen.name == TUTORIAL_OVERLAY_SURFACE:
                    self._board_full_guide_target = request
                break
            if record_kind == "story-advance":
                if self._story_advance_taps >= MAX_STORY_ADVANCE_TAPS:
                    self._tutorial_follow_stalled = (
                        f"{screen.name}:{record_element}:"
                        f"{request.params.get('x')},{request.params.get('y')}"
                    )
                    self._stalled_kind = record_kind
                    break
                self._story_advance_taps += 1
            tracks_stall = record_kind in {"tutorial-follow", "story-advance"} or (
                screen.name in GUARD_HANDOFF_SURFACES
            )
            if tracks_stall:
                # 2026-09-08: the handoff panels join the byte-identical stall
                # test.  A close or confirm that leaves the frame untouched for
                # three taps cannot start working on the fourth, and the run
                # says so (as a stall, with the surface named) instead of
                # burning the whole handoff budget first.
                tutorial_sequence_active = record_kind == "tutorial-follow"
                stall_key = (
                    f"{screen.name}:{record_element}:"
                    f"{request.params.get('x')},{request.params.get('y')}"
                )
                if record_kind == "tutorial-follow":
                    # Only a guide can put the run into board_full_under_guide;
                    # a story dialogue is not one, and handing it that target
                    # would let the state fire on the wrong surface.
                    self._board_full_guide_target = request
                if self._guided_target_stalls.get(stall_key, 0) >= (
                    guided_target_stall_budget(stall_key)
                ):
                    # F4-09: this guide has been tapped its whole budget of
                    # times against frames that did not change by a byte.
                    # Tapping it again cannot help, and the run must not spend
                    # a segment on it.  F4-10 ruling A: the budget is one for
                    # the left-rail slot.
                    self._tutorial_follow_stalled = stall_key
                    self._stalled_kind = record_kind
                    break
            else:
                stall_key = None
            frame_before_action = screenshot_sha
            result = self.actuator.act(request)
            if tutorial_sequence_active:
                tutorial_sequence_steps += 1
            records.append(
                PopupHandlingRecordV1(
                    screen=screen.name,
                    kind=record_kind,
                    element=record_element,
                    action_id=result.action_id,
                )
            )
            results.append(result)
            if result.error:
                break
            frame, screenshot_sha, capture_ms = self._capture_round_frame()
            screenshot_ms += capture_ms
            if stall_key is not None:
                self._guided_target_stalls[stall_key] = (
                    self._guided_target_stalls.get(stall_key, 0) + 1
                    if screenshot_sha == frame_before_action
                    else 0
                )
            if self._last_frame_rgb is None:
                break
            screen = self._identify_screen(self._last_frame_rgb)
            (
                frame,
                screenshot_sha,
                screenshot_ms,
                screen,
            ) = self._stable_screen(frame, screenshot_sha, screenshot_ms, screen)
            if board_tutorial is not None:
                handled_board_tutorial_intents.add(board_tutorial.intent)
                remaining_polls = int(SCREEN_GUARD_TIMEOUT_S / SCREEN_GUARD_POLL_S)
                while remaining_polls > 0:
                    next_tutorial = _registered_board_tutorial_request(
                        self._last_frame_rgb,
                        self.surface_registry,
                        screen.name,
                    )
                    if (
                        next_tutorial is not None
                        and next_tutorial.intent not in handled_board_tutorial_intents
                    ):
                        break
                    if screen.name == "main-board" and next_tutorial is None:
                        break
                    if (
                        screen.name not in {"unknown", "story-dialogue"}
                        and not self.surface_registry.is_transition(screen.name)
                    ):
                        break
                    remaining_polls -= 1
                    self.sleep(SCREEN_GUARD_POLL_S)
                    frame, screenshot_sha, capture_ms = self._capture_round_frame()
                    screenshot_ms += capture_ms
                    if self._last_frame_rgb is None:
                        break
                    screen = self._identify_screen(self._last_frame_rgb)
            else:
                (
                    frame,
                    screenshot_sha,
                    screenshot_ms,
                    screen,
                ) = self._wait_for_guard_surface(
                    frame,
                    screenshot_sha,
                    screenshot_ms,
                    screen,
                )
        return (
            frame,
            screenshot_sha,
            screenshot_ms,
            screen.name,
            records,
            results,
        )

    def _needs_confirmation(self, screen: Any) -> bool:
        """Whether this verdict has to hold before the guard acts on it.

        The board and the restaurant scene do not: neither stops the run nor
        starts a follow, and the restaurant scene carries a verified route back
        to the board.  Unknown and registered transitions do not either, but
        for the opposite reason -- ``_wait_for_guard_surface`` already polls
        those to their own timeouts, which is a longer version of this, and
        running both would poll twice for one question.
        """

        if self.surface_registry is None:
            return False
        if screen.name in SETTLED_SURFACES:
            return False
        if screen.name in {"unknown", "overlay-unknown"}:
            return False
        return not self.surface_registry.is_transition(screen.name)

    def _stable_screen(
        self,
        frame: Any,
        screenshot_sha: str,
        screenshot_ms: float,
        screen: Any,
    ) -> tuple[Any, str, float, Any]:
        """Make a stopping or following verdict prove itself on a second frame.

        F4-10, planner ruling of 2026-09-07.  Such a verdict has to come back
        the same from two frames at least ``SURFACE_DEBOUNCE_HOLD_S`` apart,
        polled every ``SURFACE_DEBOUNCE_POLL_S`` and given up on after
        ``SURFACE_DEBOUNCE_TIMEOUT_S``.  A verdict that changes restarts the
        clock, so the answer is always the latest one that held.
        """

        if not self._needs_confirmation(screen):
            return frame, screenshot_sha, screenshot_ms, screen
        holding = screen
        held_s = 0.0
        elapsed_s = 0.0
        while held_s < SURFACE_DEBOUNCE_HOLD_S and elapsed_s < SURFACE_DEBOUNCE_TIMEOUT_S:
            self.sleep(SURFACE_DEBOUNCE_POLL_S)
            elapsed_s += SURFACE_DEBOUNCE_POLL_S
            frame, screenshot_sha, capture_ms = self._capture_round_frame()
            screenshot_ms += capture_ms
            if self._last_frame_rgb is None:
                break
            current = self._identify_screen(self._last_frame_rgb)
            if not self._needs_confirmation(current):
                return frame, screenshot_sha, screenshot_ms, current
            if current.name == holding.name:
                held_s += SURFACE_DEBOUNCE_POLL_S
            else:
                holding = current
                held_s = 0.0
        if holding.name != screen.name:
            self.replay.note(
                f"surface_debounce: the first frame read {screen.name} and the "
                f"verdict settled on {holding.name}"
            )
        return frame, screenshot_sha, screenshot_ms, holding

    def _wait_for_guard_surface(
        self,
        frame: Any,
        screenshot_sha: str,
        screenshot_ms: float,
        screen: Any,
    ) -> tuple[Any, str, float, Any]:
        """Poll after a guard action until a registered non-transition surface."""

        if self.surface_registry is None:
            return frame, screenshot_sha, screenshot_ms, screen
        remaining_polls = max(
            1,
            int(SCREEN_GUARD_TIMEOUT_S / SCREEN_GUARD_POLL_S),
        )
        extended_transition: str | None = None
        saw_registered_transition = self.surface_registry.is_transition(screen.name)
        while screen.name in {"unknown", "overlay-unknown"} or (
            self.surface_registry.is_transition(screen.name)
        ):
            timeout_s = self.surface_registry.transition_timeout_s(
                screen.name,
                default=SCREEN_GUARD_TIMEOUT_S,
            )
            if timeout_s > SCREEN_GUARD_TIMEOUT_S and screen.name != extended_transition:
                remaining_polls = max(
                    remaining_polls,
                    int(timeout_s / SCREEN_GUARD_POLL_S),
                )
                extended_transition = screen.name
            if remaining_polls <= 0:
                break
            remaining_polls -= 1
            self.sleep(SCREEN_GUARD_POLL_S)
            frame, screenshot_sha, capture_ms = self._capture_round_frame()
            screenshot_ms += capture_ms
            if self._last_frame_rgb is None:
                break
            screen = self._identify_screen(self._last_frame_rgb)
            saw_registered_transition = (
                saw_registered_transition or self.surface_registry.is_transition(screen.name)
            )
        if (
            saw_registered_transition
            and screen.name not in {"unknown", "overlay-unknown"}
            and not self.surface_registry.is_transition(screen.name)
        ):
            refresh = getattr(self.actuator, "refresh_display_id", None)
            if callable(refresh):
                refresh(reason="transition-ended:screen-guard")
        return frame, screenshot_sha, screenshot_ms, screen

    # -- F4-12 Phase 2: opt-in behavior state machine wiring ------------------
    #
    # With ``use_behavior_sm`` the round's plan comes from the pure
    # ``behavior_sm.decide(frame)`` instead of ``plan_batch``: the assembly
    # below builds the frame from the same values the record assembly uses,
    # and the translator renders the one decided action with planner.py's own
    # request/prediction recipes so execution, adjudication and the record
    # keep their existing shapes.  The default False keeps the legacy path
    # byte-identical.

    def _sell_filter_entries(self, state: GameStateSemanticV1) -> frozenset[str]:
        """The real ItemModelConfig sell filter, as frame entries.

        F4-12 appendix A ruling 1.  planner.py:828-833 excludes a sell
        candidate when its type is Rare, carries a ProtectLevel, or is special
        without a coin collection, and when its chain serves an open order.
        The per-type exclusions land as ``type:<id>`` entries; the order
        chains land as ``chain:<id>`` entries keyed the semantics way
        (semantics.py:319-324: the same-segment walk root shares the type's
        own ``// 100`` segment), which is the key ``cell.chain_id`` carries.
        """

        metadata = _configured_item_metadata(self.policy.config_dir)
        entries = {
            f"type:{type_id}"
            for type_id, cell in metadata.items()
            if cell.rare
            or cell.protect_level is not None
            or (cell.special and cell.collect_coins is None)
        }
        for order in state.orders:
            for required in order.required_items:
                entries.add(f"chain:{_segment(required.type)}")
        return frozenset(entries)

    @staticmethod
    def _deliverable_slots(
        state: GameStateSemanticV1,
        planning_policy: PolicyV1,
    ) -> frozenset[int]:
        """The slots whose Complete tap the executor can actually place.

        planner.py:932-955 with planner.py:661-695: a fulfillable order is
        only plannable when its slot has a pixel-detected control override or
        the calibrated order geometry covers it; everything else lands in
        blocked_actions as blocked_missing_geometry.  The behavior frame
        carries this set so decide() never picks an unexecutable delivery.
        """

        slots = set(planning_policy.delivery_control_overrides)
        geometry = planning_policy.order_geometry
        if geometry is not None:
            display_order = _display_slot_order(state)
            for order in state.orders:
                if order.slot in slots:
                    continue
                try:
                    center = geometry.order_card_center(
                        order.slot, slot_order=display_order
                    )
                except TypeError:
                    center = geometry.order_card_center(order.slot)
                except (IndexError, ValueError):
                    continue
                if isinstance(center, (tuple, list)) and len(center) == 2:
                    slots.add(order.slot)
        return frozenset(slots)

    def _assemble_decision_frame(
        self,
        before: GameStateSemanticV1,
        *,
        current_screen: str,
        screen_guard_actions: list[PopupHandlingRecordV1],
        business_refuted_streak: int,
        just_landed: frozenset[tuple[int, int]],
        cumulative_wait_s: float,
        max_wait_s: int | None,
        deliveries_executed: int,
        conditions: StopConditionsV1,
        chest_states: tuple[ChestRuntimeStateV1, ...],
        deliverable_slots: frozenset[int],
    ) -> BehaviorFrame:
        """The pre-action frame ``decide`` reads on the behavior-sm path.

        Same sources as the Phase 0 record assembly below, minus the values
        only the post-round record knows.  ``rounds_remaining`` stays None on
        purpose: the run loop's own bound enforces max_rounds, and a 0 here
        would make decide stop the final round before it plays.
        """

        round_popup_attempts: dict[str, int] = {}
        for guard_action in screen_guard_actions:
            round_popup_attempts[guard_action.screen] = (
                round_popup_attempts.get(guard_action.screen, 0) + 1
            )
        detector = self.exhaustion
        return BehaviorFrame(
            save=before,
            surface=current_screen,
            counters=BehaviorCounters(
                guard_steps=len(screen_guard_actions),
                handoff_rounds=dict(self._guard_handoff_rounds),
                story_taps=self._story_advance_taps,
                refuted_streak=business_refuted_streak,
                guided_stalls=dict(self._guided_target_stalls),
                forbidden_close_times={
                    surface: list(times)
                    for surface, times in self._forbidden_close_times.items()
                },
                relaunch_times=list(self._app_relaunch_times),
                suppressed_pairs=dict(self._suppressed_merge_pairs),
                suppressed_cells=dict(self._suppressed_generator_cells),
                held_positions=sorted(just_landed),
                cumulative_wait_s=cumulative_wait_s,
                popup_attempts=round_popup_attempts,
                consecutive_wait_rounds=detector.consecutive_wait_rounds,
                waiting_for_energy=detector.waiting_for_energy,
                energy_rechecked_without_action=(
                    detector.energy_rechecked_without_action
                ),
                deliveries_executed=deliveries_executed,
            ),
            clocks=BehaviorClocks(
                now=time.time(),
                energy_enough_at=detector.report.energy_enough_at,
                energy_full_at=detector.report.energy_full_at,
                rounds_remaining=None,
                wait_budget_remaining_s=(
                    None
                    if max_wait_s is None
                    else max(max_wait_s - cumulative_wait_s, 0.0)
                ),
                now_monotonic=time.monotonic(),
            ),
            signals=BehaviorSignals(
                board_full_under_guide=self._board_full_under_guide,
            ),
            policy=BehaviorPolicy(
                version=self.policy.version,
                enable_main_task=self.policy.enable_main_task,
                max_main_tasks_per_round=self.policy.max_main_tasks_per_round,
                max_actions_per_round=self.policy.max_actions_per_round,
                max_deliveries_per_run=self.policy.max_deliveries_per_run,
                max_energy_collects_per_round=(
                    self.policy.max_energy_collects_per_round
                ),
                max_sells_per_round=self.policy.max_sells_per_round,
                energy_recovery_seconds=self.policy.energy_recovery_seconds,
                max_consecutive_refuted=conditions.max_consecutive_refuted,
                stop_on_exhaustion=conditions.stop_on_exhaustion,
                stop_on_resource_wait=conditions.stop_on_resource_wait,
                chest_states=chest_states,
                suppressed_chest_cells=frozenset(self._suppressed_chest_cells),
            ),
            sell_protected_chains=self._sell_filter_entries(before),
            delivery_control_visible=(
                None
                if self._last_frame_rgb is None
                else bool(detect_delivery_control_centers(self._last_frame_rgb))
            ),
            deliverable_slots=deliverable_slots,
        )

    @staticmethod
    def _board_cell(
        state: GameStateSemanticV1,
        position: tuple[int, int],
    ) -> Any | None:
        row, col = position
        for cell_row in state.board.cells:
            for cell in cell_row:
                if cell.row == row and cell.col == col:
                    return cell
        return None

    def _behavior_wait_plan(
        self,
        decision: Any,
        before: GameStateSemanticV1,
        planning_policy: PolicyV1,
        now: float,
    ) -> PlanV1:
        """Redeem a WaitUntil as the planner's own wait action (planner.py:1562-1616).

        The epoch is honored up to the planner's per-round wait quantum, so
        the round pacing and the loop's cumulative wait accounting keep their
        legacy shape; the next round re-decides either way.
        """

        wait_s = min(self.policy.energy_recovery_seconds, 60)
        ms = int(round(min(max(decision.epoch - now, 0.0), float(wait_s)) * 1000))
        if decision.reason.startswith("generator inventory depleted:"):
            intent = "wait:generator_inventory_recovery"
            exhausted = next(
                (
                    cell
                    for cell_row in before.board.cells
                    for cell in cell_row
                    if cell.kind == "generator"
                    and cell.generator is not None
                    and cell.generator.inventory_exhausted is True
                ),
                None,
            )
            prediction = PredictionV1(
                prediction_id="p.1",
                kind="generator_inventory_wait",
                source_cell=(
                    None
                    if exhausted is None
                    else CellPositionV1(row=exhausted.row, col=exhausted.col)
                ),
                source_before=None if exhausted is None else exhausted.item_type,
                source_after=None if exhausted is None else exhausted.item_type,
                generator_inventory_available=False,
            )
            request = ActionRequestV1(
                kind="wait",
                intent=intent,
                params={"ms": ms},
                capture_pre=False,
                capture_post=False,
            )
        else:
            intent = "wait:resource_or_board_state"
            prediction = PredictionV1(prediction_id="p.1", kind="wait")
            request = ActionRequestV1(kind="wait", intent=intent, params={"ms": ms})
        return PlanV1(
            actions=[
                PlannedActionV1(
                    kind="wait",
                    intent=intent,
                    prediction=prediction,
                    reason=decision.reason,
                    request=request,
                )
            ],
            predictions=[prediction],
            visual_occupancy=planning_policy.visual_occupancy,
        )

    def _behavior_deliver_plan(
        self,
        decided: Any,
        before: GameStateSemanticV1,
        planning_policy: PolicyV1,
    ) -> PlanV1 | None:
        """planner.py:917-970, single delivery."""

        order = next(
            (
                item
                for item in before.orders
                if item.order_id == decided.params.get("order_id")
            ),
            None,
        )
        if order is None:
            return None
        intent = decided.intent
        prediction = PredictionV1(
            prediction_id="p.1",
            kind="deliver",
            coins_delta=order.reward_coins or 0,
            order_slot=order.slot,
            order_before=order.order_id,
            required_cells=_required_cells(before, order.required_items),
            required_item_types=[
                required.type
                for required in order.required_items
                for _index in range(required.count)
            ],
        )
        request = _delivery_request(
            planning_policy.order_geometry,
            slot=order.slot,
            slot_order=_display_slot_order(before),
            intent=intent,
            control_override=planning_policy.delivery_control_overrides.get(
                order.slot
            ),
        )
        planned = PlannedActionV1(
            kind="deliver",
            intent=intent,
            prediction=prediction,
            reason=(
                "fulfillable order has highest priority; predict exact coin, item, "
                "and slot replacement deltas"
            ),
            request=request,
            status=_request_status(request),
        )
        if request is None:
            return PlanV1(
                actions=[],
                predictions=[],
                blocked_actions=[planned],
                visual_occupancy=planning_policy.visual_occupancy,
            )
        return PlanV1(
            actions=[planned],
            predictions=[prediction],
            visual_occupancy=planning_policy.visual_occupancy,
        )

    def _behavior_main_task_plan(
        self,
        decided: Any,
        before: GameStateSemanticV1,
        planning_policy: PolicyV1,
    ) -> PlanV1 | None:
        """planner.py:985-1020: the delegated-flow placeholder action."""

        task = before.main_task
        if task is None or task.cost is None:
            return None
        intent = decided.intent
        prediction = PredictionV1(
            prediction_id="p.1",
            kind="start_main_task",
            coins_delta=-task.cost,
            main_task_before=task.task_id,
            main_task_cost=task.cost,
        )
        return PlanV1(
            actions=[
                PlannedActionV1(
                    kind="start_main_task",
                    intent=intent,
                    prediction=prediction,
                    reason=(
                        "affordable main task on the recognized main board has "
                        "priority below delivery and above merge or generation"
                    ),
                    request=ActionRequestV1(
                        kind="wait",
                        intent=f"{intent}:delegated-flow",
                        params={"ms": 0},
                    ),
                )
            ],
            predictions=[prediction],
            visual_occupancy=planning_policy.visual_occupancy,
        )

    def _behavior_chest_plan(
        self,
        decided: Any,
        before: GameStateSemanticV1,
        planning_policy: PolicyV1,
    ) -> PlanV1 | None:
        """planner.py:1022-1114: select-then-open for a ready chest, else the timer tap."""

        if decided.position is None:
            return None
        cell = self._board_cell(before, decided.position)
        if cell is None or cell.item_type is None:
            return None
        item_type = str(cell.item_type)
        position = CellPositionV1(row=cell.row, col=cell.col)
        possible_types: list[str] = []
        for chest in _chest_states(before, planning_policy):
            if chest.row == cell.row and chest.col == cell.col:
                possible_types = list(chest.possible_types)
                break
        base_intent = f"open-chest:{item_type}"
        request = _tap_position_request(planning_policy.geometry, position, base_intent)
        if decided.target is not None:
            target = CellPositionV1(row=decided.target[0], col=decided.target[1])
            prediction = PredictionV1(
                prediction_id="p.2",
                kind="open_chest",
                source_cell=position,
                target_cell=target,
                source_before=item_type,
                source_after=item_type,
                target_before=None,
                target_after=possible_types[0] if possible_types else None,
                possible_target_types=possible_types,
            )
            selection_prediction = PredictionV1(
                prediction_id="p.1",
                kind="wait",
                source_cell=position,
                source_before=item_type,
                source_after=item_type,
            )
            selection = PlannedActionV1(
                kind="tap",
                intent=f"select-chest:{item_type}",
                prediction=selection_prediction,
                reason="select the free opened chest before requesting one output",
                request=_tap_position_request(
                    planning_policy.geometry,
                    position,
                    f"select-chest:{item_type}",
                ),
            )
            opening = PlannedActionV1(
                kind="open_chest",
                intent=base_intent,
                prediction=prediction,
                reason=(
                    "free ready chest has priority and emits into an authoritative "
                    "empty cell without energy cost"
                ),
                request=request,
                status=_request_status(request),
            )
            actions = (
                [selection, opening]
                if planning_policy.max_actions_per_round >= 2
                else [opening]
            )
        else:
            prediction = PredictionV1(
                prediction_id="p.1",
                kind="open_chest",
                source_cell=position,
                source_before=item_type,
                source_after=item_type,
            )
            actions = [
                PlannedActionV1(
                    kind="open_chest",
                    intent=f"{base_intent}:start-free-timer",
                    prediction=prediction,
                    reason=(
                        "start or advance the free chest timer; gem acceleration "
                        "is outside the action space"
                    ),
                    request=request,
                    status=_request_status(request),
                )
            ]
        if all(action.request is not None for action in actions):
            return PlanV1(
                actions=actions,
                predictions=[action.prediction for action in actions],
                visual_occupancy=planning_policy.visual_occupancy,
            )
        return PlanV1(
            actions=[],
            predictions=[],
            blocked_actions=[
                action for action in actions if action.request is None
            ],
            visual_occupancy=planning_policy.visual_occupancy,
        )

    def _behavior_merge_plan(
        self,
        decided: Any,
        before: GameStateSemanticV1,
        planning_policy: PolicyV1,
    ) -> PlanV1 | None:
        """planner.py:1196-1249, one merge drag."""

        if decided.position is None or decided.target is None:
            return None
        cell = self._board_cell(before, decided.position)
        if cell is None or cell.item_type is None or cell.merge_target is None:
            return None
        cobweb = decided.intent.startswith("clear-cobweb:")
        source = CellPositionV1(row=cell.row, col=cell.col)
        target = CellPositionV1(row=decided.target[0], col=decided.target[1])
        prediction = PredictionV1(
            prediction_id="p.1",
            kind="merge",
            source_cell=source,
            target_cell=target,
            source_before=cell.item_type,
            source_after=None,
            target_before=f"c#{cell.item_type}" if cobweb else cell.item_type,
            target_after=cell.merge_target,
        )
        request = _drag_request(
            planning_policy.geometry,
            source,
            target,
            decided.intent,
        )
        if cobweb and request is not None:
            request = request.model_copy(update={"settle": "stable"})
        if cobweb:
            reason = (
                "merge a normal same-Type item onto its cobwebbed target to clear "
                "the web and recover space; clearing the board still pays "
                f"{before.board.sand_clear_energy_pending} energy in paperbox "
                "rewards"
                if before.board.sand_clear_energy_pending
                else "merge a normal same-Type item onto its cobwebbed target to "
                "clear the web and recover space"
            )
        else:
            counts = Counter(
                item.item_type
                for cell_row in before.board.cells
                for item in cell_row
                if item.kind == "item" and item.item_type is not None
            )
            outstanding = (
                set(_outstanding_required_counts(before, counts))
                if planning_policy.version == "v2"
                else set()
            )
            chained = (
                _chain_distance(
                    cell.merge_target,
                    outstanding,
                    dict(_configured_item_metadata(self.policy.config_dir)),
                )
                is not None
            )
            reason = (
                "order-directed safe merge toward an outstanding requirement"
                if chained
                else "lowest-value safe merge that preserves required order items"
            )
        return PlanV1(
            actions=[
                PlannedActionV1(
                    kind="drag",
                    intent=decided.intent,
                    prediction=prediction,
                    reason=reason,
                    request=request,
                    status=_request_status(request),
                )
            ],
            predictions=[prediction],
            visual_occupancy=planning_policy.visual_occupancy,
        )

    def _behavior_collect_reward_plan(
        self,
        decided: Any,
        before: GameStateSemanticV1,
        planning_policy: PolicyV1,
    ) -> PlanV1 | None:
        """Render a behavior collectible as select-then-collect taps."""

        if decided.position is None:
            return None
        cell = self._board_cell(before, decided.position)
        if cell is None or cell.item_type is None:
            return None
        rewards = list(cell.collect_rewards)
        if not rewards or any(
            reward.type not in SUPPORTED_FREE_COLLECT_REWARD_TYPES or reward.amount <= 0
            for reward in rewards
        ):
            return None
        energy_gain = sum(reward.amount for reward in rewards if reward.type == "energy")
        coins_gain = sum(reward.amount for reward in rewards if reward.type == "gold")
        legacy_energy = all(reward.type == "energy" for reward in rewards)
        position = CellPositionV1(row=cell.row, col=cell.col)
        select_intent = (
            f"select-energy-item:{cell.item_type}"
            if legacy_energy
            else f"select-reward-item:{cell.item_type}"
        )
        select_request = _tap_position_request(
            planning_policy.geometry,
            position,
            select_intent,
        )
        collect_request = _tap_position_request(
            planning_policy.geometry,
            position,
            decided.intent,
        )
        if select_request is None or collect_request is None:
            # planner.py:782-785: without both coordinates the branch plans
            # nothing at all.
            return PlanV1(
                actions=[],
                predictions=[],
                visual_occupancy=planning_policy.visual_occupancy,
            )
        select_prediction = PredictionV1(
            prediction_id="p.1",
            kind="wait",
            source_cell=position,
            source_before=cell.item_type,
            source_after=cell.item_type,
        )
        collect_prediction = PredictionV1(
            prediction_id="p.2",
            kind="collect_energy" if legacy_energy else "collect_reward",
            energy_delta=energy_gain,
            coins_delta=coins_gain,
            source_cell=position,
            source_before=cell.item_type,
            source_after=None,
            collect_rewards=rewards,
        )
        return PlanV1(
            actions=[
                PlannedActionV1(
                    kind="tap",
                    intent=select_intent,
                    prediction=select_prediction,
                    reason=(
                        "select the unmergeable energy item before collecting it"
                        if legacy_energy
                        else "select the supported free reward before collecting it"
                    ),
                    request=select_request,
                ),
                PlannedActionV1(
                    kind="tap",
                    intent=decided.intent,
                    prediction=collect_prediction,
                    reason=(
                        "collect the supported free reward after preserving merges"
                    ),
                    request=collect_request,
                    depends_on=select_prediction.prediction_id,
                ),
            ],
            predictions=[select_prediction, collect_prediction],
            visual_occupancy=planning_policy.visual_occupancy,
        )

    def _behavior_collect_energy_plan(
        self,
        decided: Any,
        before: GameStateSemanticV1,
        planning_policy: PolicyV1,
    ) -> PlanV1 | None:
        """Compatibility alias for callers using the old energy name."""

        return self._behavior_collect_reward_plan(decided, before, planning_policy)

    def _behavior_claim_reward_queue_plan(
        self,
        decided: Any,
        before: GameStateSemanticV1,
        planning_policy: PolicyV1,
    ) -> PlanV1 | None:
        """Render one guarded tap on the decoded CacheItem reward queue."""

        rows = before.cache_items
        if (
            not rows
            or before.cache_item_free_claim_supported is not True
            or len(before.board.empty_cells) < 3
        ):
            return None
        request = ActionRequestV1(
            kind="tap",
            intent="claim-reward-queue",
            params={"x": 100, "y": 390},
        )
        first = _first_cache_item(rows)
        if first is None:
            return None
        code = first.get("codeStr", first.get("code"))
        prediction = PredictionV1(
            prediction_id="p.1",
            kind="claim_reward_queue",
            cache_items_before=len(rows),
            cache_item_code_before=None if code is None else str(code),
            cache_queue_head_id_before=(
                None if first.get("id") is None else str(first.get("id"))
            ),
        )
        return PlanV1(
            actions=[
                PlannedActionV1(
                    kind="tap",
                    intent="claim-reward-queue",
                    prediction=prediction,
                    reason=(
                        "claim one free queued reward; post-save must show a cache "
                        "decrement and a new board item"
                    ),
                    request=request,
                )
            ],
            predictions=[prediction],
            visual_occupancy=planning_policy.visual_occupancy,
        )

    def _behavior_generate_plan(
        self,
        decided: Any,
        before: GameStateSemanticV1,
        planning_policy: PolicyV1,
    ) -> PlanV1 | None:
        """planner.py:1424-1462, one generator tap."""

        if decided.position is None or decided.target is None:
            return None
        cell = self._board_cell(before, decided.position)
        if cell is None:
            return None
        generator = cell.generator
        cost = (
            generator.cost_energy
            if generator is not None and generator.cost_energy is not None
            else 1
        )
        possible_types = (
            [weight.item_type for weight in generator.spread_weights]
            if generator is not None
            else []
        )
        predicted_type = possible_types[0] if possible_types else None
        source = CellPositionV1(row=cell.row, col=cell.col)
        target = CellPositionV1(row=decided.target[0], col=decided.target[1])
        prediction = PredictionV1(
            prediction_id="p.1",
            kind="generate",
            energy_delta=-max(cost, 1),
            source_cell=source,
            target_cell=target,
            source_before=cell.item_type,
            source_after=cell.item_type,
            target_before=None,
            target_after=predicted_type,
            possible_target_types=possible_types,
            generator_inventory_available=(
                None
                if generator is None
                or generator.item_rest is None
                or generator.storage_rest is None
                else True
            ),
        )
        counts = Counter(
            item.item_type
            for cell_row in before.board.cells
            for item in cell_row
            if item.kind == "item" and item.item_type is not None
        )
        outstanding = (
            set(_outstanding_required_counts(before, counts))
            if planning_policy.version == "v2"
            else set()
        )
        chained = bool(outstanding) and any(
            _chain_distance(
                weight.item_type,
                outstanding,
                dict(_configured_item_metadata(self.policy.config_dir)),
            )
            is not None
            for weight in (generator.spread_weights if generator is not None else [])
        )
        request = _tap_request(planning_policy.geometry, cell, decided.intent)
        return PlanV1(
            actions=[
                PlannedActionV1(
                    kind="tap",
                    intent=decided.intent,
                    prediction=prediction,
                    reason=(
                        "generate from the shortest configured chain toward an "
                        "outstanding order"
                        if planning_policy.version == "v2" and chained
                        else "energy and board space permit a generator action"
                    ),
                    request=request,
                    status=_request_status(request),
                )
            ],
            predictions=[prediction],
            visual_occupancy=planning_policy.visual_occupancy,
        )

    def _behavior_sell_plan(
        self,
        decided: Any,
        before: GameStateSemanticV1,
        planning_policy: PolicyV1,
    ) -> PlanV1 | None:
        """planner.py:1478-1558: select-then-sell (or collect) one cell."""

        if decided.position is None:
            return None
        cell = self._board_cell(before, decided.position)
        if cell is None or cell.item_type is None:
            return None
        value = int(decided.params.get("value") or 0)
        coin_collectible = decided.intent.startswith("collect-coin-piece:")
        position = CellPositionV1(row=cell.row, col=cell.col)
        select_intent = f"select-sell-item:{cell.item_type}"
        select_request = _tap_position_request(
            planning_policy.geometry,
            position,
            select_intent,
        )
        sell_request = (
            _tap_position_request(planning_policy.geometry, position, decided.intent)
            if coin_collectible
            else _sell_control_request(planning_policy, decided.intent)
        )
        if select_request is None or sell_request is None:
            prediction = PredictionV1(
                prediction_id="b.1",
                kind="sell",
                source_cell=position,
                source_before=cell.item_type,
                source_after=None,
                coins_delta=value,
            )
            return PlanV1(
                actions=[],
                predictions=[],
                blocked_actions=[
                    PlannedActionV1(
                        kind="sell",
                        intent=decided.intent,
                        prediction=prediction,
                        reason=(
                            "safe sale is blocked because a required UI coordinate "
                            "is missing"
                        ),
                        request=sell_request,
                        status="blocked_missing_geometry",
                    )
                ],
                visual_occupancy=planning_policy.visual_occupancy,
            )
        select_prediction = PredictionV1(
            prediction_id="p.1",
            kind="wait",
            source_cell=position,
            source_before=cell.item_type,
            source_after=cell.item_type,
        )
        prediction = PredictionV1(
            prediction_id="p.2",
            kind="sell",
            source_cell=position,
            source_before=cell.item_type,
            source_after=None,
            coins_delta=value,
        )
        return PlanV1(
            actions=[
                PlannedActionV1(
                    kind="tap",
                    intent=select_intent,
                    prediction=select_prediction,
                    reason="select the verified low-value non-order-chain item",
                    request=select_request,
                ),
                PlannedActionV1(
                    kind="sell",
                    intent=decided.intent,
                    prediction=prediction,
                    reason=(
                        "collect a verified coin piece to recover one full-board cell"
                        if coin_collectible
                        else "sell the lowest-price safe non-order-chain item to "
                        "recover space"
                    ),
                    request=sell_request,
                    depends_on=select_prediction.prediction_id,
                ),
            ],
            predictions=[select_prediction, prediction],
            visual_occupancy=planning_policy.visual_occupancy,
        )

    def _decide_behavior_plan(
        self,
        before: GameStateSemanticV1,
        *,
        planning_policy: PolicyV1,
        current_screen: str,
        screen_guard_actions: list[PopupHandlingRecordV1],
        business_refuted_streak: int,
        just_landed: frozenset[tuple[int, int]],
        cumulative_wait_s: float,
        max_wait_s: int | None,
        deliveries_executed: int,
        conditions: StopConditionsV1,
    ) -> tuple[PlanV1, tuple[str, str, bool] | None]:
        """One ``decide(frame)`` rendered as a legacy PlanV1 plus a pending stop.

        The pending stop tuple is (reason, detail, is_escalation); it is
        applied after the round record lands, in the shape of the round-end
        ladder entries it replaces.  ``None`` means play the plan.
        """

        # behavior_sm imports this module's guard constants, so the import
        # stays local to keep module loading acyclic; the spec's constant
        # re-homing is a later cleanup.
        from .behavior_sm import Act, Escalate, Stop, WaitUntil, decide

        frame = self._assemble_decision_frame(
            before,
            current_screen=current_screen,
            screen_guard_actions=screen_guard_actions,
            business_refuted_streak=business_refuted_streak,
            just_landed=just_landed,
            cumulative_wait_s=cumulative_wait_s,
            max_wait_s=max_wait_s,
            deliveries_executed=deliveries_executed,
            conditions=conditions,
            chest_states=tuple(planning_policy.chest_states),
            deliverable_slots=self._deliverable_slots(before, planning_policy),
        )
        decision = decide(frame)
        if isinstance(decision, Stop):
            return (
                PlanV1(
                    actions=[],
                    predictions=[],
                    visual_occupancy=planning_policy.visual_occupancy,
                ),
                (decision.reason, "", False),
            )
        if isinstance(decision, Escalate):
            return (
                PlanV1(
                    actions=[],
                    predictions=[],
                    visual_occupancy=planning_policy.visual_occupancy,
                ),
                (decision.reason, decision.detail, True),
            )
        if isinstance(decision, WaitUntil):
            return (
                self._behavior_wait_plan(decision, before, planning_policy, frame.clocks.now),
                None,
            )
        if isinstance(decision, Act):
            action = decision.action
            if action.kind == "deliver":
                plan = self._behavior_deliver_plan(action, before, planning_policy)
            elif action.kind == "start_main_task":
                plan = self._behavior_main_task_plan(action, before, planning_policy)
            elif action.kind == "open_chest":
                plan = self._behavior_chest_plan(action, before, planning_policy)
            elif action.kind == "merge":
                plan = self._behavior_merge_plan(action, before, planning_policy)
            elif action.kind in {"collect_energy", "collect_reward"}:
                plan = self._behavior_collect_reward_plan(action, before, planning_policy)
            elif action.kind == "claim_reward_queue":
                plan = self._behavior_claim_reward_queue_plan(
                    action, before, planning_policy
                )
            elif action.kind == "generate":
                plan = self._behavior_generate_plan(action, before, planning_policy)
            elif action.kind == "sell":
                plan = self._behavior_sell_plan(action, before, planning_policy)
            elif (
                action.kind == "popup"
                and action.screen == "board-full-under-guide"
            ):
                # game_loop.py:1930-1968: the unchanged post-round
                # _open_chest_when_room_exists performs this tap on the same
                # five-empty-cells condition; the round's plan stays empty.
                plan = PlanV1(
                    actions=[],
                    predictions=[],
                    visual_occupancy=planning_policy.visual_occupancy,
                )
            else:
                # Popup, navigation, relaunch: the live guard has already
                # ruled on every surface before this point, so reaching one
                # here means the executor has no coverage for the decision --
                # the unwired-surface stop.
                plan = None
            if plan is not None:
                return plan, None
        return (
            PlanV1(
                actions=[],
                predictions=[],
                visual_occupancy=planning_policy.visual_occupancy,
            ),
            (
                "not_on_board",
                f"behavior decision {getattr(decision, 'action', decision)!r} "
                "has no executor coverage",
                True,
            ),
        )

    def _apply_behavior_stop(self, pending: tuple[str, str, bool]) -> LoopStopReason:
        """Apply a decide() stop in the shape of the round-end ladder entry."""

        reason, detail, escalation = pending
        if reason == "waiting_for_energy":
            return self._note_energy_wait()
        if reason == "exhausted_v0":
            self.replay.note("planner reached exhausted_v0")
        elif reason == "resource_wait":
            self.replay.note("planner stopped on resource recovery wait")
        elif reason == "max_wait_s":
            self.replay.note("planner stopped at max_wait_s")
        elif reason == "refuted_streak":
            self.replay.alert(
                "planner stopped after three consecutive refuted business predictions"
            )
        elif escalation:
            self.replay.alert(f"{reason}: {detail}" if detail else reason)
        else:
            self.replay.note(f"{reason}: {detail}" if detail else reason)
        return reason  # type: ignore[return-value]

    def run(
        self,
        max_rounds: int,
        stop_conditions: StopConditionsV1 | None = None,
    ) -> LoopReportV1:
        if max_rounds < 1:
            raise ValueError("max_rounds must be positive")
        conditions = stop_conditions or StopConditionsV1()
        max_wait_s = (
            self.policy.max_wait_s if conditions.max_wait_s is None else conditions.max_wait_s
        )
        started_at = datetime.now(timezone.utc).isoformat()
        wall_started = time.perf_counter()
        rounds: list[RoundRecordV1] = []
        previous_screenshot_sha: str | None = None
        business_refuted_streak = 0
        cumulative_wait_s = 0.0
        stop_reason: LoopStopReason = "max_rounds"
        selected_generator: tuple[int, int] | None = None
        reusable_before: tuple[Any, GameStateSemanticV1, float] | None = None
        reusable_ready_at: float | None = None
        # F4-11 section 1: when the round that took its own read got it back.
        fresh_read_ready_at: float | None = None
        # 2026-09-09 input-death ruling: business actions awaiting their
        # adjudication, and the surface the stop happened on, so an
        # input_dead_unrecovered report can vouch for whether an automatic
        # force-stop was safe.  The surface stays None when the stop came from
        # inside the main-task flow, whose own navigation the loop does not
        # track -- and an unknown surface is never a safe one.
        business_action_log: list[dict[str, Any]] = []
        input_dead_surface: str | None = None

        # A configured control file is a closed gate until it is read as a
        # complete, valid object.  This check covers the whole batch before
        # touching the device.
        batch_control_stop = self._control_stop_reason()
        if batch_control_stop is not None:
            stop_reason = batch_control_stop

        base_planning_policy = self.policy.model_copy(
            update={
                "geometry": _SemanticGeometryAdapter(self.geometry),
                "order_geometry": self.order_geometry,
            }
        )
        deliveries_executed = 0
        for round_number in range(1, max_rounds + 1):
            if stop_reason != "max_rounds":
                break
            round_started = time.perf_counter()
            # E6: when save versioning is armed the channel's commit message
            # carries this round; default off means the attribute is never set.
            if getattr(self.save_channel, "save_history", None) is not None:
                self.save_channel.save_history_context = {"round": round_number}
            try:
                frame, screenshot_sha, screenshot_ms = self._capture_round_frame()
                read_reused = self.reuse_read and reusable_before is not None
                if read_reused:
                    _before_result, before, _previous_read_s = reusable_before
                    read_before_s = 0.0
                else:
                    _before_result, before, read_before_s = self._read_semantic(
                        flush="none" if self.reuse_read else "home_and_return"
                    )
                    fresh_read_ready_at = time.perf_counter()
            except Exception as exc:
                outcome = self._handle_read_failure(exc, now=time.time())
                if outcome is None:
                    reusable_before = None
                    reusable_ready_at = None
                    continue
                stop_reason = outcome
                break
            # Judge the target from the authoritative save before the frame
            # guard, planner, warm-up, or any other input can run.
            if self._target_reached(before):
                self.replay.note(
                    f"target_level_reached: save level {before.resources.level} "
                    f">= target {self.target_level}"
                )
                stop_reason = "target_level_reached"
                break
            control_stop = self._control_stop_reason()
            if control_stop is not None:
                stop_reason = control_stop
                break
            # F4-09 1.1: anchor this round's claim evidence on the read it just
            # took, whether that read was fresh or reused from the round before.
            self._claim_evidence_before = before

            plan_started = time.perf_counter()
            current_screen = "unknown"
            screen_guard_actions: list[PopupHandlingRecordV1] = []
            screen_guard_action_results: list[ActionResultV1] = []
            shop_entry_transition = False
            shop_entry_stalled = False
            if self.surface_registry is not None:
                if self._last_frame_rgb is None:
                    self.replay.alert("unknown_screen: captured frame is unavailable")
                    stop_reason = "unknown_screen"
                    break
                (
                    frame,
                    screenshot_sha,
                    screenshot_ms,
                    current_screen,
                    screen_guard_actions,
                    screen_guard_action_results,
                ) = self._guard_main_board(frame, screenshot_sha, screenshot_ms)
                if self._tutorial_follow_stalled is not None:
                    if self._enter_board_full_under_guide(before):
                        current_screen = "main-board"
                    else:
                        self.replay.alert(
                            "tutorial_follow_stalled: the registered guide at "
                            f"{self._tutorial_follow_stalled} was tapped "
                            f"{guided_target_stall_budget(self._tutorial_follow_stalled)}"
                            " times and the frame did not change by a byte"
                        )
                        stop_reason = "tutorial_follow_stalled"
                        break
                if self._forbidden_recurring_stop is not None:
                    self.replay.alert(
                        "forbidden_surface_recurring: screen guard closed "
                        f"{self._forbidden_recurring_stop} "
                        f"{FORBIDDEN_RECURRENCE_LIMIT} times within "
                        f"{int(FORBIDDEN_RECURRENCE_WINDOW_S)} s"
                    )
                    stop_reason = "forbidden_surface_recurring"
                    break
                if self._forbidden_surface_stop is not None:
                    self.replay.alert(
                        "forbidden_surface: screen guard stopped on "
                        f"{self._forbidden_surface_stop.name} "
                        f"({', '.join(self._forbidden_surface_stop.reasons)})"
                    )
                    stop_reason = "forbidden_surface"
                    break
                if self._unexplored_surface_stop is not None:
                    self.replay.alert(
                        "unexplored_surface: screen guard left registered "
                        f"surface {self._unexplored_surface_stop} standing for "
                        "the agent handler"
                    )
                    stop_reason = "unexplored_surface"
                    break
                if _has_input_restart_recovery(screen_guard_action_results):
                    self.input_restart_recoveries += 1
                    self.replay.alert(
                        "input_restart_recovery: screen guard action was lost to a "
                        "renderer freeze and the app was relaunched"
                    )
                    reusable_before = None
                    reusable_ready_at = None
                    continue
                if _has_input_dead(screen_guard_action_results):
                    self.replay.alert("input_dead: screen guard recovery failed")
                    input_dead_surface = current_screen
                    stop_reason = "input_dead_unrecovered"
                    break
                if current_screen != "main-board":
                    if current_screen == STORY_DIALOGUE_SURFACE:
                        # F4-11 follow-up, 2026-09-08.  The guard advanced this
                        # dialogue up to its per-round step cap and the dialogue
                        # is still going: that is play in progress, not a lost
                        # surface.  Hand the next round back to the guard, which
                        # spends from the same per-dialogue budget
                        # (MAX_STORY_ADVANCE_TAPS, counted across rounds) under
                        # the same three-unchanged-frames stall test, and stops
                        # for a ruling the moment either one binds.  The
                        # 2026-09-08 07:36Z segment spent its eight guard steps
                        # on a Carmen/Quinn dialogue and then stopped
                        # not_on_board at zero rounds with the advance far from
                        # either limit.  The read is dropped with the round:
                        # entering the dialogue can coincide with a save change,
                        # so the next round reads fresh rather than planning on
                        # a state from before the story.
                        self._guard_handoff_rounds.clear()
                        self.replay.note(
                            "story_advance_continue: the dialogue is still up "
                            "after this round's guard steps; the next round "
                            "keeps advancing it"
                        )
                        reusable_before = None
                        reusable_ready_at = None
                        continue
                    if current_screen in GUARD_HANDOFF_SURFACES:
                        # 2026-09-08.  A registered panel with a wired departure
                        # action is still up: either the segment opened on it
                        # (the 13:35Z main-task-panel the interrupted main-task
                        # session left standing) or this round's eight guard
                        # steps ran out underneath it (the 13:40Z
                        # decoration-confirmation, after seven story advances
                        # and the selection tap).  That is a departure in
                        # progress, not a lost surface: hand the next round
                        # back to the guard, at most MAX_GUARD_HANDOFF_ROUNDS
                        # times per continuous occupation, under the same
                        # three-unchanged-frames stall test the guard applies
                        # to the panel's own actions.  The read is dropped
                        # with the round, as with the dialogue above.
                        spent = self._guard_handoff_rounds.get(current_screen, 0)
                        if spent >= MAX_GUARD_HANDOFF_ROUNDS:
                            self.replay.alert(
                                f"not_on_board: screen guard stopped at {current_screen}"
                                f" after {spent} extra guard rounds"
                            )
                            stop_reason = "not_on_board"
                            break
                        self._guard_handoff_rounds = {current_screen: spent + 1}
                        self.replay.note(
                            f"panel_departure_continue: {current_screen} is still "
                            "up after this round's guard steps; the next round "
                            f"keeps leaving it ({spent + 1} of "
                            f"{MAX_GUARD_HANDOFF_ROUNDS})"
                        )
                        reusable_before = None
                        reusable_ready_at = None
                        continue
                    self._guard_handoff_rounds.clear()
                    self.replay.alert(f"not_on_board: screen guard stopped at {current_screen}")
                    stop_reason = "not_on_board"
                    break
                self._guard_handoff_rounds.clear()
                if not self._shop_entry_checked:
                    # The guard has just established an uncovered, settled
                    # board.  Recheck the control gate at the final boundary
                    # before allowing the optional shop input.
                    control_stop = self._control_stop_reason()
                    if control_stop is not None:
                        stop_reason = control_stop
                        break
                    (
                        frame,
                        screenshot_sha,
                        screenshot_ms,
                        shop_record,
                        shop_result,
                        shop_entry_stalled,
                    ) = self._check_shop_red_badge_entry(
                        frame,
                        screenshot_sha,
                        screenshot_ms,
                    )
                    if shop_record is not None and shop_result is not None:
                        screen_guard_actions.append(shop_record)
                        screen_guard_action_results.append(shop_result)
                    shop_entry_transition = (
                        shop_record is not None and not shop_entry_stalled
                    )
            self._expire_refused_moves(round_number)
            self._refresh_chest_holds(getattr(_before_result, "state", None), before)
            # F4-09 1.6: what this read shows that the read before it did not.
            # The boundary is the previous read's newest item stamp, so the
            # comparison is against the state the last round planned on, never
            # against this read itself.
            planning_read_epoch_ms = latest_item_epoch_ms(
                getattr(_before_result, "state", None)
            )
            just_landed = just_landed_positions(
                getattr(_before_result, "state", None),
                self._previous_read_epoch_ms,
            )
            board_full_update = (
                {
                    # Only the two actions that free a cell.  Generating fills
                    # one, and the run has no evidence it can deliver or open a
                    # panel while the guide is up.
                    "max_generates_per_round": 0,
                    "max_deliveries_per_round": 0,
                    "max_energy_collects_per_round": 0,
                    "max_main_tasks_per_round": 0,
                }
                if self._board_full_under_guide
                else {}
            )
            planning_policy = base_planning_policy.model_copy(
                update={
                    "suppressed_merge_pairs": frozenset(self._suppressed_merge_pairs),
                    "suppressed_generator_cells": frozenset(
                        self._suppressed_generator_cells
                    ),
                    "suppressed_chest_cells": frozenset(self._suppressed_chest_cells),
                    "warehouse_stash_held": (
                        self._warehouse_stash_hold_until >= round_number
                    ),
                    "generate_energy_multiplier": self._generate_energy_multiplier,
                    "held_positions": just_landed,
                    "current_screen": current_screen,
                    "chest_states": _runtime_chest_states(
                        getattr(_before_result, "state", None),
                        before,
                        base_planning_policy.config_dir,
                    ),
                    "visual_nonempty_cells": frozenset(),
                    "visual_occupancy": None,
                    "delivery_control_overrides": (
                        {}
                        if self._last_frame_rgb is None
                        else _delivery_control_overrides(before, self._last_frame_rgb)
                    ),
                    # The queue chest slides away when there is nothing to
                    # collect and renders the head item when there is, so the
                    # surveyed coordinate is only safe once its count badge is
                    # visible in this very frame.
                    "warehouse_parked": tuple(self._warehouse_parked),
                    "reward_queue_control": (
                        None
                        if self._last_frame_rgb is None
                        else self.surface_registry.visible_variant_point(
                            "main-board", "reward-queue", self._last_frame_rgb
                        )
                    ),
                    "max_deliveries_per_round": (
                        0
                        if deliveries_executed >= self.policy.max_deliveries_per_run
                        else self.policy.max_deliveries_per_round
                    ),
                    # Last, so the full-board restriction wins over every
                    # limit set above it.
                    **board_full_update,
                }
            )
            if self._last_frame_rgb is not None:
                visual_occupancy = _visual_occupancy_coverage(
                    before,
                    self._last_frame_rgb,
                    self.geometry,
                )
                planning_policy = planning_policy.model_copy(
                    update={
                        "visual_nonempty_cells": frozenset(
                            (item.row, item.col)
                            for item in visual_occupancy.visually_occupied_cells
                        ),
                        "visual_occupancy": visual_occupancy,
                    }
                )
            if shop_entry_transition or shop_entry_stalled:
                # The optional entry consumed the board frame.  Record it in
                # this round, but never plan a board action against the
                # post-entry/shop pixels; the next round starts from a fresh
                # save and the existing screen guard.
                plan = PlanV1(
                    actions=[],
                    predictions=[],
                    visual_occupancy=planning_policy.visual_occupancy,
                )
                behavior_stop = None
            elif self.use_behavior_sm:
                # F4-12 Phase 2: the pure behavior state machine replaces
                # plan_batch; the translator renders its one decision with
                # planner.py's own recipes.
                plan, behavior_stop = self._decide_behavior_plan(
                    before,
                    planning_policy=planning_policy,
                    current_screen=current_screen,
                    screen_guard_actions=screen_guard_actions,
                    business_refuted_streak=business_refuted_streak,
                    just_landed=just_landed,
                    cumulative_wait_s=cumulative_wait_s,
                    max_wait_s=max_wait_s,
                    deliveries_executed=deliveries_executed,
                    conditions=conditions,
                )
            else:
                plan = plan_batch(before, planning_policy)
                behavior_stop = None
            # The pre-seed that let a guard-less caller treat its first
            # generator tap as production died with F4-06: the screen guard is
            # unconditional now, so every run has frame evidence and the first
            # tap on an unselected generator is always a selection tap.
            plan, _selected_after_plan = _adapt_generator_selection(
                plan,
                selected_generator=selected_generator,
            )
            plan = _stabilize_chain_dependencies(plan)
            if business_refuted_streak:
                plan = _limit_business_predictions(
                    plan,
                    conditions.max_consecutive_refuted - business_refuted_streak,
                )
            plan_ms = (time.perf_counter() - plan_started) * 1000
            executed: list[ExecutedActionV1] = []
            main_task_results: list[MainTaskResultV1] = []
            flow_after: GameStateSemanticV1 | None = None
            main_task_stop: LoopStopReason | None = None
            action_phase_started = time.perf_counter()
            foreground_settle_ms = 0.0
            if read_reused and reusable_ready_at is not None:
                ready_after = reusable_ready_at + self.post_foreground_settle_ms / 1000
                remaining_s = max(ready_after - time.perf_counter(), 0.0)
                if remaining_s:
                    self.sleep(remaining_s)
                    foreground_settle_ms = remaining_s * 1000
            cold_start = None
            if not read_reused and fresh_read_ready_at is not None:
                control_stop = self._control_stop_reason()
                if control_stop is not None:
                    stop_reason = control_stop
                    break
                cold_start = self._apply_cold_start_policy(before, fresh_read_ready_at)
                if cold_start is not None:
                    foreground_settle_ms += float(cold_start.get("settle_ms") or 0.0)
            wait_limit_reached = False
            resource_wait_reached = False
            action_guard_stopped = shop_entry_stalled
            replan_after_guard = shop_entry_transition
            control_stopped = False
            previous_action: PlannedActionV1 | None = None
            if shop_entry_stalled:
                main_task_stop = "shop_entry_stalled"
            for action in plan.actions:
                if action.request is None:
                    continue
                control_stop = self._control_stop_reason()
                if control_stop is not None:
                    stop_reason = control_stop
                    control_stopped = True
                    break
                if (
                    self.surface_registry is not None
                    and action.prediction.kind in BUSINESS_PREDICTION_KINDS
                ):
                    guarded_frame, guarded_sha, guarded_ms = self._capture_round_frame()
                    (
                        frame,
                        screenshot_sha,
                        guarded_ms,
                        current_screen,
                        action_guard_records,
                        action_guard_results,
                    ) = self._guard_main_board(
                        guarded_frame,
                        guarded_sha,
                        guarded_ms,
                    )
                    screenshot_ms += guarded_ms
                    screen_guard_actions.extend(action_guard_records)
                    screen_guard_action_results.extend(action_guard_results)
                    if self._tutorial_follow_stalled is not None and not (
                        self._board_full_under_guide
                    ):
                        self.replay.alert(
                            "tutorial_follow_stalled: the registered guide at "
                            f"{self._tutorial_follow_stalled} was tapped "
                            f"{guided_target_stall_budget(self._tutorial_follow_stalled)}"
                            " times and the frame did not change by a byte"
                        )
                        action_guard_stopped = True
                        main_task_stop = "tutorial_follow_stalled"
                        break
                    if self._forbidden_recurring_stop is not None:
                        self.replay.alert(
                            "forbidden_surface_recurring: pre-action guard closed "
                            f"{self._forbidden_recurring_stop} "
                            f"{FORBIDDEN_RECURRENCE_LIMIT} times within "
                            f"{int(FORBIDDEN_RECURRENCE_WINDOW_S)} s"
                        )
                        action_guard_stopped = True
                        main_task_stop = "forbidden_surface_recurring"
                        break
                    if self._forbidden_surface_stop is not None:
                        self.replay.alert(
                            "forbidden_surface: pre-action guard stopped "
                            f"{action.prediction.kind} on "
                            f"{self._forbidden_surface_stop.name} "
                            f"({', '.join(self._forbidden_surface_stop.reasons)})"
                        )
                        action_guard_stopped = True
                        main_task_stop = "forbidden_surface"
                        break
                    if _has_input_restart_recovery(action_guard_results):
                        self.input_restart_recoveries += 1
                        self.replay.alert(
                            "input_restart_recovery: pre-action guard action was lost "
                            "to a renderer freeze and the app was relaunched"
                        )
                        replan_after_guard = True
                        break
                    if _has_input_dead(action_guard_results):
                        self.replay.alert("input_dead: pre-action screen guard recovery failed")
                        input_dead_surface = current_screen
                        action_guard_stopped = True
                        main_task_stop = "input_dead_unrecovered"
                        break
                    if current_screen != "main-board":
                        if current_screen == STORY_DIALOGUE_SURFACE:
                            # F4-12 Phase 0, canonical ruling 2: the pre-action
                            # guard rules the same way the round-level guard
                            # does.  A dialogue that outlives one guard pass is
                            # play in progress, not a lost surface: replan next
                            # round, which goes back to the guard and spends
                            # from the same per-dialogue advance budget.
                            self._guard_handoff_rounds.clear()
                            self.replay.note(
                                "story_advance_continue: the dialogue is still up "
                                "after this pre-action guard pass; the next round "
                                "keeps advancing it"
                            )
                            replan_after_guard = True
                            break
                        if current_screen in GUARD_HANDOFF_SURFACES:
                            # F4-12 Phase 0, canonical ruling 2: a registered
                            # panel with a wired departure action gets the same
                            # handoff the round-level guard grants -- at most
                            # MAX_GUARD_HANDOFF_ROUNDS extra rounds per
                            # continuous occupation, counted in the same budget,
                            # and only past the budget does the run stop.
                            spent = self._guard_handoff_rounds.get(current_screen, 0)
                            if spent >= MAX_GUARD_HANDOFF_ROUNDS:
                                self.replay.alert(
                                    "not_on_board: pre-action guard stopped "
                                    f"{action.prediction.kind} at {current_screen}"
                                    f" after {spent} extra guard rounds"
                                )
                                action_guard_stopped = True
                                break
                            self._guard_handoff_rounds = {current_screen: spent + 1}
                            self.replay.note(
                                f"panel_departure_continue: {current_screen} is still "
                                "up after this pre-action guard pass; the next round "
                                f"keeps leaving it ({spent + 1} of "
                                f"{MAX_GUARD_HANDOFF_ROUNDS})"
                            )
                            replan_after_guard = True
                            break
                        self._guard_handoff_rounds.clear()
                        self.replay.alert(
                            "not_on_board: pre-action guard stopped "
                            f"{action.prediction.kind} at {current_screen}"
                        )
                        action_guard_stopped = True
                        break
                    if action_guard_records:
                        replan_after_guard = True
                        break
                    # The guard may have taken time to settle.  Re-read the
                    # hand-off gate immediately before the actual action.
                    control_stop = self._control_stop_reason()
                    if control_stop is not None:
                        stop_reason = control_stop
                        control_stopped = True
                        break
                if action.kind == "start_main_task":
                    control_stop = self._control_stop_reason()
                    if control_stop is not None:
                        stop_reason = control_stop
                        control_stopped = True
                        break
                    if self.main_task_flow is None:
                        self.replay.alert("main-task flow is not configured")
                        main_task_stop = "main_task_stopped"
                        break
                    flow_result = self.main_task_flow.run(
                        self.save_channel,
                        self.actuator,
                        self.replay,
                    )
                    main_task_results.append(flow_result)
                    if flow_result.action_results:
                        executed.append(
                            ExecutedActionV1(
                                action=action,
                                result=flow_result.action_results[-1],
                            )
                        )
                    if flow_result.after_state_payload is not None:
                        flow_after = derive_game_state_semantics(
                            flow_result.after_state_payload,
                            self.policy.config_dir,
                        )
                    else:
                        flow_after = before
                    if (
                        flow_result.status != "completed"
                        and _has_input_restart_recovery(flow_result.action_results)
                    ):
                        self.input_restart_recoveries += 1
                        self.replay.alert(
                            "input_restart_recovery: main-task flow was lost to a "
                            "renderer freeze and the app was relaunched"
                        )
                        replan_after_guard = True
                        selected_generator = None
                        break
                    if flow_result.status == "completed":
                        main_task_stop = "main_task_completed"
                    elif flow_result.status == "unavailable":
                        # The event HUD hid the task notebook: the main line is
                        # unavailable by game mechanism, so the segment -- and
                        # the shift -- ends cleanly on its own reason.
                        main_task_stop = "main_task_unavailable"
                    elif _has_input_dead(flow_result.action_results):
                        main_task_stop = "input_dead_unrecovered"
                    else:
                        main_task_stop = "main_task_stopped"
                    selected_generator = None
                    break
                if action.kind == "wait":
                    if conditions.stop_on_resource_wait and action.reason.startswith(
                        "energy depleted:"
                    ):
                        resource_wait_reached = True
                        break
                    requested_wait_s = action.request.params["ms"] / 1000
                    if cumulative_wait_s + requested_wait_s > max_wait_s:
                        wait_limit_reached = True
                        break
                    cumulative_wait_s += requested_wait_s
                if (
                    self.backend != "mumu_ipc"
                    and action.kind == "tap"
                    and previous_action is not None
                ):
                    if previous_action.kind == "tap":
                        self.sleep(INTER_GENERATOR_ACTION_DELAY_S)
                if (
                    self.backend == "mumu_ipc"
                    and action.intent == "select-generator:before-production"
                ):
                    refresh = getattr(self.actuator, "refresh_display_id", None)
                    if callable(refresh):
                        refresh(reason="before-generator-selection")
                result = self._act(action)
                executed.append(ExecutedActionV1(action=action, result=result))
                if action.kind != "wait":
                    business_action_log.append(
                        {
                            "epoch": time.time(),
                            "prediction_id": action.prediction.prediction_id,
                        }
                    )
                if _has_input_restart_recovery([result]):
                    self.input_restart_recoveries += 1
                    self.replay.alert(
                        f"input_restart_recovery: business action {action.intent} was "
                        "lost to a renderer freeze and the app was relaunched"
                    )
                    replan_after_guard = True
                    selected_generator = None
                    break
                if _has_input_dead([result]):
                    self.replay.alert(
                        f"input_dead: business action {action.intent} recovery failed"
                    )
                    input_dead_surface = current_screen
                    action_guard_stopped = True
                    main_task_stop = "input_dead_unrecovered"
                    break
                source = action.prediction.source_cell
                if (
                    action.intent == "select-generator:before-production"
                    and source is not None
                ):
                    selected_generator = (source.row, source.col)
                elif action.prediction.kind == "generate" and source is not None:
                    selected_generator = (source.row, source.col)
                elif action.kind in {"drag", "deliver", "open_chest", "sell"} or (
                    action.kind == "tap" and action.prediction.kind != "generate"
                ):
                    selected_generator = None
                if action.kind == "deliver":
                    deliveries_executed += 1
                previous_action = action
            act_ms = (time.perf_counter() - action_phase_started) * 1000

            if control_stopped:
                # Do not perform a post-action read or chest opener after a
                # hand-off.  The next controller owns the next input boundary.
                break

            if action_guard_stopped or replan_after_guard:
                executed_prediction_ids = {
                    item.action.prediction.prediction_id for item in executed
                }
                plan = plan.model_copy(
                    update={
                        "actions": [
                            item
                            for item in plan.actions
                            if item.prediction.prediction_id in executed_prediction_ids
                        ],
                        "predictions": [
                            item
                            for item in plan.predictions
                            if item.prediction_id in executed_prediction_ids
                        ],
                    }
                )

            _after_result = None
            if flow_after is not None:
                after = flow_after
                read_after_s = 0.0
                reusable_before = None
                reusable_ready_at = None
            else:
                try:
                    _after_result, after, read_after_s = self._read_semantic(
                        flush="home_and_return"
                    )
                    reusable_before = (_after_result, after, read_after_s)
                    reusable_ready_at = time.perf_counter()
                except Exception as exc:
                    outcome = self._handle_read_failure(exc, now=time.time())
                    if outcome is None:
                        reusable_before = None
                        reusable_ready_at = None
                        continue
                    stop_reason = outcome
                    break
            adjudicate_started = time.perf_counter()
            adjudication = adjudicate_predictions(
                plan, before, after,
                before_raw=getattr(_before_result, "state", None),
                after_raw=getattr(_after_result, "state", None),
            )
            # The input-death safety gate counts business actions still waiting
            # for this verdict; everything this round just adjudicated is done,
            # and anything older than the window no longer counts either.
            self._observe_generate_energy_multiplier(plan, before, after, adjudication)
            adjudicated_ids = {item.prediction_id for item in adjudication}
            # Keep the warehouse ledger honest: only a confirmed verdict moves a
            # piece in or out, so a refused drag never leaves a phantom entry.
            verdict_by_id = {item.prediction_id: item for item in adjudication}
            for prediction in plan.predictions:
                verdict = verdict_by_id.get(prediction.prediction_id)
                if verdict is None or verdict.status == "refuted":
                    continue
                if prediction.kind == "stash_to_warehouse" and prediction.source_before:
                    self._warehouse_parked.append(str(prediction.source_before))
                    self._save_warehouse_holds()
                elif prediction.kind == "retrieve_from_warehouse" and self._warehouse_parked:
                    self._warehouse_parked.pop(0)
                    self._save_warehouse_holds()
            in_flight_cutoff = time.time() - INPUT_DEAD_IN_FLIGHT_WINDOW_S
            business_action_log[:] = [
                entry
                for entry in business_action_log
                if entry["epoch"] >= in_flight_cutoff
                and entry["prediction_id"] not in adjudicated_ids
            ]
            save_evidence = getattr(
                self.actuator,
                "observe_business_save_evidence",
                None,
            )
            if callable(save_evidence):
                by_prediction_id = {item.prediction_id: item for item in adjudication}
                for item in executed:
                    outcome = by_prediction_id.get(item.action.prediction.prediction_id)
                    if (
                        outcome is not None
                        and item.action.prediction.kind in BUSINESS_PREDICTION_KINDS
                        and item.action.request is not None
                        and item.action.request.kind == "tap"
                    ):
                        save_evidence(
                            item.result.action_id,
                            # A deviation is something the device did do, so it
                            # is evidence of a landed input just as a strict
                            # confirmation is.
                            confirmed=outcome.status != "refuted",
                        )
            adjudicate_ms = (time.perf_counter() - adjudicate_started) * 1000
            business_refuted_streak, refuted_streak_reached = _update_business_refuted_streak(
                adjudication,
                business_refuted_streak,
                conditions.max_consecutive_refuted,
            )
            self._record_refused_moves(
                plan, adjudication, round_number, after=after,
                after_raw=getattr(_after_result, "state", None),
            )
            self._previous_read_epoch_ms = planning_read_epoch_ms
            exhaustion = self.exhaustion.observe(
                before,
                plan,
                adjudication,
                unavailable_merge_pairs=self._unavailable_merge_pairs(
                    before, just_landed
                ),
            )
            timing = RoundTimingV1(
                screenshot_ms=screenshot_ms,
                read_before_s=read_before_s,
                plan_ms=plan_ms,
                act_ms=act_ms,
                read_after_s=read_after_s,
                adjudicate_ms=adjudicate_ms,
                total_s=time.perf_counter() - round_started,
                read_reused=read_reused,
                foreground_settle_ms=foreground_settle_ms,
            )
            self._last_screenshot_sha = screenshot_sha
            # ``_open_chest_when_room_exists`` is itself an input.  Once the
            # post-action authoritative read reaches the target, or control
            # has been handed over, preserve this round and skip that input.
            post_read_stop = (
                "target_level_reached"
                if self._target_reached(after)
                else self._control_stop_reason()
            )
            chest_record = (
                None
                if post_read_stop is not None
                else self._open_chest_when_room_exists(after)
            )
            if chest_record is not None:
                screen_guard_actions = [*screen_guard_actions, chest_record]
            # F4-12 Phase 0: assemble the round's explicit behavior frame from
            # the state this round actually carried.  Nothing reads it yet;
            # Phase 1 moves the judging behind it.
            round_popup_attempts: dict[str, int] = {}
            for guard_action in screen_guard_actions:
                round_popup_attempts[guard_action.screen] = (
                    round_popup_attempts.get(guard_action.screen, 0) + 1
                )
            behavior_frame = BehaviorFrame(
                save=before,
                surface=current_screen,
                counters=BehaviorCounters(
                    guard_steps=len(screen_guard_actions),
                    handoff_rounds=dict(self._guard_handoff_rounds),
                    story_taps=self._story_advance_taps,
                    refuted_streak=business_refuted_streak,
                    guided_stalls=dict(self._guided_target_stalls),
                    forbidden_close_times={
                        surface: list(times)
                        for surface, times in self._forbidden_close_times.items()
                    },
                    relaunch_times=list(self._app_relaunch_times),
                    suppressed_pairs=dict(self._suppressed_merge_pairs),
                    suppressed_cells=dict(self._suppressed_generator_cells),
                    held_positions=sorted(just_landed),
                    cumulative_wait_s=cumulative_wait_s,
                    popup_attempts=round_popup_attempts,
                ),
                clocks=BehaviorClocks(
                    now=time.time(),
                    energy_enough_at=exhaustion.energy_enough_at,
                    energy_full_at=exhaustion.energy_full_at,
                    rounds_remaining=max_rounds - round_number,
                    wait_budget_remaining_s=(
                        None
                        if max_wait_s is None
                        else max(max_wait_s - cumulative_wait_s, 0.0)
                    ),
                ),
            )
            record = RoundRecordV1(
                round=round_number,
                screenshot_frame_id=frame.frame_id,
                screenshot_sha256=screenshot_sha,
                screenshot_changed_from_previous=(
                    None
                    if previous_screenshot_sha is None
                    else screenshot_sha != previous_screenshot_sha
                ),
                state_before=_summary(before),
                plan=plan,
                executed=executed,
                adjudication=adjudication,
                state_after=_summary(after),
                timing=timing,
                exhaustion=exhaustion,
                main_task_results=main_task_results,
                screen_guard_actions=screen_guard_actions,
                screen_guard_action_results=screen_guard_action_results,
                cold_start=cold_start,
                behavior_frame=behavior_frame.to_record(
                    save_summary=_summary(before).model_dump(mode="json")
                ),
            )
            rounds.append(record)
            self.replay.add_state(
                source="planner",
                payload=record.model_dump(mode="json"),
                latency_ms=timing.total_s * 1000,
            )
            previous_screenshot_sha = screenshot_sha

            if behavior_stop is not None:
                if behavior_stop[0] == "waiting_for_energy" and self._try_gem_spend(after):
                    reusable_before = None
                    reusable_ready_at = None
                    continue
                # F4-12 Phase 2: the state machine's stop lands in the shape
                # of the round-end ladder entry it replaces, after the record.
                stop_reason = self._apply_behavior_stop(behavior_stop)
                break

            if post_read_stop is not None:
                if post_read_stop == "target_level_reached":
                    self.replay.note(
                        f"target_level_reached: save level {after.resources.level} "
                        f">= target {self.target_level}"
                    )
                stop_reason = post_read_stop
                break

            if self._tutorial_follow_stalled is not None and not (
                self._board_full_under_guide
            ):
                stop_reason = "tutorial_follow_stalled"
                break
            if refuted_streak_reached:
                # The streak breaker must fire before replan_after_guard:
                # continuing here would re-plan with a zero prediction budget
                # (max_consecutive_refuted - streak == 0), which
                # _limit_business_predictions rejects.
                self.replay.alert(
                    "planner stopped after three consecutive refuted business predictions"
                )
                stop_reason = "refuted_streak"
                break
            if replan_after_guard:
                continue
            if action_guard_stopped:
                stop_reason = main_task_stop or "not_on_board"
                break
            if main_task_stop is not None:
                stop_reason = main_task_stop
                break
            if resource_wait_reached:
                # A bounded resource wait that the detector has already
                # attributed to energy is the legal energy wait, and it carries
                # a recovery time.  Reporting it as the generic resource_wait
                # would throw that away: the loop reaches this branch on the
                # first waiting round, before the detector can see the three
                # consecutive wait rounds its own verdict needs.
                if (
                    conditions.stop_on_exhaustion
                    and self.exhaustion.report.energy_needed is not None
                ):
                    if self._try_gem_spend(after):
                        reusable_before = None
                        reusable_ready_at = None
                        continue
                    stop_reason = self._note_energy_wait()
                    break
                self.replay.note("planner stopped on resource recovery wait")
                stop_reason = "resource_wait"
                break
            if wait_limit_reached:
                self.replay.note("planner stopped at max_wait_s")
                stop_reason = "max_wait_s"
                break
            if refuted_streak_reached:
                self.replay.alert(
                    "planner stopped after three consecutive refuted business predictions"
                )
                stop_reason = "refuted_streak"
                break
            if conditions.stop_on_exhaustion and self.exhaustion.exhausted():
                self.replay.note("planner reached exhausted_v0")
                stop_reason = "exhausted_v0"
                break
            if conditions.stop_on_exhaustion and self.exhaustion.awaiting_energy():
                if self._try_gem_spend(after):
                    reusable_before = None
                    reusable_ready_at = None
                    continue
                stop_reason = self._note_energy_wait()
                break

        ended_at = datetime.now(timezone.utc).isoformat()
        wall_seconds = time.perf_counter() - wall_started
        adjudications = [item for round_item in rounds for item in round_item.adjudication]
        confirmed = sum(item.status == "confirmed" for item in adjudications)
        deviated = sum(item.status == "confirmed_with_deviation" for item in adjudications)
        refuted = sum(item.status == "refuted" for item in adjudications)
        denominator = confirmed + deviated + refuted
        game_actions = sum(
            item.action.kind != "wait" for round_item in rounds for item in round_item.executed
        )
        input_dead_context: dict[str, Any] | None = None
        if stop_reason == "input_dead_unrecovered":
            in_flight_cutoff = time.time() - INPUT_DEAD_IN_FLIGHT_WINDOW_S
            input_dead_context = {
                "surface": input_dead_surface,
                "unadjudicated_actions_60s": sum(
                    1
                    for entry in business_action_log
                    if entry["epoch"] >= in_flight_cutoff
                ),
                "adb_contrast": self._last_input_dead_adb_contrast(),
            }
        return LoopReportV1(
            started_at=started_at,
            ended_at=ended_at,
            stop_reason=stop_reason,
            input_restart_recoveries=self.input_restart_recoveries,
            app_relaunch_recoveries=self.app_relaunch_recoveries,
            cold_start_policy=self.cold_start_policy,
            cold_start_warmups=self.cold_start_warmups,
            rounds=rounds,
            confirmed_predictions=confirmed,
            refuted_predictions=refuted,
            deviation_predictions=deviated,
            deviation_rate=0.0 if denominator == 0 else deviated / denominator,
            refuted_rate=0.0 if denominator == 0 else refuted / denominator,
            game_actions=game_actions,
            wall_seconds=wall_seconds,
            effective_apm=(0.0 if wall_seconds == 0 else game_actions * 60 / wall_seconds),
            exhaustion=self.exhaustion.report,
            input_dead_context=input_dead_context,
            unexplored_surface=(
                self._unexplored_surface_stop
                if stop_reason == "unexplored_surface"
                else None
            ),
            target_level=self.target_level,
        )


__all__ = [
    "AdjudicationStatus",
    "BackendName",
    "CLAIM_SAVE_EVIDENCE_FIELDS",
    "CLAIM_SAVE_EVIDENCE_KINDS",
    "build_claim_save_delta",
    "detect_delivery_control_centers",
    "ExecutedActionV1",
    "GameLoop",
    "GenerateDeviationV1",
    "LoopReportV1",
    "PredictionAdjudicationV1",
    "RoundRecordV1",
    "RoundTimingV1",
    "StateSummaryV1",
    "StopConditionsV1",
    "adjudicate_predictions",
    "_visual_occupancy_coverage",
]
