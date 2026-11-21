# [OMNI] origin=codex domain=dashboard ts=2026-06-13T06:18:00+08:00 type=dashboard
# [OMNI] material_id="material:dashboard.boss_sight.llm_runtime_usage.py"
"""Runtime LLM usage and batch-state aggregation for BOSS SIGHT."""

from __future__ import annotations

import json
import os
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from omnicompany.core.config import omni_workspace_root
from omnicompany.runtime.llm.batch import default_batch_status_path, read_batch_status


_SURFACES = ("single", "batch", "agent")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _meter_path() -> Path:
    override = os.environ.get("OMNI_LLM_METER_PATH")
    if override:
        return Path(override)
    return omni_workspace_root() / "data" / "llm" / "meter.jsonl"


def _surface_for(caller: str) -> str:
    lower = caller.lower()
    if caller.startswith("governance."):
        return "batch"
    if ".turn_" in lower or "agent" in lower or "loop" in lower:
        return "agent"
    return "single"


def _empty_totals() -> dict[str, Any]:
    return {
        "call_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "latency_ms": 0.0,
    }


def _add_record(bucket: dict[str, Any], rec: dict[str, Any]) -> None:
    input_tokens = int(rec.get("input_tokens") or 0)
    output_tokens = int(rec.get("output_tokens") or 0)
    cache_read = int(rec.get("cache_read_tokens") or 0)
    cache_creation = int(rec.get("cache_creation_tokens") or 0)
    bucket["call_count"] += 1
    bucket["input_tokens"] += input_tokens
    bucket["output_tokens"] += output_tokens
    bucket["cache_read_tokens"] += cache_read
    bucket["cache_creation_tokens"] += cache_creation
    bucket["total_tokens"] += input_tokens + output_tokens + cache_read + cache_creation
    bucket["cost_usd"] += float(rec.get("cost_usd") or 0.0)
    bucket["latency_ms"] += float(rec.get("latency_ms") or 0.0)


def _finalize(bucket: dict[str, Any]) -> dict[str, Any]:
    out = dict(bucket)
    calls = int(out.get("call_count") or 0)
    out["cost_usd"] = round(float(out.get("cost_usd") or 0.0), 6)
    out["latency_avg_ms"] = round(float(out.get("latency_ms") or 0.0) / calls, 2) if calls else 0.0
    out.pop("latency_ms", None)
    return out


def _recent_record(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": rec.get("timestamp"),
        "model": rec.get("model") or "",
        "role": rec.get("role") or "",
        "caller": rec.get("caller") or "",
        "surface": _surface_for(str(rec.get("caller") or "")),
        "input_tokens": int(rec.get("input_tokens") or 0),
        "output_tokens": int(rec.get("output_tokens") or 0),
        "cache_read_tokens": int(rec.get("cache_read_tokens") or 0),
        "cache_creation_tokens": int(rec.get("cache_creation_tokens") or 0),
        "cost_usd": round(float(rec.get("cost_usd") or 0.0), 6),
        "latency_ms": float(rec.get("latency_ms") or 0.0),
        "stop_reason": rec.get("stop_reason") or "",
    }


def _read_meter_records(path: Path, *, limit: int) -> tuple[list[dict[str, Any]], int, int]:
    if not path.is_file():
        return [], 0, 0
    records: deque[dict[str, Any]] = deque(maxlen=max(1, limit))
    total_lines = 0
    invalid_lines = 0
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                total_lines += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    invalid_lines += 1
                    continue
                if isinstance(raw, dict):
                    records.append(raw)
                else:
                    invalid_lines += 1
    except OSError:
        return [], total_lines, invalid_lines
    return list(records), total_lines, invalid_lines


