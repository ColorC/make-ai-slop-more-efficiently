# [OMNI] origin=claude-code domain=software_engineering/verify ts=2026-04-08T03:23:43Z
# [OMNI] material_id="material:domains.software_engineering.verify.router_bindings.definition.py"
from __future__ import annotations
from omnicompany.runtime.routing.router import Router


def build_bindings(input_dict: dict | None = None) -> dict[str, Router]:
    """构建管线节点到 Router 实例的映射。"""

    from omnicompany.packages.domains.software_engineering.verify.routers import (
        ClaimParserRouter,
        EnvCheckerRouter,
        CmdExecutorRouter,
        OutputAnalyzerRouter,
        SupplementalDesignerRouter,
        ReportEmitterRouter,
    )
    return {
        "claim_parser": ClaimParserRouter(),
        "env_checker": EnvCheckerRouter(),
        "cmd_executor": CmdExecutorRouter(),
        "output_analyzer": OutputAnalyzerRouter(),
        "supplemental_designer": SupplementalDesignerRouter(),
        "report_emitter": ReportEmitterRouter(),
    }
