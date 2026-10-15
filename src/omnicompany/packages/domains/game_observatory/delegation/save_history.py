# [OMNI] origin=kimi created_by=kimi intent="E6 save versioning: normalized per-table export of the decrypted save into a change-committed local git history"
"""E6 save versioning: export every save table, commit on change.

Strategy file: ``explore-first-strategy-2026-09-09.md`` section 5, E6 entry
(2026-09-09 user ruling).  After the save channel decrypts a read, every
table in the plaintext database is exported as one normalized JSON document
per table (``tables/<table>.json``) into a standalone local git repository
(``game_models/romantic-restaurant/save-history/``).  The repository is the
incremental modification trail: a read whose export is byte-identical to the
work tree produces no commit, and a read that changed anything is committed
with a message carrying run_id / round / timestamp / trigger, so any two
states can be diffed and replayed through git alone.

Normalization rules are fixed and deterministic:

- key order: every JSON object is serialized with sorted keys;
- row order: rows sort by the schema's primary key when the table declares
  one and the row carries it, otherwise by the row's canonical JSON;
- value types: ``None`` -> ``null``, ints and finite floats stay numeric,
  non-finite floats become ``{"__float__": "nan|inf|-inf"}``, SQLite blobs
  become ``{"__bytes_hex__": "..."}``;
- no timestamps land in the exported files -- the wall clock lives only in
  the commit message, which is what keeps a rerun over an unchanged save a
  zero-diff no-op.

Schema evolution is not special-cased: a table the game adds appears as a
new file, a table it drops disappears from ``tables/`` and the deletion is
committed like any other change.

Git goes through ``subprocess``.  Every failure -- git missing, init or
commit error, unreadable database -- degrades to one JSONL alert event and
an ``"error"`` receipt; the play loop that owns the save channel never sees
an exception from this module.  Historical snapshots are never backfilled:
only reads that happen while the flag is on are recorded.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .save_reader import DBSchemaV1

EXPORT_SCHEMA = "game-observatory.save-history-export.v1"
TABLES_DIR = "tables"
MANIFEST_FILENAME = "manifest.json"

GIT_TIMEOUT_S = 60
GIT_USER_NAME = "save-history"
GIT_USER_EMAIL = "save-history@localhost"


class SaveHistoryError(RuntimeError):
    """A git or export step failed; the caller degrades to an alert event."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def normalize_value(value: Any) -> Any:
    """Map a SQLite value onto its deterministic JSON representation."""

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        if math.isnan(value):
            return {"__float__": "nan"}
        return {"__float__": "inf" if value > 0 else "-inf"}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__bytes_hex__": bytes(value).hex()}
    return str(value)


def _canonical_row(row: dict[str, Any]) -> str:
    return json.dumps(
        {str(key): normalize_value(value) for key, value in row.items()},
        ensure_ascii=False,
        sort_keys=True,
    )


def _sort_token(value: Any) -> str:
    """A type-tagged token so int 1, float 1.0 and str "1" never compare equal."""

    normalized = normalize_value(value)
    return f"{type(normalized).__name__}:{json.dumps(normalized, ensure_ascii=False, sort_keys=True)}"


def _sorted_rows(rows: list[dict[str, Any]], primary_key: str | None) -> list[dict[str, Any]]:
    def key(row: dict[str, Any]) -> tuple[int, str]:
        if primary_key is not None and row.get(primary_key) is not None:
            return (0, _sort_token(row[primary_key]))
        return (1, _canonical_row(row))

    return sorted(rows, key=key)


