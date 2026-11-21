<!-- [OMNI] origin=claude-code domain=bus ts=2026-04-25T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:bus.system_design.specification.md" -->

# bus · 设计文档

## 状态
- **版本**: V1 (骨架填充完成，确认多实现并存且稳定运行)
- **成熟度**: active
- **下一步**: 将 `EventBusAuditEmitter` 完全接入本包 (`runtime` 层取代 `LocalJsonlEmitter`)，并推进 `NetworkBus` gRPC 草案实现 (Phase 1)

## 核心目的
提供进程内统一的**事件发布/订阅抽象层** (`EventBus`) 与多种传输实现，支撑 Agent 任务轨迹观测、审计落盘与跨组件解耦。
**解决**：
- 统一事件接口：上层业务只依赖 `EventBus` ABC，屏蔽 SQLite/Redis/内存差异。
- 零依赖默认持久化：`SQLiteBus` 开箱即用，单文件落盘，崩溃不丢事件，支持 SQL 回放。
- 测试/开发敏捷：`MemoryBus` 提供短生命周期沙箱；`OmniBusClient` 提供 Redis Streams 分布式消费组支持。
**不解决**：
- 调度/同步原语：不提供 barrier、latch、DAG 聚合等待（EventBus 是日志流，非协调器）。
- 复杂消息路由/转换：不负责 Payload 路由分发（那是 `routing` 或 `runtime` 层的职责）。
- 强一致分布式事务：仅保证投递与顺序，不涵盖跨节点 ACID。

## 核心接口
- **`EventBus` (抽象基类)** — [base.py](base.py)
  - `async def connect() / close()` — 生命周期管理 (支持 `async with`)
  - `async def publish(event: FactoryEvent) -> str` — 发布事件，返回唯一 ID
  - `async def subscribe(group: str, consumer: str, *, event_types, tags) -> AsyncIterator[FactoryEvent]` — 订阅流 (竞争消费组语义)
  - `async def ack(event: FactoryEvent) -> None` / `read_trace(trace_id) -> list` — 消费确认与回溯查询
- **`SQLiteBus(EventBus)`** — [sqlite.py](sqlite.py)
  - 零外部依赖默认实现，支持 AND 语义标签过滤、SQL 级回放与持久化消费位移。
- **`OmniBusClient`** — [client.py](client.py)
  - Redis Streams 客户端，双写策略 (`omnicompany:trace:{id}` + `omnicompany:global`)，内置消费者组与自动 `max_stream_len` 裁剪。
- **`MemoryBus(EventBus)`** — [memory.py](memory.py)
  - 纯内存环形缓冲区实现，用于单元测试、CI 与短生命周期 DAG 执行器 (`TeamRunner`)。

## 架构决策
### D1 · 抽象与实现分离：上层仅依赖 `EventBus` ABC
**决策**: 所有具体总线 (`SQLiteBus`, `MemoryBus`, 未来的 `RedisBus`/`NetworkBus`) 继承 `EventBus`，业务代码通过 DI 注入，禁止 `import sqlite_bus` 直连。
**理由**: 隔离传输细节，使审计、观测管线可在不同环境（单测内存/开发 SQLite/生产 Redis）无缝切换；符合依赖倒置，便于后续迁移至 `runtime` 审计底座。

### D2 · 零依赖默认持久化采用 SQLite 而非文件日志
**决策**: `SQLiteBus` 作为框架默认落盘实现，使用单文件 `data/events.db` (或 `audit.db`)，而非 JSONL/CSV 文本流。
**理由**: SQLite 是 Python 标准库，无需额外运维；原生支持 SQL 查询、标签 AND 过滤、消费组位移持久化，轻松支撑 1000+ EPS，远超 Agent 场景需求，且提供进程崩溃后的强一致性恢复。

### D3 · Redis 客户端采用双写策略 (`trace` + `global`)
**决策**: `OmniBusClient` 发布时同步写入 `omnicompany:trace:{trace_id}` (任务隔离流) 与 `omnicompany:global` (全局镜像流)。
**理由**: `trace` 流保证单任务事件严格有序且可按 ID 快速检索；`global` 流供监控/审计组件全量消费。双写由客户端透明完成，消费者按需订阅，兼顾隔离性与全局可观测性。

### D4 · 明确 EventBus 边界：是事件日志，非分布式协调器
**决策**: 不实现 barrier、latch、countdown 或 "等 N 个事件到齐" 原语；`read_trace` 为阻塞查询，非流式聚合。
**理由**: 计划 `CROSS-LANG-NETWORK-FACTORY` 明确 EventBus 仅用于事件记录与轨迹回放。调度/并发控制应由 `runtime` 或 DAG 引擎负责，保持总线轻量，避免状态爆炸与死锁风险。

### D5 · 消费组语义：组内竞争，组间独立
**决策**: `subscribe` 强制传入 `group` 和 `consumer`，同组内同一事件仅由一个消费者处理（SQLite 位移表 / Redis Consumer Group 保证），不同组可独立消费全量事件。
**理由**: 满足多模块并行监听的需求（如 `audit` 组存盘、`guardian` 组巡检、`tracing` 组上报），且保证同一业务逻辑的 Worker 集群横向扩展时不会重复处理同一事件。

