# [OMNI] origin=ai-ide ts=2026-05-11 type=infra
# [OMNI] material_id="material:dashboard.ccdaemon.providers.omni_agent_provider.py"
"""OmniAgentProvider — 包装 omnicompany 自家 AgentNodeLoop 路径 (qwen-3.6-plus + 自家工具).

跟 ClaudeCodeProvider 区别
==========================

- ClaudeCodeProvider 包 claude-agent-sdk → 本地 claude binary → Anthropic 订阅
- OmniAgentProvider 包 omnicompany.packages.services._core.agent.AgentNodeLoop →
  qwen-3.6-plus (THE_COMPANY_API_KEY) → omnicompany 自家工具体系 (glob/grep/read_file/...)
- 都实现同一 BaseProvider 接口, 上层 ChatSession 无感知

事件捕获策略
============

AgentNodeLoop 上游已经为"实时 UI 更新"提供了三个 async 钩子:

- `on_tool_dispatch_start(tool_name, tool_args, tool_use_id, turn, trace_id)`
- `on_tool_dispatch_end(tool_name, tool_use_id, result, is_error, turn, trace_id)`
- `on_turn_end_async(turn, messages, trace_id)`  — 拿到完整 messages 列表

OmniAgentProvider 通过**实例级 monkey-patch** 把这些钩子接管到内部 queue, 不需要
用户自己写子类. 比起 bus subscribe 路径:
- 优: 拿到完整数据 (bus event payload 把 LLM text 截断到 500 chars 仅作 audit 用)
- 优: 不需要 bus 实例 (虽然 agent 实例化时要 bus, 但 provider 不参与订阅)
- 劣: 颗粒度是 turn-level 不是真 streaming (LLM 单 turn 跑完才推 text NormalizedMessage)
       后续要 token-level streaming 得改上游 LLMCallRouter emit 流式 chunk 事件

session_created / complete / error 由 provider 自己生成 (不来自 hook, 来自
provider 知道 send_prompt 起点跟 agent.run() 完成时机).

ProviderOptions 扩展字段
========================

跟 ClaudeCodeProvider 共用 base.py 的 `ProviderOptions`. 额外约定 (key 名借 extras):
- `agent_class` (ClassVar): AgentNodeLoop 子类, 调用方传. 不传走 DefaultChatAgent
- `agent_bus`: Bus 实例 (SQLiteBus / MemoryBus), agent 需要

注意: ProviderOptions 是 TypedDict total=False, 这两个 key 不在 base.py 声明, 但
TypedDict 允许 extras (Python 类型检查器宽松). 实际访问用 .get() 兜底.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, AsyncIterator

from omnicompany.packages.services._core.agent.event_bridge import publish_agent_event

from ..normalized_protocol import NormalizedMessage
from .base import BaseProvider, ProviderOptions

logger = logging.getLogger(__name__)


def _extract_assistant_text(content: Any) -> str:
    """从 messages[i].content 抽 assistant 文本. content 可能是 str 或 block list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "thinking":
                    # thinking 单独走 thinking NormalizedMessage; 这里不抓
                    continue
        return "".join(parts)
    return str(content) if content else ""


def _extract_assistant_thinking(content: Any) -> str:
    """从 assistant content 抽 thinking 块 (qwen 偶发的 reasoning 内容)."""
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "thinking":
            parts.append(block.get("thinking", "") or block.get("text", ""))
    return "".join(parts)


