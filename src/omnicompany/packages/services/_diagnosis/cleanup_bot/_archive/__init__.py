# [OMNI] origin=claude-code domain=cleanup_bot/__init__.py ts=2026-04-08T03:23:36Z
# [OMNI] material_id="material:diagnosis.cleanup_bot.package_aggregate_init.py"
"""omnifactory.packages.services._diagnosis.cleanup_bot — 环境副作用清理工作流

扫描磁盘目录中由 AI 误触产生的错位文件/目录，用 LLM 判定合法性，
生成 PowerShell 清理脚本（不自动执行，仅生成计划）。
"""
