# [OMNI] origin=human domain=software_engineering/verify ts=2026-04-08T03:23:43Z
# [OMNI] material_id="material:domains.software_engineering.verify.dag_topology.definition.py"
"""sw_verify.pipeline — 验证管线拓扑

DAG (7 节点, 1 回路):

  claim_parser → env_checker → cmd_executor → output_analyzer
                                                    ↓ (CONFIRMED → report_emitter → EMIT)
                                                    ↓ (REFUTED  → report_emitter → EMIT)
                                                    ↓ (UNCERTAIN)
                                             supplemental_designer → cmd_executor [回路]
"""

from omnicompany.protocol.team import (
    TeamSpec, TeamNode, NodeKind, NodeMaturity,
)
from omnicompany.protocol.anchor import (
    AnchorSpec, ValidatorSpec, ValidatorKind,
    Route, RouteAction, VerdictKind,
)

DOMAIN = "sw_verify"


def build_team() -> TeamSpec:
    nodes = [
        # ── 1. 声称解析（确定性）──
        TeamNode(
            id="claim_parser",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-claim-parse",
                name="ClaimParser",
                format_in=f"{DOMAIN}.claim",
                format_out=f"{DOMAIN}.env-check",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-parse-v",
                    kind=ValidatorKind.HARD,
                    description="解析声称文本，推断预期模式，初始化 verify-context",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="env_checker"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT,
                                            feedback="声称解析失败"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 2. 环境检查（HARD）──
        TeamNode(
            id="env_checker",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-env-check",
                name="EnvChecker",
                format_in=f"{DOMAIN}.env-check",
                format_out=f"{DOMAIN}.verify-context",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-env-v",
                    kind=ValidatorKind.HARD,
                    description="检查工作目录存在、命令可执行",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="cmd_executor"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT,
                                            feedback="验证环境不就绪"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 3. 命令执行（HARD）──
        TeamNode(
            id="cmd_executor",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-cmd-exec",
                name="CmdExecutor",
                format_in=f"{DOMAIN}.verify-context",
                format_out=f"{DOMAIN}.execution",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-exec-v",
                    kind=ValidatorKind.HARD,
                    description="执行验证命令，捕获 stdout/stderr/exit_code",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="output_analyzer"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT,
                                            feedback="命令执行失败（超时或异常）"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 4. 输出分析（SOFT，三态判定）──
        TeamNode(
            id="output_analyzer",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-output-analyze",
                name="OutputAnalyzer",
                format_in=f"{DOMAIN}.execution",
                format_out=f"{DOMAIN}.analysis",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-analyze-v",
                    kind=ValidatorKind.SOFT,
                    description="分析命令输出 vs 声称: CONFIRMED / REFUTED / UNCERTAIN",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="report_emitter",
                                            feedback="声称得到证实"),
                    VerdictKind.FAIL: Route(action=RouteAction.NEXT, target="report_emitter",
                                            feedback="声称被否定"),
                    VerdictKind.PARTIAL: Route(action=RouteAction.NEXT,
                                               target="supplemental_designer",
                                               feedback="证据不确定，需补充验证"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 5. 补充验证设计（SOFT，回路入口）──
        TeamNode(
            id="supplemental_designer",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-supp-design",
                name="SupplementalDesigner",
                format_in=f"{DOMAIN}.analysis",
                format_out=f"{DOMAIN}.verify-context",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-supp-v",
                    kind=ValidatorKind.SOFT,
                    description="UNCERTAIN 时设计补充验证命令",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="cmd_executor"),
                    VerdictKind.FAIL: Route(action=RouteAction.NEXT, target="report_emitter",
                                            feedback="无法设计补充验证，以当前判定出报告"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 6. 最终报告（确定性）──
        TeamNode(
            id="report_emitter",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-report",
                name="ReportEmitter",
                format_in=f"{DOMAIN}.analysis",
                format_out=f"{DOMAIN}.report",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-report-v",
                    kind=ValidatorKind.HARD,
                    description="汇总所有证据，生成最终验证报告",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.EMIT,
                                            feedback="✅ 声称验证通过"),
                    VerdictKind.FAIL: Route(action=RouteAction.EMIT,
                                            feedback="❌ 声称验证失败"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),
    ]

    return TeamSpec(
        id=f"pipeline-{DOMAIN}",
        name="软件验证管线",
        description="验证声称是否有 evidence-based 支持 (7 节点, 1 补充验证回路)",
        nodes=nodes,
        edges=[],
        entry="claim_parser",
    )


# ── Bindings ──────────────────────────────────────────────────────────────────
