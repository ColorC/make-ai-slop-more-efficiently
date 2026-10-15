# [OMNI] origin=claude-code domain=dashboard/boss_sight/services ts=2026-06-13T18:30:00Z type=service
# [OMNI] material_id="material:dashboard.boss_sight.services.agent_digest.py"
"""多 agent 对话摘要管线 — 性价比模型自动维护"每个对话在鼓捣啥"。

背景(用户 2026-06-13): 多 agent 视图里每行只有 transcript 的原始首条 prompt + 最后一段
助手输出, 经常是 "No files found" / 一串 UUID / "E2-OK" / 一大段英文工具思考 —— 看不出这个
对话到底在做什么项目、什么内容、最近做了什么。

本管线给每个对话维护三件最关键的事(中文为主):
  - project / workstream / plan : 项目、项目内业务线和计划名
  - title          : 这个对话整体在做什么内容(一句话标题)
  - last_step      : 最近一步做了什么 / 正在做什么

设计要点(都贴用户原话):
  - **性价比模型负责**: 走 runtime.llm.call_json 默认结构化模型(qwen3.6-plus), 不用昂贵主模型。
  - **只动态更新运行中的 agent**: status=working 且 transcript 有新写入才重算; done/idle 不动
    (它们不变, 末次活跃时算过的摘要一直留着)。没摘要的也补一次, 让每行都看得懂。
  - **合适频率 + token 有界**: 每个会话两次摘要至少隔 _MIN_INTERVAL_SEC; 每轮最多 _MAX_PER_TICK
    条; 全局两轮之间至少隔 _MIN_TICK_GAP_SEC; 单次只喂任务+最近输出(不整文件读)。
  - **运行时归属写入**: 会话生命周期由 session_companion 统一消费并写入身份台账；本模块的
    ``run_tick`` 只保留旧 CLI/测试兼容入口，``schedule_tick`` 明确不再调度它，避免摘要
    管线和 companion 形成两套项目绑定权威。
  - **扫描侧补盲**: companion 只覆盖经 Dashboard/hook 跑起来的会话，/active 扫 transcript 发现
    的外部对话不会产生 companion 事件。``schedule_scan_backfill`` 专门补这块盲区，只写摘要、
    绝不写身份绑定，所以绑定权威仍然只有 companion 一个。

摘要不依赖额外 I/O: /active 扫描已经抽好了 preview(首条用户消息=任务)与 last_did(末段助手
输出), 这里只把它们喂给便宜模型提炼。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from omnicompany.core.config import omni_workspace_root

from .project_resolution import canonical_project_id

_log = logging.getLogger(__name__)

# 频率 / 预算闸(都偏保守, 后续有需要再调)
_MIN_INTERVAL_SEC = 90      # 同一会话两次摘要最小间隔
_MAX_PER_TICK = 8           # 单轮最多摘要几条(burst 上限)
_MIN_TICK_GAP_SEC = 12      # 全局两轮 tick 最小间隔(挡住前端高频轮询)
_WORKERS = 4
_DIGEST_FIELDS = ("project", "workstream", "plan", "title", "last_step")
_DIGEST_STORAGE_FIELDS = (*_DIGEST_FIELDS, "summary")
_INSUFFICIENT_PREFIXES = ("信息不足", "资料不足", "证据不足")

_DIGEST_CACHE_LOCK = threading.RLock()
_DIGEST_CACHE_TOKEN: tuple[str, int, int, int] | None = None
_DIGEST_CACHE: dict[str, dict[str, Any]] = {}


def _clean_digest_value(value: Any) -> str:
    """Keep model uncertainty internal instead of turning it into UI copy."""
    text = str(value or "").strip()
    if not text or any(text.startswith(prefix) for prefix in _INSUFFICIENT_PREFIXES):
        return ""
    return text

DIGEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "project": {"type": "string"},    # 项目名(中文为主), 只从会话内容推
        "workstream": {"type": "string"}, # 项目内稳定业务线；不确定留空
        "plan": {"type": "string"},       # 计划名(中文); 没有就填 "无"
        "title": {"type": "string"},      # 这个对话在做什么内容, 一句话中文标题
        "last_step": {"type": "string"},  # 最近一步做了什么 / 正在做什么, 一句话中文
        "summary": {"type": "string"},    # 持续会话摘要；详情侧栏消费，列表不直接展示
    },
    "required": ["title", "last_step"],
}

_SYSTEM = (
        "你是 AI 编码 agent 会话的摘要助手。给你一个 agent 对话的初始任务、最近一段输出, "
    "请用**中文**提炼这个对话最关键的几件事, 让人一眼看懂它在鼓捣什么。要求:\n"
    "- project: 项目名(中文为主, 只能从初始任务、最近输出和摘要内容推断, 如『行者无乡』『anniv-fest 周年庆』『omnicompany 驾驶舱』)。工作目录只是定位元数据, 不能作为项目证据。\n"
    "- workstream: 项目内长期稳定的单一业务线/责任域(不是本轮任务); 看不出就留空。\n"
    "- plan: 计划/任务批次名(中文); 看不出就填『无』。\n"
    "- title: 这个对话整体在做什么内容, 一句话中文标题(≤20字), 具体而非笼统(别写『协助开发』这种废话)。\n"
    "- last_step: 最近一步做了什么 / 正在做什么, 一句话中文(≤30字), 据『最近输出』写实。\n"
    "- summary: 会话关于什么、做了什么、产出了什么、收到什么反馈的持续摘要(≤220字)。\n"
    "只输出 JSON。不要编造工作目录/输出里没有的事实; 某字段信息不足时返回空字符串，绝不输出『信息不足』之类的占位文字。"
)


def _store_path() -> Path:
    return omni_workspace_root() / "data" / "boss_sight" / "agent_digests.json"


@contextmanager
def _digest_write_guard(*, timeout_sec: float = 3.0) -> Iterator[None]:
    """Serialize writers from the dashboard tick and companion worker.

    A directory lock is used because the writers normally live in different
    processes on Windows.  The payload itself remains the existing JSON store;
    this lock is coordination only, never a second digest authority.
    """

    store_path = _store_path()
    store_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = store_path.with_name(f".{store_path.name}.lock")
    deadline = time.monotonic() + max(0.1, timeout_sec)
    while True:
        try:
            lock_path.mkdir()
            break
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > 300:
                    lock_path.rmdir()
                    continue
            except (FileNotFoundError, OSError):
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out acquiring digest writer lock: {lock_path}")
            time.sleep(0.04)
    try:
        yield
    finally:
        try:
            lock_path.rmdir()
        except FileNotFoundError:
            pass


def load_digests() -> dict[str, dict[str, Any]]:
    p = _store_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        # Old model runs may have persisted visible placeholders. Treat them as
        # absent immediately, without requiring a migration or another LLM call.
        for digest in raw.values():
            if not isinstance(digest, dict):
                continue
            for field in _DIGEST_STORAGE_FIELDS:
                digest[field] = _clean_digest_value(digest.get(field))
        return raw
    except (OSError, json.JSONDecodeError):
        return {}


def load_digests_cached() -> dict[str, dict[str, Any]]:
    """Read the shared digest projection at most once per file version.

    Sidebar and retry snapshots are high-frequency read paths.  They only need
    a coherent projection, not a fresh JSON parse on every request.  Writers
    replace the file atomically; the stat token notices local and companion
    process updates without putting the 2 MB decode back on the event loop.
    """
    global _DIGEST_CACHE_TOKEN, _DIGEST_CACHE
    path = _store_path()
    try:
        stat = path.stat()
        token = (str(path), int(stat.st_mtime_ns), int(stat.st_ctime_ns), int(stat.st_size))
    except OSError:
        token = (str(path), 0, 0, 0)
    with _DIGEST_CACHE_LOCK:
        if token == _DIGEST_CACHE_TOKEN:
            return _DIGEST_CACHE
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            _DIGEST_CACHE = raw if isinstance(raw, dict) else {}
        except (OSError, json.JSONDecodeError):
            _DIGEST_CACHE = {}
        for digest in _DIGEST_CACHE.values():
            if not isinstance(digest, dict):
                continue
            for field in _DIGEST_STORAGE_FIELDS:
                digest[field] = _clean_digest_value(digest.get(field))
        _DIGEST_CACHE_TOKEN = token
        return _DIGEST_CACHE


def _save_digests(store: dict[str, dict[str, Any]]) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # 临时名带 pid: 摘要库有多个进程写(ccdaemon / companion worker / litework bridge),
    # 共用一个 .tmp 会互相踩。
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    # Windows 上另一个进程正好开着目标文件读时 replace 直接 WinError 5(2026-09-19 实测),
    # 一次失败就把这一轮摘要整批丢掉。退避重试几次再放弃。
    for attempt in range(6):
        try:
            tmp.replace(p)
            break
        except PermissionError:
            if attempt == 5:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05 * (attempt + 1))
    # ccdaemon owns the live PTY projection. Push newly generated summary
    # titles into memory immediately so the 2s tab-state stream can replace the
    # first-message fallback without waiting for another full session-list read.
    try:
        from omnicompany.dashboard.ccdaemon.api import get_manager

        get_manager().apply_digest_titles(store)
    except Exception:  # noqa: BLE001
        _log.debug("live PTY digest-title projection skipped", exc_info=True)


def _key(item: dict[str, Any]) -> str:
    return f"{item.get('provider')}:{item.get('session_id')}"


def get_digest(
    provider: str,
    session_id: str,
    store: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Read one digest, optionally from a store loaded by the caller.

    Registry rebuilds inspect many sessions in one pass.  Letting that caller
    share its already loaded snapshot avoids reparsing the multi-megabyte JSON
    store once per session while preserving the original single-read API.
    """
    d = (store if store is not None else load_digests()).get(f"{provider}:{session_id}")
    if not d:
        return None
    return {k: _clean_digest_value(d.get(k)) for k in _DIGEST_FIELDS}


