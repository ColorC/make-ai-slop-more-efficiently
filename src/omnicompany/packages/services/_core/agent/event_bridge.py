# [OMNI] origin=codex domain=services/agent ts=2026-06-13 type=infrastructure
# [OMNI] material_id="material:core.agent.event_bridge.authority_adapter.py"
"""Shared helpers for agent events that must land on the EventBus surface."""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from omnicompany.core.config import resolve_unified_db_path
from omnicompany.protocol.events import FactoryEvent

logger = logging.getLogger(__name__)

_EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    trace_id    TEXT NOT NULL,
    parent_id   TEXT,
    event_type  TEXT NOT NULL,
    source      TEXT NOT NULL,
    tags        TEXT NOT NULL DEFAULT '[]',
    timestamp   TEXT NOT NULL,
    data        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_trace ON events (trace_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_type ON events (event_type);
"""


@dataclass(frozen=True)
class PublishedAgentEvent:
    event: FactoryEvent
    db_path: Path

    @property
    def event_id(self) -> str:
        return self.event.id


def make_agent_event(
    *,
    trace_id: str,
    event_type: str,
    source: str,
    payload: dict[str, Any],
    parent_id: str | None = None,
    tags: list[str] | None = None,
) -> FactoryEvent:
    return FactoryEvent(
        trace_id=trace_id,
        parent_id=parent_id,
        event_type=event_type,
        source=source,
        payload=_json_safe(payload),
        tags=list(tags or []),
    )


def publish_agent_event_sync(
    *,
    trace_id: str,
    event_type: str,
    source: str,
    payload: dict[str, Any],
    parent_id: str | None = None,
    tags: list[str] | None = None,
    basename: str = "events.db",
) -> PublishedAgentEvent | None:
    """Publish a FactoryEvent to the canonical SQLite EventBus table.

    This is for compatibility code that cannot await an EventBus. It writes the
    same FactoryEvent envelope and table shape as SQLiteBus, keeping the
    canonical event DB as the record surface.
    """
    event = make_agent_event(
        trace_id=trace_id,
        parent_id=parent_id,
        event_type=event_type,
        source=source,
        payload=payload,
        tags=tags,
    )
    db_path = resolve_unified_db_path(basename)
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            conn.executescript(_EVENTS_SCHEMA)
            conn.execute(
                "INSERT INTO events (id, trace_id, parent_id, event_type, source, tags, timestamp, data) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.id,
                    event.trace_id,
                    event.parent_id,
                    event.event_type,
                    event.source,
                    json.dumps(event.tags, ensure_ascii=False),
                    event.timestamp.isoformat(),
                    event.model_dump_json(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return PublishedAgentEvent(event=event, db_path=db_path)
    except Exception as exc:
        logger.warning("agent event bridge sync publish failed: %s", exc)
        return None


async def publish_agent_event(
    bus: Any,
    *,
    trace_id: str,
    event_type: str,
    source: str,
    payload: dict[str, Any],
    parent_id: str | None = None,
    tags: list[str] | None = None,
) -> str | None:
    if bus is None:
        return None
    event = make_agent_event(
        trace_id=trace_id,
        parent_id=parent_id,
        event_type=event_type,
        source=source,
        payload=payload,
        tags=tags,
    )
    try:
        return await bus.publish(event)
    except Exception as exc:
        logger.warning("agent event bridge publish failed: %s", exc)
        return None


def _json_safe(data: dict[str, Any]) -> dict[str, Any]:
    try:
        json.dumps(data, ensure_ascii=False, default=str)
        return data
    except (TypeError, ValueError):
        return {key: _json_safe_value(value) for key, value in data.items()}


def _json_safe_value(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False, default=str)
        return value
    except (TypeError, ValueError):
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if hasattr(value, "__dict__"):
            return {key: _json_safe_value(item) for key, item in vars(value).items()}
        return str(value)


__all__ = [
    "PublishedAgentEvent",
    "make_agent_event",
    "publish_agent_event",
    "publish_agent_event_sync",
]
