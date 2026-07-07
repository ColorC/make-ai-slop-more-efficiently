# [OMNI] origin=claude-code domain=services/_diagnosis/ux_audit ts=2026-06-30T00:00:00Z type=team status=active
# [OMNI] summary="ux_audit Team 拓扑:InteractionEnumerator→InfoEnumerator→NavEnumerator→Consolidator(EMIT)。全确定性 SOFT。"
# [OMNI] why="把'前端三维 UX 审计(交互/信息/跳转)+ 据矩阵打错位标记'做成可复用可观测的 omnicompany team,复跑于 omnidashboard/lofa/poof/whatnow。"
# [OMNI] material_id="material:services._diagnosis.ux_audit.team"
"""ux_audit Team · 拓扑声明(4 节点确定性链)。"""
from __future__ import annotations

from omnicompany.protocol.anchor import (
    AnchorSpec, Route, RouteAction, ValidatorKind, ValidatorSpec, VerdictKind,
)
from omnicompany.protocol.team import NodeKind, NodeMaturity, TeamEdge, TeamNode, TeamSpec


def _anchor(node_id, fmt_in, fmt_out, *, desc, action):
    return TeamNode(
        id=node_id,
        kind=NodeKind.ANCHOR,
        maturity=NodeMaturity.GROWING,
        anchor=AnchorSpec(
            id=f'a_{node_id}',
            name=node_id,
            format_in=fmt_in,
            format_out=fmt_out,
            validator=ValidatorSpec(id=f'v_{node_id}', kind=ValidatorKind.SOFT, description=desc),
            routes={
                VerdictKind.PASS: Route(action=action),
                VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=1),
            },
        ),
    )


def build_team() -> TeamSpec:
    """前端三维 UX 审计 — 交互/信息/跳转确定性枚举 + 汇总错位报告。"""
    nodes = [
        _anchor('InteractionEnumerator', 'ux_audit.target', 'ux_audit.accum',
                desc="扫 .tsx 枚举每面板的露出按钮/⋯收纳/select/input/onClick。", action=RouteAction.NEXT),
        _anchor('InfoEnumerator', 'ux_audit.accum', 'ux_audit.accum',
                desc="每面板信息层级信号:字号档(深度)/字重档/整段说明文字/mono。", action=RouteAction.NEXT),
        _anchor('NavEnumerator', 'ux_audit.accum', 'ux_audit.accum',
                desc="枚举导航调用 → 跳转边(从面板→动作→去向)。", action=RouteAction.NEXT),
        _anchor('Consolidator', 'ux_audit.accum', 'ux_audit.report',
                desc="汇总三维 + 据矩阵/层级打错位标记 → 报告 material,落盘。", action=RouteAction.EMIT),
    ]
    edges = [
        TeamEdge(source='InteractionEnumerator', target='InfoEnumerator', condition=VerdictKind.PASS),
        TeamEdge(source='InfoEnumerator', target='NavEnumerator', condition=VerdictKind.PASS),
        TeamEdge(source='NavEnumerator', target='Consolidator', condition=VerdictKind.PASS),
    ]
    return TeamSpec(
        id='ux_audit',
        name='ux_audit',
        description='前端 src 三维 UX 审计(交互/信息/跳转)— 确定性枚举 + 据频率×重要性矩阵/信息层级打错位标记。可复跑于 omnidashboard/lofa/poof/whatnow。',
        entry='InteractionEnumerator',
        nodes=nodes,
        edges=edges,
        tags=['audit', 'ux', 'frontend', 'interaction', 'diagnosis'],
    )
