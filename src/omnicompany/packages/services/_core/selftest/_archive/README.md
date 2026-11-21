# _archive/ · Legacy Implementations

> Clean Migration 2026-04-20 归档

## 归档清单

- `routers_legacy.py` (原 `routers.py`) — 4 个 `*Router` 类的原始单文件实现
  - 新位置: [`workers/`](../workers/) 目录, 每个 Worker 一个文件
  - 新命名: `RegistryCheckerRouter` → `RegistryCheckerWorker` (等)
  - 兼容路径: `from .routers import *` 通过 `routers.py` shim 继续可用（旧名别名）

## 原因

Clean Migration 硬规则（见 `migration_log.md` · 完全迁移标准）:

- ≥ 3 Worker 的 Team 必须拆 `workers/` 子目录
- 类继承必须从 `omnicompany.Worker`（非 `runtime.routing.router.Router`）
- 旧 `routers.py` 单文件装 4 Router 不再符合标准

## 不要直接使用

不要从 `_archive/` import。使用:
- 新代码: `from omnifactory.packages.services.selftest.workers import RegistryCheckerWorker`
- 兼容路径: `from omnifactory.packages.services.selftest.routers import RegistryCheckerRouter` (旧名自动 alias 到新名)
