"""Swap localization keys for the text the unpacked package actually holds.

Several configuration tables store a lookup key where a reader expects a
sentence.  ``GameTextModel:GetText`` resolves those keys at runtime against
``game_text_<language>.json``, which the Addressables unpack step writes next to
the normalized tables.  This module is the single place that decides whether a
column is a key column and performs the substitution, so the verified layer and
the permissive layer report the same numbers.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence


# A cell that already reads as Chinese is authored text, not a lookup key.
CJK = re.compile(r"[一-鿿]")

MISSING = "（未取得）"


def is_authored_text(value: Any) -> bool:
    return bool(CJK.search(str(value or "")))


def lookup(texts: Mapping[str, str], key: Any) -> str | None:
    """Resolve one key, returning ``None`` when the package does not hold it."""

    name = str(key or "")
    if not name:
        return None
    value = texts.get(name)
    return None if value is None else str(value)


def scan_field(
    values: Sequence[Any], texts: Mapping[str, str]
) -> dict[str, Any]:
    """Count how many of one column's values resolve against the text table."""

    present = [value for value in values if str(value or "")]
    hits = sum(1 for value in present if str(value) in texts)
    authored = sum(1 for value in present if is_authored_text(value))
    return {
        "values": len(present),
        "resolved": hits,
        "authored_text": authored,
        # Authored text never becomes a key column, however many of its cells
        # happen to collide with a key name.
        "substitutable": bool(hits) and not authored,
    }


def scan_table(
    table: str,
    fields: Iterable[str],
    rows: Iterable[Any],
    texts: Mapping[str, str],
) -> dict[str, Any]:
    """Report every column of one table against the unpacked text table."""

    materialized = [row for row in rows if isinstance(row, dict)]
    columns: list[dict[str, Any]] = []
    for name in fields:
        report = scan_field([row.get(name) for row in materialized], texts)
        report["field"] = name
        columns.append(report)
    substitutable = [column["field"] for column in columns if column["substitutable"]]
    return {
        "table": table,
        "row_count": len(materialized),
        "text_available": bool(texts),
        "columns": columns,
        "substitutable_fields": substitutable,
        "substitutable_field_count": len(substitutable),
    }


def substitute_table(
    table: str,
    fields: Iterable[str],
    rows: Iterable[Any],
    texts: Mapping[str, str],
) -> dict[str, Any]:
    """Scan one table and swap out every cell of its key columns.

    A column only counts as a key column when it has at least one hit and none
    of its cells is already authored Chinese, so a table whose copy is written
    in place keeps that copy.
    """

    materialized = [row for row in rows if isinstance(row, dict)]
    scan = scan_table(table, fields, materialized, texts)
    substituted: list[dict[str, Any]] = []
    for name in scan["substitutable_fields"]:
        for row in materialized:
            text = lookup(texts, row.get(name))
            if text is None:
                continue
            substituted.append({"field": name, "key": str(row.get(name)), "text": text})
    scan["available"] = bool(texts)
    scan["substituted"] = substituted
    scan["substituted_count"] = len(substituted)
    return scan


def hit_rate(resolved: int, total: int) -> float:
    """Percentage with one decimal, so a document never prints a raw ratio."""

    return round(100.0 * resolved / total, 1) if total else 0.0