def get_session_summary(provider: str, session_id: str) -> str:
    """Return the long-lived companion summary without changing legacy digest shape."""

    row = load_digests_cached().get(f"{provider}:{session_id}") or {}
    return _clean_digest_value(row.get("summary"))


def upsert_digest(
    provider: str,
    session_id: str,
    patch: dict[str, Any],
    *,
    source: str,
    source_mtime: float = 0.0,
) -> dict[str, Any]:
    """Merge one trusted projection writer into the existing digest store.

    This is the convergence entry for lifecycle companions.  It deliberately
    reuses the same store and live PTY projection as the lazy digest tick, so a
    second title/summary authority cannot emerge.
    """

    merged = upsert_digests(
        [(provider, session_id, patch, source_mtime)],
        source=source,
    )
    return merged[f"{str(provider).strip()}:{str(session_id).strip()}"]


def upsert_digests(
    rows: list[tuple[str, str, dict[str, Any], float]],
    *,
    source: str,
) -> dict[str, dict[str, Any]]:
    """Merge a batch of trusted projections under one store write.

    ``upsert_digest`` is the single-row spelling of this call.  Batching exists
    so a backfill pass does not rewrite the whole store once per session; the
    merge rules, lock and live PTY projection stay exactly the same.
    """

    cleaned: list[tuple[str, str, dict[str, Any], float]] = []
    for provider, session_id, patch, source_mtime in rows:
        provider = str(provider or "").strip()
        session_id = str(session_id or "").strip()
        if not provider or not session_id:
            raise ValueError("provider and session_id are required")
        cleaned.append((provider, session_id, patch, source_mtime))
    if not cleaned:
        return {}
    now = time.time()
    out: dict[str, dict[str, Any]] = {}
    with _digest_write_guard():
        # Reload under the cross-process lock so the companion and lazy tick
        # merge their fields instead of replacing a snapshot read earlier.
        store = load_digests()
        for provider, session_id, patch, source_mtime in cleaned:
            key = f"{provider}:{session_id}"
            merged = dict(store.get(key) or {})
            for field in _DIGEST_STORAGE_FIELDS:
                value = _clean_digest_value(patch.get(field))
                if value:
                    merged[field] = value[:1200 if field == "summary" else 180]
                elif field not in merged:
                    merged[field] = ""
            merged.update({
                "_source": str(source or "unknown"),
                "_source_mtime": max(float(merged.get("_source_mtime") or 0.0), float(source_mtime or 0.0)),
                "_updated_ts": now,
                "updated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            })
            store[key] = merged
            out[key] = {field: merged.get(field, "") for field in _DIGEST_STORAGE_FIELDS}
        _save_digests(store)
    return out


def summarize_one(item: dict[str, Any]) -> dict[str, str]:
    """便宜模型提炼项目、业务线、计划与当前进展。"""
    from omnicompany.runtime.llm import call_json

    task = str(item.get("preview") or "")
    latest = str(item.get("last_did") or "")
    user = (
        f"初始任务:\n{task[:1200] or '(无)'}\n\n"
        f"最近输出:\n{latest[:1500] or '(无)'}"
    )
    out = call_json(
        system=_SYSTEM, user=user, schema=DIGEST_SCHEMA,
        # 400 只够写完前两个字段: 2026-09-03 实测 deepseek-v4-pro 在这个 schema 上
        # 被截断成 '{"', 整条管线不产任何标题。summary 一项就允许 1200 字。
        caller="agent_digest.summarize_one", max_tokens=1600,
    )
    return {
        "project": _clean_digest_value(out.get("project"))[:60],
        "workstream": _clean_digest_value(out.get("workstream"))[:80],
        "plan": _clean_digest_value(out.get("plan"))[:80],
        "title": _clean_digest_value(out.get("title"))[:80],
        "last_step": _clean_digest_value(out.get("last_step"))[:160],
        "summary": _clean_digest_value(out.get("summary"))[:1200],
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
    # Providers commonly become idle as soon as an answer ends. Refresh that
    # changed transcript once as well; mtime, interval and batch caps bound cost.
    if item.get("status") not in {"working", "idle"}:
        return False
    mtime = float(item.get("mtime") or 0)
    changed = mtime > float(cur.get("_source_mtime") or 0) + 1
    stale = (now - float(cur.get("_updated_ts") or 0)) >= _MIN_INTERVAL_SEC
    return changed and stale


def _bind_inferred_project(item: dict[str, Any], digest: dict[str, Any]) -> str:
    """Legacy direct-call compatibility; runtime binding belongs to companion."""
    provider = str(item.get("provider") or "").strip()
    session_id = str(item.get("session_id") or "").strip()
    if not provider or not session_id:
        return ""

    from omnicompany.core.projects_registry import list_projects
    from omnicompany.packages.services._core.identity import (
        binding_for_session,
        update_session_binding,
    )

    projects = list_projects()
    active_plan_project = (
        canonical_project_id(
            label=item.get("active_plan"), projects=projects
        )
        if item.get("active_plan")
        else ""
    )
    digest_project = canonical_project_id(
        label=digest.get("project"), projects=projects
    )
    digest_plan_project = canonical_project_id(
        label=digest.get("plan"), projects=projects
    )
    # An active plan is deterministic. Two conflicting model fields are
    # ambiguity, not permission to pick whichever happened to be checked first.
    if active_plan_project:
        project_id = active_plan_project
    elif digest_project and digest_plan_project and digest_project != digest_plan_project:
        return ""
    else:
        project_id = digest_plan_project or digest_project
    if not project_id:
        return ""

    existing = binding_for_session(
        provider=provider,
        session_id=session_id,
        pty_id=item.get("pty_id"),
    )
    if existing and (
        str(existing.get("project") or "").strip()
        or any(
            str(segment.get("project") or "").strip()
            for segment in existing.get("scope_segments") or []
            if isinstance(segment, dict)
        )
    ):
        return ""

    # Re-read immediately before the merge so a user/dispatch bind that landed
    # while the digest was running wins. The resolver itself performs the write.
    existing = binding_for_session(
        provider=provider,
        session_id=session_id,
        pty_id=item.get("pty_id"),
    )
    if existing and str(existing.get("project") or "").strip():
        return ""
    trace_id = str((existing or {}).get("trace_id") or "")
    if not trace_id:
        trace_id = f"session_{session_id}"
    update_session_binding(
        trace_id,
        session_id=session_id,
        provider=provider,
        project=project_id,
        source="agent_digest",
    )
    return project_id


def _bind_inferred_workstream(item: dict[str, Any], digest: dict[str, Any]) -> str:
    """Legacy direct-call compatibility; runtime binding belongs to companion."""
    provider = str(item.get("provider") or "").strip()
    session_id = str(item.get("session_id") or "").strip()
    if not provider or not session_id:
        return ""
    from omnicompany.core.projects_registry import list_projects
    from omnicompany.core.workstreams import infer_workstream
    from omnicompany.packages.services._core.identity import (
        binding_for_session,
        update_session_binding,
    )

    existing = binding_for_session(
        provider=provider,
        session_id=session_id,
        pty_id=item.get("pty_id"),
    )
    if existing and str(existing.get("workstream") or "").strip():
        return ""
    projects = list_projects()
    project = str((existing or {}).get("project") or "").strip() or canonical_project_id(
        label=digest.get("project"), projects=projects
    )
    if not project and item.get("active_plan"):
        project = canonical_project_id(
            label=item.get("active_plan"), projects=projects
        )
    if not project:
        return ""
    workstream = infer_workstream(
        project,
        digest.get("workstream"),
        digest.get("title"),
        digest.get("last_step"),
        item.get("preview"),
        item.get("last_did"),
    )
    if not workstream:
        return ""
    # Re-check immediately before merge so explicit user binding always wins.
    existing = binding_for_session(
        provider=provider,
        session_id=session_id,
        pty_id=item.get("pty_id"),
    ) or existing
    if existing and str(existing.get("workstream") or "").strip():
        return ""
    trace_id = str((existing or {}).get("trace_id") or "") or f"session_{session_id}"
    update_session_binding(
        trace_id,
        session_id=session_id,
        provider=provider,
        project=project,
        workstream=workstream,
        source="agent_digest",
    )
    return workstream


def _bind_known_scopes(items: list[dict[str, Any]], store: dict[str, dict[str, Any]]) -> tuple[int, int]:
    projects_bound = 0
    workstreams_bound = 0
    for item in items:
        digest = store.get(_key(item))
        if not digest:
            continue
        try:
            if _bind_inferred_project(item, digest):
                projects_bound += 1
            if _bind_inferred_workstream(item, digest):
                workstreams_bound += 1
        except Exception:  # noqa: BLE001
            _log.warning("agent digest scope binding skipped", exc_info=True)
    return projects_bound, workstreams_bound


_tick_lock = threading.Lock()
_tick_running = False
_last_tick_ts = 0.0


def run_tick(items: list[dict[str, Any]], *, now: float | None = None,
             max_per_tick: int = _MAX_PER_TICK, workers: int = _WORKERS) -> dict[str, Any]:
    """Legacy digest refresh API; it is not scheduled by the runtime anymore."""
    from omnicompany.runtime.llm.batch import run_parallel_items

    now = now if now is not None else time.time()
    store = load_digests()
    targets = [it for it in items if _needs_digest(it, store.get(_key(it)), now)]
    # 运行中的优先, 再按最近活跃排序
    targets.sort(key=lambda it: (0 if it.get("status") == "working" else 1, -float(it.get("mtime") or 0)))
    targets = targets[:max_per_tick]
    if not targets:
        projects_bound, workstreams_bound = _bind_known_scopes(items, store)
        return {"updated": 0, "failed": 0, "targets": 0, "scanned": len(items),
                "projects_bound": projects_bound, "workstreams_bound": workstreams_bound}

    res = run_parallel_items(targets, _worker, workers=workers, progress_label="agent-digest")
    iso = datetime.fromtimestamp(now, timezone.utc).isoformat()
    updated = 0
    skipped_insufficient = 0
    with _digest_write_guard():
        # Model calls happen outside the lock.  Re-read only for the merge so a
        # companion summary written while the calls ran is never lost.
        store = load_digests()
        for d in res.results:
            k = d.pop("_key")
            src = d.pop("_source_mtime", 0.0)
            previous = store.get(k) if isinstance(store.get(k), dict) else {}
            merged = dict(previous)
            accepted = 0
            for field in _DIGEST_STORAGE_FIELDS:
                value = _clean_digest_value(d.get(field))
                if value:
                    merged[field] = value
                    accepted += 1
                elif field not in merged:
                    merged[field] = ""
            # Even when no visible field is usable, remember which transcript
            # generation was attempted. This prevents repeated token spend while
            # preserving the last good summary (or the normal transcript fallback).
            merged.update({"_source_mtime": src, "_updated_ts": now, "updated_at": iso})
            store[k] = merged
            if accepted:
                updated += 1
            else:
                skipped_insufficient += 1
        _save_digests(store)
    projects_bound, workstreams_bound = _bind_known_scopes(items, store)
    return {"updated": updated, "skipped_insufficient": skipped_insufficient,
            "failed": len(res.failures),
            "targets": len(targets), "scanned": len(items), "projects_bound": projects_bound,
            "workstreams_bound": workstreams_bound}


def schedule_tick(items: list[dict[str, Any]]) -> dict[str, Any]:
    """懒触发: /active 调一次, 单飞 + 全局节流, 在线程里跑(不堵事件循环, 失败静默)。"""
    return {
        "scheduled": False,
        "owner": "session_companion",
        "legacy_items_ignored": len(items),
    }


# ── 扫描侧补盲: companion 看不见的对话也要有摘要 ────────────────────────────
# companion 只在自己的生命周期事件上写摘要, 所以它只覆盖经 Dashboard PTY / hook 跑起来的会话。
# /active 是靠 transcript 扫盘发现对话的, 里面大量是在别处直接起的 claude/codex/kiro —— 这些会话
# 永远不会产生 companion 事件, 2026-08-14 旧懒触发停摆后就再没有人给它们算过摘要, 列表里只剩
# 原始首条 prompt(用户 2026-09-19: "好多对话已经没有在总结会话内容和会话项目了")。
# 这里只补这一块盲区, 且刻意不做任何身份绑定写入 —— project/workstream 绑定仍然只有 companion
# 一个权威, 本管线的 project 字段只是 residents 在没有权威绑定时的显示回退。
_SCAN_BACKFILL_SOURCE = "active_scan"
# 本管线只维护自己写的行, 以及旧懒触发留下的无 _source 存量行; 其它写入方的行一概不碰。
_SCAN_BACKFILL_OWNED = ("", _SCAN_BACKFILL_SOURCE)


def _needs_scan_backfill(
    item: dict[str, Any], cur: dict[str, Any] | None, now: float
) -> bool:
    if not item.get("session_id"):
        return False
    if cur is None:
        return True  # 从没被摘要过 —— 正是本管线存在的理由
    mtime = float(item.get("mtime") or 0)
    changed = mtime > float(cur.get("_source_mtime") or 0) + 1
    stale = (now - float(cur.get("_updated_ts") or 0)) >= _MIN_INTERVAL_SEC
    if not str(cur.get("title") or "").strip():
        # 有行但标题是空的 = 上一个写入方(常见是 companion 的便宜模型)什么都没提炼出来。
        # 2026-09-19 实测近四天 107/359 次 companion 运行返回全空摘要, 列表上就退回成原始
        # 首条 prompt。等同于"没覆盖": 只要 transcript 比上次尝试新就再试一次, 仍受最小间隔
        # 和每轮上限约束, 内容确实为空的会话不会被反复重算。
        return changed and stale
    if str(cur.get("_source") or "") not in _SCAN_BACKFILL_OWNED:
        return False  # 别人已经写出可用标题 —— 那是它的行, 不碰
    if item.get("status") not in {"working", "idle"}:
        return False
    return changed and stale


def run_scan_backfill(
    items: list[dict[str, Any]], *, now: float | None = None,
    max_per_tick: int = _MAX_PER_TICK, workers: int = _WORKERS,
) -> dict[str, Any]:
    """给扫描发现、但 companion 从未覆盖的对话补一次摘要(不写任何身份绑定)。"""
    from omnicompany.runtime.llm.batch import run_parallel_items

    now = now if now is not None else time.time()
    store = load_digests()
    targets = [it for it in items if _needs_scan_backfill(it, store.get(_key(it)), now)]
    # 运行中的优先, 再按最近活跃排序
    targets.sort(key=lambda it: (0 if it.get("status") == "working" else 1, -float(it.get("mtime") or 0)))
    targets = targets[:max_per_tick]
    if not targets:
        return {"updated": 0, "failed": 0, "targets": 0, "scanned": len(items)}

    res = run_parallel_items(targets, _worker, workers=workers, progress_label="agent-digest-backfill")
    rows: list[tuple[str, str, dict[str, Any], float]] = []
    accepted = 0
    for d in res.results:
        key = str(d.pop("_key"))
        source_mtime = float(d.pop("_source_mtime", 0.0) or 0.0)
        provider, _, session_id = key.partition(":")
        if not provider or not session_id:
            continue
        rows.append((provider, session_id, d, source_mtime))
        # 一个可见字段都没提炼出来时也要写回: _source_mtime/_updated_ts 记下"这代 transcript 试过了",
        # 挡住对同一段内容反复烧 token, 同时保留上一版好摘要和前端的原始 preview 回退。
        if any(_clean_digest_value(d.get(field)) for field in _DIGEST_STORAGE_FIELDS):
            accepted += 1
    if rows:
        upsert_digests(rows, source=_SCAN_BACKFILL_SOURCE)
    return {
        "updated": accepted,
        "skipped_insufficient": len(rows) - accepted,
        "failed": len(res.failures),
        "targets": len(targets),
        "scanned": len(items),
    }


_backfill_lock = threading.Lock()
_backfill_running = False
_last_backfill_ts = 0.0


def schedule_scan_backfill(items: list[dict[str, Any]]) -> dict[str, Any]:
    """懒触发: /active 每次调一次, 单飞 + 全局节流, 在后台线程里跑(不堵响应, 失败静默)。"""
    global _backfill_running, _last_backfill_ts

    now = time.time()
    with _backfill_lock:
        if _backfill_running:
            return {"scheduled": False, "reason": "running"}
        # 节流先判: 别让每次前端轮询都去读一遍几 MB 的摘要库。
        if now - _last_backfill_ts < _MIN_TICK_GAP_SEC:
            return {"scheduled": False, "reason": "throttled"}
        snapshot = [dict(it) for it in items]
        store = load_digests()
        pending = sum(1 for it in snapshot if _needs_scan_backfill(it, store.get(_key(it)), now))
        if not pending:
            _last_backfill_ts = now
            return {"scheduled": False, "reason": "covered"}
        _backfill_running = True
        _last_backfill_ts = now

    def _run() -> None:
        global _backfill_running
        try:
            run_scan_backfill(snapshot)
        except Exception:  # noqa: BLE001 - 摘要缺失只降级显示, 不能拖垮会话列表
            _log.warning("agent digest scan backfill failed", exc_info=True)
        finally:
            with _backfill_lock:
                _backfill_running = False

    threading.Thread(target=_run, daemon=True, name="agent-digest-backfill").start()
    return {"scheduled": True, "pending": pending, "scanned": len(snapshot)}


__all__ = [
    "DIGEST_SCHEMA",
    "get_digest",
    "get_session_summary",
    "load_digests",
    "run_scan_backfill",
    "run_tick",
    "schedule_scan_backfill",
    "schedule_tick",
    "summarize_one",
    "upsert_digest",
    "upsert_digests",
]
