# [OMNI] origin=ai-ide domain=research ts=2026-06-14T00:00:00Z type=format status=active
# [OMNI] summary="research domain 的 Material(Format)定义。调研管线节点间流动的数据契约。"
# [OMNI] why="框架级统一:产物只能是 Material。把 请求/中间态/记录 声明成 Format,节点 FORMAT_IN/OUT 才有契约。"
# [OMNI] tags=research,format,material
"""research domain Materials。

链路: research.request → research.intake → research.snippets → research.synthesis → research.record
"""

from __future__ import annotations

from omnicompany.protocol.format import Format, FormatRegistry


RESEARCH_REQUEST = Format(
    id="research.request",
    name="ResearchRequest",
    description="一次公开调研的发起请求。来自 omni run CLI。字段: topic(必填)、max_results、dry_run。",
    tags=["domain.research", "stage.request", "kind.source"],
    json_schema={
        "type": "object",
        "properties": {
            "topic": {"type": "string"},
            "max_results": {"type": "integer"},
            "dry_run": {"type": ["boolean", "string"]},
        },
        "required": ["topic"],
    },
)

RESEARCH_INTAKE = Format(
    id="research.intake",
    name="ResearchIntake",
    description="入题态:归一化题目 + 查重门结果 + 跑参(轮数/子主题数/并发)。existing 非空=库里已有同题记录。拆题交给下游 planner。",
    tags=["domain.research", "stage.intake", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "topic": {"type": "string"},
            "topic_norm": {"type": "string"},
            "run_dir": {"type": "string"},
            "existing": {"type": ["object", "null"]},
            "max_results": {"type": "integer"},
            "max_rounds": {"type": "integer"},
            "max_subtopics": {"type": "integer"},
            "workers": {"type": "integer"},
        },
        "required": ["topic", "topic_norm", "run_dir"],
    },
)

RESEARCH_SNIPPETS = Format(
    id="research.snippets",
    name="ResearchSnippets",
    description="检索态:多 query 搜索+抓取得到的原始片段 snippets([{query,title,url,text}])。",
    tags=["domain.research", "stage.retrieved", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "topic": {"type": "string"},
            "topic_norm": {"type": "string"},
            "run_dir": {"type": "string"},
            "snippets": {"type": "array", "items": {"type": "object"}},
            "existing": {"type": ["object", "null"]},
        },
        "required": ["topic", "topic_norm", "run_dir", "snippets"],
    },
)

RESEARCH_SYNTHESIS = Format(
    id="research.synthesis",
    name="ResearchSynthesis",
    description="综合态:便宜模型据片段综合出的结构化结论(summary/findings 带来源/keywords/aliases)。",
    tags=["domain.research", "stage.synthesized", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "topic": {"type": "string"},
            "topic_norm": {"type": "string"},
            "run_dir": {"type": "string"},
            "synthesis": {"type": "object"},
            "sources": {"type": "array", "items": {"type": "object"}},
            "existing": {"type": ["object", "null"]},
            "synth_ok": {"type": "boolean"},
        },
        "required": ["topic", "topic_norm", "run_dir", "synthesis"],
    },
)

RESEARCH_PLAN = Format(
    id="research.plan",
    name="ResearchPlan",
    description="规划态:先搜后拆出的互不重叠子主题列表(各带 goal/perspective/queries/boundary)+ 研究 brief。",
    tags=["domain.research", "stage.plan", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "topic": {"type": "string"},
            "topic_norm": {"type": "string"},
            "run_dir": {"type": "string"},
            "brief": {"type": "string"},
            "subtopics": {"type": "array", "items": {"type": "object"}},
            "existing": {"type": ["object", "null"]},
        },
        "required": ["topic", "run_dir", "subtopics"],
    },
)

RESEARCH_GATHERED = Format(
    id="research.gathered",
    name="ResearchGathered",
    description="收集态:并行子研究 + 有界反思迭代后,汇总的带来源发现 + 来源 + 覆盖账本(covered/open)+ 轮数。",
    tags=["domain.research", "stage.gathered", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "topic": {"type": "string"},
            "topic_norm": {"type": "string"},
            "run_dir": {"type": "string"},
            "findings": {"type": "array", "items": {"type": "object"}},
            "sources": {"type": "array", "items": {"type": "object"}},
            "coverage": {"type": "object"},
            "rounds": {"type": "integer"},
            "existing": {"type": ["object", "null"]},
        },
        "required": ["topic", "run_dir", "findings"],
    },
)

