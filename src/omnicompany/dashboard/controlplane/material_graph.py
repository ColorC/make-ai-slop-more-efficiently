# [OMNI] origin=ai-ide domain=dashboard ts=2026-06-27T00:00:00Z type=router status=active
# [OMNI] summary="dashboard material-graph 只读 API:把决策库投影成 material-centric 探索 DAG 给前端。带 source_token 缓存,写归 CLI。"
# [OMNI] why="探索路径可视化前端需要图数据;图=真本体投影。权威=plans/[2026-06-27]EXPLORATION-PATH-VIZ B2。"
# [OMNI] tags=dashboard,decisions,exploration,material-graph,readonly
"""dashboard material-graph API —— 只读层,写归 CLI(omni decisions)。

端点(前缀 /api/v2 由 app.py 路由表统一加):
  GET /material-graph?project=&kind=&include_deleted=  → 投影出的 {nodes,edges,roots,...}
  GET /material-graph/projects                          → 各项目节点计数(前端筛选用)
  GET /material-graph/record/{record_id}                → 单条决策 record 全文(下钻用)
"""

from __future__ import annotations

import collections
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from omnicompany.packages.domains.decisions import library
from omnicompany.packages.domains.decisions.exploration import projection

material_graph_router = APIRouter()

# (project, kinds, include_deleted) → (source_token, graph);source_token 变了即失效重投
_GRAPH_CACHE: dict[tuple, tuple[str, dict]] = {}


@material_graph_router.get("/material-graph")
def get_material_graph(
    project: Optional[str] = Query(None, description="按项目过滤,如 aigc"),
    kind: Optional[list[str]] = Query(None, description="按 record_kind 过滤:decision/belief/comment"),
    include_deleted: bool = Query(True, description="是否含墓碑节点"),
    status: Optional[str] = Query(None, description="按记录 status 过滤,逗号分隔(如 adopted 只看已拍板)"),
) -> dict[str, Any]:
    """投影出探索 DAG。mtime/行数 token 缓存,真库变了自动重投。"""
    kinds = list(kind) if isinstance(kind, list) else None
    statuses = [s.strip() for s in status.split(",") if s.strip()] if status else None
    key = (project, tuple(kinds or ()), include_deleted, tuple(statuses or ()))
    token = projection.source_token()
    cached = _GRAPH_CACHE.get(key)
    if cached and cached[0] == token:
        return cached[1]
    graph = projection.build_graph(
        project=project, kinds=kinds, include_deleted=include_deleted, statuses=statuses,
    )
    if len(_GRAPH_CACHE) > 64:        # 上限,防按 project query 无界增长(R2)
        _GRAPH_CACHE.clear()
    _GRAPH_CACHE[key] = (token, graph)
    return graph


@material_graph_router.get("/material-graph/projects")
def get_material_graph_projects() -> dict[str, Any]:
    """各项目的节点计数 + 总数,供前端项目筛选下拉。"""
    counter: collections.Counter = collections.Counter()
    total = 0
    for rec in library.fold().values():
        if rec.get("status") == "deleted":
            continue
        counter[rec.get("project") or "(未归位)"] += 1
        total += 1
    projects = [{"project": p, "count": n} for p, n in counter.most_common()]
    return {"projects": projects, "total": total}


@material_graph_router.get("/material-graph/record/{record_id}")
def get_material_graph_record(record_id: str) -> dict[str, Any]:
    """单条决策 record 的全文(含 decision_space/anchor/links/origin),供节点下钻。

    注:墓碑(status=deleted)也返回全文(200)而非 404 —— 图里墓碑节点可下钻看内容是有意设计。
    """
    rec = library.fold().get(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail=f"record not found: {record_id}")
    return rec


@material_graph_router.get("/material-graph/conversation")
def get_material_graph_conversation(
    session_ref: str = Query(..., description="来源会话(截断 8 位或全 UUID)"),
    max_chars: int = Query(4000, ge=200, le=20000),
) -> dict[str, Any]:
    """按 session_ref 拉该会话原文片段,供下钻面板『查看更多原文』(F3 必要时拉更多)。"""
    from omnicompany.packages.domains.decisions.exploration import causal_extract
    from omnicompany.packages.domains.decisions.sources import conversation as cv

    path, sid = causal_extract._resolve_path(session_ref, causal_extract._session_path_map())
    if not path:
        raise HTTPException(status_code=404, detail=f"session not resolvable / ambiguous: {session_ref}")
    text = cv.condense_text(path)
    full_chars = len(text)
    truncated = full_chars > max_chars
    if truncated:
        text = text[:max_chars] + "\n…(更多略)…"
    return {"session_ref": sid, "text": text,
            "chars": max_chars if truncated else full_chars,   # 截断时报截断长度,不含省略号后缀
            "full_chars": full_chars, "truncated": truncated}


@material_graph_router.get("/material-graph/narrative")
def get_narrative(
    mode: str = Query("project", description="project(A 单领域) / period(B 时期全景)"),
    project: Optional[str] = Query(None, description="A 模式的领域,如 aigc"),
) -> dict[str, Any]:
    """读已提炼的探索历程(连续操作流 + 主题泳道)。未提炼则返回提示,提炼走 CLI(LLM 慢,不在 API 里跑)。"""
    from omnicompany.packages.domains.decisions.exploration import narrative

    d = narrative.read_narrative(mode, project)
    if d is None:
        hint = f"omni decisions narrative --mode {mode}" + (f" --project {project}" if project else "")
        return {"lanes": [], "events": [], "extracted": False, "hint": hint}
    return {**d, "extracted": True}


@material_graph_router.get("/material-graph/narrative/list")
def list_narrative() -> dict[str, Any]:
    """已提炼历程清单(供前端列可选项)。"""
    from omnicompany.packages.domains.decisions.exploration import narrative

    return {"narratives": narrative.list_narratives()}
