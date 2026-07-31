# _archive/ · absorption Legacy Implementations

> Clean Migration V2 · 2026-04-20 归档

## 归档清单

### `routers_v1v2_legacy.py` (原 `routers.py`, ~2939 行)

- 原位置: `absorption/routers.py` (13 个 `*Router` 类: V1 6 个 + V2 7 个)
- 归档原因: Clean Migration 要求 ≥ 3 Worker 的 Team 按子域拆 `workers/` 子目录
- 新位置: `workers/v1/` (6 Worker) + `workers/v2/` (7 Worker)
- 业务逻辑来源: 本文件仍是**业务逻辑的唯一源** (V1/V2 部分). 每个 `workers/v1/*.py` 或
  `workers/v2/*.py` 内的 `XxxWorker` 类通过多重继承组合 `Worker` 和本文件的 `XxxRouter`
  (Diamond MRO, `Worker` 和 `XxxRouter` 共享 `Router` 祖先)
- 兼容路径: `routers/__init__.py` 是 compat shim package, re-export 新 Worker 名 + 旧 Router 名 alias

**内嵌 AgentNodeLoop 类** (不迁, 保留原位):
- `_ReconLoop` (在 ReconScoutV2Router.run() 内部)
- `_DirectedReaderLoop` (在 DirectedReaderV2Router.run() 内部)

### `routers_v3_legacy/` (原 `routers/` 子目录, ~4212 行 across 12 文件)

- 原位置: `absorption/routers/` (20 个 `*Router` 类 跨 12 文件)
- 归档原因: 统一归档策略, 所有 Router 源码集中 `_archive/`
- 新位置: `workers/v3/` (含 `workers/v3/knowledge_loaders/` 子子域 7 Worker)
- 业务逻辑来源: 本目录仍是**业务逻辑的唯一源** (V3 部分, 含 knowledge loaders + Stage 3)
- 兼容路径:
  - `routers/__init__.py` re-export 所有 V3 Router 名
  - `routers/<name>.py` (如 `routers/module_explorer.py`) shim 模块 re-export 子模块旧路径
  - 特殊: `routers/report_writer.py` 还 re-export `_build_finding_with_code` / `_split_report_parts`
    给 `_archive/routers_v3_legacy/report_updater.py` (本文件内部 import 仍从 _archive 取)

**内嵌 AgentNodeLoop 类** (不迁, 保留原位):
- `_ExplorerLoop` (在 ModuleExplorerRouter.run() 内部)
- `_DisputeLoop` (在 ProposalDisputeLoopRouter.run() 内部)

## 为什么走 Diamond Shortcut

absorption 是当前最大 Team (~7151 行 legacy 代码, 35 Router + 4 AgentNodeLoop 类), 远超
`migration_log.md` 中 "> 20 Router / > 4000 行" 的 Diamond shortcut 门槛. 真迁 35 个 Worker
将重写大量 `__init__` / `run` / 辅助函数, 引入业务行为偏差风险.

Diamond shortcut 在保证命名层合规 (Worker 继承链 + workers/ 结构 + kind.* + compat shim)
的同时, 业务代码零修改, 回归风险最低. 这是 doctor Team (2026-04-20) 采用并验证过的模式.

**Stage 3 清洁工作** (未来可做): 将 `_archive/` 业务代码搬到 `workers/*.py` 顶层.
优先级低于 Stage 2 全 Team 覆盖.

## Clean Migration 硬规则

见 `migration_log.md` · 完全迁移标准 (Stage 2 升级版):

- 类继承必须从 `omnifactory.packages.services.omnicompany.Worker`
- ≥ 3 Worker 的 Team 必须拆 `workers/` 子目录 (本 Team 用 v1/v2/v3 三子域)
- Material kind (F-19) 100% 覆盖 (本 Team 39 Format, 全标)
- DESIGN.md 活跃 + §十 Team 专属

## 不要直接使用

不要从 `_archive/` 直接 import Worker 类实例化. 使用:

- **新代码**: `from omnifactory.packages.services.absorption.workers import TargetIntakeWorker`
  或从子域 `.workers.v1 import TargetIntakeWorker`
- **兼容路径**: `from omnifactory.packages.services.absorption.routers import TargetIntakeRouter`
  (旧名 alias = 新 Worker 类)
  `from omnifactory.packages.services.absorption.routers.module_explorer import ModuleExplorerRouter`
  (子模块路径也保留 shim)

## 为什么保留

- **Diamond 继承源**: Worker 子类通过继承归档 Router 重用业务逻辑 (零重写)
- **Shim re-export 源**: 顶层 `routers/report_writer.py` shim 仍从此 re-export 辅助函数
- **历史追溯**: 35 Router 跨 V1/V2/V3 三代演化的原始证据

## 需要搬上去的 (Stage 3 清洁工作, P3)

1. `_archive/routers_v1v2_legacy.py` 中 `_ReconLoop`, `_DirectedReaderLoop` 两个内嵌 AgentNodeLoop
   等阶段 D AGENT-NODE-LOOP-ROUTERIZATION 落地后, 随 V2 Worker 真迁同步处理.
2. `_archive/routers_v3_legacy/module_explorer.py` 中 `_ExplorerLoop` / `proposal_dispute_loop.py`
   中 `_DisputeLoop` 同上.
3. 40+ 辅助函数 (如 `_parse_repo`, `_gh_api`, `_build_finding_with_code`) 可跟 Worker 真迁一同
   搬到 `workers/_helpers.py`, 或直接内嵌到各自 Worker 文件.