RESEARCH_VERIFIED = Format(
    id="research.verified",
    name="ResearchVerified",
    description="核源态:综合结论的每条 finding 已对抗式抓原始来源判 supported/partial/unsupported(写进 support)。",
    tags=["domain.research", "stage.verified", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "topic": {"type": "string"},
            "topic_norm": {"type": "string"},
            "run_dir": {"type": "string"},
            "synthesis": {"type": "object"},
            "sources": {"type": "array", "items": {"type": "object"}},
            "existing": {"type": ["object", "null"]},
        },
        "required": ["topic", "run_dir", "synthesis"],
    },
)

RESEARCH_RECORD = Format(
    id="research.record",
    name="ResearchRecord",
    description="管线 sink:落进统一研究库的一条研究记录 + 渲好的 report.md 路径。dup=是否同题增量。",
    tags=["domain.research", "stage.record", "kind.sink"],
    json_schema={
        "type": "object",
        "properties": {
            "record_id": {"type": "string"},
            "report": {"type": "string"},
            "run_dir": {"type": "string"},
            "dup": {"type": "boolean"},
            "richness": {"type": "integer"},
        },
        "required": ["record_id", "report"],
    },
)


# ── 统一本地资产 catalog 条目(研究记录 / 已拉 repo / 资料 三类统一形态)──
RESEARCH_CATALOG_ITEM = Format(
    id="research.catalog_item",
    name="ResearchCatalogItem",
    description="统一本地资产索引的一条:kind=research_record|repo|material;别名召回为命脉;'先查本地'查的就是它。",
    tags=["domain.research", "stage.catalog", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "kind": {"type": "string", "enum": ["research_record", "repo", "material"]},
            "name": {"type": "string"},
            "path": {"type": "string"},
            "description": {"type": "string"},
            "aliases": {"type": "array", "items": {"type": "string"}},
            "source_url": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "indexed_at": {"type": "string"},
            "status": {"type": "string"},
        },
        "required": ["id", "kind", "name"],
    },
)

# ── 落库后的完整研究记录 schema(落库前 validate_json_schema 校验的契约)──
RESEARCH_RECORD_FULL = Format(
    id="research.record_full",
    name="ResearchRecordFull",
    description="统一研究库 records.jsonl 每行的完整契约。sources[].url 必填、snapshot_path 可选(源原文本地快照)。",
    tags=["domain.research", "stage.library", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "record_id": {"type": "string"},
            "topic": {"type": "string"},
            "topic_norm": {"type": "string"},
            "summary": {"type": "string"},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim": {"type": "string"},
                        "source_url": {"type": "string"},
                        "support": {"type": "string"},
                    },
                    "required": ["claim"],
                },
            },
            "sources": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "title": {"type": "string"},
                        "kind": {"type": "string"},
                        "snapshot_path": {"type": "string"},
                        "snapshot_sha256": {"type": ["string", "null"]},
                        "platform": {"type": ["string", "null"]},
                        "publisher": {"type": ["string", "null"]},
                        "author": {"type": ["string", "null"]},
                        "author_or_publisher": {"type": ["string", "null"]},
                        "published_at": {"type": ["string", "null"]},
                        "updated_at": {"type": ["string", "null"]},
                        "retrieved_at": {"type": ["string", "null"]},
                        "game_version": {"type": ["string", "null"]},
                        "season": {"type": ["string", "null"]},
                        "server_stage": {"type": ["string", "null"]},
                        "citation_locator": {"type": ["string", "null"]},
                        "summary": {"type": ["string", "null"]},
                        "fresh_until": {"type": ["string", "null"]},
                    },
                    "required": ["url"],
                },
            },
            "keywords": {"type": "array", "items": {"type": "string"}},
            "aliases": {"type": "array", "items": {"type": "string"}},
            "status": {"type": "string"},
            "richness": {"type": "integer"},
        },
        "required": ["record_id", "topic", "topic_norm"],
    },
)


ALL_FORMATS = [
    RESEARCH_REQUEST,
    RESEARCH_INTAKE,
    RESEARCH_SNIPPETS,
    RESEARCH_PLAN,
    RESEARCH_GATHERED,
    RESEARCH_SYNTHESIS,
    RESEARCH_VERIFIED,
    RESEARCH_RECORD,
    RESEARCH_CATALOG_ITEM,
    RESEARCH_RECORD_FULL,
]


def register_formats(registry: FormatRegistry) -> None:
    for fmt in ALL_FORMATS:
        if not registry.is_registered(fmt.id):
            registry.register(fmt)
