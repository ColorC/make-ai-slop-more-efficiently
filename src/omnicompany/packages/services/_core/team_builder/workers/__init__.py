# [OMNI] origin=claude-code domain=omnicompany/workflow_factory ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:team_builder.workers.package_aggregator.exports.py"
"""workflow_factory Team · 14 Worker 清单 (Clean Migration 2026-04-20).

拆分策略 (扁平):
  - 设计阶段 (4): req_analyzer / format_designer / node_planner / node_plan_auditor
  - 上下文注入 (1): framework_context_loader
  - 代码生成 (4 per-file fallback): code_generator (含 4 子 Worker, 当前管线默认用
    CodeGenLoop AgentNodeLoop, 见 ../routers_codegen.py)
  - 修复 (3): syntax_fixer / deterministic_fixer / auto_fixer
  - 验证/最终化 (3): compile_checker / error_route_auditor / integration_tester /
    finalizer
    (2026-07-03 批4: LAP 九维检查器显式废止, 已从验证链摘除, 实现体留归档
     _archive/routers_legacy.py 不删。见 DESIGN.md 废止说明。)

Diamond 继承模式 (2026-07-26 OMNI-040 Stage 3: 业务代码已迁回正式位置
../routers_legacy.py, `_archive/` 仅留废止实现体作历史参考).
所有 Worker 都从 omnicompany.Worker 继承链挂入.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker

from .req_analyzer import ReqAnalyzerWorker
from .format_designer import FormatDesignerWorker
from .node_planner import NodePlannerWorker
from .framework_context_loader import FrameworkContextLoaderWorker
from .node_plan_auditor import NodePlanAuditorWorker
from .code_generator import (
    CodeGenFormatsWorker,
    CodeGenPipelineWorker,
    CodeGenRoutersWorker,
    CodeGenRunWorker,
)
from .syntax_fixer import SyntaxFixerWorker
from .compile_checker import CompileCheckerWorker
from .error_route_auditor import ErrorRouteAuditorWorker
from .integration_tester import IntegrationTesterWorker
from .deterministic_fixer import DeterministicFixerWorker
from .auto_fixer import AutoFixerWorker
from .finalizer import FinalizerWorker

# ── A3 V1 agent-first 起步 (2026-04-23 · 4 worker 已跑通 E2E) ──
from .intent_analyzer import IntentAnalyzerWorker
from .origin_request_loader import OriginRequestLoaderWorker
from .reference_scout import ReferenceScoutWorker
from .team_architect import TeamArchitectWorker

# ── A3 V2 深化 (2026-04-23 · 7 worker 已单测 PASS · 待串拓扑) ──
# 2 HARD: WorkspaceDesigner / ContractAuditor
# 5 AgentNodeLoop: ScaleAssessor / MaterialDesigner / WorkerDesigner
#                  / DesignValidator / DecompositionPlanner
from .contract_auditor import ContractAuditorWorker
from .decomposition_planner import DecompositionPlannerWorker
from .design_validator import DesignValidatorWorker
from .material_designer import MaterialDesignerWorker
from .scale_assessor import ScaleAssessorWorker
from .worker_designer import WorkerDesignerWorker
from .workspace_designer import WorkspaceDesignerWorker

# ── A3 V3 · Phase 8/10 (2026-04-23 · 代码生成 + 注册 dry_run) ──
from .team_code_generator import CodeGeneratorLoopWorker
from .registrar import RegistrarWorker

# ── A3 V3.2 · CodeGenerator 子 team (2026-04-24 · 分形重构) ──
# 6 HARD 模板 + 2 SOFT (bundle + md) + 1 Aggregator
from .code_gen_hard import (
    FormatsFileGenerator,
    TeamFileGenerator,
    RunFileGenerator,
    PackageInitGenerator,
    WorkersInitGenerator,
    WorkspaceYamlGenerator,
)
from .code_gen_soft import WorkerCodeOrchestrator, DesignMdGenerator
from .code_aggregator import CodeAggregator
from .code_reviewer import CodeReviewer


# 14 "逻辑" Worker 粒度 (对齐任务 brief 声明):
#  - code_generator 子域实际有 4 文件级 Worker (per-file fallback, 未被主拓扑使用),
#    清单里合并计为 1 个类别保持 14 = 14 的心智模型, 但 export / ALL_WORKERS 暴露全部 4 个.
ALL_WORKERS: list[type[Worker]] = [
    # 设计阶段 (4)
    ReqAnalyzerWorker,
    FormatDesignerWorker,
    NodePlannerWorker,
    NodePlanAuditorWorker,
    # 上下文注入 (1)
    FrameworkContextLoaderWorker,
    # 代码生成 (4 per-file fallback, 主拓扑用 ../routers_codegen.CodeGenLoop)
    CodeGenFormatsWorker,
    CodeGenPipelineWorker,
    CodeGenRoutersWorker,
    CodeGenRunWorker,
    # 修复 (3)
    SyntaxFixerWorker,
    DeterministicFixerWorker,
    AutoFixerWorker,
    # 验证 + 最终化 (4)
    CompileCheckerWorker,
    ErrorRouteAuditorWorker,
    IntegrationTesterWorker,
    FinalizerWorker,
    # ── A3 V1 agent-first 起步 (2026-04-23) ──
    OriginRequestLoaderWorker,  # HARD 入口
    IntentAnalyzerWorker,        # SOFT LLM
    ReferenceScoutWorker,        # SOFT 启发式 (v0)
    TeamArchitectWorker,         # SOFT LLM composite fan-in
    # ── A3 V2 深化 (2026-04-23) ──
    ScaleAssessorWorker,         # Phase 2 · AgentNodeLoop
    DecompositionPlannerWorker,  # Phase 2 · AgentNodeLoop (conditional large)
    WorkerDesignerWorker,        # Phase 4 · Orchestrator (N 份 fan-out)
    MaterialDesignerWorker,      # Phase 4' · Orchestrator (M 份 fan-out)
    WorkspaceDesignerWorker,     # Phase 5 · HARD
    ContractAuditorWorker,       # Phase 6 · HARD
    DesignValidatorWorker,       # Phase 7 · AgentNodeLoop (7 维)
    # ── A3 V3 · Phase 8/10 ──
    CodeGeneratorLoopWorker,     # Phase 8 · (V3 legacy) AgentNodeLoop 单体 · 保留备用
    RegistrarWorker,             # Phase 10 · HARD · 产 registration_plan (dry_run)
    # ── A3 V3.2 · CodeGenerator 子 team (2026-04-24 · 分形) ──
    FormatsFileGenerator,
    TeamFileGenerator,
    RunFileGenerator,
    PackageInitGenerator,
    WorkersInitGenerator,
    WorkspaceYamlGenerator,
    WorkerCodeOrchestrator,
    DesignMdGenerator,
    CodeAggregator,
    CodeReviewer,
]


__all__ = [
    # 设计阶段
    "ReqAnalyzerWorker",
    "FormatDesignerWorker",
    "NodePlannerWorker",
    "NodePlanAuditorWorker",
    # 上下文注入
    "FrameworkContextLoaderWorker",
    # 代码生成 per-file fallback
    "CodeGenFormatsWorker",
    "CodeGenPipelineWorker",
    "CodeGenRoutersWorker",
    "CodeGenRunWorker",
    # 修复
    "SyntaxFixerWorker",
    "DeterministicFixerWorker",
    "AutoFixerWorker",
    # 验证 + 最终化
    "CompileCheckerWorker",
    "ErrorRouteAuditorWorker",
    "IntegrationTesterWorker",
    "FinalizerWorker",
    # A3 V1 agent-first 起步
    "OriginRequestLoaderWorker",
    "IntentAnalyzerWorker",
    "ReferenceScoutWorker",
    "TeamArchitectWorker",
    # A3 V2 深化
    "ScaleAssessorWorker",
    "DecompositionPlannerWorker",
    "WorkerDesignerWorker",
    "MaterialDesignerWorker",
    "WorkspaceDesignerWorker",
    "ContractAuditorWorker",
    "DesignValidatorWorker",
    # A3 V3 · Phase 8/10
    "CodeGeneratorLoopWorker",
    "RegistrarWorker",
    # A3 V3.2 · CodeGenerator 子 team (2026-04-24)
    "FormatsFileGenerator",
    "TeamFileGenerator",
    "RunFileGenerator",
    "PackageInitGenerator",
    "WorkersInitGenerator",
    "WorkspaceYamlGenerator",
    "WorkerCodeOrchestrator",
    "DesignMdGenerator",
    "CodeAggregator",
    "CodeReviewer",
    # Manifest
    "ALL_WORKERS",
]
