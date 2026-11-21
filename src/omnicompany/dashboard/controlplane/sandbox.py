# [OMNI] origin=ai-ide domain=dashboard ts=2026-05-02T05:00:00Z type=router status=active agent=ai-ide-current
# [OMNI] summary="dashboard sandbox API - G5 沙盒状态只读 + G1 身份只读"
# [OMNI] why="web 端能看沙盒草稿清单 + 当前 session 身份. 不暴露 new/promote 写操作 (走 CLI 兜底)"
# [OMNI] tags=dashboard,api,sandbox,identity,read-only,G1-G5-integration
# [OMNI] material_id="material:dashboard.sandbox_identity.read_api.py"
"""dashboard G5 沙盒 + G1 身份只读 API.

接口:
  GET /api/v2/sandbox/drafts            列沙盒 drafts/ 草稿清单
  GET /api/v2/sandbox/archive           列归档清单
  GET /api/v2/sandbox/guides/{kind}     对应 kind 的向导.md 内容
  GET /api/v2/identity/who              当前 session 身份 (跟 omni who 同源)
  GET /api/v2/identity/writes           当前 session 写过的文件 (从 event bus 派生)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException


sandbox_router = APIRouter()


def _project_root() -> Path:
    here = Path(__file__).resolve()
    for p in (here, *here.parents):
        if (p / "src" / "omnicompany").is_dir() and (p / "docs").is_dir():
            return p
    return here.parents[3]


def _sandbox_root() -> Path:
    return _project_root() / ".omni" / "sandbox"


@sandbox_router.get("/sandbox/drafts")
def list_drafts() -> dict[str, Any]:
    """列沙盒 drafts/ 草稿清单, 按 kind 分组."""
    drafts = _sandbox_root() / "drafts"
    if not drafts.is_dir():
        return {"by_kind": {}, "total": 0}
    by_kind: dict[str, list[dict[str, Any]]] = {}
    total = 0
    for kind_dir in sorted(drafts.iterdir()):
        if not kind_dir.is_dir():
            continue
        items = []
        for entry in sorted(kind_dir.iterdir()):
            try:
                stat = entry.stat()
            except OSError:
                continue
            items.append({
                "name": entry.name,
                "path": str(entry.relative_to(_project_root())).replace("\\", "/"),
                "is_dir": entry.is_dir(),
                "size_bytes": stat.st_size if entry.is_file() else None,
                "mtime": stat.st_mtime,
            })
            total += 1
        by_kind[kind_dir.name] = items
    return {"by_kind": by_kind, "total": total}


@sandbox_router.get("/sandbox/archive")
def list_archive() -> dict[str, Any]:
    """列沙盒 archive/ 归档清单 (按时间倒序)."""
    archive = _sandbox_root() / "archive"
    if not archive.is_dir():
        return {"items": [], "total": 0}
    items = []
    for entry in sorted(archive.iterdir(), key=lambda p: p.name, reverse=True):
        if not entry.is_dir():
            continue
        try:
            count = sum(1 for _ in entry.rglob("*") if _.is_file())
        except OSError:
            count = 0
        items.append({
            "name": entry.name,
            "path": str(entry.relative_to(_project_root())).replace("\\", "/"),
            "file_count": count,
        })
    return {"items": items, "total": len(items)}


@sandbox_router.get("/sandbox/guides/{kind}")
def get_guide(kind: str) -> dict[str, str]:
    """显示某 kind 的向导.md (8 kind + header + omni-header / omnicompany_cli / sandbox 三份 cli 规范)."""
    proj = _project_root()
    if kind == "header":
        guide = proj / "docs" / "standards" / "cli" / "omni-header.md"
    elif kind == "cli":
        guide = proj / "docs" / "standards" / "cli" / "omnicompany_cli.md"
    elif kind == "sandbox":
        guide = proj / "docs" / "standards" / "cli" / "sandbox.md"
    else:
        guide = proj / "templates" / kind / "向导.md"
    if not guide.is_file():
        raise HTTPException(status_code=404, detail=f"指引不存在: {guide}")
    return {
        "kind": kind,
        "path": str(guide.relative_to(proj)).replace("\\", "/"),
        "content": guide.read_text(encoding="utf-8"),
    }


@sandbox_router.get("/identity/who")
def who() -> dict[str, Any]:
    """当前 session 身份 (跟 omni who 同源)."""
    from omnicompany.packages.services._core.identity import current_session_meta
    return current_session_meta()


@sandbox_router.get("/identity/writes")
def writes(limit: int = 50) -> dict[str, Any]:
    """当前 session 写过的文件 (跟 omni who --writes 同源)."""
    from omnicompany.packages.services._core.identity import (
        resolve_active_trace_id, session_writes,
    )
    trace_id = resolve_active_trace_id()
    items = session_writes(trace_id, limit=limit)
    return {"trace_id": trace_id, "items": items, "total": len(items)}
