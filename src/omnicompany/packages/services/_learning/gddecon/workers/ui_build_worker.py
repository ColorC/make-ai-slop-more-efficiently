# [OMNI] origin=claude-code domain=services/_learning/gddecon ts=2026-06-27T00:00:00Z type=worker status=active
# [OMNI] material_id="material:services.learning.gddecon.workers.ui_build_worker.py"
"""UiBuildWorker —— 事件型「建立UI设计稿（按真后端）」worker。

读真实后端代码 → call_json 产出 complete-expression 战斗屏界面设计稿（HTML+缩放查看器）。
脑子=统一 call_json, 不 fork agent (R-26)。
"""
from __future__ import annotations

from typing import Any, ClassVar

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


class UiBuildWorker(Worker):
    DESCRIPTION: ClassVar[str] = (
        "按真实后端逻辑产出一版 complete-expression 界面设计稿（把后端所有状态+操作完整暴露），"
        "落盘 HTML+缩放查看器。脑子=统一 call_json。"
    )
    FORMAT_IN: ClassVar[str] = "gddecon.deconstruction-request"
    FORMAT_OUT: ClassVar[str] = "gddecon.ui-design"

    async def run(self, input_data: Any) -> Verdict:
        cfg: dict = {}
        if isinstance(input_data, dict):
            inner = input_data.get(self.FORMAT_IN)
            cfg = dict(inner) if isinstance(inner, dict) else dict(input_data)
        if not cfg.get("game_name"):
            return Verdict(kind=VerdictKind.FAIL, confidence=1.0,
                           output={"error": "missing game_name"},
                           diagnosis="[gddecon-ui-build] 缺 game_name。")

        from omnicompany.packages.services._learning.gddecon.pipeline import _run_ui_build_async
        out = await _run_ui_build_async(cfg)
        des = out.get("ui_design")
        if out.get("ok") and isinstance(des, dict) and des.get("body_html"):
            payload = {k: v for k, v in des.items() if k != "body_html"}  # body_html 大, 不进事件 payload
            payload["doc_path"] = out.get("doc_path")
            payload["game_name"] = out.get("game_name")
            return Verdict(
                kind=VerdictKind.PASS, confidence=0.0, output=payload,
                granted_tags=["domain.gddecon", "stage.ui-design"],
                diagnosis=f"[gddecon-ui-build] {out.get('game_name')}: 设计稿 -> {out.get('doc_path')}",
            )
        return Verdict(kind=VerdictKind.FAIL, confidence=0.0,
                       output={"error": out.get("error") or "no_design"},
                       diagnosis=f"[gddecon-ui-build] 未产出设计稿: {out.get('error')}")


__all__ = ["UiBuildWorker"]
