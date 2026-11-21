# [OMNI] origin=ai-ide domain=research ts=2026-06-14T00:00:00Z type=catalog status=active
# [OMNI] summary="统一本地资产 catalog:研究记录 + 已拉 repo + 资料 一张索引,别名召回。'先查本地'查的就是它。"
# [OMNI] why="用户痛点=找不到手头已有的(参考源/调研过的)。镜像 library.py 的 JSONL+倒排+别名模式;研究记录由 library.active_records 投影进来零重复存。别名种子把行话('claude code 源码')固化到对应 repo。"
# [OMNI] tags=research,catalog,discovery,aliases
"""统一本地资产 catalog。

真源 catalog.jsonl(append-only)+ 聚合视图 catalog.json(可 grep)。
rebuild(): 扫参考项目 repo/material + 投影 library 研究记录 → 全量重建(不靠人记,不馊)。
find(query): 确定性别名/子串/词重叠召回,零命中再便宜模型语义兜底。有就有、没有就没有。
"""

from __future__ import annotations

import json
from collections import Counter

from . import _scan, library
from ._llm import safe_json
from ._paths import CATALOG_JSON, CATALOG_JSONL, ensure_dirs

# 别名种子: 把"行话/口语"固化到对应资产(召回命脉)。key = repo 名 或 id 尾段。
ALIAS_SEED: dict[str, list[str]] = {
    "claude-code-analysis": [
        "claude code 源码", "claude code 真源", "claude code 内部实现", "claude code source",
        "anthropic claude cli 源码", "cc 源码", "claudecode 源码",
    ],
    "codex": ["codex 源码", "openai codex", "codex cli 源码"],
    "claudecodeui": ["claude code ui", "cc ui", "claudecodeui 源码"],
    "gemini-cli": ["gemini cli 源码", "google gemini cli"],
    "aider": ["aider 源码"],
    "repomix": ["repomix 源码", "仓库打包工具"],
}


def _norm(s: str) -> str:
    return library.normalize_topic(s or "")


def _tokens(s: str) -> list[str]:
    """中英混排分词: 连续拉丁数字一组、连续中文一组。让 'codex源码' 这种粘连查询也能切出 'codex'。"""
    import re
    return re.findall(r"[a-z0-9]+|[一-鿿]+", _norm(s))


# 通用填充词: 这些词在别名种子('X 源码'/'X cli')里到处出现,词重叠匹配时滤掉,
# 否则查 'codex源码' 会因 '源码' 命中所有带 '源码' 别名的资产(aider/gemini…)。
_FILLER_TOKENS = {"源码", "源代码", "代码", "源", "项目", "工具", "库", "仓库", "的"}


def _match_tokens(s: str) -> list[str]:
    return [t for t in _tokens(s) if len(t) >= 2 and t not in _FILLER_TOKENS]


def _read_lines() -> list[dict]:
    if not CATALOG_JSONL.is_file():
        return []
    out: list[dict] = []
    for line in CATALOG_JSONL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def fold() -> dict[str, dict]:
    folded: dict[str, dict] = {}
    for it in _read_lines():
        iid = it.get("id")
        if iid:
            folded[iid] = it
    return folded


def active_items() -> list[dict]:
    return [it for it in fold().values() if it.get("status") != "deleted"]


def _apply_alias_seed(item: dict) -> dict:
    name = item.get("name", "")
    iid = item.get("id", "")
    extra: list[str] = []
    for key, phrases in ALIAS_SEED.items():
        # 按完整段匹配(name 等于 key、id 是 repo:key、或 id 以 /key 结尾);
        # 去掉裸 iid.endswith(key)——它会误命中 repo:my-codex 这种任意后缀。
        if key == name or iid == f"repo:{key}" or iid.endswith(f"/{key}"):
            extra += phrases
    if extra:
        item["aliases"] = sorted(set((item.get("aliases") or []) + extra))
    return item


def _record_items() -> list[dict]:
    """把统一研究库的 active 记录投影成 catalog 条目(零重复存储)。"""
    out: list[dict] = []
    for r in library.active_records():
        out.append({
            "id": r.get("record_id", ""), "kind": "research_record",
            "name": r.get("topic", ""), "path": "",
            "description": (r.get("summary") or "")[:300],
            "aliases": sorted(set((r.get("aliases") or []) + (r.get("keywords") or []))),
            "source_url": "", "tags": ["research_record"],
            "status": r.get("status", "active"), "indexed_at": library.now_iso(),
        })
    return out


