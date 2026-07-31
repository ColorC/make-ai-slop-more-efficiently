# [OMNI] origin=claude-code domain=omnicompany/knowledge ts=2026-04-21T00:00:00Z type=router
# [OMNI] material_id="material:learning.knowledge.natural_language_locator.worker.py"
"""KBLocateWorker — OmniKB 自然语言定位 + code_anchors 聚合 (Stage 3 独立文件).

Worker 协议:
  FORMAT_IN  = kb.locate_query
  FORMAT_OUT = kb.locate_result

"Q2: OmniCompany 某功能对应在哪" 场景的主入口。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from omnicompany.packages.services._learning.knowledge import load_or_rebuild
from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import _project_root


class KBLocateWorker(Worker):
    """自然语言定位: 给定问题, 返回相关 entries + 代码锚点。

    input_data = {
        "query": "哪里处理 LLM 上下文压缩",
        "limit": 5,             # 可选
        "types": ["karch"],     # 可选, 限定类型
    }

    返回:
      output = {
          "entries": [...],
          "code_anchors": [    # 所有匹配到的 karch 的 code_anchors 聚合去重
              "src/omnicompany/runtime/llm/compression_summary.py:L1-L200",
              ...
          ],
          "count": N,
      }

    本质上是 text_search 的一个便利封装, 但多出了 code_anchors 提取。
    """

    DESCRIPTION = "OmniKB 定位 Worker: 文本搜索 + 聚合 code_anchors"
    FORMAT_IN = "kb.locate_query"
    FORMAT_OUT = "kb.locate_result"

    def __init__(self, *, project_root: Path | None = None) -> None:
        self._project_root = project_root or _project_root()

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict) or not input_data.get("query"):
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis="KBLocateWorker 需要 input.query 字段",
            )

        query = input_data["query"]
        limit = int(input_data.get("limit", 5))
        types_filter = input_data.get("types")

        index = load_or_rebuild(self._project_root)
        results = index.text_search(query, limit=limit * 2)

        if types_filter:
            results = [e for e in results if e.omnikb_type in types_filter]

        results = results[:limit]

        anchors: list[str] = []
        seen_anchors: set[str] = set()
        for entry in results:
            entry_anchors = getattr(entry, "code_anchors", None) or []
            for a in entry_anchors:
                if a not in seen_anchors:
                    seen_anchors.add(a)
                    anchors.append(a)

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "query": query,
                "entries": [e.model_dump() for e in results],
                "code_anchors": anchors,
                "count": len(results),
            },
            confidence=1.0,
            diagnosis=f"found {len(results)} entries, {len(anchors)} code anchors",
        )
