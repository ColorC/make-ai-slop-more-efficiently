# [OMNI] origin=claude-code domain=lap_auditor/pipeline.py ts=2026-04-08T03:23:36Z
# [OMNI] material_id="material:diagnosis.lap_auditor.team_specification.python"
"""lap_auditor — TeamSpec 声明"""

from __future__ import annotations

from omnifactory.protocol.anchor import (
    AnchorSpec,
    Route,
    RouteAction,
    ValidatorKind,
    ValidatorSpec,
    VerdictKind,
)
from omnifactory.protocol.team import (
    NodeKind,
    NodeMaturity,
    TeamEdge,
    TeamNode,
    TeamSpec,
)


def build_team() -> TeamSpec:
    nodes = [
        TeamNode(
            id="context_getter",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="lap-audit-getter",
                name="源码拉取器",
                format_in="lap_auditor.input",
                format_out="lap_auditor.context",
                validator=ValidatorSpec(
                    id="lap-audit-getter-v",
                    kind=ValidatorKind.HARD,
                    description="读取目标路径下的 Python 源码文件",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="spec_auditor"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="拉取源码失败"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),
        TeamNode(
            id="spec_auditor",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="lap-audit-auditor",
                name="LLM 架构审计员",
                format_in="lap_auditor.context",
                format_out="lap_auditor.report",
                validator=ValidatorSpec(
                    id="lap-audit-auditor-v",
                    kind=ValidatorKind.SOFT,
                    description="LLM 评估代码对 LAP 四大红线的依从度",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="report_formatter"),
                    VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=2),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),
        TeamNode(
            id="report_formatter",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="lap-audit-formatter",
                name="报告格式化输出",
                format_in="lap_auditor.report",
                format_out="lap_auditor.done",
                validator=ValidatorSpec(
                    id="lap-audit-formatter-v",
                    kind=ValidatorKind.HARD,
                    description="打印审计报告到控制台",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.EMIT),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="报告输出失败"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),
    ]

    edges = [
        TeamEdge(source="context_getter", target="spec_auditor"),
        TeamEdge(source="spec_auditor", target="report_formatter"),
    ]

    return TeamSpec(
        id="lap-audit",
        name="LAP 架构审计工作流",
        description="抓取目录代码并审计其对 LAP 六元规范的依从度",
        nodes=nodes,
        edges=edges,
        entry="context_getter",
    )
