# [OMNI] origin=claude-code domain=omnicompany/workflow_factory ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:core.team_builder.compile_checker.three_layer.py"
"""CompileCheckerWorker — workflow_factory Team Worker (Clean Migration 2026-04-20).

Worker 协议:
  FORMAT_IN  = wf.project_skeleton
  FORMAT_OUT = wf.project_skeleton

职责: 三层编译检查 (HARD).
  L1: py_compile 语法检查
  L2: importlib 可达验证
  L3: TeamChecker 类型兼容检查
报告写进 reports['compile'], PASS 贴 compile-passed tag.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from .._archive.routers_legacy import CompileCheckerRouter as _Legacy


class CompileCheckerWorker(Worker, _Legacy):
    """三层编译检查: py_compile → import → TeamChecker, 任一失败即 FAIL."""
    pass
