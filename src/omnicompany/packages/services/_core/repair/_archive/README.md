# _archive/ · Legacy Implementations

> Clean Migration 2026-04-20 归档 (Stage 2 完全迁移)

## 归档清单

- `routers_legacy.py` (原 `routers.py`) — Format 修复子管线的 3 `*Router` 类单文件实现
  - 新位置: [`../workers/`](../workers/) 三个文件
    - `RepairPlannerRouter` → `workers/repair_planner.py::RepairPlannerWorker`
    - `FormatPatcherRouter` → `workers/format_patcher.py::FormatPatcherWorker`
    - `FormatRepairAgentLoop` → `workers/format_repair_agent_loop.py::FormatRepairAgentLoopWorker`
  - 兼容路径: `from .routers import FormatRepairAgentLoop` 通过 `../routers.py` shim 继续可用 (旧名 alias)

- `router_repair_legacy.py` (原 `router_repair.py`) — Router 修复子管线的 9 `*Router` 类单文件实现 (+ `run_router_repair` 驱动函数)
  - 新位置: [`../workers/`](../workers/) 九个文件
    - `IssueLoaderRouter` → `workers/issue_loader.py::IssueLoaderWorker`
    - `RouterSourceLoaderRouter` → `workers/router_source_loader.py::RouterSourceLoaderWorker`
    - `DescriptionPlannerRouter` → `workers/description_planner.py::DescriptionPlannerWorker`
    - `FailPathPlannerRouter` → `workers/fail_path_planner.py::FailPathPlannerWorker`
    - `GrantedTagsPlannerRouter` → `workers/granted_tags_planner.py::GrantedTagsPlannerWorker`
    - `PatchMergerRouter` → `workers/patch_merger.py::PatchMergerWorker`
    - `PatchValidatorRouter` → `workers/patch_validator.py::PatchValidatorWorker`
    - `PatchApplierRouter` → `workers/patch_applier.py::PatchApplierWorker`
    - `RediagnoseRouter` → `workers/rediagnose.py::RediagnoseWorker`
  - 共享 AST / diff 工具函数提取到 [`../workers/_shared.py`](../workers/_shared.py)
  - 驱动函数 `run_router_repair` 保留在 [`../routers.py`](../routers.py) shim (向后兼容)

## 原因

Clean Migration 硬规则 (见 `migration_log.md` · 完全迁移标准 §"完全迁移标准"):

- ≥ 3 Worker 的 Team 必须拆 `workers/` 子目录 (repair 共 12 Worker)
- 类继承必须从 `omnicompany.Worker` (非 `runtime.routing.router.Router`)
- `formats.py` 必须用 `Material` (而非 `Format`), 每条 Material 标 `kind.*` tag (F-19)
- 旧 `routers.py` + `router_repair.py` 单文件装 12 Router 不再符合标准

## 不要直接使用

不要从 `_archive/` import。使用:

- 新代码 (推荐): `from omnifactory.packages.services.repair.workers import FormatRepairAgentLoopWorker`
- 兼容路径: `from omnifactory.packages.services.repair.routers import FormatRepairAgentLoop` (旧名自动 alias 到新名)
