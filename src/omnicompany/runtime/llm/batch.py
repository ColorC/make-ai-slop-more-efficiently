# [OMNI] origin=codex domain=omnicompany/runtime ts=2026-06-13T06:05:00+08:00 type=runtime
# [OMNI] material_id="material:runtime.llm.batch_runner.py"
"""Shared batch helpers for long-running LLM pipelines.

This module owns the common mechanics for parallel item execution, per-item
failure isolation, progress logging, and JSON checkpoints used for resume.
Domain pipelines keep their business steps; they should not open their own
thread pools or hand-roll checkpoint file protocols.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Generic, Sequence, TypeVar

T = TypeVar("T")
R = TypeVar("R")


@dataclass(frozen=True)
class BatchFailure:
    index: int
    label: str
    error: str


@dataclass(frozen=True)
class BatchResult(Generic[R]):
    total: int
    completed: int
    results: list[R]
    failures: list[str]
    failure_details: list[BatchFailure] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def _default_item_label(index: int, _item: Any) -> str:
    return f"item {index}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_batch_status_path() -> Path:
    override = None
    try:
        import os
        override = os.environ.get("OMNI_LLM_BATCH_STATUS_PATH")
    except Exception:  # noqa: BLE001
        override = None
    if override:
        return Path(override)
    from omnicompany.core.config import omni_workspace_root
    return omni_workspace_root() / "data" / "llm" / "batch_status.json"


def write_batch_status(
    run_id: str,
    payload: dict[str, Any],
    *,
    status_path: Path | None = None,
) -> None:
    path = Path(status_path or default_batch_status_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        current = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if not isinstance(current, dict):
            current = {}
    except (OSError, json.JSONDecodeError):
        current = {}
    current[run_id] = payload
    path.write_text(json.dumps(current, ensure_ascii=False, indent=1), encoding="utf-8")


def read_batch_status(*, status_path: Path | None = None) -> dict[str, Any]:
    path = Path(status_path or default_batch_status_path())
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def run_parallel_items(
    items: Sequence[T],
    worker: Callable[[T], R],
    *,
    workers: int = 4,
    item_label: Callable[[int, T], str] | None = None,
    echo: Callable[[str], None] | None = None,
    progress_label: str = "batch",
    progress_every: int = 10,
    status_run_id: str | None = None,
    status_path: Path | None = None,
) -> BatchResult[R]:
    """Run independent items concurrently and isolate failures per item.

    Results are returned in input order for successful items. Failure strings
    are stable and suitable for user-facing reports.
    """

    total = len(items)
    started_at = _now_iso()

    def _emit_status(status: str, completed: int, success_count: int, failure_count: int) -> None:
        if not status_run_id:
            return
        try:
            write_batch_status(
                status_run_id,
                {
                    "run_id": status_run_id,
                    "status": status,
                    "progress_label": progress_label,
                    "total": total,
                    "completed": completed,
                    "successes": success_count,
                    "failures": failure_count,
                    "started_at": started_at,
                    "updated_at": _now_iso(),
                },
                status_path=status_path,
            )
        except Exception:
            pass

    if total == 0:
        _emit_status("completed", 0, 0, 0)
        return BatchResult(total=0, completed=0, results=[], failures=[])

    label_for = item_label or _default_item_label
    max_workers = max(1, min(max(1, workers), total))
    progress_every = max(1, progress_every)
    ordered: list[R | None] = [None] * total
    success_indices: list[int] = []
    failures: list[str] = []
    failure_details: list[BatchFailure] = []
    completed = 0

    _emit_status("running", 0, 0, 0)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(worker, item): (idx, item) for idx, item in enumerate(items)}
        for future in as_completed(futures):
            idx, item = futures[future]
            label = label_for(idx, item)
            try:
                ordered[idx] = future.result()
                success_indices.append(idx)
            except Exception as exc:  # noqa: BLE001 - isolation is the point here.
                error = f"{label}: {exc}"
                failures.append(error)
                failure_details.append(BatchFailure(index=idx, label=label, error=str(exc)))
            completed += 1
            if echo and (completed % progress_every == 0 or completed == total):
                echo(f"  {progress_label} {completed}/{total} ok={len(success_indices)} failed={len(failures)}")
            _emit_status("running", completed, len(success_indices), len(failures))

    success_indices.sort()
    results = [ordered[idx] for idx in success_indices if ordered[idx] is not None]
    _emit_status("completed" if not failures else "completed_with_failures",
                 completed, len(success_indices), len(failures))
    return BatchResult(
        total=total,
        completed=completed,
        results=results,
        failures=failures,
        failure_details=failure_details,
    )


@dataclass(frozen=True)
class JsonCheckpoint:
    data_path: Path
    meta_path: Path | None = None
    missing_error: str = "checkpoint not found"


@dataclass(frozen=True)
class JsonCheckpointLoad:
    ok: bool
    data: Any = None
    meta: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def load_json_checkpoint(checkpoint: JsonCheckpoint) -> JsonCheckpointLoad:
    data_path = Path(checkpoint.data_path)
    if not data_path.is_file():
        return JsonCheckpointLoad(ok=False, error=checkpoint.missing_error)
    try:
        data = json.loads(data_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return JsonCheckpointLoad(ok=False, error=f"invalid checkpoint {data_path}: {exc}")

    meta: dict[str, Any] = {}
    if checkpoint.meta_path:
        meta_path = Path(checkpoint.meta_path)
        if meta_path.is_file():
            try:
                raw_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(raw_meta, dict):
                    meta = raw_meta
            except (OSError, json.JSONDecodeError):
                meta = {}
    return JsonCheckpointLoad(ok=True, data=data, meta=meta)


def write_json_checkpoint(
    checkpoint: JsonCheckpoint,
    data: Any,
    *,
    meta: dict[str, Any] | None = None,
    indent: int | None = None,
) -> None:
    data_path = Path(checkpoint.data_path)
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")

    if checkpoint.meta_path and meta is not None:
        meta_path = Path(checkpoint.meta_path)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=indent), encoding="utf-8")


__all__ = [
    "BatchFailure",
    "BatchResult",
    "JsonCheckpoint",
    "JsonCheckpointLoad",
    "default_batch_status_path",
    "load_json_checkpoint",
    "read_batch_status",
    "run_parallel_items",
    "write_batch_status",
    "write_json_checkpoint",
]
