# [OMNI] origin=ai-ide domain=decisions ts=2026-06-27T00:00:00Z type=module status=active
# [OMNI] summary="管理治理:找近重复决策簇(同 session + 语句高度相似),供去重/标『同一决策多次落定』。"
# [OMNI] why="抽取管线会把同一决策在一个 session 里反复落定(如酒馆要美术 DEC-1270/1277/1333/1338),投影会节点膨胀。权威=plan B7。"
# [OMNI] tags=decisions,exploration,dedup,management
"""探索图管理治理:近重复决策簇。"""

from __future__ import annotations

import re

from .. import library

_WORD = re.compile(r"[\w一-鿿]+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    """中文按字、英文按词,做相似度的特征集。"""
    out: set[str] = set()
    for tok in _WORD.findall((text or "").lower()):
        if any("一" <= ch <= "鿿" for ch in tok):
            out.update(tok)          # 中文逐字
        else:
            out.add(tok)             # 英文整词
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b)


def duplicate_clusters(project: str | None = None, threshold: float = 0.6) -> list[list[dict]]:
    """同一 session 内、语句 Jaccard 相似度 ≥ threshold 的决策聚成簇(size>1)。

    返回 [[{id, statement, session_ref}, ...], ...],按簇大小降序。
    """
    recs = [r for r in library.active_records()
            if r.get("kind") == "decision"
            and (project is None or (r.get("project") or "") == project)]
    by_session: dict[str, list[dict]] = {}
    for r in recs:
        sr = (r.get("origin") or {}).get("session_ref") or "(无session)"
        by_session.setdefault(sr, []).append(r)

    clusters: list[list[dict]] = []
    for sr, group in by_session.items():
        feats = [(_tokens(r.get("statement", "")), r) for r in group]
        used: set[int] = set()
        for i in range(len(feats)):
            if i in used:
                continue
            ai, ri = feats[i]
            cluster = [ri]
            used.add(i)
            for j in range(i + 1, len(feats)):
                if j in used:
                    continue
                aj, rj = feats[j]
                if _jaccard(ai, aj) >= threshold:
                    cluster.append(rj)
                    used.add(j)
            if len(cluster) > 1:
                clusters.append([{"id": c["id"], "statement": c.get("statement", ""),
                                  "session_ref": sr} for c in cluster])
    clusters.sort(key=len, reverse=True)
    return clusters
