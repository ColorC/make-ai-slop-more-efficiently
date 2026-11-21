# [OMNI] origin=human domain=omnicompany/core ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:omnicompany.core.observe.event_query.engine.py"
"""omnicompany.core.observe — 统一观测 SDK（基础设施）

CLI 和 Dashboard 共享的观测层 API。
所有函数从 events.db 读取数据，不依赖 intent_traces.db 或 semantic_network.db。

用法:
    from omnicompany.core.observe import list_traces, read_trace, tail_events

    # 在 CLI 中
    traces = await list_traces(domain="demogame", n=10)

    # 在 Dashboard 中
    @app.get("/api/v3/traces")
    async def api_traces(limit: int = 30):
        return await list_traces(n=limit)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TraceSummary:
    """一条 trace 的摘要信息。"""
    trace_id: str
    source: str
    event_count: int
    first_ts: str
    last_ts: str
    has_error: bool


@dataclass
class EventSummary:
    """单条事件的摘要。"""
    id: int
    trace_id: str
    event_type: str
    source: str
    timestamp: str
    payload: dict[str, Any]
    metadata: dict[str, Any]
    diagnosis: str = ""



# ── 核心查询 API ─────────────────────────────────────────────────────────────

async def list_traces(
    domain: str = "*",
    n: int = 20,
    source: str | None = None,
) -> list[TraceSummary]:
    """列出最近的 trace 列表。

    Args:
        domain: 领域过滤（"*" = 搜索所有 domain 目录）
        n:      返回条数上限
        source: 按 source 过滤
    """
    db_paths = _find_event_dbs(domain)
    all_traces: list[TraceSummary] = []

    for db_path in db_paths:
        conn = _safe_conn(db_path)
        if conn is None:
            continue
        try:
            wheres = []
            params: list[Any] = []
            if source:
                wheres.append("source = ?")
                params.append(source)
            where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""

            rows = conn.execute(
                f"""SELECT trace_id,
                           source,
                           COUNT(*) as cnt,
                           MIN(timestamp) as first_ts,
                           MAX(timestamp) as last_ts,
                           SUM(CASE WHEN event_type='task.error' THEN 1 ELSE 0 END) as err
                    FROM events{where_sql}
                    GROUP BY trace_id
                    ORDER BY first_ts DESC LIMIT ?""",
                params + [n],
            ).fetchall()

            for r in rows:
                all_traces.append(TraceSummary(
                    trace_id=r[0],
                    source=r[1] or "",
                    event_count=r[2],
                    first_ts=r[3] or "",
                    last_ts=r[4] or "",
                    has_error=r[5] > 0,
                ))
        except sqlite3.Error as e:
            logger.debug("list_traces: %s error: %s", db_path, e)
        finally:
            conn.close()

    # 按时间降序排列，截取 top-n
    all_traces.sort(key=lambda t: t.first_ts, reverse=True)
    return all_traces[:n]


async def read_trace(
    trace_id: str,
    domain: str = "*",
) -> list[EventSummary]:
    """读取指定 trace 的全部事件。

    自动搜索所有 domain 的 events.db。
    """
    db_paths = _find_event_dbs(domain)

    for db_path in db_paths:
        conn = _safe_conn(db_path)
        if conn is None:
            continue
        try:
            rows = conn.execute(
                """SELECT id, trace_id, event_type, source, timestamp, data
                   FROM events
                   WHERE trace_id = ?
                   ORDER BY timestamp""",
                (trace_id,),
            ).fetchall()
            if not rows:
                continue

            results = []
            for r in rows:
                data = {}
                try:
                    data = json.loads(r[5]) if r[5] else {}
                except (json.JSONDecodeError, TypeError):
                    pass
                payload = data.get("payload", {})
                metadata = data.get("metadata", {})
                results.append(EventSummary(
                    id=r[0],
                    trace_id=r[1],
                    event_type=r[2],
                    source=r[3] or "",
                    timestamp=r[4] or "",
                    payload=payload,
                    metadata=metadata,
                    diagnosis=payload.get("diagnosis", ""),
                ))
            return results
        except sqlite3.Error as e:
            logger.debug("read_trace: %s error: %s", db_path, e)
        finally:
            conn.close()

    return []


async def tail_events(
    domain: str = "*",
    source: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
) -> list[EventSummary]:
    """获取最近的事件（静态快照模式）。

    未来可扩展为真正的 async iterator 实时 tail。
    """
    db_paths = _find_event_dbs(domain)
    all_events: list[EventSummary] = []

    for db_path in db_paths:
        conn = _safe_conn(db_path)
        if conn is None:
            continue
        try:
            wheres = []
            params: list[Any] = []
            if source:
                wheres.append("source = ?")
                params.append(source)
            if event_type:
                wheres.append("event_type = ?")
                params.append(event_type)
            where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""

            rows = conn.execute(
                f"""SELECT id, trace_id, event_type, source, timestamp, data
                    FROM events{where_sql}
                    ORDER BY rowid DESC LIMIT ?""",
                params + [limit],
            ).fetchall()

            for r in rows:
                data = {}
                try:
                    data = json.loads(r[5]) if r[5] else {}
                except (json.JSONDecodeError, TypeError):
                    pass
                payload = data.get("payload", {})
                metadata = data.get("metadata", {})
                all_events.append(EventSummary(
                    id=r[0],
                    trace_id=r[1],
                    event_type=r[2],
                    source=r[3] or "",
                    timestamp=r[4] or "",
                    payload=payload,
                    metadata=metadata,
                    diagnosis=payload.get("diagnosis", ""),
                ))
        except sqlite3.Error as e:
            logger.debug("tail_events: %s error: %s", db_path, e)
        finally:
            conn.close()

    all_events.sort(key=lambda e: e.timestamp, reverse=True)
    return all_events[:limit]


def health_check() -> dict[str, Any]:
    """系统健康检查。

    返回每个 domain 的 events.db 状态、事件总数、最新事件时间等。
    """
    from omnicompany.core.config import _project_root
    data_root = _project_root() / "data"
    if not data_root.exists():
        return {"status": "no_data_dir", "domains": {}}

    domains: dict[str, Any] = {}
    for d in sorted(data_root.iterdir()):
        if not d.is_dir():
            continue
        db_path = d / "events.db"
        if not db_path.exists():
            domains[d.name] = {"status": "no_db"}
            continue

        conn = _safe_conn(db_path)
        if conn is None:
            domains[d.name] = {"status": "conn_error"}
            continue
        try:
            row = conn.execute("SELECT COUNT(*), MAX(timestamp) FROM events").fetchone()
            domains[d.name] = {
                "status": "ok",
                "event_count": row[0] or 0,
                "latest_event": row[1] or "",
                "db_size_kb": round(db_path.stat().st_size / 1024, 1),
            }
        except sqlite3.Error as e:
            domains[d.name] = {"status": f"error: {e}"}
        finally:
            conn.close()

    return {"status": "ok", "domains": domains}


# ── 内部工具 ─────────────────────────────────────────────────────────────────

def _find_event_dbs(domain: str = "*") -> list[Path]:
    """查找指定 domain（或所有）的 events.db 文件。"""
    from omnicompany.core.config import _project_root, resolve_db_dir
    if domain != "*":
        p = resolve_db_dir(domain) / "events.db"
        return [p] if p.exists() else []

    # 搜索所有 domain
    data_root = _project_root() / "data"
    if not data_root.exists():
        return []

    dbs = []
    for d in data_root.iterdir():
        if d.is_dir():
            db = d / "events.db"
            if db.exists():
                dbs.append(db)

    # 兼容旧路径
    legacy_paths = [
        data_root / "autonomous" / "events.db",
        data_root / "evolution_phase1.db",
    ]
    for lp in legacy_paths:
        if lp.exists() and lp not in dbs:
            dbs.append(lp)

    return dbs


def _safe_conn(db_path: Path) -> sqlite3.Connection | None:
    """安全建立 SQLite 连接。"""
    try:
        conn = sqlite3.connect(str(db_path), timeout=3.0)
        return conn
    except sqlite3.Error:
        return None
