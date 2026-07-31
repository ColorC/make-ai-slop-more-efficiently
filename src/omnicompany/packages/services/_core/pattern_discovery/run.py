# [OMNI] origin=claude-code domain=pattern_discovery/run.py ts=2026-04-08T03:23:37Z
# [OMNI] material_id="material:core.pattern_discovery.service_bindings.implementation.py"
"""pattern_discovery run — 构建绑定 + 注册"""

from __future__ import annotations


from omnicompany.packages.services._core.pattern_discovery.formats import register_formats
from omnicompany.packages.services._core.pattern_discovery.routers import (
    InductionDispatcherRouter,
    PatternClustererRouter,
    SummaryReaderRouter,
)
from omnicompany.runtime.routing.router import Router


def build_bindings(input_dict: dict | None = None, *, model: str | None = None) -> dict[str, Router]:
    """构建 pattern-discovery 的节点绑定。"""
    from omnicompany.runtime.llm.llm import LLMClient
    from omnicompany.protocol.format import create_builtin_registry

    registry = create_builtin_registry()
    register_formats(registry)

    client = LLMClient(
        role="runtime_main", max_tokens=4096,
        **({"model": model} if model else {}),
    )

    return {
        "summary_reader": SummaryReaderRouter(),
        "pattern_clusterer": PatternClustererRouter(client=client),
        "induction_dispatcher": InductionDispatcherRouter(),
    }
