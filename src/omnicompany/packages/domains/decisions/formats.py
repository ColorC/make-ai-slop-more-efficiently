# [OMNI] origin=ai-ide domain=decisions ts=2026-06-17T00:00:00Z type=format status=active
# [OMNI] summary="decisions domain 的 Material(Format)定义。决策记录的统一数据契约:一条决策/猜想/评论的标准记录 + 抽取观测 + 索引。"
# [OMNI] why="主线=决策记录(非提取)。决策有多源(对话/collab platform/策划文档/札记)多落地面,必须一套源无关、面无关的公共契约把它们汇成一棵可搜索的决策树(符号统一体系)。"
# [OMNI] tags=decisions,format,material,decision-record,schema
"""decisions domain Materials —— 决策记录的统一数据契约。

主线是「决策记录」,不是「决策提取」;提取只是往统一库灌数的其中一种方式。
三件套:
  - decision.record       统一记录(库每行契约)。kind=decision|belief|comment。
  - decision.observation  抽取态(从某个源抽出的原始信号,未去重/未接树)。
  - decision.catalog_item 索引一条(召回入口)。

决策树不靠目录,靠 links(rests_on / supersedes / parent / anchor)在记录间链出来——
个人记录以相同接口接进团队树并保持自身结构(同构/分形)。

字段继承自既有存量(别重复发明):
  - 猜想/信念段  ← hypothesis V1(confidence / authority / verification_status /
                   risk_if_wrong / challenge_log / resolution)
  - 决策段       ← decision_model(decision_space 必列被否决项 / evidence /
                   boundary 失效边界 / human_override 人工可否决点)
  - 召回         ← research(aliases / tags / catalog_item)
  - 评论闭环     ← Spec-083(comment 挂在产物上,可沉淀/晋升为 decision)
"""

from __future__ import annotations

from omnicompany.protocol.format import Format, FormatRegistry


# ── 公共子结构(anchor / origin / links)在多个 Format 间复用 ────────────────

# anchor = 「中间契约甜蜜点」: 这条决策/猜想挂在哪个富信息载体上(文档/代码/AI产物/collab platform消息…)。
_ANCHOR = {
    "type": "object",
    "description": "决策依附的中间契约(甜蜜点):富信息、好读好改、可插人类控制节点的载体。",
    "properties": {
        "kind": {
            "type": "string",
            "enum": ["doc", "code", "ai_output", "feishu_msg", "spec", "prefab", "note", "other"],
        },
        "ref": {"type": "string", "description": "路径 / url / message_id / 文件:行"},
        "excerpt": {"type": "string", "description": "引文(承载决策的那段原文)"},
    },
}

# origin = 这条记录来自哪个源、谁、什么时候被观察到。
_ORIGIN = {
    "type": "object",
    "description": "记录的来源:决策记录有多源,channel 标明从哪来。",
    "properties": {
        "channel": {
            "type": "string",
            "enum": ["claude", "codex", "feishu", "note", "demogame_doc", "manual"],
        },
        "session_ref": {"type": "string", "description": "会话/文档/消息定位(jsonl 路径、wiki token、doc id…)"},
        "observed_at": {"type": "string", "description": "ISO 时间:这条决策在源里发生的时刻"},
        "author": {"type": "string", "description": "决策者(默认本人;他人产物上的决策标他人)"},
    },
    "required": ["channel"],
}

# links = 决策树的边。记录间靠这些链出拓扑,而非靠目录层级。
_LINKS = {
    "type": "object",
    "description": "决策树的边:决策依赖哪些信念、取代了哪条旧决策、父决策、相关项、被编译进哪个执法器。",
    "properties": {
        "rests_on": {"type": "array", "items": {"type": "string"}, "description": "belief id 列表:此决策立足的猜想/信念"},
        "supersedes": {"type": "array", "items": {"type": "string"}, "description": "被本记录取代的旧决策 id(决策演化)"},
        "parent": {"type": "string", "description": "父决策 id(子决策/分形)"},
        "related": {"type": "array", "items": {"type": "string"}, "description": "相关记录 id"},
        "enforced_by": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "执法载体标识列表(值不是记录 id):此裁决被编译进哪个执法器,"
                "如 demogame.design_doc_lint.check7_self_reference / frontend_design.frostpane.css#token。"
                "记录时的权威声明,与 LLM 事后推断的因果边(causal sidecar)分家。"
            ),
        },
    },
}


