# [OMNI] origin=claude-code domain=services/absorption/workers/v1 ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:learning.absorption.worker.v1.report_writer.py"
"""ReportWriterWorker — V1 Survey Worker #6 (Clean Migration 2026-04-20).

Worker 协议:
  FORMAT_IN  = absorption.triaged_landmarks
  FORMAT_OUT = absorption.report

职责: TRANSFORMER + RULE. 产 markdown 报告 (sink).
实现继承自 _archive/routers_v1v2_legacy.ReportWriterRouter.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._learning.absorption._archive.routers_v1v2_legacy import (
    ReportWriterRouter as _Legacy,
)


class ReportWriterWorker(Worker, _Legacy):
    pass
