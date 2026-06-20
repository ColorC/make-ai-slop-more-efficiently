# [OMNI] origin=ai-ide domain=example_domain ts=2026-06-20T00:00:00Z type=team status=template
# [OMNI] summary="领域模板的 Team: 最小 2 节点管线骨架, 照着改成你自己的领域。"
# [OMNI] why="给想加新领域的人一个忠于真实协议(TeamSpec/TeamNode/TeamEdge)的最小起点, 而不是从空白开始。"
# [OMNI] tags=example,template,team,pipeline
"""example_domain 的 Team —— 复制我, 改成你自己的领域管线。

一个领域 = 一个 TeamSpec(声明管线拓扑) + routers/(各节点的 transform 逻辑) + Material 契约。
本文件只示范拓扑; 真实 transform 逻辑参考 domains/research/routers/。
"""

from __future__ import annotations

from omnicompany.protocol.anchor import TransformerSpec, TransformMethod
from omnicompany.protocol.team import (
    NodeKind,
    NodeMaturity,
    TeamEdge,
    TeamNode,
    TeamSpec,
)


def _node(nid: str, name: str, fmt_in: str, fmt_out: str, method: TransformMethod, desc: str) -> TeamNode:
    return TeamNode(
        id=nid,
        kind=NodeKind.TRANSFORMER,
        transformer=TransformerSpec(
            id=f"example-{nid}", name=name, from_format=fmt_in, to_format=fmt_out,
            method=method, description=desc,
        ),
        maturity=NodeMaturity.GROWING,
    )


def build_example_pipeline() -> TeamSpec:
    """最小 2 节点管线骨架: intake(归一化输入) → process(加工成产物)。

    把这两个节点改成你的真实步骤, 加边即可。要调 LLM 把 method 换成 TransformMethod.LLM。
    """
    nodes = [
        _node("intake", "Intake", "example.request", "example.intake",
              TransformMethod.RULE, "归一化输入, 建 run_dir。(换成你的第一步)"),
        _node("process", "Process", "example.intake", "example.result",
              TransformMethod.RULE, "把输入加工成产物。(换成你的核心逻辑)"),
    ]
    edges = [TeamEdge(source="intake", target="process")]
    return TeamSpec(
        id="example_domain.run",
        name="领域模板管线",
        description="最小 2 节点骨架, 复制改成你自己的领域管线(intake → process)。",
        nodes=nodes,
        edges=edges,
        entry="intake",
        tags=["domain.example_domain"],
    )
