# [OMNI] origin=omnicompany domain=omnicompany/repair ts=2026-04-10T00:00:00Z
# [OMNI] material_id="material:core.repair.team_topology.definition.py"
"""repair.pipeline — Format 修复 AgentLoop 管线拓扑

拓扑（逻辑简图）:

  repair_request → format_repair_loop → repair_report

说明:
  - format_repair_loop 是 FormatRepairAgentLoop，内部封装了
    「诊断 → LLM 规划 → Patch → 重新诊断」的迭代循环
  - 外部管线只有一个可见节点，结构极简
"""

from omnicompany.protocol.team import (
    TeamSpec, TeamNode, TeamEdge,
    NodeKind, NodeMaturity,
)
from omnicompany.protocol.anchor import TransformerSpec, TransformMethod

DOMAIN = "repair"


def build_team() -> TeamSpec:
    nodes = [
        TeamNode(
            id="format_repair_loop",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-agent-loop",
                name="FormatRepairAgentLoop",
                from_format="repair.fmt.request",
                to_format="repair.fmt.report",
                method=TransformMethod.HYBRID,
                description=(
                    "Format 修复 AgentLoop：诊断 → LLM 规划 → Patch → 重新诊断，"
                    "循环至 A 级或达到迭代上限（默认 3 次）"
                ),
            ),
            maturity=NodeMaturity.GROWING,
        ),
    ]

    edges: list[TeamEdge] = []

    return TeamSpec(
        id=f"{DOMAIN}-format-repair",
        name="Format Repair AgentLoop Pipeline",
        description=(
            "Format 自动修复管线：给定 Format ID，迭代诊断并调用 LLM 规划修复，"
            "直至健康等级升为 A 或达到最大迭代次数"
        ),
        nodes=nodes,
        edges=edges,
        entry="format_repair_loop",
        tags=["repair", "format", "agent-loop"],
    )
