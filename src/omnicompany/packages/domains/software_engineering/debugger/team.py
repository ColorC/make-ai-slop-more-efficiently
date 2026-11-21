# [OMNI] origin=human domain=software_engineering/debugger ts=2026-04-08T03:23:41Z
# [OMNI] material_id="material:domains.software_engineering.debugger.pipeline_topology.declaration.py"
"""debugger.pipeline — 假设驱动调试管线拓扑

核心是"假设-验证-修正"循环，贯穿一个累积的 debug-context。

DAG:

  error_analyzer → context_init → hypothesis_generator → probe_designer
                       ↑                                      ↓
                  evidence_collector                    probe_executor
                   ↑           ↑                         ↙       ↓
                   │           │                   (证否)      (证实)
                   │           │                                 ↓
                   │    regression_analyzer ← tester ← fixer
                   │                          (PASS→EMIT)
                   └──────────────────────────────────────┘

evidence_collector 是所有回路的归一点。
"""

from omnicompany.protocol.team import (
    TeamSpec, TeamNode, TeamEdge,
    NodeKind, NodeMaturity,
)
from omnicompany.protocol.anchor import (
    AnchorSpec, TransformerSpec, TransformMethod,
    ValidatorSpec, ValidatorKind,
    Route, RouteAction, VerdictKind,
)

DOMAIN = "debug"


