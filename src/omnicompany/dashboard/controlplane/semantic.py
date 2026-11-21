# [OMNI] origin=ai-ide ts=2026-05-09 type=infra
# [OMNI] material_id="material:dashboard.controlplane.semantic.types_loops_pain_endpoints.py"
"""controlplane/semantic.py — semantic_types / open-loops / pain endpoints.

URL 不变:
    GET /api/v2/types       semantic_types 列表 (active filter + search)
    GET /api/v2/open-loops  未闭合 routing_events
    GET /api/v2/pain        pain_signals
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from ._db_helpers import row_to_dict, sem_db

semantic_router = APIRouter(prefix="/v2", tags=["semantic"])


@semantic_router.get("/types")
def api_types(
    limit: int = Query(100, ge=1, le=500),
    q: str = Query("", description="Search in type_id or description"),
    active_only: bool = Query(True),
):
    """List semantic types."""
    conn = sem_db()
    if not conn:
        return []
    try:
        has_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='semantic_types'"
        ).fetchone()
        if not has_table:
            return []
        wheres: list[str] = []
        params: list[Any] = []
        if active_only:
            wheres.append("active=1")
        if q:
            wheres.append("(type_id LIKE ? OR description LIKE ?)")
            params.extend([f"%{q}%", f"%{q}%"])
        where_clause = (" WHERE " + " AND ".join(wheres)) if wheres else ""
        rows = conn.execute(
            f"""SELECT type_id, level, parent_type, description, exemplars,
                       ta_domain, ta_action, ta_entity, ta_format,
                       created_at, active, required_fields
                FROM semantic_types{where_clause}
                ORDER BY type_id LIMIT ?""",
            params + [limit],
        ).fetchall()
        return [row_to_dict(r) for r in rows]
    finally:
        conn.close()


@semantic_router.get("/open-loops")
def api_open_loops(last_rounds: int = 20, round_num: int | None = None):
    """未闭合 Intent 列表."""
    conn = sem_db()
    if not conn:
        return []
    try:
        if round_num is not None:
            where = "AND round_num=?"
            params: list[Any] = [round_num]
        else:
            max_r = conn.execute(
                "SELECT MAX(round_num) FROM routing_events WHERE round_num IS NOT NULL"
            ).fetchone()[0]
            if max_r is None:
                return []
            where = "AND round_num >= ?"
            params = [max(0, max_r - last_rounds)]
        rows = conn.execute(
            f"""SELECT * FROM routing_events
                WHERE (route_found=0 OR agent_success=0 OR agent_success IS NULL)
                {where} ORDER BY round_num DESC, id DESC LIMIT 100""",
            params,
        ).fetchall()
        return [row_to_dict(r) for r in rows]
    finally:
        conn.close()


@semantic_router.get("/pain")
def api_pain(node_id: str | None = None, round_num: int | None = None, limit: int = 30):
    """痛觉事件."""
    conn = sem_db()
    if not conn:
        return []
    try:
        has_ps = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pain_signals'"
        ).fetchone()
        if not has_ps:
            return []
        cols = {r[1] for r in conn.execute("PRAGMA table_info(pain_signals)").fetchall()}
        q = "SELECT * FROM pain_signals WHERE 1=1"
        params: list[Any] = []
        if node_id:
            q += " AND node_id LIKE ?"
            params.append(node_id + "%")
        if round_num is not None and "round_num" in cols:
            q += " AND round_num=?"
            params.append(round_num)
        q += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
