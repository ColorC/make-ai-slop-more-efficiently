# [OMNI] origin=claude-code domain=services/_core/omnicompany ts=2026-06-13T03:40:00Z
# [OMNI] material_id="material:core.omnicompany.company_material_formats.py"
"""公司级材料的 Format 字典 — 材料统一计划阶段 0。

plan / progress / project / capture / 审阅材料 五类"事实材料"此前各有平行数据模型,
都不是 Format 实例 (2026-06-13 二重权威调研坐实, 用户裁决"实际上应该容纳进 material 系统")。
本模块是它们在 Format 体系里的唯一定义点: 任何新公司级材料先在这里立 Format, 再写消费代码。

schema 从各自现存真实数据模型提取 (boss_sight/progress.py, core/projects_registry.py,
boss_sight/captures/routes.py, boss_sight/reviewstage/store.py), 字段语义以源码为准,
这里只锚定身份与最小结构。

后续阶段统一由
docs/plans/format-material/[2026-08-02]MATERIAL-WIKI-FULL-FACILITY/plan.md 承接：
写入口先写项目本体，再发 FactoryEvent(event_type=Format.id)并更新派生投影。
"""
from __future__ import annotations

from omnicompany.protocol.format import Format, FormatRegistry

PLAN = Format(
    id="omni.plan",
    name="计划",
    description=(
        "docs/plans 下的一份过程记录文档 (plan.md 及其目录)。"
        "头部 frontmatter 按 standards/concepts/plan.md §三 (平铺字段 + binding 块)。"
    ),
    tags=["omni.material", "content.plan", "kind.source"],
    json_schema={
        "type": "object",
        "properties": {
            "plan_id": {"type": "string", "description": "category/[YYYY-MM-DD]TOPIC"},
            "title": {"type": "string"},
            "status": {"type": ["string", "null"], "description": "active/done/paused"},
            "plan_path": {"type": "string"},
        },
        "required": ["plan_id", "plan_path"],
    },
)

PROGRESS_ENTRY = Format(
    id="omni.progress-entry",
    name="进展条目",
    description="项目/计划时间线上的一条进展记录 (data/boss_sight/progress.json 的 entry)。",
    tags=["omni.material", "content.progress", "kind.source"],
    json_schema={
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "ref_type": {"type": "string", "enum": ["plan", "project"]},
            "ref_id": {"type": "string"},
            "text": {"type": "string"},
            "by": {"type": "string", "description": "human / agent"},
            "created_at": {"type": "string"},
        },
        "required": ["ref_type", "ref_id", "text"],
    },
)

PROJECT = Format(
    id="omni.project",
    name="项目",
    description=(
        "项目注册表 (data/registry/projects.json) 的一条项目记录; "
        "元数据权威源是其 PROJECT_INDEX.md (standards/concepts/project_index.md)。"
    ),
    tags=["omni.material", "content.project", "kind.source"],
    json_schema={
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "group": {"type": "string"},
            "index_path": {"type": ["string", "null"]},
            "plan_categories": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["id", "name"],
    },
)

CAPTURE = Format(
    id="omni.capture",
    name="界面捕获",
    description=(
        "统一捕获: 用户在任意位置截图(可选录屏/DOM快照/评论/三态), 按屏幕位置自动解析它压在哪个 "
        "omni 实体上(material/plan/note/project/task)。落盘 captures/ 的 markdown + 可选挂札记。"
        "见 plan docs/plans/dashboard/[2026-06-27]UNIVERSAL-CAPTURE。"
    ),
    tags=["omni.material", "content.capture", "kind.source"],
    json_schema={
        "type": "object",
        "properties": {
            "capture_kind": {
                "type": "string",
                "enum": ["capture", "element_comment", "page_snapshot", "debug_start"],
            },
            "modality": {"type": "string", "enum": ["still", "video", "dom_snapshot"]},
            "title": {"type": ["string", "null"]},
            "comment": {"type": "string"},
            "verdict": {"type": ["string", "null"], "enum": ["keep", "reject", "undecided", None]},
            "omni_uri": {"type": ["string", "null"], "description": "解析出的目标实体 omni://kind/id"},
            "route": {"type": "string"},
            "path": {"type": "string", "description": "落盘的 markdown 路径"},
        },
        "required": ["path"],
    },
)

