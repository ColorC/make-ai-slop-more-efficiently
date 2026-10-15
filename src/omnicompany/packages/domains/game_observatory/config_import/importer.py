"""Batch importer for decompiled Lua configuration tables."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .lua_literal import LuaLiteralError, LuaNotLiteralError, parse_lua_return
from .lua_module import RETURN_EXPORT, data_exports, scan_module
from .symbols import SymbolTable


TABLE_SCHEMA = "game-observatory.decompiled-config-table.v1"


@dataclass(frozen=True)
class ImportSummary:
    """Deterministic counters for one batch import."""

    source_files: int
    parsed: int
    skipped_not_literal: int
    parse_error: int
    error_files: tuple[str, ...]
    parsed_permissive: int = 0
    code_module: int = 0
    resolved_refs: int = 0
    unresolved_refs: int = 0

    @property
    def exit_code(self) -> int:
        return 3 if self.parse_error else 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_files": self.source_files,
            "parsed": self.parsed,
            "parsed_permissive": self.parsed_permissive,
            "code_module": self.code_module,
            "skipped": self.skipped_not_literal,
            "error": self.parse_error,
            "parse_error": self.parse_error,
            "error_files": list(self.error_files),
            "resolved_refs": self.resolved_refs,
            "unresolved_refs": self.unresolved_refs,
        }


def import_configs(source: Path | str, out: Path | str) -> ImportSummary:
    """Import all recursively matched ``*Config*.lua`` files from *source*."""

    source_path = Path(source)
    out_path = Path(out)
    if not source_path.is_dir():
        raise FileNotFoundError(f"source directory does not exist: {source_path}")

    files = sorted(
        (path for path in source_path.rglob("*Config*.lua") if path.is_file()),
        key=lambda path: path.relative_to(source_path).as_posix(),
    )
    out_path.mkdir(parents=True, exist_ok=True)

    manifest: list[dict[str, Any]] = []
    all_fields: list[str] = []
    seen_fields: set[str] = set()
    parsed_count = 0
    permissive_count = 0
    code_module_count = 0
    skipped_count = 0
    resolved_total = 0
    unresolved_total = 0
    error_files: list[str] = []
    symbols = SymbolTable(source_path)

    for path in files:
        relative = path.relative_to(source_path).as_posix()
        table_name = path.stem
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        entry: dict[str, Any] = {
            "file": relative,
            "table": table_name,
            "status": "parse_error",
            "reason": None,
            "sha256": digest,
            "row_count": 0,
            "field_count": 0,
            "fields": [],
            "permissive": False,
            "resolved_refs": 0,
            "unresolved_refs": 0,
            "code_module_reason": None,
        }

        rows: Any = None
        document: dict[str, Any] | None = None
        try:
            text = raw.decode("utf-8-sig")
            rows = parse_lua_return(text)
        except LuaNotLiteralError:
            outcome = _import_permissive(text, relative, table_name, digest, symbols)
            entry.update(outcome.entry)
            resolved_total += outcome.entry["resolved_refs"]
            unresolved_total += outcome.entry["unresolved_refs"]
            if outcome.document is None:
                if outcome.entry["status"] == "code_module":
                    code_module_count += 1
                else:
                    skipped_count += 1
            else:
                document = outcome.document
                rows = document["rows"]
                permissive_count += 1
        except (LuaLiteralError, UnicodeDecodeError) as exc:
            entry["reason"] = str(exc)
            error_files.append(relative)
        else:
            fields = _collect_fields(rows)
            document = {
                "schema": TABLE_SCHEMA,
                "table": table_name,
                "source_file": relative,
                "source_sha256": digest,
                "row_count": len(rows),
                "fields": fields,
                "rows": rows,
            }
            entry.update(
                status="parsed",
                row_count=len(rows),
                field_count=len(fields),
                fields=fields,
            )
            parsed_count += 1

        if document is not None:
            for field in document["fields"]:
                if field not in seen_fields:
                    seen_fields.add(field)
                    all_fields.append(field)
            _write_json(out_path / f"{table_name}.json", document)
        manifest.append(entry)

    seeds = _read_field_definitions(_find_field_definitions(source_path))
    dictionary: dict[str, str | None] = dict(seeds)
    for field in all_fields:
        dictionary.setdefault(field, None)

    _write_json(out_path / "_manifest.json", manifest)
    _write_json(out_path / "_field_dictionary.json", dictionary)
    return ImportSummary(
        source_files=len(files),
        parsed=parsed_count,
        skipped_not_literal=skipped_count,
        parse_error=len(error_files),
        error_files=tuple(error_files),
        parsed_permissive=permissive_count,
        code_module=code_module_count,
        resolved_refs=resolved_total,
        unresolved_refs=unresolved_total,
    )


@dataclass(frozen=True)
class _PermissiveOutcome:
    """Manifest fields and optional table document from a permissive import."""

    entry: dict[str, Any]
    document: dict[str, Any] | None


def _import_permissive(
    text: str,
    relative: str,
    table_name: str,
    digest: str,
    symbols: SymbolTable,
) -> _PermissiveOutcome:
    """Import a file that is not a plain ``return`` table literal.

    A file counts as data when it exports at least one non-empty top-level table
    containing a real literal; otherwise it is a code module and nothing is
    written.
    """

    try:
        scan = scan_module(text, symbols.resolve)
    except (LuaLiteralError, RecursionError) as exc:
        return _PermissiveOutcome(
            entry={"status": "skipped_not_literal", "reason": f"permissive scan failed: {exc}"},
            document=None,
        )

    exports = data_exports(scan)
    stats = scan.stats
    base: dict[str, Any] = {
        "permissive": True,
        "resolved_refs": stats.resolved_refs,
        "unresolved_refs": stats.unresolved_refs,
        "ignored_statements": [item.as_dict() for item in scan.ignored],
    }

    if not exports:
        first = scan.ignored[0] if scan.ignored else None
        reason = (
            f"no top-level data table; first non-data statement at line {first.line} "
            f"({first.kind}): {first.text}"
            if first
            else "no top-level data table and no statements recognised"
        )
        base.update(status="code_module", reason=None, code_module_reason=reason)
        return _PermissiveOutcome(entry=base, document=None)

    names = [table_name if name == RETURN_EXPORT else name for name in exports]
    values = list(exports.values())
    if len(values) == 1:
        rows: Any = values[0]
    else:
        rows = dict(zip(names, values))
    row_count = sum(len(value) for value in values)
    fields: list[str] = []
    seen: set[str] = set()
    for value in values:
        for name in _collect_permissive_fields(value):
            if name not in seen:
                seen.add(name)
                fields.append(name)

    document = {
        "schema": TABLE_SCHEMA,
        "table": table_name,
        "source_file": relative,
        "source_sha256": digest,
        "permissive": True,
        "exports": names,
        "row_count": row_count,
        "fields": fields,
        "rows": rows,
    }
    base.update(
        status="parsed_permissive",
        reason=None,
        row_count=row_count,
        field_count=len(fields),
        fields=fields,
        exports=names,
    )
    return _PermissiveOutcome(entry=base, document=document)


def _collect_fields(rows: Any) -> list[str]:
    fields: list[str] = []
    seen: set[str] = set()

    def add_record(record: Any) -> None:
        if not isinstance(record, dict):
            return
        for key in record:
            field = str(key)
            if field not in seen:
                seen.add(field)
                fields.append(field)

    if isinstance(rows, list):
        for row in rows:
            add_record(row)
    elif isinstance(rows, dict):
        add_record(rows)
    return fields


def _is_node(value: Any) -> bool:
    """Whether *value* is a reference or call placeholder rather than a record."""

    if not isinstance(value, dict):
        return False
    keys = set(value)
    return ("ref" in value and keys <= {"ref", "value"}) or (
        "call" in value and keys <= {"call", "args"}
    )


def _collect_permissive_fields(rows: Any) -> list[str]:
    """Collect record field names from a permissively imported table.

    Name maps (``Key = "value"`` enumerations) contribute no field names: their
    keys are data, not schema.  Reference and call placeholders contribute
    nothing either, so ``ref``/``call`` never leak into the field dictionary.
    """

    fields: list[str] = []
    seen: set[str] = set()

    def add_record(record: Any) -> None:
        if not isinstance(record, dict) or _is_node(record):
            return
        for key in record:
            field = str(key)
            if field not in seen:
                seen.add(field)
                fields.append(field)

    if isinstance(rows, list):
        for row in rows:
            add_record(row)
    elif isinstance(rows, dict) and not _is_node(rows):
        for value in rows.values():
            add_record(value)
    return fields


def _find_field_definitions(source: Path) -> Path | None:
    for directory in (source, *source.parents):
        candidate = directory / "lua_to_csv_converter.py"
        if candidate.is_file():
            return candidate
    return None


def _read_field_definitions(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    for node in tree.body:
        value_node: ast.expr | None = None
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "FIELD_DEFINITIONS"
            for target in node.targets
        ):
            value_node = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "FIELD_DEFINITIONS"
        ):
            value_node = node.value
        if value_node is not None:
            value = ast.literal_eval(value_node)
            if not isinstance(value, dict):
                raise ValueError("FIELD_DEFINITIONS must be a dictionary literal")
            return {str(key): str(description) for key, description in value.items()}
    return {}


@dataclass(frozen=True)
class FlatModuleSummary:
    """Counters for importing one non-``*Config*`` Lua data module."""

    table: str
    source_file: str
    source_sha256: str
    row_count: int
    columns: tuple[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "source_file": self.source_file,
            "source_sha256": self.source_sha256,
            "row_count": self.row_count,
            "columns": list(self.columns),
        }


def import_flat_module(
    source: Path | str,
    out: Path | str,
    *,
    key_column: str,
    value_column: str,
) -> FlatModuleSummary:
    """Import one Lua module whose ``return`` is a flat key/value map.

    The batch importer only matches ``*Config*.lua``.  Some tables the game
    reads locally are named after what they score rather than after the word
    Config -- ``ItemGenerateScore.lua`` is the one this project needed -- and
    their body is a map from an item type to a number, not a list of records.
    Naming the two columns turns that map into the same row shape every other
    imported table has, so a rule can look a row up by key without knowing
    which file it came from.
    """

    source_path = Path(source)
    out_path = Path(out)
    if not source_path.is_file():
        raise FileNotFoundError(f"source file does not exist: {source_path}")
    if key_column == value_column:
        raise ValueError("key and value columns must differ")
    raw = source_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    parsed = parse_lua_return(raw.decode("utf-8-sig"))
    if not isinstance(parsed, dict):
        raise LuaLiteralError(
            f"{source_path.name} does not return a flat map: {type(parsed).__name__}"
        )
    rows = [
        {key_column: str(key), value_column: value}
        for key, value in sorted(parsed.items(), key=lambda item: str(item[0]))
    ]
    document = {
        "schema": TABLE_SCHEMA,
        "table": source_path.stem,
        "source_file": source_path.name,
        "source_sha256": digest,
        "row_count": len(rows),
        "fields": [key_column, value_column],
        "rows": rows,
    }
    out_path.mkdir(parents=True, exist_ok=True)
    _write_json(out_path / f"{source_path.stem}.json", document)
    return FlatModuleSummary(
        table=source_path.stem,
        source_file=source_path.name,
        source_sha256=digest,
        row_count=len(rows),
        columns=(key_column, value_column),
    )


def _write_json(path: Path, value: Any) -> None:
    rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(rendered, encoding="utf-8", newline="\n")


def format_summary(summary: ImportSummary) -> str:
    lines = [
        f"parsed={summary.parsed} parsed_permissive={summary.parsed_permissive} "
        f"code_module={summary.code_module} skipped={summary.skipped_not_literal} "
        f"error={summary.parse_error}",
        f"refs resolved={summary.resolved_refs} unresolved={summary.unresolved_refs}",
    ]
    if summary.error_files:
        lines.append("error files:")
        lines.extend(f"- {path}" for path in summary.error_files)
    return "\n".join(lines)
