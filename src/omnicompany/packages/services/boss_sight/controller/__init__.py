# [OMNI] origin=ai-ide ts=2026-05-23 type=infra
# [OMNI] material_id="material:dashboard.boss_sight.controller.package_init.py"
"""controller — BOSS SIGHT 原生总控的上下文与治理组件.

模块:
- boundary_guard: 工具白名单 + 路径黑名单 enforcement
- system_prompt:  读取外部维护的 prompts/system.md
- ctx_loader:     装载 plan_index + subagent_status 到总控 ctx
- wake_up:        EventBus 订阅三种唤起条件
- service:        FastAPI 集成入口 (启停 + 路由)
- reflection:     prompt 反思 + 重复检测 (T1.5)

总控会话由 dashboard ccdaemon 的 Claude Code SDK 路径持有；本包不定义第二套
Python Agent 内循环。
"""

from __future__ import annotations
