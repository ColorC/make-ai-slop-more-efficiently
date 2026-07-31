# [OMNI] origin=internal-engine domain=services/repo_learner ts=2026-04-09T12:27:44Z
# Gemini CLI 学习报告

## Learning Value

### 1. 事件驱动 Agent 协议设计

**AgentProtocol** 定义了完整的 agent 生命周期事件类型：`initialize`、`message`、`tool_request`、`tool_response`、`elicitation`、`error`、`complete` 等。每个事件携带结构化数据（如 `ToolRequestEvent` 包含 `toolName`、`args`、`callId`）。协议支持 **trajectory 历史**，可用于会话重放和调试。

**值得借鉴**：事件驱动协议使得 agent 状态变化可观测、可回放，便于实现 checkpointing、replay、debug 等高级功能。相比直接调用 API，事件流模式解耦了状态管理和业务逻辑。

### 2. 三阶段工具执行状态机

工具执行采用 **Validating → Scheduled → Executing → Completed** 四状态流转。`Scheduler` 核心循环：
- **Validating 阶段**：并行处理策略检查 + 用户确认
- **Scheduled 阶段**：批量并行执行（`_isParallelizable` 判断）
- **AwaitingApproval 状态**：等待外部确认时 yield 到事件循环
- **取消机制**：`AbortSignal` + `isCancelling` 标志双重保护

**值得借鉴**：状态机模式让工具执行过程可追踪、可中断。批量并行优化提升了多工具调用场景的性能。`queueMicrotask` 让出控制权的设计避免了阻塞事件循环。

### 3. 权限策略引擎与通配符匹配

`PolicyEngine` 实现了灵活的权限系统：
- **规则匹配**：支持 `mcp_server_*` 通配符匹配 MCP 工具
- **权限合并**：`persistentPermissions` + `additionalPermissions` 层叠合并
- **模式切换**：`AutoEdit` 模式可自动将 `ask` 转为 `autoModify`
- **安全检查器**：`registerSecurityChecker` 支持注册自定义安全检查逻辑

**值得借鉴**：权限系统支持通配符和继承，便于配置复杂权限策略。安全检查器注入点允许自定义安全逻辑而不侵入核心代码。

### 4. 跨平台沙箱隔离方案

三个平台三种隔离技术：
- **Linux**：`bwrap` (Bubblewrap) + `seccomp` BPF 过滤器，动态生成 `ptrace` 阻止规则
- **macOS**：`Seatbelt` 配置构建器，支持 `temp-no-delete`、`fs-read-only` 等规则
- **Windows**：`Restricted Token` + `Job Object` + `Low Integrity` SID，C# helper 编译

共同接口 `SandboxManager`：`prepareCommand`、`isKnownSafeCommand`、`isDangerousCommand`、`parseDenials`。

**值得借鉴**：抽象层设计使得平台特定实现可插拔。`parseDenials` 统一处理沙箱拒绝信息，便于友好错误提示。虚拟命令（`__read`/`__write`）转换模式简化了文件操作权限控制。

### 5. 双通道确认流程与外部编辑循环

`resolveConfirmation` 实现异步确认：
- **竞速机制**：`Promise.race([waitForConfirmation, ideModifyPromise])`
- **修改循环**：`ModifyWithEditor` 状态机循环等待外部编辑器修改后重新提交
- **超时保护**：`AbortSignal` 超时自动取消确认等待
- **MessageBus derive**：子 agent 可继承父作用域消息总线，权限隔离

**值得借鉴**：双通道设计支持 CLI 和 IDE 两种交互模式。外部编辑循环让用户可修改工具参数后再执行，增强了安全性和灵活性。

### 6. 上下文压缩与 Grace Zone 机制

`AgentHistoryProvider` 管理 token 预算：
- **Grace Zone**：最近 N 条消息使用 `maximumMessageTokens` 限制，超出部分使用 `normalMessageTokens`
- **比例压缩**：`truncateProportionally` 按 `headRatio` 保留头部信息
- **结构完整性**：压缩时保持 `functionCall`/`functionResponse` 配对不拆分
- **摘要生成**：超出 `retainedTokens` 的历史生成 LLM 摘要替代

**值得借鉴**：Grace Zone 保护最近对话不被过度压缩。比例压缩算法简单有效，比直接截断保留了更多上下文。

### 7. 钩子系统与生命周期事件

`HookType` 分为 `Command`（shell 脚本）和 `Runtime`（函数）两种。事件类型：
- **BeforeTool / AfterTool**：工具执行前后
- **BeforeAgent / AfterAgent**：agent 会话生命周期
- **SessionStart / SessionEnd**：会话级别
- **PreCompress**：上下文压缩前
- **BeforeModel / AfterModel**：LLM 调用前后