# ── 1. 统一记录(库每行契约,决策树的节点)───────────────────────────────────

DECISION_RECORD = Format(
    id="decision.record",
    name="DecisionRecord",
    description=(
        "统一决策库 records.jsonl 每行的完整契约。一条决策/猜想/评论的标准记录。"
        "kind 区分三类;envelope 公共字段所有 kind 共有;decision/belief 各有专属段。"
        "决策树由 links(rests_on/supersedes/parent)+ anchor 链出。"
    ),
    tags=["domain.decisions", "stage.record", "kind.sink"],
    json_schema={
        "type": "object",
        "properties": {
            # —— envelope:公共契约(符号统一,所有 kind 共有)——
            "id": {"type": "string", "description": "稳定 id,如 DEC-2026-06-17-001 / BLF-… / CMT-…"},
            "kind": {
                "type": "string",
                "enum": ["decision", "belief", "comment"],
                "description": "decision=决策点的选择;belief=猜想/信念(可证伪);comment=对产物的评论(可晋升为 decision)",
            },
            "statement": {"type": "string", "description": "一句话:决策结论 / 猜想陈述 / 评论要点"},
            "scope": {"type": "string", "enum": ["personal", "project", "team", "global"]},
            # —— 寻址(恰当整理的地基): 项目 + 轨道 + 针对对象。一次对话常跨多个,需拆开归位 ——
            "project": {"type": "string", "description": "所属项目 id(如 vilo / omnicompany / quant-lab)"},
            "track": {
                "type": "object",
                "description": (
                    "所属轨道。**business=正在持续对外服务/运行中的软件本身(『用软件』)**;"
                    "**plan=建设或修改软件的工作(『改软件』)**。改软件 ≠ 用软件:开发/改动永远是 plan;"
                    "只有那个已经在跑、在被使用的服务才算 business(连『维护改动』也是 plan,不是 business)。"
                    "开发中、还没成为可服务软件的项目(如未上线的游戏 vilo)全是 plan,一点 business 不沾。"
                ),
                "properties": {
                    "kind": {"type": "string", "enum": ["plan", "business"]},
                    "id": {"type": "string", "description": "计划目录名 / 业务名(如 DECISION-MEMORY / vilo-card-creation)"},
                },
            },
            "applies_to": {"type": "string", "description": "针对对象:具体那张卡 / 材料 / 模块 / 对象的描述(比 anchor 更口语)"},
            "anchor": _ANCHOR,
            "origin": _ORIGIN,
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "authority": {
                "type": "string",
                "enum": ["user_explicit", "high", "medium", "low", "derived", "unknown"],
                "description": "来源权威:user_explicit=本人明确拍板;derived=从对话/文档反推未审",
            },
            "status": {
                "type": "string",
                "description": (
                    "生命周期(按 kind):"
                    "decision: proposed→adopted→superseded|revoked;"
                    "belief: untested→challenged→supported|partial|falsified;"
                    "comment: open→resolved|promoted"
                ),
            },
            "tags": {"type": "array", "items": {"type": "string"}},
            "aliases": {"type": "array", "items": {"type": "string"}, "description": "召回别名(防术语对不上漏检)"},
            "links": _LINKS,
            "created_at": {"type": "string"},
            "created_by": {"type": "string"},

            # —— decision 专属段(继承 decision_model)——
            "decision_space": {
                "type": "array",
                "description": "候选项(决策空间)。必须列出被否决的替代项及理由——否则不算显化决策。",
                "items": {
                    "type": "object",
                    "properties": {
                        "option": {"type": "string"},
                        "chosen": {"type": "boolean"},
                        "why": {"type": "string", "description": "采纳理由 / 被否决理由"},
                    },
                    "required": ["option"],
                },
            },
            "rationale": {"type": "string", "description": "为什么这么选(证据+理由的综述)"},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"ref": {"type": "string"}, "note": {"type": "string"}},
                },
                "description": "证据边界:哪些料支撑此决策(明确什么作数、什么不作数)",
            },
            "boundary": {"type": "string", "description": "失效边界:什么条件下此决策需重审"},
            "human_override": {"type": "string", "description": "人工可否决点:什么情况下应由人覆盖"},

            # —— belief 专属段(继承 hypothesis V1)——
            "verification_status": {
                "type": "string",
                "enum": ["untested", "searching", "supported", "partial", "falsified", "challenged"],
            },
            "risk_if_wrong": {"type": "string", "enum": ["low", "medium", "high"]},
            "evidence_query": {"type": "string", "description": "怎么验证这个猜想(可搜索的决策空间入口)"},
            "challenge_log": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "ts": {"type": "string"},
                        "reason": {"type": "string"},
                        "source": {"type": "string"},
                        "challenger": {"type": "string"},
                    },
                },
            },
            "resolution": {
                "type": "object",
                "properties": {
                    "ts": {"type": "string"},
                    "outcome": {"type": "string", "enum": ["supported", "partial", "falsified"]},
                    "evidence": {"type": "string"},
                    "method": {"type": "string"},
                    "by": {"type": "string"},
                },
            },
        },
        "required": ["id", "kind", "statement"],
    },
)


