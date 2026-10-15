# [OMNI] origin=codex created_by=codex intent="provide a deterministic Romantic Restaurant planner v1"
"""Deterministic, explainable planning for Romantic Restaurant save semantics."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from functools import cmp_to_key, lru_cache
from itertools import combinations
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .contracts import ActionRequestV1
from .semantics import (
    CellPositionV1,
    CellSemanticV1,
    CollectRewardV1,
    GameStateSemanticV1,
    SPREAD_STATE_CLOSED,
    SPREAD_STATE_OPENED,
    SPREAD_STATE_OPENING,
)


ACTION_SPACE = (
    "tap",
    "drag",
    "wait",
    "deliver",
    "start_main_task",
    "open_chest",
    "sell",
)
PlannerActionKind = Literal[
    "tap",
    "drag",
    "wait",
    "deliver",
    "start_main_task",
    "open_chest",
    "sell",
]
PredictionKind = Literal[
    "merge",
    "generate",
    "generator_inventory_wait",
    "wait",
    "deliver",
    "start_main_task",
    "open_chest",
    "sell",
    "collect_energy",
    "collect_reward",
    "claim_reward_queue",
    "pick_chest_choice",
    "stash_to_warehouse",
    "retrieve_from_warehouse",
]
ActionStatus = Literal[
    "planned",
    "blocked_unknown_ui",
    "blocked_missing_geometry",
    # F4-11, planner ruling of 2026-09-07.  Not a defect like the two above:
    # this pair could be merged and the planner declines because an open order
    # needs one of its items.  Recorded so a reader can tell "there is no pair"
    # from "the pair is reserved".
    "blocked_order_reserve",
]

DEFAULT_CONFIG_DIR = Path("data/domains/game_observatory/game_models/romantic-restaurant/config")
DEFAULT_NAVIGATION_CONFIG = Path(
    "config/game_observatory/romantic_restaurant/navigation.v1.json"
)
SURVEYED_CHEST_TYPES = frozenset({"ShopNewChest_1", "main_g_chest_new_1"})
AUTOMATED_CHEST_TYPES = frozenset({"ShopNewChest_1", "main_g_chest_new_1"})
# 2901 has no OpenDuration in the shipped row, but its live runtime is a
# non-auto, zero-cost, opened, finite stock box with a measured output table.
# It is therefore eligible for a ready emission; it is not eligible for a
# guessed closed-state timer start.
# main_e_chest_new2_1 is the same shape, found on the live board 2026-09-16:
# absent from ItemModelConfig, but the save's item row carries spreadState 3,
# spreadItemRestNumber 5 and spreadCodeWeightPairs "2601-2;2602-1;2603-2" --
# a finite, free box of energy pieces that the loop had no way to open while
# energy was the binding constraint on every segment.
SURVEYED_READY_ONLY_CHEST_TYPES = frozenset({"2901", "main_e_chest_new2_1"})
# main_g_chest_new_1 is absent from ItemModelConfig in this corpus, but its
# free, non-productive lifecycle and possible outputs are surveyed in
# navigation.v1.json.  Keep the hint local to the planner rather than making
# the unknown config row look authoritative.
SURVEYED_CHEST_OUTPUT_HINTS = {
    "main_g_chest_new_1": ("2401", "2402"),
}
SUPPORTED_FREE_COLLECT_REWARD_TYPES = frozenset({"gold", "gem", "energy"})


@lru_cache(maxsize=8)
def configured_free_chest_types(config_dir: Path) -> frozenset[str]:
    """Return free, manually opened content chests from ItemModelConfig.

    Generators also use ``Spread_Weight`` and zero energy cost, so requiring a
    real open duration keeps them on the generator path.  The surveyed
    ``main_g_chest_new_1`` exception has no current ItemModelConfig row and is
    included explicitly from its measured navigation evidence.
    """

    payload = json.loads((config_dir / "ItemModelConfig.json").read_text(encoding="utf-8"))
    rows = payload.get("rows", []) if isinstance(payload, dict) else []
    result = set(SURVEYED_CHEST_OUTPUT_HINTS)
    for row in rows:
        if not isinstance(row, dict):
            continue
        item_type = row.get("Type")
        if (
            item_type is not None
            and row.get("Spread_Auto") == 0
            and row.get("Spread_CostEnergy") == 0
            and row.get("Spread_OpenDuration") is not None
            and isinstance(row.get("Spread_Weight"), list)
            and bool(row["Spread_Weight"])
            and row.get("Choices") is None
        ):
            result.add(str(item_type))
    result.update(SURVEYED_READY_ONLY_CHEST_TYPES)
    return frozenset(result)


def is_free_chest_type(item_type: str | None, config_dir: Path) -> bool:
    return item_type is not None and item_type in configured_free_chest_types(config_dir)


@lru_cache(maxsize=8)
def configured_timer_chest_types(config_dir: Path) -> frozenset[str]:
    """Types with a configured open duration that can start a timer."""

    payload = json.loads((config_dir / "ItemModelConfig.json").read_text(encoding="utf-8"))
    rows = payload.get("rows", []) if isinstance(payload, dict) else []
    return frozenset(
        str(row.get("Type"))
        for row in rows
        if isinstance(row, dict)
        and is_free_chest_type(str(row.get("Type")), config_dir)
        and row.get("Spread_OpenDuration") is not None
    )


def _chest_actionability(
    chest: ChestRuntimeStateV1,
    *,
    has_empty_cell: bool,
    configured_outputs: dict[str, list[str]] | None = None,
    can_start_timer: bool = True,
) -> tuple[bool, bool, list[str]]:
    """Return ``(actionable, emits_now, expected_outputs)`` from runtime facts.

    ``spreadState`` is authoritative for the two legal manual operations:
    Closed (1) starts the free timer and Opened (3) emits an item.  Opening,
    initializing, absent, or any other state is deliberately left alone until
    the next semantic read.  A ready chest also needs a positive item stock
    and a known output type; this gives the loop enough before-state facts to
    reject a select-only or no-op tap.
    """

    if chest.spread_state == SPREAD_STATE_CLOSED:
        if not can_start_timer:
            return False, False, []
        if chest.item_rest is None and chest.storage_rest is None:
            return False, False, []
        if chest.item_rest == 0 and chest.storage_rest == 0:
            return False, False, []
        return True, False, []
    if chest.spread_state != SPREAD_STATE_OPENED:
        return False, False, []
    if not has_empty_cell or chest.item_rest is None or chest.item_rest <= 0:
        return False, False, []
    outputs = list(chest.possible_types)
    if not outputs and configured_outputs is not None:
        outputs = list(configured_outputs.get(chest.item_type, ()))
    if not outputs:
        return False, False, []
    return True, True, outputs


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)


class VisualCellObservationV1(_FrozenModel):
    row: int = Field(ge=1, le=9)
    col: int = Field(ge=1, le=7)
    background_fraction: float = Field(ge=0, le=1)
    visually_empty: bool


class VisualOccupancyCoverageV1(_FrozenModel):
    checked_saved_empty_cells: int = Field(ge=0)
    visually_empty_cells: int = Field(ge=0)
    visually_occupied_cells: list[CellPositionV1] = Field(default_factory=list)
    crop_size_px: int = Field(default=40, ge=1)
    reference_rgb: tuple[int, int, int] = (143, 184, 241)
    max_channel_delta: int = Field(default=16, ge=0)
    minimum_background_fraction: float = Field(default=0.95, ge=0, le=1)
    observations: list[VisualCellObservationV1] = Field(default_factory=list)


class ChestRuntimeStateV1(_FrozenModel):
    row: int = Field(ge=1, le=9)
    col: int = Field(ge=1, le=7)
    item_type: str
    spread_state: int | None = None
    item_rest: int | None = None
    storage_rest: int | None = None
    possible_types: list[str] = Field(default_factory=list)


class PolicyV1(_FrozenModel):
    version: Literal["v1", "v2"] = "v2"
    max_actions_per_round: int = Field(default=6, ge=1)
    max_merges_per_round: int = Field(default=5, ge=0, le=5)
    max_generates_per_round: int = Field(default=2, ge=0, le=2)
    max_deliveries_per_round: int = Field(default=1, ge=0, le=3)
    max_deliveries_per_run: int = Field(default=3, ge=0, le=3)
    max_sells_per_round: int = Field(default=2, ge=0, le=2)
    # One collect per round keeps the energy delta attributable to a single
    # tap: EnergyModel restores one point every 120 s, so a round that lands
    # more than one collection cannot separate the taps from a recovery tick.
    max_energy_collects_per_round: int = Field(default=1, ge=0, le=2)
    max_collect_rewards_per_round: int = Field(default=1, ge=0, le=1)
    allow_chained_merge: bool = False
    enable_main_task: bool = False
    current_screen: str = "unknown"
    max_main_tasks_per_round: int = Field(default=1, ge=0, le=1)
    energy_recovery_seconds: int = Field(default=60, ge=0)
    max_wait_s: int = Field(default=600, ge=0)
    config_dir: Path = DEFAULT_CONFIG_DIR
    navigation_config: Path = DEFAULT_NAVIGATION_CONFIG
    geometry: Any | None = Field(default=None, exclude=True, repr=False)
    order_geometry: Any | None = Field(default=None, exclude=True, repr=False)
    delivery_control_overrides: dict[int, tuple[int, int]] = Field(
        default_factory=dict,
        exclude=True,
        repr=False,
    )
    visual_nonempty_cells: frozenset[tuple[int, int]] = Field(
        default_factory=frozenset,
        exclude=True,
        repr=False,
    )
    # The queue chest is not always on screen, and it renders whatever item
    # is at the head of the queue, so only its count badge identifies it.  The
    # loop supplies the point it verified against the current frame, or None
    # when the badge is absent; a blind tap on the surveyed coordinate landed
    # on the board behind it and opened an item-chain modal instead.
    reward_queue_control: tuple[int, int] | None = Field(
        default=(100, 390),
        exclude=True,
        repr=False,
    )
    # What this run parked in the warehouse.  The save exposes no warehouse
    # inventory, so the loop keeps the ledger and hands it in here.
    warehouse_parked: tuple[str, ...] = Field(
        default=(),
        exclude=True,
        repr=False,
    )
    visual_occupancy: VisualOccupancyCoverageV1 | None = Field(
        default=None,
        exclude=True,
        repr=False,
    )
    chest_states: tuple[ChestRuntimeStateV1, ...] = Field(
        default_factory=tuple,
        exclude=True,
        repr=False,
    )
    # Positions whose last chest tap produced no authoritative state change.
    # The loop owns persistence and clears a position when its runtime
    # fingerprint changes; planning only applies the current suppression.
    suppressed_chest_cells: frozenset[tuple[int, int]] = Field(
        default_factory=frozenset,
        exclude=True,
        repr=False,
    )
    # F4-09 1.6.  Two runtime holds the loop sets, both about not repeating a
    # move the device has already declined.
    #
    # ``suppressed_merge_pairs`` are unordered cell pairs whose merge was
    # refuted recently.  The 2026-09-06 20:53Z segment predicted the same
    # (4,1)-(4,2) merge three rounds running, was refuted each time and burned
    # the whole segment on a refuted streak; the board never changed, so the
    # third attempt could not have gone differently from the first.
    #
    # ``suppressed_generator_cells`` are generators whose production was
    # refuted recently, held for the same reason.
    #
    # ``held_positions`` are pieces the current read says were created since
    # the previous read.  A piece that has just landed is still animating, and
    # the same segment's refuted merges all involved one: it is cheaper to
    # merge it next round than to spend a round finding out.
    suppressed_merge_pairs: frozenset[tuple[tuple[int, int], tuple[int, int]]] = Field(
        default_factory=frozenset,
        exclude=True,
        repr=False,
    )
    suppressed_generator_cells: frozenset[tuple[int, int]] = Field(
        default_factory=frozenset,
        exclude=True,
        repr=False,
    )
    # ``warehouse_stash_held`` is set while a refused parking is on hold.  The
    # warehouse is the last resort before waiting, and a shelf that just refused
    # a piece is refusing the shelf, not that piece -- holding the destination
    # rather than the cell keeps the run from walking the board one refuted
    # parking at a time, which is the streak the merge hold above exists to
    # prevent.
    warehouse_stash_held: bool = Field(default=False, exclude=True, repr=False)
    held_positions: frozenset[tuple[int, int]] = Field(
        default_factory=frozenset,
        exclude=True,
        repr=False,
    )
    # 2026-09-21.  ``Spread_CostEnergy`` is a static table value, and the level
    # 15 "能量助力" boost doubles what a pull actually charges without
    # touching it.  The 08:32Z segment's energy walked 32-30-28-26 on one
    # generate per round, and at energy 1 the planner kept planning a pull it
    # modelled at 1: every tap was refused, the game answered with its energy
    # purchase panel, and the guard closing that red-line panel twice stopped
    # the run for a person.  The loop measures the real spend and hands it in
    # here; 1 keeps the table's own number.
    generate_energy_multiplier: int = Field(default=1, exclude=True, repr=False)


class PredictionV1(_FrozenModel):
    prediction_id: str
    kind: PredictionKind
    energy_delta: int = 0
    source_cell: CellPositionV1 | None = None
    target_cell: CellPositionV1 | None = None
    source_before: str | None = None
    source_after: str | None = None
    target_before: str | None = None
    target_after: str | None = None
    possible_target_types: list[str] = Field(default_factory=list)
    coins_delta: int = 0
    order_slot: int | None = None
    order_before: str | None = None
    required_cells: list[CellPositionV1] = Field(default_factory=list)
    required_item_types: list[str] = Field(default_factory=list)
    main_task_before: str | None = None
    main_task_cost: int | None = None
    generator_inventory_available: bool | None = None
    collect_rewards: list[CollectRewardV1] = Field(default_factory=list)
    cache_items_before: int | None = None
    cache_item_code_before: str | None = None
    cache_queue_head_id_before: str | None = None
    # Chest adjudication contract.  A timer tap must change the chest state;
    # a ready output must consume one item and add one of the expected types.
    chest_spread_state_before: int | None = None
    chest_item_rest_before: int | None = None
    chest_storage_rest_before: int | None = None
    chest_expected_output_types: list[str] = Field(default_factory=list)
    chest_requires_state_change: bool = False
    chest_requires_item_decrement: bool = False


class PlannedActionV1(_FrozenModel):
    kind: PlannerActionKind
    intent: str
    prediction: PredictionV1
    reason: str
    request: ActionRequestV1 | None
    status: ActionStatus = "planned"
    depends_on: str | None = None


class PlanV1(_FrozenModel):
    actions: list[PlannedActionV1]
    predictions: list[PredictionV1]
    blocked_actions: list[PlannedActionV1] = Field(default_factory=list)
    action_space: tuple[PlannerActionKind, ...] = ACTION_SPACE
    visual_occupancy: VisualOccupancyCoverageV1 | None = None


@dataclass(frozen=True)
class _SimCell:
    item_type: str | None
    kind: str
    merge_target: str | None
    sell_price: int | None
    level: int | None
    chain_id: str | None = None
    rare: bool = False
    protect_level: int | None = None
    special: bool = False
    collect_coins: int | None = None
    collect_energy: int | None = None
    collect_rewards: tuple[tuple[str, int], ...] = ()


def _cache_item_sort_key(row: dict[str, Any]) -> tuple[int, int, int | str]:
    """Mirror ItemCacheModel:_SortItemIdList for the first queued item."""

    cache_type = _optional_int(row.get("type")) or 1
    raw_id = row.get("id")
    try:
        item_id: int | str = int(raw_id)
        id_kind = 0
    except (TypeError, ValueError):
        item_id = "" if raw_id is None else str(raw_id)
        id_kind = 1
    # Python's sorted call below uses the explicit type/id directions.
    return cache_type, id_kind, item_id


def _first_cache_item(rows: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    if not rows:
        return None

    def compare(left: dict[str, Any], right: dict[str, Any]) -> int:
        left_type = _optional_int(left.get("type")) or 1
        right_type = _optional_int(right.get("type")) or 1
        if left_type != right_type:
            return -1 if left_type > right_type else 1
        left_id = _cache_item_sort_key(left)[2]
        right_id = _cache_item_sort_key(right)[2]
        try:
            id_order = (int(left_id) > int(right_id)) - (int(left_id) < int(right_id))
        except (TypeError, ValueError):
            id_order = (str(left_id) > str(right_id)) - (str(left_id) < str(right_id))
        # Lua sorts Type1 ids ascending and every other type descending.
        return id_order if left_type == 1 else -id_order

    return sorted(rows, key=cmp_to_key(compare))[0]


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=8)
def _configured_item_metadata(config_dir: Path) -> dict[str, _SimCell]:
    payload = json.loads((config_dir / "ItemModelConfig.json").read_text(encoding="utf-8"))
    rows = payload.get("rows", []) if isinstance(payload, dict) else []
    by_type = {
        str(row["Type"]): row
        for row in rows
        if isinstance(row, dict) and row.get("Type") is not None
    }
    predecessors: dict[str, list[str]] = {}
    for type_id, row in by_type.items():
        merged = row.get("MergedType")
        if merged not in {None, ""}:
            predecessors.setdefault(str(merged), []).append(type_id)

    def chain_id(type_id: str) -> str | None:
        current = type_id
        seen = {current}
        while len(predecessors.get(current, [])) == 1:
            current = predecessors[current][0]
            if current in seen:
                return None
            seen.add(current)
        try:
            return str(int(current) // 100)
        except ValueError:
            return f"non-numeric:{current.split('_', maxsplit=1)[0]}"

    def collected_property(row: dict[str, Any], property_type: str) -> int | None:
        rewards = row.get("CollectRewards")
        if not isinstance(rewards, list):
            return None
        total = 0
        for reward in rewards:
            if not isinstance(reward, str):
                continue
            kind, separator, raw_amount = reward.rpartition("-")
            if separator and kind == property_type:
                total += _optional_int(raw_amount) or 0
        return total or None

    def collected_coins(row: dict[str, Any]) -> int | None:
        return collected_property(row, "gold")

    def collected_energy(row: dict[str, Any]) -> int | None:
        return collected_property(row, "energy")

    def collected_rewards(row: dict[str, Any]) -> tuple[tuple[str, int], ...]:
        rewards = row.get("CollectRewards")
        if not isinstance(rewards, list):
            return ()
        result: list[tuple[str, int]] = []
        for reward in rewards:
            if not isinstance(reward, str):
                continue
            property_type, separator, raw_amount = reward.rpartition("-")
            amount = _optional_int(raw_amount) if separator else None
            if separator and property_type and amount is not None and amount > 0:
                result.append((property_type, amount))
        return tuple(result)

    return {
        str(row["Type"]): _SimCell(
            item_type=str(row["Type"]),
            kind=(
                "chest"
                if row.get("Spread_OpenDuration") is not None or row.get("Choices") is not None
                else "generator"
                if any(str(key).startswith("Spread_") for key in row)
                else "item"
            ),
            merge_target=(None if row.get("MergedType") in {None, ""} else str(row["MergedType"])),
            sell_price=_optional_int(row.get("SellingPrice")),
            level=None,
            chain_id=chain_id(str(row["Type"])),
            rare=bool(_optional_int(row.get("Rare")) or 0),
            protect_level=_optional_int(row.get("ProtectLevel")),
            special=(
                any(str(key).startswith("Spread_") for key in row)
                or row.get("Choices") is not None
                or row.get("CollectRewards") is not None
            ),
            collect_coins=collected_coins(row),
            collect_energy=collected_energy(row),
            collect_rewards=collected_rewards(row),
        )
        for row in rows
        if isinstance(row, dict) and row.get("Type") is not None
    }


def _position(row: int, col: int) -> CellPositionV1:
    return CellPositionV1(row=row, col=col)


def _position_key(position: CellPositionV1) -> tuple[int, int]:
    return (position.row, position.col)


def _cell_center(geometry: Any | None, position: CellPositionV1) -> tuple[int, int] | None:
    if geometry is None:
        return None
    center = geometry.cell_center(position.row, position.col)
    if not isinstance(center, (tuple, list)) or len(center) != 2:
        raise ValueError("geometry.cell_center must return an (x, y) pair")
    return (int(center[0]), int(center[1]))


def _request_status(request: ActionRequestV1 | None) -> ActionStatus:
    return "planned" if request is not None else "blocked_missing_geometry"


def _required_reserves(
    state: GameStateSemanticV1,
    *,
    excluded_order_ids: set[str] | None = None,
) -> Counter[str]:
    reserves: Counter[str] = Counter()
    for order in state.orders:
        if excluded_order_ids and order.order_id in excluded_order_ids:
            continue
        for required in order.required_items:
            reserves[required.type] = max(reserves[required.type], required.count)
    return reserves


def _can_spend_pair(
    item_type: str,
    merge_target: str,
    counts: Counter[str],
    reserves: Counter[str],
) -> bool:
    remaining_source = counts[item_type] - 2
    if remaining_source >= reserves[item_type]:
        return True
    return counts[merge_target] < reserves[merge_target]


def _can_spend_cobweb_source(
    item_type: str,
    merge_target: str,
    counts: Counter[str],
    reserves: Counter[str],
) -> bool:
    remaining_source = counts[item_type] - 1
    return remaining_source >= reserves[item_type] or counts[merge_target] < reserves[merge_target]


def _outstanding_required_types(
    state: GameStateSemanticV1,
    counts: Counter[str],
) -> set[str]:
    return set(_outstanding_required_counts(state, counts))


def _outstanding_required_counts(
    state: GameStateSemanticV1,
    counts: Counter[str],
) -> dict[str, int]:
    required_totals: Counter[str] = Counter()
    for order in state.orders:
        for required in order.required_items:
            required_totals[required.type] += required.count
    return {
        item_type: required_count - counts[item_type]
        for item_type, required_count in required_totals.items()
        if counts[item_type] < required_count
    }


def _chain_distance(
    source_after: str | None,
    required_types: set[str],
    metadata: dict[str, _SimCell],
) -> int | None:
    current = source_after
    distance = 0
    seen: set[str] = set()
    while current is not None and current not in seen:
        if current in required_types:
            return distance
        seen.add(current)
        descriptor = metadata.get(current)
        current = None if descriptor is None else descriptor.merge_target
        distance += 1
    return None


@dataclass(frozen=True)
class _MergeCandidate:
    key: tuple[Any, ...]
    source: tuple[int, int]
    target: tuple[int, int]
    cell: _SimCell
    cobweb_target: bool = False


def _merge_candidates(
    cells: dict[tuple[int, int], _SimCell],
    touched_positions: set[tuple[int, int]],
    produced_positions: set[tuple[int, int]],
    required_types: set[str],
    metadata: dict[str, _SimCell],
) -> list[_MergeCandidate]:
    by_type: dict[str, list[tuple[int, int]]] = {}
    cobweb_by_type: dict[str, list[tuple[int, int]]] = {}
    for position, cell in cells.items():
        if (
            (position not in touched_positions or position in produced_positions)
            and cell.kind in {"item", "chest"}
            and cell.item_type is not None
            and cell.merge_target is not None
        ):
            by_type.setdefault(cell.item_type, []).append(position)
        elif (
            position not in touched_positions
            and cell.kind == "item_cobwebbed"
            and cell.item_type is not None
            and cell.merge_target is not None
        ):
            cobweb_by_type.setdefault(cell.item_type, []).append(position)

    candidates: list[_MergeCandidate] = []
    for item_type, positions in by_type.items():
        ordered = sorted(positions)
        cell = cells[ordered[0]]
        distance = _chain_distance(cell.merge_target, required_types, metadata)
        resource_merge = bool(cell.collect_rewards) and all(
            reward_type in SUPPORTED_FREE_COLLECT_REWARD_TYPES and amount > 0
            for reward_type, amount in cell.collect_rewards
        )
        for first, second in combinations(ordered, 2):
            key = (
                not resource_merge,
                distance is None,
                distance if distance is not None else 0,
                True,
                cell.sell_price is None,
                cell.sell_price if cell.sell_price is not None else 0,
                cell.level is None,
                cell.level if cell.level is not None else 0,
                item_type,
                first,
                second,
            )
            candidates.append(_MergeCandidate(key, first, second, cell))
        for source in ordered:
            for target in sorted(cobweb_by_type.get(item_type, [])):
                key = (
                    not resource_merge,
                    distance is None,
                    distance if distance is not None else 0,
                    False,
                    cell.sell_price is None,
                    cell.sell_price if cell.sell_price is not None else 0,
                    cell.level is None,
                    cell.level if cell.level is not None else 0,
                    item_type,
                    source,
                    target,
                )
                candidates.append(
                    _MergeCandidate(key, source, target, cell, cobweb_target=True)
                )
    candidates.sort(key=lambda item: item.key)
    return candidates


def _drag_request(
    geometry: Any | None,
    source: CellPositionV1,
    target: CellPositionV1,
    intent: str,
) -> ActionRequestV1 | None:
    source_center = _cell_center(geometry, source)
    target_center = _cell_center(geometry, target)
    if source_center is None or target_center is None:
        return None
    return ActionRequestV1(
        kind="drag",
        intent=intent,
        params={
            "x1": source_center[0],
            "y1": source_center[1],
            "x2": target_center[0],
            "y2": target_center[1],
            "duration_ms": 400,
        },
    )


def _tap_request(
    geometry: Any | None,
    generator: CellSemanticV1,
    intent: str,
) -> ActionRequestV1 | None:
    return _tap_position_request(
        geometry,
        _position(generator.row, generator.col),
        intent,
    )


def _tap_position_request(
    geometry: Any | None,
    position: CellPositionV1,
    intent: str,
) -> ActionRequestV1 | None:
    center = _cell_center(geometry, position)
    if center is None:
        return None
    return ActionRequestV1(
        kind="tap",
        intent=intent,
        params={"x": center[0], "y": center[1]},
    )


@lru_cache(maxsize=8)
def configured_choice_chest_types(config_dir: Path) -> dict[str, tuple[str, ...]]:
    """Chests that hand over one picked piece instead of spreading stock.

    ``ItemModelConfig`` gives these rows a ``Choices`` list and no ``MergedType``
    or ``Spread_*`` lifecycle, and the game labels them 订单棋子宝箱: three pieces
    useful for the current orders, pick one.  The picked piece replaces the chest
    in its own cell, so this is the one opener that still works on a full board.
    """

    payload = json.loads((config_dir / "ItemModelConfig.json").read_text(encoding="utf-8"))
    rows = payload.get("rows", []) if isinstance(payload, dict) else []
    result: dict[str, tuple[str, ...]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        choices = row.get("Choices")
        if not isinstance(choices, list) or not choices:
            continue
        if row.get("MergedType") not in {None, ""}:
            continue
        result[str(row["Type"])] = tuple(str(value) for value in choices)
    return result


@lru_cache(maxsize=8)
def _configured_chest_outputs(config_dir: Path) -> dict[str, list[str]]:
    payload = json.loads((config_dir / "ItemModelConfig.json").read_text(encoding="utf-8"))
    rows = payload.get("rows", []) if isinstance(payload, dict) else []
    result: dict[str, list[str]] = {}
    for row in rows:
        if not isinstance(row, dict) or not is_free_chest_type(
            str(row.get("Type")), config_dir
        ):
            continue
        result[str(row["Type"])] = [
            value.rpartition("-")[0]
            for value in row.get("Spread_Weight", [])
            if isinstance(value, str) and value.rpartition("-")[0]
        ]
    for item_type, outputs in SURVEYED_CHEST_OUTPUT_HINTS.items():
        result.setdefault(item_type, list(outputs))
    return result


def _chest_states(
    state: GameStateSemanticV1,
    policy: PolicyV1,
) -> list[ChestRuntimeStateV1]:
    suppressed = policy.suppressed_chest_cells
    runtime = [
        chest
        for chest in policy.chest_states
        if is_free_chest_type(chest.item_type, policy.config_dir)
        and (chest.row, chest.col) not in suppressed
    ]
    if runtime:
        return sorted(
            runtime,
            key=lambda item: (
                not item.item_type.startswith("main_g_"),
                item.row,
                item.col,
            ),
        )
    outputs = _configured_chest_outputs(policy.config_dir)
    # A surveyed exception without a config row (main_g_chest_new_1) is
    # actionable only when the runtime stream supplies its lifecycle state.
    # An absent runtime record is insufficient evidence to start a timer.
    configured_types = configured_free_chest_types(policy.config_dir) - set(
        SURVEYED_CHEST_OUTPUT_HINTS
    )
    inferred = [
        ChestRuntimeStateV1(
            row=cell.row,
            col=cell.col,
            item_type=str(cell.item_type),
            possible_types=outputs.get(str(cell.item_type), []),
        )
        for row in state.board.cells
        for cell in row
        if cell.item_type in configured_types
        and (cell.row, cell.col) not in suppressed
    ]
    return sorted(
        inferred,
        key=lambda item: (
            not item.item_type.startswith("main_g_"),
            item.row,
            item.col,
        ),
    )


# Enough room that the retrieved piece can be worked with, not just parked
# back.  Three cells is the same headroom the queue claim asks for.
WAREHOUSE_RETRIEVE_MIN_EMPTY_CELLS = 3
# Producers whose ItemModelConfig row says Spread_CostEnergy == 0 hand over
# coins, gems, energy or food for nothing.  They are the crowned boxes a player
# drains on sight, and charging them against the ordinary generation budget let
# a board sit on a dozen free pulls while every round spent its two slots on
# chain items instead.
FREE_PRODUCER_PULLS_PER_ROUND = 3


@lru_cache(maxsize=8)
def _warehouse_controls(path: Path) -> dict[str, tuple[int, int]]:
    """Warehouse entry, first shelf slot and close, once the survey verified them."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    warehouse = payload.get("board_actions", {}).get("warehouse", {})
    if warehouse.get("status") != "verified-stash-and-retrieve":
        return {}
    controls: dict[str, tuple[int, int]] = {}
    for name, key in (("entry", "entry"), ("close", "close")):
        value = warehouse.get(key)
        if isinstance(value, list) and len(value) == 2:
            controls[name] = (int(value[0]), int(value[1]))
    slots = warehouse.get("shelf_slot_centers")
    if isinstance(slots, list) and slots and isinstance(slots[0], list) and len(slots[0]) == 2:
        controls["slot-1"] = (int(slots[0][0]), int(slots[0][1]))
    return controls


