# [OMNI] origin=claude-code domain=omnicompany/workflow_factory ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:core.team_builder.finalizer.sink_emitter.py"
"""FinalizerWorker — workflow_factory Team Worker (Clean Migration 2026-04-20).

Worker 协议:
  FORMAT_IN  = wf.project_skeleton
  FORMAT_OUT = wf.done  (sink)

职责: 最终化 (HARD). 写入文件系统 + import 验证 + 注册到 pipeline registry +
生成质量总结报告 (编译/LAP/路由/测试四项得分), 输出最终产物 wf.done.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from .._archive.routers_legacy import FinalizerRouter as _Legacy


class FinalizerWorker(Worker, _Legacy):
    """最终化: 写盘 + import 验证 + 注册 + quality_summary 输出 wf.done."""
    pass
