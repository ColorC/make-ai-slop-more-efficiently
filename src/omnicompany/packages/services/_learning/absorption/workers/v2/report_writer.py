# [OMNI] origin=claude-code domain=services/absorption/workers/v2 ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.worker.v2.report_writer.py"
"""ReportWriterV2Worker — V2 Worker #7 (Clean Migration 2026-04-20).

职责: V2 markdown 报告产出 (sink).
实现继承自 _archive/routers_v1v2_legacy.ReportWriterV2Router.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v1v2_legacy import (
    ReportWriterV2Router as _Legacy,
)


class ReportWriterV2Worker(Worker, _Legacy):
    pass
