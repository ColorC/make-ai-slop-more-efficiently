# [OMNI] origin=human domain=software_engineering/equiv_test ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.equiv_test.pipeline_topology.declaration.py"
"""equivalence_test.pipeline — 跨语言语义等价性测试管线 V2 [EXPERIMENTAL]

Golden File 模式 + Baseline 红绿验证:

  test_designer → golden_recorder → baseline_check → ts_test_gen → ts_executor → comparator
   (LLM)          (LLM+运行)        (确定性)         (LLM)         (确定性)      (确定性)
                                                                                     │
                                                                            PASS(全匹配) → EMIT
                                                                            有不匹配    ↓
                                                                               failure_analyzer
                                                                                 (LLM)
                                                                                   │
                                                                              PASS → EMIT(带诊断)
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

DOMAIN = "equiv"


def build_team() -> TeamSpec:
    nodes = [
        TeamNode(
            id="test_designer",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-design",
                name="TestDesigner",
                format_in=f"{DOMAIN}.test-spec",
                format_out=f"{DOMAIN}.test-spec",
                validator=ValidatorSpec(id=f"{DOMAIN}-design-v", kind=ValidatorKind.SOFT,
                    description="LLM 设计测试用例清单"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="golden_recorder"),
                    VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=2),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        TeamNode(
            id="golden_recorder",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-golden",
                name="GoldenRecorder",
                format_in=f"{DOMAIN}.test-spec",
                format_out=f"{DOMAIN}.test-suite",
                validator=ValidatorSpec(id=f"{DOMAIN}-golden-v", kind=ValidatorKind.SOFT,
                    description="LLM 生成 Python 录制脚本 → 实际运行 → golden JSON"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="baseline_check"),
                    VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=2),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        TeamNode(
            id="baseline_check",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-baseline",
                name="BaselineCheck",
                from_format=f"{DOMAIN}.test-suite",
                to_format=f"{DOMAIN}.test-suite",
                method=TransformMethod.RULE,
                description="空 stub 红灯验证 — 确保测试能抓到假货",
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        TeamNode(
            id="ts_test_gen",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-tsgen",
                name="TSTestGenerator",
                format_in=f"{DOMAIN}.test-suite",
                format_out=f"{DOMAIN}.test-suite",
                validator=ValidatorSpec(id=f"{DOMAIN}-tsgen-v", kind=ValidatorKind.SOFT,
                    description="LLM 根据 golden keys + TS 代码生成对比脚本"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="ts_executor"),
                    VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=2),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        TeamNode(
            id="ts_executor",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-exec",
                name="TSExecutor",
                format_in=f"{DOMAIN}.test-suite",
                format_out=f"{DOMAIN}.execution-result",
                validator=ValidatorSpec(id=f"{DOMAIN}-exec-v", kind=ValidatorKind.HARD,
                    description="运行 TS 测试脚本，收集 JSON 输出"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="result_comparator"),
                    VerdictKind.FAIL: Route(action=RouteAction.NEXT, target="result_comparator",
                                            feedback="TS 执行失败，带错误信息继续"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        TeamNode(
            id="result_comparator",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-compare",
                name="ResultComparator",
                from_format=f"{DOMAIN}.execution-result",
                to_format=f"{DOMAIN}.comparison-report",
                method=TransformMethod.RULE,
                description="golden vs TS 输出逐 key 对比",
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        TeamNode(
            id="failure_analyzer",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-analyze",
                name="FailureAnalyzer",
                format_in=f"{DOMAIN}.comparison-report",
                format_out=f"{DOMAIN}.diagnosed-report",
                validator=ValidatorSpec(id=f"{DOMAIN}-analyze-v", kind=ValidatorKind.SOFT,
                    description="LLM 分析不匹配根因"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.EMIT),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),
    ]

    edges = [
        TeamEdge(source="test_designer", target="golden_recorder", condition="pass"),
        TeamEdge(source="golden_recorder", target="baseline_check", condition="pass"),
        TeamEdge(source="baseline_check", target="ts_test_gen"),
        TeamEdge(source="ts_test_gen", target="ts_executor", condition="pass"),
        TeamEdge(source="ts_executor", target="result_comparator"),
        TeamEdge(source="result_comparator", target="failure_analyzer"),
    ]

    return TeamSpec(
        id=f"{DOMAIN}-pipeline",
        name="Cross-Language Equivalence Test Pipeline V2 [EXPERIMENTAL]",
        description="Golden File 模式：Python 录制 → Baseline 红绿 → TS 对比 → 诊断",
        nodes=nodes, edges=edges, entry="test_designer",
        tags=["equivalence", "testing", "golden-file"],
    )
