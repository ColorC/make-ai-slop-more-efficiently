# [OMNI] origin=ai-ide ts=2026-05-10 type=infra
# [OMNI] material_id="material:dashboard.ccdaemon.normalized_protocol.message_schema.py"
"""NormalizedMessage 协议 — 各 LLM provider 后端统一输出形态.

设计原则
========

1. **后端转, 前端通用消费**: 各 provider (Claude / Codex / OpenCode / Cursor) 后端
   把自家 SDK 原始消息**转换**为 NormalizedMessage. 前端 (ChatInterface +
   useChatRealtimeHandlers / useChatSessionState) 只认 NormalizedMessage, 不识别任
   何具体 SDK 形态. 接新 provider 时**前端零修改**.

2. **kind 字段是判别式**: NormalizedMessage 全部用 `kind` 字段区分类型. legacy
   `type` 字段是上游另一套消息 (websocket-reconnected / pending-permissions-response /
   session-status), **不属于** NormalizedMessage 协议, 由其他通道传输.

3. **schema 反向推导**: kind 名跟字段从上游 [`useChatRealtimeHandlers.ts`](../../../frontend/src/components/chat/hooks/useChatRealtimeHandlers.ts)
   实现真用到的 case 反推. 改协议前必先确认前端真消费.

字段命名约定
============

- camelCase: `sessionId` / `requestId` / `toolName` / `newSessionId` / `actualSessionId` /
  `exitCode` / `tokenBudget` / `canInterrupt` (跟前端 TS 形态对齐, 避免 ws bridge 时
  前端再转 snake_case)
- 通用字段:
  - `kind: str` — 必填, 消息类型判别式
  - `sessionId: str | None` — 大部分消息选填, 标识所属 session
  - `provider: str | None` — 选填, 标识来源 provider (例 'claude' / 'codex'),
    跨 provider session 互相区分用

各 kind schema
==============

`stream_delta`     增量文本流 (LLM 边生成边推)
                   { kind, content: str, sessionId? }

`stream_end`       一段流式输出结束 (前端从 buffer flush 到 store)
                   { kind, sessionId? }

`session_created`  provider 创了 session (例 Claude SDK 第一次调用返 session_id)
                   { kind, newSessionId: str, sessionId? }

`complete`         一个 turn 结束 (LLM 答完 + 全部 tool 执行完)
                   { kind, sessionId, aborted?: bool, actualSessionId?: str,
                     exitCode?: int }

`error`            错误帧 (provider 抛错 / session 异常 / 工具调用失败上报)
                   { kind, sessionId?, error?: str }

`permission_request`  工具调用待批 (例 Bash exec 命令需用户允许)
                      { kind, requestId: str, toolName: str, input?, context?,
                        sessionId? }

`permission_cancelled`  用户取消待批 / 后端 timeout 取消
                       { kind, requestId: str }

`status`           运行时状态推送 (例 thinking 中 / 工具执行中 / token 预算)
                   { kind, text: str, tokens?: int, canInterrupt?: bool,
                     tokenBudget?: dict }
                   特殊值: text='token_budget' + tokenBudget=<dict> → 前端只更
                   tokenBudget 不更 status 文字

`text`             完整文本块 (非流式; 前端写入 store)
                   { kind, content: str, sessionId? }

`tool_use`         工具调用开始 (LLM 决定调某工具)
                   { kind, toolId: str, toolName: str, input: dict, sessionId? }

`tool_result`      工具执行完返回结果
                   { kind, toolId: str, result?, isError?: bool, exitCode?: int,
                     resultText?: str, sessionId? }

`thinking`         扩展思考块 (Claude thinking content)
                   { kind, content: str, sessionId? }

`interactive_prompt`  LLM 反问用户 (上游交互场景)
                      { kind, sessionId? }

`task_notification`  跨 session 异步通知 (例 长跑任务完成提醒)
                     { kind, sessionId? }

`context_event`   OmniChat 控制面通知 (plan/context/goal 注入状态)
                  { kind, status?: str, summary?: str, context?: dict, planId?: str }

后续扩展
========

新 kind 加之前**必先**改 [`useChatRealtimeHandlers.ts`](../../../frontend/src/components/chat/hooks/useChatRealtimeHandlers.ts)
对应 case + 本文件的 TypedDict + [`docs/standards/protocol/normalized_message.md`](../../../../../docs/standards/protocol/normalized_message.md).
单边加协议字段会导致前后端 drift.

各 provider 子类不允许定义自己专属的 kind — 如果有 SDK 特殊事件, 必须先抽象到本
协议中通用 kind, 然后所有 provider 都遵守.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict


# ── kind 全集 (Literal 形式, 便于类型检查) ───────────────────────────────────

NormalizedMessageKind = Literal[
    "stream_delta",
    "stream_end",
    "session_created",
    "complete",
    "error",
    "permission_request",
    "permission_cancelled",
    "status",
    "text",
    "tool_use",
    "tool_result",
    "thinking",
    "interactive_prompt",
    "task_notification",
    "context_event",
]


# ── 各 kind 的 TypedDict (total=False 让 sessionId / provider 等可选) ────────

class _BaseFields(TypedDict, total=False):
    """所有 NormalizedMessage 都可带的可选字段."""
    sessionId: str | None
    provider: str | None


class StreamDeltaMessage(_BaseFields):
    kind: Literal["stream_delta"]
    content: str


class StreamEndMessage(_BaseFields):
    kind: Literal["stream_end"]


class SessionCreatedMessage(_BaseFields):
    kind: Literal["session_created"]
    newSessionId: str


class CompleteMessage(_BaseFields, total=False):
    kind: Literal["complete"]
    aborted: bool
    actualSessionId: str
    exitCode: int


class ErrorMessage(_BaseFields, total=False):
    kind: Literal["error"]
    error: str


class PermissionRequestMessage(_BaseFields, total=False):
    kind: Literal["permission_request"]
    requestId: str
    toolName: str
    input: Any
    context: Any


class PermissionCancelledMessage(_BaseFields):
    kind: Literal["permission_cancelled"]
    requestId: str


class StatusMessage(_BaseFields, total=False):
    kind: Literal["status"]
    text: str
    tokens: int
    canInterrupt: bool
    tokenBudget: dict[str, Any]


class TextMessage(_BaseFields):
    kind: Literal["text"]
    content: str


class ToolUseMessage(_BaseFields, total=False):
    kind: Literal["tool_use"]
    toolId: str
    toolName: str
    input: dict[str, Any]


class ToolResultMessage(_BaseFields, total=False):
    kind: Literal["tool_result"]
    toolId: str
    result: Any
    isError: bool
    exitCode: int
    resultText: str


class ThinkingMessage(_BaseFields):
    kind: Literal["thinking"]
    content: str


class InteractivePromptMessage(_BaseFields):
    kind: Literal["interactive_prompt"]


class TaskNotificationMessage(_BaseFields):
    kind: Literal["task_notification"]


class ContextEventMessage(_BaseFields, total=False):
    kind: Literal["context_event"]
    status: str
    summary: str
    context: dict[str, Any]
    planId: str | None


# ── 联合类型 — 所有 NormalizedMessage 必为以下之一 ────────────────────────────

NormalizedMessage = (
    StreamDeltaMessage
    | StreamEndMessage
    | SessionCreatedMessage
    | CompleteMessage
    | ErrorMessage
    | PermissionRequestMessage
    | PermissionCancelledMessage
    | StatusMessage
    | TextMessage
    | ToolUseMessage
    | ToolResultMessage
    | ThinkingMessage
    | InteractivePromptMessage
    | TaskNotificationMessage
    | ContextEventMessage
)


__all__ = [
    "NormalizedMessage",
    "NormalizedMessageKind",
    "StreamDeltaMessage",
    "StreamEndMessage",
    "SessionCreatedMessage",
    "CompleteMessage",
    "ErrorMessage",
    "PermissionRequestMessage",
    "PermissionCancelledMessage",
    "StatusMessage",
    "TextMessage",
    "ToolUseMessage",
    "ToolResultMessage",
    "ThinkingMessage",
    "InteractivePromptMessage",
    "TaskNotificationMessage",
]
