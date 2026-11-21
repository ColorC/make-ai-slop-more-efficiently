# [OMNI] origin=ai-ide domain=services/_core/identity ts=2026-05-02T00:00:00Z type=service status=active agent=ai-ide-current
# [OMNI] summary="session 写入文件追溯, 从 SQLite event bus 派生'当前 session 写过哪些文件'"
# [OMNI] why="cc_wrapper trace.py 已经把 Edit/Write 工具调用记到 event bus, 这层把它派生成可查的 session-writes 视图 (供 omni who 跟注册中心用)"
# [OMNI] tags=identity,session,writes,event-bus
# [OMNI] material_id="material:core.identity.session_write_tracker.implementation.py"
"""session 写入文件追溯.

cc_wrapper 的 trace.py hook 已经把每次 Edit / Write 工具调用记到
`data/ide_events.db` SQLite event bus, 含 args.file_path. 本模块把"当前 session
写过哪些文件" 的查询抽出来, 供:
- `omni who` 显示当前 session 写过的文件清单
- 注册中心校验 (后续 G2 实装) — 注册时反查 trace_id 是不是真写了这文件
- dashboard 视图 (后续 web 端复用)
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.identity.resolver import (
    _repo_root,
    resolve_active_trace_id,
)


_DB_REL = "data/ide_events.db"
_WRITE_TOOLS = frozenset({"Edit", "Write", "NotebookEdit", "MultiEdit", "str_replace_editor"})


def _db_path() -> Path:
    return _repo_root() / _DB_REL


def session_writes(
    trace_id: str | None = None,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """返回当前 session (或指定 trace_id) 写过的文件清单.

    从 SQLite event bus 抓 `agent.tool.call` event, 过滤 tool ∈ Edit/Write/etc.,
    抽 args.file_path. 返回 [{file_path, tool, timestamp, event_id}] 列表,
    按时间倒序, 最多 `limit` 条.

    `trace_id` 不传时用 `resolve_active_trace_id()` 自动派生.
    DB 不存在时返回空列表 (不抛错, 因为 cc_wrapper hook 可能从未运行过).
    """
    tid = trace_id or resolve_active_trace_id()
    db = _db_path()
    if not db.is_file():
        return []

    try:
        conn = sqlite3.connect(str(db), timeout=2.0)
    except sqlite3.Error:
        return []

    rows: list[dict[str, Any]] = []
    try:
        cur = conn.execute(
            "SELECT id, event_type, timestamp, data FROM events "
            "WHERE trace_id = ? AND event_type = 'agent.tool.call' "
            "ORDER BY timestamp DESC LIMIT ?",
            (tid, limit * 3),  # 多取一些, 后面 tool 过滤会筛掉非写入
        )
        for eid, _etype, ts, data_json in cur:
            try:
                body = json.loads(data_json)
            except (json.JSONDecodeError, TypeError):
                continue
            payload = body.get("payload") or {}
            tool = payload.get("tool")
            if tool not in _WRITE_TOOLS:
                continue
            args = payload.get("args") or {}
            fp = args.get("file_path") or args.get("path") or args.get("notebook_path")
            if not fp:
                continue
            rows.append({
                "file_path": fp,
                "tool": tool,
                "timestamp": ts,
                "event_id": eid,
            })
            if len(rows) >= limit:
                break
    finally:
        conn.close()

    return rows


def session_write_files(trace_id: str | None = None, *, limit: int = 200) -> list[str]:
    """返回当前 session 写过的去重文件路径清单 (按最近写入时间倒序)."""
    seen: set[str] = set()
    out: list[str] = []
    for w in session_writes(trace_id, limit=limit):
        fp = w["file_path"]
        if fp in seen:
            continue
        seen.add(fp)
        out.append(fp)
    return out
