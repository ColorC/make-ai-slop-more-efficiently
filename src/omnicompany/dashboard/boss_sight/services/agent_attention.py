# [OMNI] origin=claude-code domain=dashboard/boss_sight/services ts=2026-06-25T12:00:00Z type=service
# [OMNI] material_id="material:dashboard.boss_sight.services.agent_attention.py"
"""agent_attention —— agent 自我身份 + 「请审阅本对话」请求 + 发回意见队列。

权威: omnicompany/docs/plans/dashboard/[2026-06-24]MULTIAGENT-AND-REVIEW-REDESIGN/plan.md

用户诉求(2026-06-25):
> 作为 agent 你应当可以感知自己的身份信息并输入自己的身份信息, 主动让用户专注于本对话审阅。

一个 agent(claude_code / codex 会话)在干完一段值得过目的活后, 可以:
  - `omni agent whoami`         感知自己被派生出的身份(project-role-name + session)。
  - `omni agent request-review` 主动举手, 让用户在「审阅总览 / 多Agent」顶部看到本对话的工作报告。
用户在详情里点「发回意见」→ 走本模块的 feedback 队列 → agent 侧 hook 取走继续。

⚠ 跨进程: CLI(agent 进程)写、ccdaemon(8201)读, 不能用内存态 —— 落 JSON 文件
(data/boss_sight/agent_attention.json), 读写各自整文件 + 进程内锁, 实例少、并发低, 够用。
active_context(UI 驱动的"当前选中")仍是内存态, 活在 ccdaemon 里; 本模块只管"agent 举手"。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.RLock()
# 活跃窗口: 超过此时长未被 resolve 的请求, 视为过期不再顶置(防陈旧报告长期霸屏)。
_ACTIVE_TTL = 6 * 3600.0


def _store_path() -> Path:
    override = os.environ.get("OMNI_AGENT_ATTENTION_PATH")
    if override:
        return Path(override)
    from omnicompany.core.config import omni_workspace_root

    return omni_workspace_root() / "data" / "boss_sight" / "agent_attention.json"


def _load() -> dict[str, Any]:
    p = _store_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw.setdefault("requests", [])
            raw.setdefault("feedback", {})
            return raw
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return {"requests": [], "feedback": {}}


def _save(data: dict[str, Any]) -> None:
    p = _store_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def request_attention(
    session_id: str,
    *,
    headline: str,
    identity: str = "",
    project: str = "",
    role: str = "",
    name: str = "",
    kind: str = "conversation",
    provider: str = "claude_code",
    now: float | None = None,
) -> dict[str, Any]:
    """agent 举手: 让用户专注审阅本对话。同一 session 只保留最新一条 pending。"""
    now = now if now is not None else time.time()
    rec: dict[str, Any] = {
        "session_id": session_id or "",
        "provider": provider,
        "identity": identity,
        "project": project,
        "role": role,
        "name": name,
        "headline": (headline or "").strip()[:280],
        "kind": kind,
        "ts": now,
        "status": "pending",
    }
    with _lock:
        data = _load()
        reqs = [r for r in data.get("requests", []) if r.get("session_id") != rec["session_id"]]
        reqs.append(rec)
        data["requests"] = reqs[-200:]
        _save(data)
    return rec


def list_attention(now: float | None = None, *, include_resolved: bool = False) -> list[dict[str, Any]]:
    """活跃举手, 按时间倒序。默认过滤掉 resolved 与超 TTL 的。"""
    now = now if now is not None else time.time()
    with _lock:
        data = _load()
    out: list[dict[str, Any]] = []
    for r in data.get("requests", []):
        if not include_resolved:
            if r.get("status") == "resolved":
                continue
            if (now - float(r.get("ts", 0) or 0)) > _ACTIVE_TTL:
                continue
        out.append(r)
    out.sort(key=lambda r: float(r.get("ts", 0) or 0), reverse=True)
    return out


def attention_by_session(now: float | None = None) -> dict[str, dict[str, Any]]:
    """{session_id → 最新活跃请求}, 供 residents 回填高亮。"""
    by: dict[str, dict[str, Any]] = {}
    for r in list_attention(now=now):
        sid = r.get("session_id")
        if sid and sid not in by:
            by[sid] = r
    return by


def resolve_attention(session_id: str) -> bool:
    """标记某对话的举手为已处理(用户审完/发回意见后)。"""
    with _lock:
        data = _load()
        changed = False
        for r in data.get("requests", []):
            if r.get("session_id") == session_id and r.get("status") != "resolved":
                r["status"] = "resolved"
                changed = True
        if changed:
            _save(data)
    return changed


def queue_feedback(session_id: str, message: str, *, author: str = "user", now: float | None = None) -> dict[str, Any]:
    """用户「发回意见」→ 入该对话的反馈队列, agent 侧 hook 取走。"""
    now = now if now is not None else time.time()
    item = {"message": (message or "").strip(), "author": author, "ts": now, "consumed": False}
    with _lock:
        data = _load()
        fb = data.setdefault("feedback", {})
        fb.setdefault(session_id, []).append(item)
        fb[session_id] = fb[session_id][-100:]
        _save(data)
    return item


def pop_feedback(session_id: str, *, mark: bool = True) -> list[dict[str, Any]]:
    """取走某对话未消费的反馈(agent hook 用)。mark=True 同时标记已消费。"""
    with _lock:
        data = _load()
        fb = data.setdefault("feedback", {})
        items = [dict(i) for i in fb.get(session_id, []) if not i.get("consumed")]
        if mark and items:
            for i in fb.get(session_id, []):
                i["consumed"] = True
            _save(data)
    return items


def _reset_for_test() -> None:
    _save({"requests": [], "feedback": {}})
