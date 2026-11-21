# [OMNI] origin=claude-code domain=pattern_discovery/pipeline.py ts=2026-04-08T03:23:37Z
# [OMNI] material_id="material:core.pattern_discovery.pipeline_topology.definition.py"
"""pattern_discovery pipeline — 后台模式发现管线拓扑（4 节点）

summary_reader (HARD) → pattern_clusterer (SOFT) → induction_dispatcher (SOFT) → EMIT
"""

from omnicompany.protocol.anchor import (
    AnchorSpec, Route, RouteAction, ValidatorKind, ValidatorSpec, VerdictKind,
)
from omnicompany.protocol.team import (
    NodeKind, TeamEdge, TeamNode, TeamSpec,
)


def build_team() -> TeamSpec:
    nodes = [
        TeamNode(
            id="summary_reader",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="a_summary_reader", name="summary_reader",
                format_in="pd.trigger", format_out="pd.activities",
                validator=ValidatorSpec(
                    id="v_summary_reader", kind=ValidatorKind.HARD,
                    description="从 compression_summaries 表确定性读取未处理摘要并展平 activities",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="pattern_clusterer"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT),
                },
            ),
        ),
        TeamNode(
            id="pattern_clusterer",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="a_pattern_clusterer", name="pattern_clusterer",
                format_in="pd.activities", format_out="pd.candidates",
                validator=ValidatorSpec(
                    id="v_pattern_clusterer", kind=ValidatorKind.SOFT,
                    description="LLM 对 activity purpose 做语义聚类，筛选出现 >= K 次的重复模式",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="induction_dispatcher"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT),
                },
            ),
        ),
        TeamNode(
            id="induction_dispatcher",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="a_induction_dispatcher", name="induction_dispatcher",
                format_in="pd.candidates", format_out="pd.done",
                validator=ValidatorSpec(
                    id="v_induction_dispatcher", kind=ValidatorKind.SOFT,
                    description="对每个候选模式调用 trace-induction 管线尝试自动沉淀",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.EMIT),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT),
                },
            ),
        ),
    ]

    edges = [
        TeamEdge(source="summary_reader", target="pattern_clusterer", condition=VerdictKind.PASS),
        TeamEdge(source="pattern_clusterer", target="induction_dispatcher", condition=VerdictKind.PASS),
    ]

    return TeamSpec(
        id="pattern-discovery",
        name="pattern-discovery",
        description="后台模式发现：从行为保全摘要中聚类发现重复模式 → 自动触发轨迹归纳",
        entry="summary_reader",
        nodes=nodes,
        edges=edges,
        tags=["meta", "pattern_discovery"],
    )
