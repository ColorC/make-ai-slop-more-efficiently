# [OMNI] origin=claude-code domain=omnicompany/repair ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:core.repair.workers.aggregate.exports.py"
"""Repair Team · 12 Worker 清单 (Clean Migration 2026-04-20).

两个子管线, 共 12 Worker:

── Format 修复子管线 (原 routers.py · 3 Worker) ──
  RepairPlannerWorker         · repair.fmt.attempt → repair.fmt.attempt (LLM delta 规划)
  FormatPatcherWorker         · repair.fmt.attempt → repair.fmt.attempt (delta 写入源码)
  FormatRepairAgentLoopWorker · repair.fmt.request → repair.fmt.report  (组合迭代循环)

── Router 修复子管线 (原 router_repair.py · 9 Worker) ──
  IssueLoaderWorker           · diag.repair.request        → diag.repair.issue-list
  RouterSourceLoaderWorker    · diag.repair.issue-list     → diag.repair.source-context
  DescriptionPlannerWorker    · diag.repair.source-context → diag.repair.desc-patch (R-01)
  FailPathPlannerWorker       · diag.repair.desc-patch     → diag.repair.fail-patch (R-05)
  GrantedTagsPlannerWorker    · diag.repair.fail-patch     → diag.repair.tags-patch (R-07)
  PatchMergerWorker           · diag.repair.tags-patch     → diag.repair.patch-plan
  PatchValidatorWorker        · diag.repair.patch-plan     → diag.repair.validated-patch
  PatchApplierWorker          · diag.repair.validated-patch → diag.repair.applied
  RediagnoseWorker            · diag.repair.pending        → diag.repair.result
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker

# Format 修复子管线 (3 Worker)
from .format_patcher import FormatPatcherWorker
from .format_repair_agent_loop import FormatRepairAgentLoopWorker
from .repair_planner import RepairPlannerWorker

# Router 修复子管线 (9 Worker)
from .description_planner import DescriptionPlannerWorker
from .fail_path_planner import FailPathPlannerWorker
from .granted_tags_planner import GrantedTagsPlannerWorker
from .issue_loader import IssueLoaderWorker
from .patch_applier import PatchApplierWorker
from .patch_merger import PatchMergerWorker
from .patch_validator import PatchValidatorWorker
from .rediagnose import RediagnoseWorker
from .router_source_loader import RouterSourceLoaderWorker


ALL_WORKERS: list[type[Worker]] = [
    # Format 修复
    RepairPlannerWorker,
    FormatPatcherWorker,
    FormatRepairAgentLoopWorker,
    # Router 修复
    IssueLoaderWorker,
    RouterSourceLoaderWorker,
    DescriptionPlannerWorker,
    FailPathPlannerWorker,
    GrantedTagsPlannerWorker,
    PatchMergerWorker,
    PatchValidatorWorker,
    PatchApplierWorker,
    RediagnoseWorker,
]


__all__ = [
    # Format 修复
    "RepairPlannerWorker",
    "FormatPatcherWorker",
    "FormatRepairAgentLoopWorker",
    # Router 修复
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
]
