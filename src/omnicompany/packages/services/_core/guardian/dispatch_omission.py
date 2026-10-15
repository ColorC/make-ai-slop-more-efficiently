# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-08-10 type=infra status=active
# [OMNI] material_id="material:core.guardian.dispatch_omission.session_scan.py"
"""该派没派巡逻 (SWARM-DISPATCH-PROACTIVITY P5) — 仅记录, 不阻断。

扫 ide_events.db 的 agent.tool.call 事件流: 一个会话(trace)在窗口内做了大量机械
工具调用(Edit/Write/Bash/PowerShell/NotebookEdit)、且从头到尾没有任何派发动作
(omni 派发命令 / 内建 Agent/Workflow 派生), 标记为"该派没派"候选。

判据是提示性的: 长机械串可能就该自己干(比如本来就是执行方 worker)。所以本巡逻
只出报告供周会/复盘看趋势, 不出罚单不拦截。
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

MECHANICAL_TOOLS = {"Edit", "Write", "Bash", "PowerShell", "NotebookEdit", "MultiEdit"}
DELEGATION_TOOLS = {"Agent", "Workflow", "Task"}
DISPATCH_RE = re.compile(
    r"omni\s+(task\s+dispatch|agent\s+send|agent\s+start|agents\s+send|"
    r"worker\s+run|worker\s+spawn|workflow\s+run)",
    re.IGNORECASE,
)


@dataclass
class TraceDispatchStats:
    trace_id: str
    total_tool_calls: int
    mechanical_calls: int
    max_consecutive_mechanical: int
    dispatch_markers: int
    first_ts: str
    last_ts: str
    flagged: bool


def _events_db_path() -> Path:
    from omnicompany.core.config import resolve_unified_db_path

    return resolve_unified_db_path("ide_events.db")


def scan_dispatch_omission(
    *,
    hours: float = 24.0,
    min_mechanical: int = 40,
    min_consecutive: int = 20,
    db_path: str | Path | None = None,
) -> list[TraceDispatchStats]:
    """窗口内逐 trace 统计; flagged = 机械量与连串都超阈值且零派发动作。"""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = sqlite3.connect(str(db_path or _events_db_path()), timeout=5.0)
    try:
        rows = conn.execute(
            "SELECT trace_id, timestamp, data FROM events "
            "WHERE event_type = 'agent.tool.call' AND timestamp >= ? "
            "ORDER BY trace_id, timestamp",
            (since,),
        ).fetchall()
    finally:
        conn.close()

    by_trace: dict[str, list[tuple[str, str]]] = {}
    for trace_id, ts, data in rows:
        by_trace.setdefault(trace_id or "unknown", []).append((ts, data))

    out: list[TraceDispatchStats] = []
    for trace_id, events in by_trace.items():
        total = len(events)
        mech = 0
        max_streak = 0
        streak = 0
        markers = 0
        for _ts, raw in events:
            try:
                payload = (json.loads(raw) or {}).get("payload") or {}
            except ValueError:
                payload = {}
            tool = str(payload.get("tool") or "")
            args_text = json.dumps(payload.get("args") or {}, ensure_ascii=False)
            is_dispatch = tool in DELEGATION_TOOLS or bool(DISPATCH_RE.search(args_text))
            if is_dispatch:
                markers += 1
            if tool in MECHANICAL_TOOLS and not is_dispatch:
                mech += 1
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0
        out.append(TraceDispatchStats(
            trace_id=trace_id,
            total_tool_calls=total,
            mechanical_calls=mech,
            max_consecutive_mechanical=max_streak,
            dispatch_markers=markers,
            first_ts=events[0][0],
            last_ts=events[-1][0],
            flagged=(mech >= min_mechanical and max_streak >= min_consecutive and markers == 0),
        ))
    out.sort(key=lambda s: (not s.flagged, -s.mechanical_calls))
    return out


def write_report(stats: list[TraceDispatchStats], *, out_dir: str | Path | None = None) -> Path:
    from omnicompany.core.config import omni_workspace_root

    base = Path(out_dir) if out_dir else omni_workspace_root() / "data" / "services" / "guardian" / "dispatch_omission"
    base.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    p = base / f"dispatch-omission-{ts}.json"
    p.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "flagged": [asdict(s) for s in stats if s.flagged],
        "all": [asdict(s) for s in stats],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


__all__ = ["TraceDispatchStats", "scan_dispatch_omission", "write_report"]
