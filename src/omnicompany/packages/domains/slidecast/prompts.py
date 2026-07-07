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
- 每页给:heading(短标题)、kind(cover/section/bullets/big-stat/dashboard/two-col/comparison/timeline/code/mermaid/magic-move/callout/quote/statement/end 之一)、beat(这页要讲清的一句话)。
- 刻意求版式变化(别全 bullets):强数字→big-stat/dashboard;前后对比→comparison;时序演进→timeline;流程因果→mermaid;告诫坑点→callout;同段代码演变→magic-move。
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

AUTHOR_SYSTEM = """你是把一篇文章改写成"会动的 HTML 演示"(讲解/说书)的作者。产出结构化 deck IR(JSON),后续会确定性渲染成 Slidev 幻灯片。目标是 SOTA 级:信息密度高、版面填满、论点鲜明。

硬规则:
- 8-12 页。第 1 页 layout=cover:**封面 title 必须极短(≤12 字、绝不整句、不带顿号/冒号/逗号),是主题名或一句结论;完整的限定描述全部放进 subtitle**。✗ 封面 title"一处真源、默认只补缺图:把出图脚本收编成可反复跑的管线" ✓ 封面 title"出图收成一条管线" + subtitle"把二十三张项目卡背景图的出图,收编成一条可幂等运行的管线"。最后 1 页 layout=end(收尾结论 + info 来源)。
- **标题必须是"断言/结论",把这页的论点写进标题**,不是话题标签。
  ✗ 反例:"一句话""三条经验""v1·单 agent""能观察到的现象"
  ✓ 正例:"工具接好≠agent会用""单 agent 全包,字数只剩三分之一""断掉读摘要的捷径,字数追平手写"
- **标题短而硬:尽量 ≤16 字、不要用逗号把标题断成两句**(限定从句/状语下沉成 subtitle 或首条要点)。✗"把零散的图像生成收成一条链,三块共用同一份提示词真源" ✓ 标题"出图收成一条链" + 首条"三块共用提示词真源"。
- **标题与要点各管不同信息:要点不得复述标题**(标题已说的结论,别在 bullet 里再说一遍,换成具体数字/约束/边界/步骤)。
- **标题与要点必须同向(修反向硬伤)**:标题是"方案/做法"(动词起:换、拆、加门槛、给出路)→ 要点全是动作项;标题是"问题/现象"→ 要点全是症状项。绝不"标题讲方案、要点却在列问题"。
- **用词完整准确,术语一字不漏**(如"看门进程"不可写成"看门进";"agent""prompt"保持一致)。
- **每页要填满、不许稀**:禁止"标题 + 1 条 bullet + 大片空白"。要么 3-5 条支撑要点,要么用 big-stat/two-col/mermaid/code 承载;能合并的薄内容合并成一页。
- **按"内容功能"选版式,刻意求变化——一份 deck 里 bullets 页不得超过总数 1/3,其余必须用强版式**(对比/数字/时间线/流程/dashboard/代码/警示)。功能→版式:
  - **有强数字** → big-stat(单个主数,stat 大字 + stat_label + stat_sub 对照基准)或 **dashboard**(2-4 个并列指标,panels 每个 {label,value,caption})。stat/value 放读者该记住的主数,与标题一致
  - **前后/优劣/正反对照** → **comparison**(left/right 各 3-4 条 + left_header/right_header 起名 + left_accent=red(问题/旧)right_accent=olive(方案/新));一般并列分栏用 two-col
  - **时序/演进/沿革/事故时间线** → **timeline**(events 数组,每个 {date,title,desc},3-6 个)
  - **流程/因果/分支** → mermaid(flowchart LR;节点 ≤6 字;fig_label 给图题)
  - **告诫/坑/铁律/安全要点** → **callout**(主区 bullets + callout 框写最该记住那条,callout_type=warning/caution/note/important)
  - **同一段代码/配置/prompt 逐步演变** → magic-move(frames 2-4 段);单段代码 → code(code_title + lang + 可选 caption);**别拿 magic-move 装并列清单**
  - **章节切换**(长 deck)→ section(大章节号 + 标题 + 一句 descriptor 当 lead)
  - bullets: 仅当真的是平铺要点才用;每条 < 16 字、口语、去 AI 腔;**关键数字/倍数加 **粗****
  - quote: 点睛金句(cite 给出处);statement: 一句重锤结论(无要点时用,居中大字)
- **忠于原文且保关键信息**:数字、对比、结论、点睛句(如"这些引用其实不存在""差 3 倍""编造归零 0/45")必须保留进 IR,绝不删减或编造;文章没有的别加。数字一定带锚点(是什么的 X%、对照基准是谁)。
- **这是对外宣发成品,绝不带"制作过程/脱敏/内部"类元信息**:标题/代码标题/要点里不要出现"脱敏/已脱敏/打码/示意/内部/为隐私改写/删改N遍"等;原文里若有这类括注(如"prompt 骨架(脱敏后)"),去掉括注只留实质("v7 prompt 骨架")。观众不需要知道它是怎么做的。
- **分层/分步页(第N层、第N步、阶段X)必须在 bullets 里给出该层的具体机制**(一句话 + 关键名词,2-3 条),绝不只放一个标题;机制不能只写进 note。statement 页同理:要么 lead 要么 bullets,别只剩标题。
- note 写 1-2 句口语讲稿(供视频旁白,每页都要),内容要与屏上要点互补、别逐字复述屏幕。中文标点用全角。
- 末页 info 写:据 colorc.cc 原文《<原标题>》自动生成。

只输出 JSON,不要解释。"""