def _safe_filename(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    return safe or "unnamed"


def _table_names(connection: sqlite3.Connection) -> list[str]:
    return sorted(
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    )


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def export_save(
    database: Path,
    dest_dir: Path,
    *,
    schema: DBSchemaV1 | None = None,
) -> dict[str, Any]:
    """Write one normalized JSON per table of the decrypted save database.

    The export is a pure function of the database bytes and the schema: same
    input, byte-identical output.  Table JSON files the previous export wrote
    for tables the database no longer carries are removed, so a dropped table
    shows up in git as a deletion.
    """

    database = Path(database)
    dest_dir = Path(dest_dir)
    logical_by_sqlite: dict[str, str] = {}
    table_specs: dict[str, Any] = {}
    if schema is not None:
        for logical_name, spec in schema.tables.items():
            logical_by_sqlite[spec.name] = logical_name
            table_specs[spec.name] = spec

    uri = f"file:{database.resolve().as_posix()}?mode=ro"
    exported: dict[str, dict[str, Any]] = {}
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.row_factory = sqlite3.Row
        for table_name in _table_names(connection):
            rows = [
                dict(row)
                for row in connection.execute(f"SELECT * FROM {_quote_identifier(table_name)}")
            ]
            spec = table_specs.get(table_name)
            primary_key = spec.primary_key if spec is not None else None
            if primary_key is not None and rows and any(primary_key not in row for row in rows):
                primary_key = None
            ordered = _sorted_rows(rows, primary_key)
            logical_name = logical_by_sqlite.get(table_name)
            exported[table_name] = {
                "schema": EXPORT_SCHEMA,
                "table": table_name,
                "logical_name": logical_name,
                "primary_key": primary_key,
                "row_count": len(ordered),
                "rows": [
                    {str(key): normalize_value(value) for key, value in row.items()}
                    for row in ordered
                ],
            }
    finally:
        # The sqlite3 context manager never closes the connection, and an
        # open handle locks the database file on Windows.
        connection.close()

    tables_dir = dest_dir / TABLES_DIR
    tables_dir.mkdir(parents=True, exist_ok=True)
    expected_files = {f"{_safe_filename(name)}.json" for name in exported}
    for stale in tables_dir.glob("*.json"):
        if stale.name not in expected_files:
            stale.unlink()
    for table_name, document in sorted(exported.items()):
        path = tables_dir / f"{_safe_filename(table_name)}.json"
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    manifest = {
        "schema": EXPORT_SCHEMA,
        "source_sha256": _file_sha256(database),
        "schema_config_sha256": schema.config_sha256 if schema is not None else None,
        "tables": sorted(exported),
        "row_counts": {name: exported[name]["row_count"] for name in sorted(exported)},
    }
    (dest_dir / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest


def _commit_message(
    *,
    run_id: str | None,
    round_number: int | None,
    timestamp: str,
    trigger: str,
) -> str:
    title_run = run_id or "unknown-run"
    title_round = f" round {round_number}" if round_number is not None else ""
    return (
        f"save: {title_run}{title_round}\n"
        f"\n"
        f"run_id: {run_id or 'unknown'}\n"
        f"round: {round_number if round_number is not None else 'n/a'}\n"
        f"timestamp: {timestamp}\n"
        f"trigger: {trigger}\n"
    )


class SaveHistory:
    """Change-committed git history over the normalized save export."""

    def __init__(
        self,
        repo_dir: Path,
        *,
        schema: DBSchemaV1 | None = None,
        alerts_path: Path | None = None,
        git: str = "git",
        clock: Any = None,
    ) -> None:
        self.repo_dir = Path(repo_dir)
        self.schema = schema
        self.alerts_path = (
            Path(alerts_path)
            if alerts_path is not None
            else self.repo_dir.with_name(self.repo_dir.name + "-alerts.jsonl")
        )
        self.git = git
        self._clock = clock or _utc_now

    # ------------------------------------------------------------------ git
    def _git(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        try:
            completed = subprocess.run(
                [self.git, *args],
                cwd=self.repo_dir,
                capture_output=True,
                check=False,
                timeout=GIT_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SaveHistoryError(f"git {' '.join(args)} failed to run: {exc}") from exc
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", "replace").strip()
            raise SaveHistoryError(
                f"git {' '.join(args)} exited {completed.returncode}: {stderr}"
            )
        return completed

    def _ensure_repo(self) -> None:
        if not (self.repo_dir / ".git").is_dir():
            self.repo_dir.mkdir(parents=True, exist_ok=True)
            self._git("init")
        for key, value in (("user.name", GIT_USER_NAME), ("user.email", GIT_USER_EMAIL)):
            probe = subprocess.run(
                [self.git, "config", "--local", "--get", key],
                cwd=self.repo_dir,
                capture_output=True,
                check=False,
                timeout=GIT_TIMEOUT_S,
            )
            if probe.returncode != 0 or not probe.stdout.strip():
                self._git("config", "--local", key, value)

    # -------------------------------------------------------------- alerting
    def _alert(self, event: dict[str, Any]) -> None:
        try:
            self.alerts_path.parent.mkdir(parents=True, exist_ok=True)
            with self.alerts_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(
                    json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
                )
        except OSError:
            # The alert channel itself is best-effort; losing it must not
            # turn a degraded save-history into a crashed play loop either.
            pass

    # --------------------------------------------------------------- record
    def record(
        self,
        database: Path,
        *,
        run_id: str | None,
        round_number: int | None = None,
        trigger: str,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        """Export the decrypted save and commit when it changed.

        Never raises.  The receipt's ``status`` is one of ``committed``
        (work tree differed; a commit was created), ``unchanged`` (export is
        byte-identical to the work tree; zero commits) or ``error`` (a step
        failed; one alert event was logged and play continues).
        """

        timestamp = timestamp or self._clock()
        base: dict[str, Any] = {
            "run_id": run_id,
            "round": round_number,
            "timestamp": timestamp,
            "trigger": trigger,
            "repo_dir": str(self.repo_dir),
        }
        try:
            self._ensure_repo()
            manifest = export_save(database, self.repo_dir, schema=self.schema)
            self._git("add", "-A", "--", TABLES_DIR, MANIFEST_FILENAME)
            diff = subprocess.run(
                [self.git, "diff", "--cached", "--quiet"],
                cwd=self.repo_dir,
                capture_output=True,
                check=False,
                timeout=GIT_TIMEOUT_S,
            )
            if diff.returncode == 0:
                return {**base, "status": "unchanged", "commit": None, "tables": manifest["tables"]}
            if diff.returncode not in (0, 1):
                raise SaveHistoryError(
                    "git diff --cached --quiet exited "
                    f"{diff.returncode}: {diff.stderr.decode('utf-8', 'replace').strip()}"
                )
            message = _commit_message(
                run_id=run_id,
                round_number=round_number,
                timestamp=timestamp,
                trigger=trigger,
            )
            self._git("commit", "-m", message)
            commit = self._git("rev-parse", "HEAD").stdout.decode("ascii").strip()
            return {
                **base,
                "status": "committed",
                "commit": commit,
                "tables": manifest["tables"],
                "message": message,
            }
        except Exception as exc:  # noqa: BLE001 - degrade, never break play
            self._alert(
                {
                    "schema": "game-observatory.save-history-alert.v1",
                    **base,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            return {**base, "status": "error", "error": f"{type(exc).__name__}: {exc}"}


__all__ = [
    "EXPORT_SCHEMA",
    "GIT_TIMEOUT_S",
    "MANIFEST_FILENAME",
    "TABLES_DIR",
    "SaveHistory",
    "SaveHistoryError",
    "export_save",
    "normalize_value",
]
