# [OMNI] origin=human domain=software_engineering/review ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.review.dag_topology.definition.py"
"""sw_review.pipeline — 代码审查管线拓扑

DAG (7 节点, 1 信息收集回路):

  diff_collector → context_gatherer → test_searcher → sufficiency_judge
                        ↑                                    ↓ (PARTIAL → 回路)
                        └────────────────────────────────────┘
                                                             ↓ (PASS)
                                                      deep_reviewer → finding_validator
                                                                            ↓
                                                                     report_formatter → (EMIT)
"""

from omnicompany.protocol.team import (
    TeamSpec, TeamNode, NodeKind, NodeMaturity,
)
from omnicompany.protocol.anchor import (
    AnchorSpec, ValidatorSpec, ValidatorKind,
    Route, RouteAction, VerdictKind,
)

DOMAIN = "sw_review"


def build_team() -> TeamSpec:
    nodes = [
        # ── 1. Diff 收集 ──
        TeamNode(
            id="diff_collector",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-diff-collect",
                name="DiffCollector",
                format_in=f"{DOMAIN}.diff",
                format_out=f"{DOMAIN}.review-context",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-diff-v", kind=ValidatorKind.HARD,
                    description="收集 git diff 或 diff 文本，解析变更文件列表"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="context_gatherer"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="diff 收集失败"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 2. 上下文收集 ──
        TeamNode(
            id="context_gatherer",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-ctx-gather",
                name="ContextGatherer",
                format_in=f"{DOMAIN}.review-context",
                format_out=f"{DOMAIN}.context",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-ctx-v", kind=ValidatorKind.HARD,
                    description="读取每个修改文件的 imports、函数签名"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="test_searcher"),
                    VerdictKind.FAIL: Route(action=RouteAction.NEXT, target="test_searcher"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 3. 测试搜索 ──
        TeamNode(
            id="test_searcher",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-test-search",
                name="TestSearcher",
                format_in=f"{DOMAIN}.context",
                format_out=f"{DOMAIN}.test-coverage",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-test-v", kind=ValidatorKind.HARD,
                    description="搜索变更文件的对应测试文件"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="sufficiency_judge"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 4. 充分性判定（回路触发点）──
        TeamNode(
            id="sufficiency_judge",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-sufficiency",
                name="SufficiencyJudge",
                format_in=f"{DOMAIN}.test-coverage",
                format_out=f"{DOMAIN}.review-context",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-suff-v", kind=ValidatorKind.SOFT,
                    description="判断上下文是否足以进行深度审查"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="deep_reviewer",
                                            feedback="上下文充分"),
                    VerdictKind.PARTIAL: Route(action=RouteAction.NEXT, target="context_gatherer",
                                               feedback="上下文不充分，继续收集"),
                    VerdictKind.FAIL: Route(action=RouteAction.NEXT, target="deep_reviewer",
                                            feedback="信息收集已达上限"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 5. LLM 深度审查 ──
        TeamNode(
            id="deep_reviewer",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-deep-review",
                name="DeepReviewer",
                format_in=f"{DOMAIN}.review-context",
                format_out=f"{DOMAIN}.findings",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-review-v", kind=ValidatorKind.SOFT,
                    description="LLM 多维度代码审查"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="finding_validator"),
                    VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=2),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 6. 交叉验证 ──
        TeamNode(
            id="finding_validator",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-validate",
                name="FindingValidator",
                format_in=f"{DOMAIN}.findings",
                format_out=f"{DOMAIN}.validated-findings",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-fval-v", kind=ValidatorKind.HARD,
                    description="交叉验证 Critical/Important 发现是否有代码证据"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="report_formatter"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 7. 报告输出 ──
        TeamNode(
            id="report_formatter",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-report",
                name="ReportFormatter",
                format_in=f"{DOMAIN}.validated-findings",
                format_out=f"{DOMAIN}.report",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-report-v", kind=ValidatorKind.HARD,
                    description="汇总输出审查报告"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.EMIT, feedback="审查完成 APPROVE"),
                    VerdictKind.FAIL: Route(action=RouteAction.EMIT, feedback="审查完成 REQUEST_CHANGES"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),
    ]

    return TeamSpec(
        id=f"pipeline-{DOMAIN}",
        name="代码审查管线",
        description="diff 收集 → 上下文探索(回路) → LLM 审查 → 交叉验证 → 报告",
        nodes=nodes,
        edges=[],
        entry="diff_collector",
    )


# ── Bindings ──────────────────────────────────────────────────────────────────
