# [OMNI] origin=omnicompany domain=omnicompany/guardian ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:core.guardian.package_aggregate.exports.py"
"""guardian — 守护检查 Team (omnicompany 架构).

两套入口共存:
- `run.py build_bindings()` — 经典 pipeline 形态 (FsScanner/ArchAuditor/HealthReporter)
- `run_patrol()` / `run_guardian()` — Team Worker 架构入口 (本 __init__ 导出)

本模块主要 re-export Worker 架构入口, 供外部调用者方便使用:
  from omnicompany.packages.services._core.guardian import run_patrol, run_guardian

历史:
- 原 `patrol.py` (RuleEngine/LLMJudge) 逻辑 → 归档到 `_archive/patrol_legacy.py`,
  Worker 内部自包含 (2026-04-20 Team 1 迁移)
- 原 `patrol_runner.py` (git scan + run_patrol) 逻辑 → 归档到 `_archive/patrol_runner_legacy.py`,
  新 `_patrol_shim.py` 提供向后兼容入口
"""
from __future__ import annotations

from ._patrol_shim import (
    run_guardian,
    run_patrol,
    format_patrol_report,
    RuleEngine,
    FileContext,
    GuardianRule,
    Violation,
    parse_omnimark,
    RULES,
)


__all__ = [
    "run_guardian", "run_patrol", "format_patrol_report",
    "RuleEngine", "FileContext", "GuardianRule", "Violation",
    "parse_omnimark", "RULES",
]
