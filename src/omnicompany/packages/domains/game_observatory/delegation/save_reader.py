"""Read-only Romantic Restaurant save snapshots and controlled flush receipts.

The Lua parser in this module is deliberately static: it extracts table literals
and known one-argument config creators, then delegates literal parsing to the
existing config-import parser.  Decompiled source is never executed.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..config_import.lua_literal import LuaLiteralError, parse_lua_return


REPORT_SCHEMA = "game-observatory.save-channel-report.v1"
BOARD_WIDTH = 7
BOARD_HEIGHT = 9

Runner = Callable[[list[str]], subprocess.CompletedProcess[Any]]
FlushMethod = Literal["home_and_return", "pause_broadcast", "none"]
SaveChannelVerdict = Literal["viable", "viable_with_caveats", "not_viable"]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class DBColumnV1(_FrozenModel):
    name: str
    storage_type: Literal["text", "integer", "real"]
    source_refs: list[str]


class DBTableSchemaV1(_FrozenModel):
    logical_name: str
    name: str
    columns: dict[str, DBColumnV1]
    primary_key: str
    arr_order: list[str]
    source_refs: list[str]


class SchemaFailureV1(_FrozenModel):
    logical_name: str
    reason: str
    source_ref: str


class DBSchemaV1(_FrozenModel):
    lua_dir: str
    config_sha256: str
    tables: dict[str, DBTableSchemaV1]
    failures: list[SchemaFailureV1]


class SaveFileV1(_FrozenModel):
    remote_path: str
    local_path: str
    size_bytes: int = Field(ge=0)
    sha256: str
    pull_method: str


def _snapshot_content_hash(files: Sequence[SaveFileV1]) -> str:
    digest = hashlib.sha256()
    for item in sorted(files, key=lambda value: value.remote_path):
        digest.update(item.remote_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.sha256.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


class SaveSnapshotV1(_FrozenModel):
    captured_at: str
    destination: str
    serial: str
    package: str
    files: list[SaveFileV1]
    errors: list[str]
    elapsed_ms: float = Field(ge=0)
    content_sha256: str = ""

    @model_validator(mode="after")
    def fill_content_hash(self) -> SaveSnapshotV1:
        expected = _snapshot_content_hash(self.files)
        if self.content_sha256 and self.content_sha256 != expected:
            raise ValueError("content_sha256 does not match snapshot files")
        if not self.content_sha256:
            object.__setattr__(self, "content_sha256", expected)
        return self


class CoverageV1(_FrozenModel):
    decoded: bool
    reason: str | None = None
    source_refs: list[str] = Field(default_factory=list)


class BoardCellV1(_FrozenModel):
    type: str | int | None
    level: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class OrderRequirementV1(_FrozenModel):
    item_type: str
    count: int = Field(ge=1)


class OrderRowV1(_FrozenModel):
    slot: int = Field(ge=1)
    order_id: str
    type: int | None
    requirements: list[OrderRequirementV1]
    avatar_id: int | None


class TableTimestampV1(_FrozenModel):
    last_serialized_epoch: int | None
    last_serialized_at: str | None
    modified: bool | None


class GameStateV1(_FrozenModel):
    snapshot_sha256: str
    energy: int | None
    # ``Energy.generateTime`` is the epoch second the last recovery tick was
    # accounted for; EnergyModel restores one point per 120 s from it.
    energy_generate_time: int | None = None
    energy_infinite_time: int | None = None
    coins: int | None
    gems: int | None
    level: int | None
    xp: int | None
    board: list[list[BoardCellV1 | None]] | None
    orders: list[OrderRowV1 | dict[str, Any]] | None
    main_task: dict[str, Any] | None
    order_meta: dict[str, Any] | None = None
    activity: dict[str, Any] | None = None
    # MainBoardModel.ItemCacheData.  Keep every row: the board executor uses
    # the authoritative count and Lua's type/id ordering when claiming one.
    cache_items: list[dict[str, Any]] | None = None
    # 2026-09-17.  The shop tables, raw: ``shop`` carries energyBuyCost (the
    # gem price of the next energy purchase, doubling each time in a day) and
    # ``shop_items`` the offers on the shelf (itemCodeStr, costCount, leftCount,
    # shopType 1 = daily specials, 2 = hot selling; display order is id order).
    shop: dict[str, Any] | None = None
    shop_items: list[dict[str, Any]] | None = None
    timestamps: dict[str, TableTimestampV1]
    coverage: dict[str, CoverageV1]
    decode_ms: float = Field(ge=0)


class CommandReceiptV1(_FrozenModel):
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    elapsed_ms: float = Field(default=0.0, ge=0)
    started_at_epoch: float = Field(default=0.0, ge=0)


class FlushReceiptV1(_FrozenModel):
    method: FlushMethod
    before_foreground_activity: str | None
    background_activity: str | None
    after_foreground_activity: str | None
    elapsed_ms: float = Field(ge=0)
    commands: list[CommandReceiptV1]


class SaveChannelReportV1(_FrozenModel):
    schema_name: Literal[REPORT_SCHEMA] = Field(default=REPORT_SCHEMA, alias="schema")
    created_at: datetime
    serial: str
    package: str
    evidence_dir: str
    schema_summary: dict[str, Any]
    source_refs: dict[str, list[str]]
    snapshots: list[SaveSnapshotV1]
    initial_state: GameStateV1 | None
    final_state: GameStateV1 | None
    flush_receipt: FlushReceiptV1 | None
    flush_latency_seconds: float | None = Field(alias="flush_latency_s")
    changed_fields: list[str]
    generator_actions: int = Field(ge=0, le=3)
    generator_action_receipt: dict[str, Any] | None = None
    replay_run_id: str | None = None
    sample_cost: dict[str, int | float | None]
    semantic_coverage: dict[str, CoverageV1]
    verdict: SaveChannelVerdict
    verdict_reason: str
    errors: list[str]


class SaveReaderError(RuntimeError):
    """Base error for snapshot, decode, and flush operations."""


class FlushError(SaveReaderError):
    """A controlled flush command failed before the recovery step."""


class FlushRecoveryError(FlushError):
    """The game could not be restored to the foreground after a flush attempt."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_runner(args: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(args, capture_output=True, check=False, timeout=120)


def _output_bytes(value: str | bytes | None) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    return value.encode("utf-8")


def _output_text(value: str | bytes | None) -> str:
    return _output_bytes(value).decode("utf-8", "replace")


def _call(runner: Runner, args: Sequence[str | Path]) -> subprocess.CompletedProcess[Any]:
    return runner([str(value) for value in args])


