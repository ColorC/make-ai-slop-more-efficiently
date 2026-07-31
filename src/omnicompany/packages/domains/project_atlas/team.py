# [OMNI] origin=claude-code domain=project_atlas ts=2026-06-21 type=team status=active
# [OMNI] summary="project_atlas 的 Team:项目及业务收集,4 节点(入题→勘察→带工具 worker 收集→落名录)。"
# [OMNI] why="语义起草交给带工具的受审计 worker(实地核实真入口,不裸 LLM 编造);provider 可选,做成 Team 才进驾驶舱项目-管线看板。"
# [OMNI] tags=project_atlas,team,pipeline,collect
"""project_atlas domain Team —— 项目及业务收集(4 节点)。"""

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
            id=f"project_atlas-{nid}", name=name, from_format=fmt_in, to_format=fmt_out,
            method=method, description=desc,
        ),
        maturity=NodeMaturity.GROWING,
    )


def build_project_atlas_pipeline() -> TeamSpec:
    """收集管线: 入题→勘察(确定性收线索地图)→收集(带工具 worker 实地核实写 SKILL)→落名录/报告。"""
    nodes = [
        _node("intake", "Intake", "project_atlas.request", "project_atlas.intake",
              TransformMethod.RULE, "解析 space → 根路径, 建 run_dir。"),
        _node("survey", "Survey", "project_atlas.intake", "project_atlas.surveyed",
              TransformMethod.RULE, "确定性收线索地图(顶层目录 + 清单摘要), 给 worker 当起点。"),
        _node("collect", "Collect", "project_atlas.surveyed", "project_atlas.collected",
              TransformMethod.LLM, "omni worker run <provider>(带工具): 实地核实真入口 + 写 grounded object-SKILL 到 staging。"),
        _node("finalize", "Finalize", "project_atlas.collected", "project_atlas.record",
              TransformMethod.RULE, "读 staging 实际产物 → 写名录草稿 + 报告(待人审)。"),
    ]
    edges = [
        TeamEdge(source="intake", target="survey"),
        TeamEdge(source="survey", target="collect"),
        TeamEdge(source="collect", target="finalize"),
    ]
    return TeamSpec(
        id="project_atlas.run",
        name="项目及业务收集管线",
        description="跨工作空间语义策展: 勘察→带工具 worker 实地核实切对象+写 grounded object-SKILL→落 staging 待人审 + 维护项目速览名录。",
        nodes=nodes,
        edges=edges,
        entry="intake",
        tags=["domain.project_atlas", "stage.collect"],
    )
