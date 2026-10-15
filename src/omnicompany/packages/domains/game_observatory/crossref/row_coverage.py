"""Row-level cross-reference coverage for Romantic Restaurant."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .evidence_index import FactV1
from .rules import (
    RuleResult,
    generate_deviation_observation,
    generator_weight_fits,
    order_frequency_observation,
)

SCHEMA = "game-observatory.crossref-row-coverage.v1"

# Which configuration columns identify one row of each table F6-02 tracks.
ROW_KEY_FIELDS: dict[str, tuple[str, ...]] = {
    "ItemModelConfig": ("Type",),
    "OrderItemConfig": ("Code",),
    "OrderItemConfig_chesthard": ("Code",),
    "OrderItemConfig_contiorder": ("Code",),
    "MainTaskConfig": ("day", "id"),
    "LevelConfig": ("level",),
    "BoardModelConfig": ("row", "col"),
    # F7-04 section 1.1.  The energy-party score table is keyed the same way
    # the item table is, by the item type whose chain it scores.
    "ItemGenerateScore": ("Type",),
}

CORE_TABLES = (
    "MainTaskConfig",
    "OrderItemConfig",
    "ItemModelConfig",
    "LevelConfig",
    "BoardModelConfig",
)


def _load_rows(config_root: Path, table: str) -> list[Any]:
    path = config_root / f"{table}.json"
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("rows") or []


def _row_key(row_keys: dict[str, str], fields: tuple[str, ...]) -> str | None:
    values = [row_keys.get(name) for name in fields]
    if any(value is None for value in values):
        return None
    return "|".join(str(value) for value in values)


def _total_rows(config_root: Path, table: str) -> int:
    rows = _load_rows(config_root, table)
    if table == "BoardModelConfig":
        return sum(len(row) for row in rows if isinstance(row, list))
    return len(rows)


def build_row_coverage(
    results: Iterable[RuleResult],
    facts_by_id: dict[str, FactV1],
    config_root: str | Path,
    *,
    facts: Iterable[FactV1] = (),
    generated_at: str = "",
) -> dict[str, Any]:
    """Aggregate rule results into per-row observed/contradicted ledgers."""

    root = Path(config_root)
    results = list(results)
    facts = list(facts)
    tables: dict[str, dict[str, Any]] = {}
    for result in results:
        fields = ROW_KEY_FIELDS.get(result.table)
        if not fields:
            continue
        key = _row_key(result.row_keys, fields)
        if key is None:
            continue
        table = tables.setdefault(result.table, {})
        row = table.setdefault(
            key,
            {
                "row_keys": {name: result.row_keys[name] for name in fields},
                "observed_fields": set(),
                "contradicted_fields": set(),
                "fact_ids": set(),
                "first_evidence_at": "",
                "last_evidence_at": "",
            },
        )
        bucket = "contradicted_fields" if result.status == "contradicted" else "observed_fields"
        row[bucket].add(result.field)
        for fact_id in result.all_fact_ids:
            row["fact_ids"].add(fact_id)
            captured = (facts_by_id.get(fact_id).captured_at if fact_id in facts_by_id else "")
            if not captured:
                continue
            if not row["first_evidence_at"] or captured < row["first_evidence_at"]:
                row["first_evidence_at"] = captured
            if captured > row["last_evidence_at"]:
                row["last_evidence_at"] = captured

    rendered: dict[str, Any] = {}
    for table in sorted(set(tables) | set(CORE_TABLES) | set(ROW_KEY_FIELDS)):
        rows = tables.get(table, {})
        total = _total_rows(root, table)
        entries = {}
        for key in sorted(rows):
            row = rows[key]
            observed = sorted(row["observed_fields"] - row["contradicted_fields"])
            entries[key] = {
                "row_keys": row["row_keys"],
                "observed_fields": observed,
                "contradicted_fields": sorted(row["contradicted_fields"]),
                "fact_ids": sorted(row["fact_ids"]),
                "first_evidence_at": row["first_evidence_at"],
                "last_evidence_at": row["last_evidence_at"],
            }
        observed_rows = sum(1 for entry in entries.values() if entry["observed_fields"])
        contradicted_rows = sum(
            1 for entry in entries.values() if entry["contradicted_fields"]
        )
        rendered[table] = {
            "row_key": list(ROW_KEY_FIELDS.get(table, ())),
            "total_rows": total,
            "verified_rows": observed_rows,
            "contradicted_rows": contradicted_rows,
            "row_coverage_rate": observed_rows / total if total else 0.0,
            "rows": entries,
        }

    top_verified = sorted(
        (
            {
                "table": table,
                "row": key,
                "observed_fields": entry["observed_fields"],
                "contradicted_fields": entry["contradicted_fields"],
                "fact_count": len(entry["fact_ids"]),
                "last_evidence_at": entry["last_evidence_at"],
            }
            for table, value in rendered.items()
            for key, entry in value["rows"].items()
            if entry["observed_fields"]
        ),
        key=lambda item: (-len(item["observed_fields"]), -item["fact_count"], item["table"], item["row"]),
    )[:20]

    item_rows = _load_rows(root, "ItemModelConfig")
    order_rows = _load_rows(root, "OrderItemConfig")
    weight_stats = {
        generator: {
            "declared_weights": entry["declared_weights"],
            "sample_fact_count": len(entry["fact_ids"]),
            **({"fit": entry["fit"].to_dict()} if entry["fit"] else {"fit": None}),
        }
        for generator, entry in generator_weight_fits(facts, item_rows).items()
    }
    return {
        "schema": SCHEMA,
        "generated_at": generated_at,
        "tables": rendered,
        "top_verified_rows": top_verified,
        "generator_weight_stats": weight_stats,
        "order_frequency": order_frequency_observation(facts, order_rows),
        # F7-04 section 1.5.  A relaxed generation verdict is a third column,
        # not a softer confirmation: it is counted here and nowhere else, so
        # neither ``verified_rows`` nor ``contradicted_rows`` can absorb it.
        "generate_deviation": generate_deviation_observation(facts),
    }


def row_coverage_summary(row_coverage: dict[str, Any]) -> dict[str, Any]:
    """Condense the row ledger into the per-table block carried by summary.v1."""

    return {
        table: {
            "total_rows": value["total_rows"],
            "verified_rows": value["verified_rows"],
            "contradicted_rows": value["contradicted_rows"],
            "row_coverage_rate": value["row_coverage_rate"],
        }
        for table, value in sorted((row_coverage.get("tables") or {}).items())
    }


def write_row_coverage(payload: dict[str, Any], path: str | Path) -> dict[str, Any]:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or target.read_text(encoding="utf-8") != rendered:
        target.write_text(rendered, encoding="utf-8", newline="\n")
    return payload
