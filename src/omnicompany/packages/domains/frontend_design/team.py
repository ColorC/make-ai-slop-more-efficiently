# [OMNI] origin=claude-code domain=frontend_design ts=2026-07-01T00:00:00Z type=team status=design
# [OMNI] summary="两条平级审查管线(dashboard/webgame)的 TeamSpec。同构四节点, 差异只在标尺与 archetype。"
# [OMNI] why="一域两分支共用方法脊柱: intake→gate→vlm_review→synthesize。管线只能是 Team。"
# [OMNI] tags=frontend_design,team,pipeline
"""frontend_design 的 Team —— 两条平级子分支管线。

同构四节点线性管线(标尺→确定性门禁→VLM相对评审→改进闭环+决策沉淀):
    intake(RULE) → gate(RULE) → vlm_review(LLM) → synthesize(RULE)
dashboard 与 webgame 共用此拓扑, 靠 review_request.archetype 分流标尺与门禁参数。
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
            id=f"frontend_design-{nid}", name=name, from_format=fmt_in, to_format=fmt_out,
            method=method, description=desc,
        ),
        maturity=NodeMaturity.GROWING,
    )


def _build_review_pipeline(branch: str, title: str, standard: str) -> TeamSpec:
    """审查管线通用拓扑。branch=dashboard|webgame; standard=该分支标尺真源(写进描述, 供人核)。"""
    nodes = [
        _node("intake", "Intake",
              "frontend_design.review_request", "frontend_design.intake",
              TransformMethod.RULE, f"归一化审查请求, 锁定 archetype={branch} 与标尺({standard}), 建 run_dir。"),
        _node("gate", "Gate",
              "frontend_design.intake", "frontend_design.gate_result",
              TransformMethod.RULE, "确定性门禁: 跑可判定规则(溢出/文案预算/对比度/字号地板/平铺密度), 产证据列表不打分。"),
        _node("vlm_review", "VlmReview",
              "frontend_design.gate_result", "frontend_design.vlm_review",
              TransformMethod.LLM, "VLM 相对评审: 对基准图成对比较, 列证据不打分。"),
        _node("synthesize", "Synthesize",
              "frontend_design.vlm_review", "frontend_design.review_record",
              TransformMethod.RULE, "汇总门禁+评审→改进建议; 把设计判断沉进 decisions 域(project 分流)。"),
    ]
    edges = [
        TeamEdge(source="intake", target="gate"),
        TeamEdge(source="gate", target="vlm_review"),
        TeamEdge(source="vlm_review", target="synthesize"),
    ]
    return TeamSpec(
        id=f"frontend_design.{branch}",
        name=title,
        description=(
            f"前端设计与制作管线 · {branch} 分支。标尺→确定性门禁→VLM相对评审→改进闭环+决策沉淀。"
            f"标尺真源: {standard}。"
        ),
        nodes=nodes,
        edges=edges,
        entry="intake",
        tags=["domain.frontend_design", f"branch.{branch}"],
    )


def build_dashboard_review_pipeline() -> TeamSpec:
    """dashboard 类网页(驾驶舱/poof/lofa)审查管线。标尺 = frostpane。"""
    return _build_review_pipeline(
        "dashboard", "前端设计·dashboard 分支",
        "docs/projects/frontend-design/dashboard(设计语言.md + theme.css)",
    )


def build_webgame_review_pipeline() -> TeamSpec:
    """webgame UI 审查管线。标尺 = tabletop-engine/README + walker specs。"""
    return _build_review_pipeline(
        "webgame", "前端设计·webgame 分支",
        "tabletop-engine/README.md + walker docs/specs",
    )
