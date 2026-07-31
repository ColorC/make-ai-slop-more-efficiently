# [OMNI] origin=claude-code domain=workflow_factory/pipeline.py ts=2026-04-08T03:23:37Z
# [OMNI] material_id="material:core.team_builder.topology_specification.declaration.py"
"""workflow_factory pipeline — TeamSpec 声明

10 节点 4 回路:
  A → B → C → D → E → F → G → H → J
  + E→E'→E (编译修复), F/G/H→I→E (综合修复)

Format 语义递进链:
  requirement_raw → requirement → format_chain → node_plan → project_skeleton
  → compiled_skeleton → audited_skeleton → route_checked_skeleton → tested_skeleton → done
"""

from omnicompany.protocol.anchor import (
    AnchorSpec,
    Route,
    RouteAction,
    ValidatorKind,
    ValidatorSpec,
    VerdictKind,
)
from omnicompany.protocol.team import (
    NodeKind,
    NodeMaturity,
    TeamEdge,
    TeamNode,
    TeamSpec,
)


def _anchor(
    node_id: str, fmt_in: str, fmt_out: str, *,
    vkind: ValidatorKind, desc: str,
    routes: dict[VerdictKind, Route],
    maturity: NodeMaturity = NodeMaturity.MATURE,
) -> TeamNode:
    """快捷构造 ANCHOR 节点 (SKILL §4.2 + §3.1 第 18 项: maturity 必填)。"""
    return TeamNode(
        id=node_id,
        kind=NodeKind.ANCHOR,
        maturity=maturity,
        anchor=AnchorSpec(
            id=f"a_{node_id}",
            name=node_id,
            format_in=fmt_in,
            format_out=fmt_out,
            validator=ValidatorSpec(
                id=f"v_{node_id}",
                kind=vkind,
                description=desc,
            ),
            routes=routes,
        ),
    )


