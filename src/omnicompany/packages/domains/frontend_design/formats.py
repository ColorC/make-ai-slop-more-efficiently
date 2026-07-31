# [OMNI] origin=claude-code domain=frontend_design ts=2026-07-01T00:00:00Z type=format status=design
# [OMNI] summary="frontend_design domain 的 Material(Format)契约。审查管线节点间流动的数据。"
# [OMNI] why="框架级统一: 产物只能是 Material。把 请求/门禁结果/相对评审/记录 声明成 Format。不打分用证据编进 schema。"
# [OMNI] tags=frontend_design,format,material
"""frontend_design domain Materials。

链路: frontend_design.review_request → .intake → .gate_result → .vlm_review → .review_record
两分支(dashboard/webgame)共用同一套 Format, 靠 review_request.archetype / project 分流。
"""

from __future__ import annotations

from omnicompany.protocol.format import Format, FormatRegistry


REVIEW_REQUEST = Format(
    id="frontend_design.review_request",
    name="FrontendReviewRequest",
    description=(
        "一次前端界面审查请求。surface=要审的界面(url/截图路径/DOM快照); "
        "archetype=dashboard|webgame(定标尺与门禁参数); ruler_ref=标尺真源指针; "
        "baseline_ref=相对评审基准图(可选); project=决策沉淀归属(dashboard-design|webgame-ui)。"
    ),
    tags=["domain.frontend_design", "stage.request", "kind.source"],
    json_schema={
        "type": "object",
        "properties": {
            "surface": {"type": "string"},
            "archetype": {"type": "string", "enum": ["dashboard", "webgame"]},
            "ruler_ref": {"type": "string"},
            "baseline_ref": {"type": ["string", "null"]},
            "project": {"type": "string"},
        },
        "required": ["surface"],
    },
)

REVIEW_INTAKE = Format(
    id="frontend_design.intake",
    name="FrontendReviewIntake",
    description="入题态: 归一化审查请求 + 锁定 archetype/标尺 + 建 run_dir。branch=dashboard|webgame。",
    tags=["domain.frontend_design", "stage.intake", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "surface": {"type": "string"},
            "branch": {"type": "string"},
            "ruler_ref": {"type": "string"},
            "baseline_ref": {"type": ["string", "null"]},
            "project": {"type": "string"},
            "run_dir": {"type": "string"},
        },
        "required": ["surface", "branch", "run_dir"],
    },
)

GATE_RESULT = Format(
    id="frontend_design.gate_result",
    name="FrontendGateResult",
    description=(
        "确定性门禁态: 跑可判定规则后的 failures 证据列表(不打分)。"
        "每条 failure 带 rule/severity/evidence/locator。checked=已跑过的规则清单。"
    ),
    tags=["domain.frontend_design", "stage.gate", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "branch": {"type": "string"},
            "run_dir": {"type": "string"},
            "failures": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "rule": {"type": "string"},
                        "severity": {"type": "string"},
                        "evidence": {"type": "string"},
                        "locator": {"type": "string"},
                    },
                    "required": ["rule", "evidence"],
                },
            },
            "checked": {"type": "array", "items": {"type": "string"}},
            "gate_status": {"type": "string"},
            "degraded": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["branch", "failures"],
    },
)

VLM_REVIEW = Format(
    id="frontend_design.vlm_review",
    name="FrontendVlmReview",
    description=(
        "VLM 相对评审态: 对基准图成对比较的证据列表(不打分)。"
        "每条 comparison 带 aspect/verdict(better|worse|same|n/a)/evidence, 绝不含可信度分。"
    ),
    tags=["domain.frontend_design", "stage.review", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "branch": {"type": "string"},
            "run_dir": {"type": "string"},
            "failures": {"type": "array", "items": {"type": "object"}},
            "comparisons": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "aspect": {"type": "string"},
                        "verdict": {"type": "string", "enum": ["better", "worse", "same", "n/a"]},
                        "evidence": {"type": "string"},
                    },
                    "required": ["aspect", "evidence"],
                },
            },
            "review_status": {"type": "string"},
        },
        "required": ["branch"],
    },
)

REVIEW_RECORD = Format(
    id="frontend_design.review_record",
    name="FrontendReviewRecord",
    description=(
        "管线 sink: 汇总门禁 + 相对评审后的改进建议 + 已沉淀的决策 id 列表 + 报告路径。"
        "decisions_recorded=写进 decisions 域的 DEC-/BLF- id(project 分流)。"
    ),
    tags=["domain.frontend_design", "stage.record", "kind.sink"],
    json_schema={
        "type": "object",
        "properties": {
            "branch": {"type": "string"},
            "run_dir": {"type": "string"},
            "improvements": {"type": "array", "items": {"type": "object"}},
            "decisions_recorded": {"type": "array", "items": {"type": "string"}},
            "report": {"type": "string"},
        },
        "required": ["branch"],
    },
)


