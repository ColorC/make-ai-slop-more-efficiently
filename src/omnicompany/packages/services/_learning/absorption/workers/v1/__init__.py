# [OMNI] origin=claude-code domain=services/absorption/workers/v1 ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:learning.absorption.worker.v1_registry.aggregator.py"
"""absorption V1 Survey 子域 · 6 Worker 清单 (Clean Migration 2026-04-20).

V1 Survey & Triage 管线 (build_survey_pipeline) 的同步 Worker:
  target_intake → repo_facade_fetcher → omnicompany_snapshot_fetcher →
  [LandmarkPickerRouter (AgentNodeLoop, 保留 landmark_picker.py)] →
  coverage_auditor → triage_gate → report_writer
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker

from .target_intake import TargetIntakeWorker
from .repo_facade_fetcher import RepoFacadeFetcherWorker
from .omnicompany_snapshot_fetcher import OmnicompanySnapshotFetcherWorker
from .coverage_auditor import CoverageAuditorWorker
from .triage_gate import TriageGateWorker
from .report_writer import ReportWriterWorker


ALL_WORKERS_V1: list[type[Worker]] = [
    TargetIntakeWorker,
    RepoFacadeFetcherWorker,
    OmnicompanySnapshotFetcherWorker,
    CoverageAuditorWorker,
    TriageGateWorker,
    ReportWriterWorker,
]


__all__ = [
    "TargetIntakeWorker",
    "RepoFacadeFetcherWorker",
    "OmnicompanySnapshotFetcherWorker",
    "CoverageAuditorWorker",
    "TriageGateWorker",
    "ReportWriterWorker",
    "ALL_WORKERS_V1",
]