def build_team() -> TeamSpec:
    """构建 workflow-factory 管线。"""

    _retry2 = {
        VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=2),
        VerdictKind.PARTIAL: Route(action=RouteAction.RETRY, max_retries=2),
    }

    nodes = [
        # ── 设计阶段 ──
        _anchor(
            "req_analyzer", "wf.requirement_raw", "wf.requirement",
            vkind=ValidatorKind.SOFT,
            desc="LLM 解析需求为结构化规格",
            routes={
                VerdictKind.PASS: Route(action=RouteAction.NEXT, target="format_designer"),
                **_retry2,
            },
            maturity=NodeMaturity.GROWING,
        ),
        _anchor(
            "format_designer", "wf.requirement", "wf.format_chain",
            vkind=ValidatorKind.SOFT,
            desc="LLM 设计 Format 继承链",
            routes={
                # 2026-04-19 (M2.β v2): 去掉 target="node_planner"; runner 走
                # _resolve_next_all 分发到 edges 里所有 PASS 边, 实现 fan-out 到
                # node_planner + framework_context_loader (后者通过 fan-in 绕过
                # node_planner 拿 requirement_context)。
                VerdictKind.PASS: Route(action=RouteAction.NEXT),
                **_retry2,
            },
            maturity=NodeMaturity.GROWING,
        ),
        _anchor(
            "node_planner", "wf.format_chain", "wf.node_plan",
            vkind=ValidatorKind.SOFT,
            desc="LLM 设计 Router 节点规划",
            routes={
                VerdictKind.PASS: Route(action=RouteAction.NEXT, target="node_plan_auditor"),
                **_retry2,
            },
            maturity=NodeMaturity.GROWING,
        ),
        # P7.8 meta-pipeline 自净: HARD 审计 node_plan 是否满足 SKILL §3.1 节点设计单表
        _anchor(
            "node_plan_auditor", "wf.node_plan", "wf.node_plan",
            vkind=ValidatorKind.HARD,
            desc=(
                "HARD 审计 node_plan 的语义质量: SOFT 节点是否填了 context_sources / "
                "hallucination_risks / output_token_budget / FAIL 路由。防止 workflow_factory "
                "把自己的坏习惯复制到生成的管线 (GAP §2.3 + §2.5)。"
            ),
            routes={
                VerdictKind.PASS: Route(action=RouteAction.NEXT, target="framework_context_loader"),
                # PARTIAL = 关键缺失 → retry node_planner
                VerdictKind.PARTIAL: Route(action=RouteAction.RETRY, max_retries=2),
                VerdictKind.FAIL: Route(action=RouteAction.HALT),
            },
            maturity=NodeMaturity.HYPOTHETICAL,
        ),

        # ── 框架上下文注入（SKILL §3.3 正面清单的硬性要求）──
        # 在进入 code_generator 之前，用 inspect.getsource 把框架基类/接口真源码
        # 和一份参考域实现（selftest）注入到 node_plan.framework_context。
        # 消灭 code_generator 对 Router/Verdict/LLMClient/AnchorSpec 的幻觉。
        _anchor(
            "framework_context_loader", "wf.node_plan", "wf.node_plan_augmented",
            vkind=ValidatorKind.HARD,
            desc=(
                "用 inspect.getsource 注入框架真源码（Router/Verdict/LLMClient/"
                "AnchorSpec/TeamNode/NodeKind/Format 等）+ selftest 参考域全文，"
                "到 node_plan.framework_context。"
            ),
            routes={
                VerdictKind.PASS: Route(action=RouteAction.NEXT, target="code_gen_loop"),
                VerdictKind.FAIL: Route(action=RouteAction.HALT),  # 注入失败 = 框架坏了，停
            },
        ),

        # ── 生成阶段 (2026-04-19 合一：agent-loop 自验) ──
        # 旧 4 节点 code_gen_* 固定顺序 LLM 调用，routers.py 常被 token 预算截断
        # （实证：routers.py line 77 "re.sub(r"^" 漏闭合，syntax_fixer 也救不回来）。
        # 新 code_gen_loop = 1 个 AgentNodeLoop：
        #   write_file → py_compile 自检 → 错了 read_written_file + 重写
        #   4 文件全 compile 通过才 finish
        # 本节点直接产出 wf.project_skeleton，取代原 4 节点链。
        _anchor(
            "code_gen_loop", "wf.node_plan_augmented", "wf.project_skeleton",
            vkind=ValidatorKind.SOFT,
            desc=(
                "Agent-loop 代码生成：write_file + py_compile + read_written_file 工具"
                "逐文件生成 + 自验，4 文件全 compile 通过才 finish。"
                "取代旧 4 节点 code_gen_* 固定顺序 LLM 调用（解决 routers.py 截断问题）。"
            ),
            routes={
                VerdictKind.PASS: Route(action=RouteAction.NEXT, target="compile_checker"),
                VerdictKind.PARTIAL: Route(action=RouteAction.RETRY, max_retries=1),
                VerdictKind.FAIL: Route(action=RouteAction.HALT),
            },
            maturity=NodeMaturity.HYPOTHETICAL,
        ),

        # ── 验证阶段 (P7.3 单主干 + reports 容器 + granted_tags 累加) ──
        # 老的 compile_checker_l2 复制节点已删除, 用 feedback 边替代;
        # skeleton 克隆链 4 个 Format 已删除, 全部用 wf.project_skeleton。
        _anchor(
            "compile_checker", "wf.project_skeleton", "wf.project_skeleton",
            vkind=ValidatorKind.HARD,
            desc="三层编译检查: py_compile → import → TeamChecker。报告写进 reports['compile'], PASS 贴 compile-passed tag。",
            routes={
                # 2026-04-10 回退 P7.4 并行: 三道 HARD 验证改回串行。
                # 原因: runner 的 join barrier 是 AND 语义, P7.4 并行 fan-out 后若任一验证 FAIL,
                # finalizer 永远凑不齐 3/3 (因为 FAIL 的那个走向 auto_fixer), auto_fixer 也凑不齐
                # 3/3 (因为 PASS 的那两个走向 finalizer), 两端死锁。并行节省的 ~100ms 不值得这个语义代价。
                # 2026-07-03 批4: lap_verifier 九维检查器显式废止, compile→error_route→integration→finalizer,
                # PASS 直接进 error_route_auditor(原为 lap_verifier)。
                VerdictKind.PASS: Route(action=RouteAction.NEXT, target="error_route_auditor"),
                VerdictKind.FAIL: Route(action=RouteAction.NEXT, target="deterministic_fixer"),
            },
        ),
        _anchor(
            "deterministic_fixer", "wf.project_skeleton", "wf.project_skeleton",
            vkind=ValidatorKind.HARD,
            desc="确定性修复 (Level 1): typing清理/NodeKind枚举/标准import补全。完成贴 deterministic-cleanup-applied tag。",
            routes={
                # 修完直接 feedback 回 compile_checker, 不再需要 compile_checker_l2
                VerdictKind.PASS: Route(action=RouteAction.NEXT, target="compile_checker"),
                # 2026-04-19 修拓扑死锁：没匹配到规则时升级到 syntax_fixer（LLM 层）
                VerdictKind.PARTIAL: Route(action=RouteAction.NEXT, target="syntax_fixer"),
                VerdictKind.FAIL: Route(action=RouteAction.HALT),  # 确定性修复都失败 = 异常
            },
        ),
        _anchor(
            "syntax_fixer", "wf.project_skeleton", "wf.project_skeleton",
            vkind=ValidatorKind.SOFT,
            desc="LLM 根据编译错误修复代码 (Level 2)，限制源码长度避免 token 截断",
            routes={
                VerdictKind.PASS: Route(action=RouteAction.NEXT, target="compile_checker"),
                # 2026-04-19 修拓扑死锁：LLM 修不了也要升级到 auto_fixer（更大上下文 LLM）
                VerdictKind.FAIL: Route(action=RouteAction.NEXT, target="auto_fixer"),
                VerdictKind.PARTIAL: Route(action=RouteAction.NEXT, target="auto_fixer"),
            },
            maturity=NodeMaturity.GROWING,
        ),
        # 2026-04-10 回退 P7.4 并行到串行; 2026-07-03 批4 lap_verifier 废止后:
        # compile → error_route → integration → finalizer
        # 每个验证 FAIL 单独走 auto_fixer (OR 语义, 不经过 join barrier), PASS 走下一个验证。
        _anchor(
            "error_route_auditor", "wf.project_skeleton", "wf.project_skeleton",
            vkind=ValidatorKind.HARD,
            desc="确定性错误路由完整性审计（五项检查）",
            routes={
                VerdictKind.PASS: Route(action=RouteAction.NEXT, target="integration_tester"),
                VerdictKind.FAIL: Route(action=RouteAction.NEXT, target="auto_fixer"),
            },
        ),
        _anchor(
            "integration_tester", "wf.project_skeleton", "wf.project_skeleton",
            vkind=ValidatorKind.HARD,
            desc="确定性集成测试（import + build + check）",
            routes={
                VerdictKind.PASS: Route(action=RouteAction.NEXT, target="finalizer"),
                VerdictKind.FAIL: Route(action=RouteAction.NEXT, target="auto_fixer"),
            },
        ),

        # ── 修复阶段 ──
        _anchor(
            "auto_fixer", "wf.project_skeleton", "wf.project_skeleton",
            vkind=ValidatorKind.SOFT,
            desc="LLM 跨文件自动修复, 从 reports 容器读取所有历史失败报告 (P7.3 不再瞎子修)",
            routes={
                VerdictKind.PASS: Route(action=RouteAction.NEXT, target="compile_checker"),
                VerdictKind.FAIL: Route(action=RouteAction.HALT),
            },
            maturity=NodeMaturity.GROWING,
        ),

        # ── 最终化 ──
        _anchor(
            "finalizer", "wf.project_skeleton", "wf.done",
            vkind=ValidatorKind.HARD,
            desc="注册管线 + 生成质量总结报告 (从 reports 容器读取四项得分)",
            routes={
                VerdictKind.PASS: Route(action=RouteAction.EMIT),
                VerdictKind.FAIL: Route(action=RouteAction.HALT),  # P7.6: 补 FAIL 路由
            },
        ),
    ]

    edges = [
        # 设计链
        TeamEdge(source="req_analyzer", target="format_designer", condition=VerdictKind.PASS),
        TeamEdge(source="format_designer", target="node_planner", condition=VerdictKind.PASS),
        TeamEdge(source="node_planner", target="node_plan_auditor", condition=VerdictKind.PASS),
        TeamEdge(source="node_plan_auditor", target="framework_context_loader", condition=VerdictKind.PASS),
        # 2026-04-19 (M2.β v2) fan-in bypass: format_designer 直接给 framework_context_loader
        # 送 wf.format_chain, 让后者拿 requirement_context 不再靠 node_plan 搭便车。
        # runner join barrier 等 node_plan_auditor + format_designer 两路 PASS 到齐。
        TeamEdge(source="format_designer", target="framework_context_loader", condition=VerdictKind.PASS),

        # 生成链 (P7.2 SCATTER 4-step)
        TeamEdge(source="framework_context_loader", target="code_gen_loop", condition=VerdictKind.PASS),

        # 生成 → 验证
        TeamEdge(source="code_gen_loop", target="compile_checker", condition=VerdictKind.PASS),

        # 验证链 (P7.3 单主干 + 2026-04-10 回退到串行; 2026-07-03 批4 lap_verifier 废止)
        # compile → error_route → integration → finalizer, 每个 FAIL 单独走 auto_fixer。
        TeamEdge(source="compile_checker", target="error_route_auditor", condition=VerdictKind.PASS),
        TeamEdge(source="error_route_auditor", target="integration_tester", condition=VerdictKind.PASS),
        TeamEdge(source="integration_tester", target="finalizer", condition=VerdictKind.PASS),

        # 修复链 (2026-04-19 重构, 消除死循环):
        # Level 1: compile_checker FAIL → deterministic_fixer
        #   PASS (修了东西) → feedback 回 compile_checker
        #   PARTIAL (没匹配到规则) → NEXT syntax_fixer (升级到 LLM 层)
        TeamEdge(source="compile_checker", target="deterministic_fixer", condition=VerdictKind.FAIL),
        TeamEdge(source="deterministic_fixer", target="compile_checker", condition=VerdictKind.PASS, feedback=True),
        TeamEdge(source="deterministic_fixer", target="syntax_fixer", condition=VerdictKind.PARTIAL),

        # Level 2: syntax_fixer (LLM) → compile_checker (feedback)
        #   FAIL/PARTIAL → NEXT auto_fixer (升级到大上下文 LLM)
        TeamEdge(source="syntax_fixer", target="compile_checker", condition=VerdictKind.PASS, feedback=True),
        TeamEdge(source="syntax_fixer", target="auto_fixer", condition=VerdictKind.FAIL),
        TeamEdge(source="syntax_fixer", target="auto_fixer", condition=VerdictKind.PARTIAL),

        # Level 3: 验证链失败 → auto_fixer (LLM fallback) → compile_checker (feedback)
        # (2026-07-03 批4: lap_verifier FAIL 边随节点废止一并摘除)
        TeamEdge(source="error_route_auditor", target="auto_fixer", condition=VerdictKind.FAIL),
        TeamEdge(source="integration_tester", target="auto_fixer", condition=VerdictKind.FAIL),
        TeamEdge(source="auto_fixer", target="compile_checker", condition=VerdictKind.PASS, feedback=True),
    ]

    return TeamSpec(
        id="workflow-factory",
        name="workflow-factory",
        description="造工作流的工作流：输入自然语言需求 → 输出通过全部验证的 LAP-native 工作流代码",
        entry="req_analyzer",
        nodes=nodes,
        edges=edges,
        tags=["meta", "workflow_factory"],
    )