def _timed_call(
    runner: Runner,
    args: Sequence[str | Path],
    commands: list[CommandReceiptV1],
) -> subprocess.CompletedProcess[Any]:
    """Run one adb command and record how long its round trip took."""

    started_at = time.time()
    started = time.perf_counter_ns()
    completed = _call(runner, args)
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    commands.append(_receipt(args, completed, elapsed_ms, started_at))
    return completed


def _receipt(
    args: Sequence[str | Path],
    completed: subprocess.CompletedProcess[Any],
    elapsed_ms: float = 0.0,
    started_at_epoch: float = 0.0,
) -> CommandReceiptV1:
    def compact(value: str | bytes | None) -> str:
        text = _output_text(value)
        if len(text) <= 4096:
            return text
        omitted = len(text) - 4096
        return f"{text[:2048]}\n... <{omitted} chars omitted> ...\n{text[-2048:]}"

    return CommandReceiptV1(
        argv=[str(value) for value in args],
        returncode=completed.returncode,
        stdout=compact(completed.stdout),
        stderr=compact(completed.stderr),
        elapsed_ms=max(elapsed_ms, 0.0),
        started_at_epoch=max(started_at_epoch, 0.0),
    )


def _remote_candidates(package: str) -> dict[str, list[str]]:
    internal_roots = (f"/data/data/{package}", f"/data/user/0/{package}")
    external_roots = (
        f"/sdcard/Android/data/{package}/files",
        f"/storage/emulated/0/Android/data/{package}/files",
    )
    preferences = f"{package}.v2.playerprefs.xml"
    return {
        "database": [
            *(f"{root}/db.sqlite3" for root in external_roots),
            *(f"{root}/files/db.sqlite3" for root in internal_roots),
        ],
        "database-wal": [
            *(f"{root}/db.sqlite3-wal" for root in external_roots),
            *(f"{root}/files/db.sqlite3-wal" for root in internal_roots),
        ],
        "database-shm": [
            *(f"{root}/db.sqlite3-shm" for root in external_roots),
            *(f"{root}/files/db.sqlite3-shm" for root in internal_roots),
        ],
        "database-journal": [
            *(f"{root}/db.sqlite3-journal" for root in external_roots),
            *(f"{root}/files/db.sqlite3-journal" for root in internal_roots),
        ],
        "json-save": [
            *(f"{root}/DBTableManager.dat" for root in external_roots),
            *(f"{root}/files/DBTableManager.dat" for root in internal_roots),
        ],
        "playerprefs": [
            *(f"{root}/shared_prefs/{preferences}" for root in internal_roots),
        ],
    }


OPTIONAL_LOGICAL_NAMES = ("database-wal", "database-shm", "database-journal")


class SavePullPlan(BaseModel):
    """What the previous pull learned, so the next one stops probing blind.

    A cold pull issues 26 ``adb`` invocations for this game: ten ``run-as``
    probes that always fail on the release build, ``adb root`` plus
    ``wait-for-device``, then twelve sibling probes for the WAL/SHM/journal
    that do not exist.  Only two calls move bytes.

    A warm pull asks once for each file the cold pull actually captured and
    nothing else, which is two calls: the database and the PlayerPrefs XML.
    Skipping the sibling probes is safe because the container is a rollback
    journal database - ``file_format_write_version`` and ``read_version`` are
    both 1 and ``PRAGMA journal_mode`` reads ``delete`` in the 2026-09-05 and
    2026-09-06 copies alike - so ``-wal`` and ``-shm`` cannot exist, and a
    ``-journal`` only exists while a write transaction is open, which the
    HOME pause has already ended.  Every recorded read agrees: 46 of 46 report
    ``wal_applied`` false and no snapshot ever captured a sibling.

    ``absent`` is only ever filled by a cold scan that looked.  Any fallback,
    and any decrypted header that turns out to be in WAL mode, clears the whole
    memo so the next pull looks again.
    """

    model_config = ConfigDict(extra="forbid")

    method: Literal["run-as", "adb-root"] | None = None
    remote_paths: dict[str, str] = Field(default_factory=dict)
    absent: list[str] = Field(default_factory=list)
    last_adb_calls: int = Field(default=0, ge=0)
    last_used_fast_path: bool = False

    def forget(self) -> None:
        self.method = None
        self.remote_paths = {}
        self.absent = []


def _planned_candidates(
    logical_name: str,
    candidates: dict[str, list[str]],
    plan: SavePullPlan | None,
) -> list[str]:
    """Narrow one logical file's candidates to what the plan already proved."""

    if plan is None:
        return candidates[logical_name]
    known = plan.remote_paths.get(logical_name)
    if known:
        return [known]
    anchor = plan.remote_paths.get("database") or plan.remote_paths.get("json-save")
    if anchor:
        root = anchor.rsplit("/", 1)[0]
        narrowed = [
            path for path in candidates[logical_name] if path.rsplit("/", 1)[0] == root
        ]
        if narrowed:
            return narrowed
    return candidates[logical_name]


def _pull_one(
    adb: str | Path,
    serial: str,
    package: str,
    remote_path: str,
    *,
    method: Literal["run-as", "adb-root"],
    runner: Runner,
) -> bytes | None:
    if method == "run-as":
        args = [adb, "-s", serial, "exec-out", "run-as", package, "cat", remote_path]
    else:
        args = [adb, "-s", serial, "exec-out", "cat", remote_path]
    completed = _call(runner, args)
    if completed.returncode != 0:
        return None
    content = _output_bytes(completed.stdout)
    # On some Android/adb combinations ``exec-out`` exits zero even when the
    # remote command fails.  Never persist its diagnostic text as save bytes.
    diagnostic = content.strip().lower()
    if len(diagnostic) <= 4096 and (
        diagnostic.startswith((b"run-as:", b"cat:", b"sh:"))
        or b"package not debuggable" in diagnostic
        or b"permission denied" in diagnostic
        or b"no such file or directory" in diagnostic
        or b"inaccessible or not found" in diagnostic
    ):
        return None
    return content or None


