# _archive/ · Legacy Implementations

> Clean Migration 2026-04-20 归档 · 2026-07-26 OMNI-040 Stage 3 清洁后重编

## 现状 (2026-07-26)

Stage 3 清洁已完成: 活业务代码全部迁出 `_archive/`:

- `routers_legacy.py` 的 10 个在用 `*Router` 实现 → [`../routers_legacy.py`](../routers_legacy.py) (正式位置,
  `workers/*.py` 的 XxxWorker 经 Diamond 继承; 兼容 shim: [`../routers.py`](../routers.py))
- `routers_codegen_legacy.py` (`CodeGenLoop` AgentNodeLoop) → [`../routers_codegen_legacy.py`](../routers_codegen_legacy.py)
  (兼容 shim: [`../routers_codegen.py`](../routers_codegen.py); Agent Loop 继承体系由阶段 D 另行推进)

`_archive/` 现在只保留一件东西:

- `routers_legacy.py` — 2026-07-03 批4 显式废止的 LAP 九维检查器实现体 (锚㋒契约:
  "实现体留归档不删, 活代码引用已全摘除"). 活代码不得 import 本文件.

## 历史: Diamond shortcut 说明 (2026-04-20 ~ 2026-07-26)

本次 workflow_factory Clean Migration 曾采用 **Diamond 继承 shortcut**:
`workers/<name>.py` 中 `class XxxWorker(Worker, _LegacyRouter): pass`, 业务代码暂存
`_archive/routers_legacy.py`. 2026-07-26 OMNI-040 处理时把业务文件迁回正式位置,
shortcut 的"活代码物理依赖归档"问题随之消除.

## 不要直接使用

不要从 `_archive/` import. 使用:
- 新代码: `from omnicompany.packages.services._core.team_builder.workers import ReqAnalyzerWorker`
- 兼容路径: `from omnicompany.packages.services._core.team_builder.routers import ReqAnalyzerRouter` (旧名自动 alias 到新名)
- AgentNodeLoop: `from omnicompany.packages.services._core.team_builder.routers_codegen import CodeGenLoop` (路径不变, 逻辑未动)