def rebuild(root: str | None = None) -> dict:
    """全量重建: 扫参考项目 + 投影研究记录 → 覆盖写 catalog.jsonl(确定性、不馊)。"""
    ensure_dirs()
    items: list[dict] = []
    for it in _scan.scan_reference_repos(root):
        it = _apply_alias_seed({**it, "status": "active", "indexed_at": library.now_iso()})
        items.append(it)
    items += _record_items()
    with CATALOG_JSONL.open("w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    _rebuild_catalog_json()
    return {"total": len(items), "counts": dict(Counter(it["kind"] for it in items))}


def upsert_item(item: dict) -> dict:
    """增量加一条(如落库后投影单条研究记录),append + fold 最新胜。"""
    ensure_dirs()
    item = _apply_alias_seed({**item})
    item.setdefault("status", "active")
    item["indexed_at"] = library.now_iso()
    with CATALOG_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
    _rebuild_catalog_json()
    return item


def _rebuild_catalog_json() -> None:
    items = active_items()
    by_alias: dict[str, list[str]] = {}  # 一别名可指多资产(如 codex 同时有顶层与 agents/codex),用 list 不丢
    by_keyword: dict[str, list[str]] = {}
    for it in items:
        for a in it.get("aliases") or []:
            lst = by_alias.setdefault(_norm(a), [])
            if it["id"] not in lst:
                lst.append(it["id"])
        for kw in it.get("tags") or []:
            lst = by_keyword.setdefault(str(kw).lower(), [])
            if it["id"] not in lst:
                lst.append(it["id"])
    CATALOG_JSON.write_text(
        json.dumps({"items": items, "by_alias": by_alias, "by_keyword": by_keyword},
                   ensure_ascii=False, indent=2), encoding="utf-8")


def find(query: str, allow_semantic: bool = True) -> list[dict]:
    """查本地有没有 query 指的资产。有就返条目列表,没有就空列表(别臆造)。"""
    items = {it["id"]: it for it in active_items()}
    q = _norm(query)
    if not q:
        return []
    hits: dict[str, dict] = {}

    # 1 别名精确
    for it in items.values():
        if any(_norm(a) == q for a in (it.get("aliases") or [])):
            hits[it["id"]] = it
    # 2 子串(name/aliases/description)
    if not hits:
        for it in items.values():
            hay = _norm(f"{it.get('name','')} {' '.join(it.get('aliases') or [])} {it.get('description','')}")
            if q in hay:
                hits[it["id"]] = it
    # 3 词重叠(任一**有区分度**的 query 词命中 name/aliases;滤掉 '源码' 这类填充词避免全中)
    if not hits:
        toks = _match_tokens(q)
        for it in items.values():
            hay = _norm(f"{it.get('name','')} {' '.join(it.get('aliases') or [])}")
            if any(t in hay for t in toks):
                hits[it["id"]] = it
    # 4 语义兜底(便宜模型,宁缺毋滥)。候选先按词重叠相关度排序再截断,避免资产量大时尾部资产进不了候选。
    if not hits and allow_semantic:
        qtoks = set(_tokens(q))

        def _rel(it: dict) -> int:
            itoks = set(_tokens(f"{it.get('name','')} {' '.join(it.get('aliases') or [])}"))
            return len(qtoks & itoks)

        ranked = sorted(items.values(), key=_rel, reverse=True)
        cand = [{"id": it["id"], "name": it.get("name", ""), "aliases": it.get("aliases") or []}
                for it in ranked][:80]
        res = safe_json(
            "从候选本地资产里挑出与用户查询指同一个东西的 id(可能 0 个)。只返回确实匹配的,宁缺毋滥,不确定就别返。",
            {"query": query, "candidates": cand},
            {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "string"}}},
             "required": ["ids"]},
            caller="research.refs.find", default={"ids": []},
        )
        for i in (res.get("ids") or []):
            if i in items:
                hits[i] = items[i]
    return list(hits.values())
