# [OMNI] origin=human ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:bus.memory_bus.implementation.py"
"""MemoryBus — 内存 EventBus 实现（用于测试和进化层 DAG 执行）

不持久化，不支持订阅。仅满足 TeamRunner 的 publish 需求。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Sequence

from omnicompany.bus.base import EventBus
from omnicompany.protocol.events import FactoryEvent
from omnicompany.protocol.registry import EventType


class MemoryBus(EventBus):
    """纯内存 EventBus — 不持久化，适用于短生命周期场景"""

    def __init__(self):
        self._events: list[FactoryEvent] = []
        self._connected = False

    async def connect(self) -> None:
        self._connected = True

    async def close(self) -> None:
        self._connected = False

    async def publish(self, event: FactoryEvent) -> str:
        self._events.append(event)
        return event.id

    async def subscribe(
        self,
        group: str,
        consumer: str,
        *,
        event_types: Sequence[str | EventType] | None = None,
        tags: Sequence[str] | None = None,
    ) -> AsyncIterator[FactoryEvent]:
        for event in self._events:
            yield event

    async def ack(self, event: FactoryEvent) -> None:
        pass

    async def read_trace(self, trace_id: str) -> list[FactoryEvent]:
        return [e for e in self._events if e.trace_id == trace_id]

    async def tail(self) -> AsyncIterator[FactoryEvent]:
        for event in self._events:
            yield event
