# [OMNI] origin=claude-code domain=services/_learning/gddecon ts=2026-06-28T00:00:00Z type=worker status=active
# [OMNI] material_id="material:services.learning.gddecon.workers.info_hierarchy_worker.py"
"""InfoHierarchyWorker —— 事件型「制定信息层级」worker。

读完整表达清单 + 游戏核心循环 → call_json 按玩家注意力/行为频次排出信息层级表
（常驻/揭示 + 揭示即操作），落盘 .md/.json。脑子=统一 call_json，不 fork agent (R-26)。
"""
from __future__ import annotations

from typing import Any, ClassVar

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


class InfoHierarchyWorker(Worker):
    DESCRIPTION: ClassVar[str] = (
        "把一屏的完整信息清单按玩家注意力/行为频次排成信息层级表（常驻/揭示 + 揭示即操作），"
        "落盘 .md。脑子=统一 call_json。"
    )
    FORMAT_IN: ClassVar[str] = "gddecon.deconstruction-request"
    FORMAT_OUT: ClassVar[str] = "gddecon.info-hierarchy"

    async def run(self, input_data: Any) -> Verdict:
        cfg: dict = {}
        if isinstance(input_data, dict):
            inner = input_data.get(self.FORMAT_IN)
            cfg = dict(inner) if isinstance(inner, dict) else dict(input_data)
        if not cfg.get("game_name"):
            return Verdict(kind=VerdictKind.FAIL, confidence=1.0,
                           output={"error": "missing game_name"},
                           diagnosis="[gddecon-info-hier] 缺 game_name。")

        from omnicompany.packages.services._learning.gddecon.pipeline import _run_info_hierarchy_async
        out = await _run_info_hierarchy_async(cfg)
        data = out.get("info_hierarchy")
        if out.get("ok") and isinstance(data, dict) and data.get("tiers"):
            payload = {
                "game_name": out.get("game_name"),
                "doc_path": out.get("doc_path"),
                "tier_count": out.get("tier_count"),
                "reveal_op_count": out.get("reveal_op_count"),
            }
            return Verdict(
                kind=VerdictKind.PASS, confidence=0.0, output=payload,
                granted_tags=["domain.gddecon", "stage.info-hierarchy"],
                diagnosis=f"[gddecon-info-hier] {out.get('game_name')}: 信息层级 -> {out.get('doc_path')}",
            )
        return Verdict(kind=VerdictKind.FAIL, confidence=0.0,
                       output={"error": out.get("error") or "no_hierarchy"},
                       diagnosis=f"[gddecon-info-hier] 未产出信息层级: {out.get('error')}")


__all__ = ["InfoHierarchyWorker"]
