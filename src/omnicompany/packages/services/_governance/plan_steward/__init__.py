# [OMNI] origin=claude-code domain=services/_governance/plan_steward ts=2026-06-12T12:00:00Z type=config
# [OMNI] material_id="material:governance.plan_steward.package_init.py"
"""plan_steward — 计划治理: 归属项目分类 + 中文标题 + 格式检查(便宜模型干活)。"""

from .steward import run_governance, governance_summary

__all__ = ["run_governance", "governance_summary"]
