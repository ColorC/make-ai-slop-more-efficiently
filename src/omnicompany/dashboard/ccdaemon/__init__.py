# [OMNI] origin=ai-ide ts=2026-05-09 type=infra
# [OMNI] material_id="material:dashboard.ccdaemon.package_init.py"
"""ccdaemon — Claude Code 进程独家持有方.

跟 dashboard 主进程 (controlplane/) 是**两个独立 uvicorn 进程**:
- dashboard 进程 (8200, 开 --reload): 控制面所有 API + 反向代理
- ccdaemon 进程 (8201, 默认不 reload): claude-agent-sdk client + winpty PTY

dashboard 重启不影响 ccdaemon, 浏览器 chat 不掉线; ccdaemon 重启由
`omni cc daemon restart` 显式触发, 浏览器走自动重连协议续展历史.

完整道路: docs/plans/dashboard/[2026-05-09]DASHBOARD-DOGFOOD-RESILIENCE/plan.md
"""
