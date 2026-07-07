# [OMNI] origin=ai-ide domain=dashboard/boss_sight/services ts=2026-06-27T00:00:00Z type=service
# [OMNI] summary="agent_notify —— AI 主动推一条带跳转链接的提醒到驾驶舱铃铛通知中心。我做完一个东西,推一条 {标题+open_ref},用户在铃铛看到、点了就跳到对应页面。"
# [OMNI] why="用户诉求(2026-06-27):AI 能发消息到提醒让我跳转去某个网页,这样我知道东西放哪儿了。复用现有铃铛通知面板(读 ctx_summary.unresolved,每项带 open_ref)。"
# [OMNI] tags=dashboard,boss_sight,notification,open_ref,jump
"""AI 通知 —— AI 推一条带跳转的提醒到铃铛。

跨进程:CLI(AI 进程)写、ccdaemon(8201)/dashboard(8210)读 —— 落 JSON 文件
(data/boss_sight/agent_notify.json),读写整文件 + 进程内锁。并入 cockpit_workflow 的
ctx_summary.unresolved,前端 NotificationPanel 已支持 open_ref 一键跳转。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.RLock()
_TTL = 24 * 3600.0   # 超过一天未点的提醒不再顶置


def _store_path() -> Path:
    override = os.environ.get("OMNI_AGENT_NOTIFY_PATH")
    if override:
        return Path(override)
    from omnicompany.core.config import omni_workspace_root

    return omni_workspace_root() / "data" / "boss_sight" / "agent_notify.json"


def _load() -> dict[str, Any]:
    try:
        raw = json.loads(_store_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw.setdefault("notices", [])
            return raw
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return {"notices": []}


def _save(data: dict[str, Any]) -> None:
    p = _store_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def push_notice(title: str, *, open_ref: dict | None = None, body: str = "",
                source: str = "agent", now: float | None = None) -> dict[str, Any]:
    """AI 推一条提醒到铃铛。open_ref={type,id,facet?} 内部跳转 或 {url} 外链。"""
    now = now if now is not None else time.time()
    rec = {
        "id": f"notice-{int(now * 1000)}",
        "title": (title or "").strip()[:200],
        "reason": (body or "").strip()[:280],
        "open_ref": open_ref or None,
        "source": source,
        "created_at": _iso(now),
        "ts": now,
        "status": "pending",
    }
    with _lock:
        data = _load()
        data.setdefault("notices", []).append(rec)
        data["notices"] = data["notices"][-200:]
        _save(data)
    return rec


def list_notices(*, include_resolved: bool = False, now: float | None = None) -> list[dict[str, Any]]:
    """活跃提醒(按时间倒序;默认过滤 resolved 与超 TTL)。供 ctx_summary 并入铃铛。"""
    now = now if now is not None else time.time()
    with _lock:
        data = _load()
    out = []
    for r in data.get("notices", []):
        if not include_resolved and r.get("status") == "resolved":
            continue
        if (now - float(r.get("ts", 0) or 0)) > _TTL:
            continue
        out.append(r)
    out.sort(key=lambda r: float(r.get("ts", 0) or 0), reverse=True)
    return out


def resolve(notice_id: str) -> bool:
    with _lock:
        data = _load()
        hit = False
        for r in data.get("notices", []):
            if r.get("id") == notice_id:
                r["status"] = "resolved"
                hit = True
        if hit:
            _save(data)
    return hit


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
