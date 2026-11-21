<!-- [OMNI] origin=claude-code domain=tracing ts=2026-04-25T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:tracing.intent_tracer_design.specification.md" -->

# tracing · 设计文档

## 状态
- **版本**: V1 (2026-04-25 初始稳定版 + 权威路径迁移)
- **成熟度**: active
- **下一步**: 引入异步队列缓冲写入以解除 SQLite 锁竞争，并增加基于会话生命周期的数据自动清理策略。

## 核心目的
本包提供**意图轨迹采集与类型约束校验**能力，专注于记录每次 LLM 工具调用的语义意图（`input_types` / `output_types` / `action_class` / `desc`）。
解决：
- 统一收集工具调用的六元语义信号，落盘至独立 SQLite 表供审计/调试/回放。
- 运行时追踪“持有类型集合”，自动识别 `input_types` 缺失的违规步骤，输出幻觉/越权调用信号。
不解决：
- 不处理分布式 TraceID 传播（如 OpenTelemetry Span/Trace），仅提供业务语义层轨迹。
- 不干预工具执行逻辑，仅为旁路记录器（Observer）。
- 不替代结构化日志（logging），专注语义流式快照。

## 核心接口
- **`IntentTracer`** (类) — 意图轨迹采集器核心类。提供 `record(event: FactoryEvent, tool_name: str, session_id: str, intent: dict) -> None` 方法，负责校验、合并持有集、写入 DB。源码：[intent_tracer.py](intent_tracer.py)
- **`intent_tracer._SCHEMA`** (字符串常量) — `intent_steps` 表的建表 DDL，包含 `id`, `ts_utc`, `action_class`, `desc`, `input_types`, `output_types`, `violations`, `held_types_snapshot` 等字段。源码：[intent_tracer.py](intent_tracer.py)
- **`omnicompany.tracing.IntentTracer`** (包级导出) — 唯一公开导入入口。源码：[`__init__.py`](__init__.py)

## 架构决策
### D1 · 采用旁路 SQLite 独立落盘而非追加主业务日志
**决策**: 创建独立的 `intent_steps` SQLite 表，与主日志/业务 DB 物理隔离，通过 `open_db_rw` 异步写入。
**理由**: 语义轨迹数据具有高写入频率且需结构化查询（如按 `session_id` 回放、按 `action_class` 统计）。独立表避免污染业务库，且 SQLite 单进程零配置完全满足当前单体/轻量部署的审计需求。

### D2 · 基于持有集（Held Set）的运行时类型约束校验
**决策**: 维护运行时的语义持有集合，初始化为 `{"user_request"}`。每步 `output_types` 并入持有集，校验 `input_types` 是否全在持有集中，缺失则记入 `violations`。
**理由**: LLM 易产生幻觉或跳过必要前置步骤。该机制提供轻量级、无外部依赖的“语义类型系统”，自动捕获非法消费链（如未获取 `file_content` 就直接 `summarize`），为后续自纠正提供事实依据。

### D3 · 仅暴露 `IntentTracer` 单类，隐藏 DB 底层实现
**决策**: `__init__.py` 仅导出 `IntentTracer`，内部 `open_db`/`_SCHEMA` 及并发控制逻辑完全私有。
**理由**: 降低接入成本，调用方只需实例化并调用 `record`。数据库模式可能随分析需求演进，隐藏实现可保障后续迁移（如换为 DuckDB 或远程列存）不破坏调用契约。

### D4 · 弃用旧 shim 并强制直连权威路径
**决策**: 2026-04-07 移除 `omnicompany.runtime.intent_tracer` 兼容层，所有导入强制指向 `omnicompany.tracing.IntentTracer`。
**理由**: 原 runtime 子包承载了过多基础设施，违背单一职责。收敛至 `tracing` 域包使边界清晰，符合 `distributed-docs.md` 基础设施模块就近原则，减少循环依赖风险。

## 数据流 / 拓扑
```
[LLM Agent / Tool Router] 产生 FactoryEvent + intent payload
       ↓ (同步/异步调用)
[IntentTracer.record()]
       ├─ 1. 解析 intent → 提取 {input_types, output_types, action_class, desc}
       ├─ 2. 约束校验 → 比对内部 held_types_set → 记录 violations (若有)
       ├─ 3. 快照更新 → 将 output_types 合并入 held_types_set
       └─ 4. 事务落盘 → open_db_rw() → INSERT INTO intent_steps (ts, session_id, types, violations...)
       ↓
[SQLite: intent_steps 表] (持久化存储)
       ↓
[下游消费] (审计脚本 / Dashboard 可视化 / 回放引擎 / Guardian 幻觉规则扫描)
```

## 已知局限
- **并发写入存在锁竞争风险** · 升级路径: 当前 SQLite 默认 WAL 模式可处理基础并发，但高频多 Agent 并发可能触发 `database is locked`。计划引入 `asyncio.Queue` + 单 Writer Actor 模式，将并发调用缓冲为串行批量 `executemany` 写入，彻底消除写锁冲突。
- **缺乏自动清理与数据生命周期管理** · 升级路径: `intent_steps` 表随运行无限增长，未内置按 `session_id` 或时间窗口的自动归档/清理策略。下一步将在初始化参数增加 `max_sessions` 与 `retention_days`，底层对接定期 `DELETE` 与 `VACUUM`，防止本地磁盘耗尽。
- **持有集校验状态跨重启不保留** · 升级路径: `held_types_set` 为运行时内存状态，进程重启后重置。对于长周期流式任务可能导致前期校验状态丢失。计划支持可选的 `state_db_table` 配置，将持有集快照持久化至轻量级附加表，实现断点续传校验。

## 参考资料
- 核心实现: [intent_tracer.py](intent_tracer.py)
- 包导出声明: [__init__.py](__init__.py)
- 依赖协议: omnicompany/protocol/events.py (`FactoryEvent`)
- 依赖存储: omnicompany/runtime/storage/db_access.py (`open_db_rw`)
- 迁移记录: `intent_tracer.py` 顶部 docstring (2026-04-07 shim 移除声明)