# [OMNI] origin=human domain=pipeline_ci/pipeline.py ts=2026-04-08T03:23:37Z
# [OMNI] material_id="material:diagnosis.pipeline_ci.team_specification.python"
"""pipeline_ci — TeamSpec 声明"""

from __future__ import annotations

from omnifactory.protocol.anchor import (
    AnchorSpec,
    Route,
    RouteAction,
    TransformerSpec,
    TransformMethod,
    ValidatorKind,
    ValidatorSpec,
    VerdictKind,
)
from omnifactory.protocol.team import (
    NodeKind,
    NodeMaturity,
    TeamEdge,
    TeamNode,
    TeamSpec,
)


def build_team() -> TeamSpec:
    nodes = [
        # 1. 扫描域 — 确定性 RULE Transformer
        TeamNode(
            id="domain_scanner",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id="pipeline-ci-domain-scanner",
                name="DomainScanner",
                description="扫描 packages/ 找出含 routers.py+pipeline.py 的域",
                from_format="pipeline_ci.scan-request",
                to_format="pipeline_ci.domains",
                method=TransformMethod.RULE,
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # 2. 批量审计 — 确定性 RULE Transformer（内部调用 ErrorRouteAuditor + TeamChecker）
        TeamNode(
            id="batch_auditor",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id="pipeline-ci-batch-auditor",
                name="BatchAuditor",
                description="批量执行 ErrorRouteAuditor + TeamChecker，聚合 issues",
                from_format="pipeline_ci.domains",
                to_format="pipeline_ci.ci-report",
                method=TransformMethod.RULE,
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # 3. CI 门控 — HARD Anchor（critical > 0 则 FAIL）
        TeamNode(
            id="ci_gate",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="pipeline-ci-gate",
                name="CIGate",
                format_in="pipeline_ci.ci-report",
                format_out="pipeline_ci.ci-report",
                validator=ValidatorSpec(
                    id="pipeline-ci-gate-validator",
                    kind=ValidatorKind.HARD,
                    description="CI 门控：critical_count == 0 时通过，否则失败并阻断",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.EMIT),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),
    ]

    edges = [
        TeamEdge(source="domain_scanner", target="batch_auditor"),
        TeamEdge(source="batch_auditor", target="ci_gate"),
    ]

    return TeamSpec(
        id="pipeline-ci",
        name="Pipeline CI Scanner",
        description="管线质量 CI 扫描 — 对所有域批量执行 ErrorRouteAuditor + TeamChecker",
        nodes=nodes,
        edges=edges,
        entry="domain_scanner",
    )