### D6 · 审计底座迁移：EventBus 作为其他 Bus 的终点
**决策**: 按照 `SELF-STABLE-CORE` 计划，旧 `events.py` 移至 `runtime/buses/event_bus.py` 并留 shim；`EventBus` 自身不再接收上游总线数据，仅作为所有审计事件的最终落盘层。
**理由**: 统一审计终点，消除散落的数据源。过渡期保留 `LocalJsonlEmitter` 应急，稳定后全量切换至 `SQLiteBus`，确保架构分层清晰（`bus` 传数据 → `runtime` 收数据 → `data/` 落盘）。

## 数据流 / 拓扑
```
[上游组件] (Agent / Worker / Router)
     │ publish(FactoryEvent)
     ▼
┌─────────────────────────────────────┐
│           EventBus 抽象层           │
│  (EventBus ABC + DI 注入)           │
└───────────────┬─────────────────────┘
                │ 多态路由 (根据实例类型)
      ┌─────────┼─────────┬────────────┐
      ▼         ▼         ▼            ▼
[MemoryBus] [SQLiteBus] [OmniBus]  [NetworkBus*]
 (测试/沙箱)  (默认落盘)  (Redis流)    (未来:gRPC)
      │         │          │
      ▼         ▼          ▼
┌─────────┐┌──────────┐┌──────────────┐
│ 内存 List││ data/db  ││ trace:{id}   │
│         ││ + 位移表 ││ + global 流  │
└─────────┘└──────────┘└──────────────┘
      ▲         ▲          ▲
      └─────────┼──────────┘
                │ subscribe(group, consumer)
                ▼
[下游消费者] (Audit / Guardian / Tracing / Dashboard)
```
**调用链**：业务代码调用 `bus.publish(event)` → ABC 路由至具体实现 → 异步写入目标存储（SQLite/Redis/Memory）→ 返回 `event_id`。
**消费链**：下游通过 `async for event in bus.subscribe("audit", "worker-1", event_types=["task.*"])` 拉取流 → 处理完成调用 `bus.ack(event)` → 更新位移/标记已消费。

## 已知局限
- **局限 1**: `MemoryBus` 不支持真正的消费者位移与 ACK，`subscribe` 仅遍历当前内存列表，长生命周期或高并发测试可能丢失事件/重复消费。· **升级路径**: 引入 `collections.deque` 限定最大长度并维护简单的消费者游标字典；若测试需求增长，直接改用 `SQLiteBus` 的 `:memory:` 模式，复用完整持久化逻辑。
- **局限 2**: `OmniBusClient` 的双写策略非事务性，若 Redis 连接在 `trace` 写入后、`global` 写入前断开，会导致全局流缺失该事件。· **升级路径**: 改用 Redis `EVAL` 脚本原子执行双写 `XADD`，或引入本地 WAL 缓冲层在断连期间暂存并重试，保证最终一致性。
- **局限 3**: `SQLiteBus` 为单写多读模型，高并发 `publish` 依赖 Python `sqlite3` 内置锁，虽满足 1000 EPS 但无法线性扩展至万级吞吐。· **升级路径**: 生产高吞吐场景切换至 `OmniBusClient` (Redis Cluster 模式) 或计划中的 `NetworkBus`，`SQLiteBus` 降级为轻量级调试/边缘设备专用实现。

## 参考资料
- 核心源码: [base.py](base.py), [sqlite.py](sqlite.py), [client.py](client.py), [memory.py](memory.py), [__init__.py](__init__.py)
- 关联计划: `docs/plans/[2026-04-23]SELF-STABLE-CORE/plan.md` (§A1.2 Event Bus 迁移 / 审计底座)
- 架构调研: `docs/plans/_archive/[2026-04-04]CROSS-LANG-NETWORK-FACTORY/findings-dag-support.md` (EventBus 边界定义 / 同步原语缺失)
- 关联实现: `docs/plans/_archive/[2026-04-05]CROSS-NETWORK-INTEROP/implementation-roadmap.md` (NetworkBus Phase 1 蓝图)
- 规范引用: `docs/standards/distributed-docs.md` (基础设施模块设计归属 `src/omnicompany/bus/DESIGN.md`)

## 接收意愿
- **接收**: 新传输协议实现 (如 `NetworkBus` gRPC, `PulsarBus`), 针对现有实现的性能优化 (批量写入/连接池), 消费者组位移的原子性修复。
- **不接收**: 业务逻辑侵入 (如特定 Agent 的 Prompt 格式化), 跨层干预 (如直接修改 `protocol.events` 结构), 调度/同步原语 (barrier/latch 应归属 `runtime` 或 `core` 调度器)。
- **边界信号**: 若实现开始依赖 `services/` 下的业务模型、或在 `bus` 内实现 DAG 状态机/等待超时重试逻辑，则已越界，应拆分至独立包或移回 `runtime`。