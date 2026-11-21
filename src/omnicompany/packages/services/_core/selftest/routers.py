# [OMNI] origin=claude-code domain=omnicompany/selftest ts=2026-04-20T00:00:00Z type=shim
# [OMNI] material_id="material:core.selftest.router_compat.aliases.py"
"""selftest/routers.py — 向后兼容 shim (Clean Migration 2026-04-20).

真实 Worker 实现在 `workers/` 目录。本文件仅为旧 import 路径保留兼容:
  - 旧名 FooRouter → 新名 FooWorker (别名)
  - 旧 `from ...selftest.routers import RegistryCheckerRouter` 继续工作

不要往本文件加新逻辑; 新增 Worker 请直接写 `workers/<name>.py`。
归档: `_archive/routers_legacy.py` 保留旧实现供历史追溯。
"""
from __future__ import annotations

from .workers import (
    FunctionalTesterWorker,
    LLMReporterWorker,
    RegistryCheckerWorker,
    SelftestGateWorker,
)


# ─── 旧名别名 (兼容) ────────────────────────────────────────────────────────
RegistryCheckerRouter = RegistryCheckerWorker
FunctionalTesterRouter = FunctionalTesterWorker
SelftestGateRouter = SelftestGateWorker
LLMReporterRouter = LLMReporterWorker


__all__ = [
    # 新名 (推荐)
    "RegistryCheckerWorker",
    "FunctionalTesterWorker",
    "SelftestGateWorker",
    "LLMReporterWorker",
    # 旧名 (兼容)
    "RegistryCheckerRouter",
    "FunctionalTesterRouter",
    "SelftestGateRouter",
    "LLMReporterRouter",
]
