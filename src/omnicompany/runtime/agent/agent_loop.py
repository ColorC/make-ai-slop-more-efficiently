# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:43Z
# [OMNI] material_id="material:runtime.agent.codeact_loop.pipeline_definition.py"
"""Agent Loop — LAP 三节点声明 + 运行入口

复刻 OpenHands CodeAct Agent 的调度逻辑:
- 4 个工具: bash, str_replace_editor, finish, think
- 多 tool_call 支持
- EventBus 全程事件驱动

架构分层（六元语义接口规范 V1.1）：
    build_agent_pipeline() — 纯 LAP 声明（TeamSpec，只描述"是什么"）
    build_bindings()       — Router 绑定（实现"怎么做"）
    run_agent()            — 最小骨架入口（意图解析 + system prompt 增强 + TeamRunner）

扩展入口：
    run_agent_with_intent  — 见 omnicompany.runtime.agent.agent_intent_router（带 mirror/mutation_state 支持）
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from omnicompany.protocol.anchor import (
    AnchorSpec,
    Route,
    RouteAction,
    TransformerSpec,
    TransformMethod,
    ValidatorKind,
    ValidatorSpec,
    VerdictKind,
)
from omnicompany.protocol.team import (
    NodeKind,
    TeamEdge,
    TeamNode,
    TeamSpec,
)
from omnicompany.runtime.llm.llm import LLMClient
from omnicompany.runtime.routing.router import ContextRouter, LLMRouter, Router, ToolRouter
from omnicompany.runtime.exec.runner import TeamRunner
from omnicompany.runtime.exec.tool_executor import ToolExecutor

from omnicompany.runtime.agent.agent_constants import (
    DEFAULT_SYSTEM_PROMPT,
)


def build_agent_pipeline() -> TeamSpec:
    """构建 Agent Loop 的 TeamSpec（纯声明，不含任何实现逻辑）

    三节点闭环：
        context (确定性整流器) → llm (语义整流器) → tool (确定性整流器) → context ...

    终止条件：llm 节点 PASS（LLM 调用 finish 或返回纯文本）→ 管线退出。
    """

    context_node = TeamNode(
        id="context",
        kind=NodeKind.TRANSFORMER,
        transformer=TransformerSpec(
            id="context-router",
            name="Context 拼接器",
            from_format="tool-observation",
            to_format="agent-state",
            method=TransformMethod.RULE,
            description="将 user_input / tool_results 拼接为 Anthropic messages 格式",
        ),
    )

    llm_node = TeamNode(
        id="llm",
        kind=NodeKind.ANCHOR,
        anchor=AnchorSpec(
            id="llm-router",
            name="LLM 语义整流器",
            format_in="agent-state",
            format_out="agent-action",
            validator=ValidatorSpec(
                id="llm",
                kind=ValidatorKind.SOFT,
                description="LLM 调用: 接收 messages, 产出 response (支持多工具调用)",
            ),
            routes={
                VerdictKind.PASS: Route(
                    action=RouteAction.EMIT,
                    feedback="LLM 调用 finish / 返回纯文本, 任务结束",
                ),
                VerdictKind.FAIL: Route(
                    action=RouteAction.NEXT,
                    target="tool",
                    feedback="LLM 请求工具执行 (bash/editor/think)",
                ),
            },
        ),
    )

    tool_node = TeamNode(
        id="tool",
        kind=NodeKind.ANCHOR,
        anchor=AnchorSpec(
            id="tool-router",
            name="工具执行整流器",
            format_in="agent-action",
            format_out="tool-observation",
            validator=ValidatorSpec(
                id="tool-executor",
                kind=ValidatorKind.HARD,
                description="按 tool_name 分发执行: bash, str_replace_editor, think",
            ),
            routes={
                VerdictKind.PASS: Route(
                    action=RouteAction.NEXT,
                    target="context",
                    feedback="工具执行完成, 结果回到 Context 拼接",
                ),
                VerdictKind.FAIL: Route(
                    action=RouteAction.NEXT,
                    target="context",
                    feedback="工具执行失败, 错误信息回到 Context 拼接",
                ),
            },
        ),
    )

    return TeamSpec(
        id="agent-loop",
        name="LAP CodeAct Agent Loop",
        description="ContextRouter -> LLMRouter -> ToolRouter 循环 (复刻 OpenHands 调度逻辑)",
        nodes=[context_node, llm_node, tool_node],
        edges=[
            TeamEdge(source="context", target="llm", label="messages 就绪"),
            TeamEdge(source="llm", target="tool", condition=VerdictKind.FAIL, label="需要工具"),
            TeamEdge(source="tool", target="context", label="工具结果回拼"),
        ],
        entry="context",
    )


def build_bindings(
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: int = 30,
) -> dict[str, Router]:
    """构建 Router 绑定（将 LAP 声明节点绑定到真实实现）"""
    from omnicompany.runtime.exec.tools import ALL_TOOLS
    client = LLMClient(model=model, base_url=base_url, api_key=api_key, tools=ALL_TOOLS)
    executor = ToolExecutor(timeout=timeout)
    return {
        "context": ContextRouter(),
        "llm":     LLMRouter(client),
        "tool":    ToolRouter(executor=executor),
    }


async def run_agent(
    task: str,
    *,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    max_steps: int = 50,
    db_path: str | None = None,
    pipeline: TeamSpec | None = None,
    intent_db_path: str | None = None,
    route_db_path: str | None = None,
    semantic_network_db_path: str | None = None,
    parent_task_id: str = "",
    origin: str = "human",
    mirror: "MirrorNode | None" = None,
) -> str:
    """运行 LAP CodeAct Agent（最小骨架版本，纯三节点 pipeline）

    本函数是纯净的 LAP 入口：意图解析 → system prompt 增强 → TeamRunner。
    不包含语义网络、PathExecutor、进化等扩展逻辑。

    扩展功能请使用：
        run_agent_with_intent  — 带语义网络、PathExecutor、M1/M3 回路
        run_autonomous         — 自主闭环

    Args:
        task:          用户任务描述
        system_prompt: 系统提示词
        model/base_url/api_key: LLM 配置（默认从环境变量读取）
        max_steps:     LLM 决策预算（只计算 SOFT 节点 / LLM 调用次数）
        db_path:       SQLite 事件库路径（默认走 unified data/events.db；任何路径都会被引擎层 redirect）
        pipeline:      可选外部 TeamSpec（拓扑进化场景用）
        intent_db_path: 意图轨迹库路径（默认 data/intent_traces.db）
        route_db_path: 保留参数（兼容旧调用，本函数不使用）
        parent_task_id: 启动本 trace 的任务 ID（空 = 用户直接发起）
        origin:        trace 发起者 — 'human' | 'explorer' | 'meta_agent'
        mirror:        MirrorNode 实例（保留参数，本函数不注入 Truth）

    Returns:
        Agent 的最终输出字符串
    """
    from pathlib import Path
    from ulid import ULID
    from omnicompany.bus.sqlite import SQLiteBus
    from omnicompany.tracing import IntentTracer
    from omnicompany.runtime.agent.agent_intent_router import _parse_user_intent

    # Move 8: SQLiteBus engine routes to unified data/events.db when db_path is None.
    intent_db = Path(intent_db_path) if intent_db_path else Path("data/intent_traces.db")
    trace_id = str(ULID())

    _agent_success = False
    async with SQLiteBus(db_path) as bus:
        tracer = IntentTracer(
            db_path=intent_db,
            trace_id=trace_id,
            parent_task_id=parent_task_id,
            origin=origin,
            event_bus=bus,
        )

        # 意图解析前置：将用户请求拆解为结构化意图（step=-1 写入轨迹）
        try:
            _parsed_intent = await _parse_user_intent(
                task, tracer, model=model, base_url=base_url, api_key=api_key
            )
            if _parsed_intent:
                for t in _parsed_intent.get("desired_output_types", []):
                    tracer._held_types.add(f"__intent__{t}")
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("User intent parsing failed: %s", e)

        effective_pipeline = pipeline or build_agent_pipeline()
        bindings = build_bindings(model=model, base_url=base_url, api_key=api_key)
        bindings["llm"].tracer = tracer  # type: ignore[attr-defined]

        runner = TeamRunner(effective_pipeline, bindings, bus, max_steps=max_steps)
        try:
            result = await runner.run({
                "system_prompt": system_prompt,
                "user_input": task,
                "messages": [],
            })
            _agent_success = True
        finally:
            tracer.close()

        return result


# ── Re-exports ───────────────────────────────────────────────────────────────
from omnicompany.runtime.agent.agent_intent_router import run_agent_with_intent  # noqa: F401

if TYPE_CHECKING:
    from omnicompany.runtime.signals.mirror_node import MirrorNode
