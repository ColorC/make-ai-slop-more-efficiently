# [OMNI] origin=human domain=software_engineering/tdd ts=2026-04-08T03:23:43Z
# [OMNI] material_id="material:domains.software_engineering.tdd.dag_topology.definition.py"
"""sw_tdd.pipeline — TDD 执行管线拓扑

DAG (5 节点, 1 修复回路):

  plan_loader → test_writer → test_runner
                                     ↓ (PASS)
                                report_emitter → (EMIT)
                                     ↓ (FAIL)
                              impl_writer → test_runner    ← 回路 (最多 3 轮)
"""

from omnicompany.protocol.team import (
    TeamSpec, TeamNode, NodeKind, NodeMaturity,
)
from omnicompany.protocol.anchor import (
    AnchorSpec,
    ValidatorSpec, ValidatorKind,
    Route, RouteAction, VerdictKind,
)

DOMAIN = "sw_tdd"


def build_team() -> TeamSpec:
    nodes = [
        # ── 1. 计划加载 ──
        TeamNode(
            id="plan_loader",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-plan-load",
                name="PlanLoader",
                format_in=f"{DOMAIN}.plan",
                format_out=f"{DOMAIN}.test-code",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-plan-v", kind=ValidatorKind.HARD,
                    description="加载 TDD 实施计划"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="test_writer"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="计划加载失败"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 2. 测试生成（LLM / agent_loop 节点）──
        TeamNode(
            id="test_writer",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-test-write",
                name="TestWriter",
                format_in=f"{DOMAIN}.plan",
                format_out=f"{DOMAIN}.test-code",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-test-write-v", kind=ValidatorKind.SOFT,
                    description="LLM 生成测试代码"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="test_runner"),
                    VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=2),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 3. 测试执行 ──
        TeamNode(
            id="test_runner",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-test-run",
                name="TestRunner",
                format_in=f"{DOMAIN}.test-code",
                format_out=f"{DOMAIN}.test-result",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-test-run-v", kind=ValidatorKind.HARD,
                    description="执行测试命令，捕获结果"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="report_emitter",
                                            feedback="测试全部通过"),
                    VerdictKind.FAIL: Route(action=RouteAction.NEXT, target="impl_writer",
                                            feedback="测试失败，需要写实现"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 4. 实现生成（LLM / agent_loop 节点, 回路触发点）──
        TeamNode(
            id="impl_writer",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-impl-write",
                name="ImplWriter",
                format_in=f"{DOMAIN}.test-result",
                format_out=f"{DOMAIN}.impl-code",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-impl-write-v", kind=ValidatorKind.SOFT,
                    description="LLM 生成实现代码使测试通过"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="test_runner",
                                            feedback="实现代码已生成，重新执行测试"),
                    VerdictKind.FAIL: Route(action=RouteAction.NEXT, target="report_emitter",
                                            feedback="实现生成失败，输出当前报告"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 5. 报告输出 ──
        TeamNode(
            id="report_emitter",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-report-emit",
                name="ReportEmitter",
                format_in=f"{DOMAIN}.test-result",
                format_out=f"{DOMAIN}.report",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-report-v", kind=ValidatorKind.HARD,
                    description="汇总 TDD 执行报告"),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.EMIT, feedback="TDD 完成"),
                    # V24 加 FAIL 路由 — 修 V22 假设系统真发现:
                    # report_emitter HARD validator 校验失败时无路由 = 局部 happy path only
                    # (反模式 PA-02). HALT 让管线终止而非继续传播不完整产物.
                    VerdictKind.FAIL: Route(action=RouteAction.HALT,
                                              feedback="报告生成失败 — TDD 执行管线终止"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),
    ]

    return TeamSpec(
        id=f"pipeline-{DOMAIN}",
        name="TDD 执行管线",
        description="计划加载 → 写测试 → 跑测试 → 写实现(回路) → 报告",
        nodes=nodes,
        edges=[],
        entry="plan_loader",
    )


# ── Bindings ──────────────────────────────────────────────────────────────────
