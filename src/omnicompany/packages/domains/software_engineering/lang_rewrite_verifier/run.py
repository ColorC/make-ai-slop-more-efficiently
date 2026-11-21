# [OMNI] origin=claude-code domain=software_engineering/lang_rewrite_verifier ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.lang_rewrite_verifier.pipeline_bindings.builder.py"
"""lang_rewrite_verifier.run — Bindings 构建

节点 ID 对应 pipeline.py 中的 node.id：
  smoke_gen, smoke_runner                          ← 本包新增
  error_analyzer, context_init, hypothesis_generator,
  probe_designer, probe_executor, evidence_collector,
  fixer, tester, regression_analyzer, regression_to_context   ← 复用 debugger
"""

from __future__ import annotations

from typing import Any

from omnicompany.runtime.routing.router import Router


def build_bindings(input_dict: dict[str, Any] | None = None) -> dict[str, Router]:
    """构建管线节点→Router 绑定。延迟 import 避免启动时拉入重依赖。"""

    # ── 本包新增节点 ──
    from omnicompany.packages.domains.software_engineering.lang_rewrite_verifier.routers import (
        SmokeRunnerRouter,
        SmokeTestGeneratorRouter,
    )

    # ── 复用 debugger 节点 ──
    from omnicompany.packages.domains.software_engineering.debugger.routers import (
        ContextInitRouter,
        ErrorAnalyzerRouter,
        EvidenceCollectorRouter,
        FixerRouter,
        HypothesisGeneratorRouter,
        ProbeDesignerRouter,
        ProbeExecutorRouter,
        RegressionAnalyzerRouter,
        RegressionToContextRouter,
        TesterRouter,
    )

    model: str | None = input_dict.get("model") if input_dict else None

    return {
        # ── 冒烟验证 ──
        "smoke_gen":    SmokeTestGeneratorRouter(model=model),
        "smoke_runner": SmokeRunnerRouter(),

        # ── Debugger（完整复用，tester 路由由 pipeline.py 中定义）──
        "error_analyzer":       ErrorAnalyzerRouter(model=model),
        "context_init":         ContextInitRouter(),
        "hypothesis_generator": HypothesisGeneratorRouter(model=model),
        "probe_designer":       ProbeDesignerRouter(model=model),
        "probe_executor":       ProbeExecutorRouter(),
        "evidence_collector":   EvidenceCollectorRouter(),
        "fixer":                FixerRouter(model=model),
        "tester":               TesterRouter(),
        "regression_analyzer":  RegressionAnalyzerRouter(model=model),
        "regression_to_context": RegressionToContextRouter(),
    }