# ── 2. 抽取态(源 → 库 的桥;尚未去重/精炼/接树)──────────────────────────────

DECISION_OBSERVATION = Format(
    id="decision.observation",
    name="DecisionObservation",
    description=(
        "抽取态:从某个源(对话/collab platform/札记/策划文档)抽出的一条原始决策信号,尚未去重/精炼/接树。"
        "下游 refine 把它判成 decision|belief|comment、补 anchor/links、并进统一库。"
    ),
    tags=["domain.decisions", "stage.extracted", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "channel": {
                "type": "string",
                "enum": ["claude", "codex", "feishu", "note", "demogame_doc", "manual"],
            },
            "raw_quote": {"type": "string", "description": "原话(不改写,留作证据)"},
            "gist": {"type": "string", "description": "一句话概括"},
            "guessed_kind": {"type": "string", "enum": ["decision", "belief", "comment"]},
            "anchor": _ANCHOR,
            "session_ref": {"type": "string"},
            "observed_at": {"type": "string"},
            "author": {"type": "string"},
            "project": {"type": "string"},
        },
        "required": ["channel", "raw_quote"],
    },
)


# ── 3. 索引一条(召回入口,照 research.catalog_item)──────────────────────────

DECISION_CATALOG_ITEM = Format(
    id="decision.catalog_item",
    name="DecisionCatalogItem",
    description=(
        "统一决策索引的一条:按 id/anchor/project/tag/alias 召回。"
        "『先查有没有记过这决策 / 这条立足的信念还成不成立』查的就是它。"
    ),
    tags=["domain.decisions", "stage.catalog", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "kind": {"type": "string", "enum": ["decision", "belief", "comment"]},
            "statement": {"type": "string"},
            "path": {"type": "string", "description": "记录所在(records.jsonl 行 / 落地的标准化文档)"},
            "project": {"type": "string"},
            "anchor_ref": {"type": "string"},
            "aliases": {"type": "array", "items": {"type": "string"}},
            "tags": {"type": "array", "items": {"type": "string"}},
            "status": {"type": "string"},
            "indexed_at": {"type": "string"},
        },
        "required": ["id", "kind", "statement"],
    },
)


ALL_FORMATS = [
    DECISION_RECORD,
    DECISION_OBSERVATION,
    DECISION_CATALOG_ITEM,
]


def register_formats(registry: FormatRegistry) -> None:
    for fmt in ALL_FORMATS:
        if not registry.is_registered(fmt.id):
            registry.register(fmt)
