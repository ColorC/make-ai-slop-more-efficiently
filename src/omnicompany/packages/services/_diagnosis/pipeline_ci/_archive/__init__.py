# [OMNI] origin=claude-code domain=pipeline_ci/__init__.py ts=2026-04-08T03:23:37Z
# [OMNI] material_id="material:diagnosis.pipeline_ci.package_entry.python"
"""pipeline_ci — 管线质量 CI 扫描器

纯确定性管线，扫描 packages/ 下所有域，运行 ErrorRouteAuditor 和
TeamChecker，聚合报告并在有 critical 问题时阻断 CI。
"""