REVIEW_MATERIAL = Format(
    id="omni.review-material",
    name="审阅材料",
    description=(
        "提交到审阅台 (boss_sight/reviewstage) 等用户审阅的一份材料。"
        "审阅材料是 material 的一种 (用户裁决 2026-06-13); reviewstage.Material 是其实例载体, "
        "kind/tier 映射到 tags (review.kind.* / review.tier.*)。"
    ),
    tags=["omni.material", "content.review", "kind.source"],
    json_schema={
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "kind": {"type": "string", "description": "image/markdown/html/key_question/custom_web_template/..."},
            "tier": {"type": "string", "enum": ["mandatory", "important", "processual", "ignored"]},
            "title": {"type": "string"},
            "status": {"type": "string", "enum": ["pending", "accepted", "rejected", "blocked"]},
            "source_plan_id": {"type": ["string", "null"]},
        },
        "required": ["id", "kind", "tier", "title"],
    },
)

MATERIAL_WIKI_PROFILE = Format(
    id="omni.material-wiki-profile",
    name="材料 Wiki 项目 Profile",
    description=(
        "项目材料分类、状态词表与动态扩展深度的机器合同。平台名录只保存 source 指针，"
        "分类正文留在项目 Wiki frontmatter 或 domain-tree 权威。"
    ),
    tags=["omni.material", "content.material-wiki-profile", "kind.source"],
    json_schema={
        "type": "object",
        "properties": {
            "profile_id": {"type": "string"},
            "project_id": {"type": "string"},
            "schema_version": {"type": "integer", "minimum": 1},
            "source": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["wiki_frontmatter", "domain_tree", "track"]},
                    "ref": {"type": "string"},
                },
                "required": ["kind", "ref"],
            },
        },
        "required": ["profile_id", "project_id", "schema_version", "source"],
    },
)

MATERIAL_WIKI_ENTRY = Format(
    id="omni.material-wiki-entry",
    name="材料 Wiki 本体条目",
    description=(
        "指向项目本体的一条材料身份与版本记录。canonical_ref 是正文唯一位置；"
        "本 Format 只承载归位、来源、版本、引用与自动会话信息。"
    ),
    tags=["omni.material", "content.material-wiki-entry", "kind.source"],
    json_schema={
        "type": "object",
        "properties": {
            "material_id": {"type": "string"},
            "project_id": {"type": "string"},
            "profile_id": {"type": "string"},
            "title": {"type": "string"},
            "canonical_ref": {"type": "string"},
            "placement_path": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "version": {"type": "string"},
            "status": {"type": "string"},
            "sources": {"type": "array", "items": {"type": "object"}, "minItems": 1},
            "relations": {"type": "object"},
            "author_session": {"type": ["string", "null"]},
        },
        "required": [
            "material_id", "project_id", "profile_id", "title", "canonical_ref",
            "placement_path", "version", "status", "sources",
        ],
    },
)

MATERIAL_REPORT_VIEW = Format(
    id="omni.material-report-view",
    name="材料报告聚合视图",
    description=(
        "报告只保存非材料说明与 material_ref 列表；latest 引用在渲染时读取当前有效版，"
        "fixed 引用读取指定版本，不复制 Wiki 本体正文。"
    ),
    tags=["omni.view", "content.material-report-view", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "narration": {"type": "string", "description": "明确标记为非材料的说明文字"},
            "material_refs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "material_id": {"type": "string"},
                        "version_policy": {"type": "string", "enum": ["latest", "fixed"]},
                        "version": {"type": ["string", "null"]},
                    },
                    "required": ["material_id", "version_policy"],
                },
            },
        },
        "required": ["title", "narration", "material_refs"],
    },
)

