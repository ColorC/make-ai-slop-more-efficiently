# [OMNI] origin=claude-code domain=services/_core/lifecycle ts=2026-06-25T00:00:00Z type=infra status=active
# [OMNI] summary="plan 时间线聚合器: 从 data/events.db 把 external_agent + tool.call 事件按 trace_id 配对成会话, 含 duration/tool_count/tokens"
# [OMNI] why="PLAN-TIMELINE P-1: work_report 概览缺过程指标(每会话耗时/工具数/token), 先建底层聚合器供 CLI 与报告复用"
# [OMNI] tags=lifecycle,timeline,events,aggregator
# [OMNI] material_id="material:services._core.lifecycle.plan_timeline.py"
"""plan 时间线聚合器。

数据源: ``data/events.db`` (unified SQLiteBus). 表 ``events`` 列:
``id / trace_id / parent_id / event_type / source / tags / timestamp / data``,
其中 ``data`` 是 ``FactoryEvent.model_dump_json()`` 全文 (含 payload/metadata/tags 等)。

做法:
- 查 ``event_type in (external_agent.started, external_agent.completed, agent.tool.call)``
  最近 ``lookback_min`` 分钟内的全部事件;
- ``external_agent.started.payload.metadata.plan_id == plan_id`` 筛出本 plan 的
  trace_id 集合 (dispatch_task 起 sdk 会话时 metadata 带 plan_id);
- 同一 trace_id 的 started/completed 配对算 ``duration_s``;
- 同一 trace_id 的 ``agent.tool.call`` 计数并取 top 工具名 (工具名优先 payload.tool,
  其次 payload.name);
- ``completed.payload.raw.usage`` 或 ``payload.usage`` 若含 input_tokens/output_tokens
  则累计 tokens。

返回结构:
    {plan_id, sessions:[{trace_id, session_id, start, end, duration_s,
                          tool_count, top_tools:{name:count},
                          tokens:{input,output,total}}, ...],
     totals:{duration_s, tool_count, tokens:{input,output,total}}}
``session_id`` 是 ``trace_id`` 的别名, 便于上层按"会话"叙述。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any


_EVENT_TYPES = ("external_agent.started", "external_agent.completed", "agent.tool.call")


def _parse_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _extract_plan_id(payload: dict[str, Any]) -> str | None:
    meta = payload.get("metadata")
    if isinstance(meta, dict):
        plan_id = meta.get("plan_id")
        if isinstance(plan_id, str) and plan_id:
            return plan_id
    plan_id = payload.get("plan_id")
    if isinstance(plan_id, str) and plan_id:
        return plan_id
    return None


def _extract_tool_name(payload: dict[str, Any]) -> str | None:
    for key in ("tool", "name", "tool_name"):
        v = payload.get(key)
        if isinstance(v, str) and v:
            return v
    return None


def _extract_usage(payload: dict[str, Any]) -> tuple[int, int]:
    """从 completed 事件 payload 里挖 (input_tokens, output_tokens)。

    兼容多种位置: payload.usage / payload.raw.usage / payload.raw.totals.usage。
    """
    candidates: list[Any] = []
    if isinstance(payload.get("usage"), dict):
        candidates.append(payload["usage"])
    raw = payload.get("raw")
    if isinstance(raw, dict):
        if isinstance(raw.get("usage"), dict):
            candidates.append(raw["usage"])
        totals = raw.get("totals")
        if isinstance(totals, dict) and isinstance(totals.get("usage"), dict):
            candidates.append(totals["usage"])
    for usage in candidates:
        try:
            in_t = int(usage.get("input_tokens") or 0)
            out_t = int(usage.get("output_tokens") or 0)
        except (TypeError, ValueError):
            continue
        if in_t or out_t:
            return in_t, out_t
    return 0, 0


def _load_recent_events(db_path: Any, cutoff_iso: str) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in _EVENT_TYPES)
    sql = (
        f"SELECT trace_id, event_type, timestamp, data FROM events "
        f"WHERE event_type IN ({placeholders}) AND timestamp >= ? "
        f"ORDER BY timestamp ASC"
    )
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        return list(conn.execute(sql, (*_EVENT_TYPES, cutoff_iso)))
    finally:
        conn.close()


def build_plan_timeline(plan_id: str, *, lookback_min: int = 180) -> dict[str, Any]:
    """聚合 plan_id 在最近 lookback_min 分钟内的会话时间线。

    Args:
        plan_id: 形如 ``_scratch/[2026-06-25]PLAN-TIMELINE`` 的计划 id。
        lookback_min: 回看窗口 (分钟), 默认 180。

    Returns:
        ``{plan_id, sessions, totals}`` (见模块 docstring)。events.db 不存在 /
        本窗口无相关事件时, sessions 为空、totals 全 0。
    """
    from omnicompany.core.config import resolve_unified_db_path

    db_path = resolve_unified_db_path("events.db")
    empty_totals = {"duration_s": 0.0, "tool_count": 0,
                    "tokens": {"input": 0, "output": 0, "total": 0}}
    if not db_path.is_file():
        return {"plan_id": plan_id, "sessions": [], "totals": empty_totals}

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max(1, int(lookback_min)))
    try:
        rows = _load_recent_events(db_path, cutoff.isoformat())
    except sqlite3.Error:
        return {"plan_id": plan_id, "sessions": [], "totals": empty_totals}

    parsed: list[tuple[str, str, datetime, dict[str, Any]]] = []
    plan_traces: set[str] = set()
    for row in rows:
        try:
            envelope = json.loads(row["data"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(envelope, dict):
            continue
        payload = envelope.get("payload") if isinstance(envelope.get("payload"), dict) else {}
        ts = _parse_ts(row["timestamp"]) or _parse_ts(envelope.get("timestamp"))
        if ts is None:
            continue
        trace_id = row["trace_id"] or envelope.get("trace_id") or ""
        if not trace_id:
            continue
        event_type = row["event_type"]
        parsed.append((trace_id, event_type, ts, payload))
        if event_type == "external_agent.started":
            if _extract_plan_id(payload) == plan_id:
                plan_traces.add(trace_id)

    if not plan_traces:
        return {"plan_id": plan_id, "sessions": [], "totals": empty_totals}

    sessions: dict[str, dict[str, Any]] = {}
    for trace_id, event_type, ts, payload in parsed:
        if trace_id not in plan_traces:
            continue
        sess = sessions.setdefault(trace_id, {
            "trace_id": trace_id,
            "session_id": trace_id,
            "start": None,
            "end": None,
            "duration_s": 0.0,
            "tool_count": 0,
            "top_tools": {},
            "tokens": {"input": 0, "output": 0, "total": 0},
        })
        if event_type == "external_agent.started":
            if sess["start"] is None or ts < _parse_ts(sess["start"]):
                sess["start"] = ts.isoformat()
        elif event_type == "external_agent.completed":
            if sess["end"] is None or ts > _parse_ts(sess["end"]):
                sess["end"] = ts.isoformat()
            in_t, out_t = _extract_usage(payload)
            sess["tokens"]["input"] += in_t
            sess["tokens"]["output"] += out_t
        elif event_type == "agent.tool.call":
            sess["tool_count"] += 1
            name = _extract_tool_name(payload) or "?"
            sess["top_tools"][name] = sess["top_tools"].get(name, 0) + 1

    total_duration = 0.0
    total_tool_count = 0
    total_in = 0
    total_out = 0
    for sess in sessions.values():
        start_dt = _parse_ts(sess["start"])
        end_dt = _parse_ts(sess["end"])
        if start_dt and end_dt and end_dt >= start_dt:
            sess["duration_s"] = round((end_dt - start_dt).total_seconds(), 3)
        sess["tokens"]["total"] = sess["tokens"]["input"] + sess["tokens"]["output"]
        sess["top_tools"] = dict(
            sorted(sess["top_tools"].items(), key=lambda kv: kv[1], reverse=True)
        )
        total_duration += sess["duration_s"]
        total_tool_count += sess["tool_count"]
        total_in += sess["tokens"]["input"]
        total_out += sess["tokens"]["output"]

    sessions_list = sorted(
        sessions.values(),
        key=lambda s: s["start"] or "",
    )

    return {
        "plan_id": plan_id,
        "sessions": sessions_list,
        "totals": {
            "duration_s": round(total_duration, 3),
            "tool_count": total_tool_count,
            "tokens": {
                "input": total_in,
                "output": total_out,
                "total": total_in + total_out,
            },
        },
    }


__all__ = ["build_plan_timeline"]
