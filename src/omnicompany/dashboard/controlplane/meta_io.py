# [OMNI] origin=ai-ide domain=dashboard ts=2026-05-02T06:00:00Z type=router status=active agent=ai-ide-current
# [OMNI] summary="dashboard 元 IO API - 元 IO 列表 / 详情 + 跟 tool 联动 (consumed/produced)"
# [OMNI] why="web 端能看元 IO 注册表 + 一份资源(如 fs.read_file_text) 跟哪些 tool 关联. 跟 G2 catalogue 同源"
# [OMNI] tags=dashboard,api,meta_io,read-only
# [OMNI] material_id="material:dashboard.meta_io.read_api.implementation.py"
"""dashboard 元 IO API.

只读层. 写 (注册新元 IO) 走业务包代码 + omni register --kind=meta_io CLI.

接口:
  GET /api/v2/meta_io                         列已注册元 IO (支持 kind / target_type filter)
  GET /api/v2/meta_io/{meta_io_id}            单条详情
  GET /api/v2/meta_io/{meta_io_id}/consumers  哪些 tool 声明消费这条元 IO (待 tool 端补)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query


meta_io_router = APIRouter()


@meta_io_router.get("/meta_io")
def list_meta_io_api(
    kind: str | None = Query(None, pattern="^(read|write|mutate)$"),
    target_type: str | None = Query(None),
    limit: int = Query(200, ge=1, le=2000),
) -> dict[str, Any]:
    """列已注册元 IO."""
    from omnicompany.packages.services._core.meta_io import list_meta_io
    items = list_meta_io(kind=kind, target_type=target_type)[:limit]
    return {
        "items": [
            {
                "id": m.id,
                "kind": m.kind.value,
                "target_type": m.target_type,
                "side_effect_scope": m.side_effect_scope,
                "is_atomic_semantic": m.is_atomic_semantic,
                "description": m.description,
                "tags": list(m.tags),
            }
            for m in items
        ],
        "total": len(items),
    }


@meta_io_router.get("/meta_io/{meta_io_id}")
def get_meta_io_api(meta_io_id: str) -> dict[str, Any]:
    """单条元 IO 详情 (含 state_check)."""
    from omnicompany.packages.services._core.meta_io import get_meta_io
    m = get_meta_io(meta_io_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"meta_io {meta_io_id!r} 未注册")
    return {
        "id": m.id,
        "kind": m.kind.value,
        "target_type": m.target_type,
        "side_effect_scope": m.side_effect_scope,
        "is_atomic_semantic": m.is_atomic_semantic,
        "description": m.description,
        "tags": list(m.tags),
        "state_check": {
            "precondition": m.state_check.precondition,
            "postcondition": m.state_check.postcondition,
            "invariant": m.state_check.invariant,
        },
    }
