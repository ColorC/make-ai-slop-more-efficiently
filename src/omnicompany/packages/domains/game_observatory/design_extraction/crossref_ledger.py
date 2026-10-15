"""Build the manifest-derived configuration cross-reference ledger skeleton."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config_facts import ConfigFacts
from .evidence import EvidenceBundle
from .render_docs import utc_now, write_atomic

# Configuration rows that arrive as bare lists carry no column names, so the
# field-level ledger used to report 0/0 for them.  The row-level ledger already
# addresses those cells by (row, col); the same pair composes the column name.
MATRIX_ROW_KEY = ["row", "col"]
_CARRIED_KEYS = ("status", "fact_ids", "last_evidence_at")


def matrix_tables(evidence: EvidenceBundle | None) -> list[str]:
    """Name the tables whose row ledger addresses cells by (row, col)."""

    if evidence is None:
        return []
    tables = (evidence.row_coverage or {}).get("tables") or {}
    return sorted(
        name
        for name, value in tables.items()
        if isinstance(value, dict) and list(value.get("row_key") or []) == MATRIX_ROW_KEY
    )


def matrix_field_name(row: Any, column: Any) -> str:
    return f"cell({row},{column})"


def _matrix_fields(facts: ConfigFacts, table_name: str) -> dict[str, dict[str, Any]]:
    if not facts.has_table(table_name):
        return {}
    fields: dict[str, dict[str, Any]] = {}
    for row_index, row in enumerate(facts.rows(table_name), start=1):
        if not isinstance(row, list):
            continue
        for column_index, _cell in enumerate(row, start=1):
            name = matrix_field_name(row_index, column_index)
            fields[name] = {
                "status": "unobserved",
                "fact_ids": [],
                "config_refs": [f"{table_name}.rows[{row_index - 1}][{column_index - 1}]"],
            }
    return fields


def _seed_matrix_evidence(
    table_name: str, fields: dict[str, dict[str, Any]], evidence: EvidenceBundle
) -> None:
    """Carry row-level verdicts into the synthesized columns of one matrix table.

    The cross-reference rules address every board cell through a single ``cell``
    column, so its per-cell verdicts only ever reach the row ledger.  Reading
    them back here is what turns the field-level ledger from 0/0 into the same
    ratio the row ledger already reports.
    """

    rows = ((evidence.row_coverage or {}).get("tables") or {}).get(table_name) or {}
    for entry in (rows.get("rows") or {}).values():
        if not isinstance(entry, dict):
            continue
        row_keys = entry.get("row_keys") or {}
        name = matrix_field_name(row_keys.get("row"), row_keys.get("col"))
        target = fields.get(name)
        if target is None:
            continue
        if entry.get("contradicted_fields"):
            target["status"] = "contradicted"
        elif entry.get("observed_fields"):
            target["status"] = "observed"
        target["fact_ids"] = sorted(entry.get("fact_ids") or [])
        last_seen = entry.get("last_evidence_at") or ""
        if last_seen:
            target["last_evidence_at"] = last_seen


def build_crossref_ledger(
    facts: ConfigFacts,
    *,
    generated_at: str | None = None,
    evidence: EvidenceBundle | None = None,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a field-complete ledger, preserving verdicts already written back."""

    tables: dict[str, dict[str, Any]] = {}
    manifest_entries = sorted(
        (
            entry
            for entry in facts.manifest
            if isinstance(entry.get("table"), str) and isinstance(entry.get("fields"), list)
        ),
        key=lambda entry: str(entry["table"]),
    )
    synthesized = set(matrix_tables(evidence))
    previous_tables = (previous or {}).get("tables") or {}
    for entry in manifest_entries:
        table_name = str(entry["table"])
        field_names = [str(field) for field in entry["fields"]]
        fields: dict[str, dict[str, Any]] = {
            name: {
                "status": "unobserved",
                "fact_ids": [],
                "config_refs": [f"{table_name}.{name}"],
            }
            for name in field_names
        }
        if not fields and table_name in synthesized:
            fields = _matrix_fields(facts, table_name)
        previous_fields = (previous_tables.get(table_name) or {}).get("fields") or {}
        for name, value in fields.items():
            carried = previous_fields.get(name)
            if isinstance(carried, dict):
                for key in _CARRIED_KEYS:
                    if key in carried:
                        value[key] = carried[key]
        if table_name in synthesized and evidence is not None:
            _seed_matrix_evidence(table_name, fields, evidence)
        tables[table_name] = {"field_count": len(fields), "fields": fields}

    counts = {"observed": 0, "unobserved": 0, "contradicted": 0}
    total_fields = 0
    for table in tables.values():
        for value in table["fields"].values():
            total_fields += 1
            status = str(value.get("status", "unobserved"))
            counts[status if status in counts else "unobserved"] += 1

    return {
        "schema": "game-observatory.config-crossref-ledger.v1",
        "generated_at": generated_at or utc_now(),
        "tables": tables,
        "summary": {
            "tables": len(tables),
            "fields": total_fields,
            **counts,
        },
    }


def write_crossref_ledger(
    facts: ConfigFacts,
    out_ledger: str | Path,
    *,
    generated_at: str | None = None,
    evidence: EvidenceBundle | None = None,
) -> dict[str, Any]:
    """Write the ledger without discarding verdicts a previous write-back made."""

    output_path = Path(out_ledger)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    previous: dict[str, Any] | None = None
    if output_path.is_file():
        loaded = json.loads(output_path.read_text(encoding="utf-8"))
        previous = loaded if isinstance(loaded, dict) else None
    ledger = build_crossref_ledger(
        facts, generated_at=generated_at, evidence=evidence, previous=previous
    )
    # Written through a sibling and renamed into place.  Opening this product
    # directly for truncation fails on this host whenever another session still
    # holds the copy it has just read, which is most of the time now that the
    # ledger is read by the dashboard, the report and the write-back.  The
    # metrics package already writes its products this way for the same reason.
    write_atomic(
        output_path,
        json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return ledger
