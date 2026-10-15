# [OMNI] origin=qoder ts=2026-08-17 type=package
# [OMNI] material_id="material:packages.services._core.conventional_workflow"
"""惯例性工作流（conventional workflow）契约与运行时。

母文档：spark.workshop/docs/requirements/2026-08-16-conventional-workflow-canvas-material-team-job.md
契约口径与 webworks/packages/material-core/workflow.js (M1) 对齐：
模板层可带环（feedback 回边），执行层经 unroll 恒为 DAG；
审批/检查通过不等于自动写入，写入只在显式副作用事务里。
"""
from .spec import NODE_KINDS, WorkflowSpec, WorkflowSpecError, load_spec, unroll_spec, validate_spec
from .runtime import (
    GatePending,
    Verdict,
    WorkflowRunner,
    WorkerRegistry,
)

__all__ = [
    "NODE_KINDS",
    "WorkflowSpec",
    "WorkflowSpecError",
    "load_spec",
    "unroll_spec",
    "validate_spec",
    "GatePending",
    "Verdict",
    "WorkflowRunner",
    "WorkerRegistry",
]
