# [OMNI] origin=claude-code domain=skill_importer/formats.py ts=2026-04-09T00:00:00Z
# [OMNI] material_id="material:utility.skill_importer.material_definitions.config.py"
"""skill_importer.formats — Format 定义.

2026-04-09 新增: 旧 skill_importer 只用 ResourceDomainManifest 注册 Format 名字,
没有走 protocol/format.py 的 Format 对象 + FormatRegistry。
TeamChecker 因此 valid=False。
现在补一份正式的 Format 定义 + register_formats(registry), 和 workflow_factory
对齐。
"""

from __future__ import annotations

from omnicompany.protocol.format import Format, FormatRegistry


# ── 主管线 Format 链 ──────────────────────────────────────

SKILL_IMPORTER_RAW = Format(
    id="skill_importer.raw",
    name="SkillImporterRaw",
    description=(
        "用户的原始 skill 导入请求。内容语义: 包含 skill_dir 字段指向一个 Claude "
        "Code Skill 目录 (含 SKILL.md 和可选的 references/ scripts/ 子目录)。"
        "验证标准: skill_dir 必须存在且含 SKILL.md 文件。下游用途: 供 "
        "SkillParserRouter 解析, 产出结构化 sections 列表。"
    ),
    parent="requirement",
    tags=["domain.skill_importer", "stage.input", "kind.source"],
    json_schema={
        "type": "object",
        "properties": {
            "skill_dir": {
                "type": "string",
                "description": "Claude Code Skill 目录路径，必须包含 SKILL.md 文件",
            },
        },
        "required": ["skill_dir"],
    },
    examples=[
        {"skill_dir": ".claude/skills/my-skill"},
    ],
)

SKILL_IMPORTER_PARSED_SECTIONS = Format(
    id="skill_importer.parsed_sections",
    name="SkillImporterParsedSections",
    description=(
        "SKILL.md 按 markdown 标题切段后的结构化列表 + references/*.md + scripts/* "
        "的内容。每个 section 含 title/level/body。内容语义: 完整保留 skill 原始信息, "
        "不做语义归纳。验证标准: sections 非空, 每个 section 含必填字段。下游用途: "
        "StructureAnalysisRouter 读它做 LLM 语义归纳。"
    ),
    parent="skill_importer.raw",
    tags=["domain.skill_importer", "stage.parsed", "structured", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "level": {"type": "integer"},
                        "body": {"type": "string"},
                    },
                    "required": ["title", "level", "body"],
                },
            },
            "references": {
                "type": "object",
                "description": "references/*.md 文件名 → 内容",
            },
            "scripts": {
                "type": "object",
                "description": "scripts/* 文件名 → 内容",
            },
        },
        "required": ["sections"],
    },
    examples=[
        {
            "sections": [
                {"title": "Overview", "level": 1, "body": "This skill does X."},
                {"title": "Nodes", "level": 2, "body": "- node_a: input parser\n- node_b: processor"},
            ],
            "references": {"guide.md": "# Guide\nDetailed guide content."},
            "scripts": {},
        }
    ],
)

SKILL_IMPORTER_SKILL_STRUCTURE = Format(
    id="skill_importer.skill_structure",
    name="SkillImporterSkillStructure",
    description=(
        "LLM 归纳后的结构化 skill 蓝图: skill_purpose / skill_domain / nodes "
        "(id/title/kind/is_llm/...) / dag_edges / special_constraints / "
        "coverage_expectations。内容语义: 把 skill 的意图和节点拓扑提炼成 JSON 形态。"
        "验证标准: nodes 非空, 每个 node 含 id/title/kind, dag_edges 指向合法 node id。"
        "下游用途: MaterialInferenceRouter 基于它推断 format_in/format_out 命名。"
    ),
    parent="skill_importer.parsed_sections",
    tags=["domain.skill_importer", "stage.analyzed", "structured", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "skill_purpose": {"type": "string"},
            "skill_domain": {"type": "string"},
            "nodes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "kind": {"type": "string"},
                        "is_llm": {"type": "boolean"},
                    },
                    "required": ["id", "title", "kind"],
                },
            },
            "dag_edges": {
                "type": "array",
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 2,
                },
            },
            "special_constraints": {"type": "array", "items": {"type": "string"}},
            "coverage_expectations": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["skill_purpose", "skill_domain", "nodes"],
    },
    examples=[
        {
            "skill_purpose": "Parse SKILL.md and produce a workflow-factory requirement draft",
            "skill_domain": "skill_importer",
            "nodes": [
                {"id": "parser", "title": "SkillParser", "kind": "DETERMINISTIC", "is_llm": False},
                {"id": "analyzer", "title": "StructureAnalyzer", "kind": "LLM", "is_llm": True},
            ],
            "dag_edges": [["parser", "analyzer"]],
            "special_constraints": ["skill_dir must contain SKILL.md"],
            "coverage_expectations": ["All nodes in SKILL.md are represented"],
        }
    ],
)

