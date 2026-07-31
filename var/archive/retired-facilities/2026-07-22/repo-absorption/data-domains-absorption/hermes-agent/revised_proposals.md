# [OMNI] origin=unknown domain=absorption ts=2026-04-17T06:01:52Z
# 修订版提案 — hermes-agent

**修订总结**: 本次修订响应人类审阅的 4 条异议：(1) 修正 PRO-001 状态从"缺失"改为"已有可改进"，因为 Omnicompany 已有 RateLimiter + 指数退避，真正独特的是 error_classifier.py 将错误分类到恢复策略 flag 的流水线；(2) 扩充 PRO-002，明确 8 种 memory provider 实现与 12 个生命周期钩子（prefetch/sync_turn/on_pre_compress/on_session_end/on_delegation 等）；(3) 新增 PRO-008 自学习闭环（trajectory→insights→skill_manager，对标 crystallize，P0）；(4) 新增 PRO-009 子 Agent 委托架构（delegate_tool.py 的 parent→child 并发委托+工具限制，P0）；(5) 新增 PRO-010 向量符号架构记忆（HRR 相位编码与组合检索，从 PRO-002 拆分独立提案，P0）。

共 10 条：

| ID | 标题 | 优先级 | status | 变化 |
|---|---|---|---|---|
| PRO-001 | 统一错误分类与结构化恢复策略 | P0 | 已有可改进 | revised |
| PRO-002 | 可插拔记忆提供者抽象架构 | P0 | 缺失 | revised |
| PRO-003 | 工具运行时安全沙箱与边界防护 | P1 | 缺失 | unchanged |
| PRO-004 | 外部服务凭证高可用池管理 | P1 | 部分存在 | unchanged |
| PRO-005 | 指数退避调度器引入抖动机制 | P2 | 部分存在 | unchanged |
| PRO-006 | Agent自治技能生命周期与安全管控 | P1 | 缺失 | unchanged |
| PRO-007 | 多端流式输出适配与动态投递路由 | P1 | 部分存在 | unchanged |
| PRO-008 | Agent自学习闭环 — 轨迹记录→会话分析→技能沉淀 | P0 | 缺失 | new |
| PRO-009 | 子Agent委托架构 — 并发委托+工具继承限制 | P0 | 缺失 | new |
| PRO-010 | 向量符号架构记忆 — HRR 相位编码与组合检索 | P0 | 缺失 | new |

---

## PRO-001: 统一错误分类与结构化恢复策略

**优先级**: P0 | **status**: 已有可改进 | **变化**: revised

**摘要**: 将运行时异常映射到结构化恢复语义（rotate_key / backoff / compress / fallback），替代碎片化 try/except。Omnicompany 已有 RateLimiter + 指数退避，真正缺口是把错误分类到恢复策略 flag 的流水线。

**理由**: hermes 的 error_classifier.py 用 FailoverReason 枚举将 15+ 类错误映射为 retryable/should_compress/should_rotate_credential 等恢复标志，使主循环可执行精准恢复而非盲目重试。Omnicompany 当前只有基础重试，缺乏错误语义→恢复动作的结构化映射。

**hermes 参考**: agent/error_classifier.py:43-80 (FailoverReason 枚举 + ClassifiedError dataclass with recovery flags); agent/error_classifier.py:231+ (classify_api_error pipeline)

## PRO-002: 可插拔记忆提供者抽象架构

**优先级**: P0 | **status**: 缺失 | **变化**: revised

**摘要**: 设计统一记忆后端 ABC 接口与单实例激活管控，支持 8 种插件实现（byterover/hindsight/holographic/honcho/mem0/openviking/retaindb/supermemory）及 12+ 生命周期钩子（initialize/prefetch/sync_turn/on_pre_compress/on_session_end/on_delegation 等）。

**理由**: hermes 的 memory_provider.py 定义了包含 mandatory (initialize/is_available/get_tool_schemas) 和 optional (prefetch/sync_turn/on_turn_start/on_session_end/on_pre_compress/on_memory_write/on_delegation/queue_prefetch) 钩子的完整 ABC。MemoryManager 强制仅一个外部 provider 激活，避免工具 schema 膨胀。这是 Omnicompany 完全缺失的记忆插件架构。

**hermes 参考**: agent/memory_provider.py:1-231 (MemoryProvider ABC with 12 lifecycle hooks); plugins/memory/ (8 provider implementations); agent/memory_provider.py:144-187 (optional hooks: on_turn_start/on_session_end/on_pre_compress/on_delegation)

## PRO-003: 工具运行时安全沙箱与边界防护

**优先级**: P1 | **status**: 缺失 | **变化**: unchanged

**摘要**: 构建动态命令拦截与执行隔离沙箱，对高危文件操作、网络请求进行实时权限校验与输出截断，防止越权写入与 OOM。

**理由**: hermes 通过 detect_dangerous_command() 进行命令级模式匹配，配合进程隔离（process_registry.py）与写入沙箱（skills_guard.py）构建多层防护。Omnicompany 缺乏运行时动态拦截能力。

**hermes 参考**: tools/skills_guard.py; tools/file_operations.py; tools/process_registry.py

## PRO-004: 外部服务凭证高可用池管理

**优先级**: P1 | **status**: 部分存在 | **变化**: unchanged

**摘要**: 实现多源凭证注入、策略路由与租约锁同步机制，支持单用令牌的自动轮换与故障隔离，消除单点凭证失效导致的链路中断。

**理由**: hermes 的 credential_pool.py 实现多凭证池化、健康探测轮询与租约锁定，Omnicompany 有基础凭证管理但缺少池化与自动轮换机制。

