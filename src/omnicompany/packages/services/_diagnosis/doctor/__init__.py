# [OMNI] origin=omnicompany domain=omnicompany/doctor ts=2026-04-22T00:00:00Z
# [OMNI] material_id="material:diagnosis.doctor.package_aggregate.exports.py"
"""omnicompany.packages.services._diagnosis.doctor — 诊断 Team (Stage 3 命名规范化完成 2026-04-22).

三个子域 24 Worker:
  - material/ (9 Worker) · Material 健康诊断 (原 format 子域)
  - worker/   (6 Worker) · Worker 健康诊断 (原 router 子域)
  - team/     (9 Worker) · Team 拓扑诊断 + Lineage (原 pipeline 子域)
  - blackboard/ (6 Worker) · 订阅图诊断

使用:
    from omnicompany.packages.services._diagnosis.doctor.team import build_team
    from omnicompany.packages.services._diagnosis.doctor.run import build_bindings
    from omnicompany.packages.services._diagnosis.doctor.workers import ALL_WORKERS
"""
from __future__ import annotations

from .workers import (
    ALL_WORKERS,
    ALL_WORKERS_MATERIAL,
    ALL_WORKERS_WORKER_DIAG,
    ALL_WORKERS_TEAM,
    ALL_WORKERS_BLACKBOARD,
)

# routers.py / pipeline_topology.py shim 继续存在, 暴露 legacy Router 名别名
from . import routers  # noqa: F401
from . import pipeline_topology  # noqa: F401


__all__ = [
    "ALL_WORKERS",
    "ALL_WORKERS_MATERIAL",
    "ALL_WORKERS_WORKER_DIAG",
    "ALL_WORKERS_TEAM",
    "ALL_WORKERS_BLACKBOARD",
]
