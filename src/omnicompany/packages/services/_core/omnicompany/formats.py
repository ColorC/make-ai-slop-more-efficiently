# [OMNI] origin=claude-code domain=services/_core/omnicompany ts=2026-06-13T03:40:00Z
# [OMNI] material_id="material:core.omnicompany.company_material_formats.py"
"""公司级材料的 Format 字典 — 材料统一计划阶段 0。

plan / progress / project / capture / 审阅材料 五类"事实材料"此前各有平行数据模型,
都不是 Format 实例 (2026-06-13 二重权威调研坐实, 用户裁决"实际上应该容纳进 material 系统")。
本模块是它们在 Format 体系里的唯一定义点: 任何新公司级材料先在这里立 Format, 再写消费代码。

schema 从各自现存真实数据模型提取 (boss_sight/progress.py, core/projects_registry.py,
boss_sight/captures/routes.py, boss_sight/reviewstage/store.py), 字段语义以源码为准,
这里只锚定身份与最小结构。

后续阶段 (见 docs/plans/format-material/[2026-06-13]MATERIAL-UNIFICATION/plan.md):
写入口落盘同时发 FactoryEvent(event_type=Format.id)。
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

FORMATS = [PLAN, PROGRESS_ENTRY, PROJECT, CAPTURE, REVIEW_MATERIAL, REVIEW_WEBGAME_SPEC,
           REVIEW_DECISION_CANDIDATE]


def register_formats(registry: FormatRegistry) -> None:
    """注册公司级材料 Format（dispatch 约定签名: register_fn(registry)）。"""
    for fmt in FORMATS:
        if not registry.is_registered(fmt.id):
            registry.register(fmt)
