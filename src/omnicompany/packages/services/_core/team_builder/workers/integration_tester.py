# [OMNI] origin=claude-code domain=omnicompany/workflow_factory ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:team_builder.workers.integration_test_executor.worker.py"
"""IntegrationTesterWorker — workflow_factory Team Worker (Clean Migration 2026-04-20).

Worker 协议:
  FORMAT_IN  = wf.project_skeleton
  FORMAT_OUT = wf.project_skeleton

职责: 集成测试 (HARD), 验证生成的代码能实际跑起来. 六项测试:
  T1: 文件写入磁盘
  T2: import package + submodules (routers/formats/pipeline/run)
  T3: build_pipeline() 返回合法 TeamSpec
  T4: TeamChecker.check() 通过
  T5: build_bindings() 实例化 Router
  T6: TeamRunner 构造 dry-run (spec + bindings + bus + registry)
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from .._archive.routers_legacy import IntegrationTesterRouter as _Legacy


class IntegrationTesterWorker(Worker, _Legacy):
    """集成测试: 写文件 + import + build_pipeline + TeamChecker + build_bindings + runner 构造."""
    pass
