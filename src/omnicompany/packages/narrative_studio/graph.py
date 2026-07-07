"""节点图共享辅助:入边统计 + 入口判定。

health 与 playthrough 都依赖"入口节点"概念;集中在此保证两处口径唯一,
避免 health 认为可达而 playthrough 起点不同导致的偏差报告。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, Optional

from .models import NodeType, Project


def inbound_counts(project: Project) -> Dict[str, int]:
    """每个 node id 的入边数(只数 target 落在已知节点上的连接)。"""
    known = {n.id for n in project.nodes}
    inbound: Dict[str, int] = defaultdict(int)
    for c in project.connections:
        if c.target in known:
            inbound[c.target] += 1
    return inbound


def reachable_node_ids(project: Project) -> set:
    """从入口出发,沿 connections 可达的全部 node id(BFS)。"""
    start = entry_node_id(project)
    if start is None:
        return set()
    out_edges: Dict[str, list] = defaultdict(list)
    known = {n.id for n in project.nodes}
    for c in project.connections:
        if c.source in known and c.target in known:
            out_edges[c.source].append(c.target)
    seen = {start}
    stack = [start]
    while stack:
        cur = stack.pop()
        for nxt in out_edges.get(cur, []):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def entry_node_id(project: Project) -> Optional[str]:
    """唯一入口判定:无入边的第一个 scene/hub 节点;退而求其次第一个无入边节点;再退第一个节点。"""
    nodes = project.nodes or []
    if not nodes:
        return None
    targets = {c.target for c in (project.connections or [])}
    for n in nodes:
        if n.id not in targets and n.type in (NodeType.scene, NodeType.hub):
            return n.id
    for n in nodes:
        if n.id not in targets:
            return n.id
    return nodes[0].id