class OmniAgentProvider(BaseProvider):
    """包装 omnicompany 自家 AgentNodeLoop, 通过实例钩子捕获事件转 NormalizedMessage."""

    def __init__(self, options: ProviderOptions) -> None:
        super().__init__(options)
        self._agent: Any = None  # AgentNodeLoop 子类实例; 在 connect 里实例化
        self._connected = False
        self._queue: asyncio.Queue[NormalizedMessage | None] = asyncio.Queue()
        self._run_task: asyncio.Task | None = None
        self._current_trace: str | None = None
        self._agent_bus: Any = None

    async def connect(self) -> None:
        if self._connected:
            return

        # 从 options 取 agent_class + bus (TypedDict extras)
        opts: dict[str, Any] = dict(self.options)
        agent_class = opts.get("agent_class")
        bus = opts.get("agent_bus")

        if agent_class is None:
            raise RuntimeError(
                "OmniAgentProvider requires options['agent_class'] (AgentNodeLoop subclass)"
            )
        if bus is None:
            raise RuntimeError(
                "OmniAgentProvider requires options['agent_bus'] (SQLiteBus / MemoryBus instance)"
            )

        # 实例化 agent
        self._agent = agent_class(bus=bus, model=opts.get("model"))
        self._agent_bus = bus

        # 实例级 monkey-patch hooks 接管事件 → queue
        self._patch_agent_hooks(self._agent)

        self._connected = True
        logger.info(
            "OmniAgentProvider connected (agent_class=%s, model=%s)",
            agent_class.__name__, opts.get("model"),
        )

    def _patch_agent_hooks(self, agent: Any) -> None:
        """把 agent 实例的 3 个钩子方法替换成推 queue 的版本."""
        queue = self._queue
        provider = self

        async def hook_tool_start(*, tool_name: str, tool_args: dict, tool_use_id: str,
                                   turn: int, trace_id: str) -> None:
            await queue.put({
                "kind": "tool_use",
                "toolId": tool_use_id,
                "toolName": tool_name,
                "input": tool_args,
                "sessionId": trace_id,
            })
            await provider._publish_provider_event(
                trace_id,
                "agent.provider.tool_call",
                {
                    "tool": tool_name,
                    "args": tool_args,
                    "tool_use_id": tool_use_id,
                    "turn": turn,
                },
                tags=[f"tool:{tool_name}"],
            )

        async def hook_tool_end(*, tool_name: str, tool_use_id: str, result: str,
                                 is_error: bool, turn: int, trace_id: str) -> None:
            await queue.put({
                "kind": "tool_result",
                "toolId": tool_use_id,
                "result": result,
                "isError": is_error,
                "sessionId": trace_id,
            })
            await provider._publish_provider_event(
                trace_id,
                "agent.provider.tool_result",
                {
                    "tool": tool_name,
                    "result": result,
                    "tool_use_id": tool_use_id,
                    "is_error": is_error,
                    "turn": turn,
                },
                tags=[f"tool:{tool_name}"],
            )

        # turn-level: 拿 messages 末尾 assistant text → text NormalizedMessage
        # (thinking 块独立推 thinking NormalizedMessage)
        async def hook_turn_end(*, turn: int, messages: list, trace_id: str) -> None:
            if not messages:
                return
            # 找最近 assistant message (最末或倒数第二 — tool_result 是 user role 不算)
            for msg in reversed(messages):
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    content = msg.get("content")
                    text = _extract_assistant_text(content)
                    thinking = _extract_assistant_thinking(content)
                    if thinking:
                        await queue.put({
                            "kind": "thinking",
                            "content": thinking,
                            "sessionId": trace_id,
                        })
                    if text:
                        await queue.put({
                            "kind": "text",
                            "content": text,
                            "sessionId": trace_id,
                        })
                    break

        # 实例级覆盖 (不污染类)
        agent.on_tool_dispatch_start = hook_tool_start
        agent.on_tool_dispatch_end = hook_tool_end
        agent.on_turn_end_async = hook_turn_end

    async def send_prompt(self, prompt: str, options: dict[str, Any] | None = None) -> None:
        if not self._connected or self._agent is None:
            raise RuntimeError("OmniAgentProvider not connected; call connect() first")

        # 复用调用方下传的 sessionId(同会话跨 turn 稳定 → 历史按它聚合到同一 JSONL);
        # 没有(新会话首条)才新铸 uuid。这是 D4 落盘恢复历史的前置: 不复用则每 turn 一个
        # 新 trace_id, 历史散成多文件、刷新只看到最后一 turn。
        trace_id = (options or {}).get("sessionId") or str(uuid.uuid4())
        self._current_trace = trace_id

        # 推 session_created (本 turn 开始)
        await self._queue.put({
            "kind": "session_created",
            "newSessionId": trace_id,
            "sessionId": trace_id,
        })
        await self._publish_provider_event(
            trace_id,
            "agent.provider.session_created",
            {
                "model": self.options.get("model"),
            },
        )

        # 构造 agent input_data — 默认 key 'input', 子类 PromptBuilder 按需读
        input_data: dict[str, Any] = {
            "input": prompt,
            "prompt": prompt,  # 双 key 兼容不同 PromptBuilder 约定
            "trace_id": trace_id,
            "origin": "ai-ide",
            "agent_name": type(self._agent).__name__,
        }
        if options:
            input_data.update(options)

        async def _run() -> None:
            try:
                verdict = await self._agent.run(input_data)
                # complete NormalizedMessage
                exit_code = 0 if str(verdict.kind).endswith("PASS") else 1
                final_output = verdict.output if isinstance(verdict.output, dict) else {}
                # 把 verdict 收尾文本作为最后 text 推 (兜底 turn_end 没推到的 / finish 工具回复)。
                # 注意 key: ExtractResultRouter 默认 output 用 "text"(见 extract_result.py),
                # finish 工具回复经 loop final_text → extract → output["text"]; 漏读这个 key 会
                # 导致 agent 回复被静默丢掉(只剩 session_created + complete, 无 text)。
                final_text = (
                    final_output.get("final_text")
                    or final_output.get("text")
                    or final_output.get("result", "")
                )
                if final_text and isinstance(final_text, str):
                    await self._queue.put({
                        "kind": "text",
                        "content": final_text,
                        "sessionId": trace_id,
                    })
                await self._queue.put({
                    "kind": "complete",
                    "sessionId": trace_id,
                    "exitCode": exit_code,
                    "actualSessionId": trace_id,
                })
                await self._publish_provider_event(
                    trace_id,
                    "agent.provider.complete",
                    {
                        "exit_code": exit_code,
                        "actual_session_id": trace_id,
                    },
                )
            except asyncio.CancelledError:
                await self._queue.put({
                    "kind": "complete",
                    "sessionId": trace_id,
                    "aborted": True,
                })
                await self._publish_provider_event(
                    trace_id,
                    "agent.provider.aborted",
                    {},
                )
                raise
            except Exception as e:
                logger.exception("OmniAgentProvider agent.run() failed")
                await self._queue.put({
                    "kind": "error",
                    "sessionId": trace_id,
                    "error": f"{type(e).__name__}: {e}",
                })
                await self._publish_provider_event(
                    trace_id,
                    "agent.provider.error",
                    {
                        "error": f"{type(e).__name__}: {e}",
                    },
                )

        self._run_task = asyncio.create_task(_run())

    async def interrupt(self) -> None:
        if self._agent is not None:
            try:
                self._agent.abort()  # threading.Event.set, agent 主循环每 turn 头检查
            except Exception as e:
                logger.warning("OmniAgentProvider agent.abort() failed: %s", e)
        if self._run_task and not self._run_task.done():
            self._run_task.cancel()

    async def disconnect(self) -> None:
        if self._run_task and not self._run_task.done():
            self._run_task.cancel()
            try:
                await self._run_task
            except (asyncio.CancelledError, Exception):
                pass
        await self._queue.put(None)  # 哨兵
        self._connected = False
        self._agent = None
        self._agent_bus = None

    async def consume_messages(self) -> AsyncIterator[NormalizedMessage]:
        while True:
            nm = await self._queue.get()
            if nm is None:
                break
            yield nm

    async def _publish_provider_event(
        self,
        trace_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        tags: list[str] | None = None,
    ) -> None:
        await publish_agent_event(
            self._agent_bus,
            trace_id=trace_id,
            event_type=event_type,
            source="agent.provider.omni_agent",
            payload={"provider": "omni_agent", **payload},
            tags=["omni_agent_provider", *(tags or [])],
        )


__all__ = ["OmniAgentProvider"]
