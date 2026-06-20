# [OMNI] origin=ai-ide ts=2026-05-09 type=infra
# [OMNI] material_id="material:dashboard.controlplane.traces.routing_signal_endpoints.py"
"""controlplane/traces.py — execution rounds + signal spans + trace 聚合.

URL 不变:
    GET /api/v2/rounds                  最近 N 轮摘要
    GET /api/v2/round/{round_num}       单轮 routing + pain
    GET /api/v2/trace/{trace_id}        单 trace Signal 流
    GET /api/v2/trace-list              所有 events.db 聚合 trace 列表
    GET /api/v2/trace-detail/{trace_id} 单 trace 全细节 (events + routing + spans + intent_steps)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, Query

from ._db_helpers import db_paths, discover_event_dbs, row_to_dict, safe_conn, sem_db

traces_router = APIRouter(prefix="/v2", tags=["traces"])


@traces_router.get("/rounds")
def api_rounds(limit: int = Query(20, le=200)):
    """最近 N 轮摘要."""
    conn = sem_db()
    if not conn:
        return []
    try:
        has_er = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='execution_rounds'"
        ).fetchone()
        if has_er:
            rows = conn.execute(
                "SELECT * FROM execution_rounds ORDER BY round_num DESC LIMIT ?", (limit,)
            ).fetchall()
            return [row_to_dict(r) for r in rows]
        # 降级: 从 routing_events 聚合
        rows = conn.execute(
            """SELECT round_num,
                      COUNT(*) as route_total,
                      SUM(route_found) as route_hit,
                      SUM(CASE WHEN agent_success=1 THEN 1 ELSE 0 END) as success_count,
                      MIN(created_at) as started_at
               FROM routing_events WHERE round_num IS NOT NULL
               GROUP BY round_num ORDER BY round_num DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@traces_router.get("/round/{round_num}")
def api_round_detail(round_num: int):
    """单轮详情: routing events + pain signals + execution metadata."""
    conn = sem_db()
    if not conn:
        return {}
    try:
        routes = [row_to_dict(r) for r in conn.execute(
            "SELECT * FROM routing_events WHERE round_num=? ORDER BY id", (round_num,)
        ).fetchall()]
        meta = None
        has_er = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='execution_rounds'"
        ).fetchone()
        if has_er:
            row = conn.execute(
                "SELECT * FROM execution_rounds WHERE round_num=?", (round_num,)
            ).fetchone()
            meta = row_to_dict(row) if row else None
        pain = []
        has_ps = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pain_signals'"
        ).fetchone()
        if has_ps:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(pain_signals)").fetchall()}
            if "round_num" in cols:
                pain = [row_to_dict(r) for r in conn.execute(
                    "SELECT * FROM pain_signals WHERE round_num=? ORDER BY id", (round_num,)
                ).fetchall()]
        return {"round_num": round_num, "meta": meta, "routing_events": routes, "pain_signals": pain}
    finally:
        conn.close()


@traces_router.get("/trace/{trace_id}")
def api_trace(trace_id: str):
    """单次 trace 的完整 Signal 流."""
    conn = sem_db()
    if not conn:
        return {}
    try:
        routes = conn.execute(
            "SELECT * FROM routing_events WHERE trace_id LIKE ? ORDER BY id",
            (trace_id + "%",),
        ).fetchall()
        spans = []
        has_ss = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='signal_spans'"
        ).fetchone()
        if has_ss:
            spans = conn.execute(
                "SELECT * FROM signal_spans WHERE trace_id LIKE ? ORDER BY span_index",
                (trace_id + "%",),
            ).fetchall()
        return {
            "trace_id": trace_id,
            "routing_events": [row_to_dict(r) for r in routes],
            "signal_spans": [row_to_dict(s) for s in spans],
        }
    finally:
        conn.close()