@lru_cache(maxsize=8)
def _warehouse_entry(path: Path) -> tuple[int, int] | None:
    """Warehouse entry point, verified 2026-09-16 to accept a dragged item."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    warehouse = payload.get("board_actions", {}).get("warehouse", {})
    entry = warehouse.get("entry")
    if not isinstance(entry, list) or len(entry) != 2:
        return None
    if warehouse.get("status") != "verified-stash-and-retrieve":
        return None
    return (int(entry[0]), int(entry[1]))


@lru_cache(maxsize=8)
def _choice_panel_controls(path: Path) -> dict[str, tuple[int, int]]:
    """Registered controls of the 开启挑选箱 panel, surveyed 2026-09-16."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    elements = (
        payload.get("surfaces", {})
        .get("order-piece-chest-choice", {})
        .get("elements", {})
    )
    controls: dict[str, tuple[int, int]] = {}
    for name in ("choice-1", "confirm", "close-only"):
        center = elements.get(name, {}).get("center")
        if isinstance(center, list) and len(center) == 2:
            controls[name] = (int(center[0]), int(center[1]))
    return controls


@lru_cache(maxsize=8)
def _configured_sell_control(path: Path) -> tuple[int, int] | None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw = payload.get("board_actions", {}).get("sell", {}).get("selected_item_control")
    if not isinstance(raw, list) or len(raw) != 2:
        return None
    if any(isinstance(value, bool) or not isinstance(value, int) for value in raw):
        return None
    return (raw[0], raw[1])


