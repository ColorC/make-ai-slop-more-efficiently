# [OMNI] origin=claude-code domain=workflow_factory/run.py ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:core.team_builder.worker_bindings.assembly.py"
"""workflow_factory run — 构建绑定 + 注册 (Clean Migration 2026-04-20).

使用 *Worker (Clean Migration 新名). 旧 *Router 名通过 routers.py shim 仍可用.
"""

from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._core.team_builder.formats import register_formats
from omnicompany.packages.services._core.team_builder.workers import (
    AutoFixerWorker,
    CompileCheckerWorker,
    DeterministicFixerWorker,
    ErrorRouteAuditorWorker,
    FinalizerWorker,
    FormatDesignerWorker,
    FrameworkContextLoaderWorker,
    IntegrationTesterWorker,
    NodePlanAuditorWorker,
    NodePlannerWorker,
    ReqAnalyzerWorker,
    SyntaxFixerWorker,
)
from omnicompany.packages.services._core.team_builder.routers_codegen import CodeGenLoop


def _build_code_gen_loop(*, model: str | None = None) -> CodeGenLoop:
    """构造 CodeGenLoop。

    子 Router (SingleToolRouter 等) 在 __init__ 强制要求 bus, 但 bindings 构建时
    真实 bus 还没创建. 做法: 传一个 MemoryBus 占位符让构造通过; run() 入口 runner
    会把真实 bus 注入 self._bus, `_propagate_bus_to_subrouters` 会把它同步
    到所有子 Router, placeholder 就被覆盖了。
    """
    from omnicompany.bus.memory import MemoryBus
    placeholder = MemoryBus()
    return CodeGenLoop(model=model, role="ide_agent", bus=placeholder)


def build_bindings(input_dict: dict | None = None, *, model: str | None = None) -> dict[str, Worker]:
    """构建 workflow-factory 的节点绑定 (Clean Migration 2026-04-20 · *Worker 名)."""
    from omnicompany.runtime.llm.llm import LLMClient

    # 注册 Material
    from omnicompany.protocol.format import create_builtin_registry
    registry = create_builtin_registry()
    register_formats(registry)

    # LLM 客户端（共享）
    # 2026-04-10: 设计链 (req_analyzer / format_designer / node_planner / syntax_fixer) 和
    # 代码生成链 (code_gen_* / auto_fixer) 全部统一到 qwen3.6-plus.
    # 原来用 role="runtime_main" (→ standard tier → glm-5), 设计链输出体量大
    # (format_chain 6k+ token, node_plan 7k+ token), glm-5 在长输出时不如 qwen3.6-plus
    # 稳定且慢。改成 role="ide_agent" 后整条管线用同一个 quality tier 模型。
    default_model = model or "qwen3.6-plus"
    client = LLMClient(role="ide_agent", max_tokens=8192, model=default_model)

    return {
        # 设计阶段 — LLM Worker (MRO: Worker → Legacy → LLMRouter → Router)
        "req_analyzer": ReqAnalyzerWorker(client=client),
        "format_designer": FormatDesignerWorker(client=client),
        "node_planner": NodePlannerWorker(client=client),
        # P7.8 meta-pipeline 自净: HARD 审 node_plan 语义质量
        "node_plan_auditor": NodePlanAuditorWorker(),

        # 框架上下文注入 (SKILL §3.3 信息源正面清单 / 代码生成类必备)
        # 用 inspect.getsource 把 Router/Verdict/LLMClient/AnchorSpec 等真源码
        # + selftest 参考实现注入到 node_plan.framework_context, 供 code_gen_loop
        # 按字段名精确引用, 不再靠 system prompt 教导。
        "framework_context_loader": FrameworkContextLoaderWorker(),

        # 生成阶段 — 2026-04-19 改为单个 AgentNodeLoop (agent-loop 自验)
        # 用 write_file + py_compile + read_written_file 工具逐文件生成 + 自检
        # 取代旧 4 节点固定顺序 LLM 调用 (解决 routers.py token 截断根因)
        "code_gen_loop": _build_code_gen_loop(model=default_model),

        # 验证阶段 — HARD (P7.3: compile_checker_l2 已删除, 用 feedback 边替代)
        "compile_checker": CompileCheckerWorker(),
        "deterministic_fixer": DeterministicFixerWorker(),  # Level 1 HARD
        "error_route_auditor": ErrorRouteAuditorWorker(),
        "integration_tester": IntegrationTesterWorker(),

        # 验证/修复
        "syntax_fixer": SyntaxFixerWorker(client=client),  # Level 2 SOFT
        # 2026-07-03 批4: lap_verifier 节点(九维检查器)显式废止, 绑定已摘除。
        "auto_fixer": AutoFixerWorker(model=default_model),  # Level 3 LLM fallback

        # 最终化 — HARD
        "finalizer": FinalizerWorker(),
    }


