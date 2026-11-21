# [OMNI] origin=claude-code domain=software_engineering/generated ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.generated.team_topology.dag.py"
from omnicompany.protocol.anchor import (
    AnchorSpec,
    Route,
    RouteAction,
    ValidatorKind,
    ValidatorSpec,
    VerdictKind,
)
from omnicompany.protocol.team import (
    NodeKind,
    TeamEdge,
    TeamNode,
    TeamSpec,
)


def build_team() -> TeamSpec:
    """构建文本统计管线"""

    # 节点定义
    validate_input_node = TeamNode(
        id="validate_input_node",
        kind=NodeKind.ANCHOR,
        anchor=AnchorSpec(
            id="a_validate_input_node",
            name="validate_input_node",
            format_in="sw.text-input",
            format_out="sw.input-check-result",
            validator=ValidatorSpec(
                id="v_validate_input_node",
                kind=ValidatorKind.HARD,
                description=(
                    "接收用户文本输入意图，执行确定性校验逻辑。检查 JSON 结构合法性及 'text' 字段是否存在。"
                    "若 text 为空字符串或 null，生成 status=FAIL 的验证结果对象；若非空，生成 status=PASS 的对象。"
                    "此节点作为守门员，确保下游仅处理有效数据。"
                )
            ),
            routes={
                VerdictKind.PASS: Route(action=RouteAction.NEXT, target="calculate_stats_node"),
                VerdictKind.FAIL: Route(action=RouteAction.HALT),
            },
        ),
    )

    calculate_stats_node = TeamNode(
        id="calculate_stats_node",
        kind=NodeKind.ANCHOR,
        anchor=AnchorSpec(
            id="a_calculate_stats_node",
            name="calculate_stats_node",
            format_in="sw.input-check-result",
            format_out="sw.stats-metrics",
            validator=ValidatorSpec(
                id="v_calculate_stats_node",
                kind=ValidatorKind.HARD,
                description=(
                    "读取上游验证结果。若输入状态为 FAIL，节点终止并返回验证失败信息；若为 PASS，"
                    "对文本执行纯确定性统计计算（字数、行数、字符数）。计算逻辑不依赖 LLM，"
                    "直接基于字符串操作生成统计指标对象。"
                )
            ),
            routes={
                VerdictKind.PASS: Route(action=RouteAction.EMIT),
                VerdictKind.FAIL: Route(action=RouteAction.HALT),
            },
        ),
    )

    # 边定义
    edges = [
        TeamEdge(
            source="validate_input_node",
            target="calculate_stats_node",
            condition=VerdictKind.PASS,
            feedback=False,
        ),
    ]

    return TeamSpec(
        id="domains/software_engineering/generated/team",
        name="generated",
        description="Generated text statistics pipeline.",
        entry="validate_input_node",
        nodes=[validate_input_node, calculate_stats_node],
        edges=edges,
        tags=["software_engineering", "generated", "text_stats"],
    )
