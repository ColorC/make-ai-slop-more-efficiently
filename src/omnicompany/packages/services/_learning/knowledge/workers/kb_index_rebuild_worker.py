# [OMNI] origin=claude-code domain=omnicompany/knowledge ts=2026-04-21T00:00:00Z type=router
# [OMNI] material_id="material:learning.knowledge.index_rebuild_worker.execution.py"
"""KBIndexRebuildWorker — OmniKB 索引重建 (Stage 3 独立文件).

Worker 协议:
  FORMAT_IN  = kb.rebuild_request
  FORMAT_OUT = kb.index_stats

重建 .omni/knowledge_index.json 索引文件。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from omnicompany.packages.services._learning.knowledge import rebuild_index
from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import _project_root


class KBIndexRebuildWorker(Worker):
    """重建 .omni/knowledge_index.json, 返回新 stats。

    input_data 无必填字段。通常被 KBWriteWorker 之后触发, 或用户手动跑。
    """

    DESCRIPTION = "OmniKB 索引重建 Worker: 全量扫描 → 落盘 .omni/knowledge_index.json"
    FORMAT_IN = "kb.rebuild_request"
    FORMAT_OUT = "kb.index_stats"

    def __init__(self, *, project_root: Path | None = None) -> None:
        self._project_root = project_root or _project_root()

    def run(self, input_data: Any) -> Verdict:
        try:
            index = rebuild_index(self._project_root)
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"rebuild 失败: {e}",
            )
        stats = index.stats()
        return Verdict(
            kind=VerdictKind.PASS,
            output={"stats": stats, "path": str(self._project_root / ".omni" / "knowledge_index.json")},
            confidence=1.0,
            diagnosis=f"index rebuilt: total={stats['total']}",
            granted_tags=["domain.knowledge", "stage.indexed"],
        )
