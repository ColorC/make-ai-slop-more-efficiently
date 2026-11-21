# [OMNI] origin=claude-code domain=software_engineering/plan ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.plan.router_bindings.definition.py"
from __future__ import annotations
from omnicompany.runtime.routing.router import Router


def build_bindings(input_dict: dict | None = None) -> dict[str, Router]:
    """构建管线节点到 Router 实例的映射。"""

    from omnicompany.packages.domains.software_engineering.plan.routers import (
        SpecLoaderRouter,
        CodebaseScannerRouter,
        FileReaderRouter,
        ContextJudgeRouter,
        FileMapperRouter,
        PlanDrafterRouter,
        SelfReviewerRouter,
        PlanEmitterRouter,
    )
    return {
        "spec_loader": SpecLoaderRouter(),
        "codebase_scanner": CodebaseScannerRouter(),
        "file_reader": FileReaderRouter(),
        "context_judge": ContextJudgeRouter(),
        "file_mapper": FileMapperRouter(),
        "plan_drafter": PlanDrafterRouter(),
        "self_reviewer": SelfReviewerRouter(),
        "plan_emitter": PlanEmitterRouter(),
    }