def build_team() -> TeamSpec:
    nodes = [
        # ── 1. 错误分析（入口）──
        TeamNode(
            id="error_analyzer",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-error-analyze",
                name="ErrorAnalyzer",
                format_in=f"{DOMAIN}.error-report",
                format_out=f"{DOMAIN}.error-analysis",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-erranalyze-v",
                    kind=ValidatorKind.SOFT,
                    description="LLM 阅读错误输出和错误位置代码，判断直接原因",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="context_init"),
                    VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=2),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 2. 初始化 debug-context（只走一次）──
        TeamNode(
            id="context_init",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-ctx-init",
                name="ContextInit",
                from_format=f"{DOMAIN}.error-analysis",
                to_format=f"{DOMAIN}.debug-context",
                method=TransformMethod.RULE,
                description="将首次错误分析包装为初始 debug-context",
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 3. 假设生成（接受 debug-context）──
        TeamNode(
            id="hypothesis_generator",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-hypothesis-gen",
                name="HypothesisGenerator",
                format_in=f"{DOMAIN}.debug-context",
                format_out=f"{DOMAIN}.hypothesis",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-hypothesis-v",
                    kind=ValidatorKind.SOFT,
                    description="LLM 根据累积 debug-context 追踪依赖来源，提出根因假设",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="probe_designer"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT,
                                            feedback="无法提出新假设，需人工介入"),
                },
            ),
            maturity=NodeMaturity.HYPOTHETICAL,
        ),

        # ── 4. 试探设计 ──
        TeamNode(
            id="probe_designer",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-probe-design",
                name="ProbeDesigner",
                format_in=f"{DOMAIN}.hypothesis",
                format_out=f"{DOMAIN}.probe-plan",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-probe-v",
                    kind=ValidatorKind.SOFT,
                    description="LLM 为假设设计试探：读哪些文件、写什么测试来证实/证否",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="probe_executor"),
                    VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=2),
                },
            ),
            maturity=NodeMaturity.HYPOTHETICAL,
        ),

        # ── 5. 试探执行 ──
        TeamNode(
            id="probe_executor",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-probe-exec",
                name="ProbeExecutor",
                format_in=f"{DOMAIN}.probe-plan",
                format_out=f"{DOMAIN}.probe-result",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-probeexec-v",
                    kind=ValidatorKind.HARD,
                    description="执行试探（读文件/运行测试），判定假设证实/证否/不确定",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="fixer",
                                            feedback="假设证实"),
                    VerdictKind.FAIL: Route(action=RouteAction.NEXT, target="evidence_collector",
                                            feedback="假设证否"),
                    VerdictKind.PARTIAL: Route(action=RouteAction.NEXT, target="evidence_collector",
                                               feedback="证据不充分，需补充试探"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 5b. 回路归一（probe-result/regression-analysis → debug-context）──
        TeamNode(
            id="evidence_collector",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-evidence-collect",
                name="EvidenceCollector",
                from_format=f"{DOMAIN}.probe-result",
                to_format=f"{DOMAIN}.debug-context",
                method=TransformMethod.RULE,
                description="将试探结果/回归分析追加到 debug-context，保留假设历史和证据链",
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 6. 修复 ──
        TeamNode(
            id="fixer",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-fix",
                name="Fixer",
                format_in=f"{DOMAIN}.probe-result",
                format_out=f"{DOMAIN}.fix-patch",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-fix-v",
                    kind=ValidatorKind.SOFT,
                    description="LLM 根据证实的假设生成修复补丁",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="tester"),
                    VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=2),
                },
            ),
            maturity=NodeMaturity.HYPOTHETICAL,
        ),

        # ── 7. 复测 ──
        TeamNode(
            id="tester",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-test",
                name="Tester",
                format_in=f"{DOMAIN}.fix-patch",
                format_out=f"{DOMAIN}.test-feedback",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-test-v",
                    kind=ValidatorKind.HARD,
                    description="应用补丁并运行编译器/测试",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.EMIT),
                    VerdictKind.FAIL: Route(action=RouteAction.NEXT, target="regression_analyzer"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 8. 回归分析 ──
        TeamNode(
            id="regression_analyzer",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-regression",
                name="RegressionAnalyzer",
                format_in=f"{DOMAIN}.test-feedback",
                format_out=f"{DOMAIN}.regression-analysis",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-regress-v",
                    kind=ValidatorKind.SOFT,
                    description="LLM 归因：假设错（回退）、实现错（改法不对）、还是新问题（部分正确）",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="regression_to_context",
                                            feedback="带结论回到假设循环"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT,
                                            feedback="调试预算耗尽"),
                },
            ),
            maturity=NodeMaturity.HYPOTHETICAL,
        ),

        # ── 8b. 回归结论归一 ──
        TeamNode(
            id="regression_to_context",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-regress-to-ctx",
                name="RegressionToContext",
                from_format=f"{DOMAIN}.regression-analysis",
                to_format=f"{DOMAIN}.debug-context",
                method=TransformMethod.RULE,
                description="将回归分析结论（回退/新问题/假设修正）追加到 debug-context",
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),
    ]

    edges = [
        # 主路径
        TeamEdge(source="error_analyzer", target="context_init"),
        TeamEdge(source="context_init", target="hypothesis_generator"),
        TeamEdge(source="hypothesis_generator", target="probe_designer"),
        TeamEdge(source="probe_designer", target="probe_executor"),
        TeamEdge(source="probe_executor", target="fixer",
                     condition="pass", label="假设证实"),
        TeamEdge(source="fixer", target="tester"),

        # 回路 A：假设证否 → 收集证据 → 重新假设
        TeamEdge(source="probe_executor", target="evidence_collector",
                     condition="fail", label="假设证否"),
        TeamEdge(source="evidence_collector", target="hypothesis_generator",
                     label="更新上下文后重新假设"),

        # 回路 B：证据不足 → 同样走 evidence_collector → hypothesis_generator
        # （PARTIAL 和 FAIL 都走同一条回路，hypothesis_generator 根据上下文决定是补充试探还是换假设）

        # 回路 C：复测失败 → 归因 → 重新假设
        TeamEdge(source="tester", target="regression_analyzer",
                     condition="fail"),
        TeamEdge(source="regression_analyzer", target="regression_to_context",
                     condition="pass"),
        TeamEdge(source="regression_to_context", target="hypothesis_generator",
                     label="回归结论→重新假设"),
    ]

    return TeamSpec(
        id=f"{DOMAIN}-pipeline",
        name="Hypothesis-Driven Debugger",
        description="假设驱动的通用调试工作流：错误分析→假设→试探验证→修复→复测，"
                    "带回路的累积循环直到所有错误清零或预算耗尽",
        nodes=nodes,
        edges=edges,
        entry="error_analyzer",
        tags=["debug", "hypothesis-driven", "cross-language"],
    )