# ═══════════════════════════════════════════════════════════════════════
# A3 agent-first team (2026-04-23)
# ═══════════════════════════════════════════════════════════════════════

def build_team_agent_first() -> TeamSpec:
    """agent-first team · 11 node · **bus-driven 订阅激活** (无需手工画拓扑边).

    每个 node 声明 FORMAT_IN/OUT, bus 看到 material event 自动激活订阅者.
    Fan-out/fan-in 都是 FORMAT_IN/OUT 声明的天然结果:
      - OriginRequestLoader 产 origin_request → IntentAnalyzer + ReferenceScout 并行 (fan-out)
      - team_design → WorkspaceDesigner + WorkerDesigner + MaterialDesigner 并行 (fan-out)
      - [intent + refs] → TeamArchitect / ScaleAssessor (composite fan-in)
      - DesignValidator FORMAT_IN 5 路 composite fan-in
      - DecompositionPlanner conditional (size!=large output=None 不 emit)
    """
    _retry2 = {
        VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=2),
        VerdictKind.PARTIAL: Route(action=RouteAction.RETRY, max_retries=2),
    }
    _retry1 = {
        VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=1),
    }
    _default_pass_next = {VerdictKind.PASS: Route(action=RouteAction.NEXT)}

    nodes = [
        # ───── V1 · Phase 0-3 已跑通 ─────
        _anchor(
            "origin_request_loader",
            "team_builder.material.request_trigger",
            "team_builder.material.origin_request",
            vkind=ValidatorKind.HARD,
            desc="Phase 0 · HARD · CLI --text 包装为完整 origin_request",
            routes={**_default_pass_next, **_retry1},
            maturity=NodeMaturity.MATURE,
        ),
        _anchor(
            "intent_analyzer",
            "team_builder.material.origin_request",
            "team_builder.material.intent_analysis",
            vkind=ValidatorKind.SOFT,
            desc="Phase 1 · SOFT LLM · 独立上下文提炼用户意图 + 诚实标 ambiguities",
            routes={**_default_pass_next, **_retry2},
            maturity=NodeMaturity.GROWING,
        ),
        _anchor(
            "reference_scout",
            "team_builder.material.origin_request",
            "team_builder.material.team_references",
            vkind=ValidatorKind.SOFT,
            desc="Phase 1' · SOFT v0 启发式 · 并行扫 standards/similar_team/bus 参考",
            routes={**_default_pass_next, **_retry1},
            maturity=NodeMaturity.GROWING,
        ),
        _anchor(
            "team_architect",
            ["team_builder.material.intent_analysis", "team_builder.material.team_references"],
            "team_builder.material.team_design",
            vkind=ValidatorKind.SOFT,
            desc="Phase 3 · SOFT LLM · composite fan-in → 七节 team_design 骨架",
            routes={**_default_pass_next, **_retry2},
            maturity=NodeMaturity.GROWING,
        ),
        # ───── V2 · Phase 2/4-7 新增 ─────
        _anchor(
            "scale_assessor",
            ["team_builder.material.intent_analysis", "team_builder.material.team_references"],
            "team_builder.material.scale_assessment",
            vkind=ValidatorKind.SOFT,
            desc="Phase 2 · AgentNodeLoop · 判 size (small/medium/large) + 拆分维度",
            routes={**_default_pass_next, **_retry2},
            maturity=NodeMaturity.GROWING,
        ),
        _anchor(
            "decomposition_planner",
            ["team_builder.material.scale_assessment", "team_builder.material.intent_analysis"],
            "team_builder.material.decomposition_plan",
            vkind=ValidatorKind.SOFT,
            desc="Phase 2 · AgentNodeLoop · conditional · size=large 时拆子 team + 契约",
            routes={**_default_pass_next, **_retry1},
            maturity=NodeMaturity.GROWING,
        ),
        _anchor(
            "workspace_designer",
            "team_builder.material.team_design",
            "team_builder.material.workspace_spec",
            vkind=ValidatorKind.HARD,
            desc="Phase 5 · HARD · 从 team_name 推规范 workspace.yaml",
            routes={**_default_pass_next, **_retry1},
            maturity=NodeMaturity.MATURE,
        ),
        _anchor(
            "worker_designer",
            "team_builder.material.team_design",
            "team_builder.material.worker_design_detailed",
            vkind=ValidatorKind.SOFT,
            desc="Phase 4 · AgentNodeLoop · 单 Worker 深化 (当前单份, V2+ 扩 N 份 fan-out)",
            routes={**_default_pass_next, **_retry2},
            maturity=NodeMaturity.GROWING,
        ),
        _anchor(
            "material_designer",
            [
                "team_builder.material.team_design",
                "team_builder.material.origin_request",  # V3.2 · 用于 source material 忠实对齐
            ],
            "team_builder.material.material_design_detailed",
            vkind=ValidatorKind.SOFT,
            desc="Phase 4' · Orchestrator · M 份 Material 深化 + 读 origin_request 防字段重命名",
            routes={**_default_pass_next, **_retry2},
            maturity=NodeMaturity.GROWING,
        ),
        _anchor(
            "contract_auditor",
            [
                "team_builder.material.worker_design_detailed",
                "team_builder.material.material_design_detailed",
            ],
            "team_builder.material.contract_audit",
            vkind=ValidatorKind.HARD,
            desc="Phase 6 · HARD · 跨 Worker FORMAT 连接 + F-15 context_sources 静态审",
            routes={**_default_pass_next, **_retry1},
            maturity=NodeMaturity.MATURE,
        ),
        _anchor(
            "design_validator",
            [
                "team_builder.material.team_design",
                "team_builder.material.workspace_spec",
                "team_builder.material.worker_design_detailed",
                "team_builder.material.material_design_detailed",
                "team_builder.material.contract_audit",
            ],
            "team_builder.material.design_validation_report",
            vkind=ValidatorKind.SOFT,
            desc="Phase 7 · AgentNodeLoop · 7 维综合草图级验证 (5 路 composite fan-in)",
            routes={**_default_pass_next, **_retry2,
                    VerdictKind.PARTIAL: Route(action=RouteAction.NEXT)},
            maturity=NodeMaturity.GROWING,
        ),
        # ───── V3.2 · Phase 8 拆分为 9 节点子 team (2026-04-24 · 分形重构) ─────
        # Wh1-Wh6: 6 HARD 模板 (formats/team/run/pkg_init/workers_init/workspace_yaml)
        _anchor(
            "formats_generator",
            ["team_builder.material.team_design", "team_builder.material.material_design_detailed"],
            "team_builder.material.formats_py",
            vkind=ValidatorKind.HARD,
            desc="Wh1 · HARD · 依 material_design_detailed 渲染 formats.py",
            routes={**_default_pass_next, **_retry1},
            maturity=NodeMaturity.MATURE,
        ),
        _anchor(
            "team_file_generator",
            ["team_builder.material.team_design", "team_builder.material.worker_design_detailed"],
            "team_builder.material.team_py",
            vkind=ValidatorKind.HARD,
            desc="Wh2 · HARD · 依 team_design + worker routes 渲染 team.py",
            routes={**_default_pass_next, **_retry1},
            maturity=NodeMaturity.MATURE,
        ),
        _anchor(
            "run_file_generator",
            ["team_builder.material.team_design", "team_builder.material.worker_design_detailed"],
            "team_builder.material.run_py",
            vkind=ValidatorKind.HARD,
            desc="Wh3 · HARD · 依 worker_design_detailed 渲染 run.py (build_bindings)",
            routes={**_default_pass_next, **_retry1},
            maturity=NodeMaturity.MATURE,
        ),
        _anchor(
            "package_init_generator",
            "team_builder.material.team_design",
            "team_builder.material.pkg_init_py",
            vkind=ValidatorKind.HARD,
            desc="Wh4 · HARD · 顶层 __init__.py 样板",
            routes={**_default_pass_next, **_retry1},
            maturity=NodeMaturity.MATURE,
        ),
        _anchor(
            "workers_init_generator",
            ["team_builder.material.team_design", "team_builder.material.worker_design_detailed"],
            "team_builder.material.workers_init_py",
            vkind=ValidatorKind.HARD,
            desc="Wh5 · HARD · workers/__init__.py 导出 ALL_WORKERS",
            routes={**_default_pass_next, **_retry1},
            maturity=NodeMaturity.MATURE,
        ),
        _anchor(
            "workspace_yaml_generator",
            "team_builder.material.workspace_spec",
            "team_builder.material.workspace_yaml",
            vkind=ValidatorKind.HARD,
            desc="Wh6 · HARD · workspace_spec → yaml.safe_dump",
            routes={**_default_pass_next, **_retry1},
            maturity=NodeMaturity.MATURE,
        ),
        # Ws7: per-worker sub-agent orchestrator
        _anchor(
            "worker_code_orchestrator",
            [
                "team_builder.material.team_design",
                "team_builder.material.worker_design_detailed",
                "team_builder.material.material_design_detailed",
            ],
            "team_builder.material.worker_code_files_bundle",
            vkind=ValidatorKind.SOFT,
            desc="Ws7 · Orchestrator · N 份 sub-agent 并行 · sub-agent 喂 Material required 字段防 output key 不匹配",
            routes={**_default_pass_next, **_retry1,
                    VerdictKind.PARTIAL: Route(action=RouteAction.NEXT)},
            maturity=NodeMaturity.GROWING,
        ),
        # Ws8: DESIGN.md SOFT
        _anchor(
            "design_md_generator",
            [
                "team_builder.material.team_design",
                "team_builder.material.workspace_spec",
                "team_builder.material.worker_design_detailed",
                "team_builder.material.material_design_detailed",
            ],
            "team_builder.material.design_md",
            vkind=ValidatorKind.SOFT,
            desc="Ws8 · SOFT · 骨架预填七节 + LLM 填内容 · 产 DESIGN.md",
            routes={**_default_pass_next, **_retry1,
                    VerdictKind.PARTIAL: Route(action=RouteAction.NEXT)},
            maturity=NodeMaturity.GROWING,
        ),
        # Wa9: 8 路 aggregator
        _anchor(
            "code_aggregator",
            [
                "team_builder.material.formats_py",
                "team_builder.material.team_py",
                "team_builder.material.run_py",
                "team_builder.material.pkg_init_py",
                "team_builder.material.workers_init_py",
                "team_builder.material.workspace_yaml",
                "team_builder.material.worker_code_files_bundle",
                "team_builder.material.design_md",
            ],
            "team_builder.material.code_package",
            vkind=ValidatorKind.HARD,
            desc="Wa9 · HARD · 8 路 composite fan-in 合成 code_package (交 CodeReviewer 他评后再给 Registrar)",
            routes={**_default_pass_next, **_retry1,
                    VerdictKind.PARTIAL: Route(action=RouteAction.NEXT)},
            maturity=NodeMaturity.MATURE,
        ),
        # P6: CodeReviewer (V3.2 · HARD 他评)
        _anchor(
            "code_reviewer",
            [
                "team_builder.material.code_package",
                "team_builder.material.worker_design_detailed",
                "team_builder.material.material_design_detailed",
            ],
            "team_builder.material.code_review_report",
            vkind=ValidatorKind.HARD,
            desc="P6 · HARD 他评 · Material schema ⇔ Worker code output + class name 对齐 · 不 patch 产物",
            routes={**_default_pass_next,
                    VerdictKind.FAIL: Route(action=RouteAction.HALT)},  # FAIL 明确不下传, Registrar 等不到 review_report 不激活
            maturity=NodeMaturity.GROWING,
        ),
        _anchor(
            "registrar",
            [
                "team_builder.material.code_package",
                "team_builder.material.code_review_report",
                "team_builder.material.scale_assessment",
            ],
            "team_builder.material.registration_plan",
            vkind=ValidatorKind.HARD,
            desc="Phase 10 · HARD · dry_run 注册计划 · 要求 code_package + code_review_report (review 过才激活)",
            routes={
                VerdictKind.PASS: Route(action=RouteAction.EMIT),  # sink
                VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=1),
            },
            maturity=NodeMaturity.MATURE,
        ),
    ]

    # edges · 当前 TeamRunner 仍按 edges 驱动 (MaterialDispatcher 才是纯 bus-driven).
    # 所以我们把 bus-driven 订阅关系**同时**声明为 edges. 未来切 MaterialDispatcher 后可删.
    edges = [
        # origin_request 激活 Intent + Scout (fan-out, 同 material 多订阅者)
        TeamEdge(source="origin_request_loader", target="intent_analyzer", condition=VerdictKind.PASS),
        TeamEdge(source="origin_request_loader", target="reference_scout", condition=VerdictKind.PASS),
        # intent + refs 激活 TeamArchitect + ScaleAssessor (composite fan-in)
        TeamEdge(source="intent_analyzer", target="team_architect", condition=VerdictKind.PASS),
        TeamEdge(source="reference_scout", target="team_architect", condition=VerdictKind.PASS),
        TeamEdge(source="intent_analyzer", target="scale_assessor", condition=VerdictKind.PASS),
        TeamEdge(source="reference_scout", target="scale_assessor", condition=VerdictKind.PASS),
        # scale + intent 激活 DecompositionPlanner (conditional: size=large 时才真 emit)
        TeamEdge(source="scale_assessor", target="decomposition_planner", condition=VerdictKind.PASS),
        TeamEdge(source="intent_analyzer", target="decomposition_planner", condition=VerdictKind.PASS),
        # team_design 激活 WorkspaceDesigner + WorkerDesigner + MaterialDesigner (fan-out)
        TeamEdge(source="team_architect", target="workspace_designer", condition=VerdictKind.PASS),
        TeamEdge(source="team_architect", target="worker_designer", condition=VerdictKind.PASS),
        TeamEdge(source="team_architect", target="material_designer", condition=VerdictKind.PASS),
        # V3.2: MaterialDesigner 也需要 origin_request (源 Material 字段忠实对齐)
        TeamEdge(source="origin_request_loader", target="material_designer", condition=VerdictKind.PASS),
        # worker_detailed + material_detailed 激活 ContractAuditor (composite fan-in)
        TeamEdge(source="worker_designer", target="contract_auditor", condition=VerdictKind.PASS),
        TeamEdge(source="material_designer", target="contract_auditor", condition=VerdictKind.PASS),
        # 全部上游激活 DesignValidator (5 路 composite fan-in)
        TeamEdge(source="team_architect", target="design_validator", condition=VerdictKind.PASS),
        TeamEdge(source="workspace_designer", target="design_validator", condition=VerdictKind.PASS),
        TeamEdge(source="worker_designer", target="design_validator", condition=VerdictKind.PASS),
        TeamEdge(source="material_designer", target="design_validator", condition=VerdictKind.PASS),
        TeamEdge(source="contract_auditor", target="design_validator", condition=VerdictKind.PASS),
        # V3.2 · design_validator PASS/PARTIAL 门控 → 9 新节点激活 (2026-04-24 分形)
        # Wh1 formats_generator: team_design + material_detailed
        TeamEdge(source="team_architect", target="formats_generator", condition=VerdictKind.PASS),
        TeamEdge(source="material_designer", target="formats_generator", condition=VerdictKind.PASS),
        # Wh2 team_file_generator: team_design + worker_detailed
        TeamEdge(source="team_architect", target="team_file_generator", condition=VerdictKind.PASS),
        TeamEdge(source="worker_designer", target="team_file_generator", condition=VerdictKind.PASS),
        # Wh3 run_file_generator: team_design + worker_detailed
        TeamEdge(source="team_architect", target="run_file_generator", condition=VerdictKind.PASS),
        TeamEdge(source="worker_designer", target="run_file_generator", condition=VerdictKind.PASS),
        # Wh4 package_init_generator: team_design only
        TeamEdge(source="team_architect", target="package_init_generator", condition=VerdictKind.PASS),
        # Wh5 workers_init_generator: team_design + worker_detailed
        TeamEdge(source="team_architect", target="workers_init_generator", condition=VerdictKind.PASS),
        TeamEdge(source="worker_designer", target="workers_init_generator", condition=VerdictKind.PASS),
        # Wh6 workspace_yaml_generator: workspace_spec only
        TeamEdge(source="workspace_designer", target="workspace_yaml_generator", condition=VerdictKind.PASS),
        # Ws7 worker_code_orchestrator: team_design + worker_detailed + material_detailed (V3.2 P6: 喂 schema)
        TeamEdge(source="team_architect", target="worker_code_orchestrator", condition=VerdictKind.PASS),
        TeamEdge(source="worker_designer", target="worker_code_orchestrator", condition=VerdictKind.PASS),
        TeamEdge(source="material_designer", target="worker_code_orchestrator", condition=VerdictKind.PASS),
        # Ws8 design_md_generator: team_design + workspace + worker + material detailed
        TeamEdge(source="team_architect", target="design_md_generator", condition=VerdictKind.PASS),
        TeamEdge(source="workspace_designer", target="design_md_generator", condition=VerdictKind.PASS),
        TeamEdge(source="worker_designer", target="design_md_generator", condition=VerdictKind.PASS),
        TeamEdge(source="material_designer", target="design_md_generator", condition=VerdictKind.PASS),
        # Wa9 code_aggregator: 8 路 composite fan-in
        TeamEdge(source="formats_generator", target="code_aggregator", condition=VerdictKind.PASS),
        TeamEdge(source="team_file_generator", target="code_aggregator", condition=VerdictKind.PASS),
        TeamEdge(source="run_file_generator", target="code_aggregator", condition=VerdictKind.PASS),
        TeamEdge(source="package_init_generator", target="code_aggregator", condition=VerdictKind.PASS),
        TeamEdge(source="workers_init_generator", target="code_aggregator", condition=VerdictKind.PASS),
        TeamEdge(source="workspace_yaml_generator", target="code_aggregator", condition=VerdictKind.PASS),
        TeamEdge(source="worker_code_orchestrator", target="code_aggregator", condition=VerdictKind.PASS),
        TeamEdge(source="worker_code_orchestrator", target="code_aggregator", condition=VerdictKind.PARTIAL),
        TeamEdge(source="design_md_generator", target="code_aggregator", condition=VerdictKind.PASS),
        TeamEdge(source="design_md_generator", target="code_aggregator", condition=VerdictKind.PARTIAL),
        # V3.2 P6 · code_aggregator → code_reviewer (必经他评) → registrar
        TeamEdge(source="code_aggregator", target="code_reviewer", condition=VerdictKind.PASS),
        TeamEdge(source="code_aggregator", target="code_reviewer", condition=VerdictKind.PARTIAL),
        # code_reviewer 的 FORMAT_IN 也需要 worker/material 深化
        TeamEdge(source="worker_designer", target="code_reviewer", condition=VerdictKind.PASS),
        TeamEdge(source="material_designer", target="code_reviewer", condition=VerdictKind.PASS),
        # Registrar 订阅 code_package + code_review_report (AND composite)
        TeamEdge(source="code_aggregator", target="registrar", condition=VerdictKind.PASS),
        TeamEdge(source="code_aggregator", target="registrar", condition=VerdictKind.PARTIAL),
        TeamEdge(source="code_reviewer", target="registrar", condition=VerdictKind.PASS),
        # Registrar 是终点；必须等规模研判结束，避免 task.finish 早于仍在运行的分支。
        TeamEdge(source="scale_assessor", target="registrar", condition=VerdictKind.PASS),
    ]

    return TeamSpec(
        id="team-builder",
        name="team-builder",
        description=(
            "造 Team 的 Team · agent-first 设计 · bus-driven 11 节点 · "
            "输入自然语言需求 → 完整草图 + workspace + 契约审计 + 7 维验证报告"
        ),
        entry="origin_request_loader",
        nodes=nodes,
        edges=edges,
        tags=["meta", "team_builder", "agent_first", "bus_driven"],
    )
