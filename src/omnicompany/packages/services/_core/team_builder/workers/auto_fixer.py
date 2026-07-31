# [OMNI] origin=claude-code domain=omnicompany/workflow_factory ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:core.team_builder.level3_auto_fixer.llm_repair.py"
"""AutoFixerWorker — workflow_factory Team Worker (Clean Migration 2026-04-20).

Worker 协议:
  FORMAT_IN  = wf.project_skeleton
  FORMAT_OUT = wf.project_skeleton

职责: LLM 自动修复 (Level 3 fallback). 接收通过编译但后续验证失败的代码,
从 reports 容器读取所有历史失败报告 (compile / lap_audit / error_route / integration),
用 LLM tool_use (apply_fixes) 做 snippet 级跨文件修复. 只在 deterministic_fixer
(Level 1) 和 syntax_fixer (Level 2) 无法解决时到达.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from .._archive.routers_legacy import AutoFixerRouter as _Legacy


class AutoFixerWorker(Worker, _Legacy):
    """Level 3 LLM fallback 修复器, snippet 级跨文件修复 + pipeline.py 全重写兜底."""
    pass
