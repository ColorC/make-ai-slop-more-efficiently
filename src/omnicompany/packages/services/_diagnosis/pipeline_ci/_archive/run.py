# [OMNI] origin=claude-code domain=pipeline_ci/run.py ts=2026-04-08T03:23:37Z
# [OMNI] material_id="material:diagnosis.pipeline_ci.runtime_bindings.python"
"""pipeline_ci — bindings + 注册"""

from __future__ import annotations

from omnifactory.packages.services._diagnosis.pipeline_ci.routers import (
    BatchAuditorRouter,
    CIGateRouter,
    DomainScannerRouter,
)
from omnifactory.runtime.routing.router import Router


def build_bindings(input_dict: dict | None = None) -> dict[str, Router]:
    return {
        "domain_scanner": DomainScannerRouter(),
        "batch_auditor": BatchAuditorRouter(),
        "ci_gate": CIGateRouter(),
    }