REVIEW_WEBGAME_SPEC = Format(
    id="omni.review.webgame-spec",
    name="网页游戏 Spec 审阅材料",
    description=(
        "网页交互游戏的新建或持续跟进 spec。它有一个'主体'(游戏本身), 审阅须始终围绕主体变化展开。"
        "法定审阅形态=三件套: 引导演示 + 文档 + 文件树 diff。本 Format 是 webgame-spec 这一审阅 kind 的权威定义点。"
    ),
    parent="omni.review-material",
    tags=["omni.material", "content.review", "review.kind.webgame-spec"],
    semantic_preconditions=[
        "引导演示: 注册标准化 tour, 以 html+live_url 材料承载真实 UI 导览(每步可评论)。规范 docs/standards/review/引导演示材料规范.md; 接口 wiki-core mountDemoTour + tools/ops/ops-run-demo-tour。在 extra.demo 给出 tour 材料 id 或 live_url。",
        "文档: spec 报告须是 wiki-core 文档, 含足够截图标注特性发生时机、链向 demo 与文件树 diff、可复制段落 id, 并承载导览三件套(对应需求/完成度/体验路径)。规范 docs/standards/review/spec报告材料规范.md; 在 extra.doc 给出文档材料 id 或 wiki 路径。",
        "文件树 diff: 用 `omni review filetree-diff` 生成并作为兄弟材料附加(extra.attached_to=本材料 id), 表明本次改了哪些文件。在 extra.filetree_diff 给出该兄弟材料 id。",
    ],
)

REVIEW_DECISION_CANDIDATE = Format(
    id="omni.review.decision-candidate",
    name="决策候选审阅材料",
    description=(
        "决策本体候选流水线的唯一队列材料(plan=[2026-07-10]DECISION-ONTOLOGY §六):"
        "任何想改陈述库(决策库/语义手册)的提议——不论来自偏离聚集、固化聚类、到期信号、"
        "AI 记忆还是人——统一成此类材料排队人裁。人机同门,只差发起者字段。"
        "裁决动作:审阅台 accept/reject(可评论修改意见);accept 后由 "
        "`omni decisions candidate-apply` 写回陈述库(新版本+取代链;有验证件的先过验证件)。"
        "正文=markdown 草案(inline_content);机器载荷在 extra.candidate。"
    ),
    parent="omni.review-material",
    tags=["omni.material", "content.review", "review.kind.decision-candidate"],
    json_schema={
        "type": "object",
        "properties": {
            "candidate": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "enum": ["deviation", "consolidate", "expiry", "memory", "human"],
                        "description": "信号入口:偏离聚集/固化聚类/到期/AI记忆/人工",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["new", "revise", "retire"],
                        "description": "提议动作:立新陈述/修订既有(带取代链)/退役",
                    },
                    "target_ids": {"type": "array", "items": {"type": "string"},
                                    "description": "被修订/退役的记录 id(action=new 可空)"},
                    "proposed": {"type": "object",
                                  "description": "提议的记录字段(decision.record 子集)"},
                    "by": {"type": "string", "description": "发起者(人名/agent 标识)"},
                    "applied_at": {"type": "string", "description": "写回完成时间(apply 后盖)"},
                    "applied_record_id": {"type": "string"},
                },
                "required": ["source", "action"],
            },
        },
        "required": ["candidate"],
    },
)

# ── Agent 常见产出物的审阅类型(2026-08-15 用户裁定): 给常见材料立语义, 不再是无说明的内置 kind ──

REVIEW_AGENT_WORKFLOW_REPORT = Format(
    id="omni.review.agent-workflow-report",
    name="Agent 任务报告",
    description=(
        "Agent 完成一项任务的收尾报告: 讲清做了什么、改了哪些文件、验证证据与下一步。"
        "这是 Agent 交付的默认材料类型 —— 任务做完先落一份报告, 而不是只留一段聊天记录。"
    ),
    parent="omni.review-material",
    tags=["omni.material", "content.review", "review.kind.agent-workflow-report"],
    semantic_preconditions=[
        "说明执行了什么(目标一句话 + 实际动作摘要)",
        "列出改动文件/产物的可寻址入口(路径、material id 或 wiki 引用)",
        "给出可复核的验证证据(测试/构建/命令回执), 不写「看起来对」",
        "区分事实、推断与待办; 结尾写结论与下一步",
    ],
)

