# [OMNI] origin=codex created_by=codex intent="detect bounded Romantic Restaurant play exhaustion"
"""Deterministic v0 exhaustion signals for the delegated game loop."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .planner import PlanV1
from .semantics import GameStateSemanticV1


ExhaustionVerdict = Literal[
    "playing",
    "waiting",
    "waiting_for_energy",
    "blocked_by_coins",
    "exhausted_v0",
]

# F4-07 section 2.  The complete set of reasons unattended play can stop, and
# the only vocabulary `rr_drive` reports in its `stop_taxonomy`.  Two of them
# are legal ends that carry a resume time; the rest end the run.
DriverStopReason = Literal[
    # Energy has to regenerate before the next generation is affordable.  Legal:
    # the driver sleeps to `energy_enough_at` and keeps playing.
    "legal_wait_energy",
    # A generator's or chest's own timer is what play is waiting on.  Legal:
    # the driver sleeps a bounded interval and keeps playing.
    "legal_wait_timer",
    # The guard could not get back to the board, or landed on a surface nobody
    # has registered.  The driver stops so a human can register it.
    "blocked_unknown_surface",
    # A registered red-line surface could not be closed with its registered
    # control.  The driver stops; it never improvises on these.
    "blocked_forbidden_surface",
    # The same red-line surface kept coming back and being closed.  F4-09: the
    # 2026-09-07 00:30Z segment closed update-game-modal twice in ninety
    # seconds and a business tap went out on the third appearance.  Closing it
    # forever is playing ping-pong with a monetization surface; the run stops
    # and asks instead.
    "blocked_forbidden_surface_recurring",
    # The renderer stopped accepting input and even the app restart did not
    # bring it back.
    "input_dead_unrecovered",
    # The authoritative save could not be read.  F4-09 1.5: this used to be
    # reported as blocked_unknown_surface, which sent an operator looking for a
    # surface to register while the frame beside it showed an ordinary board.
    # A read failure is a channel failure, and the driver retries it once
    # before it stops.
    "save_channel_failed",
    # The app died and was relaunched too often to keep calling it a transient.
    # F4-10, planner ruling of 2026-09-07: an app that goes away is a
    # recoverable device event, and the recovery ladder relaunches it; three
    # relaunches inside one hour is a device or build problem instead, and the
    # run stops and asks rather than relaunching all day.
    "app_crash_recurring",
    # The segment process itself died on a Python code fault -- stderr carried
    # a traceback, typically a circular import from a half-edited working tree.
    # The 2026-09-07 10:54Z segment exited 1 on exactly that and the driver
    # reported it as blocked_unknown_surface, which stopped the guardian for a
    # ruling on a surface that did not exist and idled the run for ten hours.
    # A code crash is a facility fault, not a surface: the driver retries the
    # segment once and then stops for a ruling; it never restarts by itself.
    "facility_code_crash",
    # NO_PROGRESS_SEGMENTS consecutive segments produced no confirmed business
    # action, no task-chain movement and no authoritative state delta, and
    # named no legal wait (rr_drive.Driver.made_progress).  This is the
    # "nothing left to play" verdict: the game is reachable and responsive
    # and still yields nothing.
    "no_progress",
    # 2026-09-11 ruling after the 18:22Z main-task drive: the event HUD hid
    # the task notebook, so the main line is unavailable by game mechanism.
    # The drive ends the shift cleanly on the first such segment -- a normal
    # result for the event's duration, not a finding; the guardian continues.
    "main_task_unavailable",
    # The run's own wall-clock budget ran out.  Not a property of the game.
    "max_wall_clock",
]

# How many consecutive segments with zero progress of any kind -- no
# confirmed business action, no task-chain movement, no authoritative state
# delta -- and no legal wait reason count as exhausted play.  Three, because
# a single segment
# can end at zero for reasons that are not exhaustion at all (a refuted streak
# on one misread generator, a main-task flow that stopped on cost), and two
# consecutive were observed inside otherwise productive F4-06 runs.
NO_PROGRESS_SEGMENTS = 3

# Segment stop reasons that let the driver start the next segment at once.
#
# ``main_task_unavailable`` sits here by ruling (2026-09-11): it must never
# classify onto an escalation.  The driver additionally ends the shift on the
# first such segment (rr_drive.Driver.run) -- the main line stays hidden for
# the event's duration, so further segments would only spin cold starts.
CONTINUING_STOP_REASONS = frozenset(
    {
        "max_rounds",
        "main_task_completed",
        "main_task_stopped",
        "main_task_unavailable",
        "refuted_streak",
        "max_wait_s",
    }
)

# Segment stop reason -> the driver stop reason it maps to.  Anything absent
# from both this mapping and CONTINUING_STOP_REASONS is unknown to the driver
# and is treated as blocked_unknown_surface, which stops and asks for a ruling.
SEGMENT_STOP_TAXONOMY: dict[str, DriverStopReason] = {
    "waiting_for_energy": "legal_wait_energy",
    "resource_wait": "legal_wait_timer",
    "not_on_board": "blocked_unknown_surface",
    "unknown_screen": "blocked_unknown_surface",
    # F4-13: a registered-but-unexplored surface asks for the agent handler,
    # which is the unknown-surface escalation path with one automated step in
    # front of it.
    "unexplored_surface": "blocked_unknown_surface",
    "save_channel_failed": "save_channel_failed",
    # F4-09: a guide nobody can follow needs a human to register what it wants,
    # which is what blocked_unknown_surface already means and exits for.
    "tutorial_follow_stalled": "blocked_unknown_surface",
    "forbidden_surface": "blocked_forbidden_surface",
    "forbidden_surface_recurring": "blocked_forbidden_surface_recurring",
    "input_dead_unrecovered": "input_dead_unrecovered",
    "app_crash_recurring": "app_crash_recurring",
    # F4-10 1.5 (F6-04 leftover).  The loop called this stop `input_dead`
    # until F4-06 renamed it; five archived segments still carry the old
    # name and the fallback was translating them into
    # `blocked_unknown_surface`, which sends a reader looking for a surface
    # to register when the renderer was what stopped answering.  The alias
    # is a rename, not a new verdict: it maps onto the same driver stop
    # reason, the same exit code and the same escalation.
    "input_dead": "input_dead_unrecovered",
    "exhausted_v0": "no_progress",
}

# Exit codes, so an operator or a scheduler can tell the four outcomes apart
# without parsing the report.
DRIVER_EXIT_CODES: dict[DriverStopReason, int] = {
    "max_wall_clock": 0,
    "no_progress": 0,
    "legal_wait_energy": 0,
    "legal_wait_timer": 0,
    # A clean shift end: the event hid the main line, nothing was wrong.
    "main_task_unavailable": 0,
    "blocked_unknown_surface": 3,
    "blocked_forbidden_surface": 4,
    "blocked_forbidden_surface_recurring": 4,
    "input_dead_unrecovered": 5,
    # Its own code: a save channel that will not answer needs the device or the
    # host looked at, not a surface registered.
    "save_channel_failed": 6,
    # And its own again: an app that keeps dying is neither a surface nor a
    # channel, and the thing to look at is the build or the emulator.
    "app_crash_recurring": 7,
    # And its own once more: the segment process dying on a Python code fault
    # is a facility fault in the working tree, which no surface registration,
    # channel check or build restart will fix.
    "facility_code_crash": 8,
}

# Driver stop reasons the driver retries once before it accepts them.  The
# retry is what separates a transient read failure from a broken channel; one
# attempt, because a channel that fails twice thirty seconds apart is not
# transient.
RETRYABLE_STOP_REASONS = frozenset({"save_channel_failed"})
# F4-09.  How often the same red-line surface may be closed inside one run
# before the run stops instead of closing it again, and the window that count
# is taken over.
FORBIDDEN_RECURRENCE_LIMIT = 3
FORBIDDEN_RECURRENCE_WINDOW_S = 300.0
SAVE_CHANNEL_RETRY_DELAY_S = 30.0
SAVE_CHANNEL_RETRIES = 1
# A code crash gets the same bounded second chance as a save channel, on a
# slightly longer fuse: sixty seconds is enough for an editor to finish
# writing the tree it was halfway through when the segment loaded it.
CODE_CRASH_RETRY_DELAY_S = 60.0
CODE_CRASH_RETRIES = 1
# The stderr shapes that mark a dead segment process as a code crash rather
# than a game state.  Anything else stays blocked_unknown_surface: a process
# that failed without a Python traceback is still a surface question.
CODE_CRASH_STDERR_MARKERS = (
    "Traceback (most recent call last)",
    "ImportError",
    "ModuleNotFoundError",
)


def stderr_marks_a_code_crash(stderr_tail: Any) -> bool:
    """Whether a dead segment process's stderr tail is a Python code fault."""

    if isinstance(stderr_tail, str):
        lines: list[str] = [stderr_tail]
    elif isinstance(stderr_tail, (list, tuple)):
        lines = [str(line) for line in stderr_tail]
    else:
        return False
    return any(marker in line for line in lines for marker in CODE_CRASH_STDERR_MARKERS)
