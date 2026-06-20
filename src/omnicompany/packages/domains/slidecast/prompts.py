# [OMNI] origin=ai-ide domain=slidecast ts=2026-06-20T00:00:00Z type=helper status=active
# [OMNI] summary="授大纲(OUTLINE) + 授 slide IR(AUTHOR) 的系统提示词与 JSON schema。AUTHOR 是核心: 文章 -> 会动的 deck IR。"
# [OMNI] why="IR-first: LLM 只产结构化 IR(可校验/可重试), 不直接写 Slidev markdown。guardrails 写进 prompt。"
# [OMNI] tags=slidecast,prompts,llm,ir
"""slidecast LLM 提示词 + schema。"""

from __future__ import annotations

# 中端模型(若网关注册了);默认 None 走便宜档
MID_MODEL = None

OUTLINE_SYSTEM = """你是讲解演示的策划。把一篇文章拆成一份演示大纲(8-12 页)。
要求:
- 一页一个观点,顺序服务讲解节奏(钩子 → 逐点展开 → 收尾)。
- 每页给:heading(短标题)、kind(cover/bullets/big-stat/two-col/code/mermaid/magic-move/quote/statement/end 之一)、beat(这页要讲清的一句话)。
- 有强烈数字的点用 big-stat;有前后对比的代码/配置/prompt 用 magic-move;有流程/因果用 mermaid。
- 忠于原文,不编造。
只输出 JSON。"""

OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "angle": {"type": "string"},
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "kind": {"type": "string"},
                    "beat": {"type": "string"},
                },
                "required": ["heading", "kind"],
            },
        },
    },
    "required": ["title", "beats"],
}

AUTHOR_SYSTEM = """你是把一篇文章改写成"会动的 HTML 演示"(讲解/说书)的作者。产出结构化 deck IR(JSON),后续会确定性渲染成 Slidev 幻灯片。

硬规则:
- 8-12 页。第 1 页 layout=cover(title + subtitle + 一句 lead)。最后 1 页 layout=end(收尾 + info 注明来源)。
- 一页一观点。标题断言式、短(不超过 12 字)。
- 按内容选 layout:
  - bullets: 分点展开(bullets 会逐步揭示,每条 < 16 字、口语、去 AI 腔)
  - big-stat: 有一个强烈数字时(stat 大字 + stat_label 说明 + 可选 stat_sub 对比)
  - two-col: 左右对照(left / right 两组要点)
  - magic-move: 有"同一段东西前后演变"(代码/配置/prompt),frames 按演变顺序给 2-4 段,lang 给语言
  - mermaid: 有流程/因果/分支,mermaid 给 mermaid 语法(flowchart LR/TD;节点文字短)
  - quote: 一句点睛金句(quote + 可选 cite)
  - statement: 强调一句话 + 可选 bullets
- lead 是标题下的一句引子(可选)。note 写一句讲稿(可选)。
- 忠于原文:数字、事实、结论都来自文章,绝不编造;文章没有的别加。
- 末页 info 写:据 colorc.cc 原文《<原标题>》自动生成。

只输出 JSON,不要解释。"""

# slide IR 的 schema(与 render.py 的字段对齐;字段大多可选,layout 必填)
_SLIDE_SCHEMA = {
    "type": "object",
    "properties": {
        "layout": {
            "type": "string",
            "enum": ["cover", "statement", "bullets", "two-col", "big-stat",
                     "code", "mermaid", "magic-move", "quote", "end"],
        },
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "lead": {"type": "string"},
        "bullets": {"type": "array", "items": {"type": "string"}},
        "left": {"type": "array", "items": {"type": "string"}},
        "right": {"type": "array", "items": {"type": "string"}},
        "stat": {"type": "string"},
        "stat_label": {"type": "string"},
        "stat_sub": {"type": "string"},
        "code": {"type": "string"},
        "lang": {"type": "string"},
        "mermaid": {"type": "string"},
        "frames": {"type": "array", "items": {"type": "string"}},
        "quote": {"type": "string"},
        "cite": {"type": "string"},
        "note": {"type": "string"},
        "info": {"type": "string"},
    },
    "required": ["layout"],
}

AUTHOR_SCHEMA = {
    "type": "object",
    "properties": {
        "meta": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "subtitle": {"type": "string"},
                "info": {"type": "string"},
            },
            "required": ["title"],
        },
        "slides": {"type": "array", "items": _SLIDE_SCHEMA, "minItems": 5},
    },
    "required": ["meta", "slides"],
}
