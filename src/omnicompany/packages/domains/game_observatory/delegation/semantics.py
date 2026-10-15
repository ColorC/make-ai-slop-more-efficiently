# [OMNI] origin=codex created_by=codex intent="translate Romantic Restaurant save codes into planner semantics"
"""Pure, config-backed semantics for Romantic Restaurant save states."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..design_extraction.config_facts import ConfigFacts, parse_rewards
from .save_reader import BoardCellV1, CoverageV1, GameStateV1


BOARD_ROWS = 9
BOARD_COLUMNS = 7

# Evidence: records/unregistered-observations.md, 2026-09-06 disposition.
LOCKED_SAND_PREFIX = "pb"
COBWEB_PREFIX = "c"
OBSERVED_PREFIX_EVIDENCE = "records/unregistered-observations.md:2026-09-06"
COBWEB_INFERENCE = (
    "inferred: cobwebbed items cannot be moved directly and require an adjacent merge"
)

# EnergyModel.lua:8-9 -- DefaultMaxEnergy / DefaultRecoveryDuration.  GetMaxEnergy
# and _GetRecoveryDuration return these constants unconditionally in 3.95.0.
ENERGY_MAX = 100
ENERGY_RECOVERY_SECONDS = 120
ENERGY_REFS = ["EnergyModel.lua:8-9,110-120,163-172,182-206"]

# F6-05 section 1.  ``ItemSpreadState`` is the enum persisted as the save column
# ``spreadState``; the numbers are the ones written by ItemManager, not an
# ordering we chose.  A cell whose item carries no ItemSpread component is
# filled with the sentinel -1 (ItemManager.lua:80-83), which is why -1 is absent
# from this map and read back as "no spread component".
SPREAD_STATE_CLOSED = 1
SPREAD_STATE_OPENING = 2
SPREAD_STATE_OPENED = 3
SPREAD_STATE_INITIALIZING = 4
SPREAD_STATE_NAMES: dict[int, str] = {
    SPREAD_STATE_CLOSED: "closed",
    SPREAD_STATE_OPENING: "opening",
    SPREAD_STATE_OPENED: "opened",
    SPREAD_STATE_INITIALIZING: "initializing",
}
# The sentinel ItemManager writes for every item that has no ItemSpread
# component, and the same sentinel the reader keeps verbatim.
SPREAD_ABSENT_SENTINEL = -1
# Line-anchored evidence for the table above and for the timer arithmetic.  Each
# entry is a file:line range in the decompiled 3.95.0 Lua.
SPREAD_STATE_REFS = [
    "ItemSpread.lua:8-13 ItemSpreadState = {Initializing=4, Closed=1, Opening=2, Opened=3}",
    "ItemSpread.lua:58-65 Init picks the initial state from Spread_InitDuration/Spread_OpenDuration",
    "ItemSpread.lua:113-139 _UpdateRecoveryState advances Initializing/Opening to Opened",
    "ItemSpread.lua:664-678 _TrySpread refuses on Closed, Opening, Initializing or itemRest==0",
    "ItemSpread.lua:1-6 SpreadFailedReason = {ItemClosed=1, ItemOpening=2, ItemRecharging=3, BoardFull=4}",
    "ItemManager.lua:78-97 _FillDBTable writes spreadState/spreadStartTimer/rest numbers",
    "ItemManager.lua:80-83 the -1 sentinel for items without an ItemSpread component",
    "ItemModelFactory.lua:12-20 CreateWithData restores the four columns verbatim",
    "DBTableConfigs.lua:18-21 the four columns are integers on the item table",
]
SPREAD_COOLDOWN_REFS = [
    "ItemSpread.lua:75-85 UpdatePerSecond arms m_startTimer with the server time",
    "ItemSpread.lua:113-139 _UpdateRecoveryState clears m_startTimer on refill, or advances it by Spread_ItemRecoveryDuration",
    "ItemSpread.lua:337-343 GetTimerAmount = (serverTime - m_startTimer) / GetTimerDuration()",
    "ItemSpread.lua:375-389 GetTimerDuration picks Init/Open/Storage/Item duration by state and storage rest",
    "ItemSpread.lua:186-210 OnTimeSkip shifts m_startTimer backwards by the skipped duration",
    "ItemSpread.lua:333-335 IsDisposable: a generator with no Spread_ItemRecoveryDuration never recovers",
    "ItemManager.lua:62-66 a nil m_startTimer is removed from the item row instead of being stored",
]

CellKind = Literal[
    "empty",
    "item",
    "item_cobwebbed",
    "locked_sand",
    "locked_sand_rewarded",
    "generator",
    "chest",
    "unknown",
]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CellPositionV1(_FrozenModel):
    row: int = Field(ge=1, le=BOARD_ROWS)
    col: int = Field(ge=1, le=BOARD_COLUMNS)


class SpreadWeightV1(_FrozenModel):
    item_type: str
    weight: int | None


class GeneratorSemanticV1(_FrozenModel):
    cost_energy: int | None
    spread_weights: list[SpreadWeightV1]
    item_rest: int | None
    storage_rest: int | None
    item_capacity: int | None
    storage_capacity: int | None
    recovery_duration: int | None
    storage_recovery_duration: int | None
    spread_state: int | None
    recovery_started_at: int | None
    inventory_exhausted: bool | None
    # ``min(Spread_ItemRecoveryDuration, Spread_StorageRecoveryDuration)``.  It
    # is a configuration constant and says nothing about this generator's
    # current timer; F4-10 mistook it for a remaining wait.  Kept for the
    # consumers that already read it; use ``active_recovery_duration_s`` for the
    # duration that is actually counting down.
    recovery_wait_seconds: int | None
    # F6-05 section 1.3, all derived, never a verdict.
    spread_state_name: str | None = None
    active_recovery_duration_s: int | None = None
    cooldown_ready_at_epoch: int | None = None
    cooldown_remaining_s: int | None = None


class CollectRewardV1(_FrozenModel):
    """One entry of ``ItemModelConfig.CollectRewards``, e.g. ``energy-15``.

    ``ItemCollectable:OnTap`` grants the whole list and removes the item, so a
    single tap on such a cell is a collection, never a merge source.
    """

    type: str
    amount: int


class PendingCollectRewardV1(_FrozenModel):
    """A visible collection whose reward type still lacks an executor."""

    position: CellPositionV1
    item_type: str
    reward_types: list[str]
    reason: str


class CellSemanticV1(_FrozenModel):
    row: int = Field(ge=1, le=BOARD_ROWS)
    col: int = Field(ge=1, le=BOARD_COLUMNS)
    raw_code: str | None
    kind: CellKind
    item_type: str | None
    chain_id: str | None
    level: int | None
    merge_target: str | None
    sell_price: int | None
    is_max_level: bool
    generator: GeneratorSemanticV1 | None
    clear_reward_energy: int | None
    interactable: bool
    config_refs: list[str]
    unknown_reason: str | None
    collect_rewards: list[CollectRewardV1] = []
    energy_gain: int | None = None


class MergeablePairV1(_FrozenModel):
    item_type: str
    first: CellPositionV1
    second: CellPositionV1
    merge_target: str | None
    sell_price: int | None


class BoardSemanticV1(_FrozenModel):
    cells: list[list[CellSemanticV1]]
    empty_cells: list[CellPositionV1]
    mergeable_pairs: list[MergeablePairV1]
    generators: list[CellSemanticV1]
    summary: dict[str, dict[str, int]]
    energy_items: list[CellPositionV1] = []
    # Every item with ItemCollectable:OnTap rewards, including rewards the
    # planner has not yet surveyed for execution (for example infinite/skip).
    collectible_items: list[CellPositionV1] = []
    pending_collect_rewards: list[PendingCollectRewardV1] = []
    sand_clear_energy_pending: int = 0
    sand_clear_reward_cells: list[CellPositionV1] = []


class RequiredItemV1(_FrozenModel):
    type: str
    count: int = Field(ge=1)


class EventBonusV1(_FrozenModel):
    """Event yield a single delivery in this slot pays out right now.

    ``好评如潮`` (the ``orderBoost`` activity) pays smileys per *slot*, not per
    order: ``OrderBoostModel:GetAddSmokeScoreBySlot`` returns the slot's
    ``reward_num`` until that slot has already been finished ``order_num``
    times in the current round, and then returns 0.
    """

    activity: str
    smileys: int = Field(ge=0)
    slot_deliveries_done: int = Field(ge=0)
    slot_deliveries_per_round: int = Field(ge=0)
    progress: int | None = None
    goal: int | None = None
    config_refs: list[str]


class OrderSemanticV1(_FrozenModel):
    slot: int = Field(ge=1)
    order_id: str
    order_type: int | None = None
    avatar_id: int | None = None
    required_items: list[RequiredItemV1]
    reward_coins: int | None
    reward_extra: list[dict[str, Any]]
    fulfillable_now: bool
    event_bonus: EventBonusV1 | None = None
    config_refs: list[str]


class RewardSemanticV1(_FrozenModel):
    type: str
    amount: int


class MainTaskSemanticV1(_FrozenModel):
    task_id: str
    name: str | None
    cost: int | None
    rewards: list[RewardSemanticV1]
    affordable: bool | None
    config_refs: list[str]


class ResourceSemanticV1(_FrozenModel):
    """Player resources plus the energy recovery clock.

    ``EnergyModel`` (decompiled Lua) hard-codes ``DefaultMaxEnergy = 100`` and
    ``DefaultRecoveryDuration = 120``: ``_RestoreEnergy`` adds
    ``(now - generateTime) // 120`` points and moves ``generateTime`` forward by
    the same multiple, so point ``n`` above the current value lands at
    ``generateTime + (n - energy) * 120``.
    """

    energy: int | None
    coins: int | None
    gems: int | None
    level: int | None
    xp: int | None
    energy_max: int = ENERGY_MAX
    energy_recovery_seconds: int = ENERGY_RECOVERY_SECONDS
    energy_generate_time: int | None = None
    energy_infinite_until: int | None = None
    energy_full_at: int | None = None
    config_refs: list[str] = []

    def energy_enough_at(self, needed: int) -> int | None:
        """Epoch second at which energy first reaches ``needed``, or None."""

        current = self.energy
        if current is None or current >= needed:
            return None
        if self.energy_generate_time is None or needed > self.energy_max:
            return None
        return self.energy_generate_time + (needed - current) * self.energy_recovery_seconds


class GameStateSemanticV1(_FrozenModel):
    resources: ResourceSemanticV1
    board: BoardSemanticV1
    orders: list[OrderSemanticV1]
    main_task: MainTaskSemanticV1 | None
    coverage: dict[str, CoverageV1]
    config_versions: dict[str, str]
    # F4-11 section 3, planner ruling of 2026-09-07.  The Activity table as the
    # save writes it, unwrapped one level.  Carried rather than interpreted:
    # the order semantics already read ``orderBoost`` out of it, and the
    # energy-party row ``triggerPlane`` is tracked from the reports.
    activity: dict[str, Any] | None = None
    # ItemCacheModel's ordered queue.  The rows stay raw so cacheItemId and
    # Type2/Type1 ordering are auditable without inventing an item meaning.
    cache_items: list[dict[str, Any]] | None = None
    cache_item_free_claim_supported: bool | None = None
    cache_item_pending_reason: str | None = None


@dataclass(frozen=True)
class _NormalizedState:
    energy: int | None
    energy_generate_time: int | None
    energy_infinite_time: int | None
    coins: int | None
    gems: int | None
    level: int | None
    xp: int | None
    board: list[list[BoardCellV1 | None]] | None
    orders: list[dict[str, Any]] | None
    main_task: dict[str, Any] | None
    activity: dict[str, Any] | None
    cache_items: list[dict[str, Any]] | None
    coverage: dict[str, CoverageV1]


@dataclass(frozen=True)
class _ItemDescriptor:
    row: dict[str, Any]
    chain_id: str | None
    level: int | None
    merge_target: str | None
    sell_price: int | None
    is_max_level: bool
    unknown_reason: str | None
    collect_rewards: tuple[CollectRewardV1, ...] = ()

    @property
    def energy_gain(self) -> int | None:
        total = sum(
            reward.amount for reward in self.collect_rewards if reward.type == "energy"
        )
        return total or None


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _segment(type_id: str) -> str:
    try:
        return str(int(type_id) // 100)
    except ValueError:
        prefix = re.split(r"[_:#-]", type_id, maxsplit=1)[0]
        return f"non-numeric:{prefix or 'unknown'}"


def _item_descriptors(facts: ConfigFacts) -> dict[str, _ItemDescriptor]:
    rows = {
        str(row["Type"]): dict(row)
        for row in facts.rows("ItemModelConfig")
        if isinstance(row, dict) and row.get("Type") is not None
    }
    predecessors: dict[str, list[str]] = defaultdict(list)
    cross_segment_targets: set[str] = set()
    for source, row in rows.items():
        target_value = row.get("MergedType")
        if target_value is None or target_value == "":
            continue
        target = str(target_value)
        if target in rows and _segment(source) == _segment(target):
            predecessors[target].append(source)
        elif target in rows:
            cross_segment_targets.add(source)

    result: dict[str, _ItemDescriptor] = {}
    for type_id, row in rows.items():
        path = [type_id]
        current = type_id
        problem: str | None = None
        while True:
            incoming = sorted(predecessors.get(current, []))
            if not incoming:
                break
            if len(incoming) > 1:
                problem = f"multiple chain predecessors for {current}: {', '.join(incoming)}"
                break
            current = incoming[0]
            if current in path:
                problem = f"merge chain cycle reaches {current}"
                break
            path.append(current)

        merged_value = row.get("MergedType")
        merge_target = None if merged_value is None or merged_value == "" else str(merged_value)
        if type_id in cross_segment_targets:
            problem = problem or f"merge target {merge_target} crosses a chain segment"
        elif merge_target is not None and merge_target not in rows:
            problem = problem or f"merge target {merge_target} is absent from ItemModelConfig"
        level = None if problem else len(path)
        result[type_id] = _ItemDescriptor(
            row=row,
            chain_id=None if problem else _segment(path[-1]),
            level=level,
            merge_target=merge_target,
            sell_price=_as_int(row.get("SellingPrice")),
            is_max_level=merge_target is None,
            unknown_reason=problem,
            collect_rewards=_collect_rewards(row.get("CollectRewards")),
        )
    return result


def _collect_rewards(values: Any) -> tuple[CollectRewardV1, ...]:
    """Parse ``CollectRewards`` entries shaped ``"<property>-<amount>"``.

    ``ConfigUtil.GetCurrencyFromArrStr`` splits on the last dash, so the
    property name itself may contain dashes; entries without an integer amount
    are dropped rather than guessed.
    """

    if not isinstance(values, list):
        return ()
    rewards: list[CollectRewardV1] = []
    for value in values:
        if not isinstance(value, str):
            continue
        property_type, separator, raw_amount = value.rpartition("-")
        amount = _as_int(raw_amount) if separator else None
        if not separator or not property_type or amount is None:
            continue
        rewards.append(CollectRewardV1(type=property_type, amount=amount))
    return tuple(rewards)


def _spread_weights(values: Any) -> list[SpreadWeightV1]:
    if not isinstance(values, list):
        return []
    result: list[SpreadWeightV1] = []
    for value in values:
        if not isinstance(value, str):
            continue
        item_type, separator, raw_weight = value.rpartition("-")
        result.append(
            SpreadWeightV1(
                item_type=item_type if separator else value,
                weight=_as_int(raw_weight) if separator else None,
            )
        )
    return result


def _active_recovery_duration(
    *,
    spread_state: int | None,
    storage_rest: int | None,
    init_duration: int | None,
    open_duration: int | None,
    recovery_duration: int | None,
    storage_recovery_duration: int | None,
) -> int | None:
    """Transcribe ``ItemSpread:GetTimerDuration`` (ItemSpread.lua:375-389).

    Which of the four configured durations is counting down depends on the
    persisted state and, for an opened generator, on whether the storage is
    empty.  ``recovery_wait_seconds`` takes a ``min`` over two of them and is
    therefore a constant, not the running duration.
    """

    if spread_state == SPREAD_STATE_INITIALIZING:
        return init_duration
    if spread_state == SPREAD_STATE_OPENING:
        return open_duration
    if spread_state == SPREAD_STATE_OPENED:
        if storage_rest == 0:
            return storage_recovery_duration
        return recovery_duration
    return None


def _generator_semantic(
    descriptor: _ItemDescriptor,
    extra: Mapping[str, Any],
    observed_at_epoch: int | None = None,
) -> GeneratorSemanticV1:
    row = descriptor.row
    item_rest = _as_int(extra.get("spreadItemRestNumber"))
    storage_rest = _as_int(extra.get("spreadStorageRestNumber"))
    recovery_duration = _as_int(row.get("Spread_ItemRecoveryDuration"))
    storage_recovery_duration = _as_int(
        row.get("Spread_StorageRecoveryDuration")
    )
    inventory_exhausted = (
        item_rest == 0 and storage_rest == 0
        if item_rest is not None and storage_rest is not None
        else None
    )
    recovery_durations = [
        value
        for value in (recovery_duration, storage_recovery_duration)
        if value is not None and value > 0
    ]
    spread_state = _as_int(extra.get("spreadState"))
    started_at = _as_int(extra.get("spreadStartTimer"))
    # ItemManager.lua:62-66 removes the column when the timer is nil, so a live
    # save carries either an absent key or the server epoch it was armed at.
    # ItemManager.lua:80-83 leaves -1 on items with no ItemSpread component.
    running_started_at = (
        None if started_at is None or started_at == SPREAD_ABSENT_SENTINEL else started_at
    )
    active_duration = _active_recovery_duration(
        spread_state=spread_state,
        storage_rest=storage_rest,
        init_duration=_as_int(row.get("Spread_InitDuration")),
        open_duration=_as_int(row.get("Spread_OpenDuration")),
        recovery_duration=recovery_duration,
        storage_recovery_duration=storage_recovery_duration,
    )
    ready_at = (
        running_started_at + active_duration
        if running_started_at is not None and active_duration is not None
        else None
    )
    if ready_at is None or observed_at_epoch is None:
        remaining = None
    else:
        # Clamp: a save written before the app was backgrounded can carry a
        # timer that expired long ago, and a negative wait is not a fact.
        remaining = max(0, ready_at - observed_at_epoch)
    return GeneratorSemanticV1(
        cost_energy=_as_int(row.get("Spread_CostEnergy")),
        spread_weights=_spread_weights(row.get("Spread_Weight")),
        item_rest=item_rest,
        storage_rest=storage_rest,
        item_capacity=_as_int(row.get("Spread_ItemMaxNumber")),
        storage_capacity=_as_int(row.get("Spread_StorageMaxNumber")),
        recovery_duration=recovery_duration,
        storage_recovery_duration=storage_recovery_duration,
        spread_state=spread_state,
        recovery_started_at=started_at,
        inventory_exhausted=inventory_exhausted,
        recovery_wait_seconds=min(recovery_durations) if recovery_durations else None,
        spread_state_name=(
            SPREAD_STATE_NAMES.get(spread_state) if spread_state is not None else None
        ),
        active_recovery_duration_s=active_duration,
        cooldown_ready_at_epoch=ready_at,
        cooldown_remaining_s=remaining,
    )


PAPERBOX_REWARD_TABLE = "MainPaperboxExtraRewardConfig"
# F4-07 open item 4.  The lightning number printed on a sand ("pb", paperbox)
# cell is what clearing it PAYS, not what clearing it costs.  Live proof, F4-06
# segment F round 9: the only action was clear-cobweb:merge:505->506, cell
# (4,7) went pb#c#702 -> c#702, energy went 97 -> 127 and a "claim reward 30"
# panel opened.  MainPaperboxExtraRewardConfig is a 9x7 grid parallel to
# BoardModelConfig and its [4][7] entry is exactly "energy-30".
PAPERBOX_REWARD_REFS = [
    f"{PAPERBOX_REWARD_TABLE}.row=4,col=7=energy-30",
    "records/long-run-2026-09-06.md:F4-06 segment F round 9",
]


def _paperbox_rewards(facts: ConfigFacts) -> dict[tuple[int, int], dict[str, Any]]:
    """Read the per-cell paperbox clear reward grid, keyed by (row, col).

    Rows that are not a 7-wide list, and cells holding the literal "0", carry no
    reward and are simply absent from the mapping.
    """

    if not facts.has_table(PAPERBOX_REWARD_TABLE):
        return {}
    rewards: dict[tuple[int, int], dict[str, Any]] = {}
    for row_index, row in enumerate(facts.rows(PAPERBOX_REWARD_TABLE), start=1):
        if not isinstance(row, list) or len(row) != BOARD_COLUMNS:
            continue
        for col_index, value in enumerate(row, start=1):
            if not isinstance(value, str) or value in {"", "0"}:
                continue
            parsed = parse_rewards(
                value,
                table=PAPERBOX_REWARD_TABLE,
                field=f"row{row_index}col{col_index}",
            )
            rewards[(row_index, col_index)] = {
                "raw": value,
                "items": parsed,
            }
    return rewards


def _clear_reward_energy(reward: Mapping[str, Any] | None) -> int | None:
    """The energy a paperbox clear pays, or None when it pays something else."""

    if reward is None:
        return None
    total = sum(
        int(item["amount"])
        for item in reward.get("items", [])
        if str(item.get("type")) == "energy"
    )
    return total or None


def _cell_semantic(
    cell: BoardCellV1 | None,
    *,
    row: int,
    col: int,
    energy: int | None,
    descriptors: Mapping[str, _ItemDescriptor],
    sand_reward: Mapping[str, Any] | None = None,
    observed_at_epoch: int | None = None,
) -> CellSemanticV1:
    raw_code = None if cell is None or cell.type is None else str(cell.type)
    if raw_code in {None, ""}:
        return CellSemanticV1(
            row=row,
            col=col,
            raw_code=raw_code,
            kind="empty",
            item_type=None,
            chain_id=None,
            level=None,
            merge_target=None,
            sell_price=None,
            is_max_level=False,
            generator=None,
            clear_reward_energy=None,
            interactable=False,
            config_refs=[],
            unknown_reason=None,
        )

    parts = raw_code.split("#")
    prefixes = parts[:-1]
    item_type = parts[-1]
    descriptor = descriptors.get(item_type)
    extra: Mapping[str, Any] = {} if cell is None else cell.extra
    config_refs = (
        [f"ItemModelConfig.Type={item_type}"] if descriptor is not None else []
    )
    if LOCKED_SAND_PREFIX in prefixes:
        clear_reward = _clear_reward_energy(sand_reward)
        config_refs.append(OBSERVED_PREFIX_EVIDENCE)
        if sand_reward is not None:
            config_refs.append(
                f"{PAPERBOX_REWARD_TABLE}.row={row},col={col}={sand_reward['raw']}"
            )
        return CellSemanticV1(
            row=row,
            col=col,
            raw_code=raw_code,
            kind="locked_sand_rewarded" if clear_reward is not None else "locked_sand",
            item_type=item_type,
            chain_id=None if descriptor is None else descriptor.chain_id,
            level=None if descriptor is None else descriptor.level,
            merge_target=None if descriptor is None else descriptor.merge_target,
            sell_price=None if descriptor is None else descriptor.sell_price,
            is_max_level=False if descriptor is None else descriptor.is_max_level,
            generator=None,
            clear_reward_energy=clear_reward,
            interactable=False,
            config_refs=config_refs,
            unknown_reason=(
                f"item type {item_type} is absent from ItemModelConfig"
                if descriptor is None
                else descriptor.unknown_reason
                or (
                    None
                    if sand_reward is not None
                    else "this paperbox cell pays no configured clear reward"
                )
            ),
        )

    if descriptor is None:
        return CellSemanticV1(
            row=row,
            col=col,
            raw_code=raw_code,
            kind="unknown",
            item_type=item_type,
            chain_id=None,
            level=None,
            merge_target=None,
            sell_price=None,
            is_max_level=False,
            generator=None,
            clear_reward_energy=None,
            interactable=False,
            config_refs=[],
            unknown_reason=f"item type {item_type} is absent from ItemModelConfig",
        )

    has_spread = any(key.startswith("Spread_") for key in descriptor.row)
    generator = (
        _generator_semantic(descriptor, extra, observed_at_epoch) if has_spread else None
    )
    if COBWEB_PREFIX in prefixes:
        kind: CellKind = "item_cobwebbed"
        interactable = False
        config_refs.append(OBSERVED_PREFIX_EVIDENCE)
        unknown_reason = COBWEB_INFERENCE
    elif (
        descriptor.row.get("Spread_OpenDuration") is not None
        or descriptor.row.get("Choices") is not None
    ):
        kind = "chest"
        interactable = False
        unknown_reason = descriptor.unknown_reason
    elif has_spread:
        kind = "generator"
        has_supply = not (
            generator is not None
            and generator.item_rest == 0
            and generator.storage_rest == 0
        )
        has_energy = (
            generator is None
            or generator.cost_energy is None
            or energy is None
            or energy >= generator.cost_energy
        )
        interactable = has_supply and has_energy
        unknown_reason = descriptor.unknown_reason
    else:
        kind = "item"
        interactable = True
        unknown_reason = descriptor.unknown_reason

    return CellSemanticV1(
        row=row,
        col=col,
        raw_code=raw_code,
        kind=kind,
        item_type=item_type,
        chain_id=descriptor.chain_id,
        level=descriptor.level,
        merge_target=descriptor.merge_target,
        sell_price=descriptor.sell_price,
        is_max_level=descriptor.is_max_level,
        generator=generator,
        clear_reward_energy=None,
        interactable=interactable,
        config_refs=config_refs,
        unknown_reason=unknown_reason,
        collect_rewards=list(descriptor.collect_rewards),
        energy_gain=descriptor.energy_gain,
    )


def _board_semantic(
    board: list[list[BoardCellV1 | None]] | None,
    *,
    energy: int | None,
    descriptors: Mapping[str, _ItemDescriptor],
    sand_rewards: Mapping[tuple[int, int], Mapping[str, Any]] | None = None,
    observed_at_epoch: int | None = None,
) -> BoardSemanticV1:
    if board is None:
        board = [[None for _col in range(BOARD_COLUMNS)] for _row in range(BOARD_ROWS)]
    if len(board) != BOARD_ROWS or any(len(row) != BOARD_COLUMNS for row in board):
        raise ValueError("board must contain exactly 9 rows and 7 columns")

    cells = [
        [
            _cell_semantic(
                cell,
                row=row_index,
                col=col_index,
                energy=energy,
                descriptors=descriptors,
                sand_reward=(
                    None
                    if sand_rewards is None
                    else sand_rewards.get((row_index, col_index))
                ),
                observed_at_epoch=observed_at_epoch,
            )
            for col_index, cell in enumerate(row, start=1)
        ]
        for row_index, row in enumerate(board, start=1)
    ]
    flat = [cell for row in cells for cell in row]
    empty_cells = [
        CellPositionV1(row=cell.row, col=cell.col)
        for cell in flat
        if cell.kind == "empty"
    ]

    mergeable: dict[str, list[CellSemanticV1]] = defaultdict(list)
    for cell in flat:
        if cell.kind == "item" and cell.item_type is not None:
            mergeable[cell.item_type].append(cell)
    pairs: list[MergeablePairV1] = []
    for item_type, candidates in mergeable.items():
        for first_index, first in enumerate(candidates):
            for second in candidates[first_index + 1 :]:
                pairs.append(
                    MergeablePairV1(
                        item_type=item_type,
                        first=CellPositionV1(row=first.row, col=first.col),
                        second=CellPositionV1(row=second.row, col=second.col),
                        merge_target=first.merge_target,
                        sell_price=first.sell_price,
                    )
                )
    pairs.sort(
        key=lambda pair: (
            pair.sell_price is None,
            pair.sell_price if pair.sell_price is not None else 0,
            pair.item_type,
            pair.first.row,
            pair.first.col,
            pair.second.row,
            pair.second.col,
        )
    )

    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for cell in flat:
        if cell.chain_id is not None and cell.level is not None:
            counts[cell.chain_id][str(cell.level)] += 1
    summary = {
        chain_id: dict(sorted(levels.items(), key=lambda item: int(item[0])))
        for chain_id, levels in sorted(counts.items())
    }
    return BoardSemanticV1(
        cells=cells,
        empty_cells=empty_cells,
        mergeable_pairs=pairs,
        generators=[
            cell
            for cell in flat
            if cell.kind == "generator" and cell.interactable
        ],
        summary=summary,
        energy_items=[
            CellPositionV1(row=cell.row, col=cell.col)
            for cell in flat
            if cell.kind == "item"
            and cell.energy_gain is not None
            and cell.energy_gain > 0
        ],
        collectible_items=[
            CellPositionV1(row=cell.row, col=cell.col)
            for cell in flat
            if cell.kind == "item" and cell.collect_rewards
        ],
        pending_collect_rewards=[
            PendingCollectRewardV1(
                position=CellPositionV1(row=cell.row, col=cell.col),
                item_type=str(cell.item_type),
                reward_types=sorted({reward.type for reward in cell.collect_rewards}),
                reason=(
                    "CollectRewards contains an unsupported type; requires a surveyed "
                    "free collection flow before execution"
                ),
            )
            for cell in flat
            if (
                cell.kind == "item"
                and cell.item_type is not None
                and cell.collect_rewards
                and any(
                    reward.type not in {"gold", "gem", "energy"}
                    for reward in cell.collect_rewards
                )
            )
        ],
        # F4-07 open item 4: clearing sand PAYS.  The board carries this much
        # energy that is still recoverable by clearing paperbox cells, so it is
        # a benefit the planner and the exhaustion detector may count, never a
        # price to be paid.
        sand_clear_energy_pending=sum(
            cell.clear_reward_energy or 0
            for cell in flat
            if cell.kind == "locked_sand_rewarded"
        ),
        sand_clear_reward_cells=[
            CellPositionV1(row=cell.row, col=cell.col)
            for cell in flat
            if cell.kind == "locked_sand_rewarded"
        ],
    )


def _requirements(order: Mapping[str, Any]) -> list[str]:
    explicit = order.get("requirements", order.get("required_items"))
    if isinstance(explicit, list):
        expanded: list[str] = []
        for item in explicit:
            if isinstance(item, Mapping) and (
                item.get("item_type") is not None or item.get("type") is not None
            ):
                count = max(_as_int(item.get("count")) or 1, 1)
                item_type = item.get("item_type", item.get("type"))
                expanded.extend([str(item_type)] * count)
            elif item is not None and item != "":
                expanded.append(str(item))
        return expanded
    result = []
    for index in range(1, 4):
        value = order.get(f"requirement{index}Str")
        if value in {None, ""}:
            value = order.get(f"requirement{index}")
        if value not in {None, "", 0, "0"}:
            result.append(str(value))
    return result


ORDER_BOOST_ACTIVITY = "orderBoost"
TIMED_ORDER_SLOT = 13
CONTIORDER_SLOT = 18
ORDER_BOOST_CONFIG_REFS = [
    "OrderBoostModel.lua:470-498 GetAddSmokeScoreBySlot",
    "MainOrderDefinition.lua:3-21 MainOrderSlotDefinition",
    "save activity.orderBoost CurConfigs/CurInfo",
]


def _order_boost_bonus(
    activity: Mapping[str, Any] | None,
    slot: int,
) -> EventBonusV1 | None:
    """Smileys one more delivery in ``slot`` pays out, or None when unknowable.

    Mirrors ``OrderBoostModel:GetAddSmokeScoreBySlot``.  The chained-order slot
    reads its yield from another activity model that the save does not expose,
    so it stays unknown rather than guessed.
    """

    if not isinstance(activity, Mapping):
        return None
    payload = activity.get(ORDER_BOOST_ACTIVITY)
    if not isinstance(payload, Mapping):
        return None
    cur_info = payload.get("CurInfo")
    if not isinstance(cur_info, Mapping):
        return None
    progress = _as_int(cur_info.get("curScore"))
    goal = _as_int(cur_info.get("MaxScore"))
    raw_finished = cur_info.get("orderFinishInfo")
    finished: dict[str, int] = {}
    if isinstance(raw_finished, Mapping):
        finished = {
            str(key): _as_int(value) or 0 for key, value in raw_finished.items()
        }
    if slot == CONTIORDER_SLOT:
        return None
    if slot == TIMED_ORDER_SLOT:
        return EventBonusV1(
            activity=ORDER_BOOST_ACTIVITY,
            smileys=2,
            slot_deliveries_done=0,
            slot_deliveries_per_round=0,
            progress=progress,
            goal=goal,
            config_refs=ORDER_BOOST_CONFIG_REFS,
        )
    configs = payload.get("CurConfigs")
    if not isinstance(configs, list):
        return None
    entry = next(
        (
            item
            for item in configs
            if isinstance(item, Mapping) and _as_int(item.get("order_slot")) == slot
        ),
        None,
    )
    if entry is None:
        return None
    per_round = _as_int(entry.get("order_num")) or 0
    reward = _as_int(entry.get("reward_num")) or 0
    done = finished.get(str(slot), 0)
    return EventBonusV1(
        activity=ORDER_BOOST_ACTIVITY,
        smileys=0 if done >= per_round else reward,
        slot_deliveries_done=done,
        slot_deliveries_per_round=per_round,
        progress=progress,
        goal=goal,
        config_refs=ORDER_BOOST_CONFIG_REFS,
    )


# MainOrderDefinition.lua:34 MainOrderType.CharacterIntimacyRandom.  Orders of
# this type come from Gerald (CharacterIntimacyModel.OrderAvatarId = 601), are
# only created while the community is open (MainOrderCreatorRandom.lua:790-803),
# and while it is open pay no coins: MainOrder:_HasGoldReward is false for them
# (MainOrder.lua:102-104).  Instead FinishOrder grants 2-6 community gifts of one
# random type, the count read from CharacterIntimacyCoinToGiftConfig by the
# order's price (MainOrderModel.lua:753-766, BaseSceneBoardModel.lua:320-328).
CHARACTER_INTIMACY_ORDER_TYPE = 12


def _order_semantics(
    orders: list[dict[str, Any]] | None,
    *,
    board: BoardSemanticV1,
    facts: ConfigFacts,
    activity: Mapping[str, Any] | None = None,
) -> tuple[list[OrderSemanticV1], list[str]]:
    if orders is None:
        return [], ["orders are absent from the decoded save"]
    price_by_type = {
        str(row["Code"]): _as_int(row.get("Price"))
        for row in facts.rows("OrderItemConfig")
        if isinstance(row, dict) and row.get("Code") is not None
    }
    available = Counter(
        cell.item_type
        for row in board.cells
        for cell in row
        if cell.kind == "item" and cell.item_type is not None
    )
    result: list[OrderSemanticV1] = []
    problems: list[str] = []
    for ordinal, order in enumerate(orders, start=1):
        raw_requirements = _requirements(order)
        required_counts = Counter(raw_requirements)
        required_items = [
            RequiredItemV1(type=item_type, count=count)
            for item_type, count in sorted(required_counts.items())
        ]
        prices = [price_by_type.get(item_type) for item_type in raw_requirements]
        missing = sorted(
            {item_type for item_type, price in zip(raw_requirements, prices) if price is None}
        )
        if missing:
            problems.append(
                f"order {ordinal} lacks OrderItemConfig for: {', '.join(missing)}"
            )
        raw_extra = order.get("reward_extra", [])
        if isinstance(raw_extra, Mapping):
            reward_extra = [dict(raw_extra)]
        elif isinstance(raw_extra, list):
            reward_extra = [dict(item) for item in raw_extra if isinstance(item, Mapping)]
        else:
            reward_extra = []
        order_type = _as_int(order.get("type", order.get("order_type")))
        price = (
            None
            if any(price is None for price in prices)
            else sum(price for price in prices if price is not None)
        )
        reward_coins = price
        if order_type == CHARACTER_INTIMACY_ORDER_TYPE:
            # 2026-09-17: read as a 24-coin order, Gerald's 507 would have
            # been built and delivered for coins it never pays, and the
            # delivery verdict would have expected +24.
            reward_coins = 0
            reward_extra.append(
                {
                    "type": "character_intimacy_gift",
                    "count_range": _gift_count_range(facts, price),
                    "price": price,
                    "config_refs": [
                        "MainOrder.lua:102-104 _HasGoldReward",
                        "MainOrderModel.lua:753-766 _AddGiftRewardInfo",
                        "CharacterIntimacyCoinToGiftConfig",
                    ],
                }
            )
        result.append(
            OrderSemanticV1(
                slot=max(_as_int(order.get("slot")) or ordinal, 1),
                order_id=str(
                    order.get("order_id", order.get("id", order.get("slot", ordinal)))
                ),
                order_type=order_type,
                avatar_id=_as_int(order.get("avatar_id", order.get("avatarId"))),
                required_items=required_items,
                reward_coins=reward_coins,
                reward_extra=reward_extra,
                fulfillable_now=all(
                    available[item.type] >= item.count for item in required_items
                ),
                event_bonus=_order_boost_bonus(
                    activity,
                    max(_as_int(order.get("slot")) or ordinal, 1),
                ),
                config_refs=[
                    f"OrderItemConfig.Code={item_type}"
                    for item_type in sorted(required_counts)
                    if item_type in price_by_type
                ],
            )
        )
    return result, problems


def _gift_count_range(facts: ConfigFacts, price: int | None) -> list[int] | None:
    """CharacterIntimacyModel:GetCoinToGiftNum's candidates for this price."""

    if price is None:
        return None
    for row in facts.rows("CharacterIntimacyCoinToGiftConfig"):
        if not isinstance(row, dict):
            continue
        low = _as_int(row.get("MinCoin"))
        high = _as_int(row.get("MaxCoin"))
        if low is not None and price >= low and (high is None or price <= high):
            numbers = row.get("NumberRange")
            return [int(value) for value in numbers] if isinstance(numbers, list) else None
    return None


