# [OMNI] origin=codex domain=services/_core/omnicompany ts=2026-06-13T06:00:00+08:00 type=infra status=active
# [OMNI] material_id="material:core.omnicompany.material_event_publisher.py"
"""同步材料事件发布助手。

公司级材料写入口是同步落盘路径, 而 EventBus 是 async API。本模块给这些写入口一个
短连接入口: 写入统一 data/events.db, 失败只告警, 不阻断业务落盘。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import uuid
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from omnicompany.protocol.events import FactoryEvent

_log = logging.getLogger(__name__)


def _event_tags(event_type: str, tags: Sequence[str] | None) -> list[str]:
    out: list[str] = ["omni.material", event_type]
    for tag in tags or ():
        if tag not in out:
            out.append(tag)
    return out


def _trace_id_for(event_type: str, payload: Mapping[str, Any], trace_id: str | None) -> str:
    if trace_id:
        return trace_id
    for key in ("trace_id", "id", "path", "saved_path", "ref_id"):
        value = payload.get(key)
        if value:
            text = str(value).replace("\\", "/")
            return f"material:{event_type}:{text[:160]}"
    return f"material:{event_type}:{uuid.uuid4().hex}"


async def _publish_material_event_async(
    event_type: str,
    payload: Mapping[str, Any],
    *,
    source: str,
    trace_id: str | None,
    parent_id: str | None,
    tags: Sequence[str] | None,
    event_id: str | None,
) -> str:
    from omnicompany.bus.sqlite import SQLiteBus

    payload_dict = dict(payload)
    event_kwargs: dict[str, Any] = {}
    if event_id:
        event_kwargs["id"] = event_id
    event = FactoryEvent(
        **event_kwargs,
        trace_id=_trace_id_for(event_type, payload_dict, trace_id),
        parent_id=parent_id,
        event_type=event_type,
        source=source,
        payload=payload_dict,
        tags=_event_tags(event_type, tags),
    )
    bus = SQLiteBus()
    await bus.connect()
    try:
        try:
            return await bus.publish(event)
        except sqlite3.IntegrityError:
            if event_id:
                return event.id
            raise
    finally:
        await bus.close()


def _run_async_in_short_thread(coro: Any) -> Any:
    result: dict[str, Any] = {}

    def runner() -> None:
        try:
            result["event_id"] = asyncio.run(coro)
        except Exception as exc:  # noqa: BLE001
            result["error"] = exc

    thread = threading.Thread(target=runner, name="omni-material-event-publish", daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result["event_id"]


async def _query_material_events_async(
    *,
    event_type: str | None,
    source: str | None,
    tags: Sequence[str] | None,
    limit: int,
) -> list[FactoryEvent]:
    from omnicompany.bus.sqlite import SQLiteBus

    bus = SQLiteBus()
    await bus.connect()
    try:
        query_tags = ["omni.material"]
        for tag in tags or ():
            if tag not in query_tags:
                query_tags.append(tag)
        return await bus.query(
            event_type=event_type,
            source=source,
            tags=query_tags,
            limit=limit,
        )
    finally:
        await bus.close()


def publish_material_event(
    event_type: str,
    payload: Mapping[str, Any],
    *,
    source: str = "omnicompany.material",
    trace_id: str | None = None,
    parent_id: str | None = None,
    tags: Sequence[str] | None = None,
    event_id: str | None = None,
) -> str | None:
    """发布一条公司级材料事件。

    这是 best-effort 同步入口: 任何 DB/序列化/事件循环错误都会被记录为 warning,
    调用方的业务写入不应因此失败。
    """
    try:
        coro = _publish_material_event_async(
            event_type,
            payload,
            source=source,
            trace_id=trace_id,
            parent_id=parent_id,
            tags=tags,
            event_id=event_id,
        )
        # Always isolate the async SQLiteBus lifecycle in a short thread. Using
        # asyncio.run() in the caller thread clears that thread's default event
        # loop on Python 3.11, which breaks older synchronous test fixtures and
        # tools that still call asyncio.get_event_loop().
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return str(_run_async_in_short_thread(coro))
        return str(_run_async_in_short_thread(coro))
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "publish_material_event failed: event_type=%s source=%s error=%s",
            event_type,
            source,
            exc,
        )
        return None


def query_material_events(
    *,
    event_type: str | None = None,
    source: str | None = None,
    tags: Sequence[str] | None = None,
    limit: int = 1000,
) -> list[FactoryEvent]:
    """Query material events from the unified SQLite bus.

    This mirrors `publish_material_event`: callers stay synchronous, the async
    SQLiteBus lifecycle is isolated in a short thread, and failures are
    best-effort because material discovery must not break dashboard requests.
    """
    try:
        return list(_run_async_in_short_thread(_query_material_events_async(
            event_type=event_type,
            source=source,
            tags=tags,
            limit=limit,
        )))
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "query_material_events failed: event_type=%s source=%s tags=%s error=%s",
            event_type,
            source,
            tags,
            exc,
        )
        return []


__all__ = ["publish_material_event", "query_material_events"]
