<!-- [OMNI] origin=claude-code domain=runtime/storage ts=2026-04-25T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:runtime.storage.design_specification.documentation.md" -->

# storage · 设计文档

## 状态
- **版本**: V1 (2026-04-25 骨架升级为 active，固化集中式 DB 访问与工具/经验索引设计)
- **成熟度**: active
- **下一步**: 剥离 `pipeline_index` 向量存储硬编码，对接 `omnicompany.core.vector_store` 抽象；增加 `db_access` 的连接池上限动态观测与慢查询日志埋点

## 核心目的
提供 omnicompany 运行时统一、受控的持久化与检索入口。
**解决**：集中管理 SQLite 连接生命周期（WAL/busy_timeout/Row 工厂）、私有 Domain 节点 YAML → 数据库的幂等装载、Agent 执行前的 Pipeline/经验混合检索、以及跨任务工具模板（LAP Hawkes 能量）的 JSON 持久化。
**不解决**：上层业务语义建模（属 `core` / `protocol` 职责）、分布式多节点事务同步、或通用 ORM 对象映射。本包是“连接与基础索引层”，不承载业务逻辑。

## 核心接口
- `open_db(db_path, readonly=False)` / `open_db_rw(db_path)` — [db_access.py](db_access.py) 上下文管理器/长连接创建，强制 WAL 与 guard 检查。
- `install_connect_guard()` — [db_access.py](db_access.py) 拦截绕过本模块的直接 `sqlite3.connect` 调用，触发 `RuntimeWarning`。
- `load_domain(domain_path, db_path)` / `load_all_domains(config_path, db_path)` — [domain_loader.py](domain_loader.py) 扫描 YAML 节点定义并 upsert 至 `semantic_nodes` 表，内置极简 YAML 降级解析。
- `search_pre_execution(query, ...)` / `PipelineIndexer` — [experience_search.py](experience_search.py) 执行前搜索，混合语义向量+关键词或纯关键词+LLM 重排，读写 `pipeline_index` 表。
- `PersistentFormatRegistry` — [tool_pattern_registry.py](tool_pattern_registry.py) 跨任务工具模板注册表，实现 `register()` / `record_usage()` / `get_energy()`（基于 Hawkes 过程）/ `get_reuse_rate()`，JSON 文件持久化。
- `__init__.py` 包入口 — [__init__.py](__init__.py) 仅导出文档引用指向 `docs/ARCHITECTURE.md`，保持运行时零隐式依赖。

## 架构决策
### D1 · 强制集中式 DB 访问层
**决策**: 所有模块禁止直接 `import sqlite3; connect()`，必须经 `open_db` / `open_db_rw` 封装。
**理由**: 消除多线程/多进程写入时的 `database is locked` 异常。统一设置 `PRAGMA journal_mode=WAL`、`busy_timeout=30000` 及 `sqlite3.Row` 工厂，并在 `agent_loop` 启动时注入 `install_connect_guard()` 强制拦截，防止隐式绕过导致锁竞争或连接泄漏。

### D2 · 短生命周期连接优先 (Context Manager)
**决策**: 默认推荐 `with open_db(...) as conn:` 模式，严禁在 LLM 推理或 IO 等待期间持有 DB 连接。
**理由**: Agent 循环单次 LLM 调用常耗时数秒至数十秒。若期间持有连接，会阻塞其他 Worker 的 WAL 写入。上下文管理器确保 `with` 块结束立即 `commit` + `close`，将 DB 锁定时间压缩至毫秒级。仅状态同步/长事务允许 `open_db_rw` 手动管理。

### D3 · 领域节点加载采用“扫描+Upsert+极简解析器”
**决策**: `domain_loader` 直接读取 YAML 目录结构，使用内置正则回退解析器应对无 `pyyaml` 环境，并以 `UPSERT` 逻辑写入。
**理由**: OmniCompany 部署环境可能为极简容器或沙盒，不强制依赖外部 YAML 库。正则回退足够支撑 `key: value` 与简单列表格式的节点定义，降低部署摩擦。Upsert 保证反复扫描不产生重复节点，维持 `semantic_network` 拓扑一致性。

