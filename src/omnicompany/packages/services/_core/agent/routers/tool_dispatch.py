# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-04-18
# [OMNI] material_id="material:core.agent.routers.tool_dispatch.router.py"
"""ToolDispatchRouter — agent.tool-request → agent.tool-response

按 tool_name 分发到具体 SingleToolRouter 子类的 .run()。

职责：
1. 维护 tool_name → SingleToolRouter 实例 的注册表
2. 收到 tool-request 时按 tool_name 找到对应 Router 并 await .run()
3. 工具不存在时返回 is_error=True 的 tool-response
4. 在 bus 上发 router.tool_dispatch.input/output（嵌套 tool_<name>.input/output）

设计说明：
- Dispatch 本身是薄路由器，真正的 tool 执行逻辑在 SingleToolRouter 子类里
- 权限检查的位置：本阶段先放在 dispatch（作为前置门），未来可拆到专门的 Router
- tools_spec 对外暴露给 LLMCallRouter（LLM 需要的工具规范由 dispatch 汇总）
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router
from omnicompany.packages.services._core.agent._bus import (
    emit_router_input,
    emit_router_output,
)
from omnicompany.packages.services._core.agent.routers.single_tool import SingleToolRouter

logger = logging.getLogger(__name__)


_NATIVE_TOOL_ALIASES = {
    "read": "read_file",
    "write": "write_file",
    "edit": "edit",
    "bash": "bash",
    "glob": "glob",
    "grep": "grep",
    "list": "list_dir",
    "list_dir": "list_dir",
    "finish": "finish",
}


class ToolDispatchRouter(Router):
    """工具分发 Router。

    FORMAT_IN  = agent.tool-request
    FORMAT_OUT = agent.tool-response
    """

    DESCRIPTION: ClassVar[str] = "按 tool_name 分发到具体 SingleToolRouter；未知工具返回 is_error=True"
    FORMAT_IN: ClassVar[str] = "agent.tool-request"
    FORMAT_OUT: ClassVar[str] = "agent.tool-response"
    INPUT_KEYS: ClassVar[list[str]] = ["tool_name", "tool_args", "tool_use_id"]
    OUTPUT_KEYS: ClassVar[list[str]] = ["tool_name", "tool_use_id", "result", "is_error"]

    ROUTER_NAME: ClassVar[str] = "tool_dispatch"

    def __init__(
        self,
        *,
        tool_routers: list[SingleToolRouter],
        bus: Any | None = None,
    ):
        if bus is None:
            raise RuntimeError(
                "ToolDispatchRouter requires an EventBus (bus=...). "
                "Silent no-op emit loses tool dispatch trail."
            )
        self._bus = bus
        self._routers: dict[str, SingleToolRouter] = {
            r.TOOL_NAME: r for r in tool_routers
        }
        if not self._routers:
            logger.warning(
                "[ToolDispatchRouter] no tool_routers registered — every tool call will error"
            )

    @property
    def routers(self) -> list[SingleToolRouter]:
        """返回所有注册的 SingleToolRouter（用于 LLMCallRouter 收集 tools_spec）。"""
        return list(self._routers.values())

    def tools_spec(self) -> list[dict]:
        """汇总所有注册工具的 Anthropic API schema。"""
        return [type(r).to_api_spec() for r in self._routers.values()]

    def produces_filesystem_mutation(self, tool_name: str) -> bool:
        """Whether a controlled tool declares a concrete filesystem write.

        Wildcard producers such as shell and sub-agent tools are deliberately
        excluded: their successful return does not prove that workspace state
        changed. File tools declare explicit ``meta_io.fs.*`` outputs and can
        safely advance the Agent loop's workspace-mutation generation.
        """

        canonical_name = tool_name
        if canonical_name not in self._routers:
            canonical_name = _NATIVE_TOOL_ALIASES.get(
                canonical_name.casefold(),
                canonical_name,
            )
        target = self._routers.get(canonical_name)
        if target is None:
            return False
        produced = tuple(getattr(type(target), "PRODUCED_META_IO", ()) or ())
        return any(
            item != "*" and str(item).startswith("meta_io.fs.")
            for item in produced
        )

    async def run(self, input_data: Any) -> Verdict:
        pre = self.validate_input(input_data)
        if pre is not None:
            return pre

        trace_id = input_data.get("trace_id", "")
        requested_tool_name = str(input_data.get("tool_name", ""))
        tool_name = requested_tool_name
        if tool_name not in self._routers:
            tool_name = _NATIVE_TOOL_ALIASES.get(tool_name.casefold(), tool_name)
        routed_input = input_data if tool_name == requested_tool_name else {**input_data, "tool_name": tool_name}
        tool_use_id = input_data.get("tool_use_id", "")
        turn = input_data.get("turn", 0)

        await emit_router_input(
            self._bus,
            trace_id=trace_id,
            router_name=self.ROUTER_NAME,
            format_id=self.FORMAT_IN,
            data={
                "tool_name": tool_name,
                "requested_tool_name": requested_tool_name,
                "tool_use_id": tool_use_id,
                "turn": turn,
                "available_tools": list(self._routers.keys()),
            },
        )

        target = self._routers.get(tool_name)
        if target is None:
            diagnosis = (
                f"Unknown tool '{tool_name}'. Available: {sorted(self._routers)}"
            )
            logger.warning("[ToolDispatchRouter] %s", diagnosis)
            output = {
                "tool_name": tool_name,
                "tool_use_id": tool_use_id,
                "result": f"Error: {diagnosis}",
                "is_error": True,
                "duration_ms": 0.0,
                "turn": turn,
            }
            verdict = Verdict(kind=VerdictKind.PASS, output=output, diagnosis=diagnosis)
        else:
            # 委派给具体工具 Router
            inner = await target.run(routed_input)
            verdict = inner

        await emit_router_output(
            self._bus,
            trace_id=trace_id,
            router_name=self.ROUTER_NAME,
            format_id=self.FORMAT_OUT,
            data={
                "tool_name": tool_name,
                "tool_use_id": tool_use_id,
                "is_error": verdict.output.get("is_error", False) if isinstance(verdict.output, dict) else False,
                "turn": turn,
            },
            verdict_kind=verdict.kind.value,
        )
        return verdict