`evaluateBeforeToolHook` 可阻止工具执行、强制用户确认、修改工具参数。

**值得借鉴**：钩子系统提供了扩展点，可在不修改核心逻辑的情况下注入自定义行为。`BeforeModel` 钩子可用于 prompt 注入或过滤。

### 8. Agent 定义验证与远程 Agent 支持

`AgentLoader` 使用 Zod 验证 agent 定义：
- **Local Agent**：`kind: 'local'`，支持 `tools`、`mcp_servers`、`model`、`temperature` 配置
- **Remote Agent**：`kind: 'remote'`，通过 `agent_card_url` 或 `agent_card_json` 发现
- **认证配置**：`apiKey`、`http`（Bearer/Basic）、`google-credentials`、`oauth` 四种方式
- **工具过滤**：`include_tools` / `exclude_tools` 精细控制

**值得借鉴**：Schema 验证确保配置正确性，避免运行时错误。远程 agent 发现机制支持分布式 agent 协作。

### 9. MCP 工具命名规范与权限控制

MCP 工具命名格式：`mcp_{server_name}_{tool_name}`
- **解析**：`parseMcpToolName` 提取 server 和 tool 名
- **格式化**：`formatMcpToolName` 支持通配符 `mcp_*`、`mcp_server_*`
- **注释元数据**：`McpToolAnnotation` 携带 `_serverName`
- **Allowlist**：`DiscoveredMCPToolInvocation` 维护允许列表

**值得借鉴**：命名规范使得策略匹配简单直观。通配符支持简化了批量权限配置。

### 10. 错误处理与取消机制

- **AbortSignal 传播**：所有异步操作接受 `AbortSignal`，支持级联取消
- **错误类型**：`ToolErrorType` 枚举区分 `TIMEOUT`、`SANDBOX_DENIED`、`POLICY_VIOLATION` 等
- **重试逻辑**：工具执行失败可配置重试策略
- **优雅降级**：沙箱不可用时回退到普通执行

**值得借鉴**：结构化错误类型便于错误处理和用户提示。`AbortSignal` 传播是现代异步取消的标准做法。

---

## Learning Locations

| 文件位置 | 一句话定位 |
|---------|----------|
| `packages/core/src/agent/types.ts:1-200` | AgentProtocol 事件类型定义，包含 initialize/message/tool_request/tool_response/elicitation 等完整事件 |
| `packages/core/src/agent/agent-session.ts:1-200` | AgentSession 封装 AgentProtocol，提供 AsyncIterable 事件流 API |
| `packages/core/src/scheduler/scheduler.ts:410-560` | Scheduler 核心循环：Validating → Scheduled → Executing 状态流转，批量并行执行优化 |
| `packages/core/src/scheduler/confirmation.ts:1-150` | resolveConfirmation 异步确认循环，ModifyWithEditor 外部编辑状态机 |
| `packages/core/src/policy/policy-engine.ts:1-200` | PolicyEngine 权限匹配引擎，支持 MCP 工具通配符、权限合并、安全检查器注册 |
| `packages/core/src/sandbox/linux/LinuxSandboxManager.ts:1-200` | Linux 沙箱：bwrap + seccomp BPF 过滤器，动态生成 ptrace 阻止规则 |
| `packages/core/src/sandbox/macos/MacOsSandboxManager.ts:1-150` | macOS 沙箱：Seatbelt 配置构建器，虚拟命令转换 |
| `packages/core/src/sandbox/windows/WindowsSandboxManager.ts:1-150` | Windows 沙箱：Restricted Token + Job Object + Low Integrity 隔离 |
| `packages/core/src/services/sandboxManager.ts:1-100` | SandboxManager 接口定义，跨平台沙箱抽象层 |
| `packages/core/src/tools/tools.ts:1-200` | ToolInvocation 三阶段抽象：getDescription/shouldConfirmExecute/execute |
| `packages/core/src/tools/mcp-tool.ts:1-200` | DiscoveredMCPTool 实现：命名规范、allowlist、MCP 内容块类型 |
| `packages/core/src/context/agentHistoryProvider.ts:1-200` | AgentHistoryProvider：Grace Zone 机制、比例压缩、结构完整性保留 |
| `packages/core/src/hooks/types.ts:1-100` | HookType 和 HookEventName 定义，完整生命周期事件 |
| `packages/core/src/confirmation-bus/message-bus.ts:1-150` | MessageBus：publish 集成 PolicyEngine 检查，derive 创建子作用域 |
| `packages/core/src/agents/agentLoader.ts:1-200` | AgentLoader：Zod 验证 local/remote agent 定义，支持多种认证方式 |