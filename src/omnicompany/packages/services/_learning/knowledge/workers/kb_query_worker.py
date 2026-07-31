# [OMNI] origin=claude-code domain=omnicompany/knowledge ts=2026-04-21T00:00:00Z type=router
# [OMNI] material_id="material:learning.knowledge.multi_dimension_query.worker.py"
"""KBQueryWorker — OmniKB 多维度查询 (Stage 3 独立文件).

Worker 协议:
  FORMAT_IN  = kb.query
  FORMAT_OUT = kb.query_result
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from omnicompany.packages.services._learning.knowledge import (
    KnowledgeEntry,
    load_or_rebuild,
)
from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import _project_root


class KBQueryWorker(Worker):
    """查询 OmniKB 条目。

    input_data 支持 5 种查询形态 (至少提供一个):
      - {"id": "kb.arch.bus_unification"}            按 id 精确查
      - {"types": ["karch", "kdec"]}                  按类型枚举
      - {"tags": ["domain.absorption"]}               按标签 AND
      - {"domain": "absorption"}                      按 domain 快捷查
      - {"scope": "omnicompany"}                      按 scope (karch/krepo)
      - {"id_prefix": "kb.repo."}                     id 前缀匹配
      - {"text": "context compression"}               模糊文本搜索

    可与 maturity 组合:  {"maturity": "stable", "types": ["karch"]}
    """

    DESCRIPTION = "OmniKB 查询 Worker: 按 id/tag/domain/scope/type/text 多维度检索"
    FORMAT_IN = "kb.query"
    FORMAT_OUT = "kb.query_result"

    def __init__(self, *, project_root: Path | None = None) -> None:
        self._project_root = project_root or _project_root()

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis="KBQueryWorker 需要 dict 输入",
            )

        index = load_or_rebuild(self._project_root)

        kb_id = input_data.get("id")
        if kb_id:
            entry = index.get(kb_id)
            results = [entry] if entry else []
            return self._ok(results, 1 if entry else 0)

        text = input_data.get("text")
        if text:
            limit = int(input_data.get("limit", 20))
            results = index.text_search(text, limit=limit)
            return self._ok(results, len(results))

        results = index.find(
            types=input_data.get("types"),
            tags=input_data.get("tags"),
            domain=input_data.get("domain"),
            scope=input_data.get("scope"),
            maturity=input_data.get("maturity"),
            id_prefix=input_data.get("id_prefix"),
        )
        return self._ok(results, len(results))

    def _ok(self, entries: list[KnowledgeEntry], count: int) -> Verdict:
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "entries": [e.model_dump() for e in entries],
                "count": count,
            },
            confidence=1.0,
            diagnosis=f"returned {count} entries",
        )
