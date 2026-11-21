# [OMNI] origin=claude-code domain=selftest/pipeline.py ts=2026-04-08T03:23:37Z
# [OMNI] material_id="material:core.selftest.team_spec.pipeline_definition.py"
"""selftest — TeamSpec 声明"""

from __future__ import annotations

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


def build_team() -> TeamSpec:
    nodes = [
        # 1. 注册检查 — 验证所有管线可加载、bindings 完整
        TeamNode(
            id="registry_checker",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id="selftest-registry-checker",
                name="RegistryChecker",
                description="调用 register_all()，逐一验证 build_team/build_bindings 和 bindings 完整性",
                from_format="selftest.request",
                to_format="selftest.registry-report",
                method=TransformMethod.RULE,
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # 2. 功能测试 — 确定性冒烟 + EventBus 读写往返
        TeamNode(
            id="functional_tester",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id="selftest-functional-tester",
                name="FunctionalTester",
                description="DomainScanner 冒烟 + EventBus 读写 + TeamChecker + CLI health",
                from_format="selftest.registry-report",
                to_format="selftest.selftest-report",
                method=TransformMethod.RULE,
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # 3. 门控 — HARD Anchor（PASS → llm_reporter，FAIL → HALT）
        TeamNode(
            id="selftest_gate",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="selftest-gate",
                name="SelftestGate",
                format_in="selftest.selftest-report",
                format_out="selftest.selftest-report",
                validator=ValidatorSpec(
                    id="selftest-gate-validator",
                    kind=ValidatorKind.HARD,
                    description="Selftest 门控：failed_checks == 0 时通过，否则失败",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="llm_reporter"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # 4. LLM 报告 — SOFT Anchor（LLM 不可用时降级，始终 EMIT）
        TeamNode(
            id="llm_reporter",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="selftest-llm-reporter",
                name="LLMReporter",
                format_in="selftest.selftest-report",
                format_out="selftest.health-report",
                validator=ValidatorSpec(
                    id="selftest-llm-reporter-validator",
                    kind=ValidatorKind.SOFT,
                    description="调用 LLM 验证端点连通性并生成自然语言摘要；LLM 不可用时降级返回 PASS",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.EMIT),
                    VerdictKind.FAIL: Route(action=RouteAction.EMIT),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),
    ]

    edges = [
        TeamEdge(source="registry_checker", target="functional_tester"),
        TeamEdge(source="functional_tester", target="selftest_gate"),
        TeamEdge(source="selftest_gate", target="llm_reporter"),
    ]

    return TeamSpec(
        id="omnicompany-selftest",
        name="OmniCompany Selftest",
        description="OmniCompany e2e 功能自测 — 验证管线注册、bindings 完整性、EventBus 和 CLI 基础功能",
        nodes=nodes,
        edges=edges,
        entry="registry_checker",
    )
