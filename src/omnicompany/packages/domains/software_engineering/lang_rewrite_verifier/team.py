# [OMNI] origin=human domain=software_engineering/lang_rewrite_verifier ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.lang_rewrite_verifier.team_topology.dag.py"
"""lang_rewrite_verifier.pipeline — 翻译后冒烟验证 + 自动修复管线

拓扑（12 节点）：

  smoke_gen ──PASS──► smoke_runner ──PASS──► EMIT ✅
                            │
                           FAIL
                            │
                            ▼
                      error_analyzer ──► context_init ──► hypothesis_generator
                                               ▲                   │
                                        evidence_collector    probe_designer
                                          ▲        ▲               │
                                          │        │          probe_executor
                                          │        │           │        │
                                          │    (回归)       (证否)   (证实)
                                          │        │                   │
                                          │   regression_to_context  fixer
                                          │        ▲                   │
                                          │   regression_analyzer ◄─ tester
                                          │                       │
                                          └── (tester PASS) ──────┘
                                                    │
                                               smoke_runner  ← 修复后重验

关键设计：
- smoke_gen 是 AgentNodeLoop，拥有全局项目视角，生成有意义的测试套件
- smoke_runner 是 HARD 节点，顺序执行，失败时打包 debug.error-report
- debugger 10 个节点完整复用（language="rust" 由 smoke_runner 注入）
- tester PASS → smoke_runner（而非 EMIT），确保修复后重跑全套冒烟测试
"""

