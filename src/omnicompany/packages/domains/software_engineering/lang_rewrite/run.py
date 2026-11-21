# [OMNI] origin=claude-code domain=software_engineering/lang_rewrite ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.lang_rewrite.pipeline_bindings.builder.py"
"""lang_rewrite.run — Bindings 构建 + 便捷入口

bindings 的 key 必须与 pipeline.py 中的 node.id 一一对应。
"""

from __future__ import annotations

from typing import Any

from omnicompany.runtime.routing.router import Router


def build_bindings(input_dict: dict[str, Any] | None = None) -> dict[str, Router]:
    """构建管线节点→Router 绑定。

    延迟 import Router 实现避免在 CLI 启动时拉入 anthropic 等重依赖。
    """
    from omnicompany.packages.domains.software_engineering.lang_rewrite.routers import (
        SourceAnalyzerRouter,
        DependencyMapperRouter,
        DemandExtractorRouter,
        SupplyScannerRouter,
        FeedbackDemoteRouter,
        IdiomTranslatorRouter,
        TypeCheckerRouter,
        AgentFixerRouter,
        StyleCheckerRouter,
        StyleFixerRouter,
        InterfaceExtractorRouter,
        SignatureComparatorRouter,
        BehavioralTesterRouter,
        EquivalenceJudgeRouter,
    )

    model = None
    work_dir = None
    ts_dir = None
    rs_dir = None
    if input_dict:
        model = input_dict.get("model")
        work_dir = input_dict.get("work_dir")
        ts_dir = input_dict.get("ts_dir")
        rs_dir = input_dict.get("rs_dir")

    return {
        # ── 分析阶段 ──
        "source_analyzer": SourceAnalyzerRouter(),
        "dependency_mapper": DependencyMapperRouter(),
        # ── 上下文扫描（fan-out）──
        "demand_extractor": DemandExtractorRouter(),
        "supply_scanner": SupplyScannerRouter(ts_dir=ts_dir, rs_dir=rs_dir),
        # ── 翻译 ──
        "idiom_translator": IdiomTranslatorRouter(model=model),
        # ── L1: 编译 ──
        "type_checker": TypeCheckerRouter(work_dir=work_dir),
        "agent_fixer": AgentFixerRouter(model=model),
        # ── L2: 风格 ──
        "style_checker": StyleCheckerRouter(work_dir=work_dir),
        "style_fixer": StyleFixerRouter(model=model),
        # ── L3: 接口对比（fan-out）──
        "interface_extractor": InterfaceExtractorRouter(),
        "signature_comparator": SignatureComparatorRouter(),
        "behavioral_tester": BehavioralTesterRouter(ts_dir=ts_dir),
        # ── L4: 语义裁判（fan-in）+ 降级 ──
        "equivalence_judge": EquivalenceJudgeRouter(model=model),
        "feedback_demote": FeedbackDemoteRouter(),
    }
