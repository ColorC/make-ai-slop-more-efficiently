# [OMNI] origin=claude-code domain=services/absorption/workers/v3 ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.worker.v3.report_updater.py"
"""ReportUpdaterV3Worker (Clean Migration 2026-04-20).

职责: LLM 增量融合补充发现到已有报告.
实现继承自 _archive/routers_v3_legacy.report_updater.ReportUpdaterV3Router.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v3_legacy.report_updater import (
    ReportUpdaterV3Router as _Legacy,
)


class ReportUpdaterV3Worker(Worker, _Legacy):
    pass