def _sell_control_request(policy: PolicyV1, intent: str) -> ActionRequestV1 | None:
    center = _configured_sell_control(policy.navigation_config)
    if center is None:
        return None
    return ActionRequestV1(kind="tap", intent=intent, params={"x": center[0], "y": center[1]})


# F4-07 open item 4.  Order types observed in the three-tray row, in the order
# the row puts them.  Type 3 (the timestamp-id order) leads; the plain type 0
# orders follow, newest slot first.  Type 2 was present in the save on
# 2026-09-06 at slot 3 and was not on the row at all, so it is excluded rather
# than assumed to be somewhere off the right edge.
ROW_ORDER_TYPES = (3, 0)
LEADING_ORDER_TYPE = 3


def _display_slot_order(state: GameStateSemanticV1) -> list[int]:
    """The live left-to-right display sequence of order trays.

    Inferred from two live frames, both with four orders in the save and three
    trays on screen:

      F4-06 segment H  save 1(t0) 2(t0) 3(t0) 4(t3)  ->  trays 4, 3, 2
      2026-09-06 live  save 1(t0) 2(t0) 3(t2) 4(t3)  ->  trays 4, 2, 1

    Type 3 first, then type 0 by descending slot, type 2 not on the row.  The
    caller truncates to the visible positions; this returns the whole sequence
    so an order past the third tray is knowably off screen rather than silently
    mapped onto someone else's tray.
    """

    rows = [
        order
        for order in state.orders
        if order.order_type is None or order.order_type in ROW_ORDER_TYPES
    ]
    return [
        order.slot
        for order in sorted(
            rows,
            key=lambda order: (order.order_type != LEADING_ORDER_TYPE, -order.slot),
        )
    ]


