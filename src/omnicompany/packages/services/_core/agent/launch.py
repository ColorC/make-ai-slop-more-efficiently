# [OMNI] origin=claude-code domain=services/_core/agent ts=2026-06-23T00:00:00Z type=infra
# OMNI-024 ALLOW: launch helper for the unified AgentNodeLoop, not a package router entrypoint.
# [OMNI] material_id="material:core.agent.unified_launch.json_readonly_runner.py"
"""run_json_agent —— 统一 AgentNodeLoop 的启动器（**不是**另造 agent）。

给"非 agent 的普通 Worker"一个标准入口: 借**统一** AgentNodeLoop 干"读 + 推理 + 出结构化 JSON"
的活, 而不是自己手搓 ReAct 循环 / 自己 new LLMClient 串 tool。任何要让 worker 调 LLM+工具的地方,
都走这里, 不许另起第二套 agent 实现。

铁律(对齐 worker.md R-26 / team.md P-18): 重复造 agent = 违规。统一 agent 不够用 → 改进
packages/services/_core/agent(AgentNodeLoop / SingleToolRouter / TOOL_REGISTRY), 不 fork。

返回 {ok, final(解析+校验过的 JSON), text, turn_count, error}。
"""
from __future__ import annotations

import os
import uuid
from dataclasses import replace
from typing import Any, Mapping

from omnicompany.bus.memory import MemoryBus
from omnicompany.runtime.agent.agent_loop_config import PRESET_STANDARD
from omnicompany.runtime.llm.structured import parse_json_block, validate_json_schema

from omnicompany.packages.services._core.agent.loop import AgentNodeLoop
from omnicompany.packages.services._core.agent.routers.single_tool import (
    FinishRouter,
    GlobRouter,
    GrepRouter,
    ListDirRouter,
    ReadFileRouter,
)


class _ReadOnlyJsonAgent(AgentNodeLoop):
    """统一 AgentNodeLoop 的只读实例(read_file/grep/glob/list_dir + finish)。

    不是新 agent —— 是 AgentNodeLoop 的一个具体配置, 给 run_json_agent 复用。
    工具上下文里钉死 cwd/project_root, 让只读工具能在仓里正确寻址。
    """

    TOOL_ROUTERS = [ReadFileRouter, GrepRouter, GlobRouter, ListDirRouter, FinishRouter]
    DESCRIPTION = "统一 AgentNodeLoop 只读 JSON 启动实例(read/grep/glob/list_dir+finish)"

    def __init__(self, *, model: str, bus: Any, config: Any, project_root: str):
        super().__init__(model=model, bus=bus, config=config)
        self._project_root = project_root

    def build_tool_context(self, *, input_data: dict, turn: int, trace_id: str) -> dict:
        ctx = super().build_tool_context(input_data=input_data, turn=turn, trace_id=trace_id)
        ctx["cwd"] = self._project_root
        ctx["project_root"] = self._project_root
        return ctx


async def run_json_agent(
    *,
    task: str,
    node_prompt: str,
    model: str = "gpt-5.5",
    result_schema: Mapping[str, Any] | None = None,
    project_root: str | None = None,
    max_turns: int = 16,
    caller: str = "json_agent",
) -> dict:
    """跑统一 AgentNodeLoop 一次(只读工具), 从 final_text 解析 JSON 并按 schema 校验。

    Args:
        task: 喂给 agent 的首轮 user 消息(具体任务, 含绝对路径等已解析好的上下文)。
        node_prompt: 作 system prompt 的指令模板(NODE_PROMPT)。
        model: 默认 gpt-5.5(便宜+够强)。
        result_schema: JSON Schema 子集; final 须满足, 否则 ok=False。
        project_root: 只读工具寻址根(默认进程 cwd)。
        max_turns: 轮次预算(默认 16, 读类任务足够)。

    Returns: {ok, final, text, turn_count, error}
    """
    bus = MemoryBus()
    cfg = replace(PRESET_STANDARD, max_turns=max_turns)
    loop = _ReadOnlyJsonAgent(model=model, bus=bus, config=cfg,
                              project_root=project_root or os.getcwd())
    loop.NODE_PROMPT = node_prompt  # run() 会把它作 node_prompt_template 透传给 PromptBuilder
    await bus.connect()
    try:
        verdict = await loop.run({"task": task, "trace_id": f"{caller}-{uuid.uuid4().hex[:8]}"})
    finally:
        try:
            await bus.close()
        except Exception:  # noqa: BLE001
            pass
    out = verdict.output if isinstance(verdict.output, dict) else {}
    text = out.get("text", "") or ""
    turn_count = int(out.get("turn_count", 0) or 0)
    try:
        parsed = parse_json_block(text)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "final": None, "text": text, "turn_count": turn_count, "error": str(e)}
    issues = validate_json_schema(parsed, result_schema) if result_schema else []
    if issues:
        return {"ok": False, "final": parsed, "text": text, "turn_count": turn_count,
                "error": "; ".join(f"{i.path}: {i.message}" for i in issues[:8])}
    return {"ok": True, "final": parsed, "text": text, "turn_count": turn_count, "error": ""}


__all__ = ["run_json_agent"]
