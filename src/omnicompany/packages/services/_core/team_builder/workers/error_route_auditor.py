# [OMNI] origin=claude-code domain=omnicompany/workflow_factory ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:core.team_builder.error_route_auditor.five_check.py"
"""ErrorRouteAuditorWorker — workflow_factory Team Worker (Clean Migration 2026-04-20).

Worker 协议:
  FORMAT_IN  = wf.project_skeleton
  FORMAT_OUT = wf.project_skeleton

职责: 错误路由完整性审计 (HARD). 五项确定性检查:
  1. FAIL 路由覆盖率 (每个 ANCHOR 节点必须有 FAIL 出边)
  2. LLM 失败声明 (SOFT 节点必须包含 Verdict.fail / PARTIAL)
  3. 验证绑定 (bugfix 类有测试, 代码生成类有编译检查)
  4. UserInquiry 接口 (needs_user_inquiry=True 节点必须 import UserInquiry)
  5. DESCRIPTION 完整性 (>= 50 字符)
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from .._archive.routers_legacy import ErrorRouteAuditorRouter as _Legacy


class ErrorRouteAuditorWorker(Worker, _Legacy):
    """确定性错误路由完整性审计, 五项检查, critical 失败即 FAIL."""
    pass
