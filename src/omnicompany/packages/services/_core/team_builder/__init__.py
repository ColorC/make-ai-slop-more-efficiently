# [OMNI] origin=claude-code domain=workflow_factory/__init__.py ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:core.team_builder.package_aggregate.exports.py"
"""workflow_factory — 造工作流的工作流 (Clean Migration 2026-04-20)

元管线: 输入自然语言需求 → 输出通过全部验证的 LAP-native 工作流代码.

拓扑 (13 Worker + 1 AgentNodeLoop):
  设计链:       req_analyzer → format_designer → node_planner → node_plan_auditor
  上下文注入:    framework_context_loader (composite fan-in: node_plan + format_chain)
  代码生成:      code_gen_loop (AgentNodeLoop · write_file/py_compile/read_written_file)
  验证链:        compile_checker → error_route_auditor → integration_tester
                 (2026-07-03 批4: lap_verifier 九维检查器显式废止, 已从验证链摘除)
  修复链:        deterministic_fixer (L1) → syntax_fixer (L2) → auto_fixer (L3)
  最终化:        finalizer (EMIT wf.done)

Clean Migration 硬规则:
  - 14 Worker 都继承自 omnicompany.Worker (见 workers/)
  - 每条 Material 标 kind.source / kind.internal / kind.sink (见 formats.py)
  - 旧 *Router 名通过 routers.py shim 保留兼容
  - legacy 业务逻辑暂存 _archive/routers_legacy.py (Diamond shortcut)
"""
from __future__ import annotations

# ─── Worker 类 (Clean Migration 新名, 推荐) ────────────────────────────
from .workers import (
    ALL_WORKERS,
    ReqAnalyzerWorker,
    FormatDesignerWorker,
    NodePlannerWorker,
    NodePlanAuditorWorker,
    FrameworkContextLoaderWorker,
    CodeGenFormatsWorker,
    CodeGenPipelineWorker,
    CodeGenRoutersWorker,
    CodeGenRunWorker,
    SyntaxFixerWorker,
    DeterministicFixerWorker,
    AutoFixerWorker,
    CompileCheckerWorker,
    ErrorRouteAuditorWorker,
    IntegrationTesterWorker,
    FinalizerWorker,
)

# ─── Material 定义 (Clean Migration 新名) ──────────────────────────────
from .formats import (
    ALL_FORMATS,
    ALL_MATERIALS,
    register_formats,
)

# ─── 旧名兼容 shim (routers.py 转发) ───────────────────────────────────
from .routers import (
    ReqAnalyzerRouter,
    FormatDesignerRouter,
    NodePlannerRouter,
    NodePlanAuditorRouter,
    FrameworkContextLoaderRouter,
    CodeGenFormatsRouter,
    CodeGenPipelineRouter,
    CodeGenRoutersRouter,
    CodeGenRunRouter,
    SyntaxFixerRouter,
    DeterministicFixerRouter,
    AutoFixerRouter,
    CompileCheckerRouter,
    ErrorRouteAuditorRouter,
    IntegrationTesterRouter,
    FinalizerRouter,
)

# AgentNodeLoop (本次 Clean Migration 不迁, 仅 re-export)
from .routers_codegen import CodeGenLoop


__all__ = [
    # Workers (新名)
    "ALL_WORKERS",
    "ReqAnalyzerWorker",
    "FormatDesignerWorker",
    "NodePlannerWorker",
    "NodePlanAuditorWorker",
    "FrameworkContextLoaderWorker",
    "CodeGenFormatsWorker",
    "CodeGenPipelineWorker",
    "CodeGenRoutersWorker",
    "CodeGenRunWorker",
    "SyntaxFixerWorker",
    "DeterministicFixerWorker",
    "AutoFixerWorker",
    "CompileCheckerWorker",
    "ErrorRouteAuditorWorker",
    "IntegrationTesterWorker",
    "FinalizerWorker",
    # Materials
    "ALL_FORMATS",
    "ALL_MATERIALS",
    "register_formats",
    # 旧名 Router 兼容
    "ReqAnalyzerRouter",
    "FormatDesignerRouter",
    "NodePlannerRouter",
    "NodePlanAuditorRouter",
    "FrameworkContextLoaderRouter",
    "CodeGenFormatsRouter",
    "CodeGenPipelineRouter",
    "CodeGenRoutersRouter",
    "CodeGenRunRouter",
    "SyntaxFixerRouter",
    "DeterministicFixerRouter",
    "AutoFixerRouter",
    "CompileCheckerRouter",
    "ErrorRouteAuditorRouter",
    "IntegrationTesterRouter",
    "FinalizerRouter",
    # AgentNodeLoop
    "CodeGenLoop",
]
