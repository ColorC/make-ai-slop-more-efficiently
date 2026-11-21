# _archive/ · Legacy Implementations

> Clean Migration V2 · 2026-04-20 归档

## 归档清单

### `routers_legacy.py`（原 `routers.py`）

- 原位置: `doctor/routers.py`（3911 行，22 个 `*Router` 类 + AST 工具函数）
- 归档原因: Clean Migration 要求 ≥ 3 Worker 的 Team 按子域拆 `workers/` 子目录
- 新位置: `workers/format/` (9 Worker) + `workers/router/` (6 Worker) + `workers/pipeline/` (7 Worker + 2 来自 pipeline_topology)
- 业务逻辑来源: 本文件仍是**业务逻辑的唯一源**。每个 `workers/*/<name>.py` 内的 `XxxWorker` 类通过多重继承组合 `Worker` 和本文件的 `XxxRouter`（diamond MRO，`Worker` 和 `XxxRouter` 共享 `Router` 祖先）
- 兼容路径: `routers.py` 现为 compat shim，re-export 新 Worker 名 + 旧 Router 名 alias + AST 工具函数（`_is_format_call` / `_extract_kwargs` / `_iter_format_calls` / `_find_constant_name` / `_extract_router_ast` / ... 共 15 个）

### `pipeline_topology_legacy.py`（原 `pipeline_topology.py`）

- 原位置: `doctor/pipeline_topology.py`（1162 行，2 Router + 11 条 check 注册表 + Lineage 工具）
- 归档原因: 统一归档策略，所有 Router 源码集中一处
- 新位置: `workers/pipeline/pipeline_topology_check.py` + `workers/pipeline/pipeline_lineage.py` 两个 Worker
- 业务逻辑来源: 本文件仍是**业务逻辑的唯一源**（`Finding` / `CheckContext` / `PipelineCheckSpec` / `run_pipeline_checks` / `extract_pipeline_lineage` / `discover_all_pipelines` 等工具保留在此）
- 兼容路径: `pipeline_topology.py` 现为 compat shim，re-export 2 Worker + 2 Router alias + Finding/CheckContext/run_pipeline_checks/PipelineLineage 等工具

## Clean Migration 硬规则

见 `migration_log.md` · 完全迁移标准（Stage 2 升级版）:

- 类继承必须从 `omnifactory.packages.services.omnicompany.Worker`
- ≥ 3 Worker 的 Team 必须拆 `workers/` 子目录
- Material kind（F-19）100% 覆盖
- DESIGN.md 七节 + §十 Team 专属

## 不要直接使用

不要从 `_archive/` 直接 import Worker 类实例化。使用:

- **新代码**: `from omnifactory.packages.services.doctor.workers import FormatExtractorWorker` 或从子域 `.workers.format import FormatExtractorWorker`
- **兼容路径**: `from omnifactory.packages.services.doctor.routers import FormatExtractorRouter`（旧名 alias = 新 Worker 类）

## 为什么保留

- **Diamond 继承源**: Worker 子类通过继承归档 Router 重用业务逻辑（零重写）
- **Shim re-export 源**: 顶层 `routers.py` / `pipeline_topology.py` 的 AST 工具函数仍在此定义
- **历史追溯**: 单文件装 22 + 2 Router 的早期架构证据
