# [OMNI] origin=ai-ide ts=2026-05-11 type=infra
# [OMNI] material_id="material:dashboard.ccdaemon.providers.claude_code_provider.py"
"""ClaudeCodeProvider — claude-agent-sdk 包装本地 claude binary 路径.

跟 chat.py 现有 SDK 代码同源, 包装成 BaseProvider 子类形态. 阶段过渡期 chat.py
仍跑现有路径, 本 provider 作为 OmniAgent / Codex 接入完后**统一切换**的目标.

跟现有 chat.py 区别
====================

- chat.py 直接 spawn ClaudeSDKClient, 把 5 种 message 类型转 ws 帧 (kind='assistant'/
  'user'/'result'/...). 帧是 SDK message 形态直转的"半 NormalizedMessage".
- 本 provider 把 SDK message 流转成 **NormalizedMessage** (跟 14 kind 协议对齐).
  上层 ChatSession 拿到的就是标准化的事件流, 不识别任何 SDK 形态.

NormalizedMessage 转换映射 (SDK message → NormalizedMessage kind 序列)
=====================================================================

| SDK message                  | 拆成 NormalizedMessage                          |
|------------------------------|------------------------------------------------|
| SystemMessage(subtype=init)  | session_created (含 newSessionId)               |
| AssistantMessage             | 拆 content[] →                                  |
|   TextBlock                  |   text (content=text 字符串)                    |
|   ThinkingBlock              |   thinking (content=thinking 字符串)            |
|   ToolUseBlock               |   tool_use (toolId/toolName/input)              |
| UserMessage(tool_result)     | tool_result (toolId/result/isError)             |
| ResultMessage                | complete (sessionId/exitCode/...)               |
| RateLimitEvent               | status (text='rate_limited', tokenBudget)       |
| StreamEvent (partial text)   | stream_delta (content)                          |

claude-agent-sdk 0.1.50 文本是**完整 message**, 非真 delta. 我们只在 SDK 出
StreamEvent partial 时用 stream_delta; AssistantMessage 完整文本用 text kind.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Any, AsyncIterator

import claude_agent_sdk as casdk

from ..normalized_protocol import NormalizedMessage
from .base import BaseProvider, ProviderOptions

logger = logging.getLogger(__name__)


# 默认参数 (从 chat.py 移过来 — 用户 ~/.claude/settings.json 不写死)
DEFAULT_PERMISSION_MODE = "bypassPermissions"
# 默认推理档 = 最大(用户明示"默认最大思考"); SDK 干净档位, 不污染消息。
DEFAULT_EFFORT = "max"


class ClaudeCodeProvider(BaseProvider):
    """claude-agent-sdk 包装本地 claude binary, 走 stream-json + 订阅认证."""

    def __init__(self, options: ProviderOptions) -> None:
        super().__init__(options)
        self._client: casdk.ClaudeSDKClient | None = None
        self._connected = False
        # 内部 NormalizedMessage 队列 (SDK 消费 task 推, consume_messages 读)
        self._queue: asyncio.Queue[NormalizedMessage | None] = asyncio.Queue()
        self._receive_task: asyncio.Task | None = None
        self._claude_session_id: str | None = None  # SystemMessage(init) 装回来

    async def connect(self) -> None:
        if self._connected:
            return
        sdk_options = casdk.ClaudeAgentOptions(
            system_prompt={"type": "preset", "preset": "claude_code"},
            tools={"type": "preset", "preset": "claude_code"},
            setting_sources=["user", "project", "local"],
            permission_mode=self.options.get("permission_mode", DEFAULT_PERMISSION_MODE),
            cwd=self.options.get("cwd"),
            model=self.options.get("model"),
            # 默认最大思考(用户明示): 走 SDK effort 档(干净), 不再往消息前拼 "ultrathink:" 暗号词。
            # 'max' 已实测 connect+query 通过; 可经 options 覆盖。
            effort=self.options.get("effort", DEFAULT_EFFORT),
            include_partial_messages=True,  # 开 SDK 流式 partial events, 让前端逐字看到打字
        )
        self._client = casdk.ClaudeSDKClient(options=sdk_options)
        try:
            await self._client.connect()
        except (casdk.CLINotFoundError, casdk.CLIConnectionError, casdk.ProcessError) as e:
            raise RuntimeError(f"ClaudeCodeProvider 启动失败 ({type(e).__name__}): {e}") from e
        self._connected = True
        logger.info("ClaudeCodeProvider connected (model=%s cwd=%s)",
                    self.options.get("model"), self.options.get("cwd"))

    async def send_prompt(self, prompt: str, options: dict[str, Any] | None = None) -> None:
        if self._client is None or not self._connected:
            raise RuntimeError("ClaudeCodeProvider not connected; call connect() first")

        # 启 receive task 把 SDK 流转 NormalizedMessage 推队列
        # 同 session 多次调 send_prompt: 每次新建 task (上一轮 task 已经自然结束)
        async def _receive_loop() -> None:
            assert self._client is not None
            try:
                async for msg in self._client.receive_response():
                    for nm in self._sdk_msg_to_normalized(msg):
                        await self._queue.put(nm)
            except asyncio.CancelledError:
                # interrupt 时取消, 推 complete(aborted=True) 让上层知道
                await self._queue.put({
                    "kind": "complete",
                    "sessionId": self._claude_session_id,
                    "aborted": True,
                })
                raise
            except Exception as e:
                logger.exception("ClaudeCodeProvider receive loop failed")
                await self._queue.put({
                    "kind": "error",
                    "sessionId": self._claude_session_id,
                    "error": f"{type(e).__name__}: {e}",
                })

        # 给 SDK query, 跟 chat.py 一致
        await self._client.query(prompt, session_id=self._claude_session_id or "default")
        self._receive_task = asyncio.create_task(_receive_loop())

    async def interrupt(self) -> None:
        if self._client is None:
            return
        try:
            await self._client.interrupt()
        except Exception as e:
            logger.warning("ClaudeCodeProvider interrupt failed: %s", e)
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()

    async def disconnect(self) -> None:
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception as e:
                logger.warning("ClaudeCodeProvider disconnect failed: %s", e)
        # 推一个 None 哨兵让 consume_messages 退出循环
        await self._queue.put(None)
        self._connected = False
        self._client = None

    async def consume_messages(self) -> AsyncIterator[NormalizedMessage]:
        """从内部队列流式吐 NormalizedMessage. None 哨兵触发循环结束 (disconnect 后)."""
        while True:
            nm = await self._queue.get()
            if nm is None:
                break
            yield nm

    # ── SDK message → NormalizedMessage 序列 ────────────────────────────────

    def _sdk_msg_to_normalized(self, msg: Any) -> list[NormalizedMessage]:
        """单个 SDK message 拆成 0+ 个 NormalizedMessage. 见模块 docstring 映射表."""
        sid = self._claude_session_id

        if isinstance(msg, casdk.SystemMessage):
            # SystemMessage(subtype=init) 第一帧带 session_id
            d = dataclasses.asdict(msg)
            if d.get("subtype") == "init":
                new_sid = (d.get("data") or {}).get("session_id")
                if new_sid:
                    self._claude_session_id = new_sid
                    return [{
                        "kind": "session_created",
                        "newSessionId": new_sid,
                        "sessionId": sid,
                    }]
            # 其他 SystemMessage subtype (例 'subagent' 等) 不映射, 上层不需要
            return []

        if isinstance(msg, casdk.AssistantMessage):
            out: list[NormalizedMessage] = []
            for block in msg.content:
                if isinstance(block, casdk.TextBlock):
                    out.append({"kind": "text", "content": block.text, "sessionId": sid})
                elif isinstance(block, casdk.ThinkingBlock):
                    out.append({"kind": "thinking", "content": block.thinking, "sessionId": sid})
                elif isinstance(block, casdk.ToolUseBlock):
                    out.append({
                        "kind": "tool_use",
                        "toolId": block.id,
                        "toolName": block.name,
                        "input": block.input,
                        "sessionId": sid,
                    })
                elif isinstance(block, casdk.ServerToolUseBlock):
                    # server_tool_use 也映射成 tool_use (NormalizedMessage 不区分 client/server tool)
                    out.append({
                        "kind": "tool_use",
                        "toolId": block.id,
                        "toolName": block.name,
                        "input": block.input,
                        "sessionId": sid,
                    })
            return out

        if isinstance(msg, casdk.UserMessage):
            # UserMessage 通常含 tool_result blocks (SDK 自循环工具调用结果)
            content = msg.content
            if not isinstance(content, list):
                return []
            out = []
            for block in content:
                if isinstance(block, casdk.ToolResultBlock):
                    out.append({
                        "kind": "tool_result",
                        "toolId": block.tool_use_id,
                        "result": block.content,
                        "isError": bool(getattr(block, "is_error", False)),
                        "sessionId": sid,
                    })
                elif isinstance(block, casdk.ServerToolResultBlock):
                    out.append({
                        "kind": "tool_result",
                        "toolId": block.tool_use_id,
                        "result": block.content,
                        "isError": bool(getattr(block, "is_error", False)),
                        "sessionId": sid,
                    })
            return out

        if isinstance(msg, casdk.ResultMessage):
            d = dataclasses.asdict(msg)
            return [{
                "kind": "complete",
                "sessionId": sid,
                "exitCode": 0 if not d.get("is_error") else 1,
                "actualSessionId": d.get("session_id"),
            }]

        if isinstance(msg, casdk.RateLimitEvent):
            d = dataclasses.asdict(msg)
            return [{
                "kind": "status",
                "text": "rate_limited",
                "tokenBudget": d,
                "sessionId": sid,
            }]

        if isinstance(msg, casdk.StreamEvent):
            # SDK 0.1.50 StreamEvent.event 是 Anthropic 流式协议原帧, 形如:
            #   {"type": "content_block_delta", "index": 0,
            #    "delta": {"type": "text_delta", "text": "Hi"}}
            # 抽出 delta.text 作 stream_delta NormalizedMessage. 别的事件类型
            # (content_block_start / content_block_stop / message_delta / etc) 跳过 —
            # 上层 useChatRealtimeHandlers stream_end 由 AssistantMessage 完整帧到达
            # 时由 ChatInterface 自家逻辑 finalize, 我们不主动发 stream_end.
            try:
                event = getattr(msg, "event", None)
                if isinstance(event, dict):
                    ev_type = event.get("type")
                    if ev_type == "content_block_delta":
                        delta = event.get("delta") or {}
                        d_type = delta.get("type")
                        if d_type == "text_delta":
                            text = delta.get("text") or ""
                            if text:
                                return [{"kind": "stream_delta", "content": text, "sessionId": sid}]
                        elif d_type == "thinking_delta":
                            thinking = delta.get("thinking") or ""
                            if thinking:
                                return [{"kind": "thinking", "content": thinking, "sessionId": sid}]
            except Exception:
                pass
            return []

        return []


__all__ = ["ClaudeCodeProvider"]
