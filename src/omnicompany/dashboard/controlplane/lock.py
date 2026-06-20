# [OMNI] origin=ai-ide domain=dashboard ts=2026-05-02T05:00:00Z type=router status=active agent=ai-ide-current
# [OMNI] summary="dashboard lock API - G4 锁状态只读暴露"
# [OMNI] why="web 端能看锁开关 / watched / 违规清单 / baseline. 不暴露 enable/handle 写操作 (跟 D2 只读聚合层原则一致)"
# [OMNI] tags=dashboard,api,lock,protection,read-only,G4-integration
# [OMNI] material_id="material:dashboard.lock_api.status_endpoint.py"
"""dashboard G4 锁状态 API.

只读层. 写 (enable/disable/handle/baseline 等) 仍走 CLI `omni lock ...`.

接口:
  GET /api/v2/lock/status      锁状态 (enabled / watched_paths / whitelist 数 / baseline 数)
  GET /api/v2/lock/violations  当前违规清单 (跟 omni lock scan 同源)
  GET /api/v2/lock/baseline    baseline 路径列表 (前 200 条预览)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from omnicompany.packages.services._core.protection import (
    load_policy, load_baseline, scan_violations,
)


lock_router = APIRouter()


@lock_router.get("/lock/status")
def lock_status() -> dict[str, Any]:
    """看锁状态."""
    policy = load_policy()
    baseline = load_baseline()
    return {
        "enabled": policy.get("enabled", False),
        "watched_paths": policy.get("watched_paths", []),
        "whitelist_patterns_count": len(policy.get("whitelist_patterns", [])),
        "whitelist_patterns_preview": policy.get("whitelist_patterns", [])[:5],
        "baseline_count": len(baseline),
        "version": policy.get("version", 1),
    }


@lock_router.get("/lock/violations")
def list_violations(
    classification: str | None = Query(None, pattern="^(internal_misplace|external_write)$"),
    limit: int = Query(100, ge=1, le=1000),
) -> dict[str, Any]:
    """跑一次 scan 列违规候选."""
    violations = scan_violations()
    items = []
    for v in violations:
        if classification and v.classification != classification:
            continue
        items.append(v.to_dict())
        if len(items) >= limit:
            break
    by_class = {"internal_misplace": 0, "external_write": 0}
    for v in violations:
        if v.classification in by_class:
            by_class[v.classification] += 1
    return {"items": items, "total": len(items), "by_classification": by_class}


@lock_router.get("/lock/baseline")
def list_baseline(limit: int = Query(200, ge=1, le=2000)) -> dict[str, Any]:
    """看 baseline 路径列表 (预览前 N 条)."""
    bl = sorted(load_baseline())
    return {
        "items": bl[:limit],
        "total": len(bl),
        "shown": min(len(bl), limit),
    }
