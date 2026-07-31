# [OMNI] origin=claude-code domain=services/knowledge/pipeline.py ts=2026-04-09T00:00:00Z
# [OMNI] material_id="material:learning.knowledge.pipeline_topology.definition.py"
"""omnikb.pipeline — OmniKB 管线拓扑定义。

当前只定义一条管线: omnikb-audit (全量审计)。
seed 管线 (R3) 之后再加, 避免一次引入太多未验证节点。

omnikb-audit 拓扑 (线性单节点, 可日后扩展):

  audit_request
       │
  [audit_all]  — KBAuditRouter
       │
  audit_report → EMIT
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

DOMAIN = "omnikb"


def build_audit_pipeline() -> TeamSpec:
    """单节点审计管线。

    单节点的意义在于让 omnikb-audit 成为一个正式的 registered pipeline,
    可被 guardian / cron / 其他管线通过 TeamRunner 调用。
    """
    nodes = [
        TeamNode(
            id="audit_all",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-audit-all",
                name="OmniKBAuditAll",
                format_in="kb.audit_request",
                format_out="kb.audit_report",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-audit-v",
                    kind=ValidatorKind.HARD,
                    description=(
                        "跑 5 类一致性审计: validation / anchor drift / orphan routers / "
                        "staleness / format coverage。PASS = 无问题; PARTIAL = 有 warning "
                        "或 info 但无 error; FAIL = 有 error 级别问题 (如重复 id)。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.EMIT),
                    VerdictKind.PARTIAL: Route(
                        action=RouteAction.EMIT,
                        feedback="审计发现 warning/info, 建议人工查看 audit_report",
                    ),
                    VerdictKind.FAIL: Route(
                        action=RouteAction.HALT,
                        feedback="审计发现 error (例如重复 id), 必须先修",
                    ),
                },
            ),
            maturity=NodeMaturity.HYPOTHETICAL,
        ),
    ]
    edges: list[TeamEdge] = []

    return TeamSpec(
        id=f"{DOMAIN}.audit",
        name="OmniKB Audit",
        description=(
            "OmniKB 全量一致性审计: 扫 data/knowledge + packages/*/knowledge 下的所有 "
            "知识条目, 校验引用完整性、code_anchor 漂移、孤儿 Router、Format 覆盖。"
        ),
        nodes=nodes,
        edges=edges,
        entry="audit_all",
        tags=["domain.knowledge", "phase.audit"],
    )


PIPELINES = {
    "omnikb.audit": build_audit_pipeline,
}