def _task_identity(task: Mapping[str, Any]) -> tuple[str | None, int | None, int | None]:
    raw = task.get("task_id", task.get("curId"))
    if raw is None and task.get("day") is not None and task.get("id") is not None:
        raw = f"{task['day']}_{task['id']}"
    if raw is None:
        current_cost = task.get("curCost")
        if isinstance(current_cost, str):
            raw = current_cost.split("##", maxsplit=1)[0]
    if raw is None:
        return None, None, None
    task_id = str(raw)
    match = re.fullmatch(r"(\d+)_(\d+)", task_id)
    if match is None:
        return task_id, _as_int(task.get("day")), _as_int(task.get("id"))
    return task_id, int(match.group(1)), int(match.group(2))


def _main_task_semantic(
    task: dict[str, Any] | None,
    *,
    coins: int | None,
    facts: ConfigFacts,
) -> tuple[MainTaskSemanticV1 | None, str | None]:
    if task is None:
        return None, "main task is absent from the decoded save"
    task_id, day, item_id = _task_identity(task)
    if task_id is None or day is None or item_id is None:
        return None, "main task identity is unavailable or malformed"
    config = next(
        (
            row
            for row in facts.rows("MainTaskConfig")
            if isinstance(row, dict)
            and _as_int(row.get("day")) == day
            and _as_int(row.get("id")) == item_id
        ),
        None,
    )
    if config is None:
        return None, f"MainTaskConfig has no day={day}, id={item_id} row"
    cost = _as_int(config.get("cost"))
    rewards = [
        RewardSemanticV1(type=str(item["type"]), amount=int(item["amount"]))
        for item in parse_rewards(
            config.get("rewards"), table="MainTaskConfig", field="rewards"
        )
    ]
    return (
        MainTaskSemanticV1(
            task_id=task_id,
            name=(None if config.get("name") is None else str(config["name"])),
            cost=cost,
            rewards=rewards,
            affordable=None if coins is None or cost is None else coins >= cost,
            config_refs=[f"MainTaskConfig.day={day},id={item_id}"],
        ),
        None,
    )


