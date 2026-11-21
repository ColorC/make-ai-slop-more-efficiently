# [OMNI] origin=ai-ide ts=2026-05-09 type=infra
# [OMNI] material_id="material:dashboard.controlplane.package_init.py"
"""controlplane — dashboard 控制面 (跟 ccdaemon 进程级隔离的 API 集合).

本包跟 ccdaemon 是两个独立 uvicorn 进程:
- controlplane (本包) → dashboard 主进程 (8200, 开 --reload)
- ccdaemon → 独立 daemon 进程 (8201, 默认不 reload)

本包内全是只读 API + 写入 API + IDE bus + 反向代理. 文件改动可被 dashboard 进程
file watcher 自动 reload, 不会影响 ccdaemon 持有的 chat 会话.

完整道路: docs/plans/dashboard/[2026-05-09]DASHBOARD-DOGFOOD-RESILIENCE/plan.md
"""
