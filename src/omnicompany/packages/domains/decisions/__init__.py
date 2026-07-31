# [OMNI] origin=ai-ide domain=decisions ts=2026-06-17T00:00:00Z type=package status=active
# [OMNI] summary="decisions domain 包根。主线=决策记录(非提取):一套源无关的统一决策契约 + 统一决策库,把对话/collab platform/策划文档/札记里的决策汇成一棵可搜索的决策树。"
# [OMNI] why="用户 2026-06-17 新开「决策记录」线。LLM 把记录决策的成本打到 ROI 为正,个人决策树沉淀成重点;决策有多源多落地面,需统一契约(符号统一)。非 demogame 自有内容内化进 omnicompany。"
# [OMNI] tags=decisions,domain,decision-record,schema
"""decisions domain —— 「决策记录」在 omnicompany 内的家。

边界:
  - 契约(schema/管线代码)在本 domain;记录产物(统一决策库/runs)在 data/domains/decisions。
  - omnicompany 是实验室+个人决策记录的收集管理处;管线成熟后可导出成
    claude code 标准 workflow js,落进对应执行端工程(如 demogame),且执行端绝不反向依赖本仓。

已落地:schema 三件套(formats.py)+ 统一库(library.py)+ 索引召回(catalog.py)+ CLI(omni decisions)。
待加:抽取管线(对话源先行)/ collab platform消息源 / demogame 落地面渲染(见 DESIGN.md)。
"""

from __future__ import annotations

from typing import Any

from . import catalog, library


def record(kind: str, statement: str, **fields: Any) -> dict:
    """手记一条决策/猜想/评论 → 落统一库 → 刷索引。返回落库后的完整记录。

    kind ∈ decision|belief|comment。其余字段(anchor/origin/decision_space/links…)按需传,见 formats.py。
    """
    rec, _ = library.upsert(library.new_record(kind, statement, **fields))
    catalog.rebuild_index()
    return rec


def lookup_or_none(query: str) -> dict | None:
    """先查库里有没有记过这件事。命中返第一条记录,没有返 None(别臆造)。"""
    hits = catalog.find(query)
    return hits[0] if hits else None


def find_local(query: str) -> list[dict]:
    """查库里所有与 query 相关的决策记录(空列表=没有)。"""
    return catalog.find(query)


__all__ = ["record", "lookup_or_none", "find_local", "library", "catalog"]
