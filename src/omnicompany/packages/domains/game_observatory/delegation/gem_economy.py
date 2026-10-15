"""Gem spending, every option valued in energy.

2026-09-17, by the account owner: 钻石就换资源；有更高效的钻石转化就换；按同价值换算，
按体力换算即可.  So every thing gems can buy is priced as the energy it is worth,
and the loop spends gems on the best energy-per-gem offer that clears the bar.

What an offer is worth, in energy:

- an energy purchase: the 100 energy it grants (its gem price doubles through
  the day, 20 -> 40 -> 80, read from the save's ``shop.energyBuyCost``);
- a collectable piece: its ``CollectRewards`` (energy as is, coins divided by
  ``COINS_PER_ENERGY``);
- a free box such as 2901 or 3101: its stock times the weighted mean value of
  what it spreads;
- a piece a standing order needs: the pulls it saves -- the part of the order
  line it covers times what one unit of that line costs from the producers on
  the board.  When the order also needs something no producer can make, the
  order only closes by buying, so the piece is worth its share of the order's
  coins (in energy) less the energy the order's other lines still cost.

Smiley (好评) progress is not valued: its grant is a server-configured item mix
with no stable energy price, and the owner asked for energy terms only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

# Measured 2026-09-16 15:03-15:16Z: 92 coins earned for 124 energy spent.
COINS_PER_ENERGY = 0.74
ENERGY_PURCHASE_GAIN = 100
# The second energy purchase of a day (40 gems for 100 energy), which the owner
# approved, is the worst rate the loop will pay.
MIN_ENERGY_PER_GEM = 2.5
# A shop purchase never takes gems below the price of a day's first energy
# purchase, so that purchase stays affordable.
GEM_RESERVE = 20
SHOP_SURFACE = "shop-daily-specials-and-hot-selling"
_MAX_CHAIN = 32


@dataclass(frozen=True)
class GemOffer:
    kind: Literal["energy-purchase", "shop-item"]
    cost: int
    energy_value: float
    item_type: str | None = None
    shop_type: int | None = None
    slot_index: int | None = None
    shop_item_id: str | None = None
    reason: str = ""

    @property
    def energy_per_gem(self) -> float:
        return self.energy_value / self.cost if self.cost > 0 else float("inf")


@lru_cache(maxsize=4)
def _item_rows(config_dir: str) -> dict[str, dict[str, Any]]:
    payload = json.loads((Path(config_dir) / "ItemModelConfig.json").read_text(encoding="utf-8"))
    return {
        str(row.get("Type")): row
        for row in payload.get("rows", [])
        if isinstance(row, dict) and row.get("Type") is not None
    }


def _distance(rows: dict[str, dict[str, Any]], start: str | None, target: str) -> int | None:
    current, steps, seen = start, 0, set()
    while current is not None and current not in seen and steps <= _MAX_CHAIN:
        if current == target:
            return steps
        seen.add(current)
        merged = rows.get(current, {}).get("MergedType")
        current = str(merged) if merged is not None else None
        steps += 1
    return None


def _weights(row: dict[str, Any]) -> list[tuple[str, float]]:
    pairs: list[tuple[str, float]] = []
    for token in row.get("Spread_Weight") or []:
        item, _, weight = str(token).rpartition("-")
        try:
            pairs.append((item, float(weight)))
        except ValueError:
            continue
    return pairs


def _collect_value(row: dict[str, Any]) -> float | None:
    rewards = row.get("CollectRewards")
    if not isinstance(rewards, list) or not rewards:
        return None
    value = 0.0
    for token in rewards:
        kind, _, amount = str(token).rpartition("-")
        try:
            number = float(amount)
        except ValueError:
            continue
        if kind == "energy":
            value += number
        elif kind == "gold":
            value += number / COINS_PER_ENERGY
    return value


def _box_value(rows: dict[str, dict[str, Any]], row: dict[str, Any]) -> float | None:
    """A zero-cost spreader of collectables: stock times the mean piece value."""

    if row.get("Spread_CostEnergy") not in (0, None) or not row.get("Spread_Weight"):
        return None
    pairs = _weights(row)
    total = sum(weight for _, weight in pairs)
    if total <= 0:
        return None
    mean = 0.0
    for item, weight in pairs:
        value = _collect_value(rows.get(item, {}))
        if value is None:
            return None
        mean += value * weight / total
    stock = row.get("Spread_ItemMaxNumber")
    return mean * float(stock) if isinstance(stock, (int, float)) and stock > 0 else None


def _inventory(semantic: Any, raw_state: Any) -> list[str]:
    types: list[str] = []
    board = getattr(semantic, "board", None)
    for row in getattr(board, "cells", []) or []:
        for cell in row:
            if getattr(cell, "item_type", None) and getattr(cell, "kind", None) != "generator":
                types.append(str(cell.item_type))
    for item in getattr(raw_state, "cache_items", None) or []:
        code = item.get("codeStr", item.get("code")) if isinstance(item, dict) else None
        if code is not None:
            types.append(str(code))
    return types


def _unit_costs(rows: dict[str, dict[str, Any]], semantic: Any, required: set[str]) -> dict[str, float]:
    costs: dict[str, float] = {}
    board = getattr(semantic, "board", None)
    for row in getattr(board, "cells", []) or []:
        for cell in row:
            generator = getattr(cell, "generator", None)
            if getattr(cell, "kind", None) != "generator" or generator is None:
                continue
            pull = max(int(getattr(generator, "cost_energy", None) or 0), 1)
            for weight in getattr(generator, "spread_weights", []) or []:
                for target in required:
                    distance = _distance(rows, getattr(weight, "item_type", None), target)
                    if distance is not None:
                        cost = float(pull * (1 << min(distance, 16)))
                        costs[target] = min(costs.get(target, cost), cost)
    return costs


def order_line_value(item_type: str, semantic: Any, raw_state: Any, config_dir: str | Path) -> tuple[float, str]:
    """The energy a piece saves or unlocks against the standing orders."""

    rows = _item_rows(str(config_dir))
    orders = list(getattr(semantic, "orders", []) or [])
    required = {str(line.type) for order in orders for line in order.required_items}
    if not required:
        return 0.0, "no standing order"
    inventory = _inventory(semantic, raw_state)
    credit = {
        target: sum(
            2.0 ** -distance
            for held in inventory
            if (distance := _distance(rows, held, target)) is not None
        )
        for target in required
    }
    units = _unit_costs(rows, semantic, required)
    best, why = 0.0, "serves no standing order line"
    for order in orders:
        lines = [
            (str(line.type), max(float(line.count) - credit.get(str(line.type), 0.0), 0.0))
            for line in order.required_items
        ]
        if all(missing <= 0 for _, missing in lines):
            continue
        reachable_cost = sum(missing * units[target] for target, missing in lines if target in units)
        blocked = [(target, missing) for target, missing in lines if target not in units and missing > 0]
        for target, missing in lines:
            distance = _distance(rows, item_type, target)
            if distance is None or missing <= 0:
                continue
            provided = min(2.0 ** -distance, missing)
            if blocked:
                if target in units:
                    continue  # a reachable line of an order that cannot close
                share = provided / sum(amount for _, amount in blocked)
                coins = float(getattr(order, "reward_coins", None) or 0)
                value = max(coins / COINS_PER_ENERGY - reachable_cost, 0.0) * share
                reason = f"unlocks order slot {order.slot} ({target} is made by no producer)"
            else:
                value = provided * units[target]
                reason = f"saves {value:.0f} energy on order slot {order.slot} line {target}"
            if value > best:
                best, why = value, reason
    return best, why


def item_energy_value(item_type: str, semantic: Any, raw_state: Any, config_dir: str | Path) -> tuple[float, str]:
    rows = _item_rows(str(config_dir))
    row = rows.get(str(item_type), {})
    candidates: list[tuple[float, str]] = []
    collect = _collect_value(row)
    if collect is not None:
        candidates.append((collect, "collectable reward"))
    box = _box_value(rows, row)
    if box is not None:
        candidates.append((box, "free box of collectables"))
    candidates.append(order_line_value(str(item_type), semantic, raw_state, config_dir))
    return max(candidates, key=lambda pair: pair[0])


def shelf(raw_state: Any) -> list[dict[str, Any]]:
    """Shop rows with their display slot (id order within each shop type)."""

    rows = [dict(row) for row in (getattr(raw_state, "shop_items", None) or []) if isinstance(row, dict)]
    rows.sort(key=lambda row: (row.get("shopType") or 0, str(row.get("id") or "")))
    counters: dict[Any, int] = {}
    for row in rows:
        kind = row.get("shopType")
        row["slot_index"] = counters.get(kind, 0)
        counters[kind] = row["slot_index"] + 1
    return rows


def free_shop_offers(raw_state: Any) -> list[GemOffer]:
    return [
        GemOffer(
            kind="shop-item",
            cost=0,
            energy_value=0.0,
            item_type=str(row.get("itemCodeStr")),
            shop_type=row.get("shopType"),
            slot_index=row["slot_index"],
            shop_item_id=str(row.get("id")),
            reason="free shelf item",
        )
        for row in shelf(raw_state)
        if row.get("costType") == "gem" and (row.get("costCount") or 0) == 0 and (row.get("leftCount") or 0) > 0
    ]


def price_offers(
    semantic: Any,
    raw_state: Any,
    config_dir: str | Path,
    *,
    energy_purchases_today: int,
    energy_daily_limit: int,
) -> list[GemOffer]:
    """Every paid option available now, valued in energy, best rate first."""

    offers: list[GemOffer] = []
    price = (getattr(raw_state, "shop", None) or {}).get("energyBuyCost")
    if isinstance(price, int) and price > 0 and energy_purchases_today < energy_daily_limit:
        offers.append(
            GemOffer(kind="energy-purchase", cost=price, energy_value=float(ENERGY_PURCHASE_GAIN),
                     reason=f"+{ENERGY_PURCHASE_GAIN} energy")
        )
    for row in shelf(raw_state):
        cost = row.get("costCount") or 0
        if row.get("costType") != "gem" or cost <= 0 or (row.get("leftCount") or 0) <= 0:
            continue
        value, reason = item_energy_value(str(row.get("itemCodeStr")), semantic, raw_state, config_dir)
        if value <= 0:
            continue
        offers.append(
            GemOffer(kind="shop-item", cost=int(cost), energy_value=value,
                     item_type=str(row.get("itemCodeStr")), shop_type=row.get("shopType"),
                     slot_index=row["slot_index"], shop_item_id=str(row.get("id")), reason=reason)
        )
    offers.sort(key=lambda offer: (-offer.energy_per_gem, -offer.energy_value))
    return offers


def choose_offer(
    offers: list[GemOffer],
    gems: int | None,
    *,
    reserve: int = GEM_RESERVE,
    min_rate: float = MIN_ENERGY_PER_GEM,
) -> GemOffer | None:
    if gems is None:
        return None
    for offer in offers:
        if offer.cost <= 0 or offer.energy_per_gem < min_rate or offer.cost > gems:
            continue
        if offer.kind == "shop-item" and gems - offer.cost < reserve:
            continue
        return offer
    return None


__all__ = [
    "COINS_PER_ENERGY",
    "ENERGY_PURCHASE_GAIN",
    "GEM_RESERVE",
    "MIN_ENERGY_PER_GEM",
    "SHOP_SURFACE",
    "GemOffer",
    "choose_offer",
    "free_shop_offers",
    "item_energy_value",
    "order_line_value",
    "price_offers",
    "shelf",
]