@traces_router.get("/trace-list")
def api_trace_list(
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str = Query("", description="Search in task description"),
    source: str = Query("", description="Filter by source/domain"),
):
    """Aggregated trace list from ALL events.db files across data/.

    Each trace = one unique trace_id, with its first task.intent as description,
    event counts, source domain, and timestamps.
    """
    all_traces: list[dict] = []
    event_dbs = discover_event_dbs()

    for domain, db_path in event_dbs:
        conn = safe_conn(db_path)
        if not conn:
            continue
        try:
            rows = conn.execute("""
                SELECT
                    trace_id,
                    MIN(timestamp) as started_at,
                    MAX(timestamp) as ended_at,
                    COUNT(*) as event_count,
                    SUM(CASE WHEN event_type='agent.tool.call' THEN 1 ELSE 0 END) as tool_calls,
                    SUM(CASE WHEN event_type='agent.llm.request' THEN 1 ELSE 0 END) as llm_calls,
                    MAX(CASE WHEN event_type IN
                        ('task.finish','agent.loop.finish','agent_loop.finish',
                         'agent.turn.end','task.completed') THEN 1 ELSE 0 END) as finished,
                    MAX(CASE WHEN event_type IN
                        ('task.error','agent.loop.error','agent_loop.error') THEN 1 ELSE 0 END) as errored
                FROM events
                GROUP BY trace_id
                ORDER BY MIN(timestamp) DESC
            """).fetchall()

            for row in rows:
                r = dict(row)
                intent_row = conn.execute(
                    "SELECT data FROM events WHERE trace_id=? AND event_type='task.intent' ORDER BY timestamp LIMIT 1",
                    (r["trace_id"],),
                ).fetchone()
                task_desc = None
                src = domain
                if intent_row:
                    try:
                        ev_data = json.loads(intent_row[0])
                        payload = ev_data.get("payload", {})
                        src = ev_data.get("source", domain)
                        task_desc = (
                            payload.get("instruction")
                            or payload.get("task_desc")
                            or (f"{payload['pipeline']}: {payload.get('entry', '')}" if "pipeline" in payload else None)
                        )
                    except (json.JSONDecodeError, KeyError):
                        pass

                status: str
                if r["errored"]:
                    status = "error"
                elif r["finished"]:
                    status = "finished"
                else:
                    try:
                        last = datetime.fromisoformat((r["ended_at"] or "").replace("Z", "+00:00"))
                        if datetime.now(timezone.utc) - last > timedelta(minutes=5):
                            status = "finished"
                        else:
                            status = "running"
                    except (ValueError, TypeError):
                        status = "running"

                all_traces.append({
                    "trace_id": r["trace_id"],
                    "task_desc": task_desc,
                    "source": src,
                    "domain": domain,
                    "started_at": r["started_at"],
                    "ended_at": r["ended_at"],
                    "event_count": r["event_count"],
                    "tool_calls": r["tool_calls"],
                    "llm_calls": r["llm_calls"],
                    "status": status,
                })
        except sqlite3.Error:
            continue
        finally:
            conn.close()

    all_traces.sort(key=lambda t: t.get("started_at") or "", reverse=True)

    if q:
        ql = q.lower()
        all_traces = [t for t in all_traces if ql in (t.get("task_desc") or "").lower() or ql in t.get("source", "")]
    if source:
        all_traces = [t for t in all_traces if source in t.get("domain", "") or source in t.get("source", "")]

    total = len(all_traces)
    items = all_traces[offset:offset + limit]
    return {"items": items, "total": total}


@traces_router.get("/trace-detail/{trace_id}")
def api_trace_detail(trace_id: str):
    """Full trace detail — searches ALL events.db for the given trace_id."""
    result: dict[str, Any] = {"trace_id": trace_id, "events": [], "routing_events": [], "signal_spans": [], "intent_steps": []}

    # 1. all events.db
    for domain, db_path in discover_event_dbs():
        conn = safe_conn(db_path)
        if not conn:
            continue
        try:
            rows = conn.execute(
                "SELECT data FROM events WHERE trace_id=? ORDER BY timestamp",
                (trace_id,),
            ).fetchall()
            if rows:
                for (raw,) in rows:
                    try:
                        ev = json.loads(raw)
                        ev["_domain"] = domain
                        result["events"].append(ev)
                    except json.JSONDecodeError:
                        continue
                break
        except sqlite3.Error:
            pass
        finally:
            conn.close()

    # 2. routing_events + signal_spans in semantic_network.db
    conn = sem_db()
    if conn:
        try:
            routes = conn.execute(
                "SELECT * FROM routing_events WHERE trace_id=? ORDER BY id",
                (trace_id,),
            ).fetchall()
            result["routing_events"] = [row_to_dict(r) for r in routes]

            has_ss = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='signal_spans'"
            ).fetchone()
            if has_ss:
                spans = conn.execute(
                    "SELECT * FROM signal_spans WHERE trace_id=? ORDER BY span_index",
                    (trace_id,),
                ).fetchall()
                result["signal_spans"] = [row_to_dict(s) for s in spans]
        except sqlite3.Error:
            pass
        finally:
            conn.close()

    # 3. intent_traces.db
    paths = db_paths()
    it_conn = safe_conn(paths["intent_traces"])
    if it_conn:
        try:
            steps = it_conn.execute(
                """SELECT id, trace_id, step_num, tool_name, input_types, output_types,
                          action_class, desc, rationale, violations, timestamp,
                          route_node_id, tool_args_summary, tool_result, tool_exit_ok,
                          expected_output, info_transform
                   FROM intent_steps WHERE trace_id=? ORDER BY step_num""",
                (trace_id,),
            ).fetchall()
            result["intent_steps"] = [dict(s) for s in steps]
        except sqlite3.Error:
            pass
        finally:
            it_conn.close()

    return result
