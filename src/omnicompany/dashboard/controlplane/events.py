# [OMNI] origin=ai-ide ts=2026-05-09 type=infra
# [OMNI] material_id="material:dashboard.controlplane.events.intent_step_endpoints.py"
"""controlplane/events.py — intent_traces.db / events.db 端点 (阶段 9 拆离自 app.py).

URL 不变:
    GET /api/events                    intent_steps 列表 (sortable/searchable)
    GET /api/events/{event_id}         单 intent_step 详情
    GET /api/system_events             events.db 原始事件
    GET /api/event_types               distinct event_type 给前端 filter dropdown
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Query

from ._db_helpers import db_paths, safe_conn

events_router = APIRouter(tags=["events"])


@events_router.get("/events")
def api_events(
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    tool: str = Query("", description="Filter by tool_name"),
    ok: str = Query("", description="Filter: ok|fail|all"),
    q: str = Query("", description="Search in desc/rationale/result"),
):
    """Full event table from intent_traces.db — sortable, searchable."""
    paths = db_paths()
    conn = safe_conn(paths["intent_traces"])
    if conn is None:
        return {"items": [], "total": 0}
    try:
        wheres: list[str] = []
        params: list[Any] = []
        if tool:
            wheres.append("tool_name = ?")
            params.append(tool)
        if ok == "ok":
            wheres.append("tool_exit_ok = 1")
        elif ok == "fail":
            wheres.append("(tool_exit_ok = 0 OR tool_exit_ok IS NULL)")
        if q:
            wheres.append("(desc LIKE ? OR rationale LIKE ? OR tool_result LIKE ?)")
            wild = f"%{q}%"
            params.extend([wild, wild, wild])

        where_clause = (" WHERE " + " AND ".join(wheres)) if wheres else ""

        count_row = conn.execute(
            f"SELECT COUNT(*) FROM intent_steps{where_clause}", params
        ).fetchone()
        total = count_row[0] if count_row else 0

        rows = conn.execute(
            f"""SELECT id, trace_id, step_num, tool_name, input_types, output_types,
                       desc, rationale, tool_args_summary, tool_result, tool_exit_ok,
                       timestamp, route_node_id
                FROM intent_steps{where_clause}
                ORDER BY rowid DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()
        items = [dict(r) for r in rows]
        return {"items": items, "total": total}
    except sqlite3.Error as e:
        return {"items": [], "total": 0, "error": str(e)}
    finally:
        conn.close()


@events_router.get("/events/{event_id}")
def api_event_detail(event_id: int):
    """Full detail for a single event — shows all columns."""
    paths = db_paths()
    conn = safe_conn(paths["intent_traces"])
    if conn is None:
        return {"error": "intent_traces.db not found"}
    try:
        row = conn.execute("SELECT * FROM intent_steps WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            return {"error": "not found"}
        return dict(row)
    except sqlite3.Error as e:
        return {"error": str(e)}
    finally:
        conn.close()


@events_router.get("/system_events")
def api_system_events(
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    event_type: str = Query("", description="Filter by event_type"),
    source: str = Query(""),
    q: str = Query("", description="Search in data"),
):
    """Raw system events from events.db."""
    paths = db_paths()
    conn = safe_conn(paths["events"])
    if conn is None:
        return {"items": [], "total": 0}
    try:
        wheres: list[str] = []
        params: list[Any] = []
        if event_type:
            wheres.append("event_type = ?")
            params.append(event_type)
        if source:
            wheres.append("source = ?")
            params.append(source)
        if q:
            wheres.append("data LIKE ?")
            params.append(f"%{q}%")
        where_clause = (" WHERE " + " AND ".join(wheres)) if wheres else ""

        count_row = conn.execute(f"SELECT COUNT(*) FROM events{where_clause}", params).fetchone()
        total = count_row[0] if count_row else 0

        rows = conn.execute(
            f"""SELECT id, trace_id, parent_id, event_type, source, tags, timestamp, data
                FROM events{where_clause}
                ORDER BY rowid DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()
        items = [dict(r) for r in rows]
        return {"items": items, "total": total}
    except sqlite3.Error as e:
        return {"items": [], "total": 0, "error": str(e)}
    finally:
        conn.close()


@events_router.get("/event_types")
def api_event_types():
    """Distinct event types for filter dropdown."""
    paths = db_paths()
    conn = safe_conn(paths["events"])
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT event_type, COUNT(*) as cnt FROM events GROUP BY event_type ORDER BY cnt DESC"
        ).fetchall()
        return [{"type": r["event_type"], "count": r["cnt"]} for r in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()
