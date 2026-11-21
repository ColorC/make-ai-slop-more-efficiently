# [OMNI] origin=claude-code domain=omnifactory/cleanup_bot ts=2026-04-21T00:00:00Z type=config
# [OMNI] material_id="material:diagnosis.cleanup_bot.workers_aggregate_exports.py"
"""cleanup_bot Team · 3 Worker 清单 (Stage 3 Clean Migration 2026-04-21).

每个 Worker 独立文件, 无 Diamond shortcut, _archive 不再被 workers import。

链路: cleanup.input → EvidenceGathererWorker → cleanup.evidence
          → AnomalyDetectorWorker → cleanup.plan
          → RollbackPlannerWorker → cleanup.done
"""
from __future__ import annotations

from omnifactory.packages.services._core.omnicompany import Worker

from .anomaly_detector_worker import AnomalyDetectorWorker
from .evidence_gatherer_worker import EvidenceGathererWorker
from .rollback_planner_worker import RollbackPlannerWorker

ALL_WORKERS: list[type[Worker]] = [
    EvidenceGathererWorker,
    AnomalyDetectorWorker,
    RollbackPlannerWorker,
]

__all__ = [
    "ALL_WORKERS",
    "EvidenceGathererWorker",
    "AnomalyDetectorWorker",
    "RollbackPlannerWorker",
]
