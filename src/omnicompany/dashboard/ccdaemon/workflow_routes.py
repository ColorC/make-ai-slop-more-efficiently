# [OMNI] origin=ai-ide ts=2026-06-06 type=route
"""N2d workflow 编排 HTTP 路由 (ccdaemon, 挂在 /cc/workflow)。

CLI `omni workflow` 与 dashboard 通过这里驱动 boss_sight 的 WorkflowOrchestrator。
编排引擎本体在 boss_sight/services/workflow_orchestrator.py, 这里只做薄 HTTP 壳。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

workflow_router = APIRouter(prefix="/cc/workflow", tags=["cc-workflow"])


class RunWorkflowBody(BaseModel):
    title: str = Field(default="", description="workflow 名字")
    plan_id: str = Field(..., description="关联 plan id (必填, 工作流是 plan 范围的)")
    tasks: list[str] = Field(..., description="fan-out 子任务 prompt 列表 (一任务一 subagent)")
    synthesize: str | None = Field(default=None, description="可选: fan-out 全完成后综合阶段的 prompt; 不填=不综合")
    provider: str = Field(default="claude_code", description="subagent provider")
    model: str | None = Field(default=None)
    cwd: str | None = Field(default=None)


def _orch():
    from omnicompany.packages.services.boss_sight.services.workflow_orchestrator import get_orchestrator

    return get_orchestrator()


@workflow_router.post("/run")
async def run_workflow(body: RunWorkflowBody) -> dict[str, Any]:
    try:
        return await _orch().create_and_run({
            "title": body.title,
            "plan_id": body.plan_id,
            "tasks": body.tasks,
            "synthesize": body.synthesize,
            "provider": body.provider,
            "model": body.model,
            "cwd": body.cwd,
        })
    except ValueError as e:
        raise HTTPException(400, str(e))


@workflow_router.get("")
async def list_workflows() -> dict[str, Any]:
    return {"items": _orch().list_all()}


@workflow_router.get("/{wf_id}")
async def get_workflow(wf_id: str) -> dict[str, Any]:
    wf = _orch().get(wf_id)
    if wf is None:
        raise HTTPException(404, f"workflow {wf_id} not found")
    return wf


__all__ = ["workflow_router"]
