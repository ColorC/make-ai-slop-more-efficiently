# [OMNI] origin=codex domain=services/trace_induction ts=2026-07-18 type=infrastructure
"""Small SQLite index for external-Agent traces.

The provider transcripts remain the source of truth.  This database stores only
normalized tool operations, bounded/redacted summaries, aliases, and incremental
source cursors so trace induction never has to feed a raw transcript to an LLM.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_cursors (
    provider      TEXT NOT NULL,
    source_kind   TEXT NOT NULL,
    source_path   TEXT NOT NULL,
    position      INTEGER NOT NULL DEFAULT 0,
    source_size   INTEGER NOT NULL DEFAULT 0,
    mtime_ns      INTEGER NOT NULL DEFAULT 0,
    parse_version INTEGER NOT NULL DEFAULT 1,
    last_error    TEXT NOT NULL DEFAULT '',
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (provider, source_kind, source_path)
);

CREATE TABLE IF NOT EXISTS trace_aliases (
    provider    TEXT NOT NULL,
    alias       TEXT NOT NULL,
    trace_id    TEXT NOT NULL,
    alias_kind  TEXT NOT NULL,
    source_path TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (provider, alias)
);
CREATE INDEX IF NOT EXISTS idx_external_alias_trace
    ON trace_aliases (trace_id, provider);

CREATE TABLE IF NOT EXISTS normalized_steps (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id          TEXT NOT NULL,
    provider          TEXT NOT NULL,
    native_session_id TEXT NOT NULL DEFAULT '',
    source_order      INTEGER NOT NULL,
    tool_call_id      TEXT NOT NULL DEFAULT '',
    tool_name         TEXT NOT NULL,
    action_class      TEXT NOT NULL,
    description       TEXT NOT NULL DEFAULT '',
    args_summary      TEXT NOT NULL DEFAULT '',
    result_summary    TEXT NOT NULL DEFAULT '',
    exit_ok           INTEGER NOT NULL DEFAULT -1,
    source_kind       TEXT NOT NULL,
    source_path       TEXT NOT NULL,
    source_offset     INTEGER NOT NULL DEFAULT 0,
    source_event_key  TEXT NOT NULL UNIQUE,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_external_steps_trace
    ON normalized_steps (trace_id, source_order, id);
CREATE INDEX IF NOT EXISTS idx_external_steps_session
    ON normalized_steps (provider, native_session_id, source_order);
CREATE INDEX IF NOT EXISTS idx_external_steps_call
    ON normalized_steps (provider, trace_id, tool_call_id);
"""

