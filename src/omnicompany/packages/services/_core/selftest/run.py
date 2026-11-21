# [OMNI] origin=claude-code domain=omnicompany/selftest ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:core.selftest.binding_builder.worker_factory.py"
"""selftest — 构建绑定 (Clean Migration 2026-04-20)."""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker

from omnicompany.packages.services._core.selftest.workers import (
    FunctionalTesterWorker,
    LLMReporterWorker,
    RegistryCheckerWorker,
    SelftestGateWorker,
)


def build_bindings(input_dict: dict | None = None) -> dict[str, Worker]:
    from omnicompany.runtime.llm.llm import LLMClient

    client = LLMClient(role="runtime_main", max_tokens=512)

    return {
        "registry_checker": RegistryCheckerWorker(),
        "functional_tester": FunctionalTesterWorker(),
        "selftest_gate": SelftestGateWorker(),
        "llm_reporter": LLMReporterWorker(client=client),
    }
