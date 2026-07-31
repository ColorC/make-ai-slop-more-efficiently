# [OMNI] origin=ai-ide domain=slidecast ts=2026-06-20T00:00:00Z type=format status=active
# [OMNI] summary="slidecast 节点间流动的 Material(Format)契约。链路: request→brief→outline→deck_ir→deck_ir_valid→slidev_md→deck_html。"
# [OMNI] why="框架级统一:产物只能是 Material。声明 Format, 节点 FORMAT_IN/OUT 才有契约(dispatch 自动 register_formats)。"
# [OMNI] tags=slidecast,format,material
"""slidecast domain Materials。"""

from __future__ import annotations

from omnicompany.protocol.format import Format, FormatRegistry


def _f(fid: str, name: str, desc: str, stage: str) -> Format:
    return Format(
        id=fid, name=name, description=desc,
        tags=["domain.slidecast", f"stage.{stage}"],
        json_schema={"type": "object"},
    )


SLIDECAST_REQUEST = _f("slidecast.request", "SlidecastRequest",
                       "发起请求: article(文章md路径) 或 topic, 可选 style/build。", "request")
SLIDECAST_BRIEF = _f("slidecast.brief", "SlidecastBrief",
                     "入题态: 文章标题/一句话/正文 + run_dir + 是否构建。", "intake")
SLIDECAST_OUTLINE = _f("slidecast.outline", "SlidecastOutline",
                       "大纲态: 讲解节奏 plan(可空, 降级只靠原文)。", "outline")
SLIDECAST_DECK_IR = _f("slidecast.deck_ir", "SlidecastDeckIR",
                       "授稿态: 结构化会动 deck IR(meta + slides[layout/...])。", "deck_ir")
SLIDECAST_DECK_IR_VALID = _f("slidecast.deck_ir_valid", "SlidecastDeckIRValid",
                             "校验态: 修补过的 deck IR(首封面/尾收尾/字段齐)。", "validated")
SLIDECAST_SLIDEV_MD = _f("slidecast.slidev_md", "SlidecastSlidevMd",
                         "渲染态: Slidev Markdown(slides.md)路径。", "rendered")
SLIDECAST_DECK_HTML = _f("slidecast.deck_html", "SlidecastDeckHtml",
                         "sink: 构建出的可交互 HTML(dist/index.html);构建失败为 None 但 slides.md 已交付。", "built")


ALL_FORMATS = [
    SLIDECAST_REQUEST, SLIDECAST_BRIEF, SLIDECAST_OUTLINE, SLIDECAST_DECK_IR,
    SLIDECAST_DECK_IR_VALID, SLIDECAST_SLIDEV_MD, SLIDECAST_DECK_HTML,
]


def register_formats(registry: FormatRegistry) -> None:
    for fmt in ALL_FORMATS:
        if not registry.is_registered(fmt.id):
            registry.register(fmt)
