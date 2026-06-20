# [OMNI] origin=claude-code ts=2026-05-01 type=infra
# [OMNI] material_id="material:dashboard.system_info.health_api.py"
"""System info API — version, db paths, services status."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter

system_router = APIRouter()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


@system_router.get("/system/info")
def system_info() -> dict[str, Any]:
    from omnicompany.core.config import resolve_unified_db_path
    try:
        from omnicompany._core_version import __version__
    except ImportError:
        __version__ = "unknown"

    root = _project_root()
    pkg_root = root / "src" / "omnicompany" / "packages"

    pkg_count = 0
    worker_count = 0
    if pkg_root.is_dir():
        for p in pkg_root.rglob("workers/*.py"):
            if p.name != "__init__.py":
                worker_count += 1
        pkg_count = len([p for p in pkg_root.rglob("DESIGN.md")])

    dbs = {}
    for basename in ("events.db", "ide_events.db"):
        try:
            p = resolve_unified_db_path(basename)
            dbs[basename] = {
                "path": str(p),
                "exists": p.is_file(),
                "size": p.stat().st_size if p.is_file() else 0,
            }
        except Exception as e:
            dbs[basename] = {"path": "", "exists": False, "error": str(e)}

    return {
        "version": __version__,
        "project_root": str(root),
        "packages_root": str(pkg_root),
        "stats": {
            "worker_count": worker_count,
            "package_count": pkg_count,
        },
        "databases": dbs,
        "endpoints": {
            "workers": "/api/workers",
            "notes": "/api/notes",
            "events_sse": "/api/v2/ide/events",
            "trace_list": "/api/v2/trace-list",
            "assistant_goals": "/api/v2/assistant/goals",
        },
    }
