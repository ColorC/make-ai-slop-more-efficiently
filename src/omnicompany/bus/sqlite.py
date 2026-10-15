# [OMNI] origin=claude-code ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:bus.sqlite_bus.implementation.py"
"""
SQLiteBus — 基于 SQLite 的事件总线

默认实现，零外部依赖（sqlite3 是 Python 标准库）。
单文件持久化，进程崩溃事件不丢，支持 SQL 查询回放。

支持:
- 语义标签过滤 (tags): AND 语义，事件必须包含所有指定标签
- 组隔离 (consumer group): 消费位移持久化，同组竞争消费

性能: 轻松支撑 1000+ 写入/秒，远超 Agent 场景需求 (~100 事件/秒)。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from omnicompany.bus.base import EventBus
from omnicompany.protocol.events import FactoryEvent
from omnicompany.protocol.registry import EventType

logger = logging.getLogger(__name__)

_SCHEMA = """
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

CREATE INDEX IF NOT EXISTS idx_events_trace    ON events (trace_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_type     ON events (event_type);
CREATE INDEX IF NOT EXISTS idx_events_ts       ON events (timestamp);

CREATE TABLE IF NOT EXISTS consumer_offsets (
    group_name    TEXT NOT NULL,
    last_event_ts TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (group_name)
);
"""


class SQLiteBus(EventBus):
    """基于 SQLite 的事件总线

    每个事件以完整 JSON 存入 data 列，同时提取关键字段建索引。
    查询时反序列化 data 列还原 FactoryEvent。

    组隔离: 通过 consumer_offsets 表记录每个 group 的消费位移，
    同组竞争消费（谁先读到谁处理），不同组独立消费。

    用法:
        async with SQLiteBus("events.db") as bus:
            await bus.publish(event)
            events = await bus.read_trace(trace_id)
    """

    def __init__(self, db_path: str | Path | None = None, *, basename: str = "events.db"):
        """Move 8 后：引擎层强制路径在 <repo>/data/ 下。

        - db_path=None: 落到 data/<basename>（基于 basename 选 events.db / ide_events.db）
        - db_path 在 data/ 下:  接受
        - db_path 不在 data/ 下: 重导向到 data/<原 basename>，记 WARNING（不抛异常）

        历史上 30+ 处 call site 写死了 "data/events.db", "omnicompany_events.db",
        ~/.omnicompany/events.db 等乱七八糟的路径，导致 13 个 stray DB 文件
        散落在仓库各处。此处不再信任传入路径，统一由引擎裁决。
        """
        from omnicompany.core.config import resolve_unified_db_path

        if db_path is None:
            resolved = resolve_unified_db_path(basename)
        else:
            p = Path(db_path)
            if not p.is_absolute():
                p = (Path.cwd() / p).resolve()
            else:
                p = p.resolve()

            data_root = resolve_unified_db_path("events.db").parent
            try:
                p.relative_to(data_root)
                # 在 data/ 下 — 但仍然要把它折叠回 unified 路径
                # （data/<sub>/events.db → data/events.db）
                target_basename = (
                    "ide_events.db" if p.name == "ide_events.db" else "events.db"
                )
                resolved = resolve_unified_db_path(target_basename)
                if resolved != p:
                    logger.warning(
                        "SQLiteBus: %s → 重导向到 unified 路径 %s (Move 8)",
                        p, resolved,
                    )
            except ValueError:
                # 完全不在 data/ 下 — 强制重导向
                target_basename = (
                    "ide_events.db" if p.name == "ide_events.db" else "events.db"
                )
                resolved = resolve_unified_db_path(target_basename)
                logger.warning(
                    "SQLiteBus: %s 不在 data/ 下，重导向到 %s (Move 8)",
                    db_path, resolved,
                )

        self._db_path = str(resolved)
        self._conn: sqlite3.Connection | None = None
        self._notify: asyncio.Event = asyncio.Event()

    # 2026-04-21 B7: events.db 膨胀阈值 (实测 2026-04-21 达 9.5GB 才归档, 太晚)
    # 500MB 是经验值: agent.* event 平均 95KB, 500MB ≈ 5000 events ≈ 数天 agent 运行
    _BLOAT_THRESHOLD_BYTES: int = 500 * 1024 * 1024

    def _check_bloat_and_rotate(self) -> None:
        """打开前检查 db 大小, 超过阈值则归档到 data/_archive/events_db_rotation/.

        这是 belt-and-suspenders 防护 — 真正的治理应是 agent.* event payload
        外部化 (大 payload 写独立文件, events 表只存指针), 进阶阶段再做.
        """
        import shutil
        from datetime import datetime

        p = Path(self._db_path)
        if not p.exists():
            return
        size = p.stat().st_size
        if size < self._BLOAT_THRESHOLD_BYTES:
            return
        # 归档到 _archive/events_db_rotation/, 文件名带时间戳 + size
        archive_root = p.parent / "_archive" / "events_db_rotation"
        archive_root.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        size_mb = size // (1024 * 1024)
        archived = archive_root / f"{p.stem}_{ts}_{size_mb}MB{p.suffix}"
        # 2026-08-11/12 事故复盘 (MACHINE-FILE-HYGIENE): 文件被占用时 shutil.move 退化为
        # copy2+unlink, unlink 失败在复制之后才抛 OSError → 归档副本留下、源库原地不动,
        # 每个进程每次打开都再复制一份 500MB, 两天攒出 587 份精确重复 (309.6 GB)。
        # 防线 1: 30 分钟内已有同尺寸归档则跳过(说明刚有人试过且源没变)。
        import time
        for existing in archive_root.glob(f"{p.stem}_*{p.suffix}"):
            try:
                st = existing.stat()
            except OSError:
                continue
            if st.st_size == size and (time.time() - st.st_mtime) < 1800:
                logger.warning("SQLiteBus: %s 已有 30 分钟内同尺寸归档 %s, 跳过本次轮转", p.name, existing.name)
                return
        try:
            shutil.move(str(p), str(archived))
        except OSError as exc:
            # 防线 2: move 的 copy 阶段可能已成功, 只是删源失败 —— 把留下的半程副本清掉,
            # 否则就是精确重复。源库继续沿用。
            try:
                archived.unlink(missing_ok=True)
            except OSError:
                pass
            # 归档是卫生措施, 不是运行前提。别的进程(如 ccdaemon)持有这个文件时
            # Windows 不允许改名, 这里原本会把异常抛给调用方 — 结果是 db 一旦越过
            # 阈值, 每个只想记一条事件的进程都起不来。跳过归档继续用原文件即可;
            # 何况在别人开着的时候把 db 搬走, 本来也会把事件流劈成两半。
            logger.warning(
                "SQLiteBus: %s 已达 %d MB, 但文件被占用无法归档(%s), 本次继续沿用",
                p.name, size_mb, exc,
            )
            return
        # WAL 与 shm 属于刚被归档的那个 db。留在原地会让全新的 db 配上一份旧日志。
        for suffix in ("-wal", "-shm"):
            companion = p.with_name(p.name + suffix)
            if not companion.exists():
                continue
            try:
                shutil.move(str(companion), str(archived) + suffix)
            except OSError as exc:
                logger.warning("SQLiteBus: 归档 %s 失败: %s", companion.name, exc)
        logger.warning(
            "SQLiteBus: %s 已达 %d MB 超阈值 %d MB, 自动归档到 %s",
            p.name, size_mb, self._BLOAT_THRESHOLD_BYTES // (1024 * 1024), archived,
        )

    async def connect(self) -> None:
        self._check_bloat_and_rotate()  # 2026-04-21 B7: 超大阈值自动归档
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")  # 并发读写友好
        self._conn.execute("PRAGMA synchronous=NORMAL")  # 性能与安全的平衡
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        logger.info("SQLiteBus ready → %s", self._db_path)

    async def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def db_path(self) -> Path:
        return Path(self._db_path)

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("SQLiteBus not connected. Call connect() first.")
        return self._conn

    async def publish(self, event: FactoryEvent) -> str:
        self.conn.execute(
            "INSERT INTO events (id, trace_id, parent_id, event_type, source, tags, timestamp, data) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.id,
                event.trace_id,
                event.parent_id,
                event.event_type,
                event.source,
                json.dumps(event.tags),
                event.timestamp.isoformat(),
                event.model_dump_json(),
            ),
        )
        self.conn.commit()
        logger.debug("Published %s [%s] tags=%s", event.event_type, event.id, event.tags)
        self._notify.set()
        return event.id

    async def subscribe(
        self,
        group: str,
        consumer: str,
        *,
        event_types: Sequence[str | EventType] | None = None,
        tags: Sequence[str] | None = None,
    ) -> AsyncIterator[FactoryEvent]:
        type_filter: set[str] | None = None
        if event_types:
            type_filter = {
                t.value if isinstance(t, EventType) else t for t in event_types
            }

        tag_filter: set[str] | None = None
        if tags:
            tag_filter = set(tags)

        # 获取组的消费位移
        last_ts = ""
        if group:
            row = self.conn.execute(
                "SELECT last_event_ts FROM consumer_offsets WHERE group_name = ?",
                (group,),
            ).fetchone()
            if row:
                last_ts = row[0]

        while True:
            rows = self.conn.execute(
                "SELECT data FROM events WHERE timestamp > ? ORDER BY timestamp LIMIT 50",
                (last_ts,),
            ).fetchall()

            for (raw,) in rows:
                event = FactoryEvent.model_validate_json(raw)
                last_ts = event.timestamp.isoformat()

                # 更新组消费位移
                if group:
                    self.conn.execute(
                        "INSERT OR REPLACE INTO consumer_offsets (group_name, last_event_ts, updated_at) "
                        "VALUES (?, ?, ?)",
                        (group, last_ts, datetime.now(timezone.utc).isoformat()),
                    )
                    self.conn.commit()

                if type_filter and event.event_type not in type_filter:
                    continue
                if tag_filter and not tag_filter.issubset(set(event.tags)):
                    continue
                yield event

            if not rows:
                await asyncio.sleep(1.0)

    async def ack(self, event: FactoryEvent) -> None:
        pass  # 消费位移在 subscribe 中自动更新

    async def read_trace(self, trace_id: str) -> list[FactoryEvent]:
        rows = self.conn.execute(
            "SELECT data FROM events WHERE trace_id = ? ORDER BY timestamp",
            (trace_id,),
        ).fetchall()
        return [FactoryEvent.model_validate_json(raw) for (raw,) in rows]

    async def tail(self, *, trace_id: str | None = None) -> AsyncIterator[FactoryEvent]:
        """实时 tail: 轮询新事件，publish() 后即时唤醒。

        Args:
            trace_id: 仅返回指定 trace 的事件（用于 IDE SSE 流）。
        """
        last_ts = ""
        while True:
            if trace_id:
                rows = self.conn.execute(
                    "SELECT data FROM events WHERE timestamp > ? AND trace_id = ? ORDER BY timestamp",
                    (last_ts, trace_id),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT data FROM events WHERE timestamp > ? ORDER BY timestamp",
                    (last_ts,),
                ).fetchall()

            for (raw,) in rows:
                event = FactoryEvent.model_validate_json(raw)
                last_ts = event.timestamp.isoformat()
                yield event

            if not rows:
                self._notify.clear()
                try:
                    await asyncio.wait_for(self._notify.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass

    # SQLite 专属查询方法

    async def replay(
        self,
        *,
        trace_id: str | None = None,
        event_type: str | EventType | None = None,
        source: str | None = None,
        tags: Sequence[str] | None = None,
        limit: int = 100,
    ) -> list[FactoryEvent]:
        """Compatibility alias for bounded event replay queries."""
        return await self.query(
            trace_id=trace_id,
            event_type=event_type,
            source=source,
            tags=tags,
            limit=limit,
        )

    async def query(
        self,
        *,
        trace_id: str | None = None,
        event_type: str | EventType | None = None,
        source: str | None = None,
        tags: Sequence[str] | None = None,
        limit: int = 100,
    ) -> list[FactoryEvent]:
        """灵活查询事件（SQLiteBus 独有能力）"""
        conditions = []
        params: list[str] = []

        if trace_id:
            conditions.append("trace_id = ?")
            params.append(trace_id)
        if event_type:
            t = event_type.value if isinstance(event_type, EventType) else event_type
            conditions.append("event_type = ?")
            params.append(t)
        if source:
            conditions.append("source = ?")
            params.append(source)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT data FROM events {where} ORDER BY timestamp LIMIT ?"
        params.append(str(limit))

        rows = self.conn.execute(sql, params).fetchall()
        events = [FactoryEvent.model_validate_json(raw) for (raw,) in rows]

        # 标签过滤 (在 Python 侧做，SQLite 的 JSON 查询不够灵活)
        if tags:
            tag_filter = set(tags)
            events = [e for e in events if tag_filter.issubset(set(e.tags))]

        return events

    async def count(self, trace_id: str | None = None) -> int:
        """统计事件数量"""
        if trace_id:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM events WHERE trace_id = ?", (trace_id,)
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()
        return row[0] if row else 0
