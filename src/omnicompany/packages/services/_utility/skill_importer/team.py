# [OMNI] origin=claude-code domain=skill_importer/pipeline.py ts=2026-04-09T00:00:00Z
# [OMNI] material_id="material:utility.skill_importer.team_topology.config.py"
"""skill_importer pipeline — 2026-04-09 重构版.

拓扑变化:
  旧: parse → analyze → infer → gen (→ test_mode: executor/diagnose loop)
  新: parse → analyze → infer → draft_requirement (主流程, 最终产物是需求稿)

verify 节点是 **独立入口**, 不在主流程中 — 它在 workflow-factory 跑完后才调用,
通过单独的 CLI `omnikb-verify` 或编程触发, 因为它需要 workflow-factory 的产出作为
输入。
"""

from omnicompany.protocol.anchor import (
    AnchorSpec,
    Route,
    RouteAction,
    TransformMethod,
    TransformerSpec,
    ValidatorKind,
    ValidatorSpec,
    VerdictKind,
)
from omnicompany.protocol.team import (
    NodeKind,
    NodeMaturity,
    TeamEdge,
    TeamNode,
    TeamSpec,
)


def build_skill_importer_pipeline() -> TeamSpec:
    """主流程: 解析 skill → 归纳结构 → 推断 Format → 产需求稿.

    产物是 `data/absorption/skill_digest/<skill>.md`, 给 workflow-factory 消费。
    本管线不再生成 Python 代码 — 那是 workflow-factory 的职责。
    """
    nodes = [
        TeamNode(
            id="parse",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id="skill-importer-parse",
                name="ParseSkill",
                from_format="skill_importer.raw",
                to_format="skill_importer.parsed_sections",
                method=TransformMethod.RULE,
                description=(
                    "按 markdown 标题切 SKILL.md 为 sections, 同时抓 "
                    "references/*.md 和 scripts/* 的内容。纯 FS 操作无 LLM。"
                ),
            ),
            maturity=NodeMaturity.HYPOTHETICAL,
        ),
        TeamNode(
            id="analyze",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="skill-importer-analyze",
                name="AnalyzeStructure",
                format_in="skill_importer.parsed_sections",
                format_out="skill_importer.skill_structure",
                validator=ValidatorSpec(
                    id="skill-importer-analyze-v",
                    kind=ValidatorKind.SOFT,
                    description=(
                        "LLM 从 sections 中归纳 skill 的核心结构: 目的 / 节点列表 / "
                        "依赖边 / 特殊约束 / 覆盖预期。输出严格 JSON 供下游消费。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="infer"),
                    VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=2),
                },
            ),
            maturity=NodeMaturity.HYPOTHETICAL,
        ),
        TeamNode(
            id="infer",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id="skill-importer-infer",
                name="InferFormatChain",
                from_format="skill_importer.skill_structure",
                to_format="skill_importer.material_chain",
                method=TransformMethod.RULE,
                description=(
                    "基于节点的 output_description 推断 format_in/format_out 的语义命名, "
                    "使用 <domain>.<concept_slug> 约定, 保证相邻节点 Format 链连通。"
                ),
            ),
            maturity=NodeMaturity.HYPOTHETICAL,
        ),
        TeamNode(
            id="draft_requirement",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="skill-importer-draft",
                name="DraftRequirement",
                format_in="skill_importer.material_chain",
                format_out="skill_importer.requirement_draft",
                validator=ValidatorSpec(
                    id="skill-importer-draft-v",
                    kind=ValidatorKind.SOFT,
                    description=(
                        "把 skill_structure + format_chain 整合成一份 workflow-factory "
                        "可消费的 markdown 需求稿, 落盘到 data/absorption/skill_digest/ "
                        "供下一步跑 workflow-factory 使用。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.EMIT),
                    VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=2),
                },
            ),
            maturity=NodeMaturity.HYPOTHETICAL,
        ),
    ]

    edges = [
        TeamEdge(source="parse", target="analyze", condition=VerdictKind.PASS),
        TeamEdge(source="analyze", target="infer", condition=VerdictKind.PASS),
        TeamEdge(source="infer", target="draft_requirement", condition=VerdictKind.PASS),
    ]

    return TeamSpec(
        id="skill_importer",
        name="Skill Importer",
        description=(
            "解析外部 Claude Code Skill (含 SKILL.md / references / scripts) 并产出 "
            "workflow-factory 可消费的 markdown 需求稿。不再自己生成 Python 代码 — "
            "那是 workflow-factory 的职责。独立的 verify 节点 (VerifyAgainstSkillRouter) "
            "在 workflow-factory 产物后运行, 做忠实度检验。"
        ),
        nodes=nodes,
        edges=edges,
        entry="parse",
        tags=["domain.skill_importer", "phase.import"],
    )


def build_verify_pipeline() -> TeamSpec:
    """独立的忠实度检验管线. 在 workflow-factory 生成 package 后触发。

    输入需要 skill_structure (来自主管线的 analyze 节点产物) + package_path
    (workflow-factory 的产物路径)。
    """
    nodes = [
        TeamNode(
            id="verify",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="skill-importer-verify",
                name="VerifyAgainstSkill",
                format_in="skill_importer.compliance_check_request",
                format_out="skill_importer.compliance_report",
                validator=ValidatorSpec(
                    id="skill-importer-verify-v",
                    kind=ValidatorKind.SOFT,
                    description=(
                        "LLM 读 package 的全部 .py 文件 + 原 skill 的核心要求, 检查生成的"
                        "管线是否覆盖所有节点 / 约束 / 覆盖预期。产出 markdown compliance "
                        "report, 并根据 '整体结论' 段返回 PASS / PARTIAL / FAIL."
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.EMIT),
                    VerdictKind.PARTIAL: Route(action=RouteAction.EMIT),
                    VerdictKind.FAIL: Route(action=RouteAction.EMIT),
                },
            ),
            maturity=NodeMaturity.HYPOTHETICAL,
        ),
    ]

    return TeamSpec(
        id="skill_importer_verify",
        name="Skill Importer Compliance Check",
        description=(
            "独立入口: 给定 workflow-factory 生成的 package 路径 + 原 skill_structure, "
            "跑忠实度检验, 产出 compliance markdown report。"
        ),
        nodes=nodes,
        edges=[],
        entry="verify",
        tags=["domain.skill_importer", "phase.verify"],
    )
