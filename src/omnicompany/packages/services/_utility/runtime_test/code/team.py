# [OMNI] origin=claude-code domain=services/code_runtime_test/team ts=2026-04-26T00:00:00Z type=team
# [OMNI] material_id="material:utility.runtime_test.code.team_topology.config.py"
"""code_runtime_test Team · 拓扑.

5 节点 (1 入口 + 3 验证并行 + 1 装配):
  TargetIngressWorker (HARD)
    ↓
    ├──→ GoldenContractRunner (HARD · success cases byte-diff)
    ├──→ ErrorPathRunner (HARD · error cases verdict + diag)
    └──→ ReproducibilityRunner (HARD · 同 input 2 次 byte 比)
              ↓ 3 路 fan-in
         PortraitAssemblerWorker (HARD · sink)
"""
from __future__ import annotations

from omnicompany.protocol.anchor import (
    AnchorSpec,
    Route,
    RouteAction,
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


def _anchor(node_id, fmt_in, fmt_out, *, vkind, desc, routes, maturity=NodeMaturity.GROWING):
    return TeamNode(
        id=node_id,
        kind=NodeKind.ANCHOR,
        maturity=maturity,
        anchor=AnchorSpec(
            id=f"a_{node_id}",
            name=node_id,
            format_in=fmt_in,
            format_out=fmt_out,
            validator=ValidatorSpec(id=f"v_{node_id}", kind=vkind, description=desc),
            routes=routes,
        ),
    )


def build_team() -> TeamSpec:
    nodes = []

    nodes.append(_anchor(
        "TargetIngressWorker",
        "code_runtime_test.target_spec",
        "code_runtime_test.target_metadata",
        vkind=ValidatorKind.HARD,
        desc="装入 · 校 target 注册 · 分类用例 success/error/reproducibility.",
        routes={
            VerdictKind.PASS: Route(action=RouteAction.NEXT),
            VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=1),
        },
    ))

    nodes.append(_anchor(
        "GoldenContractRunnerWorker",
        "code_runtime_test.target_metadata",
        "code_runtime_test.golden_evidence",
        vkind=ValidatorKind.HARD,
        desc="路 1 标杆对标 · 跑每个 success case · diff vs expected · 计 byte_diff_count.",
        routes={
            VerdictKind.PASS: Route(action=RouteAction.NEXT),
            VerdictKind.PARTIAL: Route(action=RouteAction.NEXT),
            VerdictKind.FAIL: Route(action=RouteAction.NEXT),  # FAIL 也送下游, 让 portrait 装入失败证据
        },
    ))

    nodes.append(_anchor(
        "ErrorPathRunnerWorker",
        "code_runtime_test.target_metadata",
        "code_runtime_test.error_evidence",
        vkind=ValidatorKind.HARD,
        desc="路 2 错误处理 · 跑每个 error case · 验 verdict + diagnosis 关键词.",
        routes={
            VerdictKind.PASS: Route(action=RouteAction.NEXT),
            VerdictKind.PARTIAL: Route(action=RouteAction.NEXT),
            VerdictKind.FAIL: Route(action=RouteAction.NEXT),  # FAIL 也送下游, 让 portrait 装入失败证据
        },
    ))

    nodes.append(_anchor(
        "ReproducibilityRunnerWorker",
        "code_runtime_test.target_metadata",
        "code_runtime_test.reproducibility_evidence",
        vkind=ValidatorKind.HARD,
        desc="路 3 重现性 · 同 input 跑 2 次 · byte-identical.",
        routes={
            VerdictKind.PASS: Route(action=RouteAction.NEXT),
            VerdictKind.PARTIAL: Route(action=RouteAction.NEXT),
            VerdictKind.FAIL: Route(action=RouteAction.NEXT),  # FAIL 也送下游, 让 portrait 装入失败证据
        },
    ))

    nodes.append(_anchor(
        "PortraitAssemblerWorker",
        [
            "code_runtime_test.golden_evidence",
            "code_runtime_test.error_evidence",
            "code_runtime_test.reproducibility_evidence",
            "code_runtime_test.target_metadata",
        ],
        "code_runtime_test.portrait",
        vkind=ValidatorKind.HARD,
        desc="装画像 sink · 3 路证据汇总 + 派生 verdict + 自然语言段落.",
        routes={
            VerdictKind.PASS: Route(action=RouteAction.NEXT),
            VerdictKind.PARTIAL: Route(action=RouteAction.NEXT),
            VerdictKind.FAIL: Route(action=RouteAction.NEXT),  # FAIL 也送下游, 让 portrait 装入失败证据
        },
    ))

    edges = []

    # Ingress → 3 验证器
    for verifier in [
        "GoldenContractRunnerWorker",
        "ErrorPathRunnerWorker",
        "ReproducibilityRunnerWorker",
    ]:
        edges.append(TeamEdge(
            source="TargetIngressWorker",
            target=verifier,
            condition=VerdictKind.PASS,
        ))

    # 3 验证器 + Ingress → Portrait (含 FAIL 也 forward, 让 portrait 装入失败证据)
    for verifier in [
        "GoldenContractRunnerWorker",
        "ErrorPathRunnerWorker",
        "ReproducibilityRunnerWorker",
    ]:
        for verdict in (VerdictKind.PASS, VerdictKind.PARTIAL, VerdictKind.FAIL):
            edges.append(TeamEdge(
                source=verifier,
                target="PortraitAssemblerWorker",
                condition=verdict,
            ))
    edges.append(TeamEdge(
        source="TargetIngressWorker",
        target="PortraitAssemblerWorker",
        condition=VerdictKind.PASS,
    ))

    return TeamSpec(
        id="code_runtime_test",
        name="code_runtime_test",
        description="代码产物测试团队 · 标杆对标 + 错误处理 + 重现性 (有 ground truth · 全 HARD)",
        entry="TargetIngressWorker",
        nodes=nodes,
        edges=edges,
        tags=["code_runtime_test", "verification", "code_product"],
    )
