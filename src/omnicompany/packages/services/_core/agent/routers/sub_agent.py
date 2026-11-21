# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-05-04 type=router
# [OMNI] material_id="material:core.agent.routers.sub_agent.spawn_tool.py"
"""SubAgentRouter — 让 agent 通过统一 tool 调用 spawn 子 agent.

CC 对齐 (build-src/src/coordinator/coordinatorMode.ts):
  Agent({description, subagent_type, prompt})
    → 找 subagent_type 注册的类
    → 实例化 + run({task: prompt, trace_id})
    → 等子 agent 收口
    → 返 verdict.output 文本给主 agent

之前 ad-hoc 模式 (repo/learner spawn_module_reader / landmark_picker submit_*)
全都直接 instance 化某个具体 AgentNodeLoop 子类. 现在通过 SubAgentRegistry 注册
+ SubAgentRouter spawn, agent prompt 只见 "agent" 工具 + 一个 subagent_type 字段.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)
from omnicompany.packages.services._core.agent.sub_agent_registry import SubAgentRegistry

logger = logging.getLogger(__name__)


class SubAgentRouter(SingleToolRouter):
    """Spawn a registered sub-agent type to handle a specialized task.

    Sub-agents inherit the bus + parent trace_id (传递审计链), 跑独立 turn 循环
    直到 finish, 把 Verdict.output 作为文本返给主 agent.
    """

    TOOL_NAME: ClassVar[str] = "agent"
    DESCRIPTION: ClassVar[str] = (
        "Spawn a registered specialized sub-agent to handle a focused task.\n\n"
        "Use sub-agents to:\n"
        "  - 把工具结果繁多的任务下放到子 agent (主 agent context 不被刷掉)\n"
        "  - 调专门 agent (code review / 安全审计 / 探索 / 验证)\n"
        "  - 并发跑独立任务 (虽然 agent loop 内目前 sync 跑)\n\n"
        "Args:\n"
        "  description: 3-5 word task summary (e.g. 'review auth migration')\n"
        "  subagent_type: 一个已注册的 type. 调用前先看下面 registered types.\n"
        "  prompt: 完整任务描述 (子 agent 看到的 user message)\n\n"
        "Sub-agent 跑完返 verdict.kind (pass/partial/fail) + output 文本."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "3-5 word summary of what the sub-agent will do",
            },
            "subagent_type": {
                "type": "string",
                "description": "Registered sub-agent type name. Use the agent type that best matches your task.",
            },
            "prompt": {
                "type": "string",
                "description": "Full task description / user message for the sub-agent.",
            },
        },
        "required": ["description", "subagent_type", "prompt"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False  # 子 agent 跑长, 默认不并发
    IS_READONLY: ClassVar[bool] = False  # 子 agent 可写 (取决于它自己的工具集)

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        description = (args.get("description") or "").strip()
        sub_type = (args.get("subagent_type") or "").strip()
        prompt = args.get("prompt") or ""

        if not sub_type:
            available = SubAgentRegistry.list_types()
            raise ToolExecutionError(
                f"subagent_type is required. Registered types: {available}"
            )
        if not prompt:
            raise ToolExecutionError("prompt is required (the task for the sub-agent)")

        entry = SubAgentRegistry.get(sub_type)
        if entry is None:
            available = SubAgentRegistry.list_types()
            raise ToolExecutionError(
                f"unknown subagent_type {sub_type!r}. Registered: {available}\n"
                f"To register: SubAgentRegistry.register('{sub_type}', YourAgentClass)"
            )

        logger.info(
            "[SubAgentRouter] spawning sub-agent type=%s description=%r prompt_chars=%d",
            sub_type, description, len(prompt),
        )

        # 构造 sub-agent. 复用主 agent 的 bus (审计链统一), trace_id 透传.
        try:
            sub_agent = entry.agent_class(bus=self._bus, **entry.config_overrides)
        except Exception as e:
            raise ToolExecutionError(
                f"failed to instantiate sub-agent {sub_type!r}: {type(e).__name__}: {e}"
            )

        parent_trace = getattr(ctx, "trace_id", "") or ""
        sub_input = {
            "task": prompt,
            "instruction": prompt,  # 兼容某些 agent 用 instruction 字段
            "description": description,
            "trace_id": f"{parent_trace}/sub:{sub_type}" if parent_trace else f"sub:{sub_type}",
            "parent_trace_id": parent_trace,
        }

        # 同步执行子 agent. 复用调用线程的 event loop 不行 (我们在 _execute 里, 主 loop 跑着).
        # 起新 event loop 跑 sub.run() (跟原 spawn_module_reader 同 pattern).
        loop = asyncio.new_event_loop()
        try:
            verdict = loop.run_until_complete(sub_agent.run(sub_input))
        except Exception as e:
            logger.warning("[SubAgentRouter] sub-agent %s raised: %s", sub_type, e, exc_info=True)
            return json.dumps({
                "subagent_type": sub_type,
                "description": description,
                "verdict": "error",
                "error": f"{type(e).__name__}: {e}",
            }, ensure_ascii=False)
        finally:
            loop.close()

        kind = getattr(verdict.kind, "value", str(verdict.kind))
        out = verdict.output
        # 拍扁 output 到 string (LLM tool result 必须 string)
        try:
            out_str = json.dumps(out, ensure_ascii=False, default=str)[:8000]
        except Exception:
            out_str = str(out)[:8000]
        diag = (verdict.diagnosis or "")[:500]

        return json.dumps({
            "subagent_type": sub_type,
            "description": description,
            "verdict": kind,
            "diagnosis": diag,
            "output": out_str,
        }, ensure_ascii=False)


__all__ = ["SubAgentRouter"]
