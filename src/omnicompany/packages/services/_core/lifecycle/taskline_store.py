# [OMNI] origin=claude-code domain=services/_core/lifecycle ts=2026-08-21T00:00:00+08:00 type=service status=active
# [OMNI] summary="任务线存储: 承载一件事全过程的标识, 绑一个项目+多个 plan+多个 todo; 支持 fork/split 并留血缘。横切在 plan-groups 之上, 不改 plan-groups。"
# [OMNI] why="用户 2026-08-21: 任务线目前就是一个标识, 可以绑定一个项目/多个 plan/多个 todo, 可 CLI 查阅, 可 fork; 追加要求可拆分。"
# [OMNI] material_id="material:services.core.lifecycle.taskline_store"
"""任务线（taskline）存储。

一条任务线 = 一件事的全过程标识。它**横切**在 plan-groups（组 → 里程碑 → 任务）之上：
一条线可以跨组、跨计划。本模块不读写 plan-groups，只记引用。

单文件一条线：`data/services/tasklines/<id>.json`
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

_write_lock = threading.RLock()

# 任务线五态 + 归档。归档 != 抛弃：抛弃是不做了, 归档是不在视野里。
VALID_STATUS = ("active", "paused", "done", "dropped", "archived")

_CJK = "一-鿿"


def _repo_root() -> Path:
    from omnicompany.core.config import omni_workspace_root

    return Path(omni_workspace_root())


def tasklines_root() -> Path:
    return _repo_root() / "data" / "services" / "tasklines"


def _path(taskline_id: str) -> Path:
    return tasklines_root() / f"{taskline_id}.json"


def _now() -> int:
    return int(time.time())


def slug(text: str) -> str:
    base = re.sub(rf"[^0-9a-zA-Z{_CJK}]+", "-", text).strip("-").lower()
    return base[:40] or "line"


def new_id(title: str) -> str:
    return f"tl-{_now()}-{slug(title)[:24]}"


def _empty(taskline_id: str, title: str) -> dict[str, Any]:
    now = _now()
    return {
        "id": taskline_id,
        "title": title,
        "project": None,
        "plans": [],
        "todos": [],
        "summary": "",
        "doc_paths": [],
        "work_paths": [],
        "session_files": [],
        "status": "active",
        "forked_from": None,
        "split_from": None,
        "created_at": now,
        "updated_at": now,
    }


def load(taskline_id: str) -> dict[str, Any] | None:
    p = _path(taskline_id)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save(record: dict[str, Any]) -> dict[str, Any]:
    with _write_lock:
        root = tasklines_root()
        root.mkdir(parents=True, exist_ok=True)
        record["updated_at"] = _now()
        p = _path(str(record["id"]))
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        return record


def create(title: str, project: str | None = None, summary: str = "") -> dict[str, Any]:
    rec = _empty(new_id(title), title.strip())
    rec["project"] = project
    rec["summary"] = summary
    return save(rec)


def list_all(include_archived: bool = False) -> list[dict[str, Any]]:
    root = tasklines_root()
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.glob("*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if rec.get("status") == "archived" and not include_archived:
            continue
        out.append(rec)
    out.sort(key=lambda r: r.get("updated_at") or 0, reverse=True)
    return out


_LIST_FIELDS = ("plans", "todos", "doc_paths", "work_paths", "session_files")


def bind(taskline_id: str, **fields: Any) -> dict[str, Any]:
    """绑定/追加。project 是单值覆盖; 列表字段去重追加。"""
    rec = load(taskline_id)
    if rec is None:
        raise KeyError(f"任务线不存在: {taskline_id}")
    for key, value in fields.items():
        if value in (None, (), []):
            continue
        if key == "project":
            rec["project"] = str(value).strip()
        elif key == "summary":
            rec["summary"] = str(value)
        elif key in _LIST_FIELDS:
            items = [value] if isinstance(value, str) else list(value)
            # id/路径一律去首尾空白: shell 管道很容易把 CR 带进来, 静默错配比报错更难查。
            items = [str(it).strip() for it in items if str(it).strip()]
            existing = rec.setdefault(key, [])
            for it in items:
                if it not in existing:
                    existing.append(it)
        else:
            raise ValueError(f"不支持的字段: {key}")
    return save(rec)


def unbind(taskline_id: str, **fields: Any) -> dict[str, Any]:
    rec = load(taskline_id)
    if rec is None:
        raise KeyError(f"任务线不存在: {taskline_id}")
    for key, value in fields.items():
        if value in (None, (), []):
            continue
        if key == "project":
            rec["project"] = None
        elif key in _LIST_FIELDS:
            items = [value] if isinstance(value, str) else list(value)
            rec[key] = [x for x in rec.get(key, []) if x not in items]
        else:
            raise ValueError(f"不支持的字段: {key}")
    return save(rec)


def set_status(taskline_id: str, status: str) -> dict[str, Any]:
    if status not in VALID_STATUS:
        raise ValueError(f"状态必须是 {VALID_STATUS} 之一, 收到 {status!r}")
    rec = load(taskline_id)
    if rec is None:
        raise KeyError(f"任务线不存在: {taskline_id}")
    rec["status"] = status
    return save(rec)


def fork(taskline_id: str, title: str | None = None) -> dict[str, Any]:
    """继承相同上下文, 后续独立进行 —— 不再维护同一份任务线。血缘留 forked_from。"""
    src = load(taskline_id)
    if src is None:
        raise KeyError(f"任务线不存在: {taskline_id}")
    new_title = title or f"{src['title']}（fork）"
    rec = _empty(new_id(new_title), new_title)
    # 上下文继承: 项目/摘要/路径/历史会话文件都带走; plan/todo 也带一份引用。
    for key in ("project", "summary", "plans", "todos", "doc_paths", "work_paths", "session_files"):
        value = src.get(key)
        rec[key] = list(value) if isinstance(value, list) else value
    rec["forked_from"] = src["id"]
    return save(rec)


def split(
    taskline_id: str,
    title: str,
    todos: list[str] | None = None,
    plans: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """把一条线拆成两条: 指定的 todo/plan 移到新线上, 原线保留其余。血缘留 split_from。"""
    src = load(taskline_id)
    if src is None:
        raise KeyError(f"任务线不存在: {taskline_id}")
    move_todos = list(todos or [])
    move_plans = list(plans or [])
    unknown = [t for t in move_todos if t not in src.get("todos", [])]
    unknown += [p for p in move_plans if p not in src.get("plans", [])]
    if unknown:
        raise ValueError(f"这些条目不在原任务线上, 拆不出去: {unknown}")

    rec = _empty(new_id(title), title.strip())
    rec["project"] = src.get("project")
    rec["summary"] = src.get("summary", "")
    rec["todos"] = move_todos
    rec["plans"] = move_plans
    # 上下文类字段两边都留 (拆分不是切断历史)
    for key in ("doc_paths", "work_paths", "session_files"):
        rec[key] = list(src.get(key) or [])
    rec["split_from"] = src["id"]
    save(rec)

    src["todos"] = [t for t in src.get("todos", []) if t not in move_todos]
    src["plans"] = [p for p in src.get("plans", []) if p not in move_plans]
    save(src)
    return rec, src


def lines_for(kind: str, ref: str, include_archived: bool = True) -> list[dict[str, Any]]:
    """反向查询: 给一个 project / plan / todo, 返回关联的任务线 (双向可见)。"""
    field = {"project": "project", "plan": "plans", "todo": "todos"}.get(kind)
    if field is None:
        raise ValueError("kind 必须是 project / plan / todo")
    out = []
    for rec in list_all(include_archived=include_archived):
        value = rec.get(field)
        if field == "project":
            if value and str(value) == ref:
                out.append(rec)
        elif ref in (value or []):
            out.append(rec)
    return out


__all__ = [
    "VALID_STATUS",
    "tasklines_root",
    "new_id",
    "load",
    "save",
    "create",
    "list_all",
    "bind",
    "unbind",
    "set_status",
    "fork",
    "split",
    "lines_for",
]
