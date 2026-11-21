# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-04-18
# [OMNI] material_id="material:core.agent.routers.result_extractor.router.py"
"""ExtractResultRouter — agent.result-request → agent.result-final

把 Agent Loop 收尾时的 final_text + messages 转成业务产物 Verdict。

默认行为：直接把 final_text 包成 PASS Verdict，output={text, turn_count, stop_reason}。

业务子类通常 override `extract()` 或继承此 Router 实现自己的产物提取逻辑
（如业务 ExtractResultRouter 可从 messages 里找结构化提交工具调用、
找 Markdown 产物等）。
"""

from __future__ import annotations

from typing import Any, ClassVar

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router
from omnicompany.packages.services._core.agent._bus import (
    emit_router_input,
    emit_router_output,
)


class ExtractResultRouter(Router):
    """Agent Loop 收尾产物提取 Router。

    FORMAT_IN  = agent.result-request
    FORMAT_OUT = agent.result-final
    """

    DESCRIPTION: ClassVar[str] = "Agent Loop 收尾：把 final_text + messages 提取为业务 Verdict"
    FORMAT_IN: ClassVar[str] = "agent.result-request"
    FORMAT_OUT: ClassVar[str] = "agent.result-final"
    INPUT_KEYS: ClassVar[list[str]] = ["messages", "final_text", "turn_count"]
    OUTPUT_KEYS: ClassVar[list[str]] = ["verdict_kind"]

    ROUTER_NAME: ClassVar[str] = "extract_result"

    def __init__(self, *, bus: Any | None = None):
        if bus is None:
            raise RuntimeError(
                "ExtractResultRouter requires an EventBus (bus=...). "
                "Silent no-op emit loses final verdict trail."
            )
        self._bus = bus

    # ── 子类 override 点 ────────────────────────────────────────────

    def extract(
        self,
        *,
        final_text: str,
        messages: list[dict],
        turn_count: int,
        stop_reason: str,
    ) -> Verdict:
        """默认：把 final_text 包成 PASS Verdict。子类通常 override 做业务提取。"""
        if not final_text.strip() and turn_count == 0:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": "empty_final_text_and_no_turns"},
                diagnosis="Agent returned no text and made no turns",
            )

        kind = VerdictKind.PASS
        diagnosis = ""
        if stop_reason == "max_turns":
            kind = VerdictKind.PARTIAL
            diagnosis = f"Budget exhausted: {turn_count} turns used"

        return Verdict(
            kind=kind,
            output={
                "text": final_text,
                "turn_count": turn_count,
                "stop_reason": stop_reason,
            },
            diagnosis=diagnosis,
        )

    # ── Router 入口 ────────────────────────────────────────────────

    async def run(self, input_data: Any) -> Verdict:
        pre = self.validate_input(input_data)
        if pre is not None:
            return pre

        trace_id = input_data.get("trace_id", "")
        final_text = input_data.get("final_text", "")
        messages = input_data.get("messages", [])
        turn_count = input_data.get("turn_count", 0)
        stop_reason = input_data.get("stop_reason", "end_turn")

        await emit_router_input(
            self._bus,
            trace_id=trace_id,
            router_name=self.ROUTER_NAME,
            format_id=self.FORMAT_IN,
            data={
                "final_text_len": len(final_text),
                "messages_count": len(messages),
                "turn_count": turn_count,
                "stop_reason": stop_reason,
            },
        )

        verdict = self.extract(
            final_text=final_text,
            messages=messages,
            turn_count=turn_count,
            stop_reason=stop_reason,
        )

        # 把 trace_id 填进 output 以符合 agent.result-final schema
        if isinstance(verdict.output, dict):
            verdict.output.setdefault("trace_id", trace_id)
            verdict.output.setdefault("verdict_kind", verdict.kind.value)
            if verdict.diagnosis:
                verdict.output.setdefault("diagnosis", verdict.diagnosis)

        await emit_router_output(
            self._bus,
            trace_id=trace_id,
            router_name=self.ROUTER_NAME,
            format_id=self.FORMAT_OUT,
            data={
                "verdict_kind": verdict.kind.value,
                "diagnosis": verdict.diagnosis,
                "output_preview": str(verdict.output)[:500] if verdict.output else "",
            },
            verdict_kind=verdict.kind.value,
        )
        return verdict
