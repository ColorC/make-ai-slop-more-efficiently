"""Durable background queue for Reviewstage cover generation.

Submission only persists a tiny JSON job; Playwright work happens in the
Dashboard process.  Jobs survive restarts, are deduplicated by material
version, and use bounded exponential retry.  Cover generation is deliberately
best-effort: a preview failure must never roll back an otherwise valid material.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from . import preview

_log = logging.getLogger(__name__)

QUEUE_DIRNAME = "preview_queue"
MAX_ATTEMPTS = 8
DEFAULT_BATCH_SIZE = 3
_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]+")


def queue_dir(store_root: Path | str) -> Path:
    path = Path(store_root) / QUEUE_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _job_path(store_root: Path | str, material_id: str) -> Path:
    safe = _SAFE_ID.sub("_", material_id).strip("._") or "material"
    return queue_dir(store_root) / f"{safe}.json"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _read_job(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        _log.exception("Invalid preview queue job: %s", path)
        return None


def _cover_complete(material: Any, store_root: Path | str) -> bool:
    root = Path(store_root)
    ver = preview._ver(material)
    cover = preview.cover_path(root, material.id, ver)
    if not (cover.exists() and cover.stat().st_size > 0):
        return False
    if preview._kind(material) in preview.VIDEO_KINDS:
        return len(preview.list_frames(root, material.id, ver)) == len(preview.FRAME_POSITIONS)
    return True


def enqueue_preview(
    material: Any,
    store_root: Path | str,
    *,
    force: bool = False,
) -> bool:
    """Persist one deduplicated preview job.

    Returns ``True`` only when a job file was created or replaced.
    """
    if not preview.is_cover_kind(material) or _cover_complete(material, store_root):
        return False

    path = _job_path(store_root, material.id)
    version = preview._ver(material)
    current = _read_job(path) if path.exists() else None
    if (
        not force
        and current
        and current.get("material_id") == material.id
        and current.get("version") == version
    ):
        # A terminal failure is also a deduplication record. Automatic
        # reconciliation must not reset its bounded retry budget forever;
        # explicit manual refresh can still pass force=True.
        return False

    now = time.time()
    _atomic_json(path, {
        "material_id": material.id,
        "version": version,
        "status": "queued",
        "attempts": 0,
        "queued_at": now,
        "next_attempt_at": now,
        "last_error": "",
    })
    return True


def reconcile_recent(store: Any, *, limit: int = 24) -> int:
    """Queue a bounded set of recent missing covers after a worker restart."""
    queued = 0
    for material in store.list(include_archived=False)[:max(0, limit)]:
        try:
            queued += int(enqueue_preview(material, store.root))
        except Exception:  # noqa: BLE001 - one bad material must not stop recovery
            _log.exception("Failed to reconcile preview for %s", getattr(material, "id", "?"))
    return queued


def _remove_if_version(path: Path, version: str) -> None:
    """Avoid deleting a newer job that a concurrent submission just replaced."""
    current = _read_job(path)
    if current and current.get("version") != version:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        _log.exception("Failed to remove completed preview job: %s", path)


def _retry(path: Path, job: dict[str, Any], error: str) -> None:
    attempts = int(job.get("attempts") or 0) + 1
    job = dict(job)
    job["attempts"] = attempts
    job["last_error"] = error[:1000]
    if attempts >= MAX_ATTEMPTS:
        job["status"] = "failed"
        job["next_attempt_at"] = None
    else:
        job["status"] = "queued"
        job["next_attempt_at"] = time.time() + min(300, 2 ** attempts)
    _atomic_json(path, job)


def process_preview_jobs(
    store_root: Path | str,
    *,
    limit: int = DEFAULT_BATCH_SIZE,
) -> dict[str, int]:
    """Process one due batch synchronously; intended for ``asyncio.to_thread``."""
    from .store import MaterialStore

    root = Path(store_root)
    store = MaterialStore(root)
    now = time.time()
    candidates: list[tuple[Path, dict[str, Any]]] = []
    for path in queue_dir(root).glob("*.json"):
        job = _read_job(path)
        if not job or job.get("status") == "failed":
            continue
        try:
            due = float(job.get("next_attempt_at") or 0) <= now
        except (TypeError, ValueError):
            due = True
        if due:
            candidates.append((path, job))
    candidates.sort(key=lambda item: float(item[1].get("queued_at") or 0))
    candidates = candidates[:max(0, limit)]

    materials: list[Any] = []
    active: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path, job in candidates:
        material_id = str(job.get("material_id") or "")
        material = store.get(material_id)
        if material is None or not preview.is_cover_kind(material):
            _remove_if_version(path, str(job.get("version") or ""))
            continue
        if _cover_complete(material, root):
            _remove_if_version(path, str(job.get("version") or ""))
            continue
        current_version = preview._ver(material)
        if job.get("version") != current_version:
            enqueue_preview(material, root, force=True)
            refreshed = _read_job(path)
            if refreshed is None:
                continue
            job = refreshed
        job["status"] = "running"
        _atomic_json(path, job)
        materials.append(material)
        active[material.id] = (path, job)

    if not materials:
        return {"selected": len(candidates), "generated": 0, "failed": 0}

    def _read_text(material: Any) -> str:
        file_path = store.resolve_file_path(material)
        if file_path is None:
            return ""
        try:
            return file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    try:
        result = preview.generate_for(materials, root, read_text=_read_text)
    except Exception as exc:  # noqa: BLE001 - keep the worker alive and retry jobs
        result = {
            "generated": [],
            "skipped": [],
            "errors": [("_batch", f"{type(exc).__name__}: {exc}")],
        }
        _log.exception("Preview batch failed")

    completed = set(result.get("generated") or []) | set(result.get("skipped") or [])
    errors = {
        str(material_id): str(message)
        for material_id, message in (result.get("errors") or [])
    }
    batch_error = errors.get("_batch") or errors.get("_import") or ""
    failed = 0
    for material in materials:
        path, job = active[material.id]
        if material.id in completed or _cover_complete(material, root):
            _remove_if_version(path, str(job.get("version") or ""))
            continue
        failed += 1
        _retry(
            path,
            job,
            errors.get(material.id) or batch_error or "preview renderer produced no cover",
        )

    return {
        "selected": len(candidates),
        "generated": len(materials) - failed,
        "failed": failed,
    }


async def run_preview_worker(
    store_root: Path | str,
    *,
    stop_event: asyncio.Event | None = None,
    poll_seconds: float = 2.0,
) -> None:
    """Consume durable jobs until cancelled or ``stop_event`` is set."""
    from .store import MaterialStore

    root = Path(store_root)
    next_reconcile = 0.0
    try:
        await asyncio.to_thread(reconcile_recent, MaterialStore(root))
    except Exception:  # noqa: BLE001
        _log.exception("Preview queue startup reconciliation failed")

    while stop_event is None or not stop_event.is_set():
        try:
            now = time.monotonic()
            if now >= next_reconcile:
                # Compatibility safety net for a long-lived ccdaemon that loaded
                # before this producer hook existed, and for out-of-process writers.
                # Normal updated submitters still enqueue synchronously at commit.
                await asyncio.to_thread(reconcile_recent, MaterialStore(root))
                next_reconcile = now + 10.0
            stats = await asyncio.to_thread(process_preview_jobs, root)
            delay = 0.2 if stats["selected"] else poll_seconds
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _log.exception("Preview queue worker iteration failed")
            delay = poll_seconds

        if stop_event is None:
            await asyncio.sleep(delay)
            continue
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass


def queue_status(store_root: Path | str) -> dict[str, int]:
    """Small observability helper for tests and diagnostics."""
    counts = {"queued": 0, "running": 0, "failed": 0, "invalid": 0}
    for path in queue_dir(store_root).glob("*.json"):
        job = _read_job(path)
        status = str((job or {}).get("status") or "invalid")
        counts[status if status in counts else "invalid"] += 1
    return counts