# ═══════════════════════════════════════════════════════════════════════
# A3 agent-first bindings (2026-04-23)
# ═══════════════════════════════════════════════════════════════════════

def build_bindings_agent_first(input_dict: dict | None = None) -> dict[str, Worker]:
    """agent-first team bindings · 11 节点 bus-driven (2026-04-23 V2).

    V1 (4): origin_request_loader (HARD) · intent_analyzer (SOFT LLM) ·
            reference_scout (SOFT v0) · team_architect (SOFT LLM fan-in)

    V2 (7): scale_assessor (AgentNodeLoop) · decomposition_planner (AgentNodeLoop conditional) ·
            workspace_designer (HARD) · worker_designer (AgentNodeLoop) ·
            material_designer (AgentNodeLoop) · contract_auditor (HARD) ·
            design_validator (AgentNodeLoop 7 维)

    所有 LLM Worker 共享 WebBus (workspace=team_builder).
    """
    from omnicompany.packages.services._core.team_builder.workers import (
        # V1
        IntentAnalyzerWorker,
        OriginRequestLoaderWorker,
        ReferenceScoutWorker,
        TeamArchitectWorker,
        # V2
        ContractAuditorWorker,
        DecompositionPlannerWorker,
        DesignValidatorWorker,
        MaterialDesignerWorker,
        ScaleAssessorWorker,
        WorkerDesignerWorker,
        WorkspaceDesignerWorker,
        # V3
        RegistrarWorker,
        # V3.2 · CodeGenerator 子 team (2026-04-24 · 9 新 worker)
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
    )
    from omnicompany.packages.services._core.team_builder.formats import register_formats
    from omnicompany.protocol.format import create_builtin_registry
    from omnicompany.runtime.buses import WebBus, load_workspace

    # 注册 Material (含 A3 V2 的 16 类)
    registry = create_builtin_registry()
    register_formats(registry)

    # 加载 team_builder workspace (从 yaml)
    try:
        workspace = load_workspace(
            "src/omnicompany/packages/services/team_builder/.omni/workspace.yaml"
        )
    except Exception:
        workspace = None  # 容错 · bus 回退 fallback 模式

    # 共享 WebBus 给 V1 LLM agent worker (V2 agent 暂用内置 MemoryBus, 不对接 WebBus audit)
    web_bus = WebBus(workspace=workspace)

    return {
        # V1 · Phase 0-3
        "origin_request_loader": OriginRequestLoaderWorker(),
        "intent_analyzer": IntentAnalyzerWorker(web_bus=web_bus),
        "reference_scout": ReferenceScoutWorker(),
        "team_architect": TeamArchitectWorker(web_bus=web_bus),
        # V2 · Phase 2/4-7 bus-driven 订阅激活
        "scale_assessor": ScaleAssessorWorker(),
        "decomposition_planner": DecompositionPlannerWorker(),  # conditional size=large
        "workspace_designer": WorkspaceDesignerWorker(),
        "worker_designer": WorkerDesignerWorker(),
        "material_designer": MaterialDesignerWorker(),
        "contract_auditor": ContractAuditorWorker(),
        "design_validator": DesignValidatorWorker(),
        # V3.2 · Phase 8 拆分为 9 节点子 team (2026-04-24 · 分形重构)
        # 6 HARD 纯模板
        "formats_generator": FormatsFileGenerator(),
        "team_file_generator": TeamFileGenerator(),
        "run_file_generator": RunFileGenerator(),
        "package_init_generator": PackageInitGenerator(),
        "workers_init_generator": WorkersInitGenerator(),
        "workspace_yaml_generator": WorkspaceYamlGenerator(),
        # 2 SOFT (Orchestrator + single agent)
        "worker_code_orchestrator": WorkerCodeOrchestrator(),
        "design_md_generator": DesignMdGenerator(),
        # 1 HARD aggregator (8 路)
        "code_aggregator": CodeAggregator(),
        # P6 · HARD 他评 (V3.2 · 2026-04-24)
        "code_reviewer": CodeReviewer(),
        # Phase 10 · Registrar (现 AND 订阅 code_package + code_review_report)
        "registrar": RegistrarWorker(),
    }