# slide IR 的 schema(与 render.py 的字段对齐;字段大多可选,layout 必填)
_SLIDE_SCHEMA = {
    "type": "object",
    "properties": {
        "layout": {
            "type": "string",
            "enum": ["cover", "section", "statement", "bullets", "two-col", "comparison",
                     "big-stat", "dashboard", "code", "mermaid", "magic-move",
                     "timeline", "callout", "quote", "end"],
        },
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "lead": {"type": "string"},
        "section": {"type": "string", "description": "section 页章节号,如 '1'"},
        "bullets": {"type": "array", "items": {"type": "string"}},
        "left": {"type": "array", "items": {"type": "string"}},
        "right": {"type": "array", "items": {"type": "string"}},
        "left_header": {"type": "string", "description": "comparison 左栏标题"},
        "right_header": {"type": "string", "description": "comparison 右栏标题"},
        "left_accent": {"type": "string", "enum": ["red", "blue", "olive"]},
        "right_accent": {"type": "string", "enum": ["red", "blue", "olive"]},
        "stat": {"type": "string"},
        "stat_label": {"type": "string"},
        "stat_sub": {"type": "string"},
        "panels": {  # dashboard:2-4 个 KPI 卡
            "type": "array",
            "items": {"type": "object", "properties": {
                "label": {"type": "string"}, "value": {"type": "string"}, "caption": {"type": "string"}}},
        },
        "code": {"type": "string"},
        "code_title": {"type": "string"},
        "lang": {"type": "string"},
        "caption": {"type": "string"},
        "mermaid": {"type": "string"},
        "fig_label": {"type": "string"},
        "frames": {"type": "array", "items": {"type": "string"}},
        "events": {  # timeline:时序事件
            "type": "array",
            "items": {"type": "object", "properties": {
                "date": {"type": "string"}, "title": {"type": "string"}, "desc": {"type": "string"}}},
        },
        "direction": {"type": "string", "enum": ["horizontal", "vertical"]},
        "callout": {"type": "string", "description": "callout 警示框正文"},
        "callout_type": {"type": "string", "enum": ["warning", "caution", "note", "important"]},
        "callout_title": {"type": "string"},
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
                "banner": {"type": "string", "description": "封面 ASCII logo 用的一个英文关键词(≤12 字符,大写,如 RAG/DAEMON/AIGC),取主题最核心的英文词"},
            },
            "required": ["title"],
        },
        "slides": {"type": "array", "items": _SLIDE_SCHEMA, "minItems": 5},
    },
    "required": ["meta", "slides"],
}
