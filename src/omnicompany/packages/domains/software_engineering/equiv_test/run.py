# [OMNI] origin=claude-code domain=software_engineering/equiv_test ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.equiv_test.router_bindings_factory.py"
"""equivalence_test.run — Bindings 构建 V2"""

from __future__ import annotations
from typing import Any
from omnicompany.runtime.routing.router import Router


def build_bindings(input_dict: dict[str, Any] | None = None) -> dict[str, Router]:
    from omnicompany.packages.domains.software_engineering.equiv_test.routers import (
        TestDesignerRouter, GoldenRecorderRouter, BaselineCheckRouter,
        TSTestGeneratorRouter, TSExecutorRouter,
        ResultComparatorRouter, FailureAnalyzerRouter,
    )

    model = None
    ts_dir = None
    if input_dict:
        model = input_dict.get("model")
        ts_dir = input_dict.get("ts_dir")

    return {
        "test_designer": TestDesignerRouter(model=model),
        "golden_recorder": GoldenRecorderRouter(model=model),
        "baseline_check": BaselineCheckRouter(),
        "ts_test_gen": TSTestGeneratorRouter(model=model),
        "ts_executor": TSExecutorRouter(ts_dir=ts_dir),
        "result_comparator": ResultComparatorRouter(),
        "failure_analyzer": FailureAnalyzerRouter(model=model),
    }
