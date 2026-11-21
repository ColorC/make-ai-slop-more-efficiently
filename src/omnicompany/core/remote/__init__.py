# [OMNI] origin=claude-code domain=omnicompany/core/remote ts=2026-10-17T00:00:00+08:00 type=lib status=active
# [OMNI] summary="omni remote v1: 统一远程执行层(画像 + runner + result.json + 文件通道 + 后台作业 + 替身后端)。"
"""``omni remote`` — one remote execution layer for Claude Code / Codex on Windows nodes.

Command lines only carry fixed subcommands and short arguments; scripts and parameters
travel as files; the structured ``result.json`` (real exit code, phase, first error, absolute
paths of logs and artifacts) is the source of truth. CLI: ``omnicompany.cli.commands.remote``.
"""
