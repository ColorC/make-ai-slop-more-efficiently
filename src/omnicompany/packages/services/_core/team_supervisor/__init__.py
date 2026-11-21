# [OMNI] origin=claude-code domain=services/team_supervisor/__init__ ts=2026-04-26T00:00:00Z type=config
# [OMNI] material_id="material:core.team_supervisor.module_aggregate.exports.py"
"""team_supervisor Team · 通用 Team 健康监督设施.

入口: build_team / build_bindings (按 OmniCompany 服务包惯例).
完整设计见同目录 DESIGN.md.
"""
from __future__ import annotations

from .team import build_team
from .run import build_bindings

__all__ = ["build_team", "build_bindings"]
