# [OMNI] origin=claude-code domain=services/_learning/gddecon ts=2026-06-28T00:00:00Z type=worker status=active
# [OMNI] material_id="material:services.learning.gddecon.workers.interaction_model_worker.py"
"""InteractionModelWorker —— 事件型「制定操作交互模型」worker。

读操作全集 + 游戏核心循环 → call_json 逐操作出交互规范（频次×手势/反馈/确认/相位/选择），
落盘 .md/.json。脑子=统一 call_json，不 fork agent (R-26)。
"""
from __future__ import annotations

from typing import Any, ClassVar

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


class InteractionModelWorker(Worker):
    DESCRIPTION: ClassVar[str] = (
        "把一屏操作全集排成操作交互模型（频次×手势/反馈/确认安全/可用相位/选择模型，界面操作维度），"
        "落盘 .md。脑子=统一 call_json。"
    )
    FORMAT_IN: ClassVar[str] = "gddecon.deconstruction-request"
    FORMAT_OUT: ClassVar[str] = "gddecon.interaction-model"

    async def run(self, input_data: Any) -> Verdict:
        cfg: dict = {}
        if isinstance(input_data, dict):
            inner = input_data.get(self.FORMAT_IN)
            cfg = dict(inner) if isinstance(inner, dict) else dict(input_data)
        if not cfg.get("game_name"):
            return Verdict(kind=VerdictKind.FAIL, confidence=1.0,
                           output={"error": "missing game_name"},
                           diagnosis="[gddecon-interaction] 缺 game_name。")

        from omnicompany.packages.services._learning.gddecon.pipeline import _run_interaction_model_async
        out = await _run_interaction_model_async(cfg)
        data = out.get("interaction_model")
        if out.get("ok") and isinstance(data, dict) and data.get("operations"):
            payload = {
                "game_name": out.get("game_name"),
                "doc_path": out.get("doc_path"),
                "op_count": out.get("op_count"),
            }
            return Verdict(
                kind=VerdictKind.PASS, confidence=0.0, output=payload,
                granted_tags=["domain.gddecon", "stage.interaction-model"],
                diagnosis=f"[gddecon-interaction] {out.get('game_name')}: 交互模型 -> {out.get('doc_path')}",
            )
        return Verdict(kind=VerdictKind.FAIL, confidence=0.0,
                       output={"error": out.get("error") or "no_model"},
                       diagnosis=f"[gddecon-interaction] 未产出交互模型: {out.get('error')}")


__all__ = ["InteractionModelWorker"]
