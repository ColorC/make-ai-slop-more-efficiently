# [OMNI] origin=ai-ide ts=2026-06-12 type=infra
# [OMNI] material_id="material:dashboard.controlplane.projects_api.py"
"""controlplane/projects.py — 项目工作板 API (驾驶舱首页数据源)。

挂 dashboard 进程(8210, 可自由重启), 不挂 ccdaemon — 存储是纯文件
(data/registry/projects.json + 各项目的 PROJECT_INDEX.md), 无进程内状态。
唯一权威模型在 core/projects_registry.py(CLI omni project 同源), 本路由只是消费方。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from omnicompany.core.projects_registry import (
    assets_dir,
    enrich_projects,
    list_projects,
    parse_index_file,
    plan_governance,
    remove_project,
    resolve_project_plans,
    set_project,
)

projects_router = APIRouter(tags=["projects"])


@projects_router.get("/projects")
def get_projects(fresh: bool = False) -> dict[str, Any]:
    """项目工作板全量(含 last_active / activity_7d / quick_actions)。用户首页与总控共用。

    fresh=1 = 用户点了刷新按钮: 穿透 index 解析缓存, 保证读到最新。
    """
    return enrich_projects(fresh=fresh)


class ProjectUpsert(BaseModel):
    id: str
    name: str | None = None
    group: str | None = None
    tags: list[str] | None = None
    desc: str | None = None
    roots: list[str] | None = None
    index_path: str | None = None
    bg: str | None = None
    icon: str | None = None
    plan_categories: list[str] | None = None
    links: list[dict[str, str]] | None = None
    pinned: bool | None = None
    by: str = "human"


@projects_router.post("/projects")
def upsert_project(req: ProjectUpsert) -> dict[str, Any]:
    fields = req.model_dump(exclude={"id", "by"}, exclude_none=True)
    try:
        item = set_project(req.id, by=req.by, **fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "project": item}


@projects_router.post("/projects/remove")
def delete_project(req: dict) -> dict[str, Any]:
    pid = str(req.get("id") or "")
    return {"ok": remove_project(pid)}


@projects_router.get("/projects/{project_id}/plans")
def get_project_plans(project_id: str) -> dict[str, Any]:
    """项目关联计划 — **服务端**归属(治理覆盖表优先, 退回前缀规则)。

    2026-06-12 用户: demogame 各项目计划列表全错。根因之一是前端自带一份前缀匹配逻辑,
    治理覆盖表落地后归属判断收口到 core.resolve_project_plans, 前端只消费本端点;
    返回的 plan_ids 同时供前端过滤对话(active_plan)/审阅材料(source_plan_id)。
    """
    proj = next((p for p in list_projects() if p.get("id") == project_id), None)
    if proj is None:
        raise HTTPException(status_code=404, detail=f"未注册的项目: {project_id}")
    from omnicompany.core.projects_registry import _plan_catalogue
    gov = plan_governance()
    items = resolve_project_plans(project_id, proj.get("plan_categories"), _plan_catalogue(), gov)
    out = [{
        "id": it["id"],
        "topic": it.get("topic"),
        "title_zh": (gov.get(it["id"]) or {}).get("title_zh") or None,
        "date": it.get("date"),
        "category": it.get("category"),
        "archived": bool(it.get("archived")),
    } for it in items]
    out.sort(key=lambda x: (x.get("date") or ""), reverse=True)
    return {"project": project_id, "items": out, "plan_ids": [x["id"] for x in out]}


@projects_router.get("/projects/{project_id}/findings")
def get_project_findings(project_id: str) -> dict[str, Any]:
    """本项目的工作历史证据(重复需求/重复指正) — 治理部门 work_history 的分配结果。

    2026-06-12 用户: "重复需求和重复指正可以分配到项目上"。数据 = 最近一次
    history-run + history-assign(便宜模型分配, 主力模型复核)产物。
    """
    proj = next((p for p in list_projects() if p.get("id") == project_id), None)
    if proj is None:
        raise HTTPException(status_code=404, detail=f"未注册的项目: {project_id}")
    try:
        from omnicompany.packages.services._governance.work_history import latest_findings
        f = latest_findings() or {}
    except Exception:  # noqa: BLE001 — 治理产物损坏不拖垮项目页
        f = {}
    def _mine(key: str) -> list[dict[str, Any]]:
        return [{k: v for k, v in it.items() if k != "assigned"}
                for it in (f.get(key) or []) if project_id in (it.get("assigned") or [])]
    return {
        "project": project_id,
        "generated_at": f.get("generated_at"),
        "days": f.get("days"),
        "needs": _mine("recurring_needs"),
        "corrections": _mine("recurring_corrections"),
    }


@projects_router.get("/projects/{project_id}/index")
def get_project_index(project_id: str) -> dict[str, Any]:
    """index 文件全文 + frontmatter 解析(项目详情页用)。"""
    proj = next((p for p in list_projects() if p.get("id") == project_id), None)
    if proj is None:
        raise HTTPException(status_code=404, detail=f"未注册的项目: {project_id}")
    index_path = proj.get("index_path")
    if not index_path:
        return {"ok": False, "error": "项目未配置 index_path", "project": proj}
    parsed = parse_index_file(index_path)
    body = ""
    p = Path(index_path)
    if p.is_file():
        try:
            body = p.read_text(encoding="utf-8")
        except OSError:
            pass
    return {**parsed, "path": index_path, "content": body}


@projects_router.get("/project-assets/{filename}")
def get_project_asset(filename: str) -> FileResponse:
    """项目背景图等生成资产(data/boss_sight/project_assets/)。"""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="非法文件名")
    f = assets_dir() / filename
    if not f.is_file():
        raise HTTPException(status_code=404, detail=f"资产不存在: {filename}")
    return FileResponse(str(f))
