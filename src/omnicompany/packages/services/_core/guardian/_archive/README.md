<!-- [OMNI] origin=claude-code domain=omnifactory/guardian ts=2026-04-20T00:00:00Z type=doc status=deprecated -->
<!-- [OMNI] material_id="material:services.guardian.archive.migration_log.readme.md" -->

# Guardian Legacy Archive

此目录保存 Guardian Team 从旧 RuleEngine 架构迁移到新 Worker 架构（2026-04-20 Stage 1 Team 1）时**归档的旧文件**。

## 归档文件清单

| 归档文件 | 原路径 | 归档原因 | 新位置 |
|---|---|---|---|
| `patrol_legacy.py` | `patrol.py` | `RuleEngine` + `LLMJudge` 类，旧架构的"规则引擎"入口 | 逻辑内联到 `workers/rule_engine_worker.py` + 兼容 `RuleEngine` 类保留在 `_patrol_shim.py` |
| `patrol_runner_legacy.py` | `patrol_runner.py` | `run_patrol()` + `_git_*_changes` 等，旧架构的"扫描入口" | `_git_*_changes` / `_load_file_ctx` / `_full_src_scan` 内联到 `workers/git_diff_scan_worker.py`；`run_patrol()` 变 `_patrol_shim.py` 中的兼容 shim |
| `routers_legacy.py` | `routers.py` (Clean Migration 2026-04-20) | `FsScannerRouter` + `ArchAuditorRouter` + `HealthReporterRouter` 三类原单文件实现 | `FsScannerRouter` → `workers/fs_scanner_worker.py::FsScannerWorker`; `ArchAuditorRouter` → `workers/arch_auditor_worker.py::ArchAuditorWorker`; `HealthReporterRouter` **不迁** (AgentNodeLoop 子类) 留在新 `routers.py` 中作为与 Worker alias 共存的部分 |

### Clean Migration (Stage 2, 2026-04-20) 补充

本次 Stage 2 迁移针对 health-check 管线 (fs_scanner → arch_auditor → health_reporter)
三个旧 Router 做清理, 标准见 migration_log.md · 完全迁移标准 Stage 2:

- ✅ FsScannerRouter → FsScannerWorker (extends Worker, workers/fs_scanner_worker.py)
- ✅ ArchAuditorRouter → ArchAuditorWorker (extends Worker, workers/arch_auditor_worker.py)
- ⏭️ HealthReporterRouter 保留 AgentNodeLoop 继承 (Phase 1 runtime 统一后处理) — 仍在 routers.py

新 `routers.py` 变为 compat shim: 2 旧名 alias (FsScannerRouter = FsScannerWorker 等)
+ HealthReporterRouter 原样保留。`run.py build_bindings()` 仍正常工作, 不破坏任何下游。

## 为何归档而非删除

1. **历史追溯**: 如果将来 Worker 架构有 bug，可以对比旧实现
2. **外部引用审计**: 允许 grep 搜索是否还有第三方代码依赖旧路径（应该没有，已全部改）
3. **测试金标**: 旧实现可作为 Worker 实现正确性的参考基准

## 调用者全部已迁移到新路径

外部调用原 `patrol_runner.run_patrol` / `patrol.RuleEngine` 的 6 处 import 已全部改为:

```python
from omnifactory.packages.services.guardian import run_patrol, RuleEngine, FileContext, Violation
```

涉及文件:
- `src/omnifactory/cli/commands/guardian.py`
- `src/omnifactory/cli/commands/debt.py`
- `src/omnifactory/packages/services/guardian/sentinel.py`
- `src/omnifactory/packages/services/guardian/patrol_hook.py` (2 处)
- `src/omnifactory/packages/services/guardian/auto_check.py`
- `tests/guardian/test_patrol_rules.py`

## 不要恢复此目录文件到原位置

恢复会破坏 Worker 架构的 shape 完整性, 让旧入口重新活跃。如需查看旧实现, **只读**即可, 不 import。

## 相关文档

- [`../../../../../docs/plans/[2026-04-19]BLACKBOARD-ARCHITECTURE/migration_log.md`](../../../../../docs/plans/[2026-04-19]BLACKBOARD-ARCHITECTURE/migration_log.md) — Team 1 guardian 迁移坑 + 心智修订
- `../../../../../docs/standards/terminology.md` §6 两层命名 / §6.5 Worker 粒度原则