from omnicompany.protocol.anchor import (
    AnchorSpec,
    Route,
    RouteAction,
    TransformerSpec,
    TransformMethod,
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

DOMAIN = "smoke"
DBG = "debug"


def build_team() -> TeamSpec:
    nodes = [

        # ══════════════════════════════════════════════════════════════
        # 冒烟测试阶段（2 个新节点）
        # ══════════════════════════════════════════════════════════════

        # ── 1. 测试套件生成（AgentNodeLoop）────────────────────────────
        TeamNode(
            id="smoke_gen",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-gen",
                name="SmokeTestGenerator",
                format_in="rewrite.verified-code",
                format_out=f"{DOMAIN}.test-suite",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-gen-v",
                    kind=ValidatorKind.SOFT,
                    description=(
                        "AgentNodeLoop: 全局阅读翻译后 Rust 项目，"
                        "生成从简到繁的冒烟测试套件"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="smoke_runner"),
                    VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=2),
                },
            ),
            maturity=NodeMaturity.HYPOTHETICAL,
        ),

        # ── 2. 冒烟测试执行（HARD Anchor）──────────────────────────────
        TeamNode(
            id="smoke_runner",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-run",
                name="SmokeRunner",
                format_in=f"{DOMAIN}.test-suite",
                format_out=f"{DOMAIN}.result",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-run-v",
                    kind=ValidatorKind.HARD,
                    description=(
                        "顺序运行冒烟测试用例；"
                        "全部通过→EMIT，失败→打包 debug.error-report 进入 debugger"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.EMIT),
                    VerdictKind.FAIL: Route(action=RouteAction.NEXT, target="error_analyzer"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ══════════════════════════════════════════════════════════════
        # Debugger 管线（10 个复用节点，仅 tester 路由不同）
        # ══════════════════════════════════════════════════════════════

        # ── 3. 错误分析 ────────────────────────────────────────────────
        TeamNode(
            id="error_analyzer",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DBG}-error-analyze",
                name="ErrorAnalyzer",
                format_in=f"{DBG}.error-report",
                format_out=f"{DBG}.error-analysis",
                validator=ValidatorSpec(
                    id=f"{DBG}-erranalyze-v",
                    kind=ValidatorKind.SOFT,
                    description="LLM 阅读编译错误输出和错误位置代码，分析直接原因",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="context_init"),
                    VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=2),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 4. 初始化 debug-context ────────────────────────────────────
        TeamNode(
            id="context_init",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DBG}-ctx-init",
                name="ContextInit",
                from_format=f"{DBG}.error-analysis",
                to_format=f"{DBG}.debug-context",
                method=TransformMethod.RULE,
                description="将首次错误分析包装为初始 debug-context（只走一次）",
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 5. 假设生成 ────────────────────────────────────────────────
        TeamNode(
            id="hypothesis_generator",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DBG}-hypothesis-gen",
                name="HypothesisGenerator",
                format_in=f"{DBG}.debug-context",
                format_out=f"{DBG}.hypothesis",
                validator=ValidatorSpec(
                    id=f"{DBG}-hypothesis-v",
                    kind=ValidatorKind.SOFT,
                    description="LLM 根据累积 debug-context 追踪依赖来源，提出根因假设",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="probe_designer"),
                    VerdictKind.FAIL: Route(
                        action=RouteAction.HALT, feedback="无法提出新假设，需人工介入"
                    ),
                },
            ),
            maturity=NodeMaturity.HYPOTHETICAL,
        ),

        # ── 6. 试探设计 ────────────────────────────────────────────────
        TeamNode(
            id="probe_designer",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DBG}-probe-design",
                name="ProbeDesigner",
                format_in=f"{DBG}.hypothesis",
                format_out=f"{DBG}.probe-plan",
                validator=ValidatorSpec(
                    id=f"{DBG}-probe-v",
                    kind=ValidatorKind.SOFT,
                    description="LLM 为假设设计试探：读哪些文件、运行什么命令来证实/证否",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="probe_executor"),
                    VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=2),
                },
            ),
            maturity=NodeMaturity.HYPOTHETICAL,
        ),

        # ── 7. 试探执行 ────────────────────────────────────────────────
        TeamNode(
            id="probe_executor",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DBG}-probe-exec",
                name="ProbeExecutor",
                format_in=f"{DBG}.probe-plan",
                format_out=f"{DBG}.probe-result",
                validator=ValidatorSpec(
                    id=f"{DBG}-probeexec-v",
                    kind=ValidatorKind.HARD,
                    description="执行试探（读文件/grep/cargo check），判定假设证实/证否/不确定",
                ),
                routes={
                    VerdictKind.PASS: Route(
                        action=RouteAction.NEXT, target="fixer", feedback="假设证实"
                    ),
                    VerdictKind.FAIL: Route(
                        action=RouteAction.NEXT,
                        target="evidence_collector",
                        feedback="假设证否",
                    ),
                    VerdictKind.PARTIAL: Route(
                        action=RouteAction.NEXT,
                        target="evidence_collector",
                        feedback="证据不充分，需补充试探",
                    ),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 7b. 证据归一（回路收敛点）─────────────────────────────────
        TeamNode(
            id="evidence_collector",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DBG}-evidence-collect",
                name="EvidenceCollector",
                from_format=f"{DBG}.probe-result",
                to_format=f"{DBG}.debug-context",
                method=TransformMethod.RULE,
                description="将试探结果追加到 debug-context，保留假设历史和证据链",
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 8. 修复补丁生成 ───────────────────────────────────────────
        TeamNode(
            id="fixer",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DBG}-fix",
                name="Fixer",
                format_in=f"{DBG}.probe-result",
                format_out=f"{DBG}.fix-patch",
                validator=ValidatorSpec(
                    id=f"{DBG}-fix-v",
                    kind=ValidatorKind.SOFT,
                    description="LLM 根据证实的假设生成精确修复补丁（old_string/new_string）",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="tester"),
                    VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=2),
                },
            ),
            maturity=NodeMaturity.HYPOTHETICAL,
        ),

        # ── 9. 应用补丁 + 重验 ────────────────────────────────────────
        # 关键区别：PASS → smoke_runner（重跑全套冒烟），而非直接 EMIT
        TeamNode(
            id="tester",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DBG}-test",
                name="Tester",
                format_in=f"{DBG}.fix-patch",
                format_out=f"{DBG}.test-feedback",
                validator=ValidatorSpec(
                    id=f"{DBG}-test-v",
                    kind=ValidatorKind.HARD,
                    description="应用补丁并运行 compile_command；PASS→回到 smoke_runner 重验",
                ),
                routes={
                    VerdictKind.PASS: Route(
                        action=RouteAction.NEXT,
                        target="smoke_runner",
                        feedback="单项修复通过，重跑全套冒烟验证",
                    ),
                    VerdictKind.FAIL: Route(
                        action=RouteAction.NEXT, target="regression_analyzer"
                    ),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 10. 回归归因 ──────────────────────────────────────────────
        TeamNode(
            id="regression_analyzer",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DBG}-regression",
                name="RegressionAnalyzer",
                format_in=f"{DBG}.test-feedback",
                format_out=f"{DBG}.regression-analysis",
                validator=ValidatorSpec(
                    id=f"{DBG}-regress-v",
                    kind=ValidatorKind.SOFT,
                    description="LLM 归因：假设错（回退）/ 实现错（改法不对）/ 新问题（部分正确）",
                ),
                routes={
                    VerdictKind.PASS: Route(
                        action=RouteAction.NEXT,
                        target="regression_to_context",
                        feedback="带结论回到假设循环",
                    ),
                    VerdictKind.FAIL: Route(
                        action=RouteAction.HALT, feedback="调试预算耗尽，需人工介入"
                    ),
                },
            ),
            maturity=NodeMaturity.HYPOTHETICAL,
        ),

        # ── 10b. 回归结论归一 ─────────────────────────────────────────
        TeamNode(
            id="regression_to_context",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DBG}-regress-to-ctx",
                name="RegressionToContext",
                from_format=f"{DBG}.regression-analysis",
                to_format=f"{DBG}.debug-context",
                method=TransformMethod.RULE,
                description="将回归分析结论（回退/新问题/假设修正）追加到 debug-context",
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),
    ]

    edges = [
        # ── 冒烟测试主路径 ──
        TeamEdge(source="smoke_gen",    target="smoke_runner"),
        TeamEdge(source="smoke_runner", target="error_analyzer", condition="fail"),

        # ── Debugger 主路径 ──
        TeamEdge(source="error_analyzer",      target="context_init"),
        TeamEdge(source="context_init",         target="hypothesis_generator"),
        TeamEdge(source="hypothesis_generator", target="probe_designer"),
        TeamEdge(source="probe_designer",       target="probe_executor"),
        TeamEdge(
            source="probe_executor", target="fixer",
            condition="pass", label="假设证实",
        ),
        TeamEdge(source="fixer", target="tester"),

        # ── 关键路由：修复通过 → 重跑冒烟 ──
        TeamEdge(
            source="tester", target="smoke_runner",
            condition="pass", label="修复后重验冒烟测试",
        ),

        # ── 回路 A：假设证否 → 收集证据 → 重新假设 ──
        TeamEdge(
            source="probe_executor", target="evidence_collector",
            condition="fail", label="假设证否",
        ),
        TeamEdge(source="evidence_collector", target="hypothesis_generator"),

        # ── 回路 B：复测失败 → 回归归因 → 重新假设 ──
        TeamEdge(source="tester",              target="regression_analyzer",    condition="fail"),
        TeamEdge(source="regression_analyzer", target="regression_to_context",  condition="pass"),
        TeamEdge(source="regression_to_context", target="hypothesis_generator"),
    ]

    return TeamSpec(
        id="lang-rewrite-verifier",
        name="LangRewrite Smoke Verifier + Auto-Fixer",
        description=(
            "翻译后端到端验证管线：AgentNodeLoop 生成冒烟测试 → 执行验证 → "
            "debugger 假设驱动修复循环 → 修复后重验冒烟，直到全部通过或预算耗尽"
        ),
        nodes=nodes,
        edges=edges,
        entry="smoke_gen",
        tags=["rust", "smoke-test", "debugger", "post-rewrite"],
    )
