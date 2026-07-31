# [OMNI] origin=ai-ide ts=2026-05-10 type=infra
# [OMNI] material_id="material:dashboard.ccdaemon.providers.base_provider_abc.py"
"""BaseProvider ABC — LLM provider 接口.

跟 [`docs/standards/protocol/normalized_message.md`](../../../../../docs/standards/protocol/normalized_message.md)
配套. 各 provider 子类必须实现五个方法 + 通过 `consume_messages()` async generator
推 NormalizedMessage 给上层 ChatSession.

接口对齐参考 (调研结果)
========================

- claude-agent-sdk (`ClaudeSDKClient`): async + `query(prompt, options)` 返
  `AsyncIterator[Message]`, `interrupt()` 异步可打断
- OpenAI Codex CLI: async stdin/stdout 流式, 无明确"中断"语义 (kill 进程)
- Cursor Agent CLI: 行为待调研 (阶段 6 stub 时填)
- sst/opencode: 行为待调研

最大公约数: async + AsyncIterator + 显式 interrupt(). 同步 SDK 用
`asyncio.to_thread` 包装即可.

为什么 consume_messages 跟 send_prompt 拆开
=============================================

不少 SDK 是"启动会话 → 双向通道 → 流式收消息"模式 (Claude SDK 即如此). send_prompt
只是往通道塞用户输入, 收消息是独立 async loop. ChatSession 在 ws accept 后立即
spawn 一个 consume_task 跑 `async for msg in provider.consume_messages():`, send_prompt
跟 interrupt 是另一些独立 await.

如果 SDK 是"prompt → wait result"形态 (无后台流), 子类可以让 consume_messages 仅在
send_prompt 后才 yield, send_prompt 内部触发 yield.

后续阶段 2 ClaudeProvider 实现细节将填进本文 docstring 作为参考.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, TypedDict

from ..normalized_protocol import NormalizedMessage


class ProviderOptions(TypedDict, total=False):
    """ChatSession 创建时传给 provider 的配置. 字段全部可选, provider 子类按需用.

    通用字段
    --------
    - model: 模型短名 (例 claude 'sonnet' / 'opus' / codex 'gpt-5' 等). 各 provider
      自家命名空间, 不强制统一
    - cwd: 工作目录 (绝对路径). claude-agent-sdk / codex 都支持
    - system_prompt: 系统提示 (会话级)
    - max_turns: 自动循环最大轮数 (-1 = 无限, agent loop 用)
    - permission_mode: 工具调用权限模式 ('default' / 'acceptEdits' / 'auto' /
      'bypassPermissions' / 'plan'). 跟前端 PermissionMode 类型对齐
    - allowed_tools: 工具白名单 (按工具名)
    - disallowed_tools: 工具黑名单
    - env: 子进程环境变量 (provider 启子进程时用)

    provider-specific extras 可以加任意 key, 子类按 key 取值即可. dict 不强约束.
    """
    model: str
    cwd: str
    system_prompt: str
    max_turns: int
    permission_mode: str
    allowed_tools: list[str]
    disallowed_tools: list[str]
    env: dict[str, str]


class BaseProvider(ABC):
    """LLM provider 抽象基类.

    生命周期
    --------
    1. `__init__(options: ProviderOptions)` — 子类构造, 不发起连接
    2. `await connect()` — 真正建连接 / spawn 子进程 / 启 SDK client
    3. `await send_prompt(prompt, options)` — 用户发消息 (可多次调用, multi-turn)
    4. `async for msg in consume_messages():` — 上层独立 task 消费消息流
    5. `await interrupt()` — 打断当前生成 (用户点中断时)
    6. `await disconnect()` — 关闭 / 清理资源

    实现注意
    --------
    - send_prompt 应该是非阻塞的 — 把 prompt 推进 SDK 通道就返回, 不等 LLM 回完
    - consume_messages 是 async generator, 整个 session 期间循环 yield 新消息;
      session 结束 (disconnect 调用 / SDK 自然结束) 时正常退出循环
    - interrupt 应该幂等 — 没在生成时调也不报错
    - disconnect 必须释放所有资源 (子进程 / file handle / 网络连接)
    - 任何异常都应该转为 ErrorMessage 通过 consume_messages yield, 不直接抛出
      (上层用 yield NormalizedMessage 单一通道)
    """

    options: ProviderOptions

    def __init__(self, options: ProviderOptions) -> None:
        self.options = options

    @abstractmethod
    async def connect(self) -> None:
        """建立 provider 连接 (spawn 子进程 / 启 SDK client / 网络握手等)."""
        raise NotImplementedError

    @abstractmethod
    async def send_prompt(self, prompt: str, options: dict[str, Any] | None = None) -> None:
        """用户发消息. 非阻塞 — 推进 SDK 通道就返回, 实际响应通过 consume_messages.

        Args:
            prompt: 用户文本输入
            options: per-turn 选项 (例 thinking 模式, 临时 model override 等). 跟
              ProviderOptions 不同, 这是 turn 级而非 session 级. 字段子类自定义.
        """
        raise NotImplementedError

    @abstractmethod
    async def interrupt(self) -> None:
        """打断当前生成. 幂等 — 没在生成时调用也不报错."""
        raise NotImplementedError

    @abstractmethod
    async def disconnect(self) -> None:
        """释放资源 (子进程 / 连接 / file handle 等). 调用后 provider 不可再用."""
        raise NotImplementedError

    @abstractmethod
    def consume_messages(self) -> AsyncIterator[NormalizedMessage]:
        """Async generator yielding NormalizedMessages.

        实现写法:
            async def consume_messages(self):
                async for raw in self._sdk_client.receive():
                    for nm in self._convert_to_normalized(raw):
                        yield nm

        子类可以根据 SDK 形态调整 (例如同步 SDK 用 `asyncio.to_thread` 包装收消息
        然后 yield).

        注意: 必须是 async generator (`async def consume_messages(self):` 内带 yield),
        不要返回 AsyncIterator 实例 — Python 的 `@abstractmethod` 跟 async generator
        组合略 tricky, 类型签名按 AsyncIterator 写, 子类实现按 async generator 写
        即可被识别为合规实现.
        """
        raise NotImplementedError


__all__ = ["BaseProvider", "ProviderOptions"]