### D4 · 经验搜索支持“向量降级为纯关键词+LLM”双后端
**决策**: `experience_search` 优先使用 `embedding` (BLOB) + 关键词混合检索；若 `purpose_embedding` 缺失或模型不可用，自动降级为全文关键词匹配 + LLM 重排序。
**理由**: 保证轻量级/离线环境下的可用性。向量搜索提供高召回，但依赖 embedding 基础设施；降级策略确保在无 GPU 或网络隔离场景下，Agent 仍能通过 `pipeline_index` 的 `purpose`/`tags` 文本找到近似历史经验，避免搜索链路断裂。

### D5 · 工具模板能量计算采用 Hawkes 过程而非简单计数
**决策**: `PersistentFormatRegistry` 的 `get_energy()` 基于 Hawkes 激活函数衰减，而非线性累加 `used_in_tasks`。
**理由**: LAP (Language-Agent Programming) 理论要求复用信号反映“近期活跃度与历史积累”的加权。简单计数无法区分“十年前用过 100 次”与“本周高频使用”的模板差异。Hawkes 衰减使 Registry 能动态浮出高价值、持续进化的工具模式，为 Agent 提供质量排序依据。

## 数据流 / 拓扑
```
[Agent 启动] → agent_loop.py 调用 install_connect_guard()
                  ↓
[DB 初始化] → open_db("semantic_network.db") → PRAGMA WAL / busy_timeout / Row Factory
                  ↓
[节点装载] ← domain_loader.scan_dir() → parse_yaml() → conn.execute(UPSERT) → semantic_nodes 表
                  ↓
[执行前搜索] ← agent.query() → experience_search.load_index()
                  ├─ 有向量? → cosine_similarity(BLOB) + keyword_filter
                  └─ 无向量? → keyword_match + LLM.rerank(candidates)
                  ↓
[模板调用] ← Agent 发现/使用工具 → registry.record_usage(tool_name, timestamp)
                  ↓ Hawkes 能量更新
[持久化] → registry.flush() → format_registry.json (跨任务保留)
```

## 已知局限
- **局限 1**: `experience_search` 的向量数据以 SQLite `BLOB` 裸存储，未使用专用向量扩展（如 sqlite-vss 或 FAISS 索引），数据量破万时距离计算退化为全表扫描。
  **升级路径**: 迁移 `pipeline_index` 表至支持 HNSW/IVF 索引的专用向量库（如 LanceDB 或编译 sqlite-vss）；在 `db_access` 增加 `open_vector_db()` 抽象，保持调用方接口不变。
- **局限 2**: `PersistentFormatRegistry` 依赖本地单 JSON 文件 (`format_registry.json`)，多并发 Agent 写入存在覆盖风险（无文件锁/事务保护）。
  **升级路径**: 引入 `filelock` 库实现写入互斥，或迁移至 `db_access` 管理的 SQLite `tool_patterns` 表中，利用 SQLite 的原子事务保证并发安全；当前仅在单进程/短窗口推荐场景使用，需在调用文档中注明并发限制。

## 接收意愿
- **接收**: 新增存储后端适配器（如 DuckDB/PostgreSQL 兼容层）、扩展 `pipeline_index` 的元数据字段、提供跨域节点合并策略的工具函数、针对 `domain_loader` 的 AST/Schema 校验器。
- **不接收**: 业务领域模型定义（应放 `core` 或 `protocol`）、LLM 推理逻辑、或针对特定 Domain 的定制 ETL 脚本。
- **边界信号**: 若引入复杂业务 JOIN 查询、状态机代码或 LLM 调用链，说明模块越界，应拆分为独立 service 或迁移至 `omnicompany.core`。

## 参考资料
- 关联源码: [db_access.py](db_access.py), [domain_loader.py](domain_loader.py), [experience_search.py](experience_search.py), [tool_pattern_registry.py](tool_pattern_registry.py), [__init__.py](__init__.py)
- 关联架构: `docs/ARCHITECTURE.md` (Runtime 持久化层说明)
- 关联规范: `docs/standards/distributed-docs.md` (OMNI-034 基础设施模块设计)
- 关联计划: `docs/plans/[2026-04-14]INFO-SUFFICIENCY/FOUR_TIER_PLAN.md` (经验沉淀与工具复用背景)
- 关联理论: `docs/theory/` (LAP Hawkes 激活机制与工具模板进化)