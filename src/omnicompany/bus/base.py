# [OMNI] origin=human ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:bus.event_bus.abstract_interface.py"
"""
EventBus 抽象接口

所有总线实现（SQLiteBus / RedisBus / 未来的 Kafka 等）
都实现此接口。上层代码只依赖 EventBus，不依赖具体传输。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Sequence

from omnicompany.protocol.events import FactoryEvent
from omnicompany.protocol.registry import EventType


class EventBus(ABC):
    """事件总线抽象基类"""

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    async def __aenter__(self) -> EventBus:
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    @abstractmethod
    async def publish(self, event: FactoryEvent) -> str:
        """发布事件，返回事件 ID"""
        ...

    @abstractmethod
    async def subscribe(
        self,
        group: str,
        consumer: str,
        *,
        event_types: Sequence[str | EventType] | None = None,
        tags: Sequence[str] | None = None,
    ) -> AsyncIterator[FactoryEvent]:
        """订阅事件流

        Args:
            group: 消费者组名。同组内只有一个消费者处理同一事件。
            consumer: 消费者标识。
            event_types: 事件类型过滤 (按 event_type 字段)。
            tags: 语义标签过滤 (AND 语义: 事件必须包含所有指定标签)。
        """
        ...
        yield  # type: ignore[misc]  # make it an async generator

    @abstractmethod
    async def ack(self, event: FactoryEvent) -> None:
        """确认事件已处理"""
        ...

    @abstractmethod
    async def read_trace(self, trace_id: str) -> list[FactoryEvent]:
        """读取某个 trace 的全部事件"""
        ...

    @abstractmethod
    async def tail(self) -> AsyncIterator[FactoryEvent]:
        """实时 tail 事件流"""
        ...
        yield  # type: ignore[misc]