_SECRET_KEY = re.compile(
    r"(?:authorization|cookie|password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|credential)",
    re.IGNORECASE,
)
_INLINE_SECRET = re.compile(
    r"(?i)\b(authorization|password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_provider(value: str | None) -> str:
    normalized = str(value or "").strip().lower().replace("_", "-")
    return {
        "claude-code": "claude",
        "claude-code-cli": "claude",
        "codex-cli": "codex",
        "kimi-code": "kimi",
    }.get(normalized, normalized)


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return "<nested>"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 30:
                result["<truncated>"] = f"{len(value) - 30} more fields"
                break
            text_key = str(key)
            result[text_key] = (
                "<redacted>" if _SECRET_KEY.search(text_key) else _safe_value(item, depth=depth + 1)
            )
        return result
    if isinstance(value, (list, tuple)):
        items = [_safe_value(item, depth=depth + 1) for item in list(value)[:30]]
        if len(value) > 30:
            items.append(f"<{len(value) - 30} more items>")
        return items
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        text = _BEARER.sub("Bearer <redacted>", value)
        text = _INLINE_SECRET.sub(lambda match: f"{match.group(1)}{match.group(2)}<redacted>", text)
        return text
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def redacted_summary(value: Any, *, limit: int = 1500) -> str:
    """Return a bounded summary suitable for durable indexing.

    Provider reasoning and prompts are never passed to this function by source
    adapters.  This is a second safety boundary for tool arguments/results.
    """

    safe = _safe_value(value)
    if isinstance(safe, str):
        text = safe
    else:
        text = json.dumps(safe, ensure_ascii=False, default=str, sort_keys=True)
    if len(text) > limit:
        return f"{text[:limit]}... <truncated:{len(text)}>"
    return text


def action_class_for_tool(tool_name: str) -> str:
    name = tool_name.strip().lower()
    if any(part in name for part in ("read", "grep", "glob", "search", "find", "list", "query", "open", "view")):
        return "acquire"
    return "execute"


@dataclass(frozen=True)
class SourceCursor:
    position: int = 0
    source_size: int = 0
    mtime_ns: int = 0
    parse_version: int = 1
    last_error: str = ""


@dataclass(frozen=True)
class TraceAlias:
    provider: str
    alias: str
    trace_id: str
    alias_kind: str
    source_path: str = ""


class ExternalTraceIndex:
    """Durable normalized external trace store."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._batch_conn: sqlite3.Connection | None = None
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._batch_conn is not None:
            yield self._batch_conn
            return
        conn = sqlite3.connect(str(self.path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def batch(self) -> Iterator["ExternalTraceIndex"]:
        """Reuse one SQLite transaction for a source sync.

        A selected native session can contain thousands of tool events.  A
        transaction per event is needlessly slow on Windows; this keeps the
        durable cursor and all normalized steps atomic while preserving the
        small per-method API.
        """

        if self._batch_conn is not None:
            yield self
            return
        conn = sqlite3.connect(str(self.path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        self._batch_conn = conn
        try:
            yield self
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._batch_conn = None
            conn.close()

    def get_cursor(self, provider: str, source_kind: str, source_path: str | Path) -> SourceCursor:
        key = str(source_path)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT position, source_size, mtime_ns, parse_version, last_error "
                "FROM source_cursors WHERE provider=? AND source_kind=? AND source_path=?",
                (normalize_provider(provider), source_kind, key),
            ).fetchone()
        return SourceCursor(**dict(row)) if row else SourceCursor()

    def set_cursor(
        self,
        provider: str,
        source_kind: str,
        source_path: str | Path,
        *,
        position: int,
        source_size: int = 0,
        mtime_ns: int = 0,
        parse_version: int = 1,
        last_error: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO source_cursors "
                "(provider, source_kind, source_path, position, source_size, mtime_ns, parse_version, last_error, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(provider, source_kind, source_path) DO UPDATE SET "
                "position=excluded.position, source_size=excluded.source_size, mtime_ns=excluded.mtime_ns, "
                "parse_version=excluded.parse_version, last_error=excluded.last_error, updated_at=excluded.updated_at",
                (
                    normalize_provider(provider), source_kind, str(source_path), int(position),
                    int(source_size), int(mtime_ns), int(parse_version), redacted_summary(last_error, limit=500), _utcnow(),
                ),
            )

    def upsert_alias(
        self,
        provider: str,
        alias: str,
        trace_id: str,
        *,
        alias_kind: str,
        source_path: str | Path = "",
    ) -> None:
        provider = normalize_provider(provider)
        alias = str(alias).strip()
        if not provider or not alias or not trace_id:
            return
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO trace_aliases (provider, alias, trace_id, alias_kind, source_path, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(provider, alias) DO UPDATE SET trace_id=excluded.trace_id, "
                "alias_kind=excluded.alias_kind, source_path=excluded.source_path, updated_at=excluded.updated_at",
                (provider, alias, trace_id, alias_kind, str(source_path), _utcnow()),
            )

    def resolve_aliases(self, references: list[str], *, provider: str | None = None) -> list[TraceAlias]:
        if not references:
            return []
        normalized_provider = normalize_provider(provider)
        placeholders = ",".join("?" for _ in references)
        query = (
            "SELECT provider, alias, trace_id, alias_kind, source_path FROM trace_aliases "
            f"WHERE alias IN ({placeholders})"
        )
        args: list[Any] = list(references)
        if normalized_provider:
            query += " AND provider=?"
            args.append(normalized_provider)
        with self._connect() as conn:
            rows = conn.execute(query, args).fetchall()
        result = [TraceAlias(**dict(row)) for row in rows]
        seen = {(item.provider, item.trace_id, item.source_path) for item in result}
        # A canonical trace id is also a valid reference even when no alias row
        # was requested explicitly.
        with self._connect() as conn:
            for reference in references:
                trace_rows = conn.execute(
                    "SELECT provider, trace_id, MIN(source_path) AS source_path "
                    "FROM normalized_steps WHERE trace_id=? "
                    + ("AND provider=? " if normalized_provider else "")
                    + "GROUP BY provider, trace_id",
                    (reference, normalized_provider) if normalized_provider else (reference,),
                ).fetchall()
                for row in trace_rows:
                    key = (row["provider"], row["trace_id"], row["source_path"] or "")
                    if key not in seen:
                        result.append(TraceAlias(row["provider"], reference, row["trace_id"], "trace_id", row["source_path"] or ""))
                        seen.add(key)
        return result

    def insert_tool_call(
        self,
        *,
        trace_id: str,
        provider: str,
        native_session_id: str,
        source_order: int,
        tool_call_id: str,
        tool_name: str,
        args: Any,
        source_kind: str,
        source_path: str | Path,
        source_offset: int,
        source_event_key: str,
        description: str = "",
    ) -> bool:
        provider = normalize_provider(provider)
        tool_name = str(tool_name or "unknown")
        args_summary = redacted_summary(args)
        description = redacted_summary(description or f"调用 {tool_name}", limit=500)
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO normalized_steps "
                "(trace_id, provider, native_session_id, source_order, tool_call_id, tool_name, action_class, "
                "description, args_summary, source_kind, source_path, source_offset, source_event_key, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    trace_id, provider, native_session_id, int(source_order), tool_call_id,
                    tool_name, action_class_for_tool(tool_name), description, args_summary,
                    source_kind, str(source_path), int(source_offset), source_event_key, _utcnow(),
                ),
            )
            return cursor.rowcount > 0

    def apply_tool_result(
        self,
        *,
        trace_id: str,
        provider: str,
        native_session_id: str,
        source_order: int,
        tool_call_id: str,
        result: Any,
        exit_ok: int,
        source_kind: str,
        source_path: str | Path,
        source_offset: int,
        source_event_key: str,
        tool_name: str = "unknown",
    ) -> bool:
        provider = normalize_provider(provider)
        result_summary = redacted_summary(result)
        with self._connect() as conn:
            existing_result = conn.execute(
                "SELECT 1 FROM normalized_steps WHERE source_event_key=?",
                (source_event_key,),
            ).fetchone()
            if existing_result:
                return False
            call = None
            if tool_call_id:
                call = conn.execute(
                    "SELECT id FROM normalized_steps WHERE provider=? AND trace_id=? AND tool_call_id=? "
                    "ORDER BY source_order DESC, id DESC LIMIT 1",
                    (provider, trace_id, tool_call_id),
                ).fetchone()
            if call:
                conn.execute(
                    "UPDATE normalized_steps SET result_summary=?, exit_ok=? WHERE id=?",
                    (result_summary, int(exit_ok), call["id"]),
                )
                # A compact marker makes result ingestion idempotent without
                # duplicating the result as an induction step.
                conn.execute(
                    "INSERT INTO normalized_steps "
                    "(trace_id, provider, native_session_id, source_order, tool_call_id, tool_name, action_class, "
                    "description, args_summary, result_summary, exit_ok, source_kind, source_path, source_offset, "
                    "source_event_key, created_at) VALUES (?, ?, ?, ?, ?, ?, 'think', '', '', '', ?, ?, ?, ?, ?, ?)",
                    (
                        trace_id, provider, native_session_id, int(source_order), tool_call_id,
                        "__result_marker__", int(exit_ok), source_kind, str(source_path), int(source_offset),
                        source_event_key, _utcnow(),
                    ),
                )
                return True
            cursor = conn.execute(
                "INSERT OR IGNORE INTO normalized_steps "
                "(trace_id, provider, native_session_id, source_order, tool_call_id, tool_name, action_class, "
                "description, args_summary, result_summary, exit_ok, source_kind, source_path, source_offset, "
                "source_event_key, created_at) VALUES (?, ?, ?, ?, ?, ?, 'execute', ?, '', ?, ?, ?, ?, ?, ?, ?)",
                (
                    trace_id, provider, native_session_id, int(source_order), tool_call_id,
                    tool_name, f"记录 {tool_name} 的执行结果", result_summary, int(exit_ok),
                    source_kind, str(source_path), int(source_offset), source_event_key, _utcnow(),
                ),
            )
            return cursor.rowcount > 0

    def read_trace_steps(
        self,
        references: list[str],
        *,
        provider: str | None = None,
    ) -> dict[str, list[Any]]:
        """Read normalized rows as the existing ``TraceStep`` contract."""

        from omnicompany.packages.services._learning.trace_induction.sop_extractor import TraceStep

        aliases = self.resolve_aliases(references, provider=provider)
        trace_by_reference: dict[str, set[str]] = {reference: set() for reference in references}
        for item in aliases:
            if item.alias in trace_by_reference:
                trace_by_reference[item.alias].add(item.trace_id)
            if item.trace_id in trace_by_reference:
                trace_by_reference[item.trace_id].add(item.trace_id)
        for reference in references:
            if not trace_by_reference[reference]:
                trace_by_reference[reference].add(reference)

        result: dict[str, list[TraceStep]] = {reference: [] for reference in references}
        normalized_provider = normalize_provider(provider)
        with self._connect() as conn:
            for reference, trace_ids in trace_by_reference.items():
                placeholders = ",".join("?" for _ in trace_ids)
                query = (
                    "SELECT * FROM normalized_steps WHERE tool_name!='__result_marker__' "
                    f"AND trace_id IN ({placeholders})"
                )
                args: list[Any] = list(trace_ids)
                if normalized_provider:
                    query += " AND provider=?"
                    args.append(normalized_provider)
                query += " ORDER BY source_order, id"
                rows = conn.execute(query, args).fetchall()
                for step_num, row in enumerate(rows, 1):
                    result[reference].append(TraceStep(
                        step_num=step_num,
                        tool_name=row["tool_name"],
                        desc=row["description"],
                        rationale="",
                        tool_args_summary=row["args_summary"],
                        tool_result=row["result_summary"],
                        tool_exit_ok=row["exit_ok"],
                        action_class=row["action_class"],
                        input_types=[],
                        output_types=[],
                    ))
        return result

    def count_steps(self) -> int:
        with self._connect() as conn:
            return int(conn.execute(
                "SELECT COUNT(*) FROM normalized_steps WHERE tool_name!='__result_marker__'"
            ).fetchone()[0])


__all__ = [
    "ExternalTraceIndex",
    "SourceCursor",
    "TraceAlias",
    "action_class_for_tool",
    "normalize_provider",
    "redacted_summary",
]
