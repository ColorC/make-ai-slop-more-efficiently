# [OMNI] origin=claude-code domain=services/absorption/routers ts=2026-04-20T00:00:00Z type=shim
# [OMNI] material_id="material:learning.absorption.router_compat_shim.aggregator.py"
"""absorption/routers/ — 向后兼容 shim package (Clean Migration 2026-04-20).

真实 Worker 实现已迁到 `workers/v1/`, `workers/v2/`, `workers/v3/`.
本 package 仅为旧 import 路径保留兼容:
  - 旧名 FooRouter → 新名 FooWorker (别名 = FooWorker 类, Diamond 继承)
  - 旧 `from ...absorption.routers import TargetIntakeRouter` 继续工作
  - 旧 `from ...absorption.routers.module_explorer import ModuleExplorerRouter` 继续工作
    (子模块 shim, 见 `routers/*.py`)

不要往本 package 加新逻辑; 新增 Worker 请直接写 `workers/<subdomain>/<name>.py`.
归档: `_archive/routers_v1v2_legacy.py` + `_archive/routers_v3_legacy/` 保留旧实现.
"""
from __future__ import annotations

# ─── Worker 类 (推荐新代码使用) ──────────────────────────────────────────
from ..workers import (
    # V1
    TargetIntakeWorker,
    RepoFacadeFetcherWorker,
    OmnicompanySnapshotFetcherWorker,
    CoverageAuditorWorker,
    TriageGateWorker,
    ReportWriterWorker,
    # V2
    ReconScoutV2Worker,
    IntersectionPlannerV2Worker,
    HumanApprovalGateV2Worker,
    DirectedReaderV2Worker,
    CoverageAuditorV2Worker,
    SynthesisV2Worker,
    ReportWriterV2Worker,
    # V3 knowledge loaders
    Stage3EntryBootstrapWorker,
    CapabilityInventoryQueryBuilderWorker,
    CapabilityInventoryLoaderWorker,
    GapRegistryQueryBuilderWorker,
    GapRegistryLoaderWorker,
    ReceptionIntentsQueryBuilderWorker,
    ReceptionIntentsLoaderWorker,
    # V3 main
    ModuleExplorerWorker,
    ModulePickerWorker,
    ModuleReaderWorker,
    LearningExtractorWorker,
    RepoMapperWorker,
    # V3 report/feedback
    ReportWriterV3Worker,
    HumanFeedbackGateV3Worker,
    FeedbackRouterV3Worker,
    ReportUpdaterV3Worker,
    # V3 Stage 3
    SpecParserWorker,
    HumanApprovalGateS3Worker,
    ProposalFeedbackGateWorker,
    ProposalFeedbackRouterWorker,
    ProposalDisputeLoopWorker,
)


# ─── 旧 Router 名别名 (兼容外部调用者) ───────────────────────────────────
# 注意: 旧 FooRouter ≡ 新 FooWorker (Diamond 继承自 _archive.*Legacy, 等价类)

# V1
TargetIntakeRouter = TargetIntakeWorker
RepoFacadeFetcherRouter = RepoFacadeFetcherWorker
OmnicompanySnapshotFetcherRouter = OmnicompanySnapshotFetcherWorker
CoverageAuditorRouter = CoverageAuditorWorker
TriageGateRouter = TriageGateWorker
ReportWriterRouter = ReportWriterWorker

# V2
ReconScoutV2Router = ReconScoutV2Worker
IntersectionPlannerV2Router = IntersectionPlannerV2Worker
HumanApprovalGateV2Router = HumanApprovalGateV2Worker
DirectedReaderV2Router = DirectedReaderV2Worker
CoverageAuditorV2Router = CoverageAuditorV2Worker
SynthesisV2Router = SynthesisV2Worker
ReportWriterV2Router = ReportWriterV2Worker

# V3 knowledge_loaders
Stage3EntryBootstrapRouter = Stage3EntryBootstrapWorker
CapabilityInventoryQueryBuilderRouter = CapabilityInventoryQueryBuilderWorker
CapabilityInventoryLoaderRouter = CapabilityInventoryLoaderWorker
GapRegistryQueryBuilderRouter = GapRegistryQueryBuilderWorker
GapRegistryLoaderRouter = GapRegistryLoaderWorker
ReceptionIntentsQueryBuilderRouter = ReceptionIntentsQueryBuilderWorker
ReceptionIntentsLoaderRouter = ReceptionIntentsLoaderWorker

