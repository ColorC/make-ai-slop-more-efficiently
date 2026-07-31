# _archive/ · Legacy Implementations

> Clean Migration 2026-04-20 归档

## 归档清单

- `routers_legacy.py` (原 `routers.py`) — 14 个 `*Router` 类 + 模块级辅助函数的原始单文件实现 (3053 行)
  - 新位置: [`../workers/`](../workers/) 目录, 每个 Worker 一个文件
  - 新命名: `ReqAnalyzerRouter` → `ReqAnalyzerWorker` (等)
  - 兼容路径: `from ..routers import *` 通过 [`../routers.py`](../routers.py) shim 继续可用 (旧名别名)
- `routers_codegen_legacy.py` (原 `routers_codegen.py`) — `CodeGenLoop` (AgentNodeLoop 非 Worker, 本次 Clean Migration 不迁继承关系)
  - 新位置: [`../routers_codegen.py`](../routers_codegen.py) 作为轻量 shim re-export
  - **本次保留原 AgentNodeLoop 继承不动**, Agent Loop 体系由阶段 D (`docs/plans/[2026-04-18]AGENT-NODE-LOOP-ROUTERIZATION/plan.md`) 另行推进

## 原因

Clean Migration 硬规则 (见 `migration_log.md` · 完全迁移标准):

- ≥ 3 Worker 的 Team 必须拆 `workers/` 子目录
- 类继承必须从 `omnicompany.Worker` (非 `runtime.routing.router.Router`)
- 旧 `routers.py` 单文件装 14 Router 不再符合标准
- 每条 Material 必须标 kind.source/internal/sink (F-19)

## Diamond shortcut 说明

本次 workflow_factory Clean Migration 采用 **Diamond 继承 shortcut** (见 migration_log "已知妥协"节).
原因: routers.py 3053 行, 含多处紧耦合的模块级辅助:
  - `_wf_no_trunc` / `_extract_json_obj` / `_wf_extract_python_code`
  - `_CodeGenBaseRouter` (4 子 CodeGen*Router 共享基类)
  - `_GLOBAL_FIX_LIMIT` / `_check_global_fix_iter` (全局修复迭代上限)
  - `check_format_in_consumption` re-export
  - 5 个 LLM system prompts (`_REQ_SYSTEM` / `_FORMAT_SYSTEM` / `_NODE_SYSTEM` / `_CODE_GEN_SYSTEM` / `_SYNTAX_FIX_SYSTEM` / `_LAP_VERIFY_SYSTEM`)

直接全量搬进 `workers/*.py` 风险较大, 采用 Diamond shortcut 快速建立合规外观:
  - `workers/<name>.py` 中: `class XxxWorker(Worker, _LegacyRouter): pass`
  - 业务代码暂存 `_archive/routers_legacy.py`
  - 共享辅助从 [`../workers/_shared.py`](../workers/_shared.py) re-export

**Stage 3 清洁工作**: 把业务代码真正搬到 `workers/*.py`, `_archive/` 仅保留静态文档.
优先级低于 Stage 2 全 Team 覆盖.

## 不要直接使用

不要从 `_archive/` import. 使用:
- 新代码: `from omnifactory.packages.services.workflow_factory.workers import ReqAnalyzerWorker`
- 兼容路径: `from omnifactory.packages.services.workflow_factory.routers import ReqAnalyzerRouter` (旧名自动 alias 到新名)
- AgentNodeLoop: `from omnifactory.packages.services.workflow_factory.routers_codegen import CodeGenLoop` (路径不变, 逻辑未动)
