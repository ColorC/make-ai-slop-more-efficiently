# [OMNI] origin=claude-code domain=software_engineering/implement ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.implement.pipeline_bindings.builder.py"
from __future__ import annotations
from omnicompany.runtime.routing.router import Router


def build_bindings(input_dict: dict | None = None) -> dict[str, Router]:
    """构建管线节点到 Router 实例的映射。"""

    from omnicompany.packages.domains.software_engineering.implement.routers import (
        ReqParserRouter,
        CodebaseScannerRouter,
        ContextJudgeRouter,
        ImplementorRouter,
        ReportEmitterRouter,
    )
    return {
        "req_parser": ReqParserRouter(),
        "codebase_scanner": CodebaseScannerRouter(),
        "context_judge": ContextJudgeRouter(),
        "implementor": ImplementorRouter(),
        "report_emitter": ReportEmitterRouter(),
    }
