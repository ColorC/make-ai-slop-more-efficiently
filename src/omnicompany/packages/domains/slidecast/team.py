# [OMNI] origin=ai-ide domain=slidecast ts=2026-06-20T00:00:00Z type=team status=active
# [OMNI] summary="slidecast 管线拓扑: 选题→大纲→slide IR→校验→渲染 Slidev→构建 HTML→(可选)导出视频。IR-first。"
# [OMNI] why="科普/说书走 HTML 动画演示路线;IR 中间层让 LLM 产出可校验/可重试,换渲染后端不动 LLM 侧。"
# [OMNI] tags=slidecast,slidev,pipeline,team,aigc-video-content
"""slidecast 的 Team —— 声明管线拓扑(IR-first)。

一个领域 = 一个 TeamSpec(声明拓扑) + routers/(各节点 transform) + Material 契约。
本文件只声明拓扑; 节点的真实 transform 逻辑待在 routers/ 实现, 经 run.py 绑定
(参考 domains/research/routers/ 与 run.py)。当前为雏形:拓扑已定, routers 待实现。
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
            id=f"slidecast-{nid}", name=name, from_format=fmt_in, to_format=fmt_out,
            method=method, description=desc,
        ),
        maturity=NodeMaturity.GROWING,
    )


def build_slidecast_pipeline() -> TeamSpec:
    """slidecast.run 管线拓扑(IR-first,7 节点)。

    选题/脚本 → 大纲 → 逐页 slide IR → 校验 → 渲染 Slidev Markdown → 构建 HTML
    →(可选)导出带旁白视频。IR(结构化 slide JSON)是核心中间层:可校验、可重试、
    内容与表现解耦;换渲染后端(reveal.js)时 IR 不变。

    注: v1 到 build_deck 收尾(出会动的 HTML)。导出带旁白视频是独立支线(export_video),
    设计见 DESIGN.md,待 HTML 主线稳定后再加,不在本拓扑。
    """
    nodes = [
        _node("intake", "Intake", "slidecast.request", "slidecast.brief",
              TransformMethod.RULE,
              "归一化输入(选题/脚本/受众/目标时长/风格:科普|说书),建 run_dir。"),
        _node("outline", "Outline", "slidecast.brief", "slidecast.outline",
              TransformMethod.LLM,
              "产讲解大纲(钩子→分点讲解→收尾;一页一观点),拆 outline 与逐页填充。"),
        _node("author_ir", "AuthorIR", "slidecast.outline", "slidecast.deck_ir",
              TransformMethod.LLM,
              "逐页产结构化 slide IR(标题/要点/动画步/图表/讲稿);guardrails: "
              "断言式标题、bullet<12 词、文字保持文本不烧进图。"),
        _node("validate_ir", "ValidateIR", "slidecast.deck_ir", "slidecast.deck_ir_valid",
              TransformMethod.RULE,
              "JSON schema 校验 + 占位/越界/动画序号检查;失败回 author_ir 有界重试。"),
        _node("render_slidev", "RenderSlidev", "slidecast.deck_ir_valid", "slidecast.slidev_md",
              TransformMethod.RULE,
              "IR→Slidev Markdown(v-click 序号声明动画、代码块 magic-move、Mermaid 图表)。"),
        _node("build_deck", "BuildDeck", "slidecast.slidev_md", "slidecast.deck_html",
              TransformMethod.RULE,
              "slidev build 出可交互 HTML/SPA(node 子进程);失败优雅降级仍交付 slides.md。"),
    ]
    edges = [
        TeamEdge(source="intake", target="outline"),
        TeamEdge(source="outline", target="author_ir"),
        TeamEdge(source="author_ir", target="validate_ir"),
        TeamEdge(source="validate_ir", target="render_slidev"),
        TeamEdge(source="render_slidev", target="build_deck"),
    ]
    return TeamSpec(
        id="slidecast.run",
        name="slidecast 演示式讲解/说书生成管线",
        description=(
            "把选题/脚本变成会动的 HTML 演示式讲解 deck(Slidev),可选导出带旁白视频。"
            "IR-first:选题→大纲→slide IR→校验→渲染 Slidev→构建 HTML→(可选)视频。"
        ),
        nodes=nodes,
        edges=edges,
        entry="intake",
        tags=["domain.slidecast", "aigc-video-content"],
    )
