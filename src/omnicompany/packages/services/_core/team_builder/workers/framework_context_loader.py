# [OMNI] origin=claude-code domain=omnicompany/workflow_factory ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:team_builder.workers.framework_source_injector.worker.py"
"""FrameworkContextLoaderWorker — workflow_factory Team Worker (Clean Migration 2026-04-20).

Worker 协议:
  FORMAT_IN  = wf.framework_context_loader.input (composite: wf.node_plan + wf.format_chain)
  FORMAT_OUT = wf.node_plan_augmented

职责: 用 inspect.getsource 注入框架真源码 + selftest 参考域全文到 node_plan.framework_context.
消灭 code_generator 对框架 API 的幻觉根源.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from .._archive.routers_legacy import FrameworkContextLoaderRouter as _Legacy


class FrameworkContextLoaderWorker(Worker, _Legacy):
    """确定性注入框架真源码 (Router/Verdict/AnchorSpec 等) + selftest 参考实现."""
    pass