def _order_smileys(order: Any) -> int:
    """The rave-review smileys an order pays when delivered (0 without the event)."""

    bonus = getattr(order, "event_bonus", None)
    return 0 if bonus is None else int(getattr(bonus, "smileys", 0) or 0)


def _delivery_priority(order: Any) -> tuple[int, int, int, str]:
    """Order deliveries by the event smileys they pay, then by coins.

    2026-09-17, by the account owner's instruction: a smiley order outranks a
    richer plain one.  The smileys fill the rave-review meter, whose reward is
    claimed at the cap, so they are worth more than the coin difference between
    two orders the board can serve in the same round.  The earlier rule had it
    the other way round (F4-05: prefer the event only among equals).
    """

    return (-_order_smileys(order), -(order.reward_coins or 0), order.slot, order.order_id)


def _delivery_request(
    geometry: Any | None,
    *,
    slot: int,
    slot_order: list[int],
    intent: str,
    control_override: tuple[int, int] | None = None,
) -> ActionRequestV1 | None:
    if control_override is not None:
        return ActionRequestV1(
            kind="tap",
            intent=intent,
            params={"x": int(control_override[0]), "y": int(control_override[1])},
            settle="stable",
        )
    if geometry is None:
        return None
    try:
        center = geometry.order_card_center(slot, slot_order=slot_order)
    except TypeError:
        center = geometry.order_card_center(slot)
    except (IndexError, ValueError):
        # A slot the calibration never surveyed has no delivery point.  The
        # account unlocked a fourth order slot during the F4-06 long run and
        # order_geometry.v1.json only covers three, so the delivery becomes a
        # blocked action with a recorded reason rather than a crashed run.
        return None
    if not isinstance(center, (tuple, list)) or len(center) != 2:
        raise ValueError("order_geometry.order_card_center must return an (x, y) pair")
    return ActionRequestV1(
        kind="tap",
        intent=intent,
        params={"x": int(center[0]), "y": int(center[1])},
        settle="stable",
    )


def _required_cells(
    state: GameStateSemanticV1,
    required_items: list[Any],
) -> list[CellPositionV1]:
    selected: list[CellPositionV1] = []
    by_type: dict[str, list[CellSemanticV1]] = {}
    for row in state.board.cells:
        for cell in row:
            if cell.kind == "item" and cell.item_type is not None:
                by_type.setdefault(cell.item_type, []).append(cell)
    for required in required_items:
        candidates = sorted(
            by_type.get(required.type, []),
            key=lambda cell: (cell.row, cell.col),
        )
        selected.extend(_position(cell.row, cell.col) for cell in candidates[: required.count])
    return selected


def _wait_reason(
    state: GameStateSemanticV1,
    cells: dict[tuple[int, int], _SimCell],
) -> str:
    if (state.resources.energy or 0) < 1:
        return "energy depleted: waiting for recovery"
    if all(cell.kind != "empty" for cell in cells.values()):
        return "board full: no mergeable pairs"
    if not state.board.generators:
        return "no mergeable pairs or interactable generators"
    return "no safe productive action is currently available"


def _collect_reward_actions(
    policy: PolicyV1,
    cells: dict[tuple[int, int], _SimCell],
    touched_positions: set[tuple[int, int]],
    *,
    first_prediction_index: int,
) -> list[PlannedActionV1]:
    """Plan one select-then-collect for the best supported free board reward.

    ``ItemModelConfig`` chain 2601..2605 pays energy 2/6/15/40/100, so merging a
    pair always yields more than collecting both halves (4 -> 6, 12 -> 15,
    30 -> 40, 80 -> 100).  Any energy item that still has a same-type partner on
    the board is therefore left to the merge planner; the same rule applies to
    non-max-level gold and gem pieces.  Unsupported reward types remain visible
    in semantics but are deliberately left for a surveyed implementation.
    """

    if policy.max_collect_rewards_per_round < 1:
        return []

    partner_counts: Counter[str] = Counter(
        cell.item_type
        for position, cell in cells.items()
        if cell.kind == "item"
        and cell.item_type is not None
        and position not in touched_positions
    )
    candidates: list[tuple[tuple[Any, ...], tuple[int, int], _SimCell, tuple[tuple[str, int], ...]]] = []
    for position, cell in cells.items():
        if (
            cell.kind != "item"
            or cell.item_type is None
            or position in touched_positions
            or partner_counts[cell.item_type] >= 2 and cell.merge_target is not None
        ):
            continue
        rewards = tuple(cell.collect_rewards)
        if not rewards or any(
            reward_type not in SUPPORTED_FREE_COLLECT_REWARD_TYPES
            or amount <= 0
            for reward_type, amount in rewards
        ):
            continue
        if {reward_type for reward_type, _amount in rewards} == {"energy"}:
            if policy.max_energy_collects_per_round < 1:
                continue
        value = sum(amount for _reward_type, amount in rewards)
        candidates.append(((-value, position[0], position[1]), position, cell, rewards))
    candidates.sort(key=lambda item: item[0])
    if not candidates:
        return []
    _score, position_key, cell, rewards = candidates[0]
    position = _position(*position_key)
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
    select_request = _tap_position_request(policy.geometry, position, select_intent)
    collect_request = _tap_position_request(policy.geometry, position, collect_intent)
    if select_request is None or collect_request is None:
        return []
    select_prediction = PredictionV1(
        prediction_id=f"p.{first_prediction_index}",
        kind="wait",
        source_cell=position,
        source_before=cell.item_type,
        source_after=cell.item_type,
    )
    collect_prediction = PredictionV1(
        prediction_id=f"p.{first_prediction_index + 1}",
        kind="collect_energy" if legacy_energy else "collect_reward",
        energy_delta=energy_gain,
        coins_delta=sum(amount for reward_type, amount in rewards if reward_type == "gold"),
        source_cell=position,
        source_before=cell.item_type,
        source_after=None,
        collect_rewards=[
            CollectRewardV1(type=reward_type, amount=amount)
            for reward_type, amount in rewards
        ],
    )
    return [
        PlannedActionV1(
            kind="tap",
            intent=select_intent,
            prediction=select_prediction,
            reason=(
                "select the unmergeable energy item before collecting it"
                if legacy_energy
                else "select the highest-value free reward item before collecting it"
            ),
            request=select_request,
        ),
        PlannedActionV1(
            kind="tap",
            intent=collect_intent,
            prediction=collect_prediction,
            reason=(
                "this free energy item has no same-type partner left to merge with"
                if legacy_energy
                else "collect the highest-value supported free reward after preserving merges"
            ),
            request=collect_request,
            depends_on=select_prediction.prediction_id,
        ),
    ]


def _cache_queue_action(
    policy: PolicyV1,
    state: GameStateSemanticV1,
    *,
    prediction_index: int,
) -> PlannedActionV1 | None:
    """Claim one authoritative, free CacheItem after board cleanup."""

    rows = state.cache_items
    if (
        policy.version != "v2"
        or policy.max_actions_per_round <= 0
        or state.cache_item_free_claim_supported is not True
        or not rows
        or len(state.board.empty_cells) < 3
    ):
        return None
    first = _first_cache_item(rows)
    if first is None:
        return None
    code = first.get("codeStr", first.get("code"))
    control = policy.reward_queue_control
    if control is None:
        return None
    request = ActionRequestV1(
        kind="tap",
        intent="claim-reward-queue",
        params={"x": int(control[0]), "y": int(control[1])},
    )
    prediction = PredictionV1(
        prediction_id=f"p.{prediction_index}",
        kind="claim_reward_queue",
        cache_items_before=len(rows),
        cache_item_code_before=None if code is None else str(code),
        cache_queue_head_id_before=(
            None if first.get("id") is None else str(first.get("id"))
        ),
    )
    return PlannedActionV1(
        kind="tap",
        intent="claim-reward-queue",
        prediction=prediction,
        reason=(
            "claim one free queued reward after resource cleanup; three empty "
            "board cells leave room for the generated item"
        ),
        request=request,
    )


def _sell_candidates(
    state: GameStateSemanticV1,
    cells: dict[tuple[int, int], _SimCell],
    metadata: dict[str, _SimCell],
) -> list[tuple[tuple[Any, ...], tuple[int, int], _SimCell, int, bool]]:
    required_chain_ids = {
        descriptor.chain_id
        for order in state.orders
        for required in order.required_items
        for descriptor in [metadata.get(required.type)]
        if descriptor is not None and descriptor.chain_id is not None
    }
    candidates: list[tuple[tuple[Any, ...], tuple[int, int], _SimCell, int, bool]] = []
    for position, cell in cells.items():
        if (
            cell.kind != "item"
            or cell.item_type is None
            or cell.rare
            or cell.protect_level is not None
            or cell.chain_id in required_chain_ids
        ):
            continue
        # CollectRewards are business collections, never sales.  The generic
        # collectible branch runs before this space-recovery fallback.
        is_coin_collectible = False
        is_direct_sale = (
            cell.sell_price is not None and cell.sell_price > 0 and not cell.special
        )
        if not is_direct_sale:
            continue
        value = cell.sell_price
        if value is None:
            continue
        candidates.append(
            (
                (value, is_coin_collectible, cell.level or 0, cell.item_type, position),
                position,
                cell,
                value,
                is_coin_collectible,
            )
        )
    candidates.sort(key=lambda item: item[0])
    return candidates


