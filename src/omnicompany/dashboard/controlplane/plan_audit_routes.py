# [OMNI] origin=claude-code domain=dashboard/controlplane ts=2026-06-19T16:00:00Z type=service
# [OMNI] material_id="material:dashboard.controlplane.plan_audit_web_job_endpoints.py"
"""plan audit 网页端点 — 把 plan audit 引擎接进三点菜单的「跑 audit」项.

audit 是 LLM 多轮循环(分钟级), 不能在 HTTP 请求里同步等. 异步 job 模式:
  POST /api/plan-audit  {against, id, provider?, model?} → 起后台线程跑 → {job_id, status:running}
  GET  /api/plan-audit/{job_id} → {status: running|done|error, report_md?, result?, error?, elapsed_s}

services._core.plan_audit.run.run_* 内部各自 asyncio.run, 必须在独立线程跑(不碰 FastAPI 事件循环).
job 存进程内存(过路用, dashboard 重启即清); 报告同时落 data/services/plan_audit/(persist_report).
"""
from __future__ import annotations

import logging
import threading
import time
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

plan_audit_router = APIRouter(tags=["plan_audit"])

# 进程内 job 存储(过路用). {job_id: {status, against, target, started_at, result?, report_md?, error?}}
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_MAX_JOBS = 50


class _AuditReq(BaseModel):
    against: str  # 'conversation' | 'plan'
    id: str
    provider: str | None = "claude_code"
    model: str | None = None


def _set(job_id: str, **kw) -> None:
    with _JOBS_LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(kw)


def _run_job(job_id: str, req: _AuditReq) -> None:
    from omnicompany.packages.services._core.plan_audit.run import (
        run_conversation_audit,
        run_plan_audit,
        render_report,
        persist_report,
    )
    # 模型: 默认走引擎默认(本机 THE_COMPANY_API_KEY 只授权 models=['limited'], 强模型如 claude-sonnet
    # 会 403; 故不强制 quality 档). 调用方可显式传 model 覆盖. 大转写下默认模型可能跑满轮次吐不出
    # 结构化 JSON —— 这是模型能力+授权的硬约束, 报告里 verdict_kind=partial 会如实反映.
    try:
        if req.against == "conversation":
            result = run_conversation_audit(session_id=req.id, provider=req.provider, model=req.model)
        else:
            result = run_plan_audit(plan_id=req.id, model=req.model)
        if not result.get("ok"):
            _set(job_id, status="error", error=result.get("error", "audit 失败(无 error 字段)"))
            return
        report_md = render_report(result)
        try:
            result["report_paths"] = persist_report(result, trace=req.id)
        except Exception as e:  # noqa: BLE001
            logger.debug("plan audit 报告留档失败(非致命): %s", e)
        _set(job_id, status="done", result=result, report_md=report_md)
    except Exception as e:  # noqa: BLE001
        logger.warning("plan audit job %s 失败: %s", job_id, e, exc_info=True)
        _set(job_id, status="error", error=f"{type(e).__name__}: {e}")


@plan_audit_router.post("/plan-audit")
def start_plan_audit(req: _AuditReq) -> dict:
    """启动一次 audit(后台线程跑), 立即返回 job_id; 前端轮询 GET /plan-audit/{job_id} 取报告."""
    if req.against not in ("conversation", "plan"):
        raise HTTPException(status_code=400, detail="against 必须是 conversation 或 plan")
    if not (req.id or "").strip():
        raise HTTPException(status_code=400, detail="id 不能为空")
    job_id = uuid.uuid4().hex[:16]
    with _JOBS_LOCK:
        if len(_JOBS) >= _MAX_JOBS:  # 容量保护: 删最旧
            for k, _ in sorted(_JOBS.items(), key=lambda kv: kv[1].get("started_at", 0))[: len(_JOBS) - _MAX_JOBS + 1]:
                _JOBS.pop(k, None)
        _JOBS[job_id] = {
            "status": "running", "against": req.against, "target": req.id,
            "started_at": time.time(),
        }
    threading.Thread(target=_run_job, args=(job_id, req), daemon=True).start()
    return {"job_id": job_id, "status": "running"}


@plan_audit_router.get("/plan-audit/{job_id}")
def poll_plan_audit(job_id: str) -> dict:
    """轮询 audit job 状态/报告."""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="无此 audit job(可能已过期或 dashboard 重启被清)")
        out = dict(job)
    out["elapsed_s"] = round(time.time() - out.get("started_at", time.time()), 1)
    return out
