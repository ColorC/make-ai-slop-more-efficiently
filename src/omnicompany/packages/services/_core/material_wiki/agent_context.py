# [OMNI] origin=codex domain=services/_core/material_wiki ts=2026-08-02T00:00:00+08:00 type=router status=active
# [OMNI] summary="材料 Wiki Agent 开工上下文：唯一总体计划、项目 profile、当前有效材料目录和统一提交入口"
# [OMNI] why="Agent 必须先读同一权威与项目合同，再通过同一 mutation 服务提交；不能从报告或旧草稿旁路猜测当前材料"
# [OMNI] tags=material-wiki,agent,context,authority,submission
# [OMNI] material_id="material:core.material_wiki.agent_context.py"
"""Bounded, project-scoped start packet for Material Wiki agents."""

from __future__ import annotations

from collections import Counter
from typing import Any
from urllib.parse import quote

from .runtime import MaterialWikiRuntime


FACILITY_PLAN_REF = (
    "omnicompany:docs/plans/format-material/"
    "[2026-08-02]MATERIAL-WIKI-FULL-FACILITY/plan.md"
)


def _taxonomy(node: Any) -> dict[str, Any]:
    return {
        "id": node.id,
        "label": node.label,
        "allow_dynamic_children": node.allow_dynamic_children,
        "children": [_taxonomy(child) for child in node.children],
    }


def build_agent_context(
    runtime: MaterialWikiRuntime,
    project_id: str,
    *,
    offset: int = 0,
    limit: int = 50,
    include_terminal: bool = False,
) -> dict[str, Any]:
    """Return one bounded packet without copying profile or material authority."""
    if offset < 0:
        raise ValueError("offset 不能小于 0")
    if limit < 1 or limit > 500:
        raise ValueError("limit 必须在 1 到 500 之间")
    profile = runtime.profile(project_id)
    registration = runtime.registration(project_id)
    status_by_id = {item.id: item for item in profile.statuses}
    materials: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    for _, document in runtime.documents(project_id):
        version = document.current or document.head
        status = status_by_id.get(version.status)
        if not include_terminal and (document.current is None or (status and status.terminal)):
            continue
        status_counts[version.status] += 1
        encoded_project = quote(project_id, safe="")
        encoded_id = quote(document.material_id, safe="")
        materials.append(
            {
                "material_id": document.material_id,
                "title": document.title,
                "placement_path": list(document.placement_path),
                "version": version.version,
                "status": version.status,
                "canonical_ref": document.canonical_ref,
                "preview": version.body[:240],
                "get_api": (
                    "/api/boss-sight/material-wiki/projects/"
                    f"{encoded_project}/materials/{encoded_id}"
                ),
            }
        )
    materials.sort(key=lambda item: (item["placement_path"], item["title"], item["material_id"]))
    page = materials[offset : offset + limit]
    return {
        "schema_version": 1,
        "authority": {
            "overall_plan_ref": registration.overall_plan_ref or FACILITY_PLAN_REF,
            "profile_ref": registration.source.ref,
            "rule": "报告只聚合并引用本体；旧来源只供追溯；可复用内容必须经统一提交进入本体。",
        },
        "project": {
            "project_id": project_id,
            "profile_id": profile.profile_id,
            "taxonomy": [_taxonomy(node) for node in profile.taxonomy],
            "statuses": [
                {
                    "id": item.id,
                    "label": item.label,
                    "current": item.current,
                    "approved": item.approved,
                    "terminal": item.terminal,
                }
                for item in profile.statuses
            ],
            "default_status": profile.default_status,
            "locked_depth": profile.locked_depth,
            "dynamic_from_depth": profile.dynamic_from_depth,
            "max_depth": profile.max_depth,
        },
        "current_materials": {
            "total": len(materials),
            "offset": offset,
            "limit": limit,
            "returned": len(page),
            "has_more": offset + len(page) < len(materials),
            "status_counts": dict(sorted(status_counts.items())),
            "items": page,
        },
        "submission": {
            "api_base": f"/api/boss-sight/material-wiki/projects/{quote(project_id, safe='')}",
            "cli": f"omni material wiki submit {project_id} --payload <request.json>",
            "operations": [
                "create",
                "revise",
                "derive",
                "supersede",
                "reject",
                "restore",
                "link",
            ],
            "required_semantics": {
                "placement": "project/profile + 合法 placement_path；二级类型只在 profile 允许时动态建立",
                "source": "至少一个可追溯 source；系统自动补 author_session 与时间",
                "version": "稳定 material_id、显式状态、expected_version 与幂等键",
                "citation": "成功回执返回 canonical_ref；下游计划和报告只保存该引用",
            },
            "preflight": "所有 mutation 可先 dry_run=true；同一请求去掉 dry_run 后正式写本体。",
        },
    }


__all__ = ["FACILITY_PLAN_REF", "build_agent_context"]
