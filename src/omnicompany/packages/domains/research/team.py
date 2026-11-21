# [OMNI] origin=ai-ide domain=research ts=2026-06-30T00:00:00Z type=team status=active
# [OMNI] summary="research domain 的 Team。无人值守调研管线:入题查重→codex 原生搜索调研→落统一库。"
# [OMNI] why="2026-06-30 校准:弃外部搜索 API(Serper/Tavily/DDG)+ 便宜模型编排,改用带原生搜索的 frontier agent。无人值守路用 codex 执行器,交互式路用前台 agent(SKILL),二者同协议同落点 library.save_research_record。"
# [OMNI] tags=research,team,pipeline,native,codex
"""research domain Teams。"""

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
            id=f"research-{nid}", name=name, from_format=fmt_in, to_format=fmt_out,
            method=method, description=desc,
        ),
        maturity=NodeMaturity.GROWING,
    )


def build_research_pipeline() -> TeamSpec:
    """无人值守公开调研管线: 入题查重 → codex 原生搜索调研(搜+读+核源+综合)→ 落统一库。"""
    nodes = [
        _node("intake", "TopicIntake", "research.request", "research.intake",
              TransformMethod.RULE, "归一化题目 + 查重门(同题带出增量),建 run_dir。"),
        _node("native", "NativeResearch", "research.intake", "research.verified",
              TransformMethod.LLM, "codex 原生 web 搜索按调研协议搜+读+核源+综合,受 schema 约束产带源记录(无外部搜索 API)。"),
        _node("library_write", "LibraryWrite", "research.verified", "research.record",
              TransformMethod.RULE, "组装 record,去重累积 upsert 进统一研究库,渲 report.md。"),
    ]
    edges = [
        TeamEdge(source="intake", target="native"),
        TeamEdge(source="native", target="library_write"),
    ]
    return TeamSpec(
        id="research.run",
        name="公开调研管线",
        description="通用公开调研(原生搜索): 入题查重 → codex 原生 WebSearch 搜读核源综合 → 落统一库(累积/不重复)。",
        nodes=nodes,
        edges=edges,
        entry="intake",
        tags=["domain.research", "stage.research"],
    )
