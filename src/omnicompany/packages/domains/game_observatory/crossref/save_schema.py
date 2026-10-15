"""Reconcile the declared save-table contract against a real save file.

``DBTableConfigs.json`` (the F0-02b permissive import) declares every table the
game keeps in its SQLCipher save, with column names and storage types.  This
module reads one decrypted save copy and compares the two, column by column, so
the declaration itself gets a live verification and ``save_reader`` gets a
completeness check: which declared tables it has ever decoded.

Nothing here touches the device.  The caller supplies an already-decrypted
SQLite file, or uses :func:`build_from_encrypted_save`, which decrypts one
copied database into a temporary directory through the existing offline path
and removes the plaintext again.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from ..delegation.save_reader import load_db_schema_from_decompile
from ..delegation.sqlcipher_offline import (
    DEFAULT_PARAMETERS,
    SQLCipherParametersV1,
    decrypt_sqlcipher_file,
    load_save_password,
)

SCHEMA = "game-observatory.save-schema-coverage.v1"

# DBTable.lua gives a table that declares no columns the key/value pair.
DEFAULT_COLUMNS = {"key": "text", "value": "text"}
DEFAULT_PRIMARY_KEY = "key"

_STORAGE_TYPES = {"text", "integer", "real"}
_CONSUMED_RE = re.compile(
    r'(?:table_rows\.get\(|table_rows\[|_table\(schema,\s*)"([A-Za-z_][A-Za-z0-9_]*)"'
)


def save_reader_consumed_tables(source_path: str | Path) -> list[str]:
    """Name the logical tables ``save_reader`` decodes into ``GameStateV1``.

    Read statically off the module source rather than hand-listed, so a reader
    that starts decoding a new table is picked up without editing this file.
    """

    source = Path(source_path).read_text(encoding="utf-8")
    return sorted(set(_CONSUMED_RE.findall(source)))


def _storage_type(value: Any) -> str | None:
    """Normalise one declared column value to a SQLite storage type."""

    if isinstance(value, dict):
        value = value.get("value")
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    return text if text in _STORAGE_TYPES else None


def _declared_entries(config_path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
    entries = (payload.get("rows") or {}).get("EDBTableConfigs")
    if not isinstance(entries, dict):
        raise ValueError("DBTableConfigs.json has no EDBTableConfigs mapping")
    return {str(key): value for key, value in entries.items() if isinstance(value, dict)}


def load_declared_tables(
    config_path: str | Path, *, lua_dir: str | Path | None = None
) -> dict[str, dict[str, Any]]:
    """Resolve every declared save table into ``{name, columns, primary_key}``.

    Three declaration shapes appear in the import: an explicit ``columns`` map,
    a bare ``name`` (which DBTable.lua gives the default key/value pair), and a
    ``ConfigCreator`` call whose columns only exist in the Lua source.  The
    creator form is resolved through the same decompile reader the save channel
    uses; when that source is unavailable the entry is kept and marked
    unresolved rather than silently dropped.
    """

    entries = _declared_entries(config_path)
    creators: dict[str, Any] = {}
    creator_error: str | None = None
    if any("call" in entry for entry in entries.values()):
        try:
            creators = load_db_schema_from_decompile(lua_dir).tables if lua_dir else {}
            if not lua_dir:
                creator_error = "no decompiled Lua directory was supplied"
        except (OSError, ValueError) as exc:
            creator_error = f"{type(exc).__name__}: {exc}"

    declared: dict[str, dict[str, Any]] = {}
    for logical_name, entry in sorted(entries.items()):
        raw_columns = entry.get("columns")
        if isinstance(raw_columns, dict):
            columns = {
                str(column): _storage_type(value) for column, value in raw_columns.items()
            }
            declared[logical_name] = {
                "name": str(entry.get("name") or ""),
                "columns": columns,
                "primary_key": str(entry.get("primaryKey") or DEFAULT_PRIMARY_KEY),
                "declaration": "columns",
                "resolved": True,
            }
        elif "call" in entry:
            resolved = creators.get(logical_name)
            declared[logical_name] = {
                "name": str(
                    resolved.name if resolved is not None else (entry.get("args") or [""])[0]
                ),
                "columns": (
                    {name: column.storage_type for name, column in resolved.columns.items()}
                    if resolved is not None
                    else {}
                ),
                "primary_key": (
                    resolved.primary_key if resolved is not None else DEFAULT_PRIMARY_KEY
                ),
                "declaration": f"creator:{entry.get('call')}",
                "resolved": resolved is not None,
                **({} if resolved is not None else {"unresolved_reason": creator_error or ""}),
            }
        else:
            declared[logical_name] = {
                "name": str(entry.get("name") or ""),
                "columns": dict(DEFAULT_COLUMNS),
                "primary_key": DEFAULT_PRIMARY_KEY,
                "declaration": "key_value_default",
                "resolved": True,
            }
    return declared


def read_live_schema(database_path: str | Path) -> dict[str, dict[str, Any]]:
    """Read table names, columns, storage types and row counts from one save."""

    uri = f"file:{Path(database_path).resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        names = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        live: dict[str, dict[str, Any]] = {}
        for name in names:
            quoted = '"' + name.replace('"', '""') + '"'
            columns = {
                str(row[1]): str(row[2]).lower()
                for row in connection.execute(f"PRAGMA table_info({quoted})")
            }
            primary_key = [
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({quoted})")
                if row[5]
            ]
            live[name] = {
                "columns": columns,
                "primary_key": primary_key[0] if len(primary_key) == 1 else "",
                "row_count": connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0],
            }
        return live
    finally:
        connection.close()


def _compare_one(
    declared: dict[str, Any], live: dict[str, Any] | None
) -> dict[str, Any]:
    declared_columns: dict[str, Any] = declared["columns"]
    if live is None:
        return {
            "verdict": "declared_only",
            "table": declared["name"],
            "declaration": declared["declaration"],
            "declared_columns": dict(sorted(declared_columns.items())),
            "declared_only_columns": sorted(declared_columns),
            "live_only_columns": [],
            "type_mismatches": {},
            "matched_columns": [],
        }
    live_columns: dict[str, str] = live["columns"]
    shared = sorted(set(declared_columns) & set(live_columns))
    mismatches = {
        name: {"declared": declared_columns[name], "live": live_columns[name]}
        for name in shared
        if declared_columns[name] != live_columns[name]
    }
    declared_only = sorted(set(declared_columns) - set(live_columns))
    live_only = sorted(set(live_columns) - set(declared_columns))
    if declared_only or live_only:
        verdict = "column_set_differs"
    elif mismatches:
        verdict = "type_differs"
    else:
        verdict = "match"
    return {
        "verdict": verdict,
        "table": declared["name"],
        "declaration": declared["declaration"],
        "declared_columns": dict(sorted(declared_columns.items())),
        "live_columns": dict(sorted(live_columns.items())),
        "matched_columns": [name for name in shared if name not in mismatches],
        "declared_only_columns": declared_only,
        "live_only_columns": live_only,
        "type_mismatches": mismatches,
        "primary_key_matches": declared["primary_key"] == live["primary_key"],
        "declared_primary_key": declared["primary_key"],
        "live_primary_key": live["primary_key"],
        "live_row_count": live["row_count"],
    }


def build_save_schema_coverage(
    declared: dict[str, dict[str, Any]],
    live: dict[str, dict[str, Any]],
    *,
    save_ref: str,
    save_sha256: str,
    consumed_tables: list[str],
) -> dict[str, Any]:
    """Reconcile declaration and save into the coverage product F6-03 asks for."""

    tables: dict[str, dict[str, Any]] = {}
    claimed: set[str] = set()
    for logical_name, entry in sorted(declared.items()):
        live_entry = live.get(entry["name"])
        if live_entry is not None:
            claimed.add(entry["name"])
        comparison = _compare_one(entry, live_entry)
        comparison["read_by_save_reader"] = logical_name in consumed_tables
        tables[logical_name] = comparison
    for name in sorted(set(live) - claimed):
        tables[f"(undeclared) {name}"] = {
            "verdict": "live_only",
            "table": name,
            "declaration": "-",
            "declared_columns": {},
            "live_columns": dict(sorted(live[name]["columns"].items())),
            "matched_columns": [],
            "declared_only_columns": [],
            "live_only_columns": sorted(live[name]["columns"]),
            "type_mismatches": {},
            "live_row_count": live[name]["row_count"],
            "read_by_save_reader": False,
        }

    verdicts = [entry["verdict"] for entry in tables.values()]
    totals = {
        "declared_tables": len(declared),
        "live_tables": len(live),
        "match": verdicts.count("match"),
        "column_set_differs": verdicts.count("column_set_differs"),
        "type_differs": verdicts.count("type_differs"),
        "declared_only": verdicts.count("declared_only"),
        "live_only": verdicts.count("live_only"),
        "declared_columns": sum(len(entry["columns"]) for entry in declared.values()),
        "matched_columns": sum(len(entry["matched_columns"]) for entry in tables.values()),
        "declared_only_columns": sum(
            len(entry["declared_only_columns"]) for entry in tables.values()
        ),
        "live_only_columns": sum(
            len(entry["live_only_columns"]) for entry in tables.values()
        ),
        "type_mismatch_columns": sum(
            len(entry["type_mismatches"]) for entry in tables.values()
        ),
        "unresolved_declarations": sum(
            1 for entry in declared.values() if not entry["resolved"]
        ),
    }
    read = sorted(name for name in declared if name in consumed_tables)
    return {
        "schema": SCHEMA,
        "save": {"source_ref": save_ref, "encrypted_sha256": save_sha256},
        "totals": totals,
        "save_reader": {
            "read_tables": read,
            "read_table_count": len(read),
            "never_read_tables": sorted(name for name in declared if name not in consumed_tables),
        },
        "tables": tables,
    }


def build_from_encrypted_save(
    encrypted_path: str | Path,
    *,
    config_path: str | Path,
    lua_dir: str | Path | None = None,
    save_reader_source: str | Path | None = None,
    key_file: str | Path | None = None,
    save_ref: str | None = None,
    parameters: SQLCipherParametersV1 = DEFAULT_PARAMETERS,
) -> dict[str, Any]:
    """Decrypt one copied save into a temporary file and reconcile its schema."""

    source = Path(encrypted_path)
    encrypted_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    password = load_save_password(Path(key_file) if key_file else None)
    workspace = Path(tempfile.mkdtemp(prefix="rr-save-schema-"))
    try:
        plaintext = workspace / "plaintext.sqlite3"
        wal = source.with_name(source.name + "-wal")
        decrypt_sqlcipher_file(
            source,
            plaintext,
            password=password,
            wal_path=wal if wal.is_file() else None,
            parameters=parameters,
        )
        live = read_live_schema(plaintext)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    reader_source = save_reader_source or (
        Path(__file__).resolve().parents[1] / "delegation/save_reader.py"
    )
    return build_save_schema_coverage(
        load_declared_tables(config_path, lua_dir=lua_dir),
        live,
        save_ref=save_ref or source.as_posix(),
        save_sha256=encrypted_sha256,
        consumed_tables=save_reader_consumed_tables(reader_source),
    )


def write_save_schema_coverage(payload: dict[str, Any], path: str | Path) -> Path:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or target.read_text(encoding="utf-8") != rendered:
        target.write_text(rendered, encoding="utf-8", newline="\n")
    return target


def read_save_schema_coverage(path: str | Path) -> dict[str, Any] | None:
    target = Path(path)
    if not target.is_file():
        return None
    payload = json.loads(target.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


__all__ = [
    "DEFAULT_COLUMNS",
    "SCHEMA",
    "build_from_encrypted_save",
    "build_save_schema_coverage",
    "load_declared_tables",
    "read_live_schema",
    "read_save_schema_coverage",
    "save_reader_consumed_tables",
    "write_save_schema_coverage",
]
