# [OMNI] origin=claude-code domain=services/_focus ts=2026-06-23T00:00:00Z type=team
# [OMNI] material_id="material:services._focus.plan_progress_recorder.init.py"
"""plan-progress-recorder —— 事件型 team: 读一个计划自评进度 → 记录进 whatnow。

脑子=自建 gpt-5.5 tool-agent(能搜/读/列), 手=确定性 whatnow 落地 worker。
入口 material=planprog.request, 出口=planprog.recorded。
按名可跑: dispatch("plan-progress-recorder", {"plan_id": "...", "task_id": "..."})。
"""
from .formats import ALL_FORMATS
from .workers import ALL_WORKERS, PlanProgressExtractorWorker, WhatnowRecorderWorker

__all__ = ["ALL_FORMATS", "ALL_WORKERS", "PlanProgressExtractorWorker", "WhatnowRecorderWorker"]