def _coverage_value(value: Any) -> CoverageV1:
    return value if isinstance(value, CoverageV1) else CoverageV1.model_validate(value)


def _normalize_state(value: GameStateV1 | Mapping[str, Any]) -> _NormalizedState:
    if isinstance(value, GameStateV1):
        return _NormalizedState(
            energy=value.energy,
            energy_generate_time=value.energy_generate_time,
            energy_infinite_time=value.energy_infinite_time,
            coins=value.coins,
            gems=value.gems,
            level=value.level,
            xp=value.xp,
            board=value.board,
            orders=(
                [
                    item.model_dump(mode="python")
                    if isinstance(item, BaseModel)
                    else dict(item)
                    for item in value.orders
                ]
                if value.orders is not None
                else None
            ),
            main_task=value.main_task,
            activity=value.activity,
            cache_items=(
                [dict(item) for item in value.cache_items if isinstance(item, Mapping)]
                if value.cache_items is not None
                else None
            ),
            coverage=dict(value.coverage),
        )
    if "state" in value and isinstance(value["state"], Mapping):
        return _normalize_state(GameStateV1.model_validate(value["state"]))
    if "board_matrix_7x9" not in value:
        return _normalize_state(GameStateV1.model_validate(value))

    raw_board = value.get("board_matrix_7x9")
    if not isinstance(raw_board, list):
        raise ValueError("round JSON board_matrix_7x9 must be a list")
    runtime_by_position: dict[tuple[int, int], dict[str, Any]] = {}
    raw_runtime = value.get("generator_runtime", [])
    if isinstance(raw_runtime, list):
        for item in raw_runtime:
            if not isinstance(item, Mapping):
                continue
            row = _as_int(item.get("row"))
            col = _as_int(item.get("col"))
            if row is not None and col is not None:
                runtime_by_position[(row, col)] = {
                    key: item.get(key)
                    for key in (
                        "spreadItemRestNumber",
                        "spreadStorageRestNumber",
                        "spreadState",
                        "spreadStartTimer",
                    )
                    if key in item
                }
    board = [
        [
            None
            if raw is None
            else BoardCellV1(
                type=raw,
                extra=runtime_by_position.get((row_index, col_index), {}),
            )
            for col_index, raw in enumerate(row, start=1)
        ]
        for row_index, row in enumerate(raw_board, start=1)
        if isinstance(row, list)
    ]
    raw_coverage = value.get("coverage", {})
    coverage = (
        {
            str(key): _coverage_value(item)
            for key, item in raw_coverage.items()
        }
        if isinstance(raw_coverage, Mapping)
        else {}
    )
    coverage.setdefault(
        "board",
        CoverageV1(
            decoded=True,
            source_refs=["F1-04 live_acceptance.round.board_matrix_7x9"],
        ),
    )
    return _NormalizedState(
        energy=_as_int(value.get("energy")),
        energy_generate_time=_as_int(value.get("energy_generate_time")),
        energy_infinite_time=_as_int(value.get("energy_infinite_time")),
        coins=_as_int(value.get("coins")),
        gems=_as_int(value.get("gems")),
        level=_as_int(value.get("level")),
        xp=_as_int(value.get("xp")),
        board=board,
        orders=(
            [dict(item) for item in value["orders"] if isinstance(item, Mapping)]
            if isinstance(value.get("orders"), list)
            else None
        ),
        main_task=(
            dict(value["main_task"])
            if isinstance(value.get("main_task"), Mapping)
            else None
        ),
        activity=(
            dict(value["activity"])
            if isinstance(value.get("activity"), Mapping)
            else None
        ),
        cache_items=(
            [dict(item) for item in value["cache_items"] if isinstance(item, Mapping)]
            if isinstance(value.get("cache_items"), list)
            else None
        ),
        coverage=coverage,
    )


