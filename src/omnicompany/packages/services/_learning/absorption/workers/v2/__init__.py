# [OMNI] origin=claude-code domain=services/absorption/workers/v2 ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:learning.absorption.worker.v2_registry.aggregator.py"
"""absorption V2 问题驱动深读子域 · 7 Worker 清单 (Clean Migration 2026-04-20).

V2 问题驱动管线 (build_v2_pipeline) 的 Worker:
  recon_scout → intersection_planner → human_approval_gate →
  directed_reader → coverage_auditor → synthesis → report_writer_v2
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker

from .recon_scout import ReconScoutV2Worker
from .intersection_planner import IntersectionPlannerV2Worker
from .human_approval_gate import HumanApprovalGateV2Worker
from .directed_reader import DirectedReaderV2Worker
from .coverage_auditor import CoverageAuditorV2Worker
from .synthesis import SynthesisV2Worker
from .report_writer import ReportWriterV2Worker


ALL_WORKERS_V2: list[type[Worker]] = [
    ReconScoutV2Worker,
    IntersectionPlannerV2Worker,
    HumanApprovalGateV2Worker,
    DirectedReaderV2Worker,
    CoverageAuditorV2Worker,
    SynthesisV2Worker,
    ReportWriterV2Worker,
]


__all__ = [
    "ReconScoutV2Worker",
    "IntersectionPlannerV2Worker",
    "HumanApprovalGateV2Worker",
    "DirectedReaderV2Worker",
    "CoverageAuditorV2Worker",
    "SynthesisV2Worker",
    "ReportWriterV2Worker",
    "ALL_WORKERS_V2",
]
