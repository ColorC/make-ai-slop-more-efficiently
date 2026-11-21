# [OMNI] origin=claude-code domain=software_engineering/design ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.design.router_bindings_factory.py"
from __future__ import annotations
from omnicompany.runtime.routing.router import Router


def build_bindings(input_dict: dict | None = None) -> dict[str, Router]:
    """构建管线节点到 Router 实例的映射。"""

    from omnicompany.packages.domains.software_engineering.design.routers import (
        SpecParserRouter,
        ArchScannerRouter,
        FileReaderRouter,
        ContextJudgeRouter,
        PatternAnalyzerRouter,
        DesignReviewerRouter,
        ReportFormatterRouter,
    )
    return {
        "spec_parser": SpecParserRouter(),
        "arch_scanner": ArchScannerRouter(),
        "file_reader": FileReaderRouter(),
        "context_judge": ContextJudgeRouter(),
        "pattern_analyzer": PatternAnalyzerRouter(),
        "design_reviewer": DesignReviewerRouter(),
        "report_formatter": ReportFormatterRouter(),
    }
