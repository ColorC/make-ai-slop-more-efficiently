# [OMNI] origin=ai-ide domain=decisions ts=2026-06-18T00:00:00Z type=catalog status=active
# [OMNI] summary="决策库召回:按 id/statement/alias/tag/anchor 找'记过没/这条立足的信念还成不成立'。决策树的查询面。"
# [OMNI] why="决策树规模化硬成本在搜索/分类/寻址(手写需求)。catalog 是纯投影 library 的查询层:find 现算不馊,index.json 供人 grep。照 research/catalog 范式,但无外部 repo 要扫。"
# [OMNI] tags=decisions,catalog,discovery,decision-tree
"""决策库召回层。

find(query): 确定性 id/别名/子串/词重叠召回 → 零命中再便宜模型语义兜底(可降级)。
lookup(id) : 直接取一条记录。
rebuild_index(): 把当前库投影成 index.json(id→statement + by_alias/by_tag/by_project),供 grep。
索引是 library 的投影,不另存真源 —— find 永远现算,绝不馊。
"""

from __future__ import annotations

import json
import re

from . import library
from ._paths import INDEX_PATH, ensure_dirs

# 通用填充词:词重叠匹配时滤掉,避免"决策/这个/项目"这种词命中一切。
_FILLER_TOKENS = {"决策", "猜想", "信念", "评论", "这个", "那个", "项目", "的", "了", "是否", "要不要", "如何", "怎么"}


def _norm(s: str) -> str:
    """归一:去首尾空白、小写、合并空白、去常见标点。中文保留。"""
    t = (s or "").strip().lower()
    t = re.sub(r"[\s　]+", " ", t)
    t = re.sub(r"[?？。.,，、!！:：;；\"'`()（）\[\]【】]+", "", t)
    return t.strip()


def _tokens(s: str) -> list[str]:
    """中英混排分词:连续拉丁数字一组、连续中文一组。"""
    return re.findall(r"[a-z0-9]+|[一-鿿]+", _norm(s))


def _match_tokens(s: str) -> list[str]:
    return [t for t in _tokens(s) if len(t) >= 2 and t not in _FILLER_TOKENS]


def _item(rec: dict) -> dict:
    """把一条 record 投影成召回条目(decision.catalog_item 形态)。"""
    anchor = rec.get("anchor") or {}
    track = rec.get("track") or {}
    return {
        "id": rec.get("id", ""),
        "kind": rec.get("kind", ""),
        "statement": rec.get("statement", ""),
        "project": rec.get("project") or rec.get("applies_to", ""),
        "track": (f"{track.get('kind')}:{track.get('id')}" if track.get("id") else ""),
        "applies_to": rec.get("applies_to", ""),
        "anchor_ref": anchor.get("ref", ""),
        "aliases": list(rec.get("aliases") or []),
        "tags": list(rec.get("tags") or []),
        "status": rec.get("status", ""),
        "indexed_at": library.now_iso(),
    }


def _haystack(rec: dict) -> str:
    return _norm(" ".join([
        rec.get("statement", ""),
        " ".join(rec.get("aliases") or []),
        " ".join(rec.get("tags") or []),
        rec.get("applies_to", ""),
    ]))


def lookup(record_id: str) -> dict | None:
    """按 id 直取一条 active 记录。"""
    return library.get(record_id)


