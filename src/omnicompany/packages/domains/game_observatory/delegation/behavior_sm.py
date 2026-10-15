# [OMNI] origin=kimi created_by=kimi intent="F4-12 Phase 1: pure behavior state machine decide(frame)"
"""F4-12 Phase 1: the pure ``decide(frame) -> BehaviorDecision`` function.

Spec: docs/plans/game-observatory/[2026-09-05]ROMANTIC-RESTAURANT-FULL-DELEGATION-v1/
specs/F4-12-behavior-state-machine.md, sections 2 and 5 (Phase 1).

``decide`` reads only the frame: no IO, no sleep, no randomness.  Every rule
below names the current-code branch it transcribes, so a reviewer can diff the
state machine against the imperative loop line by line.  Phase 2 wires this
module into game_loop behind the opt-in ``use_behavior_sm`` flag (default
False keeps the legacy planner path byte-identical).

One decision per call.  The current planner returns a batch per round
(planner.py:858 ``plan_batch``); the state machine instead answers "the one
highest-priority thing to do now", and the Phase 2 wiring re-decides on the
next round's frame.  Branch conditions and their priority order are
transcribed from ``plan_batch`` with the resource cleanup guard: deliver > main
task > resource merge/collection > free chest > merge > collect energy >
generate > sell > wait (planner.py:914-1616).

Constants that live in game_loop today (GUARD_HANDOFF_SURFACES, stall budgets,
...) are imported from there rather than duplicated; game_loop imports this
module lazily inside the wiring method to keep module loading acyclic.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

from .behavior_frame import BehaviorFrame
from .exhaustion import (
    APP_RELAUNCH_LIMIT,
    APP_RELAUNCH_WINDOW_S,
    FORBIDDEN_RECURRENCE_LIMIT,
    FORBIDDEN_RECURRENCE_WINDOW_S,
)
from .game_loop import (
    BOARD_FULL_UNDER_GUIDE_DROP_CELLS,
    GUARD_HANDOFF_SURFACES,
    MAX_GUARD_HANDOFF_ROUNDS,
    MAX_STORY_ADVANCE_TAPS,
    guided_target_stall_budget,
)
from .planner import (
    ChestRuntimeStateV1,
    DEFAULT_CONFIG_DIR,
    SUPPORTED_FREE_COLLECT_REWARD_TYPES,
    SURVEYED_CHEST_OUTPUT_HINTS,
    configured_free_chest_types,
    is_free_chest_type,
    _can_spend_cobweb_source,
    _can_spend_pair,
    _chest_actionability,
    _configured_chest_outputs,
    configured_timer_chest_types,
    _first_cache_item,
    _delivery_priority,
    _outstanding_required_counts,
    _required_reserves,
)
from .popup_handler import STORY_ADVANCE_GUARD_BUDGET, STORY_DIALOGUE_SURFACE


BehaviorActionKind = Literal[
    "deliver",
    "start_main_task",
    "open_chest",
    "merge",
    "collect_energy",
    "collect_reward",
    "claim_reward_queue",
    "generate",
    "sell",
    "popup",
    "navigation",
    "wait",
    "relaunch",
]


@dataclass(frozen=True)
class BehaviorActionV1:
    """One decided action.

    Popup dispositions name the surface and the registry element; board
    actions name cells.  Coordinates stay with the registry/geometry lookups
    in the Phase 2 executor -- deciding *what* is this module's whole job.
    """

    kind: BehaviorActionKind
    intent: str
    reason: str
    screen: str | None = None
    popup_kind: str | None = None
    element: str | None = None
    position: tuple[int, int] | None = None
    target: tuple[int, int] | None = None
    wait_ms: int | None = None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Act:
    action: BehaviorActionV1


@dataclass(frozen=True)
class WaitUntil:
    """Waiting is an intent, never a sleep; the executor redeems it (spec §4)."""

    epoch: float
    reason: str


@dataclass(frozen=True)
class Stop:
    """A routine stop; ``reason`` is a LoopStopReason literal, unchanged."""

    reason: str


@dataclass(frozen=True)
class Escalate:
    """An uncovered state: stop and wake the agent with the judging trace.

    ``reason`` carries the LoopStopReason literal this state maps to, so the
    stop taxonomy stays byte-identical.
    """

    reason: str
    frame_ref: str | None
    detail: str = ""


BehaviorDecision = Act | WaitUntil | Stop | Escalate


# -- registry-driven surface tables ------------------------------------------
#
# decide() may not do IO, so the registry facts it needs are transcribed here
# from navigation.v1.json and popup_handler.action_for_screen.  The test suite
# loads the real registry and asserts these tables match it entry for entry,
# so a registry edit without a matching edit here fails the drift tests.

# navigation.v1.json safety.forbidden_surfaces: name -> close_element.
FORBIDDEN_CLOSE_ELEMENTS: dict[str, str] = {
    "doggy-express-real-money-offer": "close-only",
    "intro-chest-real-money-offer": "close-only",
    "update-game-modal": "close-only",
    "spring-picnic-real-money-offer": "close-only",
    "store-rating-prompt": "close-only",
    "warehouse-price-panel": "close",
    "energy-supply-panel": "close",
    "spring-picnic-real-money-offer-399": "close-only",
    "spring-picnic-real-money-offer-any-tier": "close-only",
    "save-load-login": "close-only",
    # 2026-09-16: the gem store shows priced SKUs; it is closed, never tapped.
    "shop-gems": "close",
    # 2026-09-16: the level-up offer that opened on reaching 13 and stopped the
    # run as not_on_board.  The level number changes; the panel does not.
    "level-gift-real-money-offer": "close-only",
}

# navigation.py:522-528: the literal "transition" plus every registry surface
# whose kind is "transition" (navigation.v1.json: scene-transition-curtain,
# loading-splash, restaurant-scene-cinematic).
TRANSITION_SURFACES = frozenset(
    {
        "transition",
        "scene-transition-curtain",
        "loading-splash",
        "restaurant-scene-cinematic",
    }
)

# popup_handler.action_for_screen, the entries whose disposition does not
# depend on attempt counts or matched anchors: surface -> (kind, element).
POPUP_DISPOSITIONS: dict[str, tuple[str, str]] = {
    "board-story-prompt": ("tutorial-follow", "advance-bubble"),  # popup_handler.py:168-174
    "rave-reviews-event-intro": ("close", "close-only"),  # popup_handler.py:175-176
    "energy-party-announcement": ("close", "close-only"),  # popup_handler.py:177-183
    "energy-party-announcement-fullscreen": (
        "close",
        "tap-anywhere-continue",
    ),  # popup_handler.py:184-190
    "rave-reviews-complete-panel": ("close", "close-only"),  # popup_handler.py:191-198
    "level-up-reward": ("free-claim", "claim-free-level-reward"),  # popup_handler.py:199-205
    "forced-shop-tutorial": ("tutorial-follow", "guided-shop-cart"),  # popup_handler.py:208-214
    "tutorial-overlay": ("tutorial-follow", "guided-target"),  # popup_handler.py:215-221
    "decoration-choice": ("selection", "left-option"),  # popup_handler.py:222-223
    "decoration-confirmation": ("confirm", "confirm"),  # popup_handler.py:224-225
    "newbie-gift": ("free-claim", "open-gift"),  # popup_handler.py:226-233
    "reward-panel": ("free-claim", "safe-continue"),  # popup_handler.py:236-242
    "shop-daily-specials-and-hot-selling": ("close", "close"),  # popup_handler.py:254-262
    "returning-player-gift": ("free-claim", "dismiss"),  # popup_handler.py:263-268
    # 2026-09-16: the item dialogs surveyed this morning.  popup_handler reads
    # their registered recovery_action generically; this mirror names them so
    # the state machine answers the same way instead of stopping the round.
    "item-chain": ("close", "close"),
    "item-contents": ("close", "close"),
    "item-information": ("close", "close"),
    "producer-info": ("close", "close"),
    "player-profile": ("close", "close"),
    "name-edit": ("close", "close"),
    # The planner drives this panel's four taps; anything that only lands on it
    # closes it and leaves the chest for a later round.
    "order-piece-chest-choice": ("close", "close-only"),
    # 2026-09-19/20/21: three announcements and one dialog registered after the
    # fact, each because it had stopped a segment as not_on_board.  All four are
    # pure departures -- no price, no ad, no login, no external link -- so decide
    # answers them the same way popup_handler already reads their
    # recovery_action, instead of stopping the round on a surface the registry
    # knows how to leave.
    "coin-race-announcement": ("close", "close-only"),
    "coin-race-panel": ("close", "close-only"),
    "energy-boost-unlocked": ("close", "close-only"),
    # Its 确定 is the whole panel: the ad already failed and this only hands
    # over the compensation.  Tapping it is how the panel goes away.
    "ad-reward-compensation": ("close", "confirm"),
}

# Registered in navigation.v1.json but deliberately unwired: no
# action_for_screen entry and no verified tap path back to the board.  The
# energy-depleted guide's one verified element opens the energy-supply panel,
# a red-line surface, so nothing is wired on purpose.
REGISTERED_BUT_UNWIRED = frozenset({"energy-depleted-guide"})

SAVE_CHANNEL_ERROR_NAMES = frozenset({"SaveChannelError", "SaveReaderError"})

_STORY_FINAL_CONTINUE_ANCHOR = "story-dialogue/elements/final-continue-button"

# popup_handler.py:107-120: a registered transition permits waiting only.
_TRANSITION_WAIT_MS = 200
# popup_handler.py:121-135: a registered cutscene is observed first.
_CUTSCENE_OBSERVE_MS = 3000


def _popup(
    surface: str,
    popup_kind: str,
    element: str | None,
    *,
    wait_ms: int | None = None,
    reason: str,
) -> Act:
    if wait_ms is not None:
        intent = f"popup:wait:{surface}"
    else:
        # popup_handler.py:69: popup:{kind}:{screen}:{element}
        intent = f"popup:{popup_kind}:{surface}:{element}"
    return Act(
        BehaviorActionV1(
            kind="popup" if wait_ms is None else "wait",
            intent=intent,
            reason=reason,
            screen=surface,
            popup_kind=popup_kind,
            element=element,
            wait_ms=wait_ms,
        )
    )


def _guided_stall_reached(surface: str, counters: Any) -> bool:
    """Whether a followed control on this surface has spent its stall budget.

    game_loop.py:2658-2687: tutorial-follow and story-advance actions (and the
    handoff panels' own actions) join the byte-identical-frames stall test;
    ``guided_target_stall_budget`` gives the per-key budget (1 for the
    left-rail slot, 3 otherwise).
    """

    prefix = f"{surface}:"
    for key, count in counters.guided_stalls.items():
        if key.startswith(prefix) and count >= guided_target_stall_budget(key):
            return True
    return False


def _recent_forbidden_closes(times: list[float], now_monotonic: float | None) -> int:
    """Closes inside the recurrence window (game_loop.py:2011-2022).

    The stamps are monotonic; without a monotonic now every stamp counts, the
    conservative reading.
    """

    if now_monotonic is None:
        return len(times)
    return sum(
        1 for stamp in times if now_monotonic - stamp <= FORBIDDEN_RECURRENCE_WINDOW_S
    )


def _board_cells(save: Any) -> list[Any]:
    return [cell for row in save.board.cells for cell in row]


def _deliver_action(order: Any, *, under_guide: bool) -> Act:
    # planner.py:931: deliver:slot:{slot}:order:{order_id}
    return Act(
        BehaviorActionV1(
            kind="deliver",
            intent=f"deliver:slot:{order.slot}:order:{order.order_id}",
            reason=(
                "fulfillable order shown under a tutorial overlay goes first "
                "(deliver-under-guide ruling)"
                if under_guide
                else "fulfillable order has highest priority"
            ),
            params={"slot": order.slot, "order_id": order.order_id},
        )
    )


def _first_actionable_chest(save: Any, policy: Any) -> tuple[Any, bool] | None:
    """The first automated chest worth a tap, and whether it emits now.

    planner.py:1022-1114: skip a chest with no stock left or still opening;
    an opened chest (spread_state 3) emits only into an empty cell -- a full
    board skips it here and the sell branch below frees the cell instead.
    Runtime spread columns arrive through ``policy.chest_states``.  A board
    item without those columns is observed but never guessed to be clickable.
    """

    empty = bool(save.board.empty_cells)
    config_dir = getattr(policy, "config_dir", DEFAULT_CONFIG_DIR)
    suppressed = set(getattr(policy, "suppressed_chest_cells", frozenset()))
    runtime = [
        chest
        for chest in policy.chest_states
        if is_free_chest_type(chest.item_type, config_dir)
        and (chest.row, chest.col) not in suppressed
    ]
    candidates = list(runtime)
    configured_outputs = _configured_chest_outputs(config_dir)
    timer_types = configured_timer_chest_types(config_dir)

    def sort_key(chest: Any) -> tuple[Any, ...]:
        return (not str(chest.item_type).startswith("main_g_"), chest.row, chest.col)

    for chest in sorted(candidates, key=sort_key):
        actionable, emits_now, _outputs = _chest_actionability(
            chest,
            has_empty_cell=empty,
            configured_outputs=configured_outputs,
            can_start_timer=chest.item_type in timer_types,
        )
        if not actionable:
            continue
        return chest, emits_now
    return None


def _chest_action(
    chest: Any,
    emits_now: bool,
    save: Any,
    *,
    expected_outputs: list[str] | None = None,
) -> Act:
    intent = f"open-chest:{chest.item_type}"  # planner.py:1035
    expected_outputs = list(expected_outputs or chest.possible_types)
    if emits_now:
        target = min((cell.row, cell.col) for cell in save.board.empty_cells)
        return Act(
            BehaviorActionV1(
                kind="open_chest",
                intent=intent,
                reason=(
                    "free ready chest has priority and emits into an "
                    "authoritative empty cell without energy cost"
                ),  # planner.py:1074-1077
                position=(chest.row, chest.col),
                target=target,
                params={
                    "spread_state_before": chest.spread_state,
                    "item_rest_before": chest.item_rest,
                    "storage_rest_before": chest.storage_rest,
                    "expected_output_types": expected_outputs,
                    "requires_state_change": True,
                    "requires_item_decrement": True,
                },
            )
        )
    return Act(
        BehaviorActionV1(
            kind="open_chest",
            intent=f"{intent}:start-free-timer",  # planner.py:1097
            reason=(
                "start or advance the free chest timer; gem acceleration "
                "is outside the action space"
            ),  # planner.py:1099-1102
            position=(chest.row, chest.col),
            params={
                "spread_state_before": chest.spread_state,
                "item_rest_before": chest.item_rest,
                "storage_rest_before": chest.storage_rest,
                "requires_state_change": True,
            },
        )
    )


def _pair_key(first: tuple[int, int], second: tuple[int, int]) -> tuple[Any, ...]:
    return tuple(sorted((first, second)))


def _first_usable_merge(save: Any, counters: Any, policy: Any) -> Act | None:
    """The first pair the planner would actually merge, or None.

    planner.py:1116-1299: held-out pairs (a recent refutation, or a piece that
    has only just landed) are skipped (planner.py:1144-1152), and a pair whose
    merge would spend an item an open order reserves is declined
    (planner.py:1155-1189 ``_can_spend_pair`` / ``_can_spend_cobweb_source``).
    Cobweb-target merges are v2-only (planner.py:1142-1143).

    Known ordering approximation: planner._merge_candidates ranks candidates
    by chain distance to an outstanding order requirement, which needs the
    ItemModelConfig merge chains (planner.py:393-408, 420-481).  decide() has
    no config access, so plain pairs are tried in the board's own
    mergeable_pairs order (semantics.py:779-789) and cobweb pairs after them;
    branch selection ("a usable merge exists") is unaffected.
    """

    counts = Counter(
        cell.item_type
        for cell in _board_cells(save)
        if cell.kind == "item" and cell.item_type is not None
    )
    reserves = _required_reserves(save)  # planner.py:335-346
    held = {tuple(position) for position in counters.held_positions}
    suppressed = set(counters.suppressed_pairs)

    cells = {
        (cell.row, cell.col): cell
        for cell in _board_cells(save)
        if cell.kind == "item" and cell.item_type is not None
    }
    # Supported resource pairs have their own first pass. This keeps a full
    # action budget from spending the round on an ordinary order-chain merge.
    for resource_only in (True, False):
        for pair in save.board.mergeable_pairs:
            first = (pair.first.row, pair.first.col)
            second = (pair.second.row, pair.second.col)
            if _pair_key(first, second) in suppressed:
                continue
            if first in held or second in held:
                continue
            if pair.item_type is None or pair.merge_target is None:
                continue
            first_cell = cells.get(first)
            second_cell = cells.get(second)
            rewards = (
                tuple(() if first_cell is None else first_cell.collect_rewards)
                + tuple(() if second_cell is None else second_cell.collect_rewards)
            )
            resource_pair = bool(rewards) and all(
                reward.type in SUPPORTED_FREE_COLLECT_REWARD_TYPES
                and reward.amount > 0
                for reward in rewards
            )
            if resource_pair != resource_only:
                continue
            if not _can_spend_pair(pair.item_type, pair.merge_target, counts, reserves):
                continue  # blocked_order_reserve, planner.py:1272-1299
            return Act(
                BehaviorActionV1(
                    kind="merge",
                    intent=f"merge:{pair.item_type}->{pair.merge_target}",  # planner.py:1201
                    reason=(
                        "merge a supported resource pair before ordinary board work"
                        if resource_pair
                        else "order-safe merge that preserves required order items"
                    ),
                    position=first,
                    target=second,
                )
            )

    if policy.version != "v2":
        return None
    cobwebbed: dict[str, list[tuple[int, int]]] = {}
    for cell in _board_cells(save):
        if cell.kind == "item_cobwebbed" and cell.item_type is not None:
            cobwebbed.setdefault(cell.item_type, []).append((cell.row, cell.col))
    for cell in _board_cells(save):
        if cell.kind != "item" or cell.item_type is None or cell.merge_target is None:
            continue
        source = (cell.row, cell.col)
        for target in sorted(cobwebbed.get(cell.item_type, [])):
            if _pair_key(source, target) in suppressed:
                continue
            if source in held or target in held:
                continue
            if not _can_spend_cobweb_source(
                cell.item_type, cell.merge_target, counts, reserves
            ):
                continue
            return Act(
                BehaviorActionV1(
                    kind="merge",
                    intent=(
                        f"clear-cobweb:merge:{cell.item_type}->{cell.merge_target}"
                    ),  # planner.py:1199
                    reason=(
                        "merge a normal same-Type item onto its cobwebbed target "
                        "to clear the web and recover space"
                    ),  # planner.py:1236-1238
                    position=source,
                    target=target,
                )
            )
    return None


def _collect_reward_action(save: Any, policy: Any) -> Act | None:
    """Collect the best supported free reward after merge/chest work."""

    if policy.version != "v2" or getattr(policy, "max_collect_rewards_per_round", 1) < 1:
        return None
    if policy.max_actions_per_round < 2:
        return None  # select-then-collect needs two slots
    partner_counts = Counter(
        cell.item_type
        for cell in _board_cells(save)
        if cell.kind == "item" and cell.item_type is not None
    )
    candidates = []
    for cell in _board_cells(save):
        if cell.kind != "item" or cell.item_type is None:
            continue
        rewards = tuple((reward.type, reward.amount) for reward in cell.collect_rewards)
        if not rewards or any(
            reward_type not in SUPPORTED_FREE_COLLECT_REWARD_TYPES or amount <= 0
            for reward_type, amount in rewards
        ):
            continue
        if {reward_type for reward_type, _amount in rewards} == {"energy"}:
            if policy.max_energy_collects_per_round < 1:
                continue
        if partner_counts[cell.item_type] >= 2 and cell.merge_target is not None:
            continue
        candidates.append((-(sum(amount for _kind, amount in rewards)), cell.row, cell.col, cell, rewards))
    candidates.sort(key=lambda item: item[:3])
    if not candidates:
        return None
    _score, row, col, cell, rewards = candidates[0]
    reward_types = {reward_type for reward_type, _amount in rewards}
    legacy_energy = reward_types == {"energy"}
    energy_gain = sum(amount for reward_type, amount in rewards if reward_type == "energy")
    select_intent = (
        f"select-energy-item:{cell.item_type}"
        if legacy_energy
        else f"select-reward-item:{cell.item_type}"
    )
    collect_intent = (
        f"collect-energy-item:{cell.item_type}:energy:{energy_gain}"
        if legacy_energy
        else "collect-reward-item:"
        + f"{cell.item_type}:"
        + ",".join(f"{reward_type}:{amount}" for reward_type, amount in rewards)
    )
    return Act(
        BehaviorActionV1(
            kind="collect_energy" if legacy_energy else "collect_reward",
            intent=collect_intent,
            reason=(
                "this free energy item has no same-type partner left to merge with"
                if legacy_energy
                else "collect the highest-value supported free reward after preserving merges"
            ),  # planner.py:803-807
            position=(row, col),
            params={
                "energy_gain": energy_gain,
                "collect_rewards": [
                    {"type": reward_type, "amount": amount}
                    for reward_type, amount in rewards
                ],
            },
        )
    )


def _energy_collect_action(save: Any, policy: Any) -> Act | None:
    """Backward-compatible name for the generic collectible decision."""

    return _collect_reward_action(save, policy)


def _resource_cleanup_pending(save: Any, policy: Any) -> bool:
    """Whether a supported resource merge or collection must precede chests."""

    cells = {
        (cell.row, cell.col): cell
        for cell in _board_cells(save)
        if cell.kind == "item" and cell.item_type is not None
    }
    for pair in getattr(save.board, "mergeable_pairs", ()):
        first = cells.get((pair.first.row, pair.first.col))
        second = cells.get((pair.second.row, pair.second.col))
        if first is None or second is None:
            continue
        rewards = tuple(first.collect_rewards) + tuple(second.collect_rewards)
        if rewards and all(
            reward.type in SUPPORTED_FREE_COLLECT_REWARD_TYPES and reward.amount > 0
            for reward in rewards
        ):
            return True
    return _collect_reward_action(save, policy) is not None


def _chest_merge_pending(save: Any, policy: Any) -> bool:
    """Whether a same-type free chest pair must be merged before opening."""

    cells = {
        (cell.row, cell.col): cell
        for cell in _board_cells(save)
        if cell.kind == "item" and cell.item_type is not None
    }
    config_dir = getattr(policy, "config_dir", DEFAULT_CONFIG_DIR)
    for pair in getattr(save.board, "mergeable_pairs", ()):
        first = cells.get((pair.first.row, pair.first.col))
        second = cells.get((pair.second.row, pair.second.col))
        if (
            first is not None
            and second is not None
            and first.item_type == second.item_type
            and is_free_chest_type(first.item_type, config_dir)
        ):
            return True
    return False


def _claim_reward_queue_action(save: Any, policy: Any) -> Act | None:
    """Claim one decoded CacheItem only after the board has room to receive it."""

    rows = getattr(save, "cache_items", None)
    if (
        policy.version != "v2"
        or not rows
        or getattr(save, "cache_item_free_claim_supported", None) is not True
        or len(save.board.empty_cells) < 3
    ):
        return None
    first = _first_cache_item(rows)
    if first is None:
        return None
    return Act(
        BehaviorActionV1(
            kind="claim_reward_queue",
            intent="claim-reward-queue",
            reason=(
                "claim one authoritative free queued reward after resource cleanup "
                "with at least three empty board cells"
            ),
            params={"x": 100, "y": 390},
        )
    )


def _generate_action(save: Any, counters: Any, policy: Any) -> Act | None:
    """Generate from the best stocked generator into the first empty cell.

    planner.py:1335-1469: a generator must be interactable (the semantic read
    already requires stock and affordable energy, semantics.py:682-695), not
    inventory-exhausted and not held out after a refuted production
    (planner.py:1372-1385); the tap is skipped when energy no longer covers
    the price (planner.py:1412-1418).

    Known ordering approximation: planner.generator_priority ranks generators
    by chain distance from their spread weights to outstanding order
    requirements, which needs the ItemModelConfig merge chains
    (planner.py:1335-1361).  decide() has no config access, so generators are
    tried in board order; branch selection ("a generate exists") is
    unaffected.
    """

    if not save.board.empty_cells:
        return None
    suppressed = set(counters.suppressed_cells)
    energy = save.resources.energy or 0
    for cell in _board_cells(save):
        if cell.kind != "generator" or cell.generator is None:
            continue
        if not cell.interactable or cell.generator.inventory_exhausted is True:
            continue
        if (cell.row, cell.col) in suppressed:
            continue
        cost = max(cell.generator.cost_energy or 1, 1)
        if energy < cost:
            continue
        target = min((c.row, c.col) for c in save.board.empty_cells)
        return Act(
            BehaviorActionV1(
                kind="generate",
                intent=(
                    f"generate:{cell.item_type or cell.raw_code or 'unknown'}"
                ),  # planner.py:1428
                reason="energy and board space permit a generator action",
                position=(cell.row, cell.col),
                target=target,
                params={"energy_cost": cost},
            )
    )
    return None


def _sell_action(save: Any, policy: Any, protected: frozenset[str] | None) -> Act | None:
    """Sell or coin-collect the lowest-value safe item on a full board.

    planner.py:1471-1560 and _sell_candidates (planner.py:814-855): only when
    nothing else planned and no cell is empty; the order chains are protected,
    and the lowest value goes first.

    ``protected`` is the frame's ``sell_protected_chains``: the real
    ItemModelConfig filter table the assembly injected (F4-12 appendix A
    ruling 1).  When it is None the Phase 1 approximation runs instead --
    unit tests only, never the wired loop: "special" is approximated as
    "carries CollectRewards" and required chain ids resolve from the cells on
    the board only.
    """

    if policy.max_sells_per_round < 1 or save.board.empty_cells:
        return None
    if protected is None:
        chain_by_type = {
            cell.item_type: cell.chain_id
            for cell in _board_cells(save)
            if cell.item_type is not None and cell.chain_id is not None
        }
        required_chain_ids = {
            chain_by_type[required.type]
            for order in save.orders
            for required in order.required_items
            if required.type in chain_by_type
        }

        def excluded(cell: Any) -> bool:
            return cell.chain_id in required_chain_ids

        def direct_sale(cell: Any) -> bool:
            return (
                cell.sell_price is not None
                and cell.sell_price > 0
                and not cell.collect_rewards  # the "special" approximation above
            )

    else:

        def excluded(cell: Any) -> bool:
            # planner.py:831-833: Rare, ProtectLevel and the special kinds are
            # per-type exclusions; the order chains are per-chain ones.
            return f"type:{cell.item_type}" in protected or (
                cell.chain_id is not None and f"chain:{cell.chain_id}" in protected
            )

        def direct_sale(cell: Any) -> bool:
            # Every special-without-coins type sits in the protected type
            # entries already, so a surviving cell is special only when it
            # collects coins -- the coin branch below claims those first.
            return cell.sell_price is not None and cell.sell_price > 0

    candidates = []
    for cell in _board_cells(save):
        if cell.kind != "item" or cell.item_type is None:
            continue
        if excluded(cell):
            continue
        collect_coins = sum(
            reward.amount for reward in cell.collect_rewards if reward.type == "gold"
        )
        is_coin_collectible = collect_coins > 0
        is_direct_sale = direct_sale(cell)
        if not is_coin_collectible and not is_direct_sale:
            continue
        value = collect_coins if is_coin_collectible else cell.sell_price
        candidates.append(
            (value, is_coin_collectible, cell.level or 0, cell.item_type, cell)
        )
    if not candidates:
        return None
    value, coin_collectible, _level, item_type, cell = min(
        candidates,
        key=lambda item: (item[0], item[1], item[2], item[3], item[4].row, item[4].col),
    )
    intent = (
        f"collect-coin-piece:{item_type}"
        if coin_collectible
        else f"sell-item:{item_type}:price:{value}"
    )  # planner.py:1491-1494
    return Act(
        BehaviorActionV1(
            kind="sell",
            intent=intent,
            reason=(
                "collect a verified coin piece to recover one full-board cell"
                if coin_collectible
                else "sell the lowest-price safe non-order-chain item to recover space"
            ),  # planner.py:1550-1554
            position=(cell.row, cell.col),
            params={"value": value},
        )
    )


def _starved_energy_cost(save: Any, counters: Any) -> int | None:
    """The energy price that would unblock play, mirroring _energy_starved.

    exhaustion.py:293-326: energy is the sole blocker when a stocked generator
    and an empty cell wait, no board energy item and no space-recovery action
    remains (both are action branches, so reaching this function means none
    fired), and the plan would be all wait -- which is exactly what "no action
    branch fired" means here.
    """

    if not save.board.empty_cells:
        return None
    cost = min(
        (
            max(cell.generator.cost_energy or 1, 1)
            for cell in _board_cells(save)
            if cell.kind == "generator"
            and cell.generator is not None
            and cell.generator.inventory_exhausted is not True
        ),
        default=None,
    )  # exhaustion.py:225-244
    if cost is None or (save.resources.energy or 0) >= cost:
        return None
    if save.board.energy_items:
        return None
    return cost


def _wait_reason(save: Any) -> str:
    """planner.py:717-727 ``_wait_reason``, verbatim vocabulary."""

    if (save.resources.energy or 0) < 1:
        return "energy depleted: waiting for recovery"
    if not save.board.empty_cells:
        return "board full: no mergeable pairs"
    if not save.board.generators:
        return "no mergeable pairs or interactable generators"
    return "no safe productive action is currently available"


def _wait_decision(frame: BehaviorFrame) -> BehaviorDecision:
    """The no-actionable-move ladder's tail: exhaustion verdicts and waits.

    The resource-wait stop that ranks above it in the loop
    (game_loop.py:3575-3590) is evaluated by the caller, which knows whether
    any action branch fired.
    """

    save = frame.save
    counters = frame.counters
    clocks = frame.clocks
    policy = frame.policy
    starved = _starved_energy_cost(save, counters)
    energy_at = (
        save.resources.energy_enough_at(starved)
        if starved is not None
        else None
    )  # semantics.py:250-258 via exhaustion.py:388-390
    if energy_at is None:
        energy_at = clocks.energy_enough_at

    # planner.py:1562-1599: stocked-out generators wait on their own timers;
    # the cooldown epoch is the F6-05 derived one (semantics.py:516).
    cooldown_at = min(
        (
            cell.generator.cooldown_ready_at_epoch
            for cell in _board_cells(save)
            if cell.kind == "generator"
            and cell.generator is not None
            and cell.generator.inventory_exhausted is True
            and cell.generator.cooldown_ready_at_epoch is not None
        ),
        default=None,
    )
    if clocks.cooldown_ready_at is not None:
        cooldown_at = (
            clocks.cooldown_ready_at
            if cooldown_at is None
            else min(cooldown_at, clocks.cooldown_ready_at)
        )

    # game_loop.py:3601-3604 with exhaustion.py:391-405: conditions a and b.
    if (
        policy.stop_on_exhaustion
        and counters.consecutive_wait_rounds >= 3
        and counters.energy_rechecked_without_action
    ):
        return Stop("exhausted_v0")
    # game_loop.py:3605-3607 with exhaustion.py:408-409: condition a plus a
    # named energy price is the legal energy wait, with a resume time.
    if (
        policy.stop_on_exhaustion
        and counters.consecutive_wait_rounds >= 3
        and starved is not None
    ):
        return Stop("waiting_for_energy")

    epochs = [epoch for epoch in (energy_at, cooldown_at) if epoch is not None]
    if epochs:
        return WaitUntil(min(epochs), reason=_wait_reason(save))
    if cooldown_at is None and any(
        cell.kind == "generator"
        and cell.generator is not None
        and cell.generator.inventory_exhausted is True
        for cell in _board_cells(save)
    ) and save.board.empty_cells:
        # planner.py:1562-1599: the inventory wait has no epoch of its own and
        # naps on the configured recovery duration instead.
        wait_s = min(policy.energy_recovery_seconds, 60)
        return WaitUntil(
            float(clocks.now) + wait_s,
            reason=(
                "generator inventory depleted: item_rest=0 and storage_rest=0; "
                f"wait {wait_s}s for configured recovery"
            ),
        )
    # planner.py:1601-1616: the generic fallback wait.
    wait_s = min(policy.energy_recovery_seconds, 60)
    return WaitUntil(float(clocks.now) + wait_s, reason=_wait_reason(save))


def _board_decision(frame: BehaviorFrame) -> BehaviorDecision:
    """The main-board branches in plan_batch's priority order.

    Stop-condition order mirrors the loop's round-end ladder
    (game_loop.py:3575-3607): resource_wait, then max_wait_s, then
    refuted_streak, then -- when no action branch fired -- the exhaustion
    verdicts and the legal waits.
    """

    save = frame.save
    counters = frame.counters
    clocks = frame.clocks
    policy = frame.policy
    restricted = frame.signals.board_full_under_guide
    # game_loop.py:199-207, 3074-3086: under an unfollowable guide with a full
    # board, merging and selling are the only business allowed.  Every branch
    # assigns instead of returning so the loop's round-end ladder
    # (game_loop.py:3575-3607: resource_wait > max_wait_s > refuted_streak)
    # still gates a found action.
    action: Act | None = None

    if restricted and len(save.board.empty_cells) >= BOARD_FULL_UNDER_GUIDE_DROP_CELLS:
        # game_loop.py:1930-1968: once five cells are free, tap the guide's
        # own reward-chest target once and measure it.
        action = Act(
            BehaviorActionV1(
                kind="popup",
                intent="popup:free-claim:board-full-under-guide:open-main-task-reward-chest",
                reason=(
                    "the board-full-under-guide state ends by tapping the "
                    "guide's reward chest once room exists"
                ),
                screen="board-full-under-guide",
                popup_kind="free-claim",
                element="open-main-task-reward-chest",
            )
        )

    if action is None and not restricted:
        # B1 deliver: planner.py:907-983; the run-level delivery cap is the
        # loop's (game_loop.py:3106-3110).  ``deliverable_slots`` transcribes
        # planner.py:932-955: a fulfillable order whose slot has no
        # resolvable tap point is blocked_missing_geometry and planning moves
        # on; None keeps the Phase 1 shape for unit tests.
        if counters.deliveries_executed < policy.max_deliveries_per_run:
            deliverable = frame.deliverable_slots
            fulfillable = [
                order
                for order in save.orders
                if order.fulfillable_now
                and (deliverable is None or order.slot in deliverable)
            ]
            if fulfillable:
                action = _deliver_action(
                    min(fulfillable, key=_delivery_priority),
                    under_guide=False,
                )
        # B2 main task: planner.py:985-1020.
        task = save.main_task
        if (
            action is None
            and policy.enable_main_task
            and policy.max_main_tasks_per_round == 1
            and task is not None
            and task.affordable is True
            and task.cost is not None
        ):
            action = Act(
                BehaviorActionV1(
                    kind="start_main_task",
                    intent=f"start-main-task:{task.task_id}",  # planner.py:1001
                    reason=(
                        "affordable main task on the recognized main board has "
                        "priority below delivery and above merge or generation"
                    ),
                    params={"task_id": task.task_id, "cost": task.cost},
                )
            )
        # B3 free chest: planner.py:1022-1114.
        if (
            action is None
            and policy.version == "v2"
            and not _resource_cleanup_pending(save, policy)
            and not _chest_merge_pending(save, policy)
        ):
            chest = _first_actionable_chest(save, policy)
            if chest is not None:
                runtime_chest = chest[0]
                expected_outputs = list(runtime_chest.possible_types)
                if not expected_outputs:
                    expected_outputs = list(
                        _configured_chest_outputs(
                            getattr(policy, "config_dir", DEFAULT_CONFIG_DIR)
                        ).get(runtime_chest.item_type, ())
                    )
                action = _chest_action(
                    runtime_chest,
                    chest[1],
                    save,
                    expected_outputs=expected_outputs,
                )
    if action is None:
        # B4 merge: planner.py:1116-1299.
        action = _first_usable_merge(save, counters, policy)
    if action is None and not restricted:
        # B5 collect energy: planner.py:1314-1333.
        action = _collect_reward_action(save, policy)
    if action is None and not restricted:
        # B5b authoritative CacheItem queue, after merge/collect cleanup.
        action = _claim_reward_queue_action(save, policy)
    if action is None and not restricted:
        # B6 generate: planner.py:1335-1469.
        action = _generate_action(save, counters, policy)
    if action is None:
        # B7 sell on a full board: planner.py:1471-1560.  The
        # full-board-under-guide allowance lists sell too
        # (game_loop.py:199-204), so this branch stays available restricted.
        # The frame's sell_protected_chains carries the real ItemModelConfig
        # filter table (F4-12 appendix A ruling 1); None keeps the Phase 1
        # approximation for unit tests.
        action = _sell_action(save, policy, frame.sell_protected_chains)

    energy = save.resources.energy or 0
    starved = _starved_energy_cost(save, counters)
    # game_loop.py:3336-3341, 3575-3590: stop_on_resource_wait turns the
    # planner's energy-depleted wait into a segment stop, attributed to
    # energy when the detector can name the price.  It only binds on an
    # all-wait plan, i.e. when no branch fired.
    if action is None and policy.stop_on_resource_wait and energy < 1:
        if policy.stop_on_exhaustion and starved is not None:
            return Stop("waiting_for_energy")
        return Stop("resource_wait")
    # game_loop.py:3342-3345, 3591-3594.
    if (
        clocks.wait_budget_remaining_s is not None
        and clocks.wait_budget_remaining_s <= 0
    ):
        return Stop("max_wait_s")
    # game_loop.py:3595-3600 with StopConditionsV1 (game_loop.py:255).
    if counters.refuted_streak >= policy.max_consecutive_refuted:
        return Escalate(
            "refuted_streak",
            frame.frame_ref,
            detail=(
                f"{counters.refuted_streak} consecutive refuted business "
                f"predictions (limit {policy.max_consecutive_refuted})"
            ),
        )
    if action is not None:
        return action
    return _wait_decision(frame)


def decide(frame: BehaviorFrame) -> BehaviorDecision:
    """The one pure judgment: frame in, decision out.  No IO, no sleep."""

    counters = frame.counters
    clocks = frame.clocks
    policy = frame.policy
    signals = frame.signals
    surface = frame.surface

    # -- device, channel and flow events (they arrive as signals) -----------

    # game_loop.py:2995-2998, 3232-3236, 3329-3331, 3372-3378.
    if signals.input_dead_unrecovered:
        return Stop("input_dead_unrecovered")

    # game_loop.py:2151-2192 (_handle_read_failure / _read_failure_stop).
    if signals.read_error is not None:
        if signals.read_error in SAVE_CHANNEL_ERROR_NAMES:
            if signals.app_is_gone:
                # game_loop.py:2111-2149: relaunch, unless the window is spent.
                recent = [
                    stamp
                    for stamp in counters.relaunch_times
                    if clocks.now - stamp < APP_RELAUNCH_WINDOW_S
                ]
                if len(recent) >= APP_RELAUNCH_LIMIT:
                    return Stop("app_crash_recurring")
                return Act(
                    BehaviorActionV1(
                        kind="relaunch",
                        intent="relaunch:app",
                        reason=(
                            "the game process is gone; relaunch it and void "
                            "the interrupted round"
                        ),
                    )
                )
            return Stop("save_channel_failed")
        return Stop("unknown_screen")

    # game_loop.py:2945-2948.
    if signals.frame_unavailable:
        return Stop("unknown_screen")

    # game_loop.py:3325-3332: the flow's own status maps 1:1 (its input-dead
    # case is covered by the signal rule above).  2026-09-11: ``unavailable``
    # -- the event HUD hid the task notebook -- maps to its own stop so the
    # driver can end the shift cleanly instead of counting barren segments.
    if signals.main_task_flow_status is not None:
        return Stop(
            "main_task_completed"
            if signals.main_task_flow_status == "completed"
            else (
                "main_task_unavailable"
                if signals.main_task_flow_status == "unavailable"
                else "main_task_stopped"
            )
        )

    # -- budgets that outrank any action -------------------------------------

    # game_loop.py:2901, 2915: the loop bound is the default stop.
    if clocks.rounds_remaining is not None and clocks.rounds_remaining <= 0:
        return Stop("max_rounds")

    # The round-end budget ladder (max_wait_s, refuted_streak) only binds on
    # board rounds: guard-handled popup rounds skip it via replan_after_guard
    # (game_loop.py:3567-3568), so it lives inside _board_decision, not here.

    # -- surface dispositions -------------------------------------------------

    if surface in FORBIDDEN_CLOSE_ELEMENTS:
        # game_loop.py:2488-2511: one registered close per guard pass; a
        # surface that survives it stops the run, and the third close inside
        # the recurrence window stops it as recurring instead
        # (game_loop.py:2011-2022, exhaustion.py:149-152).
        if counters.popup_attempts.get(surface, 0) >= 1:
            return Escalate(
                "forbidden_surface",
                frame.frame_ref,
                detail=f"red-line surface {surface!r} survived its one close",
            )
        recent = _recent_forbidden_closes(
            counters.forbidden_close_times.get(surface, []),
            clocks.now_monotonic,
        )
        if recent + 1 >= FORBIDDEN_RECURRENCE_LIMIT:
            return Escalate(
                "forbidden_surface_recurring",
                frame.frame_ref,
                detail=(
                    f"red-line surface {surface!r} closed {recent} times inside "
                    f"{int(FORBIDDEN_RECURRENCE_WINDOW_S)} s already"
                ),
            )
        return _popup(
            surface,
            "close",
            FORBIDDEN_CLOSE_ELEMENTS[surface],
            reason="registered red-line surface permits its one close only",
        )

    # popup_handler.py:107-120 with navigation.py:522-528.
    if surface in TRANSITION_SURFACES:
        return _popup(
            surface,
            "wait",
            None,
            wait_ms=_TRANSITION_WAIT_MS,
            reason="transition frames permit waiting but no coordinate action",
        )

    # popup_handler.py:121-148: the cutscene observe/skip ladder.
    if surface == "story-cutscene":
        attempt = counters.popup_attempts.get(surface, 0)
        if attempt == 0:
            return _popup(
                surface,
                "wait",
                None,
                wait_ms=_CUTSCENE_OBSERVE_MS,
                reason="registered cutscene is observed for three seconds first",
            )
        if attempt in {1, 2}:
            return _popup(
                surface,
                "tutorial-follow",
                "skip-once",
                reason="the two-stage skip control takes one tap per stage",
            )
        # The ladder is spent and no path back is registered: the guard's
        # fallthrough stops the run (game_loop.py:2611-2618, 3057-3060).
        return Stop("not_on_board")

    if surface == STORY_DIALOGUE_SURFACE:
        # game_loop.py:2649-2656 then 2957-2968: the advance budget spent on
        # one dialogue is a stall stop (it never enters board-full-under-
        # guide, game_loop.py:1859-1865).
        if counters.story_taps >= MAX_STORY_ADVANCE_TAPS:
            return Stop("tutorial_follow_stalled")
        if _guided_stall_reached(surface, counters):
            return Stop("tutorial_follow_stalled")
        # popup_handler.py:77-96, 160-167; the round-level handoff that keeps
        # the guard on a long dialogue is game_loop.py:3000-3024.
        element = (
            "final-continue-button"
            if _STORY_FINAL_CONTINUE_ANCHOR in frame.surface_anchors
            else "advance-open-area"
        )
        return _popup(
            surface,
            "story-advance",
            element,
            reason=(
                f"advance the dialogue; guard budget {counters.story_taps} of "
                f"{STORY_ADVANCE_GUARD_BUDGET} spent"
            ),
        )

    if surface in GUARD_HANDOFF_SURFACES:
        # game_loop.py:3025-3056, 3253-3277: a registered departure panel
        # hands the next round back to the guard, at most
        # MAX_GUARD_HANDOFF_ROUNDS rounds per continuous occupation.
        if counters.handoff_rounds.get(surface, 0) >= MAX_GUARD_HANDOFF_ROUNDS:
            return Stop("not_on_board")
        if _guided_stall_reached(surface, counters):
            return Stop("tutorial_follow_stalled")
        if surface == "main-task-panel":
            # popup_handler.py:272-294: guard-only departure; the S2 flow
            # keeps stopping on this surface for a ruling.
            return _popup(
                surface,
                "close",
                "close",
                reason="guard-only close of a task panel a dead session left",
            )
        if surface == "restaurant-scene":
            # game_loop.py:2711-2727 with navigation.v1.json's verified
            # restaurant-scene -> main-board tap: the scene's departure is the
            # navigation hop, not a popup ladder rung.
            return Act(
                BehaviorActionV1(
                    kind="navigation",
                    intent="navigation:return-to-board:restaurant-scene->main-board",
                    reason="verified tap path back to the board",
                    screen=surface,
                )
            )
        popup_kind, element = POPUP_DISPOSITIONS[surface]
        return _popup(
            surface,
            popup_kind,
            element,
            reason=f"registered departure action for {surface}",
        )

    if surface == "tutorial-overlay":
        # game_loop.py:2512-2538: an overlay frame showing a fulfillable
        # order's Complete button hands the round to the delivery, whatever
        # the registered guide target is.  F4-12 appendix A ruling 3: the
        # pixel truth (frame.delivery_control_visible, filled by
        # detect_delivery_control_centers) is authoritative; the save-side
        # fulfillable_now is supporting evidence only.  A None field is the
        # Phase 1 fallback and stays for unit tests.
        delivery_visible = frame.delivery_control_visible
        if delivery_visible is None:
            if frame.save is not None and any(
                order.fulfillable_now for order in frame.save.orders
            ):
                return _deliver_action(
                    min(
                        (order for order in frame.save.orders if order.fulfillable_now),
                        key=_delivery_priority,
                    ),
                    under_guide=True,
                )
        elif signals.board_full_under_guide:
            # The guard's deliver hand-off excludes the board-full-under-guide
            # state (game_loop.py:2512-2519); that state has its own branch
            # below, so the pixels do not apply here.
            pass
        elif delivery_visible and frame.save is not None:
            # The guard hands the round to the planner as "main-board"
            # (game_loop.py:2533-2538): deliver when an order is fulfillable
            # and its slot has a tap point (planner.py:932-955, the
            # blocked_missing_geometry skip), otherwise the ordinary board
            # branches run this round.
            deliverable = frame.deliverable_slots
            fulfillable = [
                order
                for order in frame.save.orders
                if order.fulfillable_now
                and (deliverable is None or order.slot in deliverable)
            ]
            if fulfillable:
                return _deliver_action(
                    min(fulfillable, key=_delivery_priority),
                    under_guide=True,
                )
            return _board_decision(frame)
        # delivery_visible is False: the pixels prove no Complete button, so
        # fulfillable_now alone cannot authorize a delivery; the guide rules.
        # game_loop.py:2540-2557: the guide is known not to answer and the
        # save says the board is full behind it; play the restricted board.
        if signals.board_full_under_guide:
            return _board_decision(frame)
        if _guided_stall_reached(surface, counters):
            return Stop("tutorial_follow_stalled")
        return _popup(
            surface,
            "tutorial-follow",
            "guided-target",
            reason="follow the registered guide target",
        )

    if surface == "main-board":
        return _board_decision(frame)

    # popup_handler.py:258-268: the progress modal's close/acknowledge ladder.
    if surface == "newbie-gift-progress-modal":
        attempt = counters.popup_attempts.get(surface, 0)
        if attempt == 0:
            return _popup(surface, "close", "close-only", reason="close the modal")
        if attempt == 1:
            return _popup(
                surface,
                "close",
                "acknowledge-fallback",
                reason="the modal survived its close; acknowledge once",
            )
        return Stop("not_on_board")

    # popup_handler.py:234-248: the daily check-in's claim/close ladder.  One
    # free-claim tap on the claimable day cell, then the X; a panel that
    # survives both stops the run like any spent ladder.
    if surface == "daily-rewards":
        attempt = counters.popup_attempts.get(surface, 0)
        if attempt == 0:
            return _popup(
                surface,
                "free-claim",
                "claim-daily-reward",
                reason="claim the free daily check-in grant once",
            )
        if attempt == 1:
            return _popup(
                surface,
                "close",
                "close-only",
                reason="the claimable cell moved on; close at the X",
            )
        return Stop("not_on_board")

    if surface in POPUP_DISPOSITIONS:
        popup_kind, element = POPUP_DISPOSITIONS[surface]
        if popup_kind in {"tutorial-follow", "story-advance"} and _guided_stall_reached(
            surface, counters
        ):
            return Stop("tutorial_follow_stalled")
        return _popup(
            surface,
            popup_kind,
            element,
            reason="registry whitelists this disposition",
        )

    # Registered but deliberately unwired (no action, no verified path): the
    # guard breaks and the run stops (game_loop.py:2571-2575, 2611-2618,
    # 3057-3060).
    if surface in REGISTERED_BUT_UNWIRED:
        return Stop("not_on_board")

    # Unregistered surfaces ("unknown", "overlay-unknown", anything new): the
    # run stops and wakes the agent, which owes a registration or a patch
    # (spec §1; current literal mapping game_loop.py:3057-3060).
    return Escalate(
        "not_on_board",
        frame.frame_ref,
        detail=f"unregistered surface {surface!r}",
    )


__all__ = [
    "Act",
    "BehaviorActionKind",
    "BehaviorActionV1",
    "BehaviorDecision",
    "Escalate",
    "FORBIDDEN_CLOSE_ELEMENTS",
    "POPUP_DISPOSITIONS",
    "REGISTERED_BUT_UNWIRED",
    "SAVE_CHANNEL_ERROR_NAMES",
    "Stop",
    "TRANSITION_SURFACES",
    "WaitUntil",
    "decide",
]
