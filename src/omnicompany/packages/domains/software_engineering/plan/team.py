# [OMNI] origin=human domain=software_engineering/plan ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.plan.dag_topology.definition.py"
"""sw_plan.pipeline — 实施计划管线拓扑

DAG (8 节点, 2 回路):

  spec_loader → codebase_scanner → file_reader → context_judge
                     ↑                                ↓ (PARTIAL → 回路1)
                     └────────────────────────────────┘
                                                      ↓ (PASS)
                                               file_mapper → plan_drafter → self_reviewer
                                                                  ↑              ↓ (FAIL → 回路2)
                                                                  └──────────────┘
                                                                            ↓ (PASS)
                                                                       plan_emitter → (EMIT)
"""

from omnicompany.protocol.team import (
    TeamSpec, TeamNode, NodeKind, NodeMaturity,
)
from omnicompany.protocol.anchor import (
    AnchorSpec, ValidatorSpec, ValidatorKind,
    Route, RouteAction, VerdictKind,
)

DOMAIN = "sw_plan"


def build_team() -> TeamSpec:
    nodes = [
        # ── 1. 设计文档加载 ──
        TeamNode(
            id="spec_loader",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-spec-load",
                name="SpecLoader",
                format_in=f"{DOMAIN}.spec",
                format_out=f"{DOMAIN}.code-context",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-spec-v", kind=ValidatorKind.HARD,
                    description="读取设计文档/需求文本"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="codebase_scanner"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="设计文档加载失败"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 2. 代码库扫描 ──
        TeamNode(
            id="codebase_scanner",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-codebase-scan",
                name="CodebaseScanner",
                format_in=f"{DOMAIN}.code-context",
                format_out=f"{DOMAIN}.codebase-scan",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-scan-v", kind=ValidatorKind.HARD,
                    description="扫描项目目录结构，识别关键文件"),
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
                format_in=f"{DOMAIN}.codebase-scan",
                format_out=f"{DOMAIN}.code-context",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-read-v", kind=ValidatorKind.HARD,
                    description="读取关键文件内容和模式"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="context_judge"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 4. 上下文充分性判定（回路1 触发点）──
        TeamNode(
            id="context_judge",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-ctx-judge",
                name="ContextJudge",
                format_in=f"{DOMAIN}.code-context",
                format_out=f"{DOMAIN}.code-context",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-judge-v", kind=ValidatorKind.SOFT,
                    description="判断上下文是否足以生成计划"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="file_mapper",
                                            feedback="上下文充分"),
                    VerdictKind.PARTIAL: Route(action=RouteAction.NEXT, target="codebase_scanner",
                                               feedback="继续探索代码库"),
                    VerdictKind.FAIL: Route(action=RouteAction.NEXT, target="file_mapper",
                                            feedback="达到最大探索轮次"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 5. 文件映射 ──
        TeamNode(
            id="file_mapper",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-file-map",
                name="FileMapper",
                format_in=f"{DOMAIN}.code-context",
                format_out=f"{DOMAIN}.file-map",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-map-v", kind=ValidatorKind.SOFT,
                    description="LLM 确定文件新建/修改/删除计划"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="plan_drafter"),
                    VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=2),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 6. 计划生成 ──
        TeamNode(
            id="plan_drafter",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-plan-draft",
                name="PlanDrafter",
                format_in=f"{DOMAIN}.file-map",
                format_out=f"{DOMAIN}.draft",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-draft-v", kind=ValidatorKind.SOFT,
                    description="LLM 生成 TDD 分步实施计划"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="self_reviewer"),
                    VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=2),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 7. 自检（回路2 触发点）──
        TeamNode(
            id="self_reviewer",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-self-review",
                name="SelfReviewer",
                format_in=f"{DOMAIN}.draft",
                format_out=f"{DOMAIN}.review-result",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-review-v", kind=ValidatorKind.HARD,
                    description="零占位符 + 结构完整性验证"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="plan_emitter"),
                    VerdictKind.PARTIAL: Route(action=RouteAction.NEXT, target="plan_emitter",
                                               feedback="超出修改次数，输出当前版本"),
                    VerdictKind.FAIL: Route(action=RouteAction.NEXT, target="plan_drafter",
                                            feedback="自检失败，重新生成计划"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 8. 终版输出 ──
        TeamNode(
            id="plan_emitter",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-emit",
                name="PlanEmitter",
                format_in=f"{DOMAIN}.review-result",
                format_out=f"{DOMAIN}.plan",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-emit-v", kind=ValidatorKind.HARD,
                    description="输出终版计划 + 质量报告"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.EMIT, feedback="计划生成完成"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),
    ]

    return TeamSpec(
        id=f"pipeline-{DOMAIN}",
        name="实施计划管线",
        description="设计文档 → 代码库探索(回路) → 文件映射 → TDD 计划 → 自检(回路) → 终版",
        nodes=nodes,
        edges=[],
        entry="spec_loader",
    )


# ── Bindings ──────────────────────────────────────────────────────────────────
