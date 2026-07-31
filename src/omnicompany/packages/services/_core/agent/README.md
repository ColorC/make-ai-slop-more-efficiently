<!-- [OMNI] origin=codex domain=services/agent ts=2026-07-29T00:00:00Z type=doc status=active agent=codex belongs_to_service=agent -->
<!-- [OMNI] summary="Omnicompany 唯一内部 Agent 运行时及 Pi 0.82.1 行为对齐契约" -->
<!-- [OMNI] tags=readme,agent,eventbus,pi,conformance -->
<!-- [OMNI] material_id="material:services._core.agent.readme.self_narrative.md" -->

# Omnicompany 内部 Agent

Omnicompany 只有一个内部 Agent 运行时：

```text
OmniNativeWorkspaceWorker
  -> MaterialWorkspaceAgent
  -> AgentNodeLoop
  -> Omnicompany EventBus
```

Pi 不是第二套 Agent、启动入口、工具实现或安全权威。仓库把
`@earendil-works/pi-coding-agent@0.82.1` 固定为行为规范和一致性 oracle，
由 conformance 测试锁定模型可见的系统 prompt、上下文文件顺序、默认工具
schema、工具结果语义、并行调用顺序、steering/follow-up、终止、重试与压缩
行为。生产运行不会启动 Pi 进程，也不存在 Pi bridge。

## 唯一模型可见契约

`MaterialWorkspaceAgent` 是 workspace Agent 的 canonical 入口。默认只向模型
公开与锁定版 Pi 相同的四个工具：

- `read`
- `bash`
- `edit`
- `write`

这些工具在 Omnicompany 内部通过 Router、权限门和 EventBus 执行。总线事件、
审计字段和安全控制属于实现层，不改变模型可见内容。

关键实现：

- [material_workspace.py](material_workspace.py)：唯一 workspace Agent 组合入口。
- [pi_behavior.py](pi_behavior.py)：锁定版 Pi 的模型可见 prompt 与工具契约。
- [model_visible_contract.py](model_visible_contract.py)：canonical bytes 与 digest。
- [routers/pi_tools.py](routers/pi_tools.py)：四个 canonical 工具的总线实现。
- [routers/pi_context.py](routers/pi_context.py)：Pi 对齐的上下文与压缩语义。
- [loop.py](loop.py)：唯一 Agent 状态机及 Pi lifecycle 兼容事件。

## Runtime profile

- `native_bus`：canonical profile，选择唯一的 `omni-native` engine。
- `stable_pi`：历史兼容名称，仍选择同一个 `omni-native` engine；它不是另一套
  harness。
- `compat`：显式外部 CLI 兼容入口，可选择 OpenCode、Codex、Claude Code 或
  Kimi。它们是边界适配器，不是 Omnicompany 内部基座 Agent，也不会被静默
  fallback。

`--model-provider the_company` 直接使用 Omnicompany 统一 `LLMClient` 和 the_company
OpenAI-compatible endpoint，不经过 Pi extension 或子进程。

```powershell
omni worker run --runtime-profile native_bus --model-provider the_company --model gpt-5.6-terra --prompt "完成并验证任务" --cwd .
omni worker run --runtime-profile stable_pi --model-provider the_company --model gpt-5.6-terra --prompt "完成并验证任务" --cwd .
omni worker run opencode --runtime-profile compat --prompt "执行兼容性对照" --cwd .
```

## EventBus 与审计

每次 `AgentNodeLoop` 运行都必须接入 EventBus。Router 输入/输出、模型调用、工具
调用、Pi lifecycle 兼容事件和最终结果共享同一 `trace_id`，可观察状态写入
`data/_runtime/agent_observability/<trace_id>.json`。

EventBus 是状态、权限和审计的唯一实现权威；Pi 只定义锁定版本的外部可观察
行为。升级 Pi 版本必须先更新 fixture 与 conformance 证明，不得在运行时旁挂
第二个 Agent。

## 验证

核心一致性测试位于
`tests/agent_tools/test_pi_runtime_profiles.py`，覆盖：

- 两个内部 profile 指向同一 engine；
- 系统 prompt 和四个工具 schema 的精确哈希；
- 工具错误、并行执行和按请求顺序回填；
- lifecycle、steering、follow-up 与长度终止；
- 上下文溢出压缩及重试；
- the_company 作为模型 provider 时不改变模型可见契约。