def pull_save_snapshot(
    adb: str | Path,
    serial: str,
    package: str,
    dest_dir: str | Path,
    *,
    runner: Runner = _default_runner,
    plan: SavePullPlan | None = None,
) -> SaveSnapshotV1:
    """Copy game-owned SQLite/PlayerPrefs bytes without changing their originals.

    ``plan`` is an optional memo of the previous successful pull.  When it
    already knows the transfer method and the winning remote paths the pull
    asks once per logical file; if that fast attempt does not reproduce the
    same captured set the memo is dropped and the full cold scan runs, so the
    resulting snapshot is identical either way.
    """

    started = time.perf_counter_ns()
    destination = Path(dest_dir)
    destination.mkdir(parents=True, exist_ok=True)
    candidates = _remote_candidates(package)
    files: list[SaveFileV1] = []
    errors: list[str] = []
    captured: set[str] = set()
    adb_calls = 0

    def counting_runner(args: list[str]) -> subprocess.CompletedProcess[Any]:
        nonlocal adb_calls
        adb_calls += 1
        return runner(args)

    def capture(
        method: Literal["run-as", "adb-root"],
        *,
        active_plan: SavePullPlan | None = None,
    ) -> None:
        for logical_name, remote_paths in candidates.items():
            if logical_name in captured:
                continue
            if logical_name.startswith("database-") and "database" not in captured:
                continue
            if logical_name == "json-save" and "database" in captured:
                continue
            if logical_name == "database" and "json-save" in captured:
                continue
            if active_plan is not None and logical_name in active_plan.absent:
                continue
            attempts = (
                _planned_candidates(logical_name, candidates, active_plan)
                if active_plan is not None
                else remote_paths
            )
            for remote_path in attempts:
                content = _pull_one(
                    adb,
                    serial,
                    package,
                    remote_path,
                    method=method,
                    runner=counting_runner,
                )
                if content is None:
                    continue
                local_path = destination / Path(remote_path).name
                local_path.write_bytes(content)
                files.append(
                    SaveFileV1(
                        remote_path=remote_path,
                        local_path=str(local_path),
                        size_bytes=len(content),
                        sha256=hashlib.sha256(content).hexdigest(),
                        pull_method=method,
                    )
                )
                captured.add(logical_name)
                break

    def essentials_captured() -> bool:
        return bool({"database", "json-save"} & captured) and "playerprefs" in captured

    used_fast_path = False
    if plan is not None and plan.method is not None:
        capture(plan.method, active_plan=plan)
        if essentials_captured():
            used_fast_path = True
        else:
            files = []
            captured = set()
            plan.forget()

    if not used_fast_path:
        capture("run-as")
        if not essentials_captured():
            root_args = [adb, "-s", serial, "root"]
            root_result = _call(counting_runner, root_args)
            if root_result.returncode == 0:
                wait_args = [adb, "-s", serial, "wait-for-device"]
                wait_result = _call(counting_runner, wait_args)
                if wait_result.returncode == 0:
                    capture("adb-root")
                else:
                    errors.append(
                        f"adb wait-for-device failed: {_output_text(wait_result.stderr)}"
                    )
            else:
                errors.append(f"adb root failed: {_output_text(root_result.stderr)}")

    if not ({"database", "json-save"} & captured):
        errors.append("game save missing: db.sqlite3 and DBTableManager.dat were unreadable")
    if "playerprefs" not in captured:
        errors.append("game PlayerPrefs XML was unreadable")

    if plan is not None:
        plan.last_adb_calls = adb_calls
        plan.last_used_fast_path = used_fast_path
        methods = {item.pull_method for item in files}
        if essentials_captured() and methods <= {"run-as", "adb-root"} and len(methods) == 1:
            plan.method = files[0].pull_method  # type: ignore[assignment]
            plan.remote_paths = {
                logical_name: item.remote_path
                for item in files
                for logical_name, paths in candidates.items()
                if item.remote_path in paths
            }
            if not used_fast_path:
                # Only a scan that actually looked may record an absence.
                plan.absent = [
                    logical_name
                    for logical_name in OPTIONAL_LOGICAL_NAMES
                    if logical_name not in plan.remote_paths
                ]
        else:
            plan.forget()

    return SaveSnapshotV1(
        captured_at=_utc_now(),
        destination=str(destination),
        serial=serial,
        package=package,
        files=files,
        errors=errors,
        elapsed_ms=(time.perf_counter_ns() - started) / 1_000_000,
    )


def _balanced_table(source: str, brace: int) -> tuple[str, int]:
    depth = 0
    quote = ""
    escaped = False
    index = brace
    while index < len(source):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in {'"', "'"}:
            quote = char
        elif source.startswith("--", index):
            newline = source.find("\n", index)
            index = len(source) if newline < 0 else newline
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[brace : index + 1], index + 1
        index += 1
    raise ValueError("unterminated Lua table")


def _assignment_table(source: str, name: str) -> tuple[str, int]:
    match = re.search(rf"\b{re.escape(name)}\s*=\s*\{{", source)
    if not match:
        raise ValueError(f"assignment {name} not found")
    brace = source.find("{", match.start())
    literal, _end = _balanced_table(source, brace)
    return literal, source.count("\n", 0, brace) + 1


