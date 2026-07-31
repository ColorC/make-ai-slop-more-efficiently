# [OMNI] origin=claude-code domain=omnicompany/workflow_factory ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:team_builder.workers.requirement_spec_parser.worker.py"
"""ReqAnalyzerWorker — workflow_factory Team Worker (Clean Migration 2026-04-20).

Worker 协议:
  FORMAT_IN  = wf.requirement_raw
  FORMAT_OUT = wf.requirement

职责: LLM 将自然语言需求解析为结构化需求规格 (goal/domain/input/output/约束/验证需求/错误场景).

实现继承自 _archive/routers_legacy.ReqAnalyzerRouter (Diamond shortcut, 业务逻辑不变).
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from .._archive.routers_legacy import ReqAnalyzerRouter as _Legacy


class ReqAnalyzerWorker(Worker, _Legacy):
    """LLM 将自然语言需求解析为结构化需求规格."""
    pass
