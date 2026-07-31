# [OMNI] origin=claude-code domain=services/absorption/run.py ts=2026-04-20T00:00:00Z
# [OMNI] material_id="material:learning.absorption.pipeline_bindings_orchestrator.py"
"""absorption.run — Repo Absorption 管线的 Worker bindings 入口 (Clean Migration 2026-04-20).

被 core/pipelines.py 通过 _lazy() 引用. Worker 的 import 延迟到 build_*_bindings()
内部, 避免 CLI 启动时拉重依赖 (LLMClient / AgentNodeLoop).

Bindings 使用**新 Worker 类名** (如 TargetIntakeWorker). 保留的 AgentNodeLoop 类
(LandmarkPickerRouter) 不迁, 继续从原位 import.
"""

from __future__ import annotations

from typing import Any

from omnicompany.runtime.routing.router import Router

from omnicompany.packages.services._learning.absorption.pipeline import (
    build_survey_pipeline,
    build_v2_pipeline,
    build_v3_pipeline,
    build_v3_stage3_pipeline,
)

__all__ = [
    "build_survey_pipeline", "build_survey_bindings",
    "build_v2_pipeline", "build_v2_bindings",
    "build_v3_pipeline", "build_v3_bindings",
    "build_v3_stage3_pipeline", "build_v3_stage3_bindings",
]


def build_survey_bindings(
    input_dict: dict[str, Any] | None = None,
) -> dict[str, Router]:
    """V1 Survey 7 节点管线的 bindings.

    lazy import: Worker 实现在调用时载入, 避免 import 时拉 LLMClient/AgentNodeLoop.
    """
    from omnicompany.packages.services._learning.absorption.workers.v1 import (
        TargetIntakeWorker,
        RepoFacadeFetcherWorker,
        OmnicompanySnapshotFetcherWorker,
        CoverageAuditorWorker,
        TriageGateWorker,
        ReportWriterWorker,
    )
    from omnicompany.packages.services._learning.absorption.landmark_picker import (
        LandmarkPickerRouter,
    )

    return {
        "target_intake": TargetIntakeWorker(),
        "repo_facade_fetcher": RepoFacadeFetcherWorker(),
        "omnicompany_snapshot_fetcher": OmnicompanySnapshotFetcherWorker(),
        "landmark_picker": LandmarkPickerRouter(),  # AgentNodeLoop, 不迁
        "coverage_auditor": CoverageAuditorWorker(),
        "triage_gate": TriageGateWorker(),
        "report_writer": ReportWriterWorker(),
    }


def build_v2_bindings(
    input_dict: dict[str, Any] | None = None,
) -> dict[str, Router]:
    """V2 问题驱动管线的 bindings.

    lazy import: Worker 实现在调用时载入.
    Phase 2/3/4 会逐步替换 STUB 为真实实现.
    """
    from omnicompany.packages.services._learning.absorption.workers.v2 import (
        ReconScoutV2Worker,
        IntersectionPlannerV2Worker,
        HumanApprovalGateV2Worker,
        DirectedReaderV2Worker,
        CoverageAuditorV2Worker,
        SynthesisV2Worker,
        ReportWriterV2Worker,
    )

    return {
        "recon_scout": ReconScoutV2Worker(),
        "intersection_planner": IntersectionPlannerV2Worker(),
        "human_approval_gate": HumanApprovalGateV2Worker(),
        "directed_reader": DirectedReaderV2Worker(),
        "coverage_auditor": CoverageAuditorV2Worker(),
        "synthesis": SynthesisV2Worker(),
        "report_writer_v2": ReportWriterV2Worker(),
    }


