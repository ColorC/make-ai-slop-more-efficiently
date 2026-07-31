# _archive/ · Legacy Implementations

> Clean Migration V2 · 2026-04-20 归档 · 2026-07-26 OMNI-040 Stage 3 清洁后重编

## 现状 (2026-07-26)

`_archive/` 只剩一份纯历史参考, 活代码不再从这里 import 任何东西:

### `routers_legacy.py`（原 `routers.py`）

- 原位置: `doctor/routers.py`（3911 行，22 个 `*Router` 类 + AST 工具函数）
- 归档原因: Clean Migration 要求 ≥ 3 Worker 的 Team 按子域拆 `workers/` 子目录
- Router 业务逻辑: 已迁到 `workers/material/` + `workers/worker/` + `workers/team/` 独立文件
- AST 工具函数: 正式版本在 `workers/{material,worker,team}/_shared.py`;
  顶层 `routers.py` shim 自 2026-07-26 起改从那里 re-export (下划线旧名 alias 保持兼容)
- 本文件仅作历史追溯保留

### `pipeline_topology_legacy.py` → 已迁出

- 拓扑检查引擎本体 (Finding / CheckContext / PipelineCheckSpec / run_pipeline_checks /
  extract_pipeline_lineage / discover_all_pipelines 等) 是活基础设施, 2026-07-26
  OMNI-040 Stage 3 迁到正式位置 [`../pipeline_topology_engine.py`](../pipeline_topology_engine.py),
  由 `pipeline_topology.py` shim re-export

## Clean Migration 硬规则

见 `migration_log.md` · 完全迁移标准（Stage 2 升级版）:

- 类继承必须从 `omnicompany.packages.services._core.omnicompany.Worker`
- ≥ 3 Worker 的 Team 必须拆 `workers/` 子目录
- Material kind（F-19）100% 覆盖
- DESIGN.md 七节 + §十 Team 专属

## 不要直接使用

不要从 `_archive/` import。使用:

- **新代码**: `from omnicompany.packages.services._diagnosis.doctor.workers import MaterialExtractorWorker`
- **兼容路径**: `from omnicompany.packages.services._diagnosis.doctor.routers import FormatExtractorRouter`（旧名 alias = 新 Worker 类）

## 为什么保留

- **历史追溯**: 单文件装 22 + 2 Router 的早期架构证据（既定民约: 归档先行/删除留白天）