**hermes 参考**: agent/credential_pool.py

## PRO-005: 指数退避调度器引入抖动机制

**优先级**: P2 | **status**: 部分存在 | **变化**: unchanged

**摘要**: 在现有重试机制基础上增加随机抖动与去相关算法，避免高并发场景下固定延迟重试引发第三方服务限流雪崩。

**理由**: Omnicompany 已有 RateLimiter + 指数退避，但缺少抖动去相关。hermes 的 retry_utils.py 补充了按端点独立配置的退避策略，防止重试风暴。

**hermes 参考**: agent/retry_utils.py

## PRO-006: Agent自治技能生命周期与安全管控

**优先级**: P1 | **status**: 缺失 | **变化**: unchanged

**摘要**: 提供智能体自主创建、修补与注册新技能的标准通道，配套安装前静态安全扫描与分级信任策略，使智能体具备自演进能力同时通过安全护栏控制边界。

**理由**: hermes 的 skill_manager_tool.py 支持 agent 执行 create/edit/patch/delete/write_file 操作，每条写入后触发 skills_guard.py 的静态安全扫描（依赖分析/危险操作检测），并基于信任等级分级授权。这是 Omnicompany 完全缺失的自扩展通道。

**hermes 参考**: tools/skill_manager_tool.py:1-761 (skill CRUD with security scanning); tools/skills_guard.py (scan_skill/should_allow_install/format_scan_report)

## PRO-007: 多端流式输出适配与动态投递路由

**优先级**: P1 | **status**: 部分存在 | **变化**: unchanged

**摘要**: 建立标准化协议网关与跨平台流式桥接层，支持同步回调转异步缓冲、渐进式渲染与目标通道动态解析，解耦底层推理与前端交互。

**理由**: hermes 的 MCP Server + stream_consumer + delivery_router 实现多端接入与流式消息可靠转发，Omnicompany 有基础网关但缺少跨平台流式桥接与动态路由能力。

**hermes 参考**: mcp_serve.py; gateway/stream_consumer.py; cron/scheduler.py

## PRO-008: Agent自学习闭环 — 轨迹记录→会话分析→技能沉淀

**优先级**: P0 | **status**: 缺失 | **变化**: new

**摘要**: 构建完整的'执行→记录→分析→沉淀→复用'自学习循环：trajectory.py 持久化执行轨迹，insights.py 分析会话指标（token/成本/工具使用/活跃趋势），skill_manager_tool.py 将成功经验固化为可复用技能。与 Omnicompany 的 crystallize 主轴直接对标。

**理由**: hermes 的三个文件构成闭环：trajectory.py 将执行轨迹以 ShareGPT 格式写入 JSONL（区分成功/失败）；insights.py 从 SQLite 会话数据库聚合 token 消耗、成本估算、工具调用模式、活跃趋势、模型/平台分布；skill_manager_tool.py 允许 agent 将经过验证的方法沉淀为结构化技能（SKILL.md）。Omnicompany 完全没有自学习与能力沉淀机制，这是架构级能力缺口。

**hermes 参考**: agent/trajectory.py:30-56 (save_trajectory: JSONL persistence with completed/failed routing); agent/insights.py:93-161 (InsightsEngine: token/cost/tool/activity analysis from SQLite); tools/skill_manager_tool.py:1-761 (agent-managed skill CRUD with YAML frontmatter validation)

## PRO-009: 子Agent委托架构 — 并发委托+工具继承限制

**优先级**: P0 | **status**: 缺失 | **变化**: new

**摘要**: 实现 parent→child agent 的任务委托机制：子 agent 拥有隔离上下文、受限工具集、独立会话，父 agent 阻塞等待并仅接收摘要结果。支持单任务与批量并发模式，防止递归委托与权限逃逸。

**理由**: hermes 的 delegate_tool.py (1103 行) 实现了完整的子 agent 委托架构：通过 ThreadPoolExecutor 实现并发子任务；DELEGATE_BLOCKED_TOOLS 禁止子 agent 访问 delegate_task/clarify/memory/send_message/execute_code；_strip_blocked_tools 确保子 agent 工具集不超过父 agent 权限；MAX_DEPTH=2 防止递归委托爆炸；子 agent 独立 session_id 与上下文，父 agent 仅看到委托调用与摘要结果。Omnicompany 是单线 pipeline，完全没有委托/并发子任务机制。

**hermes 参考**: tools/delegate_tool.py:1-1103 (subagent architecture with ThreadPoolExecutor, blocked tools list, depth limiting, progress callback batching, credential override routing)

## PRO-010: 向量符号架构记忆 — HRR 相位编码与组合检索

**优先级**: P0 | **status**: 缺失 | **变化**: new

**摘要**: 基于 Holographic Reduced Representations (HRR) 实现向量符号记忆：用相位向量编码概念（bind=相位加法关联, unbind=相位减法检索, bundle=叠加合并），支持组合结构固定宽度分布式表示与确定性原子生成。

**理由**: hermes 的 holographic memory 插件实现了 Plate (1995) 的 HRR 理论：用 SHA-256 确定性生成相位向量原子，通过 bind/unbind/bundle 三种代数操作实现概念的组合编码与检索。这不同于传统向量检索（余弦相似度），能在固定维度内保持组合结构关系，适合知识图谱式联想记忆。Omnicompany 没有任何向量符号架构能力。

**hermes 参考**: plugins/memory/holographic/holographic.py:1-99 (HRR: encode_atom via SHA-256, bind=phase addition, unbind=phase subtraction, bundle=circular mean); plugins/memory/holographic/retrieval.py; plugins/memory/holographic/store.py
