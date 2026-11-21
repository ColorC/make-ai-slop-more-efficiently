# [OMNI] origin=human ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:bus.redis_streams.client.py"
"""
OmniBusClient — Redis Streams 事件总线客户端

职责:
- 发布 FactoryEvent 到 Redis Streams
- 订阅并消费特定类型的事件
- 管理消费者组和 ACK 机制

Stream Key 设计:
- omnicompany:trace:{trace_id}  — 每个任务的独立事件流
- omnicompany:global             — 全局流，所有事件的镜像，用于监控/审计

所有事件同时写入 trace 流和 global 流 (双写)。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Sequence

import redis.asyncio as aioredis

from omnicompany.protocol.events import FactoryEvent
from omnicompany.protocol.registry import EventType

logger = logging.getLogger(__name__)

GLOBAL_STREAM = "omnicompany:global"


def _trace_stream(trace_id: str) -> str:
    return f"omnicompany:trace:{trace_id}"


class OmniBusClient:
    """Redis Streams 事件总线客户端

    用法:
        async with OmniBusClient() as bus:
            await bus.publish(event)
            async for event in bus.subscribe(["task.*"], "my-group", "worker-1"):
                process(event)
                await bus.ack(event)
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        *,
        max_stream_len: int = 10_000,
    ):
        self._redis_url = redis_url
        self._max_stream_len = max_stream_len
        self._redis: aioredis.Redis | None = None

    async def connect(self) -> None:
        self._redis = aioredis.from_url(
            self._redis_url, decode_responses=False
        )
        await self._redis.ping()
        logger.info("OmniBus connected to %s", self._redis_url)

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    async def __aenter__(self) -> OmniBusClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    @property
    def redis(self) -> aioredis.Redis:
        if self._redis is None:
            raise RuntimeError("OmniBusClient not connected. Call connect() first.")
        return self._redis

    # 发布

    async def publish(self, event: FactoryEvent) -> str:
        """发布事件到总线 (双写: trace 流 + global 流)

        Returns:
            Redis Stream entry ID (from global stream)
        """
        fields = event.to_stream_dict()
        trace_key = _trace_stream(event.trace_id)

        # 双写: 使用 pipeline 保证原子性
        async with self.redis.pipeline(transaction=False) as pipe:
            pipe.xadd(
                trace_key,
                fields,  # type: ignore[arg-type]
                maxlen=self._max_stream_len,
            )
            pipe.xadd(
                GLOBAL_STREAM,
                fields,  # type: ignore[arg-type]
                maxlen=self._max_stream_len,
            )
            results = await pipe.execute()

        stream_id = results[1]  # global stream 的 entry ID
        logger.debug(
            "Published %s [%s] trace=%s",
            event.event_type,
            event.id,
            event.trace_id,
        )
        return stream_id.decode() if isinstance(stream_id, bytes) else str(stream_id)

    # 订阅 (消费者组)

    async def ensure_group(
        self, stream: str, group: str, start_id: str = "0"
    ) -> None:
        """确保消费者组存在 (幂等)"""
        try:
            await self.redis.xgroup_create(stream, group, id=start_id, mkstream=True)
        except aioredis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    async def subscribe(
        self,
        group: str,
        consumer: str,
        *,
        stream: str = GLOBAL_STREAM,
        event_types: Sequence[str | EventType] | None = None,
        block_ms: int = 1000,
        count: int = 10,
    ) -> AsyncIterator[FactoryEvent]:
        """订阅并消费事件

        Args:
            group: 消费者组名
            consumer: 消费者名
            stream: 监听的 stream key
            event_types: 过滤的事件类型 (None = 全部)
            block_ms: XREADGROUP 阻塞超时
            count: 每次拉取最大条数

        Yields:
            FactoryEvent 实例
        """
        await self.ensure_group(stream, group)

        type_filter: set[str] | None = None
        if event_types:
            type_filter = {
                t.value if isinstance(t, EventType) else t for t in event_types
            }

        while True:
            try:
                entries = await self.redis.xreadgroup(
                    group,
                    consumer,
                    {stream: ">"},
                    count=count,
                    block=block_ms,
                )
            except asyncio.CancelledError:
                break

            if not entries:
                continue

            for _stream_name, messages in entries:
                for msg_id, fields in messages:
                    try:
                        event = FactoryEvent.from_stream_dict(fields)
                    except Exception:
                        logger.warning("Failed to parse event %s, skipping", msg_id)
                        await self.redis.xack(stream, group, msg_id)
                        continue

                    if type_filter and event.event_type not in type_filter:
                        await self.redis.xack(stream, group, msg_id)
                        continue

                    # 在 event 上附加 stream message ID 以便后续 ack
                    event._stream_msg_id = msg_id  # type: ignore[attr-defined]
                    event._stream_key = stream  # type: ignore[attr-defined]
                    event._group = group  # type: ignore[attr-defined]
                    yield event

    async def ack(self, event: FactoryEvent) -> None:
        """确认事件已处理"""
        msg_id = getattr(event, "_stream_msg_id", None)
        stream_key = getattr(event, "_stream_key", None)
        group = getattr(event, "_group", None)
        if msg_id and stream_key and group:
            await self.redis.xack(stream_key, group, msg_id)

    # 查询

    async def read_trace(
        self, trace_id: str, start: str = "-", end: str = "+", count: int = 1000
    ) -> list[FactoryEvent]:
        """读取某个 trace 的全部事件 (用于回放/调试)"""
        stream_key = _trace_stream(trace_id)
        entries = await self.redis.xrange(stream_key, start, end, count=count)
        events = []
        for _msg_id, fields in entries:
            try:
                events.append(FactoryEvent.from_stream_dict(fields))
            except Exception:
                logger.warning("Failed to parse event in trace %s", trace_id)
        return events

    async def tail(
        self,
        stream: str = GLOBAL_STREAM,
        last_id: str = "$",
        block_ms: int = 2000,
    ) -> AsyncIterator[FactoryEvent]:
        """实时 tail 事件流 (非消费者组模式, 用于监控)"""
        cursor = last_id
        while True:
            try:
                entries = await self.redis.xread(
                    {stream: cursor}, count=50, block=block_ms
                )
            except asyncio.CancelledError:
                break

            if not entries:
                continue

            for _stream_name, messages in entries:
                for msg_id, fields in messages:
                    cursor = msg_id
                    try:
                        yield FactoryEvent.from_stream_dict(fields)
                    except Exception:
                        logger.warning("Failed to parse event %s", msg_id)
