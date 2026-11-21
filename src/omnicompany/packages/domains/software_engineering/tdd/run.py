# [OMNI] origin=claude-code domain=software_engineering/tdd ts=2026-04-08T03:23:43Z
# [OMNI] material_id="material:domains.software_engineering.tdd.router_bindings.definition.py"
from __future__ import annotations
from omnicompany.runtime.routing.router import Router


def build_bindings(input_dict: dict | None = None) -> dict[str, Router]:
    """构建管线节点到 Router 实例的映射。"""

    from omnicompany.packages.domains.software_engineering.tdd.routers import (
        PlanLoaderRouter,
        TestWriterRouter,
        TestRunnerRouter,
        ImplWriterRouter,
        ReportEmitterRouter,
    )
    return {
        "plan_loader": PlanLoaderRouter(),
        "test_writer": TestWriterRouter(),
        "test_runner": TestRunnerRouter(),
        "impl_writer": ImplWriterRouter(),
        "report_emitter": ReportEmitterRouter(),
    }
