# [OMNI] origin=claude-code domain=narrative_studio ts=2026-07-05T00:00:00Z type=module status=active
"""HTTP API(FastAPI):叙事内容引擎的唯一对外面(网页壳已退役, 见 README 顶注)。

薄层:加载/保存单一真源 Project,转调 projections/health/playthrough/queries,
做实体 CRUD(带项目级修订快照),其余逻辑都在纯库里。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import health as health_mod
from . import ops as ops_mod
from . import playthrough as pt_mod
from . import projections as proj
from . import queries as q_mod
from . import provenance as _provenance
from . import storage
from . import wiki_sync
from .importer import import_vilo
from .models import GameText, Project

# --------------------------------------------------------------------------- #
# 配置 + 状态
# --------------------------------------------------------------------------- #
_log = logging.getLogger("narrative_studio.api")
_PKG_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PKG_DIR.parents[3]  # .../omnicompany
PROJECTS_ROOT = Path(os.environ.get(
    "NARRATIVE_STUDIO_PROJECTS",
    _REPO_ROOT / "data" / "narrative_studio" / "projects",
))
ACTIVE_PROJECT = os.environ.get("NARRATIVE_STUDIO_PROJECT", "vilo")
# vilo 仓库根(当前有效设计在其 wiki/;importer 读 wiki/00 + cards/events)
VILO_REPO = Path(os.environ.get(
    "NARRATIVE_STUDIO_VILO_REPO",
    "E:/WindowsWorkspace/故事/vilo-wants-to-know",
))

# 单对象载体 / 列表载体 / id 字段
_SINGLETONS = {"premise", "arc", "meta", "meta_progress", "audience", "background"}
_LIST_CARRIERS = {
    "reveal_layers", "world", "characters", "relationships",
    "variables", "stat_blocks", "pressures", "failure_levels",
    "beats", "storylines", "pacing",
    "nodes", "connections", "endings",
    "scenes", "prose_lines", "voices", "registers", "style_matrix",
    "tags", "notes",
    "game_texts", "rejected_archive", "comments",
}


def _active_root() -> Path:
    return PROJECTS_ROOT / ACTIVE_PROJECT


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")


# 内存中的当前 project(懒加载;若磁盘无则从 vilo 导入并落盘)
_project: Optional[Project] = None


def _import_vilo_recorded() -> Project:
    """import_vilo + 跨边界导入留痕(仅 vilo 项目留痕;失败不阻断导入)。"""
    p = import_vilo(VILO_REPO)
    if ACTIVE_PROJECT == "vilo":
        _provenance.record_import(
            vilo_repo=str(VILO_REPO),
            game_texts=len(p.game_texts),
            characters=len(p.characters),
            rejected=len(p.rejected_archive),
        )
    return p


def get_project() -> Project:
    global _project
    if _project is None:
        root = _active_root()
        if storage.project_exists(root):
            _project = storage.load_project(root)
        else:
            _project = _import_vilo_recorded()
            storage.save_project(_project, root)
    return _project


def _persist(p: Project, *, history: bool = True) -> None:
    """落盘当前 project;history=True 时先把旧状态快照进 .history/。"""
    global _project
    root = _active_root()
    if history:
        storage.snapshot(root, _now())
    storage.save_project(p, root)
    _project = p


def _dump(p: Project) -> Dict[str, Any]:
    return p.model_dump(by_alias=True, mode="json")


# --------------------------------------------------------------------------- #
# 实体 CRUD 工具
# --------------------------------------------------------------------------- #
def _entity_id(carrier: str, item: Any) -> Optional[str]:
    if isinstance(item, dict):
        if carrier == "endings":
            return item.get("node_id")
        if carrier == "variables":
            return f"{item.get('namespace')}.{item.get('name')}"
        if carrier == "stat_blocks":
            return item.get("name")
        if carrier == "registers" or carrier == "voices":
            return item.get("id")
        if carrier == "style_matrix":
            return None  # 无自然 id,走 list-replace
        if carrier == "pacing":
            return f"{item.get('kind')}:{item.get('name')}"
        if carrier == "failure_levels":
            return item.get("level")
        return item.get("id")
    return None


def _find_index(lst: List[dict], carrier: str, ent_id: str) -> int:
    for i, it in enumerate(lst):
        if _entity_id(carrier, it) == ent_id:
            return i
    return -1


def _sync_game_text_to_wiki(payload: Dict[str, Any], *, delete: bool = False) -> Optional[str]:
    """落地层(game_texts)写回 vilo wiki(取舍 1A:wiki 是游戏内容单一真源)。

    仅 vilo 项目生效;其它项目无 wiki,跳过。返回写入的相对路径。
    跨边界写回成功后留痕一条进统一账本(见 provenance.record_wiki_writeback;失败不阻断)。
    """
    if ACTIVE_PROJECT != "vilo":
        return None
    try:
        gt = GameText.model_validate(payload)
    except Exception as e:  # 不静默:校验失败要记日志(尤其删除会留孤儿)
        _log.warning("game_text 校验失败,跳过 wiki 同步(delete=%s): %s", delete, e)
        return None
    try:
        if delete:
            wiki_sync.delete_game_text(VILO_REPO, gt)
            _provenance.record_wiki_writeback(
                text_id=gt.id, text_type=gt.text_type, wiki_relpath=None, deleted=True)
            return None
        relpath = wiki_sync.write_game_text(VILO_REPO, gt)
        _provenance.record_wiki_writeback(
            text_id=gt.id, text_type=gt.text_type, wiki_relpath=relpath, deleted=False)
        return relpath
    except Exception as e:
        _log.error("wiki 写回/删除失败(delete=%s, id=%s): %s", delete, getattr(gt, "id", None), e)
        return None


# --------------------------------------------------------------------------- #
# FastAPI app
# --------------------------------------------------------------------------- #
app = FastAPI(title="Narrative Studio", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

api = FastAPI()  # 子应用挂 /api,便于和静态分离


@api.get("/project")
def r_get_project():
    return _dump(get_project())


@api.put("/project")
def r_put_project(payload: Dict[str, Any]):
    p = Project.model_validate(payload)
    _persist(p)
    return {"ok": True}


@api.put("/list/{carrier}")
def r_replace_list(carrier: str, payload: List[Dict[str, Any]]):
    if carrier not in _LIST_CARRIERS:
        raise HTTPException(400, f"未知列表载体 {carrier}")
    p = get_project()
    data = _dump(p)
    data[carrier] = payload
    np = Project.model_validate(data)
    _persist(np)
    return {"ok": True, "count": len(payload)}


@api.put("/entity/{carrier}/{ent_id}")
def r_update_entity(carrier: str, ent_id: str, payload: Dict[str, Any]):
    p = get_project()
    data = _dump(p)
    if carrier in _SINGLETONS:
        data[carrier] = payload
    elif carrier in _LIST_CARRIERS:
        lst = data.get(carrier, [])
        idx = _find_index(lst, carrier, ent_id)
        if idx < 0:
            raise HTTPException(404, f"{carrier}/{ent_id} 未找到")
        lst[idx] = payload
        data[carrier] = lst
    else:
        raise HTTPException(400, f"未知载体 {carrier}")
    np = Project.model_validate(data)
    _persist(np)
    wiki_path = _sync_game_text_to_wiki(payload) if carrier == "game_texts" else None
    return {"ok": True, "entity": payload, "wiki_path": wiki_path}


@api.post("/entity/{carrier}")
def r_create_entity(carrier: str, payload: Dict[str, Any]):
    if carrier not in _LIST_CARRIERS:
        raise HTTPException(400, f"{carrier} 不是列表载体")
    p = get_project()
    data = _dump(p)
    data.setdefault(carrier, []).append(payload)
    np = Project.model_validate(data)
    _persist(np)
    wiki_path = _sync_game_text_to_wiki(payload) if carrier == "game_texts" else None
    return {"ok": True, "entity": payload, "wiki_path": wiki_path}


@api.delete("/entity/{carrier}/{ent_id}")
def r_delete_entity(carrier: str, ent_id: str):
    if carrier not in _LIST_CARRIERS:
        raise HTTPException(400, f"{carrier} 不是可删除的列表载体")
    p = get_project()
    data = _dump(p)
    lst = data.get(carrier, [])
    idx = _find_index(lst, carrier, ent_id)
    if idx < 0:
        raise HTTPException(404, f"{carrier}/{ent_id} 未找到")
    removed = lst.pop(idx)
    data[carrier] = lst
    np = Project.model_validate(data)
    _persist(np)
    if carrier == "game_texts":
        _sync_game_text_to_wiki(removed, delete=True)
    return {"ok": True}


# --- 投影 ---
@api.get("/projections/timeline")
def r_timeline():
    return proj.timeline(get_project())


@api.get("/projections/outline")
def r_outline():
    return proj.outline(get_project())


@api.get("/projections/route-graph")
def r_route_graph():
    return proj.route_graph(get_project())


@api.get("/projections/relationship-graph")
def r_rel_graph():
    return proj.relationship_graph(get_project())


@api.get("/projections/character-scenes")
def r_char_scenes(char_id: str):
    return proj.character_scenes(get_project(), char_id)


@api.get("/projections/variable-refs")
def r_var_refs(var_key: str):
    return proj.variable_refs(get_project(), var_key)


@api.get("/projections/tag-occurrences")
def r_tag_occ(tag_id: str):
    return proj.tag_occurrences(get_project(), tag_id)


@api.get("/projections/idea-alignment")
def r_idea(idea: str):
    return proj.idea_alignment(get_project(), idea)


@api.get("/projections/drilldown")
def r_drill(scene_id: str):
    return proj.drilldown(get_project(), scene_id)


@api.get("/projections/distribution")
def r_dist():
    return proj.distribution(get_project())


@api.get("/projections/provenance-forward")
def r_prov_fwd(source: str):
    return proj.provenance_forward(get_project(), source)


# --- 健康 / 完成度 / 搜索 / 演练 ---
@api.get("/health")
def r_health():
    return health_mod.health_check(get_project())


@api.get("/completeness")
def r_completeness():
    return q_mod.completeness(get_project())


@api.get("/empties")
def r_empties():
    return q_mod.empties(get_project())


@api.get("/search")
def r_search(q: str = ""):
    return q_mod.search(get_project(), q)


class PlaythroughReq(BaseModel):
    choices: List[Any] = []
    start: Optional[str] = None


@api.post("/playthrough")
def r_playthrough(req: PlaythroughReq):
    return pt_mod.playthrough(get_project(), choices=req.choices, start=req.start)


# --- 修订历史 ---
@api.get("/history")
def r_history():
    return storage.list_history(_active_root())


@api.post("/history/restore")
def r_restore(payload: Dict[str, str]):
    ts = payload.get("ts", "")
    root = _active_root()
    storage.snapshot(root, _now())  # 还原前也快照当前,可回退
    if not storage.restore(root, ts):
        raise HTTPException(404, f"快照 {ts} 不存在")
    global _project
    _project = storage.load_project(root)
    return {"ok": True}


# --- 重新从讨论稿导入(重置 vilo,危险操作,显式调用) ---
@api.post("/reimport-vilo")
def r_reimport():
    p = _import_vilo_recorded()
    _persist(p)
    return {"ok": True}


# --- 全局查找替换 ---
class ReplaceReq(BaseModel):
    find: str
    replace: str = ""
    dry_run: bool = False


@api.post("/replace")
def r_replace(req: ReplaceReq):
    res = ops_mod.text_replace(get_project(), req.find, req.replace, dry_run=req.dry_run)
    if not req.dry_run and res.get("project") is not None:
        _persist(Project.model_validate(res["project"]))
    return {"count": res["count"], "hits": res["hits"]}


# --- 场景拆分 / 合并 ---
class SplitReq(BaseModel):
    at: int = 1


@api.post("/scene/{scene_id}/split")
def r_scene_split(scene_id: str, req: SplitReq):
    data, warnings = ops_mod.scene_split(get_project(), scene_id, at=req.at)
    _persist(Project.model_validate(data))
    return {"ok": True, "warnings": warnings}


class MergeReq(BaseModel):
    a: str
    b: str


@api.post("/scene/merge")
def r_scene_merge(req: MergeReq):
    data, warnings = ops_mod.scene_merge(get_project(), req.a, req.b)
    _persist(Project.model_validate(data))
    return {"ok": True, "warnings": warnings}


# --- 批量改字段/状态 ---
class BatchReq(BaseModel):
    carrier: str
    ids: List[str]
    patch: Dict[str, Any]


@api.post("/batch-update")
def r_batch_update(req: BatchReq):
    if req.carrier not in _LIST_CARRIERS:
        raise HTTPException(400, f"{req.carrier} 不是列表载体")
    p = get_project()
    data = _dump(p)
    lst = data.get(req.carrier, [])
    idset = set(req.ids)
    n = 0
    for it in lst:
        if _entity_id(req.carrier, it) in idset:
            it.update(req.patch)
            n += 1
    data[req.carrier] = lst
    _persist(Project.model_validate(data))
    return {"ok": True, "updated": n}


# --- 具名版本 + 对照 diff ---
@api.get("/versions")
def r_versions():
    return storage.list_versions(_active_root())


class VersionReq(BaseModel):
    name: str


@api.post("/versions/save")
def r_version_save(req: VersionReq):
    storage.save_version(_active_root(), req.name)
    return {"ok": True, "versions": storage.list_versions(_active_root())}


@api.post("/versions/activate")
def r_version_activate(req: VersionReq):
    root = _active_root()
    storage.snapshot(root, _now())  # 切换前留底,可经 history 回退
    if not storage.activate_version(root, req.name):
        raise HTTPException(404, f"版本 {req.name} 不存在")
    global _project
    _project = storage.load_project(root)
    return {"ok": True}


def _project_for(name: str) -> Optional[Project]:
    """版本名 → Project;'_working'/空 取当前工作态。"""
    if not name or name == "_working":
        return get_project()
    return storage.load_version(_active_root(), name)


@api.get("/diff")
def r_diff(a: str = "_working", b: str = "_working"):
    pa, pb = _project_for(a), _project_for(b)
    if pa is None or pb is None:
        raise HTTPException(404, "版本不存在")
    return ops_mod.project_diff(pa, pb)


# --- 草稿转正式(落地层:从 wiki/drafts 移到 wiki/cards|events)---
class PromoteReq(BaseModel):
    id: str


@api.post("/draft/promote")
def r_draft_promote(req: PromoteReq):
    p = get_project()
    data = _dump(p)
    lst = data.get("game_texts", [])
    idx = _find_index(lst, "game_texts", req.id)
    if idx < 0:
        raise HTTPException(404, f"game_text {req.id} 未找到")
    gt = dict(lst[idx])
    _sync_game_text_to_wiki(gt, delete=True)   # 删旧草稿文件
    gt["is_draft"] = False
    gt["provenance"] = None                    # 清来源 → 写正式路径
    lst[idx] = gt
    data["game_texts"] = lst
    np = Project.model_validate(data)
    _persist(np)
    wiki_path = _sync_game_text_to_wiki(gt)     # 写到正式 cards/events
    return {"ok": True, "wiki_path": wiki_path}


app.mount("/api", api)


# --------------------------------------------------------------------------- #
# 前端静态托管已退役(统一设计工作室 v2 四期 D8, DEC-2026-07-05-025/030):
# 用户侧浏览/审阅动线 = 驾驶舱阅读视图(材料展示框架的叙事展示区), 本服务只剩内容引擎 API。
# webui/ 源码留档不删(渲染器"画法"提取的出处), 不再构建、不再托管。
# --------------------------------------------------------------------------- #
@app.get("/")
def retired_notice():
    return JSONResponse({
        "msg": "叙事工作室网页壳已退役(统一设计工作室 v2): 浏览/审阅走驾驶舱阅读视图(项目 vilo → 阅读视图), 内容引擎 API 永久保留。",
        "api": "/api/project",
        "successor": "dashboard(:8210) → 项目 vilo → 阅读视图(studio_reader)",
    })
