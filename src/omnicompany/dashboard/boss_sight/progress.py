# [OMNI] origin=ai-ide ts=2026-06-06 type=infra
# [OMNI] material_id="material:dashboard.boss_sight.progress_store.py"
"""progress — project / plan 的历史时间线条目(用户 + 所有 agent 共用)。

用户 2026-06-06: 每个 project 和 plan 要有时间线; 所有 agent 用 `omni progress` 子命令做历史 CRUD;
条目自动记时间戳 + 所属 plan/project; 网页上能按时间看这个 project 经历了什么、产出了什么。

设计:
- 一层独立覆盖, 存 data/boss_sight/progress.json, **不改 plan/project 文件**。
- 条目 = {id, ref_type(plan|project), ref_id, text, by, created_at, updated_at}。
- 纯模块(不依赖 FastAPI): CLI(omni progress)与 ccdaemon 路由都能 import, 单一数据源。
- 无内存缓存: 每次读盘(CLI 写、daemon 读, 跨进程; 量小)。
- 时间线"产出时间"那部分(各 material 产出)由前端把本条目流与 plan 目录文件 mtime 合并, 不在此存。
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root
from omnicompany.packages.services._core.omnicompany.formats import PROGRESS_ENTRY
from omnicompany.packages.services._core.omnicompany.material_events import publish_material_event

REF_TYPES: tuple[str, ...] = ("plan", "project")

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path() -> Path:
    return omni_workspace_root() / "data" / "boss_sight" / "progress.json"


def _read() -> dict[str, Any]:
    p = _path()
    if not p.is_file():
        return {"version": 1, "entries": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "entries": []}
    if not isinstance(data, dict):
        return {"version": 1, "entries": []}
    data.setdefault("entries", [])
    if not isinstance(data["entries"], list):
        data["entries"] = []
    return data


def _write(data: dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _now()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def add_entry(ref_type: str, ref_id: str, text: str, *, by: str = "human") -> dict[str, Any]:
    """新增一条历史(自动记时间戳 + 所属 plan/project)。"""
    if ref_type not in REF_TYPES:
        raise ValueError(f"ref_type 必须是 {REF_TYPES}, 收到 {ref_type!r}")
    ref_id = (ref_id or "").strip()
    text = (text or "").strip()
    if not ref_id:
        raise ValueError("ref_id 不能为空")
    if not text:
        raise ValueError("text 不能为空")
    with _lock:
        data = _read()
        entry = {
            "id": uuid.uuid4().hex[:10],
            "ref_type": ref_type,
            "ref_id": ref_id,
            "text": text,
            "by": by,
            "created_at": _now(),
            "updated_at": _now(),
        }
        data["entries"].append(entry)
        _write(data)
        publish_material_event(PROGRESS_ENTRY.id, entry, source="boss_sight.progress")
        return dict(entry)


def list_entries(ref_type: str | None = None, ref_id: str | None = None) -> list[dict[str, Any]]:
    """列出条目(可按 ref_type/ref_id 过滤), 按 created_at 升序。"""
    entries = list(_read().get("entries", []))
    if ref_type is not None:
        entries = [e for e in entries if e.get("ref_type") == ref_type]
    if ref_id is not None:
        entries = [e for e in entries if e.get("ref_id") == ref_id]
    entries.sort(key=lambda e: e.get("created_at") or "")
    return entries


def edit_entry(entry_id: str, text: str) -> dict[str, Any] | None:
    """改一条历史的文本。"""
    text = (text or "").strip()
    if not text:
        raise ValueError("text 不能为空")
    with _lock:
        data = _read()
        for e in data["entries"]:
            if e.get("id") == entry_id:
                e["text"] = text
                e["updated_at"] = _now()
                _write(data)
                return dict(e)
    return None


def remove_entry(entry_id: str) -> bool:
    """删一条历史。"""
    with _lock:
        data = _read()
        n0 = len(data["entries"])
        data["entries"] = [e for e in data["entries"] if e.get("id") != entry_id]
        if len(data["entries"]) == n0:
            return False
        _write(data)
        return True


__all__ = ["REF_TYPES", "add_entry", "list_entries", "edit_entry", "remove_entry"]
