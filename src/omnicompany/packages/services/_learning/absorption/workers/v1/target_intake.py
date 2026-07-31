# [OMNI] origin=claude-code domain=services/absorption/workers/v1 ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.worker.v1.target_intake.py"
"""TargetIntakeWorker — V1 Survey Worker #1 (Clean Migration 2026-04-20).

Worker 协议:
  FORMAT_IN  = absorption.user_request
  FORMAT_OUT = absorption.intake

职责: ANCHOR + HARD. 解析 user_request 中的 repos (支持短名/HTTP/SSH URL)、
      校验 profile、为本次 absorption 分配全局唯一 absorption_id.
实现继承自 _archive/routers_v1v2_legacy.TargetIntakeRouter (业务逻辑不变).
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v1v2_legacy import (
    TargetIntakeRouter as _Legacy,
)


class TargetIntakeWorker(Worker, _Legacy):
    pass
