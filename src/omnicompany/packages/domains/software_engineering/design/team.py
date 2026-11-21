# [OMNI] origin=human domain=software_engineering/design ts=2026-04-08T03:23:41Z
# [OMNI] material_id="material:domains.software_engineering.design.pipeline_topology.declaration.py"
"""sw_design.pipeline — 设计审查管线拓扑

DAG (7 节点, 1 回路):

  spec_parser → arch_scanner → file_reader → context_judge
                     ↑                            ↓ (PARTIAL → 回路)
                     └────────────────────────────┘
                                                   ↓ (PASS)
                                            pattern_analyzer → design_reviewer → report_formatter → (EMIT)
"""

from omnicompany.protocol.team import (
    TeamSpec, TeamNode, NodeKind, NodeMaturity,
)
from omnicompany.protocol.anchor import (
    AnchorSpec,
    ValidatorSpec, ValidatorKind,
    Route, RouteAction, VerdictKind,
)

DOMAIN = "sw_design"


def build_team() -> TeamSpec:
    nodes = [
        # ── 1. 设计文档解析 ──
        TeamNode(
            id="spec_parser",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-spec-parse",
                name="SpecParser",
                format_in=f"{DOMAIN}.task",
                format_out=f"{DOMAIN}.snapshot",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-spec-v", kind=ValidatorKind.HARD,
                    description="解析设计文档，提取目标和范围"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="arch_scanner"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="设计文档解析失败"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 2. 架构扫描 ──
        TeamNode(
            id="arch_scanner",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-arch-scan",
                name="ArchScanner",
                format_in=f"{DOMAIN}.snapshot",
                format_out=f"{DOMAIN}.context-state",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-scan-v", kind=ValidatorKind.HARD,
                    description="扫描项目目录，识别架构分层和关键文件"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="file_reader"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="目录扫描失败"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 3. 文件读取 ──
        TeamNode(
            id="file_reader",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-file-read",
                name="FileReader",
                format_in=f"{DOMAIN}.context-state",
                format_out=f"{DOMAIN}.context-state",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-read-v", kind=ValidatorKind.HARD,
                    description="读取关键文件内容和接口签名"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="context_judge"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 4. 上下文充分性判定（回路触发点）──
        TeamNode(
            id="context_judge",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-ctx-judge",
                name="ContextJudge",
                format_in=f"{DOMAIN}.context-state",
                format_out=f"{DOMAIN}.context-state",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-judge-v", kind=ValidatorKind.SOFT,
                    description="判断是否已收集足够信息进行设计审查"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="pattern_analyzer",
                                            feedback="上下文充分"),
                    VerdictKind.PARTIAL: Route(action=RouteAction.NEXT, target="arch_scanner",
                                               feedback="继续探索代码库"),
                    VerdictKind.FAIL: Route(action=RouteAction.NEXT, target="pattern_analyzer",
                                            feedback="达到最大探索轮次"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 5. 架构模式分析 ──
        TeamNode(
            id="pattern_analyzer",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-pattern-analyze",
                name="PatternAnalyzer",
                format_in=f"{DOMAIN}.context-state",
                format_out=f"{DOMAIN}.patterns",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-pattern-v", kind=ValidatorKind.HARD,
                    description="分析现有架构模式（命名、分层、测试、依赖注入）"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="design_reviewer"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 6. 设计审查（LLM）──
        TeamNode(
            id="design_reviewer",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-design-review",
                name="DesignReviewer",
                format_in=f"{DOMAIN}.patterns",
                format_out=f"{DOMAIN}.review",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-review-v", kind=ValidatorKind.SOFT,
                    description="LLM 评审一致性、可行性、风险、完整性"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="report_formatter"),
                    VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=2),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 7. 报告格式化 ──
        TeamNode(
            id="report_formatter",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-report-fmt",
                name="ReportFormatter",
                format_in=f"{DOMAIN}.review",
                format_out=f"{DOMAIN}.report",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-report-v", kind=ValidatorKind.HARD,
                    description="格式化设计审查报告"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.EMIT, feedback="设计审查完成"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),
    ]

    return TeamSpec(
        id=f"pipeline-{DOMAIN}",
        name="设计审查管线",
        description="设计文档 → 架构扫描 → 文件读取 → 上下文判定(回路) → 模式分析 → LLM审查 → 报告",
        nodes=nodes,
        edges=[],
        entry="spec_parser",
    )


# ── Bindings ──────────────────────────────────────────────────────────────────
