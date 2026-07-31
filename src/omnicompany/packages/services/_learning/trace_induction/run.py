# [OMNI] origin=claude-code domain=trace_induction/run.py ts=2026-04-08T03:23:37Z
# [OMNI] material_id="material:learning.trace_induction.pipeline_bindings.builder.py"
"""trace_induction run — 构建绑定 + 注册"""

from __future__ import annotations


from omnicompany.packages.services._learning.trace_induction.formats import register_formats
from omnicompany.packages.services._learning.trace_induction.routers import (
    NoiseFilterRouter,
    ReqWriterRouter,
    SOPGeneratorRouter,
    TraceReaderRouter,
)
from omnicompany.runtime.routing.router import Router


def build_bindings(input_dict: dict | None = None, *, model: str | None = None) -> dict[str, Router]:
    """构建 trace-induction 的节点绑定。"""
    from omnicompany.runtime.llm.llm import LLMClient
    from omnicompany.protocol.format import create_builtin_registry

    # 注册 Format
    registry = create_builtin_registry()
    register_formats(registry)

    # LLM 客户端（噪音过滤 + SOP 生成 + 需求文档 共享）
    client = LLMClient(
        role="runtime_main", max_tokens=4096,
        **({"model": model} if model else {}),
    )

    return {
        "trace_reader": TraceReaderRouter(),
        "noise_filter": NoiseFilterRouter(client=client),
        "sop_generator": SOPGeneratorRouter(client=client),
        "req_writer": ReqWriterRouter(client=client),
    }
