# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-04-18
# [OMNI] material_id="material:core.agent.routers.prompt_assembler.router.py"
"""PromptBuilderRouter — agent.prompt-request → agent.prompt-built

把业务 input_data + node_prompt_template 装配为首轮 LLM 会话（system_prompt +
initial_messages）。业务子类可通过两种方式定制：

方案 A（推荐）：通过 `template` 参数传自带占位符的 prompt（`{prefab_name}` 等），
PromptBuilder 用 str.format_map(DefaultDict) 填充，未知占位符保持原样。

方案 B（更强定制）：子类继承 PromptBuilderRouter，override `build_initial_messages()`
决定首轮 user message 内容（如需要多 block、tool_result prefill 等复杂情况）。

默认行为：
- system_prompt = template 填充后（或原样）
- initial_messages = [{role: user, content: <input_data.task 或整体 JSON 字符串>}]
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router
from omnicompany.packages.services._core.agent._bus import (
    emit_router_input,
    emit_router_output,
)
from omnicompany.packages.services._core.agent.context_fork import (
    inherit_context_fork_messages,
)


class _PermissiveDict(dict):
    """str.format_map 的 default — 缺 key 时保留原占位符 `{name}` 不炸。"""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class PromptBuilderRouter(Router):
    """装配首轮 LLM 会话。

    FORMAT_IN  = agent.prompt-request
    FORMAT_OUT = agent.prompt-built
    """

    DESCRIPTION: ClassVar[str] = "装配首轮 LLM 会话：填充系统 prompt + 构造 user message"
    FORMAT_IN: ClassVar[str] = "agent.prompt-request"
    FORMAT_OUT: ClassVar[str] = "agent.prompt-built"
    INPUT_KEYS: ClassVar[list[str]] = ["input_data", "trace_id"]
    OUTPUT_KEYS: ClassVar[list[str]] = ["system_prompt", "initial_messages"]

    ROUTER_NAME: ClassVar[str] = "prompt_builder"

    def __init__(self, *, template: str = "", bus: Any | None = None):
        if bus is None:
            raise RuntimeError(
                "PromptBuilderRouter requires an EventBus (bus=...). "
                "Pass bus=SQLiteBus(...) or MemoryBus() — silent no-op emit loses Format trail."
            )
        self._template = template
        self._bus = bus

    # ── 子类 override 点 ────────────────────────────────────────────

    def render_system_prompt(self, input_data: dict) -> str:
        """默认用 DefaultDict 填模板。子类可改成完全自定义逻辑。"""
        if not self._template:
            return ""
        try:
            return self._template.format_map(_PermissiveDict(input_data))
        except Exception:
            return self._template

    def build_initial_messages(self, input_data: dict) -> list[dict]:
        """默认把 input_data 序列化为一条 user 消息。子类通常会 override。"""
        task = input_data.get("task") or input_data.get("instruction")
        if task:
            return [{"role": "user", "content": str(task)}]
        # 回退：把整体 input_data 渲染成 JSON 文本
        return [{
            "role": "user",
            "content": json.dumps(input_data, ensure_ascii=False, indent=2, default=str),
        }]

    # ── Router 入口 ────────────────────────────────────────────────

    async def run(self, input_data: Any) -> Verdict:
        # 验前
        pre = self.validate_input(input_data)
        if pre is not None:
            return pre

        trace_id = input_data.get("trace_id", "")
        # 发 input 事件
        await emit_router_input(
            self._bus,
            trace_id=trace_id,
            router_name=self.ROUTER_NAME,
            format_id=self.FORMAT_IN,
            data=input_data,
        )

        biz_input = input_data.get("input_data", {}) or {}
        # 允许调用方显式覆盖 template
        if "node_prompt_template" in input_data and input_data["node_prompt_template"]:
            old = self._template
            self._template = input_data["node_prompt_template"]
            try:
                system_prompt = self.render_system_prompt(biz_input)
            finally:
                self._template = old
        else:
            system_prompt = self.render_system_prompt(biz_input)

        initial_messages = inherit_context_fork_messages(
            biz_input,
            self.build_initial_messages(biz_input),
        )

        output = {
            "system_prompt": system_prompt,
            "initial_messages": initial_messages,
            "trace_id": trace_id,
        }
        verdict = Verdict(kind=VerdictKind.PASS, output=output)

        # 验后
        post = self.validate_output(verdict)
        if post is not None:
            verdict = post

        await emit_router_output(
            self._bus,
            trace_id=trace_id,
            router_name=self.ROUTER_NAME,
            format_id=self.FORMAT_OUT,
            data=verdict.output,
            verdict_kind=verdict.kind.value,
        )
        return verdict
