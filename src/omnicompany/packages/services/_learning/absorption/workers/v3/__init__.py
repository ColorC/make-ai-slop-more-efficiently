# [OMNI] origin=claude-code domain=services/absorption/workers/v3 ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:learning.absorption.v3.worker_registry.aggregator.py"
"""absorption V3 模块驱动学习子域 · 20 Worker 清单 (Clean Migration 2026-04-20).

子目录 knowledge_loaders/ (7 Worker) 处理 wiki 三路 fan-in + Stage 3 entry.
顶层 (13 Worker) 覆盖 V3 主路径 + Stage 2 反馈 + Stage 3 提案/审批/dispute.

注: 内嵌 AgentNodeLoop (_ExplorerLoop, _DisputeLoop) 保留在 _archive/, 阶段 D 迁移.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker

from .knowledge_loaders import (
    ALL_WORKERS_V3_KL,
    Stage3EntryBootstrapWorker,
    CapabilityInventoryQueryBuilderWorker,
    CapabilityInventoryLoaderWorker,
    GapRegistryQueryBuilderWorker,
    GapRegistryLoaderWorker,
    ReceptionIntentsQueryBuilderWorker,
    ReceptionIntentsLoaderWorker,
)
from .module_explorer import ModuleExplorerWorker
from .module_picker import ModulePickerWorker
from .module_reader import ModuleReaderWorker
from .learning_extractor import LearningExtractorWorker
from .repo_mapper import RepoMapperWorker
from .report_writer import (
    ReportWriterV3Worker,
    HumanFeedbackGateV3Worker,
    FeedbackRouterV3Worker,
)
from .report_updater import ReportUpdaterV3Worker
from .spec_parser import SpecParserWorker
from .human_approval_gate_s3 import (
    HumanApprovalGateS3Worker,
    ProposalFeedbackGateWorker,
    ProposalFeedbackRouterWorker,
)
from .proposal_dispute_loop import ProposalDisputeLoopWorker


ALL_WORKERS_V3: list[type[Worker]] = ALL_WORKERS_V3_KL + [
    ModuleExplorerWorker,
    ModulePickerWorker,
    ModuleReaderWorker,
    LearningExtractorWorker,
    RepoMapperWorker,
    ReportWriterV3Worker,
    HumanFeedbackGateV3Worker,
    FeedbackRouterV3Worker,
    ReportUpdaterV3Worker,
    SpecParserWorker,
    HumanApprovalGateS3Worker,
    ProposalFeedbackGateWorker,
    ProposalFeedbackRouterWorker,
    ProposalDisputeLoopWorker,
]


__all__ = [
    # Knowledge loaders (7)
    "ALL_WORKERS_V3_KL",
    "Stage3EntryBootstrapWorker",
    "CapabilityInventoryQueryBuilderWorker",
    "CapabilityInventoryLoaderWorker",
    "GapRegistryQueryBuilderWorker",
    "GapRegistryLoaderWorker",
    "ReceptionIntentsQueryBuilderWorker",
    "ReceptionIntentsLoaderWorker",
    # Main V3 path (6)
    "ModuleExplorerWorker",
    "ModulePickerWorker",
    "ModuleReaderWorker",
    "LearningExtractorWorker",
    "RepoMapperWorker",
    # Report/feedback (3)
    "ReportWriterV3Worker",
    "HumanFeedbackGateV3Worker",
    "FeedbackRouterV3Worker",
    "ReportUpdaterV3Worker",
    # Stage 3 (4)
    "SpecParserWorker",
    "HumanApprovalGateS3Worker",
    "ProposalFeedbackGateWorker",
    "ProposalFeedbackRouterWorker",
    "ProposalDisputeLoopWorker",
    # Manifest
    "ALL_WORKERS_V3",
]