def _config_versions(facts: ConfigFacts) -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in (
        "ItemModelConfig",
        "BoardModelConfig",
        "OrderItemConfig",
        "MainTaskConfig",
        PAPERBOX_REWARD_TABLE,
    ):
        entry = facts.manifest_by_table.get(name)
        if entry is not None and isinstance(entry.get("sha256"), str):
            versions[name] = str(entry["sha256"])
    return versions


def derive_game_state_semantics(
    state: GameStateV1 | Mapping[str, Any],
    config_dir: str | Path,
    observed_at_epoch: int | None = None,
) -> GameStateSemanticV1:
    """Translate one save state or F1-04 round mapping using config files only.

    ``observed_at_epoch`` is the wall-clock second the save was read.  The save
    stores when a generator's recovery timer was armed, not how much of it is
    left, so a remaining wait can only be produced against a reading time.
    Callers that do not have one leave it out and get ``cooldown_remaining_s``
    as ``None``; every other field is unaffected.
    """

    normalized = _normalize_state(state)
    facts = ConfigFacts.load(config_dir)
    descriptors = _item_descriptors(facts)
    board = _board_semantic(
        normalized.board,
        energy=normalized.energy,
        descriptors=descriptors,
        sand_rewards=_paperbox_rewards(facts),
        observed_at_epoch=observed_at_epoch,
    )
    orders, order_problems = _order_semantics(
        normalized.orders,
        board=board,
        facts=facts,
        activity=normalized.activity,
    )
    main_task, task_problem = _main_task_semantic(
        normalized.main_task,
        coins=normalized.coins,
        facts=facts,
    )

    coverage = dict(normalized.coverage)
    unknown_cells = [
        cell for row in board.cells for cell in row if cell.kind == "unknown"
    ]
    coverage["semantics.items"] = CoverageV1(
        decoded=not unknown_cells,
        reason=(
            None
            if not unknown_cells
            else f"{len(unknown_cells)} board cells have unknown item semantics"
        ),
        source_refs=["ItemModelConfig.json", OBSERVED_PREFIX_EVIDENCE],
    )
    # F4-07 open item 4: coverage is now about the *reward* a paperbox cell
    # pays when it is cleared.  A cell absent from MainPaperboxExtraRewardConfig
    # genuinely pays nothing, so it is decoded, not missing.
    sand_cells = [
        cell
        for row in board.cells
        for cell in row
        if cell.kind in {"locked_sand", "locked_sand_rewarded"}
    ]
    coverage["semantics.sand_clear_rewards"] = CoverageV1(
        decoded=facts.has_table(PAPERBOX_REWARD_TABLE),
        reason=(
            None
            if facts.has_table(PAPERBOX_REWARD_TABLE)
            else (
                f"{PAPERBOX_REWARD_TABLE} is absent, so the clear reward of "
                f"{len(sand_cells)} paperbox cells is unknown"
            )
        ),
        source_refs=[
            f"{PAPERBOX_REWARD_TABLE}.json",
            "BoardModelConfig.json",
            OBSERVED_PREFIX_EVIDENCE,
            *PAPERBOX_REWARD_REFS,
        ],
    )
    coverage["semantics.orders"] = CoverageV1(
        decoded=normalized.orders is not None and not order_problems,
        reason="; ".join(order_problems) or None,
        source_refs=["OrderItemConfig.json"],
    )
    coverage["semantics.main_task"] = CoverageV1(
        decoded=main_task is not None,
        reason=task_problem,
        source_refs=["MainTaskConfig.json"],
    )
    cache_refs = [
        "DBTableConfigs.lua:66-87",
        "MainBoardModel.lua:10,828-835",
        "ItemCacheModel.lua:1-4,21-23,65-70,91-111",
        "BaseSceneBoardModel.lua:201-234",
    ]
    cache_items = normalized.cache_items
    cache_pending_reason = None
    if cache_items is None:
        cache_pending_reason = (
            "CacheItem table was not decoded; free queue extraction is held "
            "until an authoritative save row is available"
        )
    coverage["semantics.cache_items"] = CoverageV1(
        decoded=cache_items is not None,
        reason=cache_pending_reason,
        source_refs=cache_refs,
    )
    versions = _config_versions(facts)
    coverage["semantics.config_versions"] = CoverageV1(
        decoded=len(versions) == 4,
        reason=(
            None
            if len(versions) == 4
            else "one or more required config manifest hashes are absent"
        ),
        source_refs=["_manifest.json"],
    )
    return GameStateSemanticV1(
        activity=normalized.activity,
        resources=ResourceSemanticV1(
            energy=normalized.energy,
            coins=normalized.coins,
            gems=normalized.gems,
            level=normalized.level,
            xp=normalized.xp,
            energy_generate_time=normalized.energy_generate_time,
            energy_infinite_until=normalized.energy_infinite_time,
            energy_full_at=(
                None
                if normalized.energy is None
                or normalized.energy_generate_time is None
                or normalized.energy >= ENERGY_MAX
                else normalized.energy_generate_time
                + (ENERGY_MAX - normalized.energy) * ENERGY_RECOVERY_SECONDS
            ),
            config_refs=list(ENERGY_REFS),
        ),
        board=board,
        orders=orders,
        main_task=main_task,
        coverage=coverage,
        config_versions=versions,
        cache_items=cache_items,
        cache_item_free_claim_supported=(True if cache_items is not None else None),
        cache_item_pending_reason=cache_pending_reason,
    )


__all__ = [
    "BOARD_COLUMNS",
    "BOARD_ROWS",
    "SPREAD_ABSENT_SENTINEL",
    "SPREAD_COOLDOWN_REFS",
    "SPREAD_STATE_CLOSED",
    "SPREAD_STATE_INITIALIZING",
    "SPREAD_STATE_NAMES",
    "SPREAD_STATE_OPENED",
    "SPREAD_STATE_OPENING",
    "SPREAD_STATE_REFS",
    "BoardSemanticV1",
    "CellSemanticV1",
    "CollectRewardV1",
    "GameStateSemanticV1",
    "GeneratorSemanticV1",
    "MainTaskSemanticV1",
    "MergeablePairV1",
    "OrderSemanticV1",
    "PendingCollectRewardV1",
    "derive_game_state_semantics",
]
