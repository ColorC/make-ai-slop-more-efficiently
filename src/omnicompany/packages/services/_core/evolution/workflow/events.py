# [OMNI] origin=codex domain=services/evolution ts=2026-06-13 type=infrastructure status=active
# [OMNI] material_id="material:core.evolution.workflow.eventbus_bridge.py"
"""EventBus bridge for evolution workflow lifecycle events."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from omnicompany.bus.sqlite import SQLiteBus
from omnicompany.protocol.events import FactoryEvent

logger = logging.getLogger(__name__)

DEFAULT_WORKFLOW_SOURCE = "evolution.workflow"
DEFAULT_WORKFLOW_TAGS = ["evolution", "workflow"]


def make_workflow_event(
    *,
    trace_id: str,
    event_type: str,
    payload: dict[str, Any],
    source: str = DEFAULT_WORKFLOW_SOURCE,
    parent_id: str | None = None,
    tags: list[str] | None = None,
) -> FactoryEvent:
    return FactoryEvent(
        trace_id=trace_id or DEFAULT_WORKFLOW_SOURCE,
        parent_id=parent_id,
        event_type=event_type,
        source=source,
        payload=_json_safe(payload),
        tags=list(tags or DEFAULT_WORKFLOW_TAGS),
    )


async def publish_workflow_event(
    bus: Any | None = None,
    *,
    trace_id: str,
    event_type: str,
    payload: dict[str, Any],
    source: str = DEFAULT_WORKFLOW_SOURCE,
    parent_id: str | None = None,
    tags: list[str] | None = None,
    bus_path: str | Path | None = None,
) -> str | None:
    """Publish a workflow event without creating a second event authority."""
    event = make_workflow_event(
        trace_id=trace_id,
        parent_id=parent_id,
        event_type=event_type,
        source=source,
        payload=payload,
        tags=tags,
    )
    try:
        if bus is not None:
            return await bus.publish(event)

        sqlite_bus = SQLiteBus(str(bus_path) if bus_path else None)
        await sqlite_bus.connect()
        try:
            return await sqlite_bus.publish(event)
        finally:
            await sqlite_bus.close()
    except Exception as exc:
        logger.warning("evolution workflow event publish failed: %s", exc)
        return None


def _json_safe(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(json.dumps(payload, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return {key: str(value) for key, value in payload.items()}


__all__ = [
    "DEFAULT_WORKFLOW_SOURCE",
    "DEFAULT_WORKFLOW_TAGS",
    "make_workflow_event",
    "publish_workflow_event",
]