# ── 域级产物层级注册(域剖面草案四层级表) ─────────────────────────────────────
# 决策树=具象管线: 每层一条 Format, 走标签机制登记进 material 系统的 review 注册表。
# material_types 读这些标签得出"域→有序层级清单 / 项目→在册轨道"。层级词汇的家在域包,
# 不新造注册表(域剖面草案 §一.1)。
#
# 载体选型(读 protocol/format.py 后定): 层级序 + 层级名走 STAGE_TAG_PREFIX 标签
# (值形如 <序>.<层级名>, material_types 解析出序与名); 这一层是什么走 description;
# 形态期望 kind 走 EXPECTED_KIND_TAG_PREFIX 标签(可多值); 门禁执法器走
# GATE_TAG_PREFIX 标签(单值, 对应决策库 enforced_by 的执法边)。
STAGE_TAG_PREFIX = "review.stage.frontend_design."
STAGE_MEMBER_TAG_PREFIX = "review.stage-member.frontend_design."
EXPECTED_KIND_TAG_PREFIX = "review.stage-expected-kind."
GATE_TAG_PREFIX = "review.stage-gate."

# 层级 Format id 前缀(与 REVIEW_* 数据契约 Format 分开, 后者是节点间流动数据, 这里是产物层级)。
_STAGE_ID_PREFIX = "frontend_design.stage."


def _stage(seq: int, layer: str, *, desc: str, kinds: list[str], gate: str) -> Format:
    return Format(
        id=f"{_STAGE_ID_PREFIX}{seq}.{layer}",
        name=f"前端设计层级 · {layer}",
        description=desc,
        tags=[
            "domain.frontend_design",
            f"{STAGE_TAG_PREFIX}{seq}.{layer}",
            *[f"{EXPECTED_KIND_TAG_PREFIX}{k}" for k in kinds],
            f"{GATE_TAG_PREFIX}{gate}",
        ],
    )


STAGE_INFO_AUDIT = _stage(
    1, "信息审计",
    desc="盘点这个界面的信息架构:有哪些层级、密度合不合适、缺了什么、有没有堆冗余说明。产出一份带标注截图的清单。",
    kinds=["markdown"],
    gate="frontend_design.ux_audit.enum_info",
)
STAGE_INTERACTION_AUDIT = _stage(
    2, "交互审计",
    desc="盘点所有操作入口与路径:每个操作从哪进、走几步、状态怎么流转,并逐条判断这个交互到底需不需要存在。",
    kinds=["markdown"],
    gate="frontend_design.ux_audit.enum_interactions",
)
STAGE_DESIGN = _stage(
    3, "设计稿",
    desc="给出结构与视觉方案:组件怎么选、布局怎么排、token 怎么用。产出可看的设计稿(截图或 html)。",
    kinds=["image", "html"],
    gate="frontend_design.frostpane.css#token 系",
)
STAGE_ACTUAL = _stage(
    4, "实际稿",
    desc="实装后的真实界面:可点 demo 或截图组。对着设计稿做成对比较,并过一遍真 UI 回归。",
    kinds=["demo", "html"],
    gate="相对评审 + 真 UI 回归",
)

STAGE_FORMATS = [STAGE_INFO_AUDIT, STAGE_INTERACTION_AUDIT, STAGE_DESIGN, STAGE_ACTUAL]


# ── 域成员项目登记 ───────────────────────────────────────────────────────────
# 项目×域归属(域剖面草案 §一.2): 一个项目可属多域, 材料校验时 项目→域集合→合法层级并集。
# 标签形如 review.stage-member.frontend_design.<project>; material_types 据此得出
# 项目→所属域集合。归属维度将来迁进决策库项目名录(唯一真源), 现以域包 Format 承载先行。
WALKER_MEMBER = Format(
    id="frontend_design.member.walker",
    name="前端设计域成员 · walker",
    description="行者无乡(walker)属前端设计域(webgame 分支), 材料走本域四层级词汇校验。",
    tags=["domain.frontend_design", f"{STAGE_MEMBER_TAG_PREFIX}walker"],
)

# 域自身的项目实体(frontend-design, 交接单位): 让接手者在本项目页就能看到域作业管线。
SELF_MEMBER = Format(
    id="frontend_design.member.frontend-design",
    name="前端设计域成员 · frontend-design",
    description="前端设计管线项目本体属前端设计域, 域级材料(标尺/方法/评审)落这里。",
    tags=["domain.frontend_design", f"{STAGE_MEMBER_TAG_PREFIX}frontend-design"],
)

MEMBER_FORMATS = [WALKER_MEMBER, SELF_MEMBER]


# ── 层级→适用裁决映射(域剖面草案: 参考来源 = 材料样例 + 适用裁决) ─────────────
# 九条设计语言裁决按内容归位到最相关层级(域作者知识, 非机械可推——裁决本身不带层级字段)。
# 端点只据此挑出真实存在于决策库且 status=adopted 的裁决, 绝不凭映射凭空造裁决。
# key=层级名, value=按相关度排的 DEC id 列表。
STAGE_RULING_MAP: dict[str, list[str]] = {
    "信息审计": ["DEC-2026-07-04-088", "DEC-2026-07-04-086", "DEC-2026-07-04-087"],
    "交互审计": ["DEC-2026-07-04-089", "DEC-2026-07-04-090"],
    "设计稿": ["DEC-2026-07-04-091", "DEC-2026-07-04-095", "DEC-2026-07-04-085", "DEC-2026-07-04-092"],
    "实际稿": [],
}


ALL_FORMATS = [
    REVIEW_REQUEST,
    REVIEW_INTAKE,
    GATE_RESULT,
    VLM_REVIEW,
    REVIEW_RECORD,
    *STAGE_FORMATS,
    *MEMBER_FORMATS,
]


def register_formats(registry: FormatRegistry) -> None:
    for fmt in ALL_FORMATS:
        if not registry.is_registered(fmt.id):
            registry.register(fmt)
