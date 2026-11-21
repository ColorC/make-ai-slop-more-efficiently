# [OMNI] origin=ai-ide domain=decisions ts=2026-06-18T00:00:00Z type=package status=active
# [OMNI] summary="decisions domain 的源读取器:把各种对话源(claude/codex 会话…)解析成人读有意义的精简文本,供独立抽取 agent 炼决策。"
# [OMNI] why="存量对话 jsonl 巨大且 90% 是工具噪声;抽取前必须先抽出人话(用户+助手正文)。读取器是确定性解析基建,不产决策(决策由独立 agent 从精简文本炼)。"
# [OMNI] tags=decisions,sources,conversation
"""decisions domain —— 对话源读取器。"""

from __future__ import annotations
