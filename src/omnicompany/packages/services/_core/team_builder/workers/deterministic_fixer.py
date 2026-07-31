# [OMNI] origin=claude-code domain=omnicompany/workflow_factory ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:core.team_builder.level1_deterministic_fixer.rule_cleanup.py"
"""DeterministicFixerWorker — workflow_factory Team Worker (Clean Migration 2026-04-20).

Worker 协议:
  FORMAT_IN  = wf.project_skeleton
  FORMAT_OUT = wf.project_skeleton

职责: 确定性修复器 (HARD, Level 1 修复). 薄包装 runtime/codegen_tools.apply_python_lap_cleanup:
  - from typing import Dict/List 删除 (用内置类型)
  - kind="ANCHOR"/"HARD" 字面量 → 枚举访问
  - 缺失的标准 import 补全
不调 LLM, 无法修复的问题 PARTIAL 升级给 syntax_fixer (LLM).
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from .._archive.routers_legacy import DeterministicFixerRouter as _Legacy


class DeterministicFixerWorker(Worker, _Legacy):
    """Level 1 确定性修复器, 无 LLM, 无法修复时 PARTIAL 升级给 syntax_fixer."""
    pass
