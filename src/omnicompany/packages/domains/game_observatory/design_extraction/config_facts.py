"""Extract configuration-backed facts for the Romantic Restaurant design notes.

The module deliberately knows nothing about device captures or observed gameplay.  Its
only input is the normalized JSON configuration directory produced by the Lua parser.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


REWARD_PATTERN = re.compile(r"^(.+)-([0-9]+)$")

# Unpacked Addressables products live beside the normalized tables, written by
# ``config_import.addressables``.  They are optional: a config directory built
# from the decompiled Lua alone simply has no text to substitute.
LOCALIZATION_DIR = "localization"
STORY_DIR = "story"
PRIMARY_LANGUAGE = "cn"


@dataclass(frozen=True)
class ConfigTable:
    """One normalized configuration table."""

    name: str
    fields: tuple[str, ...]
    rows: tuple[Any, ...]
    path: Path


class ConfigFacts:
    """Lazy, named access to normalized config tables and their manifest."""

    def __init__(self, config_dir: Path, manifest: list[dict[str, Any]]) -> None:
        self.config_dir = config_dir
        self.manifest = manifest
        self._tables: dict[str, ConfigTable] = {}
        self._localization: dict[str, dict[str, str]] = {}
        self._side_products: dict[str, dict[str, Any]] = {}

    @classmethod
    def load(cls, config_dir: str | Path) -> ConfigFacts:
        root = Path(config_dir)
        manifest_path = root / "_manifest.json"
        raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(raw_manifest, list):
            raise ValueError(f"manifest must be a list: {manifest_path}")
        manifest = [entry for entry in raw_manifest if isinstance(entry, dict)]
        return cls(root, manifest)

    @property
    def manifest_by_table(self) -> dict[str, dict[str, Any]]:
        return {
            str(entry["table"]): entry
            for entry in self.manifest
            if isinstance(entry.get("table"), str)
        }

    def has_table(self, name: str) -> bool:
        return (self.config_dir / f"{name}.json").is_file()

    def table(self, name: str) -> ConfigTable:
        cached = self._tables.get(name)
        if cached is not None:
            return cached

        path = self.config_dir / f"{name}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"table payload must be an object: {path}")
        fields = payload.get("fields", [])
        rows = payload.get("rows", [])
        if not isinstance(fields, list):
            raise ValueError(f"table fields must be a list: {path}")
        normalized_rows = rows if isinstance(rows, list) else [rows]

        table = ConfigTable(
            name=name,
            fields=tuple(str(field) for field in fields),
            rows=tuple(normalized_rows),
            path=path,
        )
        self._tables[name] = table
        return table

    def rows(self, name: str) -> tuple[Any, ...]:
        return self.table(name).rows

    def fields(self, name: str) -> tuple[str, ...]:
        return self.table(name).fields

    def _side_product(self, *parts: str) -> dict[str, Any]:
        """Read one optional JSON product written beside the tables."""

        key = "/".join(parts)
        cached = self._side_products.get(key)
        if cached is not None:
            return cached
        path = self.config_dir.joinpath(*parts)
        payload: dict[str, Any] = {}
        if path.is_file():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        self._side_products[key] = payload
        return payload

    def localization(self, language: str = PRIMARY_LANGUAGE) -> dict[str, str]:
        """Return one unpacked localization table, or an empty one when absent."""

        cached = self._localization.get(language)
        if cached is not None:
            return cached
        path = self.config_dir / LOCALIZATION_DIR / f"game_text_{language}.json"
        table: dict[str, str] = {}
        if path.is_file():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                table = {str(key): str(value) for key, value in loaded.items()}
        self._localization[language] = table
        return table

    def localization_index(self) -> dict[str, Any]:
        return self._side_product(LOCALIZATION_DIR, "_index.json")

    def packaged_story(self) -> dict[str, Any]:
        return self._side_product(STORY_DIR, "_manifest.json")


def _sort_key(value: Any) -> tuple[int, int | str]:
    text = str(value)
    try:
        return (0, int(text))
    except ValueError:
        return (1, text)


def _segment_for_type(value: Any) -> str:
    """Return a stable hundred-wide segment for numeric type identifiers."""

    text = str(value)
    try:
        number = int(text)
    except ValueError:
        prefix = re.split(r"[_:#-]", text, maxsplit=1)[0] or "unknown"
        return f"non-numeric:{prefix}"
    start = (number // 100) * 100
    return f"{start:04d}-{start + 99:04d}"


def _warning(table: str, field: str, value: Any, message: str) -> dict[str, Any]:
    return {"table": table, "field": field, "value": value, "message": message}


def parse_rewards(
    values: Any,
    *,
    warnings: list[dict[str, Any]] | None = None,
    table: str = "unknown",
    field: str = "rewards",
) -> list[dict[str, Any]]:
    """Parse ``type-amount`` reward strings and report malformed entries."""

    warning_sink = warnings if warnings is not None else []
    if values is None:
        return []
    candidates: Iterable[Any] = values if isinstance(values, list) else [values]
    parsed: list[dict[str, Any]] = []
    for value in candidates:
        match = REWARD_PATTERN.fullmatch(value) if isinstance(value, str) else None
        if match is None:
            warning_sink.append(
                _warning(table, field, value, "expected reward in <type>-<amount> format")
            )
            continue
        parsed.append({"type": match.group(1), "amount": int(match.group(2))})
    return parsed


def extract_economy_item_chains(facts: ConfigFacts) -> dict[str, Any]:
    """Build merge chains and preserve all generator parameters."""

    table = facts.table("ItemModelConfig")
    rows = [row for row in table.rows if isinstance(row, dict) and row.get("Type") is not None]
    by_type = {str(row["Type"]): row for row in rows}
    spread_fields = [field for field in table.fields if field.startswith("Spread_")]
    grouped: dict[str, list[str]] = defaultdict(list)
    for type_id in by_type:
        grouped[_segment_for_type(type_id)].append(type_id)

    chains: list[dict[str, Any]] = []
    assigned: set[str] = set()
    for segment in sorted(grouped):
        members = set(grouped[segment])
        incoming = {
            str(row.get("MergedType"))
            for row in rows
            if str(row.get("Type")) in members and str(row.get("MergedType")) in members
        }
        roots = sorted(members - incoming, key=_sort_key)
        starts = roots + [item for item in sorted(members, key=_sort_key) if item not in roots]
        for start in starts:
            if start in assigned:
                continue
            sequence: list[str] = []
            levels: list[dict[str, Any]] = []
            local_seen: set[str] = set()
            current = start
            terminated_by = "terminal"
            missing_target: str | None = None
            while current in members and current not in local_seen:
                row = by_type[current]
                local_seen.add(current)
                assigned.add(current)
                sequence.append(current)
                generator = any(field in row for field in spread_fields)
                levels.append(
                    {
                        "type": current,
                        "merged_type": (
                            str(row["MergedType"]) if row.get("MergedType") is not None else None
                        ),
                        "selling_price": row.get("SellingPrice"),
                        "rare": row.get("Rare"),
                        "bubble_chance": row.get("BubbleChance"),
                        "is_generator": generator,
                        "generator_parameters": (
                            {field: row.get(field) for field in spread_fields}
                            if generator
                            else None
                        ),
                    }
                )
                merged = row.get("MergedType")
                if merged is None or str(merged) == "":
                    break
                target = str(merged)
                if target not in by_type:
                    terminated_by = "missing_target"
                    missing_target = target
                    break
                if target not in members:
                    terminated_by = "segment_boundary"
                    missing_target = target
                    break
                if target in local_seen:
                    terminated_by = "cycle"
                    missing_target = target
                    break
                current = target

            chain_id = f"{segment}:{start}"
            chains.append(
                {
                    "chain_id": chain_id,
                    "segment": segment,
                    "sequence": sequence,
                    "levels": levels,
                    "terminated_by": terminated_by,
                    "next_type": missing_target,
                    "has_generator": any(level["is_generator"] for level in levels),
                }
            )

    chains.sort(key=lambda chain: (chain["segment"], _sort_key(chain["sequence"][0])))
    generator_chains = [chain["chain_id"] for chain in chains if chain["has_generator"]]
    return {
        "source_tables": ["ItemModelConfig"],
        "spread_fields": spread_fields,
        "chains": chains,
        "summary": {
            "chain_count": len(chains),
            "longest_chain": max((len(chain["sequence"]) for chain in chains), default=0),
            "generator_chain_count": len(generator_chains),
            "generator_chains": generator_chains,
        },
        "warnings": [],
    }


def extract_economy_order_pricing(facts: ConfigFacts) -> dict[str, Any]:
    """Compare order price tables and relate base prices to item selling prices."""

    table_names = [
        name
        for name in (
            "OrderItemConfig",
            "OrderItemConfig_chesthard",
            "OrderItemConfig_contiorder",
        )
        if facts.has_table(name)
    ]
    item_rows = facts.rows("ItemModelConfig") if facts.has_table("ItemModelConfig") else ()
    selling_prices = {
        str(row["Type"]): row.get("SellingPrice")
        for row in item_rows
        if isinstance(row, dict) and row.get("Type") is not None
    }
    required_fields = [
        "Code",
        "Price",
        "DiffScore",
        "Weight",
        "UnlockLevel",
        "ForbiddenSlot",
        "RepeatWeightDecrease",
        "ProgressUnlock",
    ]
    variants: dict[str, list[dict[str, Any]]] = {}
    for table_name in table_names:
        normalized: list[dict[str, Any]] = []
        for row in facts.rows(table_name):
            if not isinstance(row, dict):
                continue
            code = str(row.get("Code"))
            selling_price = selling_prices.get(code)
            price = row.get("Price")
            ratio = None
            if isinstance(price, (int, float)) and isinstance(selling_price, (int, float)):
                if selling_price != 0:
                    ratio = round(price / selling_price, 6)
            record = {field: row.get(field) for field in required_fields}
            record.update(
                {
                    "Code": code,
                    "SellingPrice": selling_price,
                    "PriceToSellingPrice": ratio,
                }
            )
            normalized.append(record)
        variants[table_name] = sorted(normalized, key=lambda row: _sort_key(row["Code"]))

    base_rows = variants.get("OrderItemConfig", [])
    curves: list[dict[str, Any]] = []
    for segment in sorted({_segment_for_type(row["Code"]) for row in base_rows}):
        points = [row for row in base_rows if _segment_for_type(row["Code"]) == segment]
        curves.append(
            {
                "segment": segment,
                "points": [
                    {
                        "code": row["Code"],
                        "price": row["Price"],
                        "selling_price": row["SellingPrice"],
                        "price_to_selling_price": row["PriceToSellingPrice"],
                    }
                    for row in points
                ],
            }
        )
    return {
        "source_tables": table_names,
        "variants": variants,
        "price_curves": curves,
        "warnings": [],
    }


def extract_progression_main_tasks(facts: ConfigFacts) -> dict[str, Any]:
    """Group task sequences by day/area and aggregate their cost and experience."""

    warnings: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    for ordinal, row in enumerate(facts.rows("MainTaskConfig")):
        if not isinstance(row, dict):
            continue
        rewards = parse_rewards(
            row.get("rewards"), warnings=warnings, table="MainTaskConfig", field="rewards"
        )
        tasks.append(
            {
                "ordinal": ordinal,
                "day": row.get("day"),
                "area": row.get("area"),
                "id": row.get("id"),
                "cost": row.get("cost"),
                "rewards": rewards,
                "detail_icon": row.get("detail_icon"),
                "list_icon": row.get("list_icon"),
            }
        )
    tasks.sort(
        key=lambda row: (
            _sort_key(row["day"]),
            _sort_key(row["area"]),
            _sort_key(row["id"]),
            row["ordinal"],
        )
    )

    grouped: dict[tuple[Any, Any], list[dict[str, Any]]] = defaultdict(list)
    day_totals: dict[Any, dict[str, int]] = defaultdict(lambda: {"cost": 0, "exp": 0})
    for task in tasks:
        grouped[(task["day"], task["area"])].append(task)
        if isinstance(task["cost"], (int, float)):
            day_totals[task["day"]]["cost"] += task["cost"]
        day_totals[task["day"]]["exp"] += sum(
            reward["amount"] for reward in task["rewards"] if reward["type"] == "exp"
        )

    sequences = [
        {"day": day, "area": area, "tasks": sequence}
        for (day, area), sequence in sorted(
            grouped.items(), key=lambda item: (_sort_key(item[0][0]), _sort_key(item[0][1]))
        )
    ]
    summaries = [
        {"day": day, "cost_total": totals["cost"], "exp_total": totals["exp"]}
        for day, totals in sorted(day_totals.items(), key=lambda item: _sort_key(item[0]))
    ]
    return {
        "source_tables": ["MainTaskConfig"],
        "sequences": sequences,
        "day_summaries": summaries,
        "first_30_tasks": tasks[:30],
        "warnings": warnings,
    }


def extract_progression_player_levels(facts: ConfigFacts) -> dict[str, Any]:
    """Build the player level cost/reward curve and reward frequency table."""

    warnings: list[dict[str, Any]] = []
    levels: list[dict[str, Any]] = []
    cumulative_cost: int | float = 0
    reward_frequency: Counter[str] = Counter()
    rows = [row for row in facts.rows("LevelConfig") if isinstance(row, dict)]
    for row in sorted(rows, key=lambda item: _sort_key(item.get("level"))):
        cost = row.get("cost")
        if isinstance(cost, (int, float)):
            cumulative_cost += cost
        rewards = parse_rewards(
            row.get("rewards"), warnings=warnings, table="LevelConfig", field="rewards"
        )
        reward_frequency.update(str(reward["type"]) for reward in rewards)
        levels.append(
            {
                "level": row.get("level"),
                "cost": cost,
                "cumulative_cost": cumulative_cost,
                "rewards": rewards,
            }
        )
    return {
        "source_tables": ["LevelConfig"],
        "levels": levels,
        "reward_type_frequency": dict(
            sorted(reward_frequency.items(), key=lambda item: _sort_key(item[0]))
        ),
        "warnings": warnings,
    }


def _price_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in row.items() if "price" in key.lower() or "cost" in key.lower()
    }


def _limit_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if any(
            part in key.lower() for part in ("limit", "leftcount", "purchasecount", "maxpurchase")
        )
    }


def extract_monetization_shop_iap_cn(facts: ConfigFacts) -> dict[str, Any]:
    """Preserve shop rows and expose China/overseas content differences."""

    table_names = [
        name
        for name in (
            "ShopIAPConfigCN",
            "ShopIAPConfig",
            "ShopDailyDealsConfig",
            "ShopDailyDealsConfig_random",
            "ShopSlotConfig",
            "InventorySlotConfig",
            "InventorySlotConfig_cheap",
        )
        if facts.has_table(name)
    ]

    def products(table_name: str) -> list[dict[str, Any]]:
        output = []
        for ordinal, row in enumerate(facts.rows(table_name)):
            if not isinstance(row, dict):
                continue
            product_id = row.get("purchaseId", row.get("id", ordinal))
            contents = row.get("goods", row.get("itemCode"))
            output.append(
                {
                    "id": product_id,
                    "price_fields": _price_fields(row),
                    "contents": contents,
                    "limit_fields": _limit_fields(row),
                    "raw": row,
                }
            )
        return output

    cn_products = products("ShopIAPConfigCN") if facts.has_table("ShopIAPConfigCN") else []
    overseas_products = products("ShopIAPConfig") if facts.has_table("ShopIAPConfig") else []
    cn_by_id = {str(product["id"]): product for product in cn_products}
    overseas_by_id = {str(product["id"]): product for product in overseas_products}
    comparison = []
    for product_id in sorted(set(cn_by_id) | set(overseas_by_id), key=_sort_key):
        cn = cn_by_id.get(product_id)
        overseas = overseas_by_id.get(product_id)
        cn_contents = cn["contents"] if cn else None
        overseas_contents = overseas["contents"] if overseas else None
        cn_raw = cn["raw"] if cn else None
        overseas_raw = overseas["raw"] if overseas else None
        field_names = sorted(set(cn_raw or {}) | set(overseas_raw or {}))
        different_fields = [
            field
            for field in field_names
            if (cn_raw or {}).get(field) != (overseas_raw or {}).get(field)
        ]
        comparison.append(
            {
                "id": product_id,
                "cn_contents": cn_contents,
                "overseas_contents": overseas_contents,
                "contents_differ": cn_contents != overseas_contents,
                "cn_price_fields": cn["price_fields"] if cn else None,
                "overseas_price_fields": overseas["price_fields"] if overseas else None,
                "different_fields": different_fields,
                "cn_raw": cn_raw,
                "overseas_raw": overseas_raw,
            }
        )

    inventories = {}
    for name in ("InventorySlotConfig", "InventorySlotConfig_cheap"):
        if facts.has_table(name):
            rows = [row for row in facts.rows(name) if isinstance(row, dict)]
            inventories[name] = rows

    daily_deals = {
        name: products(name)
        for name in ("ShopDailyDealsConfig", "ShopDailyDealsConfig_random")
        if facts.has_table(name)
    }
    shop_slots = (
        [row for row in facts.rows("ShopSlotConfig") if isinstance(row, dict)]
        if facts.has_table("ShopSlotConfig")
        else []
    )
    return {
        "source_tables": table_names,
        "iap_cn": cn_products,
        "iap_overseas": overseas_products,
        "iap_comparison": comparison,
        "daily_deals": daily_deals,
        "shop_slot_weights": shop_slots,
        "inventory_expansion": inventories,
        "warnings": [],
    }


def _parse_board_cell(raw: Any) -> dict[str, Any]:
    text = str(raw)
    parts = text.split("#")
    type_id = parts[-1]
    prefixes = parts[:-1]
    return {
        "raw": raw,
        "type": type_id,
        "prefixes": [
            {
                "prefix": prefix,
                "meaning": "纸箱/遮罩（候选）" if prefix == "pb" else None,
                "status": "unknown",
            }
            for prefix in prefixes
        ],
    }


def extract_board_initial_layout(facts: ConfigFacts) -> dict[str, Any]:
    """Decode board cell prefixes without assigning unverified gameplay semantics."""

    board_rows = facts.rows("BoardModelConfig") if facts.has_table("BoardModelConfig") else ()
    grid: list[list[dict[str, Any]]] = []
    type_counts: Counter[str] = Counter()
    prefix_counts: Counter[str] = Counter()
    for row in board_rows:
        if not isinstance(row, list):
            continue
        parsed_row = []
        for cell in row:
            parsed = _parse_board_cell(cell)
            parsed_row.append(parsed)
            type_counts[str(parsed["type"])] += 1
            prefix_counts.update(prefix["prefix"] for prefix in parsed["prefixes"])
        grid.append(parsed_row)

    sources = ["BoardModelConfig"] if facts.has_table("BoardModelConfig") else []
    if facts.has_table("MapBorderConfig"):
        sources.append("MapBorderConfig")
    reward_table = "MainPaperboxExtraRewardConfig"
    reward_grid: list[list[str]] = []
    reward_counts: Counter[str] = Counter()
    if facts.has_table(reward_table):
        sources.append(reward_table)
        for row in facts.rows(reward_table):
            if not isinstance(row, list):
                continue
            cells = [str(cell) for cell in row]
            reward_grid.append(cells)
            reward_counts.update(cell for cell in cells if cell != "0")
    covered = [
        {
            "row": row_index,
            "column": column_index,
            "board_cell": grid[row_index - 1][column_index - 1]["raw"],
            "covered": "pb" in {
                prefix["prefix"] for prefix in grid[row_index - 1][column_index - 1]["prefixes"]
            },
            "reward": cell,
        }
        for row_index, cells in enumerate(reward_grid, start=1)
        for column_index, cell in enumerate(cells, start=1)
        if cell != "0"
        and row_index <= len(grid)
        and column_index <= len(grid[row_index - 1])
    ]
    return {
        "source_tables": sources,
        "dimensions": {
            "rows": len(grid),
            "columns": max((len(row) for row in grid), default=0),
            "configured_size": "7×9",
        },
        "grid": grid,
        "type_distribution": dict(sorted(type_counts.items(), key=lambda item: _sort_key(item[0]))),
        "prefix_distribution": dict(sorted(prefix_counts.items())),
        "paperbox_rewards": {
            "table": reward_table,
            "available": bool(reward_grid),
            "rows": len(reward_grid),
            "columns": max((len(row) for row in reward_grid), default=0),
            "grid": reward_grid,
            "reward_distribution": dict(sorted(reward_counts.items())),
            "rewarding_cells": covered,
            "rewarding_cell_count": len(covered),
            "cells_under_a_paperbox": sum(1 for cell in covered if cell["covered"]),
        },
        "warnings": [],
    }


EXTRACTORS = {
    "economy-item-chains": extract_economy_item_chains,
    "economy-order-pricing": extract_economy_order_pricing,
    "progression-main-tasks": extract_progression_main_tasks,
    "progression-player-levels": extract_progression_player_levels,
    "monetization-shop-iap-cn": extract_monetization_shop_iap_cn,
    "board-initial-layout": extract_board_initial_layout,
}


def extract_all(facts: ConfigFacts) -> dict[str, dict[str, Any]]:
    """Run the six required extraction passes in stable slug order."""

    return {slug: extractor(facts) for slug, extractor in EXTRACTORS.items()}