def plan_batch(state: GameStateSemanticV1, policy: PolicyV1) -> PlanV1:
    """Return at most ``policy.max_actions_per_round`` deterministic actions."""

    cells: dict[tuple[int, int], _SimCell] = {}
    metadata = dict(_configured_item_metadata(policy.config_dir))
    for row in state.board.cells:
        for cell in row:
            configured = metadata.get(cell.item_type or "")
            simulated = _SimCell(
                item_type=cell.item_type,
                kind=(
                    "visually_occupied"
                    if cell.kind == "empty" and (cell.row, cell.col) in policy.visual_nonempty_cells
                    else cell.kind
                ),
                merge_target=cell.merge_target,
                sell_price=cell.sell_price,
                level=cell.level,
                chain_id=cell.chain_id,
                rare=False if configured is None else configured.rare,
                protect_level=(
                    None if configured is None else configured.protect_level
                ),
                special=False if configured is None else configured.special,
                collect_coins=(
                    None if configured is None else configured.collect_coins
                ),
                collect_energy=cell.energy_gain,
                collect_rewards=tuple(
                    (reward.type, reward.amount) for reward in cell.collect_rewards
                ),
            )
            cells[(cell.row, cell.col)] = simulated
            if cell.item_type is not None:
                metadata.setdefault(cell.item_type, simulated)

    actions: list[PlannedActionV1] = []
    blocked: list[PlannedActionV1] = []
    touched_positions: set[tuple[int, int]] = set()
    produced_positions: set[tuple[int, int]] = set()
    produced_by: dict[tuple[int, int], str] = {}
    # `_merge_candidates` offers chest-kind cells whose config row carries a
    # MergedType, and the spend guard reads these counts to decide whether a
    # pair may be spent.  Counting plain items only made every chest pair look
    # like it would break a reserve it is not part of, so a full board whose
    # only legal moves were chest merges planned a wait, round after round.
    counts = Counter(
        cell.item_type
        for cell in cells.values()
        if cell.kind in {"item", "chest"} and cell.item_type is not None
    )
    initial_empty_positions = sorted(
        position for position, cell in cells.items() if cell.kind == "empty"
    )
    outstanding_counts = _outstanding_required_counts(state, counts)
    outstanding_required = set(outstanding_counts)

    delivered_order_ids: set[str] = set()
    display_slots = _display_slot_order(state)
    delivery_limit = min(
        policy.max_deliveries_per_round,
        policy.max_deliveries_per_run,
        policy.max_actions_per_round,
    )
    for order in sorted(state.orders, key=_delivery_priority):
        if not order.fulfillable_now:
            continue
        required_cells = _required_cells(state, order.required_items)
        prediction = PredictionV1(
            prediction_id=f"p.{len(actions) + 1}",
            kind="deliver",
            coins_delta=order.reward_coins or 0,
            order_slot=order.slot,
            order_before=order.order_id,
            required_cells=required_cells,
            required_item_types=[
                required.type
                for required in order.required_items
                for _index in range(required.count)
            ],
        )
        intent = f"deliver:slot:{order.slot}:order:{order.order_id}"
        request = _delivery_request(
            policy.order_geometry,
            slot=order.slot,
            slot_order=display_slots,
            intent=intent,
            control_override=policy.delivery_control_overrides.get(order.slot),
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
            blocked.append(
                planned.model_copy(
                    update={
                        "prediction": prediction.model_copy(
                            update={"prediction_id": f"b.{len(blocked) + 1}"}
                        ),
                    }
                )
            )
            continue
        if len(actions) >= delivery_limit:
            continue
        actions.append(planned)
        delivered_order_ids.add(order.order_id)
        for required_cell in required_cells:
            key = _position_key(required_cell)
            item_type = cells[key].item_type
            if item_type is not None:
                counts[item_type] -= 1
            cells[key] = _SimCell(None, "empty", None, None, None)
            touched_positions.add(key)

    reserves = _required_reserves(
        state,
        excluded_order_ids=delivered_order_ids,
    )
    if delivered_order_ids:
        return PlanV1(
            actions=actions,
            predictions=[action.prediction for action in actions],
            blocked_actions=blocked,
            visual_occupancy=policy.visual_occupancy,
        )

    main_task = state.main_task
    if (
        policy.enable_main_task
        and policy.current_screen == "main-board"
        and policy.max_main_tasks_per_round == 1
        and main_task is not None
        and main_task.affordable is True
        and main_task.cost is not None
    ):
        prediction = PredictionV1(
            prediction_id="p.1",
            kind="start_main_task",
            coins_delta=-main_task.cost,
            main_task_before=main_task.task_id,
            main_task_cost=main_task.cost,
        )
        intent = f"start-main-task:{main_task.task_id}"
        action = PlannedActionV1(
            kind="start_main_task",
            intent=intent,
            prediction=prediction,
            reason=(
                "affordable main task on the recognized main board has priority "
                "below delivery and above merge or generation"
            ),
            request=ActionRequestV1(
                kind="wait",
                intent=f"{intent}:delegated-flow",
                params={"ms": 0},
            ),
        )
        return PlanV1(
            actions=[action],
            predictions=[prediction],
            visual_occupancy=policy.visual_occupancy,
        )

    if policy.version == "v2":
        resource_merge_candidates = _merge_candidates(
            cells, set(), set(), outstanding_required, metadata
        )
        resource_collect_actions = _collect_reward_actions(
            policy,
            cells,
            set(),
            first_prediction_index=1,
        )
        resource_collect_pending = bool(resource_collect_actions)
        resource_merge_pending = any(
            candidate.cell.collect_rewards
            and all(
                reward_type in SUPPORTED_FREE_COLLECT_REWARD_TYPES
                and amount > 0
                for reward_type, amount in candidate.cell.collect_rewards
            )
            for candidate in resource_merge_candidates
        )
        configured_outputs = _configured_chest_outputs(policy.config_dir)
        timer_types = configured_timer_chest_types(policy.config_dir)
        resource_cleanup_pending = resource_collect_pending or resource_merge_pending
        chest_merge_pending = any(
            candidate.cell.item_type is not None
            and cells.get(candidate.target) is not None
            and candidate.cell.item_type == cells[candidate.target].item_type
            and is_free_chest_type(candidate.cell.item_type, policy.config_dir)
            for candidate in resource_merge_candidates
        )
        # A standalone supported collectible is a complete resource phase.
        # Return before ordinary reserve bookkeeping, generation, or waiting;
        # otherwise a full action budget can bury the tap and report a blocked
        # lower-priority merge alongside it.
        if resource_collect_pending and not resource_merge_pending:
            return PlanV1(
                actions=resource_collect_actions,
                predictions=[action.prediction for action in resource_collect_actions],
                blocked_actions=[],
                visual_occupancy=policy.visual_occupancy,
            )
        board_chests = _chest_states(state, policy)
        # 2026-09-16, proven on the save: the game runs one chest timer at a
        # time.  Round 1 moved (2,2) from closed to opening; rounds 2-4 then
        # tapped three other closed chests with full stock and the save came
        # back unchanged every time.  Each of those refusals used to lay a
        # permanent hold on the chest it touched, which is how six chests ended
        # up sitting closed for hours.
        a_timer_is_already_running = any(
            chest.spread_state == SPREAD_STATE_OPENING for chest in board_chests
        )
        for chest in board_chests:
            if resource_cleanup_pending or chest_merge_pending:
                break
            position = _position(chest.row, chest.col)
            key = _position_key(position)
            cell = cells.get(key)
            if cell is None or cell.item_type != chest.item_type:
                continue
            # Deliveries and merges planned earlier in this round already
            # emptied their cells in `cells`; reading the start-of-round
            # snapshot here meant a ready chest never saw the space they freed,
            # and the generate loop below (which does read the live board) took
            # it instead.  A full board therefore never opened a chest again.
            live_empty_positions = sorted(
                position for position, entry in cells.items() if entry.kind == "empty"
            )
            actionable, emits_now, possible_types = _chest_actionability(
                chest,
                has_empty_cell=bool(live_empty_positions),
                configured_outputs=configured_outputs,
                can_start_timer=chest.item_type in timer_types
                and not a_timer_is_already_running,
            )
            if not actionable:
                continue
            intent = f"open-chest:{chest.item_type}"
            request = _tap_position_request(policy.geometry, position, intent)
            if emits_now and live_empty_positions:
                target_key = live_empty_positions[0]
                target = _position(*target_key)
                prediction = PredictionV1(
                    prediction_id="p.2",
                    kind="open_chest",
                    source_cell=position,
                    target_cell=target,
                    source_before=chest.item_type,
                    source_after=chest.item_type,
                    target_before=None,
                    target_after=possible_types[0] if possible_types else None,
                    possible_target_types=possible_types,
                    chest_spread_state_before=chest.spread_state,
                    chest_item_rest_before=chest.item_rest,
                    chest_storage_rest_before=chest.storage_rest,
                    chest_expected_output_types=possible_types,
                    chest_requires_state_change=True,
                    chest_requires_item_decrement=True,
                )
                selection_prediction = PredictionV1(
                    prediction_id="p.1",
                    kind="wait",
                    source_cell=position,
                    source_before=chest.item_type,
                    source_after=chest.item_type,
                )
                selection = PlannedActionV1(
                    kind="tap",
                    intent=f"select-chest:{chest.item_type}",
                    prediction=selection_prediction,
                    reason="select the free opened chest before requesting one output",
                    request=_tap_position_request(
                        policy.geometry,
                        position,
                        f"select-chest:{chest.item_type}",
                    ),
                )
                opening = PlannedActionV1(
                    kind="open_chest",
                    intent=intent,
                    prediction=prediction,
                    reason=(
                        "free ready chest has priority and emits into an authoritative "
                        "empty cell without energy cost"
                    ),
                    request=request,
                    status=_request_status(request),
                )
                chest_actions = (
                    [selection, opening]
                    if policy.max_actions_per_round >= 2
                    else [opening]
                )
            else:
                prediction = PredictionV1(
                    prediction_id="p.2",
                    kind="open_chest",
                    source_cell=position,
                    source_before=chest.item_type,
                    source_after=chest.item_type,
                    chest_spread_state_before=chest.spread_state,
                    chest_item_rest_before=chest.item_rest,
                    chest_storage_rest_before=chest.storage_rest,
                    chest_requires_state_change=True,
                )
                # 2026-09-16: a timer start needs the same two taps a ready
                # emission needs.  A 2802 that had just been merged into
                # existence took one tap and the save came back byte-identical
                # -- which is exactly what a selection-only tap looks like --
                # so the round booked a refutation and the chest kept its
                # timer unstarted.
                selection_prediction = PredictionV1(
                    prediction_id="p.1",
                    kind="wait",
                    source_cell=position,
                    source_before=chest.item_type,
                    source_after=chest.item_type,
                )
                selection = PlannedActionV1(
                    kind="tap",
                    intent=f"select-chest:{chest.item_type}",
                    prediction=selection_prediction,
                    reason="select the free closed chest before starting its timer",
                    request=_tap_position_request(
                        policy.geometry,
                        position,
                        f"select-chest:{chest.item_type}",
                    ),
                )
                opening = PlannedActionV1(
                    kind="open_chest",
                    intent=f"{intent}:start-free-timer",
                    prediction=prediction,
                    reason=(
                        "start or advance the free chest timer; gem acceleration "
                        "is outside the action space"
                    ),
                    request=request,
                    status=_request_status(request),
                )
                chest_actions = (
                    [selection, opening]
                    if policy.max_actions_per_round >= 2
                    else [
                        opening.model_copy(
                            update={
                                "prediction": prediction.model_copy(
                                    update={"prediction_id": "p.1"}
                                )
                            }
                        )
                    ]
                )
            if all(action.request is not None for action in chest_actions):
                return PlanV1(
                    actions=chest_actions,
                    predictions=[action.prediction for action in chest_actions],
                    blocked_actions=blocked,
                    visual_occupancy=policy.visual_occupancy,
                )
            blocked.extend(action for action in chest_actions if action.request is None)

    # 订单棋子宝箱.  The picked piece lands in the chest's own cell, so this is
    # the only opener that still works with nowhere to put an output -- exactly
    # the state the board keeps reaching.  The game guarantees all three cards
    # serve a current order, so the first card is a sound pick and no card
    # pixels are interpreted here.
    choice_chest_types = configured_choice_chest_types(policy.config_dir)
    choice_controls = _choice_panel_controls(policy.navigation_config)
    if (
        policy.version == "v2"
        and not actions
        and policy.max_actions_per_round >= 4
        and choice_chest_types
        and {"choice-1", "confirm"} <= set(choice_controls)
    ):
        board_is_stuck = not initial_empty_positions and not _merge_candidates(
            cells,
            touched_positions,
            set(),
            outstanding_required if policy.version == "v2" else set(),
            metadata,
        )
        chest_cells = [
            (position_key, cell)
            for position_key, cell in sorted(cells.items())
            if cell.item_type in choice_chest_types
            and position_key not in policy.suppressed_chest_cells
        ]
        if board_is_stuck and chest_cells:
            position_key, cell = chest_cells[0]
            position = _position(*position_key)
            item_type = str(cell.item_type)
            choices = list(choice_chest_types[item_type])
            steps = (
                ("navigation:order-piece-chest:{}:select", "select the chest; the footer names it 订单棋子宝箱", None),
                ("navigation:order-piece-chest:{}:open", "open its 开启挑选箱 panel", None),
                ("popup:selection:order-piece-chest-choice:choice-1", "highlight the first card and reveal 选择", choice_controls["choice-1"]),
                ("popup:confirm:order-piece-chest-choice:confirm", "confirm; the chest becomes the picked piece in place", choice_controls["confirm"]),
            )
            choice_actions: list[PlannedActionV1] = []
            for index, (intent_template, reason, control) in enumerate(steps, start=1):
                intent = intent_template.format(item_type)
                if control is None:
                    request = _tap_position_request(policy.geometry, position, intent)
                else:
                    request = ActionRequestV1(
                        kind="tap",
                        intent=intent,
                        params={"x": control[0], "y": control[1]},
                    )
                if index < len(steps):
                    prediction = PredictionV1(
                        prediction_id=f"p.{index}",
                        kind="wait",
                        source_cell=position,
                        source_before=item_type,
                        source_after=item_type,
                    )
                else:
                    prediction = PredictionV1(
                        prediction_id=f"p.{index}",
                        kind="pick_chest_choice",
                        source_cell=position,
                        source_before=item_type,
                        possible_target_types=choices,
                    )
                choice_actions.append(
                    PlannedActionV1(
                        kind="tap",
                        intent=intent,
                        prediction=prediction,
                        reason=reason,
                        request=request,
                        status=_request_status(request),
                    )
                )
            if all(action.request is not None for action in choice_actions):
                return PlanV1(
                    actions=choice_actions,
                    predictions=[action.prediction for action in choice_actions],
                    blocked_actions=blocked,
                    visual_occupancy=policy.visual_occupancy,
                )
            blocked.extend(action for action in choice_actions if action.request is None)

    # "Space is back, so take it out again."  Retrieval is three registered
    # taps (open, the first shelf slot, close) and is only worth a round once
    # the board has room to hold the piece and work with it.
    warehouse_controls = _warehouse_controls(policy.navigation_config)
    if (
        policy.version == "v2"
        and not actions
        and policy.warehouse_parked
        and policy.max_actions_per_round >= 3
        and len(initial_empty_positions) >= WAREHOUSE_RETRIEVE_MIN_EMPTY_CELLS
        and {"entry", "slot-1", "close"} <= set(warehouse_controls)
    ):
        parked_type = policy.warehouse_parked[0]
        steps = (
            ("navigation:warehouse:open", warehouse_controls["entry"], "open the warehouse"),
            (
                f"navigation:warehouse:retrieve:{parked_type}",
                warehouse_controls["slot-1"],
                "tap the parked piece; it leaves the shelf and returns to the board",
            ),
            ("navigation:warehouse:close", warehouse_controls["close"], "close the warehouse"),
        )
        retrieve_actions = [
            PlannedActionV1(
                kind="tap",
                intent=intent,
                prediction=PredictionV1(
                    prediction_id=f"p.{index}",
                    kind="retrieve_from_warehouse" if index == 2 else "wait",
                    source_after=parked_type if index == 2 else None,
                ),
                reason=reason,
                request=ActionRequestV1(
                    kind="tap", intent=intent, params={"x": control[0], "y": control[1]}
                ),
            )
            for index, (intent, control, reason) in enumerate(steps, start=1)
        ]
        return PlanV1(
            actions=retrieve_actions,
            predictions=[action.prediction for action in retrieve_actions],
            blocked_actions=blocked,
            visual_occupancy=policy.visual_occupancy,
        )

    merge_limit = min(
        policy.max_merges_per_round,
        policy.max_actions_per_round - len(actions),
    )
    merges = 0
    # F4-11, planner ruling of 2026-09-07 (option "bing").  A pair the order
    # reserve refuses is not a pair the run can use, and until now nothing said
    # so: the board showed two mergeable pairs, the plan was all wait, and the
    # reader had to guess which of the two reasons applied.  Behaviour is
    # unchanged -- keeping an item an open order needs is correct -- only the
    # record grows.
    reserve_blocked: dict[
        tuple[tuple[int, int], tuple[int, int]], dict[str, Any]
    ] = {}
    resource_merges_done = 0
    while merges < merge_limit:
        selected = None
        for candidate in _merge_candidates(
            cells,
            touched_positions,
            produced_positions if policy.allow_chained_merge else set(),
            outstanding_required if policy.version == "v2" else set(),
            metadata,
        ):
            first = candidate.source
            second = candidate.target
            cell = candidate.cell
            if candidate.cobweb_target and policy.version != "v2":
                continue
            # F4-09 1.6: a pair the device has already declined, and a piece
            # that has only just landed, are both skipped rather than tried
            # again this round.
            if (
                tuple(sorted((first, second))) in policy.suppressed_merge_pairs
                or first in policy.held_positions
                or second in policy.held_positions
            ):
                continue
            if cell.item_type is None or cell.merge_target is None:
                continue
            can_spend = (
                _can_spend_cobweb_source(
                    cell.item_type,
                    cell.merge_target,
                    counts,
                    reserves,
                )
                if candidate.cobweb_target
                else _can_spend_pair(
                    cell.item_type,
                    cell.merge_target,
                    counts,
                    reserves,
                )
            )
            if not can_spend:
                key = tuple(sorted((first, second)))
                reserve_blocked.setdefault(
                    key,
                    {
                        "source": first,
                        "target": second,
                        "item_type": cell.item_type,
                        "merge_target": cell.merge_target,
                        "counts": {
                            cell.item_type: counts[cell.item_type],
                            cell.merge_target: counts[cell.merge_target],
                        },
                        "reserves": {
                            cell.item_type: reserves[cell.item_type],
                            cell.merge_target: reserves[cell.merge_target],
                        },
                    },
                )
                continue
            resource_merge = bool(cell.collect_rewards) and all(
                reward_type in SUPPORTED_FREE_COLLECT_REWARD_TYPES and amount > 0
                for reward_type, amount in cell.collect_rewards
            )
            if resource_merges_done and not resource_merge:
                selected = None
                break
            selected = (first, second, cell, candidate.cobweb_target)
            break
        if selected is None:
            break

        first, second, cell, cobweb_target = selected
        source = _position(*first)
        target = _position(*second)
        intent = (
            f"clear-cobweb:merge:{cell.item_type}->{cell.merge_target}"
            if cobweb_target
            else f"merge:{cell.item_type}->{cell.merge_target}"
        )
        prediction = PredictionV1(
            prediction_id=f"p.{len(actions) + 1}",
            kind="merge",
            source_cell=source,
            target_cell=target,
            source_before=cell.item_type,
            source_after=None,
            target_before=f"c#{cell.item_type}" if cobweb_target else cell.item_type,
            target_after=cell.merge_target,
        )
        dependency_ids = [
            produced_by[position] for position in (first, second) if position in produced_by
        ]
        depends_on = dependency_ids[-1] if dependency_ids else None
        request = _drag_request(policy.geometry, source, target, intent)
        if cobweb_target and request is not None:
            request = request.model_copy(update={"settle": "stable"})
        actions.append(
            PlannedActionV1(
                kind="drag",
                intent=intent,
                prediction=prediction,
                reason=(
                    # F4-07 open item 4: the lightning number on a paperbox is
                    # what clearing it PAYS.  Board clearing is therefore a
                    # benefit and is never charged against the energy budget
                    # below; the pending reward is named here so the plan
                    # record carries it.
                    "merge a normal same-Type item onto its cobwebbed target to clear "
                    "the web and recover space; clearing the board still pays "
                    f"{state.board.sand_clear_energy_pending} energy in paperbox "
                    "rewards"
                    if cobweb_target and state.board.sand_clear_energy_pending
                    else "merge a normal same-Type item onto its cobwebbed target to "
                    "clear the web and recover space"
                    if cobweb_target
                    else "order-directed safe merge toward an outstanding requirement"
                    if _chain_distance(cell.merge_target, outstanding_required, metadata)
                    is not None
                    else "lowest-value safe merge that preserves required order items"
                ),
                request=request,
                status=_request_status(request),
                depends_on=depends_on,
            )
        )
        merges += 1
        if resource_merge:
            resource_merges_done += 1
        touched_positions.update((first, second))
        produced_positions.discard(first)
        produced_positions.add(second)
        produced_by.pop(first, None)
        produced_by[second] = prediction.prediction_id
        counts[cell.item_type] -= 1 if cobweb_target else 2
        counts[cell.merge_target] += 1
        cells[first] = _SimCell(None, "empty", None, None, None)
        target_metadata = metadata.get(cell.merge_target)
        cells[second] = (
            target_metadata
            if target_metadata is not None
            else _SimCell(cell.merge_target, "item", None, None, None)
        )
        if cobweb_target:
            return PlanV1(
                actions=actions,
                predictions=[action.prediction for action in actions],
                blocked_actions=blocked,
                visual_occupancy=policy.visual_occupancy,
            )

    if resource_merges_done:
        remaining_after_resource = policy.max_actions_per_round - len(actions)
        if (
            policy.version == "v2"
            and policy.max_collect_rewards_per_round > 0
            and remaining_after_resource >= 2
        ):
            collect_actions = _collect_reward_actions(
                policy,
                cells,
                touched_positions,
                first_prediction_index=len(actions) + 1,
            )
            actions.extend(collect_actions[:2])
        return PlanV1(
            actions=actions,
            predictions=[action.prediction for action in actions],
            blocked_actions=blocked,
            visual_occupancy=policy.visual_occupancy,
        )

    for key, detail in reserve_blocked.items():
        if key[0] in touched_positions or key[1] in touched_positions:
            continue
        source = _position(*detail["source"])
        target = _position(*detail["target"])
        blocked.append(
            PlannedActionV1(
                kind="drag",
                intent=f"merge:{detail['item_type']}->{detail['merge_target']}",
                prediction=PredictionV1(
                    prediction_id=f"b.{len(blocked) + 1}",
                    kind="merge",
                    source_cell=source,
                    target_cell=target,
                    source_before=detail["item_type"],
                    source_after=None,
                    target_before=detail["item_type"],
                    target_after=detail["merge_target"],
                ),
                reason=(
                    "order_reserve: merging this pair would spend an item an open "
                    f"order requires; counts={detail['counts']} "
                    f"reserves={detail['reserves']}"
                ),
                request=None,
                status="blocked_order_reserve",
            )
        )

    remaining_slots = policy.max_actions_per_round - len(actions)
    generate_limit = min(policy.max_generates_per_round, remaining_slots)
    available_energy = state.resources.energy or 0

    generation_cost_floor = min(
        (
            max(cell.generator.cost_energy or 1, 1)
            for row in state.board.cells
            for cell in row
            if cell.kind == "generator" and cell.generator is not None
        ),
        default=1,
    )
    if (
        policy.version == "v2"
        and policy.max_collect_rewards_per_round > 0
        and remaining_slots >= 2
    ):
        collect_actions = _collect_reward_actions(
            policy,
            cells,
            touched_positions,
            first_prediction_index=len(actions) + 1,
        )
        if collect_actions:
            actions.extend(collect_actions[:2])
            return PlanV1(
                actions=actions,
                predictions=[action.prediction for action in actions],
                blocked_actions=blocked,
                visual_occupancy=policy.visual_occupancy,
            )

    if policy.version == "v2" and remaining_slots >= 1:
        queue_action = _cache_queue_action(
            policy,
            state,
            prediction_index=len(actions) + 1,
        )
        if queue_action is not None:
            actions.append(queue_action)
            return PlanV1(
                actions=actions,
                predictions=[action.prediction for action in actions],
                blocked_actions=blocked,
                visual_occupancy=policy.visual_occupancy,
            )

    # What a line actually costs in pulls.  One item `distance` merges above a
    # producer's output takes 2**distance of that output, so a linear
    # `distance * missing_count` prefers a seven-step chain needing one item
    # (128 pulls) over a three-step chain needing three (24).
    def _pull_cost_from(output_type: str | None, required_type: str) -> int | None:
        distance = _chain_distance(output_type, {required_type}, metadata)
        return None if distance is None else 1 << min(distance, 16)

    # The cheapest any producer standing on this board can make one of each
    # outstanding type.  A type nothing reaches is what makes an order
    # unclosable by generating -- it has to arrive from a chest, the reward
    # queue or the warehouse instead.
    cheapest_unit_cost: dict[str, int] = {}
    for board_row in state.board.cells:
        for board_cell in board_row:
            if board_cell.kind != "generator" or board_cell.generator is None:
                continue
            for weight in board_cell.generator.spread_weights:
                for required_type in outstanding_counts:
                    unit = _pull_cost_from(weight.item_type, required_type)
                    if unit is None:
                        continue
                    if unit < cheapest_unit_cost.get(required_type, unit + 1):
                        cheapest_unit_cost[required_type] = unit

    # 2026-09-16: half an hour of play spent 145 generations on 4 deliveries.
    # Three of the six standing orders needed a 9xx piece no producer on the
    # board can reach, so every pull aimed at their other line was spent on an
    # order that could not close.  Cost the whole order, and let a producer
    # inherit the cost of the cheapest order it actually serves.
    # 2026-09-17, on the user's observation that resources were being wasted:
    # a half-built chain was invisible here.  An item `d` merges below the
    # required type is already 2**-d of it, so a board holding a 507 makes a
    # 508 cost one more 507 rather than 128 fresh pulls.  Without this credit
    # the planner kept re-pricing every order from zero, preferred whichever
    # looked cheapest from scratch, and abandoned chains it had nearly
    # finished -- which is exactly how 16 of the 26 non-producer pieces on the
    # board came to serve no standing order at all.
    def _chain_credit(required_type: str) -> float:
        credit = 0.0
        for entry in cells.values():
            if entry.kind not in {"item", "item_cobwebbed", "chest"}:
                continue
            distance = _chain_distance(entry.item_type, {required_type}, metadata)
            if distance is not None:
                credit += 2.0**-distance
        return credit

    chain_credit = {
        required_type: _chain_credit(required_type)
        for required_type in outstanding_counts
    }

    def _missing_units(required: Any) -> float:
        return max(required.count - chain_credit.get(required.type, 0.0), 0.0)

    order_pull_cost: dict[str, float | None] = {}
    for order in state.orders:
        total = 0.0
        unreachable = False
        for required in order.required_items:
            missing = _missing_units(required)
            if missing <= 0:
                continue
            unit = cheapest_unit_cost.get(required.type)
            if unit is None:
                unreachable = True
                break
            total += missing * unit
        order_pull_cost[order.order_id] = None if unreachable else total

    def generator_priority(cell: CellSemanticV1) -> tuple[Any, ...]:
        scores = []
        for weight in (
            cell.generator.spread_weights if cell.generator is not None else []
        ):
            for order in state.orders:
                whole_order = order_pull_cost.get(order.order_id)
                for required in order.required_items:
                    missing = _missing_units(required)
                    if missing <= 0:
                        continue
                    unit = _pull_cost_from(weight.item_type, required.type)
                    if unit is None:
                        continue
                    line = missing * unit
                    distance = _chain_distance(
                        weight.item_type, {required.type}, metadata
                    )
                    scores.append(
                        (
                            whole_order is None,
                            # 2026-09-17: an order that pays no coins (Gerald's
                            # community gift orders) is only built for when no
                            # coin order can use the pull.  Coins pay for the
                            # main tasks, the only experience in the game.
                            order.reward_coins == 0,
                            # 2026-09-17, by the owner's instruction: among
                            # orders the board can actually close, build toward
                            # a smiley order before a plain one, then by cost.
                            -_order_smileys(order),
                            whole_order if whole_order is not None else line,
                            line,
                            distance if distance is not None else 0,
                            -(weight.weight or 0),
                            required.type,
                        )
                    )
        best = min(scores) if scores else None
        return (
            policy.version == "v2" and best is None,
            () if best is None else best,
            cell.row,
            cell.col,
        )

    all_generators = sorted(
        (
            cell
            for row in state.board.cells
            for cell in row
            if cell.kind == "generator"
        ),
        key=generator_priority,
    )
    exhausted_generators = [
        cell
        for cell in all_generators
        if cell.generator is not None and cell.generator.inventory_exhausted is True
    ]
    generators = [
        cell
        for cell in all_generators
        # F4-09 1.6: a generator whose production was refuted recently is held
        # out for a few rounds rather than tapped again every round.
        if cell.interactable
        and cell not in exhausted_generators
        and (cell.row, cell.col) not in policy.suppressed_generator_cells
    ]
    inventory_remaining: dict[tuple[int, int], int | None] = {}
    for cell in generators:
        generator_semantic = cell.generator
        if (
            generator_semantic is not None
            and generator_semantic.item_rest is not None
            and generator_semantic.storage_rest is not None
        ):
            inventory_remaining[(cell.row, cell.col)] = (
                generator_semantic.item_rest + generator_semantic.storage_rest
            )
        else:
            inventory_remaining[(cell.row, cell.col)] = None
    empty_positions = sorted(position for position, cell in cells.items() if cell.kind == "empty")
    # A ready chest can only hand over its contents into an empty cell, and a
    # generator will happily take the last one every round -- which is how the
    # board reached 63/63 with a dozen loaded chests sitting on it.  Keep one
    # cell for the chests whenever at least one of them is ready to emit.
    ready_chest_waiting = any(
        _chest_actionability(
            chest,
            has_empty_cell=True,
            configured_outputs=configured_outputs,
            can_start_timer=chest.item_type in timer_types,
        )[1]
        for chest in _chest_states(state, policy)
    )
    chest_headroom = 1 if ready_chest_waiting else 0
    generated = 0
    free_pulls = 0
    free_pull_limit = min(
        FREE_PRODUCER_PULLS_PER_ROUND,
        max(0, policy.max_actions_per_round - len(actions)),
    )

    multiplier = max(int(policy.generate_energy_multiplier or 1), 1)

    def _pull_cost(cell: Any) -> int:
        semantic = cell.generator
        modelled = (
            int(semantic.cost_energy)
            if semantic is not None and semantic.cost_energy is not None
            else 1
        )
        # A free pull stays free whatever the boost multiplies.
        return modelled * multiplier if modelled > 0 else modelled

    while (
        (generated < generate_limit or free_pulls < free_pull_limit)
        and generators
        and len(empty_positions) > chest_headroom
    ):
        def _ready_now(cell: Any) -> bool:
            # spreadItemRestNumber is what the producer can hand over right
            # now; spreadStorageRestNumber is the reserve it refills from over
            # its recovery timer.  Tapping a producer whose ready stock is zero
            # yields nothing and books a refutation.
            semantic = cell.generator
            if semantic is None or semantic.item_rest is None:
                return True
            return semantic.item_rest > 0

        with_stock = [
            cell
            for cell in generators
            if inventory_remaining[(cell.row, cell.col)] != 0 and _ready_now(cell)
        ]
        free_first = [cell for cell in with_stock if _pull_cost(cell) == 0]
        paid = [cell for cell in with_stock if _pull_cost(cell) > 0]
        if free_pulls < free_pull_limit and free_first:
            generator = free_first[0]
        elif generated < generate_limit and paid:
            generator = paid[0]
        else:
            generator = None
        if generator is None:
            break
        cost = _pull_cost(generator)
        if cost > 0 and available_energy < cost:
            break
        target_key = empty_positions.pop(0)
        possible_types = (
            [item.item_type for item in generator.generator.spread_weights]
            if generator.generator is not None
            else []
        )
        predicted_type = possible_types[0] if possible_types else None
        source = _position(generator.row, generator.col)
        target = _position(*target_key)
        intent = f"generate:{generator.item_type or generator.raw_code or 'unknown'}"
        prediction = PredictionV1(
            prediction_id=f"p.{len(actions) + 1}",
            kind="generate",
            energy_delta=-cost,
            source_cell=source,
            target_cell=target,
            source_before=generator.item_type,
            source_after=generator.item_type,
            target_before=None,
            target_after=predicted_type,
            possible_target_types=possible_types,
            generator_inventory_available=(
                None
                if inventory_remaining[(generator.row, generator.col)] is None
                else True
            ),
        )
        request = _tap_request(policy.geometry, generator, intent)
        actions.append(
            PlannedActionV1(
                kind="tap",
                intent=intent,
                prediction=prediction,
                reason=(
                    "generate from the shortest configured chain toward an outstanding order"
                    if policy.version == "v2" and not generator_priority(generator)[0]
                    else "energy and board space permit a generator action"
                ),
                request=request,
                status=_request_status(request),
            )
        )
        cells[target_key] = _SimCell(predicted_type, "item", None, None, None)
        available_energy -= cost
        remaining_inventory = inventory_remaining[(generator.row, generator.col)]
        if remaining_inventory is not None:
            inventory_remaining[(generator.row, generator.col)] = max(
                remaining_inventory - 1,
                0,
            )
        if cost == 0:
            free_pulls += 1
        else:
            generated += 1

    if (
        policy.version == "v2"
        and not actions
        and not initial_empty_positions
        and reserve_blocked
    ):
        # 2026-09-18 live: 63/63 cells, the only pair was two 504s reserved for an order,
        # nothing was sellable (every chain on the board belonged to some order) and the
        # warehouse fallback below refused to run because a merge candidate existed at all.
        # The run waited eleven rounds with 89 energy unspent -- from outside, the game
        # had stopped.  A reserve keeps an item an order needs; it is not worth a dead
        # board.  Spend the pair whose orders are worth least (Gerald's gift orders pay
        # no coins), and record that this is what happened.
        order_value: dict[str, int] = {}
        for order in state.orders:
            for required in order.required_items:
                order_value[required.type] = max(order_value.get(required.type, 0), order.reward_coins or 0)
        key, detail = min(
            reserve_blocked.items(),
            key=lambda item: (order_value.get(item[1]["item_type"], 0), item[0]),
        )
        source = _position(*detail["source"])
        target = _position(*detail["target"])
        intent = f"merge:{detail['item_type']}->{detail['merge_target']}"
        request = _drag_request(policy.geometry, source, target, intent)
        prediction = PredictionV1(
            prediction_id="p.1",
            kind="merge",
            source_cell=source,
            target_cell=target,
            source_before=detail["item_type"],
            source_after=None,
            target_before=detail["item_type"],
            target_after=detail["merge_target"],
        )
        actions.append(PlannedActionV1(
            kind="drag",
            intent=intent,
            prediction=prediction,
            reason=(
                "board is full and every other action is blocked; spend the reserved pair "
                f"whose order pays least ({order_value.get(detail['item_type'], 0)} coins) "
                f"instead of waiting: counts={detail['counts']} reserves={detail['reserves']}"
            ),
            request=request,
            status=_request_status(request),
        ))
        blocked = [item for item in blocked if item.prediction.source_cell != source]
        return PlanV1(
            actions=actions,
            predictions=[action.prediction for action in actions],
            blocked_actions=blocked,
            visual_occupancy=policy.visual_occupancy,
        )

    if (
        policy.version == "v2"
        and not actions
        and not initial_empty_positions
        and policy.max_sells_per_round > 0
    ):
        for _sort, position_key, cell, value, coin_collectible in _sell_candidates(
            state,
            cells,
            metadata,
        )[: policy.max_sells_per_round]:
            if len(actions) + 2 > policy.max_actions_per_round:
                break
            position = _position(*position_key)
            select_intent = f"select-sell-item:{cell.item_type}"
            select_request = _tap_position_request(
                policy.geometry,
                position,
                select_intent,
            )
            sell_intent = (
                f"collect-coin-piece:{cell.item_type}"
                if coin_collectible
                else f"sell-item:{cell.item_type}:price:{value}"
            )
            sell_request = (
                _tap_position_request(policy.geometry, position, sell_intent)
                if coin_collectible
                else _sell_control_request(policy, sell_intent)
            )
            if select_request is None or sell_request is None:
                prediction = PredictionV1(
                    prediction_id=f"b.{len(blocked) + 1}",
                    kind="sell",
                    source_cell=position,
                    source_before=cell.item_type,
                    source_after=None,
                    coins_delta=value,
                )
                blocked.append(
                    PlannedActionV1(
                        kind="sell",
                        intent=sell_intent,
                        prediction=prediction,
                        reason="safe sale is blocked because a required UI coordinate is missing",
                        request=sell_request,
                        status="blocked_missing_geometry",
                    )
                )
                continue
            select_prediction = PredictionV1(
                prediction_id=f"p.{len(actions) + 1}",
                kind="wait",
                source_cell=position,
                source_before=cell.item_type,
                source_after=cell.item_type,
            )
            actions.append(
                PlannedActionV1(
                    kind="tap",
                    intent=select_intent,
                    prediction=select_prediction,
                    reason="select the verified low-value non-order-chain item",
                    request=select_request,
                )
            )
            prediction = PredictionV1(
                prediction_id=f"p.{len(actions) + 1}",
                kind="sell",
                source_cell=position,
                source_before=cell.item_type,
                source_after=None,
                coins_delta=value,
            )
            actions.append(
                PlannedActionV1(
                    kind="sell",
                    intent=sell_intent,
                    prediction=prediction,
                    reason=(
                        "collect a verified coin piece to recover one full-board cell"
                        if coin_collectible
                        else "sell the lowest-price safe non-order-chain item to recover space"
                    ),
                    request=sell_request,
                    depends_on=select_prediction.prediction_id,
                )
            )
            counts[cell.item_type] -= 1
            cells[position_key] = _SimCell(None, "empty", None, None, None)

    if not actions and exhausted_generators and empty_positions:
        generator = exhausted_generators[0]
        generator_semantic = generator.generator
        wait_seconds = min(
            (
                generator_semantic.recovery_wait_seconds
                if generator_semantic is not None
                and generator_semantic.recovery_wait_seconds is not None
                else policy.energy_recovery_seconds
            ),
            60,
        )
        prediction = PredictionV1(
            prediction_id="p.1",
            kind="generator_inventory_wait",
            source_cell=_position(generator.row, generator.col),
            source_before=generator.item_type,
            source_after=generator.item_type,
            generator_inventory_available=False,
        )
        actions.append(
            PlannedActionV1(
                kind="wait",
                intent="wait:generator_inventory_recovery",
                prediction=prediction,
                reason=(
                    "generator inventory depleted: item_rest=0 and storage_rest=0; "
                    f"wait {wait_seconds}s for configured recovery"
                ),
                request=ActionRequestV1(
                    kind="wait",
                    intent="wait:generator_inventory_recovery",
                    params={"ms": wait_seconds * 1000},
                    capture_pre=False,
                    capture_post=False,
                ),
            )
        )

    # Last resort before waiting: park a piece no open order needs in the
    # warehouse.  Nothing is destroyed -- a tap on the shelf brings it back --
    # so this only buys the board the empty cell it needs to move again.
    warehouse_entry = _warehouse_entry(policy.navigation_config)
    if (
        policy.version == "v2"
        and not actions
        and warehouse_entry is not None
        and not initial_empty_positions
        and not policy.warehouse_stash_held
    ):
        # `not actions` already means nothing was planned this round.  The old condition
        # also demanded that no merge candidate exist at all, so a board whose only pair
        # was unusable (reserved, suppressed or held) skipped the warehouse too and just
        # waited -- the 2026-09-18 stall.
        required_chain_ids = {
            descriptor.chain_id
            for order in state.orders
            for required in order.required_items
            for descriptor in [metadata.get(required.type)]
            if descriptor is not None and descriptor.chain_id is not None
        }
        parkable = sorted(
            (
                (cell.sell_price if cell.sell_price is not None else 0, position_key)
                for position_key, cell in cells.items()
                if cell.kind == "item"
                and cell.item_type is not None
                and not cell.special
                and cell.protect_level is None
                and cell.chain_id not in required_chain_ids
            )
        )
        if parkable:
            _value, position_key = parkable[0]
            position = _position(*position_key)
            parked = cells[position_key]
            request = ActionRequestV1(
                kind="drag",
                intent=f"stash-to-warehouse:{parked.item_type}",
                params={
                    "x1": policy.geometry.cell_center(*position_key)[0] if policy.geometry else 0,
                    "y1": policy.geometry.cell_center(*position_key)[1] if policy.geometry else 0,
                    "x2": warehouse_entry[0],
                    "y2": warehouse_entry[1],
                    "duration_ms": 400,
                },
            ) if policy.geometry is not None else None
            prediction = PredictionV1(
                prediction_id="p.1",
                kind="stash_to_warehouse",
                source_cell=position,
                source_before=parked.item_type,
                source_after=None,
            )
            action = PlannedActionV1(
                kind="drag",
                intent=f"stash-to-warehouse:{parked.item_type}",
                prediction=prediction,
                reason=(
                    "board is full with nothing to merge; park the lowest-value piece no "
                    "open order needs in the warehouse, where a tap brings it back"
                ),
                request=request,
                status=_request_status(request),
            )
            if request is not None:
                return PlanV1(
                    actions=[action],
                    predictions=[prediction],
                    blocked_actions=blocked,
                    visual_occupancy=policy.visual_occupancy,
                )
            blocked.append(action)

    if not actions:
        wait_seconds = min(policy.energy_recovery_seconds, 60)
        prediction = PredictionV1(prediction_id="p.1", kind="wait")
        actions.append(
            PlannedActionV1(
                kind="wait",
                intent="wait:resource_or_board_state",
                prediction=prediction,
                reason=_wait_reason(state, cells),
                request=ActionRequestV1(
                    kind="wait",
                    intent="wait:resource_or_board_state",
                    params={"ms": wait_seconds * 1000},
                ),
            )
        )

    return PlanV1(
        actions=actions,
        predictions=[action.prediction for action in actions],
        blocked_actions=blocked,
        visual_occupancy=policy.visual_occupancy,
    )


__all__ = [
    "ACTION_SPACE",
    "AUTOMATED_CHEST_TYPES",
    "ChestRuntimeStateV1",
    "PlanV1",
    "PlannedActionV1",
    "PolicyV1",
    "SUPPORTED_FREE_COLLECT_REWARD_TYPES",
    "SURVEYED_CHEST_TYPES",
    "PredictionV1",
    "VisualCellObservationV1",
    "VisualOccupancyCoverageV1",
    "configured_free_chest_types",
    "is_free_chest_type",
    "plan_batch",
]
