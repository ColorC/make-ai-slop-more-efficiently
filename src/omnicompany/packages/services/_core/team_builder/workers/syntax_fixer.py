# [OMNI] origin=claude-code domain=omnicompany/workflow_factory ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:team_builder.workers.syntax_error_fixer.worker.py"
"""SyntaxFixerWorker — workflow_factory Team Worker (Clean Migration 2026-04-20).

Worker 协议:
  FORMAT_IN  = wf.project_skeleton
  FORMAT_OUT = wf.project_skeleton

职责: 逐文件精准修复编译语法错误 (Level 2). 策略:
  - 不把所有文件一次性塞给 LLM (会溢出 token 导致截断引入新错误)
  - 每次只给 LLM 一个文件 + 它的具体错误, 输出该单文件的完整修复版
  - 从 reports['compile'] 读错误信息
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from .._archive.routers_legacy import SyntaxFixerRouter as _Legacy


class SyntaxFixerWorker(Worker, _Legacy):
    """LLM 逐文件精准修复编译语法错误 (Level 2)."""
    pass
