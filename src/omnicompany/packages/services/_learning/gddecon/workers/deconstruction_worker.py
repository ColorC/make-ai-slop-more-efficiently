# [OMNI] origin=claude-code domain=services/_learning/gddecon ts=2026-06-27T00:00:00Z type=worker status=active
# [OMNI] material_id="material:services.learning.gddecon.workers.deconstruction_worker.py"
"""DeconstructionWorker —— 事件型引擎下的薄包装 worker。

把 gddecon.deconstruction-request 交给统一编排器 (pipeline._run_async, 内部用统一
run_json_agent 跑只读拆解 agent), 产出 gddecon.aspect-tree (sink) + 落盘 .md。

async run —— MaterialDispatcher._invoke_worker_async 原生 await; 编排器内部自管 bus,
故本 worker 不需要注入 bus (避开 ConfigurableAgent 的 bus 装配问题)。
不在 run() 里手搓 ReAct / 直调 LLM —— 脑子全在统一 run_json_agent (R-26 合规)。
"""
from __future__ import annotations

from typing import Any, ClassVar

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


class DeconstructionWorker(Worker):
    DESCRIPTION: ClassVar[str] = (
        "读一款游戏的设计源 + 当前 build, 用方面发现法产出方面树 (设计应被拆成哪些维度), "
        "并把树确定性渲染落盘成 .md。脑子=统一 run_json_agent 只读 agent。"
    )
    FORMAT_IN: ClassVar[str] = "gddecon.deconstruction-request"
    FORMAT_OUT: ClassVar[str] = "gddecon.aspect-tree"

    async def run(self, input_data: Any) -> Verdict:
        # MaterialDispatcher 传 {FORMAT_IN: payload} (按 material id 嵌套, 支持 fan-in);
        # 直接调用时可能传平铺 payload。两者都兼容。
        cfg: dict = {}
        if isinstance(input_data, dict):
            inner = input_data.get(self.FORMAT_IN)
            cfg = dict(inner) if isinstance(inner, dict) else dict(input_data)
        if not cfg.get("game_name"):
            return Verdict(
                kind=VerdictKind.FAIL,
                confidence=1.0,
                output={"error": "missing game_name"},
                diagnosis="[gddecon] deconstruction-request 缺 game_name, 无法拆解。",
            )

        from omnicompany.packages.services._learning.gddecon.pipeline import _run_async
        out = await _run_async(cfg)

        tree = out.get("aspect_tree")
        if out.get("ok") and isinstance(tree, dict) and tree.get("aspects"):
            payload = dict(tree)              # gddecon.aspect-tree 平铺 payload (R-23)
            payload["doc_path"] = out.get("doc_path")
            return Verdict(
                kind=VerdictKind.PASS,
                confidence=0.0,               # SOFT: 经 LLM 拆解
                output=payload,
                granted_tags=["domain.gddecon", "stage.aspect-tree"],
                diagnosis=(
                    f"[gddecon] {out.get('game_name')}: 产出 {out.get('aspect_count')} 个方面 "
                    f"-> {out.get('doc_path')} (turns={out.get('turn_count')})"
                ),
            )

        return Verdict(
            kind=VerdictKind.FAIL,
            confidence=0.0,
            output={"error": out.get("error") or "no_aspect_tree", "session_id": out.get("session_id")},
            diagnosis=f"[gddecon] 未产出合法方面树: {out.get('error') or '(agent 无结构化输出)'}",
        )


__all__ = ["DeconstructionWorker"]
