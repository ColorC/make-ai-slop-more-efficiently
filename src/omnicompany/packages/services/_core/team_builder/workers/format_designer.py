# [OMNI] origin=claude-code domain=omnicompany/workflow_factory ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:core.team_builder.format_chain_designer.llm_planning.py"
"""FormatDesignerWorker — workflow_factory Team Worker (Clean Migration 2026-04-20).

Worker 协议:
  FORMAT_IN  = wf.requirement
  FORMAT_OUT = wf.format_chain

职责: LLM 根据结构化需求设计 Format 继承链 (语义锚定 / 单一职责 / 可验证性 / 继承优先).
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from .._archive.routers_legacy import FormatDesignerRouter as _Legacy


class FormatDesignerWorker(Worker, _Legacy):
    """LLM 根据结构化需求设计 Format 继承链."""
    pass
