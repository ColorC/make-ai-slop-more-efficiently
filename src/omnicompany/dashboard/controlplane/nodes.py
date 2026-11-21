# [OMNI] origin=ai-ide ts=2026-05-09 type=infra
# [OMNI] material_id="material:dashboard.controlplane.nodes.semantic_node_endpoints.py"
"""controlplane/nodes.py — route_graph + semantic_nodes 端点.

URL 不变:
    GET   /api/nodes                       route_graph.db.route_nodes 列表
    GET   /api/v2/nodes                    semantic_nodes 列表 (active filter)
    GET   /api/v2/node/{node_id}           节点详情 + 最近 spans
    PATCH /api/v2/node/{node_id}           编辑节点字段
    GET   /api/v2/node-detail/{node_id}    节点完整 metadata (跟 v2/node 区别: 严格匹配 id)
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ._db_helpers import db_paths, fetch_route_nodes, row_to_dict, sem_db

nodes_router = APIRouter(tags=["nodes"])


@nodes_router.get("/nodes")
def api_nodes():
    paths = db_paths()
    return fetch_route_nodes(paths["route_graph"])


@nodes_router.get("/v2/nodes")
def api_nodes_v2(
    active: int | None = None,
    limit: int = Query(100, le=500),
):
    """节点列表, 支持筛选."""
    conn = sem_db()
    if not conn:
        return []
    try:
        q = "SELECT * FROM semantic_nodes WHERE 1=1"
        params: list[Any] = []
        if active is not None:
            q += " AND active=?"
            params.append(active)
        q += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(q, params).fetchall()
        return [row_to_dict(r) for r in rows]
    finally:
        conn.close()


@nodes_router.get("/v2/node/{node_id}")
def api_node_with_spans(node_id: str, spans_limit: int = 5):
    """单节点详情 + 最近调用 signal_spans."""
    conn = sem_db()
    if not conn:
        return {}
    try:
        row = conn.execute(
            "SELECT * FROM semantic_nodes WHERE node_id LIKE ?", (node_id + "%",)
        ).fetchone()
        if not row:
            return {"error": "not found"}
        result = row_to_dict(row)
        has_ss = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='signal_spans'"
        ).fetchone()
        if has_ss:
            spans = conn.execute(
                "SELECT * FROM signal_spans WHERE node_id=? ORDER BY id DESC LIMIT ?",
                (row["node_id"], spans_limit),
            ).fetchall()
            result["recent_spans"] = [row_to_dict(s) for s in spans]
        return result
    finally:
        conn.close()


class NodePatch(BaseModel):
    description: str | None = None
    processing_prompt: str | None = None
    input_types: str | None = None   # JSON string
    output_types: str | None = None  # JSON string
    active: int | None = None        # 0 or 1


@nodes_router.patch("/v2/node/{node_id}")
def api_node_patch(node_id: str, patch: NodePatch):
    """在线编辑节点字段."""
    conn = sem_db()
    if not conn:
        raise HTTPException(status_code=503, detail="semantic_network.db unavailable")
    try:
        row = conn.execute(
            "SELECT node_id FROM semantic_nodes WHERE node_id LIKE ?", (node_id + "%",)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="node not found")
        real_id = row["node_id"]

        fields: list[str] = []
        values: list[Any] = []

        if patch.description is not None:
            fields.append("description=?")
            values.append(patch.description[:500])
        if patch.processing_prompt is not None:
            fields.append("processing_prompt=?")
            values.append(patch.processing_prompt)
        if patch.input_types is not None:
            try:
                json.loads(patch.input_types)
            except Exception:
                raise HTTPException(status_code=400, detail="input_types must be valid JSON")
            fields.append("input_types=?")
            values.append(patch.input_types)
        if patch.output_types is not None:
            try:
                json.loads(patch.output_types)
            except Exception:
                raise HTTPException(status_code=400, detail="output_types must be valid JSON")
            fields.append("output_types=?")
            values.append(patch.output_types)
        if patch.active is not None:
            fields.append("active=?")
            values.append(1 if patch.active else 0)

        if not fields:
            return {"ok": True, "node_id": real_id, "updated": 0}

        values.append(real_id)
        conn.execute(f"UPDATE semantic_nodes SET {', '.join(fields)} WHERE node_id=?", values)
        conn.commit()
        return {"ok": True, "node_id": real_id, "updated": len(fields)}
    finally:
        conn.close()


@nodes_router.get("/v2/node-detail/{node_id}")
def api_node_detail_strict(node_id: str):
    """Full metadata for a single semantic node (严格匹配 node_id, 不 LIKE)."""
    conn = sem_db()
    if not conn:
        return {}
    try:
        row = conn.execute(
            "SELECT * FROM semantic_nodes WHERE node_id=?", (node_id,)
        ).fetchone()
        if not row:
            return {}
        return row_to_dict(row)
    finally:
        conn.close()
