# [OMNI] origin=ai-ide domain=dashboard ts=2026-05-02T10:00:00Z type=router status=active agent=ai-ide-current
# [OMNI] summary="Dashboard 私有 SingleToolRouter 子类 (TodoWrite 等). 跟 NativeIdeAgent 配套."
# [OMNI] why="2026-04-18 立的 Router 化铁律, 旧 ToolDefinition 已 deprecate. 这里把 IDE 业务工具按 SingleToolRouter 子类规范重写, import 即触发 register_tool 自动登记到 TOOL_REGISTRY."
# [OMNI] tags=agent,tools,todo_write,ide
# [OMNI] material_id="material:dashboard.native_agent.ide_tool_routers.py"
"""Dashboard 私有 IDE 业务工具 (SingleToolRouter 子类).

import 本模块即触发 SingleToolRouter 子类自动注册 (NativeIdeAgent.__init__ 时
auto_register_singletool_subclasses() 扫). SPEC.tools 写工具字符串名引用即可.

当前清单:
- TodoWriteRouter — 写 / 更新当前会话的 todo 列表
- ThinkRouter — 把 agent 思考内容记入 trace (无副作用)
"""

from __future__ import annotations

from typing import ClassVar

from omnicompany.runtime.agent.agent_loop_tools import ToolContext
from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolExecutionError,
)


class TodoWriteRouter(SingleToolRouter):
    """todo_write — 创建 / 维护本会话的结构化 task 清单 (跟旧 TodoWriteTool 等价).

    描述跟 input_schema 1:1 复刻旧 [agent_loop_tools.py:TodoWriteTool], 但走 Router 化协议:
    - 输入 / 输出走 agent.tool-request / agent.tool-response Format, 进 SQLiteBus 落盘
    - 输出 result 是格式化的 todo 列表文本, agent 看了知道当前状态
    - 真实 todo 状态在 SQLiteBus events 里 (按 trace_id + tool_name='todo_write' 过滤可重建)
    """

    TOOL_NAME: ClassVar[str] = "todo_write"
    DESCRIPTION: ClassVar[str] = (
        "Create and manage a structured task list for your current session.\n\n"
        "When to Use: complex multi-step tasks (3+ steps), user provides multiple tasks.\n"
        "When NOT to Use: single straightforward task, trivial tasks (<3 steps).\n\n"
        "Task States: pending, in_progress, completed.\n"
        "IMPORTANT: Each task must have content (imperative) and activeForm (present continuous).\n"
        "Exactly ONE task must be in_progress at any time. Mark completed immediately after finishing."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "The updated todo list",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "minLength": 1},
                        "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                        "activeForm": {"type": "string", "minLength": 1},
                    },
                    "required": ["content", "status", "activeForm"],
                },
            },
        },
        "required": ["todos"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True
    # todo_write 不动 fs / 外部 IO, 纯会话状态记录
    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        todos = args.get("todos", [])
        if not isinstance(todos, list):
            raise ToolExecutionError(f"todos must be a list, got {type(todos).__name__}")
        icons = {"pending": "○", "in_progress": "◉", "completed": "✓"}
        in_progress_count = sum(1 for t in todos if t.get("status") == "in_progress")
        if in_progress_count > 1:
            raise ToolExecutionError(
                f"Exactly ONE task must be in_progress at a time, got {in_progress_count}"
            )
        if not todos:
            return "Todos cleared."
        lines = [f"Todos updated ({len(todos)} items):"]
        for t in todos:
            status = t.get("status", "?")
            content = t.get("content", "?")
            lines.append(f"  {icons.get(status, '?')} [{status}] {content}")
        return "\n".join(lines)


class ThinkRouter(SingleToolRouter):
    """think — 让 agent 把推理过程记入 trace, 无副作用 (跟旧 ThinkTool 等价).

    用途: agent 在多步任务前把思路落档, 一是 trace 可读, 二是 LLM 自己接下来一轮
    会看到 thought 作为 tool_result, 强化推理. CC 同名工具是给 sonnet 用的.
    """

    TOOL_NAME: ClassVar[str] = "think"
    DESCRIPTION: ClassVar[str] = (
        "Use this tool to think through a problem step-by-step. "
        "This tool has no side effects — it simply records your reasoning. "
        "Useful before a multi-step task or when you need to weigh trade-offs."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "thought": {"type": "string", "description": "Your reasoning or thinking content"},
        },
        "required": ["thought"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True
    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        thought = args.get("thought", "")
        if not isinstance(thought, str) or not thought.strip():
            raise ToolExecutionError("thought must be a non-empty string")
        return thought


__all__ = ["TodoWriteRouter", "ThinkRouter"]
