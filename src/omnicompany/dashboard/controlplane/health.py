# [OMNI] origin=ai-ide ts=2026-05-09 type=infra
# [OMNI] material_id="material:dashboard.controlplane.health.system_health_endpoints.py"
"""controlplane/health.py — health / marathon / guardian 端点.

URL 不变:
    GET /api/health      系统健康概览 (db files / budget / latest evolution / node count / latest guardian)
    GET /api/marathon    marathon checkpoint + budget
    GET /api/guardian    MetaGuardian audit log
"""

from __future__ import annotations

from fastapi import APIRouter

from ._db_helpers import db_paths, read_json, read_jsonl, safe_conn

health_router = APIRouter(tags=["health"])


@health_router.get("/marathon")
def api_marathon():
    """Marathon checkpoint + budget."""
    paths = db_paths()
    checkpoint = read_json(paths["dir"] / "marathon_checkpoint.json")
    budget = read_json(paths["budget_state"])
    return {"checkpoint": checkpoint, "budget": budget}


@health_router.get("/guardian")
def api_guardian():
    """MetaGuardian audit log."""
    paths = db_paths()
    return read_jsonl(paths["meta_guardian_log"])


@health_router.get("/health")
def api_health():
    paths = db_paths()
    d = paths["dir"]
    present = {name: paths[name].is_file() for name in paths if name != "dir"}

    budget = read_json(paths["budget_state"])

    evo = read_jsonl(paths["evolution_log"])
    latest = evo[-1] if evo else None

    node_count = 0
    if paths["route_graph"].is_file():
        conn = safe_conn(paths["route_graph"])
        if conn:
            try:
                row = conn.execute("SELECT COUNT(*) FROM route_nodes").fetchone()
                node_count = int(row[0]) if row else 0
            finally:
                conn.close()

    guardian_log = read_jsonl(paths["meta_guardian_log"])
    latest_guardian = guardian_log[-1] if guardian_log else None

    return {
        "db_dir": str(d),
        "data_files_present": present,
        "budget": budget,
        "latest_evolution": latest,
        "route_node_count": node_count,
        "latest_guardian": latest_guardian,
    }
