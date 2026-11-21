# [OMNI] origin=ai-ide domain=research ts=2026-06-14T00:00:00Z type=package status=active
# [OMNI] summary="research domain 包根。通用「公开调研」管线:拆题→联网查证带来源→综合成不打分报告→统一研究库(累积+查重)。"
# [OMNI] why="用户 2026-06-14 新开「公开调研」线,核心=一条通用性价比研究管线 + 统一研究库(开跑前查重,绝不重复调研)。非 demogame 自有管线内化进 omnicompany。"
# [OMNI] tags=research,domain,pipeline,public-research
"""research domain — 通用「公开调研」管线在 omnicompany 内的家。

边界: 管线(代码/prompt)在本 domain, 产物(统一研究库/runs/reports)在 data/domains/research。
苦力 worker 走统一 LLM 网关的性价比模型; 联网检索复用 services/_core/agent 的 WebSearch/WebFetch。
详见 DESIGN.md。

任意 agent 查本地的两个稳定入口(也对应 `omni refs find` / `omni research library`):
    from omnicompany.packages.domains.research import lookup_or_none, find_local
"""

from __future__ import annotations

from typing import Any


def lookup_or_none(topic: str) -> dict | None:
    """查'本地有没有调研过这题' —— 有返 record(dict),没有返 None。"""
    from .library import lookup_by_topic, normalize_topic
    return lookup_by_topic(normalize_topic(topic))


def find_local(query: str, allow_semantic: bool = True) -> list[dict]:
    """查'本地有没有这资产'(研究记录 + 已拉 repo + 资料) —— 返命中列表(可空=确实没有)。"""
    from . import catalog
    return catalog.find(query, allow_semantic=allow_semantic)


__all__ = ["lookup_or_none", "find_local"]

