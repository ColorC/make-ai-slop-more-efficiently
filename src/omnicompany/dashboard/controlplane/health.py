# [OMNI] origin=ai-ide ts=2026-05-09 type=infra
# [OMNI] material_id="material:dashboard.controlplane.health.system_health_endpoints.py"
"""controlplane/health.py — health / marathon / guardian 端点.

URL 不变:
    GET /api/health      系统健康概览 (db files / budget / latest evolution / node count / latest guardian)
    GET /api/marathon    marathon checkpoint + budget
    GET /api/guardian    MetaGuardian audit log
"""

from __future__ import annotations

import os
import socket
import time

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


def _port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    """便宜的 TCP 探活, 不发 HTTP, 用于 healthz 快速判定子进程在不在。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@health_router.get("/healthz")
def api_healthz():
    """轻量健康探测 — 供 LOFA 安卓远程端启动判定"能不能连上本机"。

    刻意不读 DB(区别于偏重的 /api/health), 保证手机端探测快(<1s)。
    dashboard_ok 恒 true(本进程响应即说明 8210 可达); daemon/chat 走本机 TCP 探活。
    """
    daemon_port = int(os.environ.get("OMNI_CCDAEMON_PORT", "8201"))
    chat_port = int(os.environ.get("OMNI_CHATUI_PORT", "7348"))
    daemon_ok = _port_open("127.0.0.1", daemon_port)
    chat_ok = _port_open("127.0.0.1", chat_port)
    return {
        "ok": True,
        "service": "omnicompany-dashboard",
        "version": "0.3.1",
        "dashboard_ok": True,
        "daemon_ok": daemon_ok,
        "chat_ok": chat_ok,
        "ts": time.time(),
    }
