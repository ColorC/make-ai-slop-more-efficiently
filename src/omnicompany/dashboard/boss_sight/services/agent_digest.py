# [OMNI] origin=claude-code domain=dashboard/boss_sight/services ts=2026-06-13T18:30:00Z type=service
# [OMNI] material_id="material:dashboard.boss_sight.services.agent_digest.py"
"""多 agent 对话摘要管线 — 性价比模型自动维护"每个对话在鼓捣啥"。

背景(用户 2026-06-13): 多 agent 视图里每行只有 transcript 的原始首条 prompt + 最后一段
助手输出, 经常是 "No files found" / 一串 UUID / "E2-OK" / 一大段英文工具思考 —— 看不出这个
对话到底在做什么项目、什么内容、最近做了什么。

本管线给每个对话维护三件最关键的事(中文为主):
  - project / plan : 项目和计划名
  - title          : 这个对话整体在做什么内容(一句话标题)
  - last_step      : 最近一步做了什么 / 正在做什么

设计要点(都贴用户原话):
  - **性价比模型负责**: 走 runtime.llm.call_json 默认结构化模型(qwen3.6-plus), 不用昂贵主模型。
  - **只动态更新运行中的 agent**: status=working 且 transcript 有新写入才重算; done/idle 不动
    (它们不变, 末次活跃时算过的摘要一直留着)。没摘要的也补一次, 让每行都看得懂。
  - **合适频率 + token 有界**: 每个会话两次摘要至少隔 _MIN_INTERVAL_SEC; 每轮最多 _MAX_PER_TICK
    条; 全局两轮之间至少隔 _MIN_TICK_GAP_SEC; 单次只喂 cwd+任务+最近输出(不整文件读)。
  - **懒触发**: 由 /active 端点 fire-and-forget 调一次 tick —— 没人看就不烧 token, 看的时候
    保持新鲜。不是常驻后台循环。

摘要不依赖额外 I/O: /active 扫描已经抽好了 preview(首条用户消息=任务)与 last_did(末段助手
输出), 这里只把它们喂给便宜模型提炼。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root

_log = logging.getLogger(__name__)

# 频率 / 预算闸(都偏保守, 后续有需要再调)
_MIN_INTERVAL_SEC = 90      # 同一会话两次摘要最小间隔
_MAX_PER_TICK = 8           # 单轮最多摘要几条(burst 上限)
_MIN_TICK_GAP_SEC = 12      # 全局两轮 tick 最小间隔(挡住前端高频轮询)
_WORKERS = 4

DIGEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "project": {"type": "string"},    # 项目名(中文为主), 多从 cwd / 内容推
        "plan": {"type": "string"},       # 计划名(中文); 没有就填 "无"
        "title": {"type": "string"},      # 这个对话在做什么内容, 一句话中文标题
        "last_step": {"type": "string"},  # 最近一步做了什么 / 正在做什么, 一句话中文
    },
    "required": ["title", "last_step"],
}

_SYSTEM = (
    "你是 AI 编码 agent 会话的摘要助手。给你一个 agent 对话的工作目录、初始任务、最近一段输出, "
    "请用**中文**提炼这个对话最关键的几件事, 让人一眼看懂它在鼓捣什么。要求:\n"
    "- project: 项目名(中文为主, 可从工作目录末段或内容推断, 如『行者无乡』『anniv-fest 周年庆』『omnicompany 驾驶舱』)。\n"
    "- plan: 计划/任务批次名(中文); 看不出就填『无』。\n"
    "- title: 这个对话整体在做什么内容, 一句话中文标题(≤20字), 具体而非笼统(别写『协助开发』这种废话)。\n"
    "- last_step: 最近一步做了什么 / 正在做什么, 一句话中文(≤30字), 据『最近输出』写实。\n"
    "只输出 JSON。不要编造工作目录/输出里没有的事实; 信息不足就如实写『信息不足』。"
)


def _store_path() -> Path:
    return omni_workspace_root() / "data" / "boss_sight" / "agent_digests.json"


def load_digests() -> dict[str, dict[str, Any]]:
    p = _store_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_digests(store: dict[str, dict[str, Any]]) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    # ccdaemon owns the live PTY projection. Push newly generated summary
    # titles into memory immediately so the 2s tab-state stream can replace the
    # first-message fallback without waiting for another full session-list read.
    try:
        from omnicompany.dashboard.ccdaemon.pty import get_manager

        get_manager().apply_digest_titles(store)
    except Exception:  # noqa: BLE001
        _log.debug("live PTY digest-title projection skipped", exc_info=True)


def _key(item: dict[str, Any]) -> str:
    return f"{item.get('provider')}:{item.get('session_id')}"


def get_digest(provider: str, session_id: str) -> dict[str, Any] | None:
    d = load_digests().get(f"{provider}:{session_id}")
    if not d:
        return None
    return {k: d.get(k, "") for k in ("project", "plan", "title", "last_step")}


def summarize_one(item: dict[str, Any]) -> dict[str, str]:
    """便宜模型把一个会话提炼成 {project, plan, title, last_step}(全中文)。"""
    from omnicompany.runtime.llm import call_json

    cwd = str(item.get("cwd") or "")
    task = str(item.get("preview") or "")
    latest = str(item.get("last_did") or "")
    user = (
        f"工作目录: {cwd or '(未知)'}\n"
        f"初始任务:\n{task[:1200] or '(无)'}\n\n"
        f"最近输出:\n{latest[:1500] or '(无)'}"
    )
    out = call_json(
        system=_SYSTEM, user=user, schema=DIGEST_SCHEMA,
        caller="agent_digest.summarize_one", max_tokens=400,
    )
    return {
        "project": str(out.get("project") or "").strip()[:60],
        "plan": str(out.get("plan") or "").strip()[:80],
        "title": str(out.get("title") or "").strip()[:80],
        "last_step": str(out.get("last_step") or "").strip()[:160],
    }


def _worker(item: dict[str, Any]) -> dict[str, Any]:
    d = summarize_one(item)
    d["_key"] = _key(item)
    d["_source_mtime"] = float(item.get("mtime") or 0)
    return d


def _needs_digest(item: dict[str, Any], cur: dict[str, Any] | None, now: float) -> bool:
    if not item.get("session_id"):
        return False
    if cur is None:
        return True  # 没摘要的补一次(不分运行态), 让每行都看得懂
    # 有摘要: 只在"运行中 + transcript 有新写入 + 距上次够久"时刷新
    if item.get("status") != "working":
        return False
    mtime = float(item.get("mtime") or 0)
    changed = mtime > float(cur.get("_source_mtime") or 0) + 1
    stale = (now - float(cur.get("_updated_ts") or 0)) >= _MIN_INTERVAL_SEC
    return changed and stale


_tick_lock = threading.Lock()
_tick_running = False
_last_tick_ts = 0.0


def run_tick(items: list[dict[str, Any]], *, now: float | None = None,
             max_per_tick: int = _MAX_PER_TICK, workers: int = _WORKERS) -> dict[str, Any]:
    """对一批会话(/active 的 items)挑出需要摘要的, 批量跑便宜模型, 落库。"""
    from omnicompany.runtime.llm.batch import run_parallel_items

    now = now if now is not None else time.time()
    store = load_digests()
    targets = [it for it in items if _needs_digest(it, store.get(_key(it)), now)]
    # 运行中的优先, 再按最近活跃排序
    targets.sort(key=lambda it: (0 if it.get("status") == "working" else 1, -float(it.get("mtime") or 0)))
    targets = targets[:max_per_tick]
    if not targets:
        return {"updated": 0, "failed": 0, "targets": 0, "scanned": len(items)}

    res = run_parallel_items(targets, _worker, workers=workers, progress_label="agent-digest")
    iso = datetime.fromtimestamp(now, timezone.utc).isoformat()
    for d in res.results:
        k = d.pop("_key")
        src = d.pop("_source_mtime", 0.0)
        store[k] = {**d, "_source_mtime": src, "_updated_ts": now, "updated_at": iso}
    _save_digests(store)
    return {"updated": len(res.results), "failed": len(res.failures),
            "targets": len(targets), "scanned": len(items)}


def schedule_tick(items: list[dict[str, Any]]) -> None:
    """懒触发: /active 调一次, 单飞 + 全局节流, 在线程里跑(不堵事件循环, 失败静默)。"""
    global _tick_running, _last_tick_ts
    now = time.time()
    with _tick_lock:
        if _tick_running or (now - _last_tick_ts) < _MIN_TICK_GAP_SEC:
            return
        _tick_running = True
        _last_tick_ts = now
    # 只把需要的字段快照传进去(items 之后会被端点改写)
    snap = [
        {k: it.get(k) for k in ("provider", "session_id", "cwd", "preview", "last_did", "mtime", "status")}
        for it in items
    ]

    def _run() -> None:
        global _tick_running
        try:
            run_tick(snap)
        except Exception:  # noqa: BLE001
            _log.exception("agent_digest tick failed")
        finally:
            with _tick_lock:
                _tick_running = False

    threading.Thread(target=_run, name="agent-digest-tick", daemon=True).start()


__all__ = ["get_digest", "load_digests", "run_tick", "schedule_tick", "summarize_one", "DIGEST_SCHEMA"]