def rebuild_index() -> dict:
    """把当前库投影成 index.json(供 grep / 人读)。返回计数。"""
    ensure_dirs()
    items = [_item(r) for r in library.active_records()]
    by_alias: dict[str, list[str]] = {}
    by_tag: dict[str, list[str]] = {}
    by_project: dict[str, list[str]] = {}
    by_track: dict[str, list[str]] = {}

    def _add(bucket: dict[str, list[str]], key: str, rid: str) -> None:
        if not key:
            return
        lst = bucket.setdefault(key, [])
        if rid not in lst:
            lst.append(rid)

    for it in items:
        for a in it["aliases"]:
            _add(by_alias, _norm(a), it["id"])
        for tg in it["tags"]:
            _add(by_tag, str(tg).lower(), it["id"])
        _add(by_project, _norm(it["project"]), it["id"])
        _add(by_track, it["track"].lower(), it["id"])

    INDEX_PATH.write_text(
        json.dumps({"items": items, "by_alias": by_alias, "by_tag": by_tag,
                    "by_project": by_project, "by_track": by_track},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"total": len(items)}


def recall(situation: str, *, model: str | None = None, k: int = 15) -> dict:
    """回忆:面对某情境,从决策库聚合出"用户在这类情境下的决策倾向"。

    主线能力(不是把单条决策当规范,而是让累积的决策树浮现偏好倾向):
    按相关度召回 top-k 决策 → 便宜模型只从这些记录归纳出第二人称倾向描述 + 最支撑的几条。
    LLM 不可用时退回:只返相关记录,不综合(llm=False)。
    返回 {situation, tendency, supporting:[record], llm}。
    """
    recs = library.active_records()
    if not recs:
        return {"situation": situation, "tendency": None, "supporting": [], "llm": False}

    # 库还小就全喂给模型,让它判相关 + 综合(中文整段会被当成单 token,确定性排序对中文情境不可靠,
    # 故不用它预筛);库大了(>cap)再按词重叠粗筛 top-cap 兜底。
    cap = max(k, 180)
    if len(recs) <= cap:
        cand = recs
    else:
        qtoks = set(_tokens(situation))
        cand = sorted(recs, key=lambda r: len(qtoks & set(_tokens(_haystack(r)))), reverse=True)[:cap]

    from ._llm import safe_json

    cards = [{
        "id": r.get("id"),
        "project": r.get("project"),
        "statement": r.get("statement"),
        "chosen": [o.get("option") for o in (r.get("decision_space") or []) if o.get("chosen") is True],
        "rejected": [o.get("option") for o in (r.get("decision_space") or []) if o.get("chosen") is False],
        "rationale": (r.get("rationale") or "")[:200],
    } for r in cand]
    res = safe_json(
        "你在帮用户回忆:面对当前情境,他过去在相关情境下的决策倾向是什么。**只从给的决策记录里归纳,绝不臆造**。"
        "先挑出真正相关的几条,再用第二人称『你倾向…』综合成几句话的倾向描述(讲清他通常怎么选、重视什么、否决什么);"
        "relevant_ids 给最支撑的 3-6 条记录 id。若没有真正相关的,tendency 返空串。",
        {"situation": situation, "past_decisions": cards},
        {"type": "object", "properties": {
            "tendency": {"type": "string"},
            "relevant_ids": {"type": "array", "items": {"type": "string"}},
        }, "required": ["tendency"]},
        model=model, caller="decisions.recall", default=None,
    )
    if not res:
        return {"situation": situation, "tendency": None, "supporting": [], "llm": False}
    rel_ids = set(res.get("relevant_ids") or [])
    supporting = [r for r in cand if r.get("id") in rel_ids]
    return {"situation": situation, "tendency": res.get("tendency") or None,
            "supporting": supporting, "llm": True}


def find(query: str, *, allow_semantic: bool = True) -> list[dict]:
    """查库里有没有 query 指的决策/猜想。有就返记录列表,没有就空(别臆造)。"""
    q = _norm(query)
    if not q:
        return []
    recs = {r["id"]: r for r in library.active_records() if r.get("id")}
    if not recs:
        return []
    hits: dict[str, dict] = {}

    # 0 id 直击
    if query.strip() in recs:
        return [recs[query.strip()]]

    # 1 别名精确
    for r in recs.values():
        if any(_norm(a) == q for a in (r.get("aliases") or [])):
            hits[r["id"]] = r
    # 2 子串(statement/aliases/tags/applies_to)
    if not hits:
        for r in recs.values():
            if q in _haystack(r):
                hits[r["id"]] = r
    # 3 词重叠(任一有区分度的 query 词命中)
    if not hits:
        toks = _match_tokens(q)
        for r in recs.values():
            hay = _haystack(r)
            if any(t in hay for t in toks):
                hits[r["id"]] = r
    # 4 语义兜底(便宜模型,宁缺毋滥,失败降级为确定性结果)
    if not hits and allow_semantic:
        from ._llm import safe_json

        qtoks = set(_tokens(q))

        def _rel(r: dict) -> int:
            return len(qtoks & set(_tokens(_haystack(r))))

        ranked = sorted(recs.values(), key=_rel, reverse=True)
        cand = [{"id": r["id"], "statement": r.get("statement", ""), "kind": r.get("kind", "")}
                for r in ranked][:60]
        res = safe_json(
            "从候选决策记录里挑出与查询指同一件事的 id(可能 0 个)。只返回确实匹配的,宁缺毋滥。",
            {"query": query, "candidates": cand},
            {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "string"}}},
             "required": ["ids"]},
            caller="decisions.catalog.find", default={"ids": []},
        )
        for i in (res.get("ids") or []):
            if i in recs:
                hits[i] = recs[i]
    return list(hits.values())
