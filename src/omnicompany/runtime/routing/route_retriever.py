# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:44Z
# [OMNI] material_id="material:runtime.routing.route_retriever.history_path_retriever.py"
"""RouteRetriever — 任务前从路由图检索候选路径，注入到 agent system prompt

V2 核心机制：
  1. 将用户任务文本 embedding
  2. 在 route_graph.db 中找 input_types 包含 user_request 的锚点节点
  3. 用 embedding 相似度 + hit_count 加权，选出 top-k 候选起始节点
  4. 沿 edges 做贪心展开，找出 top-N 路径（链式 / 树形）
  5. 将候选路径格式化为 system prompt 追加段，交给 run_agent 注入

输出格式:
  ## Known Route Hints (from memory)
  [7x confidence] user_request → feishu_chat_list_json → feishu_state_json_file → recall_result
  [4x confidence] user_request → git_log → commit_hash → git_stat_data
  If any path matches your task, follow it directly without re-exploration.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from omnicompany.runtime.storage.db_access import open_db_rw


# ────────────────────────────────────────────────────────────
# 数据结构
# ────────────────────────────────────────────────────────────

@dataclass
class RouteCandidate:
    """一条候选路径（线性展开的类型链）"""
    steps: list[str]        # 类型链: ["user_request", "feishu_chat_list_json", ...]
    node_ids: list[str]     # 对应的节点 ID
    total_weight: int       # 路径上所有节点 hit_count 之和（置信度）
    similarity: float       # 起始节点与任务文本的 embedding 相似度


# ────────────────────────────────────────────────────────────
# RouteRetriever
# ────────────────────────────────────────────────────────────

class RouteRetriever:
    """从 route_graph.db 检索最相关的历史路径。

    V3: 集成 BoltzmannRouter——embedding 筛选候选集后，
    用玻尔兹曼分布在候选集中做最终排序（替代纯余弦排序）。
    """

    def __init__(
        self,
        route_db_path: str | Path,
        top_k: int = 5,
        max_path_depth: int = 6,
        min_hit_count: int = 1,
        boltzmann_beta: float = 2.0,
    ):
        self.db_path = Path(route_db_path)
        self.top_k = top_k
        self.max_path_depth = max_path_depth
        self.min_hit_count = min_hit_count
        self.boltzmann_beta = boltzmann_beta

    def _load_graph(self) -> tuple[list[dict], list[dict]]:
        """加载节点和边（排除硬淘汰节点）。"""
        conn = open_db_rw(str(self.db_path))
        nodes_raw = conn.execute(
            "SELECT * FROM route_nodes WHERE hit_count >= ?", (self.min_hit_count,)
        ).fetchall()
        nodes = []
        for r in nodes_raw:
            d = dict(r)
            if d.get("hard_eliminated"):
                continue
            nodes.append(d)
        edges = [dict(r) for r in conn.execute(
            "SELECT * FROM route_edges ORDER BY weight DESC"
        ).fetchall()]
        conn.close()
        return nodes, edges

    async def retrieve(
        self,
        task_text: str,
        embedding_endpoint: str = "http://localhost:8000/api/embeddings",
    ) -> list[RouteCandidate]:
        """主入口：给定任务描述，返回最相关的候选路径列表。"""
        from omnicompany.runtime.llm.embedding_client import TextEmbeddingClient

        nodes, edges = self._load_graph()
        if not nodes:
            return []

        emb_client = TextEmbeddingClient(embedding_endpoint)
        task_emb = await emb_client.get_embedding(task_text)
        if not task_emb:
            return []

        # 构建 to_node_id 的邻接表: node_id -> [(from_output_types, weight)]
        # 以及 from_output_types -> [to_node_id]
        edges_by_from: dict[str, list[tuple[str, int]]] = {}  # key = json(from_types)
        for e in edges:
            k = e["from_output_types"]
            edges_by_from.setdefault(k, []).append((e["to_node_id"], e["weight"]))

        node_map = {n["node_id"]: n for n in nodes}

        # 找入口节点（from user_request）
        entry_key = json.dumps(["user_request"])
        entry_edges = edges_by_from.get(entry_key, [])

        # 也找多值形式 ["user_request", ...]
        extra_entries = []
        for k, targets in edges_by_from.items():
            types = json.loads(k)
            if "user_request" in types and k != entry_key:
                extra_entries.extend(targets)

        all_entry_edges = list(entry_edges) + extra_entries

        if not all_entry_edges:
            # 如果没有边，直接用相似度匹配节点
            all_entry_edges = [(n["node_id"], n["hit_count"]) for n in nodes
                               if "user_request" in json.loads(n["input_types"])]

        # 对入口节点按 embedding 相似度 × 玻尔兹曼权重综合打分
        from omnicompany.runtime.routing.boltzmann_router import (
            BoltzmannRouter as _BR,
            RouteCandidate as _RC,
        )
        br = _BR(beta=self.boltzmann_beta)

        scored: list[tuple[float, dict]] = []
        for (nid, weight) in all_entry_edges:
            node = node_map.get(nid)
            if not node:
                continue
            node_emb = json.loads(node["embedding"])
            if not node_emb:
                continue
            sim = cosine_sim(task_emb, node_emb)

            # 玻尔兹曼权重：低痛觉 + 高成功率 → 高权重
            rc = _RC(
                node_id=nid,
                pain_score=node.get("pain_score", 0.0) or 0.0,
                success_rate=node.get("success_rate", -1.0) or -1.0,
                hit_count=node.get("hit_count", 0),
                deprecated=bool(node.get("deprecated", 0)),
                hard_eliminated=bool(node.get("hard_eliminated", 0)),
            )
            if rc.hard_eliminated or rc.deprecated:
                continue
            bw = br.compute_weights([rc])[0]
            combined_score = sim * bw
            scored.append((combined_score, node))

        scored.sort(key=lambda x: -x[0])
        top_entries = [(s, n) for s, n in scored[:self.top_k]]

        # 从每个入口节点沿边展开，找最优路径
        candidates: list[RouteCandidate] = []
        for combined_score, entry_node in top_entries:
            sim = combined_score  # 综合得分传递到 RouteCandidate.similarity
            paths = self._expand_path(
                entry_node, node_map, edges_by_from, depth=0
            )
            for path_nodes in paths:
                types_chain = ["user_request"]
                for n in path_nodes:
                    out = json.loads(n["output_types"])
                    if out:
                        types_chain.extend(out)
                total_w = sum(n["hit_count"] for n in path_nodes)
                candidates.append(RouteCandidate(
                    steps=types_chain,
                    node_ids=[n["node_id"] for n in path_nodes],
                    total_weight=total_w,
                    similarity=sim,
                ))

        # 去重（相同 steps 的路径保留权重最高的）
        seen: dict[str, RouteCandidate] = {}
        for c in candidates:
            key = "→".join(c.steps)
            if key not in seen or c.total_weight > seen[key].total_weight:
                seen[key] = c

        # 按 similarity * log(total_weight+1) 排序
        result = sorted(
            seen.values(),
            key=lambda c: c.similarity * math.log(c.total_weight + 1),
            reverse=True,
        )
        return result[:self.top_k]

    def _expand_path(
        self,
        node: dict,
        node_map: dict[str, dict],
        edges_by_from: dict[str, list[tuple[str, int]]],
        depth: int,
        visited: set[str] | None = None,
    ) -> list[list[dict]]:
        """从节点出发，贪心展开路径（DFS，返回所有路径，限深度）。"""
        if visited is None:
            visited = set()
        if depth >= self.max_path_depth or node["node_id"] in visited:
            return [[node]]

        visited = visited | {node["node_id"]}
        out_types = json.loads(node["output_types"])
        if not out_types:
            return [[node]]

        next_key = json.dumps(sorted(out_types))
        next_edges = edges_by_from.get(next_key, [])

        if not next_edges:
            return [[node]]

        # 取权重最高的后继（贪心）
        next_edges_sorted = sorted(next_edges, key=lambda x: -x[1])
        paths = []
        for nid, _ in next_edges_sorted[:2]:  # 最多展开 2 条分支
            next_node = node_map.get(nid)
            if not next_node:
                continue
            sub_paths = self._expand_path(next_node, node_map, edges_by_from, depth + 1, visited)
            for sp in sub_paths:
                paths.append([node] + sp)

        return paths if paths else [[node]]


# ────────────────────────────────────────────────────────────
# 格式化为 system prompt 追加段
# ────────────────────────────────────────────────────────────

def format_route_hints(candidates: list[RouteCandidate], max_hints: int = 4) -> str:
    """将候选路径格式化为 system prompt 的追加段落。"""
    if not candidates:
        return ""

    lines = [
        "",
        "## Known Route Hints (from memory)",
        "Based on previous runs, the following type-chains have been observed for similar tasks.",
        "If any path matches your task, you MAY follow it directly — but always verify with the actual environment.",
        "",
    ]
    for i, c in enumerate(candidates[:max_hints]):
        chain = " → ".join(c.steps)
        lines.append(f"  [{c.total_weight}x / sim={c.similarity:.2f}] {chain}")

    lines += [
        "",
        "These are memory suggestions, not hard constraints.",
        "If the task is different or the environment has changed, explore freely.",
    ]
    return "\n".join(lines)


# ────────────────────────────────────────────────────────────
# 数学工具
# ────────────────────────────────────────────────────────────

def cosine_sim(v1: list[float], v2: list[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = math.sqrt(sum(a * a for a in v1))
    n2 = math.sqrt(sum(b * b for b in v2))
    return max(-1.0, min(1.0, dot / (n1 * n2))) if n1 and n2 else 0.0
