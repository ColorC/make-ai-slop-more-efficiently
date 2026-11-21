# [OMNI] origin=claude-code domain=omnicompany/repair ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:core.repair.module_exports.aggregator.py"
"""repair — Format / Router 自动修复 Team (Clean Migration 2026-04-20)."""
from __future__ import annotations

from .workers import (
    ALL_WORKERS,
    # Format 修复子管线
    FormatPatcherWorker,
    FormatRepairAgentLoopWorker,
    RepairPlannerWorker,
    # Router 修复子管线
    DescriptionPlannerWorker,
    FailPathPlannerWorker,
    GrantedTagsPlannerWorker,
    IssueLoaderWorker,
    PatchApplierWorker,
    PatchMergerWorker,
    PatchValidatorWorker,
    RediagnoseWorker,
    RouterSourceLoaderWorker,
)

# 旧名兼容 shim (routers.py 转发)
from .routers import (
    DescriptionPlannerRouter,
    FailPathPlannerRouter,
    FormatPatcherRouter,
    FormatRepairAgentLoop,
    GrantedTagsPlannerRouter,
    IssueLoaderRouter,
    PatchApplierRouter,
    PatchMergerRouter,
    PatchValidatorRouter,
    RediagnoseRouter,
    RepairPlannerRouter,
    RouterSourceLoaderRouter,
    run_router_repair,
)


__all__ = [
    # 新名
    "RepairPlannerWorker",
    "FormatPatcherWorker",
    "FormatRepairAgentLoopWorker",
    "IssueLoaderWorker",
    "RouterSourceLoaderWorker",
    "DescriptionPlannerWorker",
    "FailPathPlannerWorker",
    "GrantedTagsPlannerWorker",
    "PatchMergerWorker",
    "PatchValidatorWorker",
    "PatchApplierWorker",
    "RediagnoseWorker",
    "ALL_WORKERS",
    # 旧名 (兼容)
    "RepairPlannerRouter",
    "FormatPatcherRouter",
    "FormatRepairAgentLoop",
    "IssueLoaderRouter",
    "RouterSourceLoaderRouter",
    "DescriptionPlannerRouter",
    "FailPathPlannerRouter",
    "GrantedTagsPlannerRouter",
    "PatchMergerRouter",
    "PatchValidatorRouter",
    "PatchApplierRouter",
    "RediagnoseRouter",
    # 辅助入口
    "run_router_repair",
]