# V3 main
ModuleExplorerRouter = ModuleExplorerWorker
ModulePickerRouter = ModulePickerWorker
ModuleReaderRouter = ModuleReaderWorker
LearningExtractorRouter = LearningExtractorWorker
RepoMapperRouter = RepoMapperWorker

# V3 report/feedback
ReportWriterV3Router = ReportWriterV3Worker
HumanFeedbackGateV3Router = HumanFeedbackGateV3Worker
# 注: 旧类名就叫 `FeedbackRouterV3` (不带 Router 后缀), 保留原样
FeedbackRouterV3 = FeedbackRouterV3Worker
ReportUpdaterV3Router = ReportUpdaterV3Worker

# V3 Stage 3
SpecParserRouter = SpecParserWorker
HumanApprovalGateS3Router = HumanApprovalGateS3Worker
ProposalFeedbackGateRouter = ProposalFeedbackGateWorker
ProposalFeedbackRouterRouter = ProposalFeedbackRouterWorker
ProposalDisputeLoopRouter = ProposalDisputeLoopWorker


__all__ = [
    # New names (34)
    "TargetIntakeWorker",
    "RepoFacadeFetcherWorker",
    "OmnicompanySnapshotFetcherWorker",
    "CoverageAuditorWorker",
    "TriageGateWorker",
    "ReportWriterWorker",
    "ReconScoutV2Worker",
    "IntersectionPlannerV2Worker",
    "HumanApprovalGateV2Worker",
    "DirectedReaderV2Worker",
    "CoverageAuditorV2Worker",
    "SynthesisV2Worker",
    "ReportWriterV2Worker",
    "Stage3EntryBootstrapWorker",
    "CapabilityInventoryQueryBuilderWorker",
    "CapabilityInventoryLoaderWorker",
    "GapRegistryQueryBuilderWorker",
    "GapRegistryLoaderWorker",
    "ReceptionIntentsQueryBuilderWorker",
    "ReceptionIntentsLoaderWorker",
    "ModuleExplorerWorker",
    "ModulePickerWorker",
    "ModuleReaderWorker",
    "LearningExtractorWorker",
    "RepoMapperWorker",
    "ReportWriterV3Worker",
    "HumanFeedbackGateV3Worker",
    "FeedbackRouterV3Worker",
    "ReportUpdaterV3Worker",
    "SpecParserWorker",
    "HumanApprovalGateS3Worker",
    "ProposalFeedbackGateWorker",
    "ProposalFeedbackRouterWorker",
    "ProposalDisputeLoopWorker",
    # Legacy names (backward compat aliases)
    "TargetIntakeRouter",
    "RepoFacadeFetcherRouter",
    "OmnicompanySnapshotFetcherRouter",
    "CoverageAuditorRouter",
    "TriageGateRouter",
    "ReportWriterRouter",
    "ReconScoutV2Router",
    "IntersectionPlannerV2Router",
    "HumanApprovalGateV2Router",
    "DirectedReaderV2Router",
    "CoverageAuditorV2Router",
    "SynthesisV2Router",
    "ReportWriterV2Router",
    "Stage3EntryBootstrapRouter",
    "CapabilityInventoryQueryBuilderRouter",
    "CapabilityInventoryLoaderRouter",
    "GapRegistryQueryBuilderRouter",
    "GapRegistryLoaderRouter",
    "ReceptionIntentsQueryBuilderRouter",
    "ReceptionIntentsLoaderRouter",
    "ModuleExplorerRouter",
    "ModulePickerRouter",
    "ModuleReaderRouter",
    "LearningExtractorRouter",
    "RepoMapperRouter",
    "ReportWriterV3Router",
    "HumanFeedbackGateV3Router",
    "FeedbackRouterV3",
    "ReportUpdaterV3Router",
    "SpecParserRouter",
    "HumanApprovalGateS3Router",
    "ProposalFeedbackGateRouter",
    "ProposalFeedbackRouterRouter",
    "ProposalDisputeLoopRouter",
]
