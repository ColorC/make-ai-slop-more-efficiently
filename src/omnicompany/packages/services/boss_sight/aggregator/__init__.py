# [OMNI] origin=ai-ide ts=2026-05-23 type=infra
# [OMNI] material_id="material:dashboard.boss_sight.aggregator.package_init.py"
"""aggregator — 总控 ctx 装载用的索引服务.

- plan_index_scanner: 扫 docs/plans/ 给出 plan 索引
- subagent_status_aggregator: 订阅 EventBus 维护 subagent 活跃情况
"""

from __future__ import annotations
