# [OMNI] origin=claude-code domain=omnicompany/selftest ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:core.selftest.package_entry.exports.py"
"""selftest — OmniCompany e2e 功能自测 Team (Clean Migration 2026-04-20)."""
from __future__ import annotations

from .workers import (
    ALL_WORKERS,
    FunctionalTesterWorker,
    LLMReporterWorker,
    RegistryCheckerWorker,
    SelftestGateWorker,
)
# 旧名兼容 shim (routers.py 转发)
from .routers import (
    FunctionalTesterRouter,
    LLMReporterRouter,
    RegistryCheckerRouter,
    SelftestGateRouter,
)


__all__ = [
    # 新名
    "RegistryCheckerWorker",
    "FunctionalTesterWorker",
    "SelftestGateWorker",
    "LLMReporterWorker",
    "ALL_WORKERS",
    # 旧名 (兼容)
    "RegistryCheckerRouter",
    "FunctionalTesterRouter",
    "SelftestGateRouter",
    "LLMReporterRouter",
]
