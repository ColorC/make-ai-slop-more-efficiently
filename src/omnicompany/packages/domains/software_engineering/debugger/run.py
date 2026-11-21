# [OMNI] origin=claude-code domain=software_engineering/debugger ts=2026-04-08T03:23:41Z
# [OMNI] material_id="material:domains.software_engineering.debugger.router_bindings_factory.py"
"""debugger.run — Bindings 构建"""

from __future__ import annotations
from typing import Any
from omnicompany.runtime.routing.router import Router


def build_bindings(input_dict: dict[str, Any] | None = None) -> dict[str, Router]:
    from omnicompany.packages.domains.software_engineering.debugger.routers import (
        ErrorAnalyzerRouter,
        ContextInitRouter,
        HypothesisGeneratorRouter,
        ProbeDesignerRouter,
        ProbeExecutorRouter,
        EvidenceCollectorRouter,
        FixerRouter,
        TesterRouter,
        RegressionAnalyzerRouter,
        RegressionToContextRouter,
    )

    model = input_dict.get("model") if input_dict else None

    return {
        "error_analyzer": ErrorAnalyzerRouter(model=model),
        "context_init": ContextInitRouter(),
        "hypothesis_generator": HypothesisGeneratorRouter(model=model),
        "probe_designer": ProbeDesignerRouter(model=model),
        "probe_executor": ProbeExecutorRouter(),
        "evidence_collector": EvidenceCollectorRouter(),
        "fixer": FixerRouter(model=model),
        "tester": TesterRouter(),
        "regression_analyzer": RegressionAnalyzerRouter(model=model),
        "regression_to_context": RegressionToContextRouter(),
    }
