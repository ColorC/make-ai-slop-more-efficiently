# [OMNI] origin=claude-code domain=services/absorption/workers/v1 ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.worker.v1.coverage_auditor.py"
"""CoverageAuditorWorker — V1 Survey Worker #4 (Clean Migration 2026-04-20).

Worker 协议:
  FORMAT_IN  = absorption.landmark_list
  FORMAT_OUT = absorption.coverage_audit

职责: ANCHOR + HARD. 比对总 tree vs 读过的文件, 产覆盖度审计.
实现继承自 _archive/routers_v1v2_legacy.CoverageAuditorRouter.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v1v2_legacy import (
    CoverageAuditorRouter as _Legacy,
)


class CoverageAuditorWorker(Worker, _Legacy):
    pass
