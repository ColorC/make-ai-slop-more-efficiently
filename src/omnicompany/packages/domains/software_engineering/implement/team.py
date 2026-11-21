# [OMNI] origin=human domain=software_engineering/implement ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.implement.team_topology.dag.py"
"""sw_implement.pipeline — 独立实施管线拓扑

DAG (5 节点, 1 上下文收集回路):

  req_parser → codebase_scanner → context_judge
                     ↑                  ↓ (PARTIAL → 回路)
                     └──────────────────┘
                                         ↓ (PASS)
                                    implementor → report_emitter → (EMIT)
"""

from omnicompany.protocol.team import (
    TeamSpec, TeamNode, NodeKind, NodeMaturity,
)
from omnicompany.protocol.anchor import (
    AnchorSpec,
    ValidatorSpec, ValidatorKind,
    Route, RouteAction, VerdictKind,
)

DOMAIN = "sw_implement"


def build_team() -> TeamSpec:
    nodes = [
        # ── 1. 需求解析 ──
        TeamNode(
            id="req_parser",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-req-parse",
                name="ReqParser",
                format_in=f"{DOMAIN}.task",
                format_out=f"{DOMAIN}.snapshot",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-req-v", kind=ValidatorKind.HARD,
                    description="解析实施需求"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="codebase_scanner"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="需求解析失败"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 2. 代码库扫描 + 读取 ──
        TeamNode(
            id="codebase_scanner",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-scan",
                name="CodebaseScanner",
                format_in=f"{DOMAIN}.snapshot",
                format_out=f"{DOMAIN}.context-state",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-scan-v", kind=ValidatorKind.HARD,
                    description="扫描项目目录并读取关键文件"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="context_judge"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="扫描失败"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 3. 上下文充分性判定（回路触发点）──
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
                    description="判断是否已收集足够上下文"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="implementor",
                                            feedback="上下文充分"),
                    VerdictKind.PARTIAL: Route(action=RouteAction.NEXT, target="codebase_scanner",
                                               feedback="继续探索"),
                    VerdictKind.FAIL: Route(action=RouteAction.NEXT, target="implementor",
                                            feedback="最大探索轮次"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 4. 实施（LLM / agent_loop 节点）──
        TeamNode(
            id="implementor",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-impl",
                name="Implementor",
                format_in=f"{DOMAIN}.context-state",
                format_out=f"{DOMAIN}.changes",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-impl-v", kind=ValidatorKind.SOFT,
                    description="LLM 生成实现代码"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="report_emitter"),
                    VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=2),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 5. 报告输出 ──
        TeamNode(
            id="report_emitter",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-report",
                name="ReportEmitter",
                format_in=f"{DOMAIN}.changes",
                format_out=f"{DOMAIN}.report",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-report-v", kind=ValidatorKind.HARD,
                    description="汇总实施报告"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.EMIT, feedback="实施完成"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),
    ]

    return TeamSpec(
        id=f"pipeline-{DOMAIN}",
        name="独立实施管线",
        description="需求 → 代码库扫描(回路) → 上下文判定 → LLM实施 → 报告",
        nodes=nodes,
        edges=[],
        entry="req_parser",
    )


# ── Bindings ──────────────────────────────────────────────────────────────────