def build_v3_bindings(
    input_dict: dict[str, Any] | None = None,
) -> dict[str, Router]:
    """V3 模块驱动管线的 bindings (主路径 + wiki 三路 fan-in + 补充反馈循环).

    主路径 (含 2026-04-18 wiki 三路 fan-in 改造):
      RepoMapper → {module_explorer, capability_query_builder,
                     gap_query_builder, reception_query_builder}
      capability_query_builder → capability_loader → module_explorer
      gap_query_builder → gap_loader → module_explorer
      reception_query_builder → reception_loader → module_explorer
      module_explorer → LearningExtractor → ReportWriter → HumanFeedbackGate → FeedbackRouter
      FeedbackRouter 可 JUMP 回 supplement_explorer (补充学习独立路径)
    """
    from omnicompany.packages.services._learning.absorption.workers.v3 import (
        RepoMapperWorker,
        ModuleExplorerWorker,
        LearningExtractorWorker,
        ReportWriterV3Worker,
        HumanFeedbackGateV3Worker,
        FeedbackRouterV3Worker,
        ReportUpdaterV3Worker,
        CapabilityInventoryQueryBuilderWorker,
        CapabilityInventoryLoaderWorker,
        GapRegistryQueryBuilderWorker,
        GapRegistryLoaderWorker,
        ReceptionIntentsQueryBuilderWorker,
        ReceptionIntentsLoaderWorker,
    )

    return {
        # 主路径
        "repo_mapper": RepoMapperWorker(),
        # wiki 三路 fan-in 链 (2026-04-18 新增)
        "capability_query_builder": CapabilityInventoryQueryBuilderWorker(),
        "capability_loader": CapabilityInventoryLoaderWorker(),
        "gap_query_builder": GapRegistryQueryBuilderWorker(),
        "gap_loader": GapRegistryLoaderWorker(),
        "reception_query_builder": ReceptionIntentsQueryBuilderWorker(),
        "reception_loader": ReceptionIntentsLoaderWorker(),
        # 模块探索 → 主线
        "module_explorer": ModuleExplorerWorker(),
        "learning_extractor": LearningExtractorWorker(),
        "report_writer": ReportWriterV3Worker(),
        # 反馈回路 (共用 human_feedback_gate, feedback_router)
        "human_feedback_gate": HumanFeedbackGateV3Worker(),
        "feedback_router": FeedbackRouterV3Worker(),
        # 补充探索路径 (与主路径隔离)
        "supplement_explorer": ModuleExplorerWorker(),      # 复用同类, 不同节点 ID
        "supplement_extractor": LearningExtractorWorker(),  # 复用同类, 不同节点 ID
        "report_updater": ReportUpdaterV3Worker(),
    }


def build_v3_stage3_bindings(
    input_dict: dict[str, Any] | None = None,
) -> dict[str, Router]:
    """V3 Stage 3 工作流修改管线 bindings.

    2026-04-18 升级:
    - 加入知识 fan-in 链 (P-13 / F-15 示范)
    - 加入 feedback 回路 (proposal_feedback_gate + proposal_feedback_router)
    9 节点: entry_bootstrap + 4 knowledge loader + spec_parser +
            proposal_feedback_gate + proposal_feedback_router + human_approval_gate_s3
    """
    from omnicompany.packages.services._learning.absorption.workers.v3 import (
        SpecParserWorker,
        HumanApprovalGateS3Worker,
        ProposalFeedbackGateWorker,
        ProposalFeedbackRouterWorker,
        Stage3EntryBootstrapWorker,
        CapabilityInventoryQueryBuilderWorker,
        CapabilityInventoryLoaderWorker,
        GapRegistryQueryBuilderWorker,
        GapRegistryLoaderWorker,
    )

    return {
        "entry_bootstrap": Stage3EntryBootstrapWorker(),
        "capability_query_builder": CapabilityInventoryQueryBuilderWorker(),
        "capability_loader": CapabilityInventoryLoaderWorker(),
        "gap_query_builder": GapRegistryQueryBuilderWorker(),
        "gap_loader": GapRegistryLoaderWorker(),
        "spec_parser": SpecParserWorker(),
        "proposal_feedback_gate": ProposalFeedbackGateWorker(),
        "proposal_feedback_router": ProposalFeedbackRouterWorker(),
        "human_approval_gate_s3": HumanApprovalGateS3Worker(),
    }
