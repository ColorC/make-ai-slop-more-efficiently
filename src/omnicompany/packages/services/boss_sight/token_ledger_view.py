# [OMNI] origin=claude-code ts=2026-07-03T00:00:00Z type=dashboard summary="boss_sight token 记账只读视图: 读 data/llm/token_ledger/ 落盘产物(由 omni token-ledger run/gov-token-ledger-daily 写入)聚合成响应; 真实 ~/.claude+~/.codex 量级达 GB 级, 网页请求绝不现场全量重扫" why="overnight-run.md 第六节接口=看板 /api/boss-sight/token-ledger; 实测 ~/.claude 顶层会话2GB+~/.codex 6.8GB, 每次请求现场收集会超时, 落盘产物是唯一可行的读路径" tags=token-ledger,dashboard,boss-sight
"""boss_sight token 记账视图 —— 读落盘产物, 不现场重扫真实数据源。

`omni token-ledger run`(CLI)或 `gov-token-ledger-daily`(定时任务)负责按水位线增量
扫三路数据源并写 data/llm/token_ledger/{sessions.jsonl,internal_by_caller_day.jsonl,
watermark.json}; 本模块只读这三份文件重新聚合成响应。

理由(侦察实测): ~/.claude 顶层会话 jsonl 累计 ~2GB, ~/.codex 累计 ~6.8GB —— 每次网页
请求都现场全量扫这些文件会超时(远超页面可接受延迟)。水位线增量收集应由 CLI/定时任务
按自己的节奏跑, 网页只读最近一次的落盘结果, `generated_at` 告诉用户数据新鲜度。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root


def _out_dir() -> Path:
    return omni_workspace_root() / "data" / "llm" / "token_ledger"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return out


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _by_day(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for row in sessions:
        day = str(row.get("started") or "")[:10] or "unknown-day"
        b = buckets.setdefault(day, {"day": day, "session_count": 0, "calls": 0})
        b["session_count"] += 1
        b["calls"] += int(row.get("calls") or 0)
    return list(buckets.values())


def _by_project(sessions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    buckets: dict[str, dict[str, Any]] = {}
    unlinked: list[dict[str, Any]] = []
    for row in sessions:
        project = row.get("project") or "未关联"
        b = buckets.setdefault(project, {"project": project, "session_count": 0, "calls": 0})
        b["session_count"] += 1
        b["calls"] += int(row.get("calls") or 0)
        if project == "未关联":
            unlinked.append({
                "session_id": row.get("session_id"),
                "cwd": row.get("cwd"),
                "provider": row.get("provider"),
                "calls": row.get("calls"),
            })
    return list(buckets.values()), unlinked


def build_token_ledger_view() -> dict[str, Any]:
    """读 data/llm/token_ledger/ 落盘产物, 折算成按会话/按天/按项目三种聚合视图。"""
    out_dir = _out_dir()
    sessions_path = out_dir / "sessions.jsonl"
    internal_path = out_dir / "internal_by_caller_day.jsonl"
    watermark_path = out_dir / "watermark.json"

    sessions = _read_jsonl(sessions_path)
    internal_rows = _read_jsonl(internal_path)
    has_data = sessions_path.is_file() or internal_path.is_file()

    by_project, unlinked_bucket = _by_project(sessions)

    generated_at = None
    try:
        if sessions_path.is_file():
            import datetime as _dt
            generated_at = _dt.datetime.fromtimestamp(
                sessions_path.stat().st_mtime, tz=_dt.timezone.utc
            ).isoformat()
    except OSError:
        generated_at = None

    return {
        "available": has_data,
        "generated_at": generated_at,
        "note": "读落盘产物(data/llm/token_ledger/), 由 `omni token-ledger run` 按水位线增量刷新;"
                " 首次未跑过时 available=false。" if not has_data else "",
        "out_dir": str(out_dir),
        "watermark_present": watermark_path.is_file(),
        "by_session": sessions,
        "by_day": _by_day(sessions),
        "by_project": by_project,
        "internal_by_caller_day": internal_rows,
        "unlinked_bucket": unlinked_bucket,
    }


__all__ = ["build_token_ledger_view"]
