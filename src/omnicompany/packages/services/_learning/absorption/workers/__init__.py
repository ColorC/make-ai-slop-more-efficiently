# [OMNI] origin=claude-code domain=services/absorption/workers ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:learning.absorption.worker_master_registry.py"
"""absorption Team · 34 Worker 清单 (Clean Migration 2026-04-20).

三子域划分 (按管线代次):
  - v1/  (6 Worker) · Survey & Triage  (build_survey_pipeline)
  - v2/  (7 Worker) · 问题驱动深读     (build_v2_pipeline)
  - v3/  (21 Worker) · 模块驱动学习 + Stage 2 反馈 + Stage 3 提案
          └── knowledge_loaders/ (7) wiki 三路 fan-in + Stage 3 entry

注: AgentNodeLoop 类 **不迁**, 保留在原位 / _archive/:
  - landmark_picker.LandmarkPickerRouter      (V1, 整个类是 AgentNodeLoop, 原位保留)
  - _ReconLoop (内嵌于 ReconScoutV2Worker)
  - _DirectedReaderLoop (内嵌于 DirectedReaderV2Worker)
  - _ExplorerLoop (内嵌于 ModuleExplorerWorker)
  - _DisputeLoop (内嵌于 ProposalDisputeLoopWorker)
阶段 D AGENT-NODE-LOOP-ROUTERIZATION 落地后再统一迁移.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker

from .v1 import (
    ALL_WORKERS_V1,
    TargetIntakeWorker,
    RepoFacadeFetcherWorker,
    OmnicompanySnapshotFetcherWorker,
    CoverageAuditorWorker,
    TriageGateWorker,
    ReportWriterWorker,
)
from .v2 import (
    ALL_WORKERS_V2,
    ReconScoutV2Worker,
    IntersectionPlannerV2Worker,
    HumanApprovalGateV2Worker,
    DirectedReaderV2Worker,
    CoverageAuditorV2Worker,
    SynthesisV2Worker,
    ReportWriterV2Worker,
)
from .v3 import (
    ALL_WORKERS_V3,
    # knowledge_loaders (7)
    Stage3EntryBootstrapWorker,
    CapabilityInventoryQueryBuilderWorker,
    CapabilityInventoryLoaderWorker,
    GapRegistryQueryBuilderWorker,
    GapRegistryLoaderWorker,
    ReceptionIntentsQueryBuilderWorker,
    ReceptionIntentsLoaderWorker,
    # V3 main (5)
    ModuleExplorerWorker,
    ModulePickerWorker,
    ModuleReaderWorker,
    LearningExtractorWorker,
    RepoMapperWorker,
    # V3 report/feedback (4)
    ReportWriterV3Worker,
    HumanFeedbackGateV3Worker,
    FeedbackRouterV3Worker,
    ReportUpdaterV3Worker,
    # V3 Stage 3 (5)
    SpecParserWorker,
    HumanApprovalGateS3Worker,
    ProposalFeedbackGateWorker,
    ProposalFeedbackRouterWorker,
    ProposalDisputeLoopWorker,
)


ALL_WORKERS: list[type[Worker]] = (
    ALL_WORKERS_V1 + ALL_WORKERS_V2 + ALL_WORKERS_V3
)


__all__ = [
    # Subdomain manifests
    "ALL_WORKERS_V1",
    "ALL_WORKERS_V2",
    "ALL_WORKERS_V3",
    "ALL_WORKERS",
    # V1 (6)
    "TargetIntakeWorker",
    "RepoFacadeFetcherWorker",
    "OmnicompanySnapshotFetcherWorker",
    "CoverageAuditorWorker",
    "TriageGateWorker",
    "ReportWriterWorker",
    # V2 (7)
    "ReconScoutV2Worker",
    "IntersectionPlannerV2Worker",
    "HumanApprovalGateV2Worker",
    "DirectedReaderV2Worker",
    "CoverageAuditorV2Worker",
    "SynthesisV2Worker",
    "ReportWriterV2Worker",
    # V3 knowledge loaders (7)
    "Stage3EntryBootstrapWorker",
    "CapabilityInventoryQueryBuilderWorker",
    "CapabilityInventoryLoaderWorker",
    "GapRegistryQueryBuilderWorker",
    "GapRegistryLoaderWorker",
    "ReceptionIntentsQueryBuilderWorker",
    "ReceptionIntentsLoaderWorker",
    # V3 main (5)
    "ModuleExplorerWorker",
    "ModulePickerWorker",
    "ModuleReaderWorker",
    "LearningExtractorWorker",
    "RepoMapperWorker",
    # V3 report/feedback (4)
    "ReportWriterV3Worker",
    "HumanFeedbackGateV3Worker",
    "FeedbackRouterV3Worker",
    "ReportUpdaterV3Worker",
    # V3 Stage 3 (5)
    "SpecParserWorker",
    "HumanApprovalGateS3Worker",
    "ProposalFeedbackGateWorker",
    "ProposalFeedbackRouterWorker",
    "ProposalDisputeLoopWorker",
]