SKILL_IMPORTER_FORMAT_CHAIN = Format(
    id="skill_importer.material_chain",
    name="SkillImporterFormatChain",
    description=(
        "为每个节点推断出 format_in/format_out 命名后的完整 skill_structure。"
        "内容语义: 在 skill_structure 基础上, 每个 node 新增 format_in 和 format_out "
        "两个字段, 使用 <domain>.<concept_slug> 约定。验证标准: 每个 node 都有 "
        "format_in/format_out, 相邻节点的 out/in 链式连通。下游用途: "
        "RequirementDraftRouter 生成 workflow-factory 可消费的 markdown 需求稿。"
    ),
    parent="skill_importer.skill_structure",
    tags=["domain.skill_importer", "stage.format_inferred", "structured", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "skill_purpose": {"type": "string"},
            "skill_domain": {"type": "string"},
            "nodes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "kind": {"type": "string"},
                        "format_in": {"type": "string"},
                        "format_out": {"type": "string"},
                    },
                    "required": ["id", "title", "kind", "format_in", "format_out"],
                },
            },
            "dag_edges": {"type": "array"},
        },
        "required": ["skill_domain", "nodes"],
    },
    examples=[
        {
            "skill_domain": "skill_importer",
            "nodes": [
                {"id": "parser", "title": "SkillParser", "kind": "DETERMINISTIC",
                 "format_in": "skill_importer.raw", "format_out": "skill_importer.parsed_sections"},
                {"id": "analyzer", "title": "StructureAnalyzer", "kind": "LLM",
                 "format_in": "skill_importer.parsed_sections", "format_out": "skill_importer.skill_structure"},
            ],
            "dag_edges": [["parser", "analyzer"]],
        }
    ],
)

SKILL_IMPORTER_REQUIREMENT_DRAFT = Format(
    id="skill_importer.requirement_draft",
    name="SkillImporterRequirementDraft",
    description=(
        "落盘后的 markdown 需求稿, 含 requirement_draft_path 和 requirement_draft_chars。"
        "实际 markdown 内容在 data/absorption/skill_digest/<skill>.md。"
        "内容语义: workflow-factory 的输入, 严格按约定段落结构组织 (目标 / Package "
        "位置 / 节点拓扑 / 节点规格 / Format 链 / 错误路由 / 约束 / 期望验收)。"
        "验证标准: 文件已落盘, chars ≥ 2000 (短于这个说明 skill 信息不够)。"
        "下游用途: 作为 workflow-factory 的 'text' 输入, 生成完整 package。"
    ),
    parent="skill_importer.material_chain",
    tags=["domain.skill_importer", "stage.drafted", "ready_for_workflow_factory", "kind.sink"],
    json_schema={
        "type": "object",
        "properties": {
            "requirement_draft_path": {
                "type": "string",
                "description": "落盘的 markdown 需求稿路径",
            },
            "requirement_draft_chars": {
                "type": "integer",
                "description": "需求稿字符数，应 ≥ 2000",
                "minimum": 0,
            },
        },
        "required": ["requirement_draft_path", "requirement_draft_chars"],
    },
    examples=[
        {
            "requirement_draft_path": "data/absorption/skill_digest/my-skill.md",
            "requirement_draft_chars": 3542,
        }
    ],
)


# ── 验证管线 Format ────────────────────────────────────────

