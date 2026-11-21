# [OMNI] origin=ai-ide domain=dashboard ts=2026-05-02T05:00:00Z type=router status=active agent=ai-ide-current
# [OMNI] summary="dashboard registry API - 暴露 G2 注册中心 + G1 身份联动数据"
# [OMNI] why="dashboard catalogue 走 AST 扫描看不到 G2 显式注册的内容; 这层暴露 InstanceRegistry 让 web 看到完整 8 kind 实体, 含 trace_id 联动"
# [OMNI] tags=dashboard,api,registry,read-only,G2-integration
# [OMNI] material_id="material:dashboard.registry.read_api.py"
"""dashboard 注册中心 API.

只读层 (跟 dashboard D2 严格隔离写入路径原则一致), 写仍归 CLI.

接口:
  GET /api/v2/registry/types          列已注册 kind 类型 (8 种)
  GET /api/v2/registry/instances      列所有 entity (支持 filter)
  GET /api/v2/registry/instances/{id} 单 entity 详情
  GET /api/v2/registry/by-trace/{trace_id}  按 trace_id 查 (跟 G1 联动)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from omnicompany.packages.services._core.registry import (
    get_registry, query as reg_query, meta_registry,
)


registry_router = APIRouter()


@registry_router.get("/registry/types")
def list_types() -> dict[str, Any]:
    """列已注册的 kind 类型 (验证 omnicompany 8 种齐)."""
    types = meta_registry.all_types()
    return {
        "items": [
            {
                "name": t.name,
                "display": t.display_name,
                "data_dir": t.data_dir,
                "registration_criteria": t.registration_criteria[:200] if t.registration_criteria else "",
            }
            for t in types
        ],
        "total": len(types),
    }


@registry_router.get("/registry/instances")
def list_instances(
    kind: str | None = Query(None, description="按 kind 过滤"),
    package: str | None = Query(None, description="按 package 过滤"),
    source: str = Query("all", pattern="^(all|explicit|ast_scan)$",
                        description="来源: explicit=显式注册 / ast_scan=自动扫描 / all=全部"),
    limit: int = Query(100, ge=1, le=1000),
) -> dict[str, Any]:
    """列所有 entity. 跟 omni lookup 命令同源.

    kind 别名映射: omnicompany 名 → registry 内部名:
      worker → router / material → format / team → pipeline / agent → agent_loop
    """
    alias = {"worker": "router", "material": "format", "team": "pipeline", "agent": "agent_loop"}
    type_name = alias.get(kind, kind) if kind else None

    reg = get_registry()
    q = reg_query(reg)
    if type_name:
        q = q.type(type_name)
    if package:
        q = q.package(package)

    items: list[dict[str, Any]] = []
    for entry in q.execute():
        registered_via = entry.attrs.get("registered_via", "ast_scan")
        if source == "explicit" and registered_via != "cli_explicit":
            continue
        if source == "ast_scan" and registered_via == "cli_explicit":
            continue
        items.append({
            "entity_id": entry.entity_id,
            "type": entry.type,
            "kind_omnicompany": entry.attrs.get("kind_omnicompany"),
            "name": entry.name,
            "package": entry.package,
            "source_file": entry.source_file,
            "trace_id": entry.attrs.get("trace_id"),
            "registered_via": registered_via,
            "first_seen_at": entry.first_seen_at,
            "scanned_at": entry.scanned_at,
        })
        if len(items) >= limit:
            break

    return {"items": items, "total": len(items), "source": source}


@registry_router.get("/registry/instances/{entity_id:path}")
def get_instance(entity_id: str) -> dict[str, Any]:
    """单 entity 详情. entity_id 形如 router:demogame.team_table.SchemaAssembler ."""
    reg = get_registry()
    entry = reg.read(entity_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"entity {entity_id!r} 不存在")
    return entry.to_dict()


@registry_router.get("/registry/by-trace/{trace_id}")
def list_by_trace(trace_id: str, limit: int = 100) -> dict[str, Any]:
    """按 trace_id 查 - 跟 G1 身份模块联动. 显示某 session 注册过的所有 entity."""
    reg = get_registry()
    items = []
    for entry in reg.list_all():
        if entry.attrs.get("trace_id") == trace_id:
            items.append({
                "entity_id": entry.entity_id,
                "type": entry.type,
                "kind_omnicompany": entry.attrs.get("kind_omnicompany"),
                "name": entry.name,
                "source_file": entry.source_file,
                "first_seen_at": entry.first_seen_at,
            })
            if len(items) >= limit:
                break
    return {"items": items, "trace_id": trace_id, "total": len(items)}
