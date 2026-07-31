# [OMNI] origin=claude-code ts=2026-05-01 type=infra
# [OMNI] material_id="material:dashboard.worker_catalogue.browser_api.py"
"""Workers catalogue API — scans `src/omnicompany/packages/**/workers/*.py`.

The "system" Activity Bar tab in the new shell (WEB-FOUNDATION) treats each
worker as a first-class entity. This module exposes:
- GET /api/workers              list all workers
- GET /api/workers/{id:path}    one worker (design.md + source excerpt)

Worker `id` is the path under packages/ without `.py`, slash-separated, e.g.
`domains/voxelcraft/block/workers/block_designer`.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

workers_router = APIRouter()


def _packages_root() -> Path:
    return Path(__file__).resolve().parents[2] / "packages"


@lru_cache(maxsize=1)
def _scan_cached(mtime_token: float) -> list[dict[str, Any]]:
    root = _packages_root()
    items: list[dict[str, Any]] = []
    if not root.is_dir():
        return items
    for path in root.rglob("workers/*.py"):
        if path.name == "__init__.py":
            continue
        rel = path.relative_to(root).with_suffix("")
        rel_str = str(rel).replace(os.sep, "/")
        pkg = "/".join(rel_str.split("/")[:-2])
        name = rel.stem
        design_candidate = path.parent.parent / "DESIGN.md"
        items.append({
            "id": rel_str,
            "name": name,
            "package": pkg,
            "file_path": str(path),
            "has_design_md": design_candidate.is_file(),
        })
    items.sort(key=lambda x: x["id"])
    return items


def _scan() -> list[dict[str, Any]]:
    root = _packages_root()
    token = root.stat().st_mtime if root.exists() else 0.0
    return _scan_cached(token)


@workers_router.get("/workers")
def list_workers() -> dict[str, Any]:
    items = _scan()
    return {"items": items, "total": len(items)}


@workers_router.get("/workers/{worker_id:path}/traces")
def get_worker_traces(worker_id: str, limit: int = 30) -> dict[str, Any]:
    """List traces where this worker appears (matched by source/payload).

    Source field in events DB is short like 'block_designer' (worker leaf name),
    not full path. We match by leaf name.
    """
    import sqlite3
    from omnicompany.core.config import resolve_unified_db_path

    leaf = worker_id.rsplit("/", 1)[-1]
    items: list[dict[str, Any]] = []
    for basename in ("events.db", "ide_events.db"):
        db = resolve_unified_db_path(basename)
        if not db.is_file():
            continue
        try:
            conn = sqlite3.connect(str(db), timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """
                    SELECT trace_id,
                           MIN(timestamp) as started_at,
                           MAX(timestamp) as ended_at,
                           COUNT(*) as event_count
                    FROM events
                    WHERE source = ? OR data LIKE ?
                    GROUP BY trace_id
                    ORDER BY started_at DESC
                    LIMIT ?
                    """,
                    (leaf, f'%"{leaf}"%', limit),
                ).fetchall()
                for r in rows:
                    items.append({
                        "trace_id": r["trace_id"],
                        "started_at": r["started_at"],
                        "ended_at": r["ended_at"],
                        "event_count": r["event_count"],
                        "domain": basename.replace(".db", ""),
                    })
            finally:
                conn.close()
        except sqlite3.Error:
            continue
    items.sort(key=lambda x: x.get("started_at") or "", reverse=True)
    return {"items": items[:limit], "worker_leaf": leaf}


@workers_router.get("/workers/{worker_id:path}")
def get_worker(worker_id: str) -> dict[str, Any]:
    root = _packages_root()
    py_path = root / (worker_id + ".py")
    try:
        py_path.resolve().relative_to(root.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid worker id")
    if not py_path.is_file():
        raise HTTPException(status_code=404, detail=f"worker not found: {worker_id}")

    pkg_dir = py_path.parent.parent
    design_md_path = pkg_dir / "DESIGN.md"
    design_md = design_md_path.read_text(encoding="utf-8") if design_md_path.is_file() else None

    try:
        source = py_path.read_text(encoding="utf-8")
    except OSError:
        source = ""

    return {
        "id": worker_id,
        "name": py_path.stem,
        "package": str(pkg_dir.relative_to(root)).replace(os.sep, "/"),
        "file_path": str(py_path),
        "design_md_path": str(design_md_path) if design_md else None,
        "design_md": design_md,
        "source": source,
    }
