# [OMNI] origin=claude-code domain=services/_learning/gddecon ts=2026-06-27T00:00:00Z type=worker status=active
# [OMNI] material_id="material:services.learning.gddecon.workers.gap_worker.py"
"""GapReportWorker —— 事件型差距分析 worker。

消费 gddecon.deconstruction-request, 产出 gddecon.gap-report (sink): 对方面树每个方面
做应然↔实然↔差距盘点 + 落盘 <game>-差距.md。无现成方面树时先跑拆解。
脑子=统一 call_json (结构化), 不 fork agent (R-26)。
"""
from __future__ import annotations

from typing import Any, ClassVar

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


class GapReportWorker(Worker):
    DESCRIPTION: ClassVar[str] = (
        "对一款游戏的方面树做应然↔实然↔差距全局盘点, 产出差距报告并落盘 <game>-差距.md。"
        "脑子=统一 call_json 结构化产出。"
    )
    FORMAT_IN: ClassVar[str] = "gddecon.deconstruction-request"
    FORMAT_OUT: ClassVar[str] = "gddecon.gap-report"

    async def run(self, input_data: Any) -> Verdict:
        cfg: dict = {}
        if isinstance(input_data, dict):
            inner = input_data.get(self.FORMAT_IN)
            cfg = dict(inner) if isinstance(inner, dict) else dict(input_data)
        if not cfg.get("game_name"):
            return Verdict(kind=VerdictKind.FAIL, confidence=1.0,
                           output={"error": "missing game_name"},
                           diagnosis="[gddecon-gap] 缺 game_name。")

        from omnicompany.packages.services._learning.gddecon.pipeline import _run_gap_async
        out = await _run_gap_async(cfg)
        rep = out.get("gap_report")
        if out.get("ok") and isinstance(rep, dict) and rep.get("gaps"):
            payload = dict(rep)
            payload["doc_path"] = out.get("doc_path")
            return Verdict(
                kind=VerdictKind.PASS, confidence=0.0, output=payload,
                granted_tags=["domain.gddecon", "stage.gap-report"],
                diagnosis=f"[gddecon-gap] {out.get('game_name')}: {out.get('gap_count')} 差距 -> {out.get('doc_path')}",
            )
        return Verdict(kind=VerdictKind.FAIL, confidence=0.0,
                       output={"error": out.get("error") or "no_gaps"},
                       diagnosis=f"[gddecon-gap] 未产出差距报告: {out.get('error')}")


__all__ = ["GapReportWorker"]
