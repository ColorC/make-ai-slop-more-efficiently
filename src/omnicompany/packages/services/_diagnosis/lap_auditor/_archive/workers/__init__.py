# [OMNI] origin=claude-code domain=omnifactory/lap_auditor ts=2026-04-21T00:00:00Z type=config
# [OMNI] material_id="material:diagnosis.lap_auditor.worker_registry.python"
"""lap_auditor Team · 3 Worker 清单 (Stage 3 Clean Migration 2026-04-21).

每个 Worker 独立文件, 无 Diamond shortcut, _archive 不再被 workers import。

链路: lap_auditor.input → ContextGetterWorker → lap_auditor.context
           → SpecAuditorWorker → lap_auditor.report
           → ReportFormatterWorker → lap_auditor.done
"""
from __future__ import annotations

from omnifactory.packages.services._core.omnicompany import Worker

from .context_getter_worker import ContextGetterWorker
from .report_formatter_worker import ReportFormatterWorker
from .spec_auditor_worker import SpecAuditorWorker

ALL_WORKERS: list[type[Worker]] = [
    ContextGetterWorker,
    SpecAuditorWorker,
    ReportFormatterWorker,
]

__all__ = [
    "ALL_WORKERS",
    "ContextGetterWorker",
    "SpecAuditorWorker",
    "ReportFormatterWorker",
]
