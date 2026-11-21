# [OMNI] origin=claude-code domain=omnicompany/omnicompany ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:omnicompany.agent_team.demo_workers.py"
# ⚠ DEPRECATED 2026-05-01 — 这是**示例 demo**, 不是公用 framework. 但被 business_explorer /
# kb_ingestion_agent / kb_multi_agent 错误地当模板复制了 4 worker 模式. 真正的公用 agent 框架在
# packages/services/agent (V1.1 正式包: 6 子 Router + 7 SingleToolRouter + LLMCall 重试 +
# ContextCompact L1-L3 + BashRouter 走 BashBus). 新写 agent 一律继承
# packages/services/agent.AgentNodeLoop, 不要再复制本 demo.
"""Agent Team · 纯 bus 驱动最小示例 [DEPRECATED — 仅参考勿继承].

用户 2026-04-20 洞察:
  "agent 内部也完全按照生产订阅的方式运行 — 其每轮循环都可以说是一个 job,
   job 的发起者理论上要么是用户输入要么是工具返回"

本模块 = **Agent Team 不是单 Worker, 而是一组 Worker 通过主 bus 激活**, 对应 R-19 修正:
  - 无"迷你 stock"二分层
  - 每轮循环 = 一个子 job (parent_job_id 链 agent 内部因果)
  - 终止由 finish tool → sink material 决定

Agent Team 订阅图:
  agent.request (source, 用户输入)
       ↓
  AgentContextScript Worker (订阅 agent.request 或 agent.tool_result)
       ↓ agent.prompt_context
  AgentLLM Worker (订阅 agent.prompt_context)
       ↓ agent.llm_response (含 finish 或 tool_call)
       ├── finish → AgentFinalizer → agent.final_output (sink)
       └── tool_call → AgentTool → agent.tool_result (触发新子 job)
                                       ↓
                                   AgentContextScript 再激活 (新 job_id)
                                       ↓ ... 循环

子 job 触发机制: AgentContextScript 订阅 agent.tool_result 时,
在 output 标 `_emit_as_new_job: True`, dispatcher 产 event 时使用新 trace_id.
新 trace_id 使 Q1 "worker 每 job 单次激活" 能容纳 agent 多轮 LLM 调用.

注: 本模块是 **minimum viable pilot**, LLM/Tool 是 mock, 不调真 LLM.
"""
from __future__ import annotations

from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind

from .worker import Worker


# ══════════════════════════════════════════════════════════════════════
# Agent Team · Worker 实现（mock · 演示用）
#
# 注意: 继承 Worker (而非 Router) — omnicompany 层金标写法.
# Worker = Router 的 omnicompany 语义别名 + FORMAT_IN_MODE 默认值.
# ══════════════════════════════════════════════════════════════════════


class AgentContextScriptWorker(Worker):
    """组装 LLM 所需的 prompt context.

    订阅两种 material:
    - agent.request (source, 首轮, 用户输入)
    - agent.tool_result (后续轮, 工具返回)

    产出 agent.prompt_context.
    对 tool_result 触发的激活, 在 output 标 `_emit_as_new_job: True`
    → dispatcher 发布为新子 job (agent 循环新一轮).
    """

    DESCRIPTION = (
        "Agent Team Worker: 组装 LLM 的 prompt context. "
        "订阅 agent.request (首轮) 或 agent.tool_result (后续轮). "
        "tool_result 触发时 _emit_as_new_job=True 发新子 job."
    )
    FORMAT_IN = ["agent.request", "agent.tool_result"]
    FORMAT_IN_MODE = "or"  # alternative 语义: 任一到达即激活 (非 composite fan-in)
    FORMAT_OUT = "agent.prompt_context"

    def run(self, input_data: dict[str, Any]) -> Verdict:
        # 判定输入来源
        req = input_data.get("agent.request")
        tool_result = input_data.get("agent.tool_result")

        if req is not None:
            # 首轮 (根 job)
            messages = [{"role": "user", "content": req.get("content", "")}]
            round_num = 1
        elif tool_result is not None:
            # 新子 job 内 (tool_result 已经由 AgentToolWorker 标 _emit_as_new_job 触发)
            prev_messages = tool_result.get("_prev_messages", [])
            messages = list(prev_messages) + [
                {"role": "tool_result", "content": tool_result.get("result", "")}
            ]
            round_num = tool_result.get("round", 1) + 1
        else:
            return Verdict(kind=VerdictKind.FAIL, diagnosis="no input material")

        return Verdict(
            kind=VerdictKind.PASS,
            output={"messages": messages, "round": round_num},
        )


