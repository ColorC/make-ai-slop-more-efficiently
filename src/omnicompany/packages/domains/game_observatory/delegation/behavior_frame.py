# [OMNI] origin=kimi created_by=kimi intent="make the F4-12 behavior frame explicit and serializable"
"""F4-12 Phase 0: the explicit per-round behavior frame.

Every game-loop round already carries this state; it used to live scattered
across ``GameLoop`` instance fields and ``run()`` locals.  Phase 0 only makes
the frame explicit and writes it into the round record -- the judging logic
stays where it is, and Phase 1 moves it behind a pure ``decide(frame)``.

The frame is a plain dataclass so a test can construct one without the loop;
``to_record`` is the only serialization, so the on-disk shape has one source.
``save`` holds the live ``GameStateSemanticV1`` for the coming Phase 1 pure
function; on disk it serializes as the round's state summary, because the full
semantic payload is already recoverable from the same round's record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class BehaviorCounters:
    """The counters that used to be scattered across the loop and its rounds.

    ``story_taps`` counts advances on the dialogue the run is currently in;
    per-dialogue identity arrives with Phase 1, which is also when this stops
    being a single integer.  ``suppressed_pairs`` keys are unordered cell
    pairs, ``suppressed_cells`` keys are generator cells, and both serialize as
    text in ``to_record``.
    """

    guard_steps: int = 0
    handoff_rounds: dict[str, int] = field(default_factory=dict)
    story_taps: int = 0
    refuted_streak: int = 0
    guided_stalls: dict[str, int] = field(default_factory=dict)
    forbidden_close_times: dict[str, list[float]] = field(default_factory=dict)
    relaunch_times: list[float] = field(default_factory=list)
    suppressed_pairs: dict[tuple[tuple[int, int], tuple[int, int]], int] = field(
        default_factory=dict
    )
    suppressed_cells: dict[tuple[int, int], int] = field(default_factory=dict)
    held_positions: list[tuple[int, int]] = field(default_factory=list)
    cumulative_wait_s: float = 0.0
    popup_attempts: dict[str, int] = field(default_factory=dict)
    # F4-12 Phase 1: the exhaustion detector's own state, so the pure decide()
    # can evaluate verdict conditions a and b (exhaustion.py:342-344, 391-392).
    consecutive_wait_rounds: int = 0
    waiting_for_energy: bool = False
    energy_rechecked_without_action: bool = False
    # F4-12 Phase 1: the run-level delivery budget is spent by the loop, not by
    # the plan (game_loop.py:2914, 3106-3110, 3391-3392).
    deliveries_executed: int = 0


@dataclass(frozen=True)
class BehaviorClocks:
    """Wall-clock anchors and the segment/session budget margins."""

    now: float
    energy_enough_at: int | None = None
    energy_full_at: int | None = None
    # No existing runtime source emits a cooldown clock yet; the field is the
    # Phase 1 shape and stays None until one does.
    cooldown_ready_at: float | None = None
    rounds_remaining: int | None = None
    wait_budget_remaining_s: float | None = None
    # F4-12 Phase 1: ``counters.forbidden_close_times`` are time.monotonic()
    # stamps (game_loop.py:2014), so the recurrence window can only be judged
    # against a monotonic now, never against the epoch ``now``.
    now_monotonic: float | None = None


@dataclass(frozen=True)
class BehaviorSignals:
    """Distilled device- and flow-event signals one frame may carry.

    Phase 1.  These are the decision inputs that are not pixels and not save
    semantics: a failed read arrives as an exception at the loop, dead input
    arrives as an actuator result, and the main-task flow reports its own
    outcome.  Everything defaults to quiet, so a frame assembled by Phase 0's
    game_loop code reads as an ordinary, healthy round.
    """

    frame_unavailable: bool = False
    read_error: str | None = None
    app_is_gone: bool = False
    input_dead_unrecovered: bool = False
    main_task_flow_status: str | None = None
    board_full_under_guide: bool = False


@dataclass(frozen=True)
class BehaviorPolicy:
    """The static gating knobs a decision reads.

    Defaults mirror PolicyV1 (planner.py:100-117) and StopConditionsV1
    (game_loop.py:253-257); ``chest_states`` carries the runtime chest rows
    the loop derives from the raw save (game_loop.py:536-592) because the
    semantic model drops the spread columns of chest-kind cells.
    """

    version: str = "v2"
    enable_main_task: bool = False
    max_main_tasks_per_round: int = 1
    max_actions_per_round: int = 6
    max_deliveries_per_run: int = 3
    max_energy_collects_per_round: int = 1
    max_sells_per_round: int = 2
    energy_recovery_seconds: int = 60
    max_consecutive_refuted: int = 3
    stop_on_exhaustion: bool = True
    stop_on_resource_wait: bool = False
    chest_states: tuple[Any, ...] = ()


@dataclass(frozen=True)
class BehaviorFrame:
    """Everything one behavior decision reads: save, surface, counters, clocks."""

    save: Any
    surface: str
    counters: BehaviorCounters
    clocks: BehaviorClocks
    # F4-12 Phase 1 additions, all optional so the Phase 0 assembly in
    # game_loop.py:3498-3529 keeps working unchanged.
    #
    # ``surface_anchors`` are the identify_screen matched anchors; the story
    # advance picks its element from them (popup_handler.py:90-96).
    # ``signals`` carries device/flow events; ``policy`` carries the run's
    # static gating knobs; ``frame_ref`` is assembly metadata threaded into
    # Escalate payloads and never read by a judging rule.
    surface_anchors: tuple[str, ...] = ()
    signals: BehaviorSignals = field(default_factory=BehaviorSignals)
    policy: BehaviorPolicy = field(default_factory=BehaviorPolicy)
    frame_ref: str | None = None
    # F4-12 Phase 2, appendix A rulings 1 and 3 (spec
    # F4-12-behavior-state-machine.md:90-95).  Both default to None, which
    # keeps the Phase 1 approximation paths for unit tests; the Phase 2
    # assembly in game_loop always injects real values.
    #
    # ``sell_protected_chains`` is the ItemModelConfig sell filter table the
    # planner applies (planner.py:828-833), encoded as entry strings:
    # ``"type:<type_id>"`` marks a type the config itself protects (Rare,
    # ProtectLevel set, or special without a coin collection -- the exact
    # replacement for Phase 1's collect_rewards approximation), and
    # ``"chain:<chain_id>"`` marks a chain an open order currently requires,
    # keyed the way semantics keys ``cell.chain_id`` (semantics.py:319-324:
    # the same-segment walk root shares the type's own ``// 100`` segment).
    # ``delivery_control_visible`` is the pixel truth from
    # detect_delivery_control_centers on the round's frame: True/False is
    # authoritative for the deliver-under-guide ruling and the save-side
    # ``fulfillable_now`` is downgraded to supporting evidence.
    sell_protected_chains: frozenset[str] | None = None
    delivery_control_visible: bool | None = None
    # F4-12 Phase 2 shadow finding (2026-09-08): planner.py:932-955 marks a
    # fulfillable order whose slot has no resolvable tap point as
    # blocked_missing_geometry and plans past it; without the same input the
    # state machine would decide an unexecutable deliver every round.
    # ``deliverable_slots`` is the assembly's per-round answer to "which
    # slots can the executor actually tap" (delivery_control_overrides plus
    # the slots the calibrated order geometry covers).  None keeps the Phase
    # 1 behavior for unit tests; the wired loop always injects the real set.
    deliverable_slots: frozenset[int] | None = None

    def to_record(self, *, save_summary: Mapping[str, Any]) -> dict[str, Any]:
        """Serialize for the round record; the save lands as its summary.

        The on-disk schema is frozen at the Phase 0 shape (the game_loop
        record tests pin the exact key sets); the Phase 1 additions
        (``surface_anchors``, ``signals``, ``policy``, ``frame_ref``,
        ``sell_protected_chains``, ``delivery_control_visible``,
        ``deliverable_slots`` and the new counter/clock fields) are
        in-process decision inputs and are deliberately not serialized.
        """

        counters = self.counters
        clocks = self.clocks
        return {
            "save": dict(save_summary),
            "surface": self.surface,
            "counters": {
                "guard_steps": counters.guard_steps,
                "handoff_rounds": dict(counters.handoff_rounds),
                "story_taps": counters.story_taps,
                "refuted_streak": counters.refuted_streak,
                "guided_stalls": dict(counters.guided_stalls),
                "forbidden_close_times": {
                    surface: list(times)
                    for surface, times in counters.forbidden_close_times.items()
                },
                "relaunch_times": list(counters.relaunch_times),
                "suppressed_pairs": {
                    f"{pair[0][0]},{pair[0][1]}->{pair[1][0]},{pair[1][1]}": round_number
                    for pair, round_number in counters.suppressed_pairs.items()
                },
                "suppressed_cells": {
                    f"{cell[0]},{cell[1]}": round_number
                    for cell, round_number in counters.suppressed_cells.items()
                },
                "held_positions": [
                    [row, col] for row, col in counters.held_positions
                ],
                "cumulative_wait_s": counters.cumulative_wait_s,
                "popup_attempts": dict(counters.popup_attempts),
            },
            "clocks": {
                "now": clocks.now,
                "energy_enough_at": clocks.energy_enough_at,
                "energy_full_at": clocks.energy_full_at,
                "cooldown_ready_at": clocks.cooldown_ready_at,
                "rounds_remaining": clocks.rounds_remaining,
                "wait_budget_remaining_s": clocks.wait_budget_remaining_s,
            },
        }


__all__ = [
    "BehaviorClocks",
    "BehaviorCounters",
    "BehaviorFrame",
    "BehaviorPolicy",
    "BehaviorSignals",
]