SKILL_IMPORTER_COMPLIANCE_CHECK_REQUEST = Format(
    id="skill_importer.compliance_check_request",
    name="SkillImporterComplianceCheckRequest",
    description=(
        "独立 verify 管线的输入: {package_path, skill_structure}。package_path 指向 "
        "workflow-factory 生成的 OmniCompany package 目录, skill_structure 来自主管线 "
        "analyze 节点的产物。内容语义: 让 verify 路由器能同时看到 'skill 要什么' "
        "和 'workflow-factory 做了什么' 两边, 做对照检验。验证标准: package_path 存在, "
        "skill_structure 含 nodes + special_constraints。下游用途: "
        "VerifyAgainstSkillRouter 读它做 LLM 忠实度检查。"
    ),
    parent="requirement",
    tags=["domain.skill_importer", "stage.verify_input", "kind.source"],
    json_schema={
        "type": "object",
        "properties": {
            "package_path": {
                "type": "string",
                "description": "workflow-factory 生成的 OmniCompany package 目录路径",
            },
            "skill_structure": {
                "type": "object",
                "description": "来自主管线 analyze 节点的 skill 结构蓝图",
                "properties": {
                    "nodes": {"type": "array"},
                    "special_constraints": {"type": "array"},
                },
                "required": ["nodes"],
            },
        },
        "required": ["package_path", "skill_structure"],
    },
    examples=[
        {
            "package_path": "src/omnicompany/packages/services/my_skill",
            "skill_structure": {
                "skill_purpose": "Parse and import skill definitions",
                "skill_domain": "skill_importer",
                "nodes": [
                    {"id": "parser", "title": "SkillParser", "kind": "DETERMINISTIC"},
                ],
                "special_constraints": ["skill_dir must contain SKILL.md"],
            },
        }
    ],
)

SKILL_IMPORTER_COMPLIANCE_REPORT = Format(
    id="skill_importer.compliance_report",
    name="SkillImporterComplianceReport",
    description=(
        "LLM 生成的 compliance markdown 报告 + 判定结果。包含 compliance_report_path, "
        "compliance_report_chars, compliance_verdict (pass/partial/fail)。实际 markdown "
        "内容落在 data/absorption/skill_digest/<skill>.compliance.md, 含 整体结论 / "
        "节点覆盖检查 / 约束合规检查 / 质量问题 / 修复建议 五段。"
        "验证标准: 文件已落盘, verdict 字段合法。下游用途: 作为 skill_importer 管线"
        "整个循环的最终产物, 指导人工 / 其他管线继续优化 workflow-factory 的产出。"
    ),
    parent="skill_importer.compliance_check_request",
    tags=["domain.skill_importer", "stage.verified", "kind.sink"],
    json_schema={
        "type": "object",
        "properties": {
            "compliance_report_path": {
                "type": "string",
                "description": "落盘的 compliance markdown 报告路径",
            },
            "compliance_report_chars": {
                "type": "integer",
                "description": "报告字符数",
                "minimum": 0,
            },
            "compliance_verdict": {
                "type": "string",
                "enum": ["pass", "partial", "fail"],
                "description": "整体合规判定结果",
            },
        },
        "required": ["compliance_report_path", "compliance_verdict"],
    },
    examples=[
        {
            "compliance_report_path": "data/absorption/skill_digest/my-skill.compliance.md",
            "compliance_report_chars": 1820,
            "compliance_verdict": "pass",
        }
    ],
)


ALL_FORMATS = [
    SKILL_IMPORTER_RAW,
    SKILL_IMPORTER_PARSED_SECTIONS,
    SKILL_IMPORTER_SKILL_STRUCTURE,
    SKILL_IMPORTER_FORMAT_CHAIN,
    SKILL_IMPORTER_REQUIREMENT_DRAFT,
    SKILL_IMPORTER_COMPLIANCE_CHECK_REQUEST,
    SKILL_IMPORTER_COMPLIANCE_REPORT,
]


def register_formats(registry: FormatRegistry) -> None:
    """把 skill_importer domain 的 Format 注册到全局 FormatRegistry。"""
    for fmt in ALL_FORMATS:
        if not registry.is_registered(fmt.id):
            registry.register(fmt)
