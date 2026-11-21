# [OMNI] origin=claude-code ts=2026-07-03T00:00:00Z type=module summary="token_ledger cron↔meter caller 字符串匹配(仅限白名单已知调LLM任务), 每条结果显式标注非强关联" why="overnight-run.md 第六节错误样本㊃: 关联只做确定性, cron任务名↔meter.caller只匹配已知会调LLM的任务, 禁止对未登记任务做模糊匹配, 结果必须显式标注免责说明" tags=token-ledger,cron,link,string-match
"""cron 任务名 ↔ meter caller 的字符串匹配(仅限白名单)。

铁律(overnight-run.md 第六节错误样本㊃): 只在 `known_llm_cron_tasks` 里显式登记过的
任务名才参与匹配; 不在白名单里的任务名, 即使字符串上看起来很像某个 caller, 也绝不匹配。
每条匹配结果必须带免责说明"字符串匹配, 非强关联"——这不是可信的强类型绑定, 只是提示线索。
"""

from __future__ import annotations

import re
from typing import Any, Iterable

_NOTE = "字符串匹配, 非强关联(cron 任务名与 meter caller 的启发式线索, 非强类型绑定)"

_STRIP_PREFIXES = ("gov-", "guard-")
_STRIP_SUFFIXES = ("-daily", "-weekly", "-monthly", "-hourly")


def _keywords_for_task(task_name: str) -> list[str]:
    """从 cron 任务名提取可能对应 caller 关键词的片段(去前后缀, 按 - 拆分, 粗略去复数)。"""
    core = task_name
    for pre in _STRIP_PREFIXES:
        if core.startswith(pre):
            core = core[len(pre):]
            break
    for suf in _STRIP_SUFFIXES:
        if core.endswith(suf):
            core = core[: -len(suf)]
            break
    parts = [p for p in re.split(r"[-_]", core) if p]
    keywords: list[str] = []
    for p in parts:
        keywords.append(p)
        if p.endswith("s") and len(p) > 3:
            keywords.append(p[:-1])  # 粗略去复数, 如 plans -> plan
    return keywords


def link_cron_tasks_to_callers(
    *,
    known_llm_cron_tasks: Iterable[str] | None,
    cron_task_names: Iterable[str] | None,
    callers: Iterable[str],
) -> list[dict[str, Any]]:
    """只对 known_llm_cron_tasks ∩ cron_task_names 的任务名做启发式匹配, 其余一律不参与。"""
    known = set(known_llm_cron_tasks or [])
    actual = set(cron_task_names or [])
    eligible = sorted(known & actual)
    if not eligible:
        return []

    caller_list = list(callers)
    links: list[dict[str, Any]] = []
    for task in eligible:
        keywords = _keywords_for_task(task)
        matched_callers = [
            c for c in caller_list
            if any(kw and kw.lower() in c.lower() for kw in keywords)
        ]
        if not matched_callers:
            continue
        links.append({
            "cron_task": task,
            "matched_callers": matched_callers,
            "confidence": _NOTE,
            "note": _NOTE,
        })
    return links


__all__ = ["link_cron_tasks_to_callers"]