def _split_entries(table_literal: str) -> list[tuple[str, str, int]]:
    body = table_literal[1:-1]
    starts: list[int] = [0]
    depth = 0
    quote = ""
    escaped = False
    index = 0
    while index < len(body):
        char = body[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in {'"', "'"}:
            quote = char
        elif body.startswith("--", index):
            newline = body.find("\n", index)
            index = len(body) if newline < 0 else newline
        elif char in "{([":
            depth += 1
        elif char in "})]":
            depth -= 1
        elif char == "," and depth == 0:
            starts.append(index + 1)
        index += 1
    starts.append(len(body) + 1)

    entries: list[tuple[str, str, int]] = []
    for start, end in zip(starts, starts[1:]):
        raw = body[start : end - 1].strip()
        match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\Z", raw, re.DOTALL)
        if not match:
            continue
        line_offset = body.count("\n", 0, start)
        entries.append((match.group(1), match.group(2).strip(), line_offset))
    return entries


def _literalize_types(source: str, value_types: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        value = value_types.get(match.group(1))
        if value is None:
            raise ValueError(f"unknown EDBValueType.{match.group(1)}")
        return json.dumps(value)

    return re.sub(r"EDBValueType\.([A-Za-z_][A-Za-z0-9_]*)", replace, source)


def _creator_tables(source: str) -> dict[str, tuple[str, int]]:
    creators: dict[str, tuple[str, int]] = {}
    pattern = re.compile(
        r"local\s+([A-Za-z_][A-Za-z0-9_]*ConfigCreator)\s*=\s*function\(name\)"
    )
    for match in pattern.finditer(source):
        returned = re.search(r"\breturn\s*\{", source[match.end() :])
        if not returned:
            continue
        brace = match.end() + returned.start() + returned.group(0).rfind("{")
        literal, _end = _balanced_table(source, brace)
        creators[match.group(1)] = (literal, source.count("\n", 0, brace) + 1)
    return creators


def _column_refs(
    literal: str,
    *,
    source_name: str,
    start_line: int,
    columns: dict[str, Any],
) -> dict[str, list[str]]:
    refs: dict[str, list[str]] = {}
    for column in columns:
        match = re.search(rf"(?m)^\s*{re.escape(column)}\s*=", literal)
        line = start_line + (literal.count("\n", 0, match.start()) if match else 0)
        refs[column] = [f"{source_name}:{line}"]
    return refs


def load_db_schema_from_decompile(lua_dir: str | Path) -> DBSchemaV1:
    """Statically decode ``DBTableConfigs.lua`` into SQLite table contracts."""

    directory = Path(lua_dir)
    config_path = directory / "DBTableConfigs.lua"
    db_table_path = directory / "DBTable.lua"
    source = config_path.read_text(encoding="utf-8-sig")
    db_table_source = db_table_path.read_text(encoding="utf-8-sig")
    value_type_literal, _value_line = _assignment_table(source, "EDBValueType")
    parsed_types = parse_lua_return("return " + value_type_literal)
    if not isinstance(parsed_types, dict):
        raise ValueError("EDBValueType did not decode to a mapping")
    value_types = {str(key): str(value) for key, value in parsed_types.items()}

    config_literal, config_line = _assignment_table(source, "EDBTableConfigs")
    creators = _creator_tables(source)
    tables: dict[str, DBTableSchemaV1] = {}
    failures: list[SchemaFailureV1] = []
    default_ref = "DBTable.lua:1-5" if len(db_table_source.splitlines()) >= 5 else "DBTable.lua:1"

    for logical_name, raw_value, line_offset in _split_entries(config_literal):
        invocation_line = config_line + line_offset
        source_refs = [f"DBTableConfigs.lua:{invocation_line}"]
        literal = raw_value
        literal_line = invocation_line
        creator_match = re.fullmatch(
            r"([A-Za-z_][A-Za-z0-9_]*ConfigCreator)\(\s*([\"'])(.*?)\2\s*\)",
            raw_value,
            re.DOTALL,
        )
        if creator_match:
            creator = creators.get(creator_match.group(1))
            if creator is None:
                failures.append(
                    SchemaFailureV1(
                        logical_name=logical_name,
                        reason=f"creator {creator_match.group(1)} not statically available",
                        source_ref=source_refs[0],
                    )
                )
                continue
            literal, literal_line = creator
            literal = re.sub(
                r"(\bname\s*=\s*)name\b",
                lambda match: match.group(1) + json.dumps(creator_match.group(3)),
                literal,
                count=1,
            )
            source_refs.append(f"DBTableConfigs.lua:{literal_line}")
        try:
            parsed = parse_lua_return("return " + _literalize_types(literal, value_types))
            if not isinstance(parsed, dict) or not isinstance(parsed.get("name"), str):
                raise ValueError("table config needs a string name")
            raw_columns = parsed.get("columns")
            if raw_columns is None:
                raw_columns = {"key": "text", "value": "text"}
                column_refs = {"key": [default_ref], "value": [default_ref]}
                source_refs.append(default_ref)
            elif isinstance(raw_columns, dict):
                column_refs = _column_refs(
                    literal,
                    source_name="DBTableConfigs.lua",
                    start_line=literal_line,
                    columns=raw_columns,
                )
            else:
                raise ValueError("columns must be a mapping")
            columns = {
                str(name): DBColumnV1(
                    name=str(name),
                    storage_type=str(storage_type),
                    source_refs=column_refs[str(name)],
                )
                for name, storage_type in raw_columns.items()
            }
            primary_key = str(parsed.get("primaryKey", "key"))
            arr_order_value = parsed.get("arrOrder", list(columns))
            if not isinstance(arr_order_value, list):
                raise ValueError("arrOrder must be an array")
            tables[logical_name] = DBTableSchemaV1(
                logical_name=logical_name,
                name=parsed["name"],
                columns=columns,
                primary_key=primary_key,
                arr_order=[str(value) for value in arr_order_value],
                source_refs=source_refs,
            )
        except (LuaLiteralError, TypeError, ValueError) as exc:
            failures.append(
                SchemaFailureV1(
                    logical_name=logical_name,
                    reason=f"{type(exc).__name__}: {exc}",
                    source_ref=source_refs[0],
                )
            )

    return DBSchemaV1(
        lua_dir=str(directory),
        config_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        tables=tables,
        failures=failures,
    )


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sqlite_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _find_game_database(
    snapshot: SaveSnapshotV1, schema: DBSchemaV1
) -> tuple[Path | None, str | None]:
    expected = {table.name for table in schema.tables.values()}
    best: tuple[int, Path] | None = None
    for item in snapshot.files:
        path = Path(item.local_path)
        try:
            with path.open("rb") as stream:
                if stream.read(16) != b"SQLite format 3\x00":
                    continue
            uri = f"file:{path.resolve().as_posix()}?mode=ro"
            with sqlite3.connect(uri, uri=True) as connection:
                score = len(_sqlite_tables(connection) & expected)
            if best is None or score > best[0]:
                best = (score, path)
        except (OSError, sqlite3.Error):
            continue
    unreadable: list[dict[str, Any]] = []
    for item in snapshot.files:
        if Path(item.remote_path).name not in {"db.sqlite3", "DBTableManager.dat"}:
            continue
        path = Path(item.local_path)
        try:
            with path.open("rb") as stream:
                header = stream.read(32).hex()
        except OSError as exc:
            header = f"unreadable:{exc}"
        unreadable.append(
            {"remote_path": item.remote_path, "length": item.size_bytes, "header_hex": header}
        )
    binary_evidence = json.dumps(unreadable, ensure_ascii=False, sort_keys=True)
    if best is None:
        return None, f"no readable SQLite file in snapshot; container_evidence={binary_evidence}"
    if best[0] == 0:
        return None, (
            "SQLite files did not contain any decompiled game tables; "
            f"container_evidence={binary_evidence}"
        )
    return best[1], None


def _rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    cursor = connection.execute(f"SELECT * FROM {_quote_identifier(table)}")
    return [dict(row) for row in cursor.fetchall()]


def _table(schema: DBSchemaV1, logical_name: str) -> DBTableSchemaV1 | None:
    return schema.tables.get(logical_name)


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _key_value(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        str(row["key"]): row.get("value")
        for row in rows
        if row.get("key") is not None
    }


def _decode_activity(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Unwrap the Activity table's ``{name, data}`` rows into plain per-activity maps.

    Each ``data`` blob is a JSON object whose values are themselves wrapped as
    ``{"value": ...}``; nested JSON strings are decoded one level so callers can
    read fields such as ``CurConfigs`` without re-parsing.
    """

    decoded: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = row.get("name")
        if name is None:
            continue
        raw = row.get("data")
        if not isinstance(raw, str):
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        fields: dict[str, Any] = {}
        for key, wrapper in payload.items():
            value = (
                wrapper.get("value")
                if isinstance(wrapper, dict) and "value" in wrapper
                else wrapper
            )
            if isinstance(value, str):
                stripped = value.strip()
                if stripped[:1] in {"{", "["}:
                    try:
                        value = json.loads(stripped)
                    except json.JSONDecodeError:
                        pass
            fields[str(key)] = value
        decoded[str(name)] = fields
    return decoded


def _decode_orders(rows: list[dict[str, Any]]) -> list[OrderRowV1]:
    decoded: list[OrderRowV1] = []
    for ordinal, row in enumerate(rows, start=1):
        raw_slot = _as_int(row.get("slot"))
        slot = ordinal if raw_slot is None or raw_slot < 1 else raw_slot
        raw_order_id = row.get("id")
        order_id = str(slot if raw_order_id is None else raw_order_id)
        item_types: list[str] = []
        for index in range(1, 4):
            value = row.get(f"requirement{index}Str")
            if value in {None, ""}:
                value = row.get(f"requirement{index}")
            if value not in {None, "", 0, "0"}:
                item_types.append(str(value))
        requirements = [
            OrderRequirementV1(item_type=item_type, count=item_types.count(item_type))
            for item_type in dict.fromkeys(item_types)
        ]
        decoded.append(
            OrderRowV1(
                slot=slot,
                order_id=order_id,
                type=_as_int(row.get("type")),
                requirements=requirements,
                avatar_id=_as_int(row.get("avatarId")),
            )
        )
    return sorted(decoded, key=lambda item: item.slot)


def _property_total(values: dict[str, Any], key: str) -> tuple[int | None, str | None]:
    names = (f"{key}_Give", f"{key}_Buy")
    present = [name for name in names if name in values]
    if not present:
        return None, f"missing user keys: {names[0]}, {names[1]}"
    parsed = [_as_int(values[name]) for name in present]
    if any(value is None for value in parsed):
        return None, f"non-integer value in user keys: {', '.join(present)}"
    return sum(value for value in parsed if value is not None), None


def _player_prefs(snapshot: SaveSnapshotV1) -> tuple[dict[str, Any], str | None]:
    candidate = next(
        (
            Path(item.local_path)
            for item in snapshot.files
            if item.remote_path.endswith(".playerprefs.xml")
        ),
        None,
    )
    if candidate is None:
        return {}, "PlayerPrefs XML absent"
    try:
        root = ET.parse(candidate).getroot()
    except (OSError, ET.ParseError) as exc:
        return {}, f"PlayerPrefs XML unreadable: {exc}"
    values: dict[str, Any] = {}
    for element in root:
        raw_name = element.attrib.get("name")
        if raw_name is None:
            continue
        name = unquote(raw_name)
        raw_value = element.attrib.get("value", element.text or "")
        if element.tag in {"int", "long"}:
            values[name] = _as_int(raw_value)
        elif element.tag == "float":
            try:
                values[name] = float(raw_value)
            except ValueError:
                values[name] = raw_value
        elif element.tag == "boolean":
            values[name] = raw_value.lower() == "true"
        else:
            values[name] = raw_value
    return values, None


def _timestamps(preferences: dict[str, Any]) -> dict[str, TableTimestampV1]:
    prefix = "LastSerializationTimestamp_"
    modified_prefix = "TableModified_"
    names = {
        key[len(prefix) :]
        for key in preferences
        if key.startswith(prefix)
    } | {
        key[len(modified_prefix) :]
        for key in preferences
        if key.startswith(modified_prefix)
    }
    result: dict[str, TableTimestampV1] = {}
    for name in sorted(names):
        epoch = _as_int(preferences.get(prefix + name))
        try:
            timestamp = (
                datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")
                if epoch is not None
                else None
            )
        except (OverflowError, OSError, ValueError):
            timestamp = None
        modified_value = _as_int(preferences.get(modified_prefix + name))
        result[name] = TableTimestampV1(
            last_serialized_epoch=epoch,
            last_serialized_at=timestamp,
            modified=None if modified_value is None else modified_value != 0,
        )
    return result


def _decode_board(
    board_rows: list[dict[str, Any]], item_rows: list[dict[str, Any]]
) -> tuple[list[list[BoardCellV1 | None]], str | None]:
    runtime_fields = {
        "spreadItemRestNumber",
        "spreadStorageRestNumber",
        "spreadState",
        "spreadStartTimer",
    }
    item_by_id = {str(row.get("id")): row for row in item_rows if row.get("id") is not None}
    board: list[list[BoardCellV1 | None]] = [
        [BoardCellV1(type=None, extra={"empty": True}) for _x in range(BOARD_WIDTH)]
        for _y in range(BOARD_HEIGHT)
    ]
    problems: list[str] = []
    for row in board_rows:
        key = str(row.get("id", ""))
        match = re.fullmatch(r"(\d+)_(\d+)", key)
        if not match:
            problems.append(f"invalid board key {key!r}")
            continue
        x, y = int(match.group(1)), int(match.group(2))
        if not (1 <= x <= BOARD_WIDTH and 1 <= y <= BOARD_HEIGHT):
            problems.append(f"board key outside 7x9 bounds: {key}")
            continue
        item_id = str(row.get("itemId"))
        item = item_by_id.get(item_id)
        if item is None:
            problems.append(f"board item {item_id!r} missing from item table")
            cell = BoardCellV1(type=None, extra={"item_id": item_id})
        else:
            item_type = item.get("codeStr")
            if item_type in {None, ""}:
                item_type = item.get("code")
            extra = {
                key: value
                for key, value in item.items()
                if key not in {"id", "code", "codeStr"}
                and (key in runtime_fields or value not in {None, "", -1})
            }
            extra["item_id"] = item_id
            cell = BoardCellV1(type=item_type, extra=extra)
        board[y - 1][x - 1] = cell
    return board, "; ".join(problems) or None


# The nine logical tables this decoder actually consumes.  The save carries 45
# and ``config`` alone is a single 140 KB row, so reading every present table
# costs about five times what the decoded subset costs and yields nothing that
# reaches ``GameStateV1``.
DECODED_TABLES: tuple[str, ...] = (
    "Energy",
    "User",
    "Board",
    "Item",
    "Order",
    "Task",
    "OrderMeta",
    "Activity",
    "CacheItem",
    "Shop",
    "ShopItem",
)


def decode_snapshot(snapshot: SaveSnapshotV1, schema: DBSchemaV1) -> GameStateV1:
    """Decode the stable semantic subset; explain every unavailable field."""

    started = time.perf_counter_ns()
    preferences, prefs_error = _player_prefs(snapshot)
    timestamps = _timestamps(preferences)
    coverage: dict[str, CoverageV1] = {}
    database, database_error = _find_game_database(snapshot, schema)
    table_rows: dict[str, list[dict[str, Any]]] = {}

    if database is not None:
        uri = f"file:{database.resolve().as_posix()}?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True) as connection:
                available = _sqlite_tables(connection)
                for logical_name in DECODED_TABLES:
                    table = schema.tables.get(logical_name)
                    if table is not None and table.name in available:
                        table_rows[logical_name] = _rows(connection, table.name)
        except sqlite3.Error as exc:
            database_error = f"SQLite decode failed: {exc}"

    energy: int | None = None
    energy_generate_time: int | None = None
    energy_infinite_time: int | None = None
    energy_table = _table(schema, "Energy")
    energy_refs = ["DBTableConfigs.lua:149-164", "EnergyModel.lua:1-6,65-72"]
    energy_recovery_refs = [
        "EnergyModel.lua:4-8,110-120,182-206",
        "DBTableConfigs.lua:149-164",
    ]
    coverage["energy_recovery"] = CoverageV1(
        decoded=False,
        reason="Energy table was not decoded",
        source_refs=energy_recovery_refs,
    )
    if energy_table is None:
        coverage["energy"] = CoverageV1(
            decoded=False, reason="schema has no Energy table", source_refs=energy_refs
        )
    elif "Energy" not in table_rows:
        coverage["energy"] = CoverageV1(
            decoded=False,
            reason=database_error or f"SQLite table {energy_table.name!r} absent",
            source_refs=energy_refs,
        )
    else:
        row = next(
            (item for item in table_rows["Energy"] if str(item.get("id")) == "1"),
            table_rows["Energy"][0] if table_rows["Energy"] else None,
        )
        energy = _as_int(row.get("energyValue")) if row else None
        energy_generate_time = _as_int(row.get("generateTime")) if row else None
        energy_infinite_time = _as_int(row.get("infiniteTime")) if row else None
        coverage["energy"] = CoverageV1(
            decoded=energy is not None,
            reason=None if energy is not None else "energy row/value missing",
            source_refs=energy_refs,
        )
        coverage["energy_recovery"] = CoverageV1(
            decoded=energy_generate_time is not None,
            reason=(
                None
                if energy_generate_time is not None
                else "Energy.generateTime missing; recovery time cannot be derived"
            ),
            source_refs=energy_recovery_refs,
        )

    user_refs = [
        "DBTableConfigs.lua:133",
        "DBTable.lua:1-5",
        "UserModel.lua:47-60",
        "PropertyDataDefinition.lua:4-13",
        "PropertyDataManager.lua:261-287",
    ]
    user_values = _key_value(table_rows.get("User", []))
    coins, coins_reason = _property_total(user_values, "gold")
    gems, gems_reason = _property_total(user_values, "gem")
    xp, xp_reason = _property_total(user_values, "exp")
    for name, value, reason in (
        ("coins", coins, coins_reason),
        ("gems", gems, gems_reason),
        ("xp", xp, xp_reason),
    ):
        if not user_values:
            reason = database_error or "SQLite user table absent or empty"
        coverage[name] = CoverageV1(
            decoded=value is not None, reason=reason, source_refs=user_refs
        )

    level = _as_int(user_values.get("User_Level"))
    level_refs = ["UserDataKey.lua:16", "LevelModel.lua:4-18", "UserDataKey.lua:92"]
    level_reason: str | None = None
    if level is None:
        level = _as_int(preferences.get("LevelForCSharp"))
        if level is None:
            level_reason = "User_Level and PlayerPrefs LevelForCSharp are missing/non-integer"
    coverage["level"] = CoverageV1(
        decoded=level is not None, reason=level_reason, source_refs=level_refs
    )

    board: list[list[BoardCellV1 | None]] | None = None
    board_refs = [
        "DBTableConfigs.lua:11-57,165-166",
        "BaseSceneBoardModel.lua:3-4",
        "BoardPosition.lua:46-55",
        "SceneItemLayerModel.lua:35-45",
    ]
    if _table(schema, "Board") is None or _table(schema, "Item") is None:
        coverage["board"] = CoverageV1(
            decoded=False,
            reason="schema requires both Board and Item tables",
            source_refs=board_refs,
        )
    elif "Board" not in table_rows or "Item" not in table_rows:
        coverage["board"] = CoverageV1(
            decoded=False,
            reason=database_error or "SQLite board/item table absent",
            source_refs=board_refs,
        )
    else:
        board, board_reason = _decode_board(table_rows["Board"], table_rows["Item"])
        coverage["board"] = CoverageV1(
            decoded=board_reason is None, reason=board_reason, source_refs=board_refs
        )

    order_refs = ["DBTableConfigs.lua:59-120,319", "MainBoardModel.lua:12-13"]
    order_rows = table_rows.get("Order")
    orders = _decode_orders(order_rows) if order_rows is not None else None
    coverage["orders"] = CoverageV1(
        decoded=orders is not None,
        reason=None if orders is not None else database_error or "SQLite orders table absent",
        source_refs=order_refs,
    )

    task_refs = ["DBTableConfigs.lua:136", "MainTaskModel.lua:8-16,47-58,84-86"]
    task_rows = table_rows.get("Task")
    main_task = _key_value(task_rows) if task_rows is not None else None
    coverage["main_task"] = CoverageV1(
        decoded=main_task is not None,
        reason=None if main_task is not None else database_error or "SQLite mainTask table absent",
        source_refs=task_refs,
    )

    order_meta_refs = ["DBTableConfigs.lua:137", "DBTable.lua:1-5"]
    order_meta_rows = table_rows.get("OrderMeta")
    order_meta = _key_value(order_meta_rows) if order_meta_rows is not None else None
    coverage["order_meta"] = CoverageV1(
        decoded=order_meta is not None,
        reason=(
            None
            if order_meta is not None
            else database_error or "SQLite orderMeta table absent"
        ),
        source_refs=order_meta_refs,
    )

    activity_refs = ["DBTableConfigs.lua Activity{name,data}", "ActivityManager.lua"]
    activity_rows = table_rows.get("Activity")
    activity = _decode_activity(activity_rows) if activity_rows is not None else None
    coverage["activity"] = CoverageV1(
        decoded=activity is not None,
        reason=(
            None
            if activity is not None
            else database_error or "SQLite activity table absent"
        ),
        source_refs=activity_refs,
    )

    cache_item_refs = [
        "DBTableConfigs.lua:66-87",
        "MainBoardModel.lua:10,828-835",
        "ItemCacheModel.lua:1-4,21-23,65-70,91-111",
    ]
    cache_item_rows = table_rows.get("CacheItem")
    cache_items = (
        [dict(row) for row in cache_item_rows if isinstance(row, dict)]
        if cache_item_rows is not None
        else None
    )
    coverage["cache_items"] = CoverageV1(
        decoded=cache_items is not None,
        reason=(
            None
            if cache_items is not None
            else database_error or "SQLite cacheItem table absent"
        ),
        source_refs=cache_item_refs,
    )

    shop_refs = ["DBTableConfigs.lua ShopItem{id,shopType,leftCount,costCount,costType,itemCodeStr}", "DBTableConfigs.lua Shop{energyBuyCost,...}"]
    shop_rows = table_rows.get("Shop")
    shop = (
        next((dict(row) for row in shop_rows if isinstance(row, dict)), None)
        if shop_rows is not None
        else None
    )
    shop_item_rows = table_rows.get("ShopItem")
    shop_items = (
        sorted(
            (dict(row) for row in shop_item_rows if isinstance(row, dict)),
            key=lambda row: (row.get("shopType") or 0, str(row.get("id") or "")),
        )
        if shop_item_rows is not None
        else None
    )
    coverage["shop"] = CoverageV1(
        decoded=shop_items is not None,
        reason=None if shop_items is not None else database_error or "SQLite shopItem table absent",
        source_refs=shop_refs,
    )

    timestamp_refs = ["UserDataKey.lua:73-77", "DBTable.lua:17-18,507-540"]
    coverage["timestamps"] = CoverageV1(
        decoded=bool(timestamps),
        reason=None if timestamps else prefs_error or "no table timestamps in PlayerPrefs",
        source_refs=timestamp_refs,
    )

    return GameStateV1(
        snapshot_sha256=snapshot.content_sha256,
        energy=energy,
        energy_generate_time=energy_generate_time,
        energy_infinite_time=energy_infinite_time,
        coins=coins,
        gems=gems,
        level=level,
        xp=xp,
        board=board,
        orders=orders,
        main_task=main_task,
        order_meta=order_meta,
        activity=activity,
        cache_items=cache_items,
        shop=shop,
        shop_items=shop_items,
        timestamps=timestamps,
        coverage=coverage,
        decode_ms=(time.perf_counter_ns() - started) / 1_000_000,
    )


def _foreground_activity(output: str) -> str | None:
    for pattern in (
        r"mCurrentFocus=.*?\bu\d+\s+([A-Za-z0-9._]+/[A-Za-z0-9._$]+)",
        r"(?:topResumedActivity|ResumedActivity|mResumedActivity)\s*[=:].*?"
        r"\bu\d+\s+([A-Za-z0-9._]+/[A-Za-z0-9._$]+)",
    ):
        match = re.search(pattern, output)
        if match:
            return match.group(1)
    return None


# Ordered cheapest first.  ``dumpsys window windows -a`` prints every window
# with every attribute and was measured as the most expensive single call in
# the read; plain ``dumpsys window`` carries the same ``mCurrentFocus`` line in
# a fraction of the output.  The order is a preference, not an assumption: the
# first form whose output actually yields a foreground component wins and is
# remembered, and the known-good forms stay behind it as fallbacks.
FOCUS_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("shell", "dumpsys", "window"),
    ("shell", "dumpsys", "window", "windows", "-a"),
    ("shell", "dumpsys", "activity", "activities"),
)


class FlushPlan(BaseModel):
    """Which foreground probe this device answers, and what each call cost."""

    model_config = ConfigDict(extra="forbid")

    focus_command: list[str] | None = None
    last_adb_calls: int = Field(default=0, ge=0)

    def forget(self) -> None:
        self.focus_command = None


def _focus(
    adb: str | Path,
    serial: str,
    runner: Runner,
    commands: list[CommandReceiptV1],
    plan: FlushPlan | None = None,
) -> str | None:
    if plan is not None and plan.focus_command is not None:
        attempts: tuple[tuple[str, ...], ...] = (tuple(plan.focus_command),)
        attempts += tuple(c for c in FOCUS_COMMANDS if list(c) != plan.focus_command)
    else:
        attempts = FOCUS_COMMANDS[1:] if plan is None else FOCUS_COMMANDS
    for tail in attempts:
        args = [adb, "-s", serial, *tail]
        completed = _timed_call(runner, args, commands)
        if completed.returncode != 0:
            continue
        activity = _foreground_activity(_output_text(completed.stdout))
        if activity is not None:
            if plan is not None:
                plan.focus_command = list(tail)
            return activity
    return None


def _run_flush_command(
    adb: str | Path,
    serial: str,
    tail: Sequence[str],
    runner: Runner,
    commands: list[CommandReceiptV1],
    *,
    recovery: bool = False,
) -> None:
    args = [adb, "-s", serial, *tail]
    completed = _timed_call(runner, args, commands)
    if completed.returncode != 0:
        message = _output_text(completed.stderr) or _output_text(completed.stdout)
        error = FlushRecoveryError if recovery else FlushError
        raise error(f"command failed ({completed.returncode}): {' '.join(tail)}: {message}")


RECOVERY_ACTIVITY = "com.microfun.Main"
RECOVERY_RETRY_DELAYS_S = (1.0, 2.0, 4.0)
# F4-10, planner ruling of 2026-09-07: wait up to a minute for the board after
# a relaunch, only waiting, never sending a business input.
COLD_START_TIMEOUT_S = 60


def _package_process_running(
    adb: str | Path,
    serial: str,
    package: str,
    runner: Runner,
    commands: list[CommandReceiptV1],
) -> bool:
    args = [adb, "-s", serial, "shell", "pidof", package]
    completed = _timed_call(runner, args, commands)
    return completed.returncode == 0 and bool(_output_text(completed.stdout).strip())


def _start_game(
    adb: str | Path,
    serial: str,
    package: str,
    runner: Runner,
    commands: list[CommandReceiptV1],
) -> None:
    _run_flush_command(
        adb,
        serial,
        ["shell", "am", "start", "-n", f"{package}/{RECOVERY_ACTIVITY}"],
        runner,
        commands,
        recovery=True,
    )


def _recover_game_foreground(
    adb: str | Path,
    serial: str,
    package: str,
    background: str | None,
    runner: Runner,
    commands: list[CommandReceiptV1],
    sleep: Callable[[float], None],
    recovery_poll_s: float | None = None,
    plan: FlushPlan | None = None,
    min_resume_dwell_s: float = 0.0,
) -> str:
    # F4-10, planner ruling of 2026-09-07.  The dead-app check used to run only
    # when the focused window looked like a launcher, so an app that died while
    # the focus said something else took the short retry path -- three attempts
    # over seven seconds -- and then failed.  That is what ended the 04:48Z
    # session after sixteen healthy rounds.  Whether the process is there is
    # the question, and the focus string is not the way to ask it.
    game_was_killed = not _package_process_running(
        adb, serial, package, runner, commands
    )
    if game_was_killed:
        _start_game(adb, serial, package, runner, commands)
        observed = background
        for _second in range(COLD_START_TIMEOUT_S):
            sleep(1.0)
            observed = _focus(adb, serial, runner, commands, plan)
            if observed is not None and observed.startswith(package + "/"):
                return observed
        raise FlushRecoveryError(
            f"foreground recovery failed after cold start: expected {package}, "
            f"observed {observed or 'unknown'}"
        )

    observed = background
    for delay_s in RECOVERY_RETRY_DELAYS_S:
        _start_game(adb, serial, package, runner, commands)
        if recovery_poll_s is None:
            sleep(delay_s)
            observed = _focus(adb, serial, runner, commands, plan)
            if observed is not None and observed.startswith(package + "/"):
                return observed
            continue
        # Same wall-clock budget per attempt, but return as soon as the game is
        # actually back in front instead of always paying the full delay.
        # ``min_resume_dwell_s`` is the floor the save needs: the game finishes
        # writing around the resume, so returning the instant the foreground
        # comes back hands the caller a save older than this pause.
        waited = 0.0
        while waited < delay_s:
            step = min(max(recovery_poll_s, 0.05), delay_s - waited)
            sleep(step)
            waited += step
            observed = _focus(adb, serial, runner, commands, plan)
            if observed is not None and observed.startswith(package + "/"):
                if waited < min_resume_dwell_s:
                    sleep(min_resume_dwell_s - waited)
                return observed
    raise FlushRecoveryError(
        f"foreground recovery failed after {len(RECOVERY_RETRY_DELAYS_S)} attempts: "
        f"expected {package}, observed {observed or 'unknown'}"
    )


def trigger_flush(
    adb: str | Path,
    serial: str,
    package: str,
    method: FlushMethod,
    *,
    runner: Runner = _default_runner,
    sleep: Callable[[float], None] = time.sleep,
    clock_ns: Callable[[], int] = time.perf_counter_ns,
    recovery_poll_s: float | None = None,
    probe_before: bool = True,
    plan: FlushPlan | None = None,
    post_home_dwell_s: float = 0.25,
    min_resume_dwell_s: float = 0.0,
) -> FlushReceiptV1:
    """Trigger Unity pause persistence and guarantee foreground recovery.

    ``recovery_poll_s`` keeps the same per-attempt wall-clock budget but checks
    the foreground every ``recovery_poll_s`` seconds instead of always sleeping
    the whole delay.  ``probe_before=False`` drops the pre-HOME focus probe,
    which costs one ``dumpsys window windows -a`` round trip and is only used
    for the receipt; the recovery step reads the foreground itself either way.
    """

    if method not in {"home_and_return", "pause_broadcast", "none"}:
        raise ValueError(f"unsupported flush method: {method}")
    started = clock_ns()
    commands: list[CommandReceiptV1] = []
    before = (
        _focus(adb, serial, runner, commands, plan)
        if probe_before or method == "none"
        else None
    )
    background: str | None = None

    if method == "home_and_return":
        _run_flush_command(
            adb,
            serial,
            ["shell", "input", "keyevent", "KEYCODE_HOME"],
            runner,
            commands,
        )
        # Time the game gets to serialise its 45 tables before anything reads
        # the file.  Too short and the pull returns a save older than this very
        # pause; see the F1-04 live probe.
        sleep(post_home_dwell_s)
        background = _focus(adb, serial, runner, commands, plan)
    elif method == "pause_broadcast":
        _run_flush_command(
            adb,
            serial,
            [
                "shell",
                "am",
                "send-trusted-broadcast",
                "-a",
                "android.intent.action.SCREEN_OFF",
            ],
            runner,
            commands,
        )
        sleep(0.25)
        background = _focus(adb, serial, runner, commands, plan)
        _run_flush_command(
            adb,
            serial,
            [
                "shell",
                "am",
                "send-trusted-broadcast",
                "-a",
                "android.intent.action.SCREEN_ON",
            ],
            runner,
            commands,
        )

    after = before
    if method != "none":
        after = _recover_game_foreground(
            adb,
            serial,
            package,
            background,
            runner,
            commands,
            sleep,
            recovery_poll_s,
            plan,
            min_resume_dwell_s,
        )

    if plan is not None:
        plan.last_adb_calls = len(commands)
    return FlushReceiptV1(
        method=method,
        before_foreground_activity=before,
        background_activity=background,
        after_foreground_activity=after,
        elapsed_ms=max((clock_ns() - started) / 1_000_000, 0.0),
        commands=commands,
    )


def changed_game_fields(before: GameStateV1, after: GameStateV1) -> list[str]:
    """Return semantic field names whose decoded values changed."""

    fields = (
        "energy",
        "coins",
        "gems",
        "level",
        "xp",
        "board",
        "orders",
        "main_task",
        "order_meta",
        "timestamps",
    )
    return [name for name in fields if getattr(before, name) != getattr(after, name)]


def evaluate_verdict(
    state: GameStateV1 | None, flush_latency_seconds: float | None
) -> tuple[SaveChannelVerdict, str]:
    """Apply the F1-02 viability thresholds without optimistic inference."""

    if state is None:
        return "not_viable", "no decoded state"
    board_complete = (
        state.board is not None
        and len(state.board) == BOARD_HEIGHT
        and all(len(row) == BOARD_WIDTH for row in state.board)
        and state.coverage.get("board", CoverageV1(decoded=False)).decoded
    )
    core = state.energy is not None and state.coins is not None
    if core and board_complete and flush_latency_seconds is not None:
        if flush_latency_seconds <= 5:
            return "viable", "energy, coins, full 7x9 board decoded and flush latency <= 5s"
    if core:
        return "viable_with_caveats", "energy and coins decoded; full-board/latency gate incomplete"
    if flush_latency_seconds is not None and 5 < flush_latency_seconds <= 30:
        return "viable_with_caveats", "flush latency was between 5s and 30s"
    return "not_viable", "energy+coins decode and <=30s flush criteria were not met"


__all__ = [
    "BOARD_HEIGHT",
    "BOARD_WIDTH",
    "REPORT_SCHEMA",
    "BoardCellV1",
    "CommandReceiptV1",
    "CoverageV1",
    "DBColumnV1",
    "DBSchemaV1",
    "DBTableSchemaV1",
    "FlushError",
    "FlushMethod",
    "FlushReceiptV1",
    "FlushRecoveryError",
    "GameStateV1",
    "OrderRequirementV1",
    "OrderRowV1",
    "SaveChannelReportV1",
    "SaveFileV1",
    "SaveReaderError",
    "SaveSnapshotV1",
    "SchemaFailureV1",
    "TableTimestampV1",
    "changed_game_fields",
    "decode_snapshot",
    "evaluate_verdict",
    "load_db_schema_from_decompile",
    "pull_save_snapshot",
    "trigger_flush",
]
