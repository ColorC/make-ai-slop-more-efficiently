# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:44Z
# [OMNI] material_id="material:runtime.nodes.death_zone_check.intent_parse.safety.py"
"""安全与意图节点 — 禁区拦截 + 意图解析

从 semantic.py 拆分。
"""

from __future__ import annotations

import logging
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

logger = logging.getLogger(__name__)


class DeathZoneCheckRouter(Router):
    """禁区拦截节点 — 在工具执行前检查是否违反不可变规则。

    可以被不同的禁区规则集替换 → 所以是节点，不是底座。
    """

    INPUT_KEYS = ["tool_calls"]

    def run(self, input_data: Any) -> Verdict:
        from omnicompany.runtime.signals.pain_system import check_death_zones

        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.PASS, output=input_data)

        tool_name = input_data.get("tool_name", "")
        tool_args = input_data.get("tool_args", {})
        intent = input_data.get("intent")

        rule = check_death_zones(tool_name, tool_args, intent)
        if rule is None:
            return Verdict(kind=VerdictKind.PASS, output=input_data)

        # 根据规则生成具体的替代方案提示（重导向）
        _redirects = {
            "no_find_head_commands": (
                "替代方案：\n"
                "- 列目录：使用 ls 或 python -c \"import os; print(os.listdir('.'))\"\n"
                "- 读文件头：使用 cat file.txt 或 python open('file').read()\n"
                "- 搜索内容：使用 grep -r 或 python 字符串匹配\n"
                "- 统计行数：使用 wc -l 或 python len(open('f').readlines())"
            ),
            "no_write_outside_workspace": (
                "替代方案：将文件写入工作区内允许的目录：\n"
                "- 分析报告：data/autonomous/reports/\n"
                "- 临时文件：tmp/\n"
                "- 工具脚本：scripts/"
            ),
        }
        redirect_hint = _redirects.get(rule.rule_id, "请使用等效的安全命令替代。")

        logger.warning(
            "[DEATH ZONE] Blocked tool=%s rule=%s: %s",
            tool_name, rule.rule_id, rule.description,
        )
        return Verdict(
            kind=VerdictKind.FAIL,
            output={
                "blocked": True,
                "rule_id": rule.rule_id,
                "result": (
                    f"[DEATH ZONE BLOCKED] 规则 '{rule.rule_id}' 拒绝此操作：{rule.description}\n\n"
                    f"{redirect_hint}"
                ),
            },
            diagnosis=f"Death zone: {rule.rule_id}",
        )


class IntentParseRouter(Router):
    """意图解析节点 — 从 tool_calls 中提取结构化意图。

    DAG 上下文：接收 LLM 的 FAIL 输出，提取每个 tool_call 的意图信息。
    """

    INPUT_KEYS = ["tool_calls"]

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.PASS, output=input_data)

        tool_calls = input_data.get("tool_calls", [])
        intents = []
        for tc in tool_calls:
            intents.append({
                "tool_name": tc.get("tool_name", ""),
                "action_type": _classify_tool_action(tc.get("tool_name", "")),
            })

        return Verdict(
            kind=VerdictKind.PASS,
            output={**input_data, "intents": intents},
        )


def _classify_tool_action(tool_name: str) -> str:
    """Simple rule-based action classification."""
    if tool_name in ("bash", "shell"):
        return "execute"
    elif tool_name == "str_replace_editor":
        return "file_edit"
    elif tool_name == "think":
        return "reasoning"
    elif tool_name == "finish":
        return "terminate"
    return "unknown"
