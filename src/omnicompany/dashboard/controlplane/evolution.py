# [OMNI] origin=ai-ide ts=2026-05-09 type=infra
# [OMNI] material_id="material:dashboard.controlplane.evolution.evolution_log_endpoints.py"
"""controlplane/evolution.py — evolution log / reflections / params / semantic_types / v2/evo.

URL 不变:
    GET /api/evolution        evolution_log.jsonl
    GET /api/reflections      mutation_state.json conditional_rules
    GET /api/semantic_types   semantic_types.json (registry)
    GET /api/params           params.json + history
    GET /api/v2/evo           evolution_outcome_log (in semantic_network.db)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ._db_helpers import db_paths, read_json, read_jsonl, sem_db

evolution_router = APIRouter(tags=["evolution"])


@evolution_router.get("/evolution")
def api_evolution():
    paths = db_paths()
    return read_jsonl(paths["evolution_log"])


@evolution_router.get("/reflections")
def api_reflections():
    paths = db_paths()
    data = read_json(paths["mutation_state"])
    if not isinstance(data, dict):
        return []
    rules = data.get("conditional_rules")
    return rules if isinstance(rules, list) else []


@evolution_router.get("/semantic_types")
def api_semantic_types():
    """Semantic type registry — the evolved replacement for conditional_rules."""
    paths = db_paths()
    st_path = paths["dir"] / "semantic_types.json"
    data = read_json(st_path)
    return data if isinstance(data, list) else []


@evolution_router.get("/params")
def api_params():
    paths = db_paths()
    current = read_json(paths["params"])
    if current is None:
        current = {}
    elif not isinstance(current, dict):
        current = {"value": current}
    history = read_jsonl(paths["params_history"])
    return {"current": current, "history": history}


@evolution_router.get("/v2/evo")
def api_evo(limit: int = 20, node_id: str | None = None):
    """进化历史."""
    conn = sem_db()
    if not conn:
        return []
    try:
        has_eol = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='evolution_outcome_log'"
        ).fetchone()
        if not has_eol:
            return []
        q = "SELECT * FROM evolution_outcome_log WHERE 1=1"
        params: list[Any] = []
        if node_id:
            q += " AND target_node_id LIKE ?"
            params.append(node_id + "%")
        q += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
