# [OMNI] origin=codex domain=services/trace_induction ts=2026-07-18 type=infrastructure
"""Incremental external-Agent source adapters for trace induction.

Discovery uses each provider's small native index instead of recursively
scanning transcript directories.  Transcript reads then resume from byte
cursors stored by :mod:`external_trace_index`.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from omnicompany.packages.services._learning.trace_induction.external_trace_index import (
    ExternalTraceIndex,
    normalize_provider,
)


@dataclass(frozen=True)
class ExternalSourceRoots:
    codex_home: Path = field(default_factory=lambda: Path.home() / ".codex")
    claude_home: Path = field(default_factory=lambda: Path.home() / ".claude")
    kimi_home: Path = field(default_factory=lambda: Path.home() / ".kimi-code")


@dataclass
class ExternalSyncStats:
    sources_checked: int = 0
    bytes_read: int = 0
    events_seen: int = 0
    calls_inserted: int = 0
    results_applied: int = 0
    aliases_upserted: int = 0
    warnings: list[str] = field(default_factory=list)

    def merge(self, other: "ExternalSyncStats") -> "ExternalSyncStats":
        self.sources_checked += other.sources_checked
        self.bytes_read += other.bytes_read
        self.events_seen += other.events_seen
        self.calls_inserted += other.calls_inserted
        self.results_applied += other.results_applied
        self.aliases_upserted += other.aliases_upserted
        self.warnings.extend(other.warnings)
        return self


def _canonical_trace_id(provider: str, session_id: str) -> str:
    return f"external.{normalize_provider(provider)}.{session_id}"


def _session_id_from_reference(reference: str, provider: str) -> str:
    prefix = f"external.{normalize_provider(provider)}."
    return reference[len(prefix):] if reference.startswith(prefix) else reference


def _parse_json_object(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if stripped.startswith(("{", "[")):
        try:
            return json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            pass
    return value


def _explicit_exit_ok(result: Any, *, default: int = 1) -> int:
    if isinstance(result, dict):
        if result.get("is_error") is True or result.get("error") is True:
            return 0
        if result.get("success") is False:
            return 0
        status = str(result.get("status") or "").lower()
        if status in {"error", "failed", "failure", "cancelled"}:
            return 0
    return default


def _read_incremental_jsonl(
    index: ExternalTraceIndex,
    *,
    provider: str,
    source_kind: str,
    path: Path,
) -> tuple[list[tuple[int, dict[str, Any]]], int]:
    """Read complete new JSONL records and advance a durable byte cursor."""

    if not path.is_file():
        return [], 0
    stat = path.stat()
    cursor = index.get_cursor(provider, source_kind, path)
    start = cursor.position if cursor.position <= stat.st_size else 0
    with path.open("rb") as stream:
        stream.seek(start)
        raw = stream.read()
    records: list[tuple[int, dict[str, Any]]] = []
    consumed = 0
    for line in raw.splitlines(keepends=True):
        # Keep an incomplete trailing record for the next pass.
        if not line.endswith((b"\n", b"\r")) and consumed + len(line) == len(raw):
            break
        line_offset = start + consumed
        consumed += len(line)
        try:
            value = json.loads(line.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(value, dict):
            records.append((line_offset, value))
    new_position = start + consumed
    index.set_cursor(
        provider, source_kind, path,
        position=new_position,
        source_size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )
    return records, len(raw[:consumed])


def _register_alias(
    index: ExternalTraceIndex,
    stats: ExternalSyncStats,
    *,
    provider: str,
    session_id: str,
    trace_id: str,
    alias_kind: str,
    source_path: Path | str,
) -> None:
    index.upsert_alias(
        provider, session_id, trace_id,
        alias_kind=alias_kind,
        source_path=source_path,
    )
    index.upsert_alias(
        provider, trace_id, trace_id,
        alias_kind="trace_id",
        source_path=source_path,
    )
    stats.aliases_upserted += 2


def sync_omni_events(
    index: ExternalTraceIndex,
    references: list[str],
    *,
    db_paths: Iterable[str | Path],
    provider: str | None = None,
) -> ExternalSyncStats:
    """Incrementally normalize selected traces from canonical Omni EventBus DBs."""

    stats = ExternalSyncStats()
    wanted_provider = normalize_provider(provider)
    for raw_path in db_paths:
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            continue
        stats.sources_checked += 1
        try:
            conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            has_events = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'"
            ).fetchone()
            if not has_events:
                conn.close()
                continue
            for reference in references:
                cursor_key = f"{path}#trace={reference}"
                cursor = index.get_cursor(wanted_provider or "omni", "omni-events", cursor_key)
                rows = conn.execute(
                    "SELECT rowid, id, trace_id, parent_id, event_type, source, data "
                    "FROM events WHERE trace_id=? AND rowid>? ORDER BY rowid",
                    (reference, cursor.position),
                ).fetchall()
                max_rowid = cursor.position
                for row in rows:
                    max_rowid = max(max_rowid, int(row["rowid"]))
                    stats.events_seen += 1
                    source = str(row["source"] or "")
                    event_provider = normalize_provider(source.removeprefix("agent.external."))
                    if event_provider not in {"codex", "claude", "kimi"}:
                        if source.startswith("cc-chat:"):
                            event_provider = normalize_provider(source.split(":", 1)[1])
                        else:
                            continue
                    if wanted_provider and event_provider != wanted_provider:
                        continue
                    try:
                        envelope = json.loads(row["data"] or "{}")
                    except (json.JSONDecodeError, TypeError):
                        envelope = {}
                    payload = envelope.get("payload") if isinstance(envelope, dict) else {}
                    if not isinstance(payload, dict):
                        payload = {}
                    trace_id = str(row["trace_id"])
                    native_session_id = str(
                        payload.get("external_session_id")
                        or payload.get("session_id")
                        or payload.get("thread_id")
                        or payload.get("run_id")
                        or trace_id
                    )
                    _register_alias(
                        index, stats,
                        provider=event_provider,
                        session_id=native_session_id,
                        trace_id=trace_id,
                        alias_kind="omni-event-session",
                        source_path=path,
                    )
                    event_type = str(row["event_type"] or "")
                    event_key = f"omni:{path}:{row['id']}"
                    if event_type in {"agent.tool.call", "chat.normalized.tool_use"}:
                        inserted = index.insert_tool_call(
                            trace_id=trace_id,
                            provider=event_provider,
                            native_session_id=native_session_id,
                            source_order=int(row["rowid"]),
                            tool_call_id=str(payload.get("tool_use_id") or payload.get("call_id") or row["id"]),
                            tool_name=str(payload.get("tool") or payload.get("name") or "unknown"),
                            args=payload.get("args") or payload.get("input") or {},
                            source_kind="omni-events",
                            source_path=path,
                            source_offset=int(row["rowid"]),
                            source_event_key=event_key,
                        )
                        stats.calls_inserted += int(inserted)
                    elif event_type in {"agent.tool.result", "chat.normalized.tool_result"}:
                        result = payload.get("result", payload.get("output", payload.get("content", "")))
                        applied = index.apply_tool_result(
                            trace_id=trace_id,
                            provider=event_provider,
                            native_session_id=native_session_id,
                            source_order=int(row["rowid"]),
                            tool_call_id=str(payload.get("tool_use_id") or payload.get("call_id") or row["parent_id"] or ""),
                            tool_name=str(payload.get("tool") or payload.get("name") or "unknown"),
                            result=result,
                            exit_ok=_explicit_exit_ok(payload),
                            source_kind="omni-events",
                            source_path=path,
                            source_offset=int(row["rowid"]),
                            source_event_key=event_key,
                        )
                        stats.results_applied += int(applied)
                db_stat = path.stat()
                index.set_cursor(
                    wanted_provider or "omni", "omni-events", cursor_key,
                    position=max_rowid,
                    source_size=db_stat.st_size,
                    mtime_ns=db_stat.st_mtime_ns,
                )
            conn.close()
        except sqlite3.Error as exc:
            stats.warnings.append(f"{path.name}: {type(exc).__name__}")
    return stats


def _discover_codex(
    index: ExternalTraceIndex,
    references: list[str],
    *,
    home: Path,
) -> ExternalSyncStats:
    stats = ExternalSyncStats()
    state_db = home / "state_5.sqlite"
    if not state_db.is_file():
        return stats
    stats.sources_checked += 1
    ids = sorted({_session_id_from_reference(reference, "codex") for reference in references})
    try:
        conn = sqlite3.connect(f"file:{state_db.as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT id, rollout_path, updated_at_ms FROM threads WHERE id IN ({placeholders})",
            ids,
        ).fetchall() if ids else []
        max_updated = 0
        for row in rows:
            session_id = str(row["id"])
            source_path = Path(str(row["rollout_path"])).expanduser()
            trace_id = _canonical_trace_id("codex", session_id)
            _register_alias(
                index, stats,
                provider="codex", session_id=session_id, trace_id=trace_id,
                alias_kind="native-session", source_path=source_path,
            )
            max_updated = max(max_updated, int(row["updated_at_ms"] or 0))
        conn.close()
        stat = state_db.stat()
        index.set_cursor(
            "codex", "native-session-index", state_db,
            position=max_updated, source_size=stat.st_size, mtime_ns=stat.st_mtime_ns,
        )
    except sqlite3.Error as exc:
        stats.warnings.append(f"codex state index: {type(exc).__name__}")
    return stats


def _discover_claude(index: ExternalTraceIndex, *, home: Path) -> ExternalSyncStats:
    stats = ExternalSyncStats()
    history = home / "history.jsonl"
    records, bytes_read = _read_incremental_jsonl(
        index, provider="claude", source_kind="native-session-index", path=history,
    )
    if history.is_file():
        stats.sources_checked += 1
    stats.bytes_read += bytes_read
    stats.events_seen += len(records)
    for _, record in records:
        session_id = str(record.get("sessionId") or "").strip()
        project = str(record.get("project") or "").strip()
        if not session_id or not project:
            continue
        encoded = project.replace(":", "--").replace("\\", "-").replace("/", "-")
        source_path = home / "projects" / encoded / f"{session_id}.jsonl"
        _register_alias(
            index, stats,
            provider="claude", session_id=session_id,
            trace_id=_canonical_trace_id("claude", session_id),
            alias_kind="native-session", source_path=source_path,
        )
    return stats


def _discover_kimi(index: ExternalTraceIndex, *, home: Path) -> ExternalSyncStats:
    stats = ExternalSyncStats()
    session_index = home / "session_index.jsonl"
    records, bytes_read = _read_incremental_jsonl(
        index, provider="kimi", source_kind="native-session-index", path=session_index,
    )
    if session_index.is_file():
        stats.sources_checked += 1
    stats.bytes_read += bytes_read
    stats.events_seen += len(records)
    for _, record in records:
        session_id = str(record.get("sessionId") or "").strip()
        session_dir_raw = str(record.get("sessionDir") or "").strip()
        if not session_id or not session_dir_raw:
            continue
        session_dir = Path(session_dir_raw).expanduser()
        if not session_dir.is_absolute():
            session_dir = home / session_dir
        _register_alias(
            index, stats,
            provider="kimi", session_id=session_id,
            trace_id=_canonical_trace_id("kimi", session_id),
            alias_kind="native-session", source_path=session_dir,
        )
    return stats


def _sync_codex_transcript(
    index: ExternalTraceIndex,
    *,
    session_id: str,
    trace_id: str,
    path: Path,
) -> ExternalSyncStats:
    stats = ExternalSyncStats(sources_checked=int(path.is_file()))
    records, bytes_read = _read_incremental_jsonl(
        index, provider="codex", source_kind="native-transcript", path=path,
    )
    stats.bytes_read += bytes_read
    stats.events_seen += len(records)
    for offset, record in records:
        if record.get("type") != "response_item":
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        item_type = str(payload.get("type") or "")
        call_id = str(payload.get("call_id") or payload.get("id") or "")
        event_key = f"codex:{path}:{offset}"
        if item_type in {"custom_tool_call", "function_call", "local_shell_call"}:
            args = payload.get("input", payload.get("arguments", payload.get("action", {})))
            inserted = index.insert_tool_call(
                trace_id=trace_id, provider="codex", native_session_id=session_id,
                source_order=offset, tool_call_id=call_id,
                tool_name=str(payload.get("name") or payload.get("namespace") or item_type),
                args=_parse_json_object(args), source_kind="native-transcript",
                source_path=path, source_offset=offset, source_event_key=event_key,
            )
            stats.calls_inserted += int(inserted)
        elif item_type in {"custom_tool_call_output", "function_call_output", "local_shell_call_output"}:
            result = payload.get("output", payload.get("result", ""))
            applied = index.apply_tool_result(
                trace_id=trace_id, provider="codex", native_session_id=session_id,
                source_order=offset, tool_call_id=call_id, result=result,
                exit_ok=_explicit_exit_ok(payload), source_kind="native-transcript",
                source_path=path, source_offset=offset, source_event_key=event_key,
            )
            stats.results_applied += int(applied)
    return stats


def _sync_claude_transcript(
    index: ExternalTraceIndex,
    *,
    session_id: str,
    trace_id: str,
    path: Path,
) -> ExternalSyncStats:
    stats = ExternalSyncStats(sources_checked=int(path.is_file()))
    records, bytes_read = _read_incremental_jsonl(
        index, provider="claude", source_kind="native-transcript", path=path,
    )
    stats.bytes_read += bytes_read
    stats.events_seen += len(records)
    for offset, record in records:
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        blocks = content if isinstance(content, list) else []
        for block_index, block in enumerate(blocks):
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "")
            call_id = str(block.get("id") or block.get("tool_use_id") or "")
            event_key = f"claude:{path}:{offset}:{block_index}"
            source_order = offset * 100 + block_index
            if block_type == "tool_use":
                inserted = index.insert_tool_call(
                    trace_id=trace_id, provider="claude", native_session_id=session_id,
                    source_order=source_order, tool_call_id=call_id,
                    tool_name=str(block.get("name") or "unknown"), args=block.get("input") or {},
                    source_kind="native-transcript", source_path=path, source_offset=offset,
                    source_event_key=event_key,
                )
                stats.calls_inserted += int(inserted)
            elif block_type == "tool_result":
                result = block.get("content", block.get("result", ""))
                applied = index.apply_tool_result(
                    trace_id=trace_id, provider="claude", native_session_id=session_id,
                    source_order=source_order, tool_call_id=call_id, result=result,
                    exit_ok=0 if block.get("is_error") is True else 1,
                    source_kind="native-transcript", source_path=path, source_offset=offset,
                    source_event_key=event_key,
                )
                stats.results_applied += int(applied)
    return stats


def _sync_kimi_wire(
    index: ExternalTraceIndex,
    *,
    session_id: str,
    trace_id: str,
    path: Path,
) -> ExternalSyncStats:
    stats = ExternalSyncStats(sources_checked=int(path.is_file()))
    records, bytes_read = _read_incremental_jsonl(
        index, provider="kimi", source_kind="native-transcript", path=path,
    )
    stats.bytes_read += bytes_read
    stats.events_seen += len(records)
    for offset, record in records:
        if record.get("type") != "context.append_loop_event":
            continue
        event = record.get("event")
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        call_id = str(event.get("toolCallId") or event.get("uuid") or "")
        event_key = f"kimi:{path}:{offset}"
        if event_type == "tool.call":
            inserted = index.insert_tool_call(
                trace_id=trace_id, provider="kimi", native_session_id=session_id,
                source_order=offset, tool_call_id=call_id,
                tool_name=str(event.get("name") or "unknown"), args=event.get("args") or {},
                description=str(event.get("description") or ""),
                source_kind="native-transcript", source_path=path, source_offset=offset,
                source_event_key=event_key,
            )
            stats.calls_inserted += int(inserted)
        elif event_type == "tool.result":
            result = event.get("result", "")
            applied = index.apply_tool_result(
                trace_id=trace_id, provider="kimi", native_session_id=session_id,
                source_order=offset, tool_call_id=call_id, result=result,
                exit_ok=_explicit_exit_ok(result), source_kind="native-transcript",
                source_path=path, source_offset=offset, source_event_key=event_key,
            )
            stats.results_applied += int(applied)
    return stats


def _sync_native_alias(index: ExternalTraceIndex, alias: Any) -> ExternalSyncStats:
    path = Path(alias.source_path).expanduser()
    session_id = alias.alias if alias.alias_kind != "trace_id" else _session_id_from_reference(alias.trace_id, alias.provider)
    if alias.provider == "codex":
        return _sync_codex_transcript(index, session_id=session_id, trace_id=alias.trace_id, path=path)
    if alias.provider == "claude":
        return _sync_claude_transcript(index, session_id=session_id, trace_id=alias.trace_id, path=path)
    if alias.provider == "kimi":
        stats = ExternalSyncStats()
        if path.is_file():
            return _sync_kimi_wire(index, session_id=session_id, trace_id=alias.trace_id, path=path)
        if path.is_dir():
            for wire in sorted(path.glob("agents/*/wire.jsonl")):
                stats.merge(_sync_kimi_wire(
                    index, session_id=session_id, trace_id=alias.trace_id, path=wire,
                ))
        return stats
    return ExternalSyncStats()


def _default_omni_event_paths() -> list[Path]:
    try:
        from omnicompany.core.config import resolve_unified_db_path
        return [resolve_unified_db_path("events.db"), resolve_unified_db_path("ide_events.db")]
    except Exception:
        return [Path("data/events.db"), Path("data/ide_events.db")]


def _sync_selected_external_traces(
    index: ExternalTraceIndex,
    references: list[str],
    *,
    provider: str | None = None,
    omni_event_db_paths: Iterable[str | Path] | None = None,
    roots: ExternalSourceRoots | None = None,
) -> ExternalSyncStats:
    """Sync selected trace/session references without recursive discovery."""

    references = [str(item).strip() for item in references if str(item).strip()]
    stats = ExternalSyncStats()
    if not references:
        return stats
    roots = roots or ExternalSourceRoots()
    wanted_provider = normalize_provider(provider)

    # Canonical Omni events are preferred because they are already normalized,
    # indexed, and audited by the external-worker wrapper.
    stats.merge(sync_omni_events(
        index, references,
        db_paths=list(omni_event_db_paths) if omni_event_db_paths is not None else _default_omni_event_paths(),
        provider=wanted_provider or None,
    ))

    providers = [wanted_provider] if wanted_provider else ["codex", "claude", "kimi"]
    if "codex" in providers:
        stats.merge(_discover_codex(index, references, home=roots.codex_home))
    if "claude" in providers:
        stats.merge(_discover_claude(index, home=roots.claude_home))
    if "kimi" in providers:
        stats.merge(_discover_kimi(index, home=roots.kimi_home))

    synced: set[tuple[str, str, str]] = set()
    for alias in index.resolve_aliases(references, provider=wanted_provider or None):
        if alias.alias_kind not in {"native-session", "trace_id"} or not alias.source_path:
            continue
        if alias.alias_kind == "trace_id" and Path(alias.source_path).suffix.lower() in {
            ".db", ".sqlite", ".sqlite3",
        }:
            continue
        key = (alias.provider, alias.trace_id, alias.source_path)
        if key in synced:
            continue
        synced.add(key)
        stats.merge(_sync_native_alias(index, alias))
    return stats


def sync_selected_external_traces(
    index: ExternalTraceIndex,
    references: list[str],
    *,
    provider: str | None = None,
    omni_event_db_paths: Iterable[str | Path] | None = None,
    roots: ExternalSourceRoots | None = None,
) -> ExternalSyncStats:
    """Atomically sync selected traces in one SQLite transaction."""

    with index.batch():
        return _sync_selected_external_traces(
            index,
            references,
            provider=provider,
            omni_event_db_paths=omni_event_db_paths,
            roots=roots,
        )


__all__ = [
    "ExternalSourceRoots",
    "ExternalSyncStats",
    "sync_omni_events",
    "sync_selected_external_traces",
]