# F4-10.  How many app relaunches inside one window stop being a recoverable
# device event and become something to ask about, and how long that window is.
APP_RELAUNCH_LIMIT = 3
APP_RELAUNCH_WINDOW_S = 3600.0


def classify_segment_stop(stop_reason: str) -> DriverStopReason | None:
    """The driver stop reason a segment implies, or None to keep playing."""

    if stop_reason in CONTINUING_STOP_REASONS:
        return None
    return SEGMENT_STOP_TAXONOMY.get(stop_reason, "blocked_unknown_surface")


class ExhaustionReportV0(BaseModel):
    """The bounded detector conditions, the verdict, and the recovery clock.

    ``waiting_for_energy`` is not a failure: it is the legal end of play when
    the only remaining move costs energy the account does not have.  The report
    then carries the epoch second at which the account can act again, derived
    from ``Energy.generateTime`` and ``EnergyModel``'s 120 s per point.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    a: bool
    b: bool
    c: bool
    verdict: ExhaustionVerdict
    energy_needed: int | None = None
    energy_enough_at: int | None = None
    energy_full_at: int | None = None


def _all_wait(plan: PlanV1) -> bool:
    return not plan.actions or all(action.kind == "wait" for action in plan.actions)


def _can_recover_space(plan: PlanV1) -> bool:
    return any(
        action.kind in {"open_chest", "sell"} or "clear-cobweb" in action.intent
        for action in plan.actions
    )


def _affordable_generation_cost(state: GameStateSemanticV1) -> int | None:
    """Cheapest energy price among generators that only lack energy.

    Returns ``None`` when energy would not unblock anything: no board space, or
    no generator with stock left.  Only then is a wait for energy meaningless.
    """

    if not state.board.empty_cells:
        return None
    return min(
        (
            max(cell.generator.cost_energy or 1, 1)
            for row in state.board.cells
            for cell in row
            if cell.kind == "generator"
            and cell.generator is not None
            and cell.generator.inventory_exhausted is not True
        ),
        default=None,
    )


def _can_generate(state: GameStateSemanticV1) -> bool:
    energy = state.resources.energy or 0
    return bool(state.board.empty_cells and state.board.generators and energy >= 1)


def _usable_merge_pairs(
    state: GameStateSemanticV1,
    unavailable_merge_pairs: frozenset[tuple[tuple[int, int], tuple[int, int]]],
) -> list[Any]:
    """Board pairs the planner would actually merge, not merely see.

    F4-10, planner ruling B of 2026-09-07.  A pair the planner is holding out --
    because merging it was refuted a round or two ago, or because one of its
    pieces has only just landed -- is not a move the run has.  Counting it as
    one is how nine energy waits in the 2026-09-07 02:46Z run were attributed to
    "resources" instead of to energy: the board still showed one or two pairs,
    the planner would not touch any of them, energy was zero, and the driver
    never learned when energy would come back.
    """

    if not unavailable_merge_pairs:
        return list(state.board.mergeable_pairs)
    usable = []
    for pair in state.board.mergeable_pairs:
        cells = _pair_positions(pair)
        if cells is not None and cells in unavailable_merge_pairs:
            continue
        usable.append(pair)
    return usable


def _pair_positions(pair: Any) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """The unordered cell pair a mergeable-pair record names, if it names one."""

    first = getattr(pair, "source", None) or getattr(pair, "first", None)
    second = getattr(pair, "target", None) or getattr(pair, "second", None)
    if first is None or second is None:
        return None
    try:
        left = (int(first.row), int(first.col))
        right = (int(second.row), int(second.col))
    except (AttributeError, TypeError, ValueError):
        return None
    return (left, right) if left <= right else (right, left)


def _energy_starved(
    state: GameStateSemanticV1,
    plan: PlanV1,
    unavailable_merge_pairs: frozenset[tuple[tuple[int, int], tuple[int, int]]]
    | None = None,
) -> int | None:
    """The energy price that would unblock play, or ``None`` if energy is not it.

    Energy is the sole blocker only when a stocked generator and an empty cell
    are both waiting, nothing on the board can still be merged or collected for
    energy, and no space-recovery action is available.

    F4-11, planner ruling of 2026-09-07.  "Can still be merged" now means the
    plan, not the board.  F4-10 ruling B narrowed the board set by the pairs the
    loop was holding out, which was not enough: the 2026-09-07 07:55Z session
    spent nine segments and about seventy per cent of its wall clock on flat
    three-hundred-second naps because the board still showed a pair the planner
    would not use.  Segment G round four is the specimen -- energy zero, seven
    free cells, two mergeable pairs, and a plan that was all wait because one
    pair held a piece that had just landed and the other would have spent the
    last 203 an open order requires.  A plan with no business action in it is
    the run saying it has no move, and it is the authority here.
    """

    cost = _affordable_generation_cost(state)
    if cost is None or (state.resources.energy or 0) >= cost:
        return None
    if state.board.energy_items or _can_recover_space(plan):
        return None
    if _all_wait(plan):
        return cost
    if _usable_merge_pairs(state, unavailable_merge_pairs or frozenset()):
        return None
    return cost


def _board_sale_value(state: GameStateSemanticV1) -> int:
    return sum(
        cell.sell_price or 0
        for row in state.board.cells
        for cell in row
        if cell.kind == "item"
    )


class ExhaustionDetectorV0:
    """Accumulate only the v0 conditions defined by the F4-01 contract."""

    def __init__(self) -> None:
        self.consecutive_wait_rounds = 0
        self.waiting_for_energy = False
        self.energy_rechecked_without_action = False
        self.report = ExhaustionReportV0(
            a=False,
            b=False,
            c=False,
            verdict="playing",
        )

    def observe(
        self,
        state: GameStateSemanticV1,
        plan: PlanV1,
        adjudication: list[Any] | None = None,
        unavailable_merge_pairs: frozenset[tuple[tuple[int, int], tuple[int, int]]]
        | None = None,
    ) -> ExhaustionReportV0:
        """Observe one planned round and return the cumulative report.

        ``unavailable_merge_pairs`` are the board pairs the planner is holding
        out this round.  The loop knows them; the board does not.
        """

        del adjudication
        only_wait = _all_wait(plan)
        if only_wait:
            self.consecutive_wait_rounds += 1
        else:
            self.consecutive_wait_rounds = 0

        energy = state.resources.energy or 0
        usable_pairs = _usable_merge_pairs(
            state, unavailable_merge_pairs or frozenset()
        )
        no_merges = not usable_pairs
        if self.waiting_for_energy and energy >= 1 and only_wait:
            self.energy_rechecked_without_action = True
        if only_wait and energy < 1 and no_merges:
            self.waiting_for_energy = True

        needed = (
            _energy_starved(state, plan, unavailable_merge_pairs)
            if only_wait
            else None
        )
        enough_at = (
            state.resources.energy_enough_at(needed) if needed is not None else None
        )
        condition_a = self.consecutive_wait_rounds >= 3
        condition_b = self.energy_rechecked_without_action
        task = state.main_task
        wealth = (state.resources.coins or 0) + _board_sale_value(state)
        condition_c = bool(
            task is not None
            and task.affordable is False
            and task.cost is not None
            and wealth < task.cost
            and no_merges
            and not _can_generate(state)
            and not _can_recover_space(plan)
        )
        if condition_a and condition_b:
            verdict: ExhaustionVerdict = "exhausted_v0"
        elif condition_c:
            verdict = "blocked_by_coins"
        elif condition_a and needed is not None:
            verdict = "waiting_for_energy"
        elif condition_a:
            verdict = "waiting"
        else:
            verdict = "playing"
        self.report = ExhaustionReportV0(
            a=condition_a,
            b=condition_b,
            c=condition_c,
            verdict=verdict,
            energy_needed=needed,
            energy_enough_at=enough_at,
            energy_full_at=state.resources.energy_full_at,
        )
        return self.report

    def exhausted(self) -> bool:
        return self.report.verdict == "exhausted_v0"

    def awaiting_energy(self) -> bool:
        """Distinct from the ``waiting_for_energy`` instance flag used by v0 b."""

        return self.report.verdict == "waiting_for_energy"


class ExhaustionDetectorV1(ExhaustionDetectorV0):
    """Recovery-aware detector; it never exhausts while a v2 space action exists."""


__all__ = [
    "APP_RELAUNCH_LIMIT",
    "APP_RELAUNCH_WINDOW_S",
    "CODE_CRASH_RETRIES",
    "CODE_CRASH_RETRY_DELAY_S",
    "CODE_CRASH_STDERR_MARKERS",
    "CONTINUING_STOP_REASONS",
    "FORBIDDEN_RECURRENCE_LIMIT",
    "FORBIDDEN_RECURRENCE_WINDOW_S",
    "RETRYABLE_STOP_REASONS",
    "SAVE_CHANNEL_RETRIES",
    "SAVE_CHANNEL_RETRY_DELAY_S",
    "DRIVER_EXIT_CODES",
    "DriverStopReason",
    "NO_PROGRESS_SEGMENTS",
    "SEGMENT_STOP_TAXONOMY",
    "classify_segment_stop",
    "stderr_marks_a_code_crash",
    "ExhaustionDetectorV0",
    "ExhaustionDetectorV1",
    "ExhaustionReportV0",
    "ExhaustionVerdict",
]