REVIEW_PLAN = Format(
    id="omni.review.plan",
    name="Agent 计划书",
    description=(
        "Agent 起草的计划: 目标、范围、退出条件、产物清单与阶段顺序。"
        "计划是过程材料 —— 提交审阅后按计划推进, 完成时以任务报告收尾。"
    ),
    parent="omni.review-material",
    tags=["omni.material", "content.review", "review.kind.plan"],
    semantic_preconditions=[
        "写清目标与完成条件(用户能照着验收)",
        "范围写死: 明确不做的事与不在范围内的事",
        "列产物清单与阶段顺序, 标注哪些已经存在可复用",
        "风险与假设单列, 不混进正文"],
)

REVIEW_RESEARCH_REPORT = Format(
    id="omni.review.research-report",
    name="Agent 调研报告",
    description=(
        "Agent 公开资料/文献/方案调研的结论性报告: 带来源、区分事实与推断、"
        "结论不打分式夸大, 每条关键判断保留证据入口。"
    ),
    parent="omni.review-material",
    tags=["omni.material", "content.review", "review.kind.research-report"],
    semantic_preconditions=[
        "来源可追溯: 关键结论附 url/文件入口, 不编造",
        "事实、推断、未知三分; 未知明确标注而不是带过",
        "同类调研先查重(本地研究库/文档), 重复部分只补缺口",
        "结论给适用边界与反例, 不写绝对化判断",
    ],
)

REVIEW_DEMO = Format(
    id="omni.review.demo",
    name="Demo / 试玩",
    description=(
        "可交互演示或试玩切片: 给人当场跑、当场看, 不以文档代替体验。"
        "载体是 web 页面/可执行入口, 正文是「怎么打开、看什么」。"
    ),
    parent="omni.review-material",
    tags=["omni.material", "content.review", "review.kind.demo"],
    semantic_preconditions=[
        "给出可直接打开的入口(url/路径), 不是截图拼贴",
        "说明要看什么、几步能看到(体验路径)",
        "注明已知残缺与演示边界, 避免被当成成品",
    ],
)

REVIEW_UNCLASSIFIED = Format(
    id="omni.review.unclassified",
    name="未分类材料",
    description=(
        "暂时无法归入任何已注册类型的兜底。允许少量存在, 但提交者应优先选择具体类型;"
        "同类未分类堆积时说明缺一个新类型, 应在体系内注册而不是继续丢兜底。"
    ),
    parent="omni.review-material",
    tags=["omni.material", "content.review", "review.kind.unclassified"],
    semantic_preconditions=[
        "只在确实无法归类时使用; 能归类的用具体类型",
        "正文第一句说明「这是什么」与「为什么无法归类」",
    ],
)

FORMATS = [
    PLAN,
    PROGRESS_ENTRY,
    PROJECT,
    CAPTURE,
    REVIEW_MATERIAL,
    MATERIAL_WIKI_PROFILE,
    MATERIAL_WIKI_ENTRY,
    REVIEW_WEBGAME_SPEC,
    REVIEW_DECISION_CANDIDATE,
    REVIEW_AGENT_WORKFLOW_REPORT,
    REVIEW_PLAN,
    REVIEW_RESEARCH_REPORT,
    REVIEW_DEMO,
    REVIEW_UNCLASSIFIED,
]

VIEW_FORMATS = [MATERIAL_REPORT_VIEW]


def register_formats(registry: FormatRegistry) -> None:
    """注册公司级材料 Format（dispatch 约定签名: register_fn(registry)）。"""
    for fmt in (*FORMATS, *VIEW_FORMATS):
        if not registry.is_registered(fmt.id):
            registry.register(fmt)
