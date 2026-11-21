# [OMNI] origin=ai-ide ts=2026-05-23 type=infra
# [OMNI] material_id="material:dashboard.boss_sight.package_init.py"
"""boss_sight — BOSS SIGHT 总控 + 审阅台 + 多 agent 控制面板.

落实 docs/plans/dashboard/[2026-05-23]BOSS-SIGHT/master_roadmap.md 描述的四块:

- controller/  — 块 1: 总控 agent worker 本体 + 跟人对接通道
- aggregator/  — 块 1 / 块 2: plan / subagent 状态聚合(给总控 ctx 用)
- (后续块 2/3/4 在此目录添加更多子模块)

设计原则:
- 总控 prompt 由外部维护会话独立维护(用户原文 §3.1), 总控自己不能改自己 prompt
- 总控不直接执行任务 / 不修代码 / 不汇报 / 不修 omnicompany 核心层(§2.14/15/17)
- 严格按 boundaries.md 的工具白名单 + 路径黑名单
"""

from __future__ import annotations