class AgentLLMWorker(Worker):
    """LLM 调用 (mock): 根据 prompt_context 产 response.

    mock 逻辑:
    - round 1: 产 tool_call (要求调用 mock_tool)
    - round 2+: 产 finish
    """

    DESCRIPTION = (
        "Agent Team Worker: 调 LLM 产 response (mock 版). "
        "response 含 finish 或 tool_call, 决定下一步路径."
    )
    FORMAT_IN = "agent.prompt_context"
    FORMAT_OUT = "agent.llm_response"

    def run(self, input_data: dict[str, Any]) -> Verdict:
        ctx = input_data.get("agent.prompt_context") or {}
        round_num = ctx.get("round", 1)
        messages = ctx.get("messages", [])

        if round_num >= 2:
            # 第二轮: finish
            return Verdict(
                kind=VerdictKind.PASS,
                output={
                    "kind": "finish",
                    "content": f"[mock answer at round {round_num}]",
                    "_prev_messages": messages,
                    "round": round_num,
                },
            )
        else:
            # 首轮: 要求 tool_call
            return Verdict(
                kind=VerdictKind.PASS,
                output={
                    "kind": "tool_call",
                    "tool_name": "mock_tool",
                    "tool_args": {"query": "hello"},
                    "_prev_messages": messages,
                    "round": round_num,
                },
            )


class AgentToolWorker(Worker):
    """工具执行 (mock): 响应 llm_response 的 tool_call, 产 tool_result.

    仅订阅 kind=tool_call 的 response.
    finish 类 response 由 AgentFinalizerWorker 处理.
    """

    DESCRIPTION = (
        "Agent Team Worker: 执行 tool_call 产 tool_result (mock 版). "
        "finish 类 response 跳过 (AgentFinalizer 处理)."
    )
    FORMAT_IN = "agent.llm_response"
    FORMAT_OUT = "agent.tool_result"

    def run(self, input_data: dict[str, Any]) -> Verdict:
        resp = input_data.get("agent.llm_response") or {}
        if resp.get("kind") != "tool_call":
            # 非 tool_call (finish 类) 跳过 — 返回 FAIL 让 dispatcher 不 publish
            return Verdict(kind=VerdictKind.FAIL, diagnosis="not a tool_call response")

        # tool_result 产出 = 新子 job 发起 (agent 下一轮循环)
        # dispatcher 识别 _emit_as_new_job=True → 产 event 时用新 trace_id (新 job_id)
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "tool_name": resp.get("tool_name"),
                "result": f"mock result for {resp.get('tool_name')}",
                "_prev_messages": resp.get("_prev_messages", []),
                "round": resp.get("round", 1),
                "_emit_as_new_job": True,  # ← 触发 Agent Team 新一轮循环
            },
        )


class AgentFinalizerWorker(Worker):
    """终止器: 响应 kind=finish 的 llm_response, 产 sink material.

    finish 类 response → 产 agent.final_output (sink, 终止 Agent Team 循环).
    """

    DESCRIPTION = (
        "Agent Team Worker: 接收 finish 类 llm_response, 产 agent.final_output sink material."
    )
    FORMAT_IN = "agent.llm_response"
    FORMAT_OUT = "agent.final_output"

    def run(self, input_data: dict[str, Any]) -> Verdict:
        resp = input_data.get("agent.llm_response") or {}
        if resp.get("kind") != "finish":
            return Verdict(kind=VerdictKind.FAIL, diagnosis="not a finish response")

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "answer": resp.get("content", ""),
                "rounds": resp.get("round", 1),
            },
        )


# ══════════════════════════════════════════════════════════════════════
# 清单
# ══════════════════════════════════════════════════════════════════════


AGENT_TEAM_WORKERS: list[type[Worker]] = [
    AgentContextScriptWorker,
    AgentLLMWorker,
    AgentToolWorker,
    AgentFinalizerWorker,
]


__all__ = [
    "AgentContextScriptWorker",
    "AgentLLMWorker",
    "AgentToolWorker",
    "AgentFinalizerWorker",
    "AGENT_TEAM_WORKERS",
]
