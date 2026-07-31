# [OMNI] origin=ai-ide ts=2026-06-12 type=infra
# [OMNI] material_id="material:dashboard.controlplane.projects_api.py"
"""controlplane/projects.py — 项目工作板 API (驾驶舱首页数据源)。

挂 dashboard 进程(8210, 可自由重启), 不挂 ccdaemon — 存储是纯文件
(data/registry/projects.json + 各项目的 PROJECT_INDEX.md), 无进程内状态。
唯一权威模型在 core/projects_registry.py(CLI omni project 同源), 本路由只是消费方。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel

from omnicompany.core.projects_registry import (
    assets_dir,
    build_quests,
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


@projects_router.get("/project-views")
def get_project_views() -> dict[str, Any]:
    """应用视图(项目工作板的启动器视图)条目 —— config/project_views.yaml 只读浮出。

    结构: {apps: [{id, label, icon, url}]}。缺文件/解析失败 → {apps: []}(前端空态兜底)。
    条目是手挑的常用站(长期审阅面/工作入口), 与项目注册表相互独立。
    """
    from omnicompany.core.config import omni_workspace_root

    cfg = omni_workspace_root() / "config" / "project_views.yaml"
    if not cfg.is_file():
        return {"apps": []}
    try:
        import yaml

        raw = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — 配置损坏不该 500, 退回空态
        return {"apps": []}
    apps_raw = raw.get("apps") if isinstance(raw, dict) else None
    apps: list[dict[str, Any]] = []
    for a in apps_raw or []:
        if not isinstance(a, dict):
            continue
        url = str(a.get("url") or "").strip()
        label = str(a.get("label") or "").strip()
        if not url or not label:
            continue
        apps.append({
            "id": str(a.get("id") or label),
            "label": label,
            # icon = emoji 兜底; icon_url = 指向 /api/project-assets/ 的 PNG(前端优先用它渲成图标)。
            "icon": str(a.get("icon") or "").strip() or None,
            "icon_url": str(a.get("icon_url") or "").strip() or None,
            "url": url,
        })
    return {"apps": apps}


@projects_router.get("/quests")
def get_quests(fresh: bool = False) -> dict[str, Any]:
    """任务窗口(驾驶舱主区第 2 个固定页签): 进行中项目 = 长期任务卡。

    与项目工作板同源但呈现成游戏式任务面板 — 每个项目一条长期任务, 带长期目标/
    当前章节/进行中计划子目标/近 7 天活跃/AIGC 图。fresh=1 穿透缓存。
    """
    return build_quests(fresh=fresh)


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
    team_ids: list[str] | None = None
    primary_team_id: str | None = None
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

    2026-06-12 用户: 各业务项目计划列表全错。根因之一是前端自带一份前缀匹配逻辑,
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
        import importlib
        # work_history 是隐私排除的可选服务(不进公开白名单), 动态导入
        _wh = importlib.import_module("omnicompany.packages.services._governance.work_history")
        f = _wh.latest_findings() or {}
    except Exception:  # noqa: BLE001 — 服务缺失/治理产物损坏不拖垮项目页
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


# 缩略图宽度白名单 — 前端固定这几档取图, 避免缓存文件名无限膨胀
_THUMB_WIDTHS = (320, 640, 960, 1280)

# 原图/缩略图统一走同一套缓存头: 一天内浏览器直接用旧的, 过期后台重新验证时仍可先用旧的兜底
_ASSET_CACHE_HEADERS = {"Cache-Control": "public, max-age=86400, stale-while-revalidate=604800"}


def _normalize_thumb_width(w: int) -> int:
    """把请求宽度归一到白名单档位: 取 >= w 的最小档, 超过最大档一律按最大档处理。"""
    for cand in _THUMB_WIDTHS:
        if w <= cand:
            return cand
    return _THUMB_WIDTHS[-1]


@projects_router.get("/project-assets/{filename}")
def get_project_asset(filename: str, w: int | None = Query(default=None)) -> FileResponse:
    """项目背景图等生成资产(data/registry/project_assets/)。

    w: 可选缩略图宽度(归一到 320/640/960/1280 档位)。前端首页一次拉 ~20 张卡片图,
    直接回全尺寸 PNG(1.3-1.9MB/张)是加载慢的头号原因, 这里按档位生成 WebP 缩略图
    落盘缓存(.thumbs/), 命中缓存后不再重复编码。
    同步函数是有意的 — FastAPI 对同步端点会丢进线程池执行, 避免 Pillow 编解码堵事件循环。
    """
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="非法文件名")
    f = assets_dir() / filename
    if not f.is_file():
        raise HTTPException(status_code=404, detail=f"资产不存在: {filename}")

    if w is None:
        return FileResponse(str(f), headers=_ASSET_CACHE_HEADERS)

    width = _normalize_thumb_width(w)
    thumb_dir = assets_dir() / ".thumbs"
    thumb_path = thumb_dir / f"{filename}.w{width}.webp"

    src_mtime = f.stat().st_mtime
    if not thumb_path.is_file() or thumb_path.stat().st_mtime < src_mtime:
        try:
            with Image.open(f) as im:
                if im.mode == "P":
                    im = im.convert("RGBA")
                src_w, src_h = im.size
                if src_w > width:
                    ratio = width / src_w
                    im = im.resize((width, max(1, round(src_h * ratio))), Image.LANCZOS)
                thumb_dir.mkdir(parents=True, exist_ok=True)
                # 先写临时文件再原子替换: 首载时同一张图会有并发请求, 直接写目标路径
                # 会让别的请求读到半成品 WebP。
                tmp_path = thumb_path.with_suffix(f".tmp{os.getpid()}")
                im.save(tmp_path, format="WEBP", quality=82)
                os.replace(tmp_path, thumb_path)
        except Exception:  # noqa: BLE001 — 源图损坏/格式不支持时回退原图, 不 500
            return FileResponse(str(f), headers=_ASSET_CACHE_HEADERS)

    # media_type 显式给: Windows 的 mimetypes 注册表不认 .webp, 会猜成 text/plain。
    return FileResponse(str(thumb_path), media_type="image/webp", headers=_ASSET_CACHE_HEADERS)
