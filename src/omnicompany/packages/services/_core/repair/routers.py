# [OMNI] origin=claude-code domain=omnicompany/repair ts=2026-04-20T00:00:00Z type=shim
# [OMNI] material_id="material:core.repair.router_compat.shim.py"
"""repair/routers.py — 向后兼容 shim (Clean Migration 2026-04-20).

真实 Worker 实现在 `workers/` 目录。本文件仅为旧 import 路径保留兼容:
  - 旧名 FooRouter → 新名 FooWorker (别名)
  - 旧 `from ...repair.routers import FormatRepairAgentLoop` 继续工作

不要往本文件加新逻辑; 新增 Worker 请直接写 `workers/<name>.py`。
归档:
  - `_archive/routers_legacy.py` 保留原 3-Router 单文件实现
  - `_archive/router_repair_legacy.py` 保留原 9-Router 单文件实现
"""
from __future__ import annotations

from .workers import (
    # Format 修复子管线 (原 routers.py)
    FormatPatcherWorker,
    FormatRepairAgentLoopWorker,
    RepairPlannerWorker,
    # Router 修复子管线 (原 router_repair.py)
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


# ─── Format 修复子管线旧名 (兼容) ────────────────────────────────────────
RepairPlannerRouter = RepairPlannerWorker
FormatPatcherRouter = FormatPatcherWorker
FormatRepairAgentLoop = FormatRepairAgentLoopWorker

# ─── Router 修复子管线旧名 (兼容) ────────────────────────────────────────
IssueLoaderRouter = IssueLoaderWorker
RouterSourceLoaderRouter = RouterSourceLoaderWorker
DescriptionPlannerRouter = DescriptionPlannerWorker
FailPathPlannerRouter = FailPathPlannerWorker
GrantedTagsPlannerRouter = GrantedTagsPlannerWorker
PatchMergerRouter = PatchMergerWorker
PatchValidatorRouter = PatchValidatorWorker
PatchApplierRouter = PatchApplierWorker
RediagnoseRouter = RediagnoseWorker


def run_router_repair(
    router_class: str,
    source_file: str,
    source_root: str | None = None,
    model: str | None = None,
) -> dict:
    """对单个 Router 执行 B 类问题补全流程 (兼容旧入口)。

    流程: IssueLoader → SourceLoader → DescPlanner → FailPlanner → TagsPlanner
          → PatchMerger → PatchValidator → PatchApplier (直接写入源文件)

    返回 dict: status = "applied" / "no_issues" / "skipped" / "error"
    """
    from .workers._shared import _DEFAULT_SOURCE_ROOT, _MODEL

    if source_root is None:
        source_root = str(_DEFAULT_SOURCE_ROOT)
    if model is None:
        model = _MODEL

    def unpack(v):
        return v.output if hasattr(v, "output") else v

    req = {"router_class": router_class, "source_file": source_file, "source_root": source_root}

    import logging
    logger = logging.getLogger(__name__)

    try:
        r = unpack(IssueLoaderWorker().run(req))
        if r.get("skip_reason"):
            return {**r, "status": "skipped"}
        if not r.get("b_class_issues"):
            return {**r, "status": "no_issues"}

        r = unpack(RouterSourceLoaderWorker().run(r))
        r = unpack(DescriptionPlannerWorker(model=model).run(r))
        r = unpack(FailPathPlannerWorker(model=model).run(r))
        r = unpack(GrantedTagsPlannerWorker(model=model).run(r))
        r = unpack(PatchMergerWorker().run(r))

        if not r.get("diff"):
            return {**r, "status": "error", "error": "所有规划器均未生成有效 diff"}

        r = unpack(PatchValidatorWorker().run(r))
        if not r.get("validation_passed"):
            return {**r, "status": "error", "error": f"diff 验证失败: {r.get('validation_notes')}"}

        r = unpack(PatchApplierWorker().run(r))
        if not r.get("applied"):
            return {**r, "status": "error",
                    "error": r.get("apply_errors") or r.get("apply_note") or "apply 失败"}
        return {**r, "status": "applied"}

    except Exception as e:
        logger.exception("run_router_repair failed for %s", router_class)
        return {"router_class": router_class, "status": "error", "error": str(e)}


__all__ = [
    # 新名 (推荐)
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
