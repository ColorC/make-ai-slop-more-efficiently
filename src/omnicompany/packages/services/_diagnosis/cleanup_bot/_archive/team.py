# [OMNI] origin=claude-code domain=cleanup_bot/pipeline.py ts=2026-04-08T03:23:36Z
# [OMNI] material_id="material:diagnosis.cleanup_bot.team_spec_declaration.py"
"""cleanup_bot — TeamSpec 声明"""

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
            id="evidence_gatherer",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="cleanup-gatherer",
                name="路径爬虫",
                format_in="cleanup.input",
                format_out="cleanup.evidence",
                validator=ValidatorSpec(
                    id="cleanup-gatherer-v",
                    kind=ValidatorKind.HARD,
                    description="采集包含关键词的物理路径，过滤为可疑列表",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="anomaly_detector"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="查找路径失败或无内容"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),
        TeamNode(
            id="anomaly_detector",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="cleanup-detector",
                name="AI 异常判官",
                format_in="cleanup.evidence",
                format_out="cleanup.plan",
                validator=ValidatorSpec(
                    id="cleanup-detector-v",
                    kind=ValidatorKind.SOFT,
                    description="LLM 评估哪些路径是 AI 误触产生的垃圾，生成清理脚本",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="rollback_planner"),
                    VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=2),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),
        TeamNode(
            id="rollback_planner",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="cleanup-planner",
                name="清理计划输出器",
                format_in="cleanup.plan",
                format_out="cleanup.done",
                validator=ValidatorSpec(
                    id="cleanup-planner-v",
                    kind=ValidatorKind.HARD,
                    description="输出安全清理计划（不自动执行）",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.EMIT),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="输出失败"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),
    ]

    edges = [
        TeamEdge(source="evidence_gatherer", target="anomaly_detector"),
        TeamEdge(source="anomaly_detector", target="rollback_planner"),
    ]

    return TeamSpec(
        id="cleanup",
        name="全局环境副作用清理工作流",
        description="追溯和探测由于大模型幻觉引起的错位物理文件，生成安全回滚计划",
        nodes=nodes,
        edges=edges,
        entry="evidence_gatherer",
    )