def _summarize_records(records: Iterable[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    summary = _empty_totals()
    by_surface = {surface: _empty_totals() for surface in _SURFACES}
    by_model: dict[str, dict[str, Any]] = defaultdict(_empty_totals)
    by_caller: dict[str, dict[str, Any]] = defaultdict(_empty_totals)

    for rec in records:
        caller = str(rec.get("caller") or "unknown")
        model = str(rec.get("model") or "unknown")
        _add_record(summary, rec)
        _add_record(by_surface[_surface_for(caller)], rec)
        _add_record(by_model[model], rec)
        _add_record(by_caller[caller], rec)

    caller_rows = [
        {"caller": caller, **_finalize(bucket)}
        for caller, bucket in sorted(
            by_caller.items(),
            key=lambda item: (item[1]["cost_usd"], item[1]["total_tokens"], item[1]["call_count"]),
            reverse=True,
        )
    ]
    model_rows = {model: _finalize(bucket) for model, bucket in sorted(by_model.items())}
    surface_rows = {surface: _finalize(bucket) for surface, bucket in by_surface.items()}
    return _finalize(summary), surface_rows, caller_rows, model_rows


def _build_meter(*, limit: int, recent_limit: int, caller_limit: int) -> dict[str, Any]:
    path = _meter_path()
    records, total_lines, invalid_lines = _read_meter_records(path, limit=limit)
    summary, by_surface, by_caller, by_model = _summarize_records(records)
    recent = [_recent_record(rec) for rec in records[-max(0, recent_limit):]]
    return {
        "available": path.is_file(),
        "path": str(path),
        "record_count": len(records),
        "total_lines": total_lines,
        "partial": total_lines > len(records),
        "invalid_lines": invalid_lines,
        "summary": summary,
        "by_surface": by_surface,
        "by_model": by_model,
        "by_caller": by_caller[: max(0, caller_limit)],
        "recent": recent,
    }


def _build_batch() -> dict[str, Any]:
    path = default_batch_status_path()
    runs = read_batch_status(status_path=path)
    active_statuses = {"running", "queued"}
    failed_statuses = {"failed", "completed_with_failures"}
    completed_statuses = {"completed", "completed_with_failures"}
    active_count = sum(1 for run in runs.values() if isinstance(run, dict) and run.get("status") in active_statuses)
    failed_count = sum(1 for run in runs.values() if isinstance(run, dict) and run.get("status") in failed_statuses)
    completed_count = sum(1 for run in runs.values() if isinstance(run, dict) and run.get("status") in completed_statuses)
    return {
        "available": path.is_file(),
        "path": str(path),
        "run_count": len(runs),
        "active_count": active_count,
        "completed_count": completed_count,
        "failed_count": failed_count,
        "runs": runs,
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _build_governance() -> dict[str, Any]:
    root = omni_workspace_root()
    plan_path = root / "data" / "registry" / "plan_governance.json"
    plan_raw = _read_json(plan_path)
    plans = plan_raw.get("plans") if isinstance(plan_raw.get("plans"), dict) else {}

    history_dir = root / "data" / "governance" / "work_history"
    latest_path = history_dir / "latest.json"
    latest = _read_json(latest_path)
    return {
        "plan_steward": {
            "available": plan_path.is_file(),
            "path": str(plan_path),
            "generated_at": plan_raw.get("generated_at"),
            "model": plan_raw.get("model"),
            "plan_count": len(plans),
        },
        "work_history": {
            "available": latest_path.is_file(),
            "path": str(latest_path),
            "generated_at": latest.get("generated_at"),
            "model": latest.get("model"),
            "messages": latest.get("messages", 0),
            "chunks": latest.get("chunks", 0),
            "signals": latest.get("signals", 0),
            "clusters": latest.get("clusters", 0),
            "failures": latest.get("failures", []),
            "findings": latest.get("findings"),
            "report": latest.get("report"),
        },
    }


def build_llm_runtime_usage(
    *,
    limit: int = 5000,
    recent_limit: int = 20,
    caller_limit: int = 12,
) -> dict[str, Any]:
    """Build dashboard-ready internal LLM usage from runtime-owned artifacts."""

    meter = _build_meter(limit=limit, recent_limit=recent_limit, caller_limit=caller_limit)
    batch = _build_batch()
    return {
        "generated_at": _now_iso(),
        "source": "runtime.llm meter.jsonl + runtime.llm batch_status.json",
        "available": meter["available"] or batch["available"],
        "summary": meter["summary"],
        "by_surface": meter["by_surface"],
        "meter": meter,
        "batch": batch,
        "governance": _build_governance(),
    }


__all__ = ["build_llm_runtime_usage"]
