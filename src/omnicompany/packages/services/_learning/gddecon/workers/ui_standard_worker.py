# [OMNI] origin=claude-code domain=services/_learning/gddecon ts=2026-06-27T00:00:00Z type=worker status=active
# [OMNI] material_id="material:services.learning.gddecon.workers.ui_standard_worker.py"
"""UiStandardWorker —— 事件型「跟进UI标准」worker。

消费 gddecon.deconstruction-request, 产出 gddecon.ui-standard (sink): 从 UI 规格 +
方面树 UI 簇制定可检查的 UI 标准库（信息/交互两类）+ 落盘 <game>-UI标准.md。
脑子=统一 call_json, 不 fork agent (R-26)。
"""
from __future__ import annotations

from typing import Any, ClassVar

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


class UiStandardWorker(Worker):
    DESCRIPTION: ClassVar[str] = (
        "从 UI 设计规格 + 方面树 UI 簇制定可检查的 UI 标准库（信息/交互两类，每条带证据与检查法），"
        "落盘 <game>-UI标准.md。脑子=统一 call_json。"
    )
    FORMAT_IN: ClassVar[str] = "gddecon.deconstruction-request"
    FORMAT_OUT: ClassVar[str] = "gddecon.ui-standard"

    async def run(self, input_data: Any) -> Verdict:
        cfg: dict = {}
        if isinstance(input_data, dict):
            inner = input_data.get(self.FORMAT_IN)
            cfg = dict(inner) if isinstance(inner, dict) else dict(input_data)
        if not cfg.get("game_name"):
            return Verdict(kind=VerdictKind.FAIL, confidence=1.0,
                           output={"error": "missing game_name"},
                           diagnosis="[gddecon-ui-std] 缺 game_name。")

        from omnicompany.packages.services._learning.gddecon.pipeline import _run_ui_standard_async
        out = await _run_ui_standard_async(cfg)
        std = out.get("ui_standard")
        if out.get("ok") and isinstance(std, dict) and std.get("rules"):
            payload = dict(std)
            payload["doc_path"] = out.get("doc_path")
            return Verdict(
                kind=VerdictKind.PASS, confidence=0.0, output=payload,
                granted_tags=["domain.gddecon", "stage.ui-standard"],
                diagnosis=(f"[gddecon-ui-std] {out.get('game_name')}: {out.get('rule_count')} 规则"
                           f"(信息{out.get('n_info')}/交互{out.get('n_act')}) -> {out.get('doc_path')}"),
            )
        return Verdict(kind=VerdictKind.FAIL, confidence=0.0,
                       output={"error": out.get("error") or "no_rules"},
                       diagnosis=f"[gddecon-ui-std] 未产出 UI 标准: {out.get('error')}")


__all__ = ["UiStandardWorker"]
