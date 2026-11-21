# [OMNI] origin=claude-code domain=software_engineering/review ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.review.router_bindings.definition.py"
from __future__ import annotations
from omnicompany.runtime.routing.router import Router


def build_bindings(input_dict: dict | None = None) -> dict[str, Router]:
    """构建管线节点到 Router 实例的映射。"""

    from omnicompany.packages.domains.software_engineering.review.routers import (
        DiffCollectorRouter,
        ContextGathererRouter,
        TestSearcherRouter,
        SufficiencyJudgeRouter,
        DeepReviewerRouter,
        FindingValidatorRouter,
        ReportFormatterRouter,
    )
    return {
        "diff_collector": DiffCollectorRouter(),
        "context_gatherer": ContextGathererRouter(),
        "test_searcher": TestSearcherRouter(),
        "sufficiency_judge": SufficiencyJudgeRouter(),
        "deep_reviewer": DeepReviewerRouter(),
        "finding_validator": FindingValidatorRouter(),
        "report_formatter": ReportFormatterRouter(),
    }
