# [OMNI] origin=ai-ide domain=dashboard ts=2026-05-02T06:00:00Z type=router status=active agent=ai-ide-current
# [OMNI] summary="dashboard LLM API - 调用记录查询 + 成本统计 + 跟 omni llm audit CLI 同源"
# [OMNI] why="LLM 设施已完备 (LLMClient + LLMCallRouter + omni llm audit), 缺 web 端入口. 这层暴露读, 不写"
# [OMNI] tags=dashboard,api,llm,audit,read-only
# [OMNI] material_id="material:dashboard.llm_api.audit_endpoint.py"
"""dashboard LLM 调用记录 API.

接口:
  GET /api/v2/llm/audit                     调用记录列表 (跟 omni llm audit 同源)
  GET /api/v2/llm/audit/{trace_id}          按 trace_id 拉某 session 的全部 LLM 调用
  GET /api/v2/llm/stats                     调用统计 (count / by-model / by-pipeline / 估算 token)

只读. 不暴露 LLM 调用入口 (那是 LLMCallRouter / call_llm_json 的职责).
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from fastapi import APIRouter, HTTPException, Query


llm_router = APIRouter()


def _audit_root():
    """跟 cli/commands/llm_audit.py:_audit_root() 同源."""
    from omnicompany.core.config import resolve_runtime_data_dir
    return resolve_runtime_data_dir("llm_audit")


def _iter_audit_records(*, trace_id: str | None = None, limit: int = 200) -> list[dict]:
    """简化版 _iter_records, 不做完整过滤. 复杂查询走 omni llm audit CLI."""
    root = _audit_root()
    if not root.exists():
        return []
    out: list[dict] = []
    day_dirs = sorted([p for p in root.iterdir() if p.is_dir()], reverse=True)
    for day_dir in day_dirs:
        if trace_id:
            jf = day_dir / f"{trace_id}.jsonl"
            if not jf.exists():
                continue
            files = [jf]
        else:
            files = sorted(day_dir.glob("*.jsonl"), reverse=True)
        for jf in files:
            try:
                with jf.open("r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        out.append(rec)
                        if len(out) >= limit:
                            return out
            except OSError:
                continue
    return out


@llm_router.get("/llm/audit")
def list_audit(
    trace_id: str | None = Query(None, description="按 trace_id 过滤"),
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """列 LLM 调用记录."""
    records = _iter_audit_records(trace_id=trace_id, limit=limit)
    return {
        "items": [
            {
                "trace_id": rec.get("trace_id"),
                "pipeline": rec.get("pipeline"),
                "node": rec.get("node"),
                "model": rec.get("model"),
                "ts": rec.get("ts"),
                "prompt_chars": len(str(rec.get("prompt", ""))),
                "response_chars": len(str(rec.get("response", ""))),
                "duration_ms": rec.get("duration_ms"),
            }
            for rec in records
        ],
        "total": len(records),
    }


@llm_router.get("/llm/audit/{trace_id}")
def get_audit_by_trace(trace_id: str, limit: int = 200) -> dict[str, Any]:
    """单 trace_id 的全部 LLM 调用记录."""
    records = _iter_audit_records(trace_id=trace_id, limit=limit)
    if not records:
        raise HTTPException(status_code=404, detail=f"trace_id {trace_id!r} 没 LLM 调用记录")
    return {"trace_id": trace_id, "items": records, "total": len(records)}


@llm_router.get("/llm/stats")
def llm_stats(limit: int = 1000) -> dict[str, Any]:
    """LLM 调用统计.

    返回 count / by_model / by_pipeline / 估算 prompt+response 字符总数.
    """
    records = _iter_audit_records(limit=limit)
    by_model: Counter[str] = Counter()
    by_pipeline: Counter[str] = Counter()
    total_prompt_chars = 0
    total_response_chars = 0
    for rec in records:
        m = rec.get("model") or "unknown"
        p = rec.get("pipeline") or "unknown"
        by_model[m] += 1
        by_pipeline[p] += 1
        total_prompt_chars += len(str(rec.get("prompt", "")))
        total_response_chars += len(str(rec.get("response", "")))
    return {
        "total_calls": len(records),
        "by_model": dict(by_model),
        "by_pipeline": dict(by_pipeline.most_common(10)),
        "char_totals": {
            "prompt": total_prompt_chars,
            "response": total_response_chars,
            "all": total_prompt_chars + total_response_chars,
        },
        "note": "更深入的过滤走 `omni llm audit` CLI",
    }
