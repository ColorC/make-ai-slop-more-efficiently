# [OMNI] origin=claude-code domain=services/agent ts=2026-05-04 type=helper
# [OMNI] material_id="material:core.agent.session_history.jsonl_export.py"
"""SessionHistory — 从 SQLiteBus events 还原 + 导出 messages 历史为 JSONL.

CC 对齐: ~/.claude/history.jsonl 是 CC 的全量消息历史归档. 我们的等价物是 SQLite
events 表 (data/ide_events.db), 已通过 LLMCallRouter / PromptBuilderRouter 等
落 input/output 事件. 这模块提供:

  1. dump_messages_jsonl(trace_id, output_path): 从 events 抽 messages, 写 JSONL
  2. list_traces(prefix=...): 列已知 trace_id (用于 dashboard / cli)
  3. load_session_summary(trace_id): 单 trace 的 token / cost / turns 摘要

用法:
  from omnicompany.packages.services._core.agent.session_history import dump_messages_jsonl
  dump_messages_jsonl("migration_judge_v4_xxx", "/tmp/replay.jsonl")
  # 然后可以喂给 replay tool / human review / training data 抽取
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def _default_db_path() -> Path:
    """默认 events.db 位置 — 跟 SQLiteBus 默认一致."""
    return Path("data") / "ide_events.db"


def list_traces(*, prefix: str = "", limit: int = 100, db_path: Path | None = None) -> list[dict]:
    """列已知 trace_id, 返 [{trace_id, event_count, first_ts, last_ts}, ...].

    按 last_ts desc 排. 用于 dashboard 显已有 sessions.
    """
    db = db_path or _default_db_path()
    if not db.is_file():
        return []
    conn = sqlite3.connect(str(db))
    try:
        if prefix:
            rows = conn.execute(
                "SELECT trace_id, COUNT(*), MIN(timestamp), MAX(timestamp) "
                "FROM events WHERE trace_id LIKE ? "
                "GROUP BY trace_id ORDER BY MAX(timestamp) DESC LIMIT ?",
                (f"{prefix}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT trace_id, COUNT(*), MIN(timestamp), MAX(timestamp) "
                "FROM events GROUP BY trace_id ORDER BY MAX(timestamp) DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"trace_id": r[0], "event_count": r[1], "first_ts": r[2], "last_ts": r[3]}
            for r in rows
        ]
    finally:
        conn.close()


def load_session_summary(trace_id: str, *, db_path: Path | None = None) -> dict:
    """单个 trace 的总量统计 — turns / events / cost (粗算自 events) / 起止时间."""
    db = db_path or _default_db_path()
    if not db.is_file():
        return {"trace_id": trace_id, "found": False}
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT event_type, COUNT(*) FROM events WHERE trace_id=? GROUP BY event_type",
            (trace_id,),
        ).fetchall()
        if not rows:
            return {"trace_id": trace_id, "found": False}
        types = {r[0]: r[1] for r in rows}
        ts_rows = conn.execute(
            "SELECT MIN(timestamp), MAX(timestamp) FROM events WHERE trace_id=?",
            (trace_id,),
        ).fetchone()
        return {
            "trace_id": trace_id,
            "found": True,
            "event_count": sum(types.values()),
            "event_type_counts": types,
            "turns": types.get("agent.turn.start", 0),
            "tool_calls": types.get("router.tool_dispatch.input", 0),
            "llm_calls": types.get("router.llm_call.input", 0),
            "first_ts": ts_rows[0],
            "last_ts": ts_rows[1],
        }
    finally:
        conn.close()


def dump_messages_jsonl(
    trace_id: str,
    output_path: str | Path,
    *,
    db_path: Path | None = None,
) -> dict:
    """从 events 抽 trace_id 全量, 一行一 event 写 JSONL.

    便于事后 human review / replay / training data 抽取.

    返 {trace_id, events_written, output_path}. events_written=0 表示 trace 不存在.
    """
    db = db_path or _default_db_path()
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not db.is_file():
        return {"trace_id": trace_id, "events_written": 0, "output_path": str(out), "error": "db not found"}

    conn = sqlite3.connect(str(db))
    n = 0
    try:
        cur = conn.execute(
            "SELECT id, trace_id, parent_id, event_type, source, tags, timestamp, data "
            "FROM events WHERE trace_id=? ORDER BY timestamp",
            (trace_id,),
        )
        with out.open("w", encoding="utf-8") as f:
            for row in cur:
                ev = {
                    "id": row[0],
                    "trace_id": row[1],
                    "parent_id": row[2],
                    "event_type": row[3],
                    "source": row[4],
                    "tags": row[5],
                    "timestamp": row[6],
                    "data": _maybe_parse_json(row[7]),
                }
                f.write(json.dumps(ev, ensure_ascii=False, default=str))
                f.write("\n")
                n += 1
    finally:
        conn.close()
    return {"trace_id": trace_id, "events_written": n, "output_path": str(out)}


def _maybe_parse_json(raw: Any) -> Any:
    """events.data 是 JSON 字符串, 解析成 dict; 失败时原样返."""
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


__all__ = [
    "list_traces",
    "load_session_summary",
    "dump_messages_jsonl",
]
