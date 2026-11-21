# [OMNI] origin=claude-code domain=omnicompany/selftest ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:core.selftest.worker_registry.aggregator.py"
"""Selftest Team · 4 Worker 清单 (Clean Migration 2026-04-20)."""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker

from .registry_checker import RegistryCheckerWorker
from .functional_tester import FunctionalTesterWorker
from .selftest_gate import SelftestGateWorker
from .llm_reporter import LLMReporterWorker


ALL_WORKERS: list[type[Worker]] = [
    RegistryCheckerWorker,
    FunctionalTesterWorker,
    SelftestGateWorker,
    LLMReporterWorker,
]


__all__ = [
    "RegistryCheckerWorker",
    "FunctionalTesterWorker",
    "SelftestGateWorker",
    "LLMReporterWorker",
    "ALL_WORKERS",
]
