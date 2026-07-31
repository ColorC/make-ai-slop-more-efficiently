# [OMNI] origin=claude-code domain=services/absorption ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:learning.absorption.package_entry_aggregator.py"
"""omnicompany.packages.services._learning.absorption — Repo Absorption Team (Clean Migration 2026-04-20).

把外部 GitHub agent/AI 框架仓库系统化地"模仿 → 抄写 → 反省 → 利用 → 吸纳"
为纯六元 LAP 代码进入 OmniCompany. 三代管线 (V1 Survey / V2 问题驱动 / V3 模块驱动 + Stage 3 提案).

命名兼容 (terminology §6):
  - protocol 层: Router / Format / TeamSpec (不变)
  - omnicompany 层 (叙述): Worker / Material / Team (新)
  本 Team 采 Clean Migration Diamond shortcut: workers/*.py 继承 (Worker, _Legacy)
  业务代码保留在 _archive/routers_v1v2_legacy.py + _archive/routers_v3_legacy/.

设计文档:
  DESIGN.md (active)
  docs/plans/[2026-04-13]REPO-ABSORPTION-V2/
  docs/plans/[2026-04-13]REPO-ABSORPTION-V3/
  docs/plans/[2026-04-14]STAGE3-WORKFLOW-MODIFIER/
"""

from __future__ import annotations

# ─── Format / Material 定义 ──────────────────────────────────────────────
from omnicompany.packages.services._learning.absorption.formats import (
    ABSORPTION_USER_REQUEST,
    ABSORPTION_INTAKE,
    ABSORPTION_FACADE_CARD,
    ABSORPTION_OMNICOMPANY_SNAPSHOT,
    ABSORPTION_LANDMARK_LIST,
    ABSORPTION_COVERAGE_AUDIT,
    ABSORPTION_TRIAGED_LANDMARKS,
    ABSORPTION_REPORT,
    ALL_FORMATS,
    register_formats,
)

# ─── Pipeline builders ───────────────────────────────────────────────────
from omnicompany.packages.services._learning.absorption.pipeline import (
    build_survey_pipeline,
    PIPELINES,
)
from omnicompany.packages.services._learning.absorption.run import build_survey_bindings

# ─── Workers (新, 推荐) ───────────────────────────────────────────────────
from omnicompany.packages.services._learning.absorption.workers import (
    ALL_WORKERS,
    ALL_WORKERS_V1,
    ALL_WORKERS_V2,
    ALL_WORKERS_V3,
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

# ─── Legacy Router names (旧, 兼容) ──────────────────────────────────────
from omnicompany.packages.services._learning.absorption.routers import (
    # V1
    TargetIntakeRouter,
    RepoFacadeFetcherRouter,
    OmnicompanySnapshotFetcherRouter,
    CoverageAuditorRouter,
    TriageGateRouter,
    ReportWriterRouter,
    # V2
    ReconScoutV2Router,
    IntersectionPlannerV2Router,
    HumanApprovalGateV2Router,
    DirectedReaderV2Router,
    CoverageAuditorV2Router,
    SynthesisV2Router,
    ReportWriterV2Router,
    # V3
    Stage3EntryBootstrapRouter,
    CapabilityInventoryQueryBuilderRouter,
    CapabilityInventoryLoaderRouter,
    GapRegistryQueryBuilderRouter,
    GapRegistryLoaderRouter,
    ReceptionIntentsQueryBuilderRouter,
    ReceptionIntentsLoaderRouter,
    ModuleExplorerRouter,
    ModulePickerRouter,
    ModuleReaderRouter,
    LearningExtractorRouter,
    RepoMapperRouter,
    ReportWriterV3Router,
    HumanFeedbackGateV3Router,
    FeedbackRouterV3,
    ReportUpdaterV3Router,
    SpecParserRouter,
    HumanApprovalGateS3Router,
    ProposalFeedbackGateRouter,
    ProposalFeedbackRouterRouter,
    ProposalDisputeLoopRouter,
)

__all__ = [
    # Materials
    "ABSORPTION_USER_REQUEST",
    "ABSORPTION_INTAKE",
    "ABSORPTION_FACADE_CARD",
    "ABSORPTION_OMNICOMPANY_SNAPSHOT",
    "ABSORPTION_LANDMARK_LIST",
    "ABSORPTION_COVERAGE_AUDIT",
    "ABSORPTION_TRIAGED_LANDMARKS",
    "ABSORPTION_REPORT",
    "ALL_FORMATS",
    "register_formats",
    # Pipelines
    "build_survey_pipeline",
    "build_survey_bindings",
    "PIPELINES",
    # Workers manifest
    "ALL_WORKERS",
    "ALL_WORKERS_V1",
    "ALL_WORKERS_V2",
    "ALL_WORKERS_V3",
    # Workers (new, 34)
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
    # Legacy Router names (backward compat, 34)
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
