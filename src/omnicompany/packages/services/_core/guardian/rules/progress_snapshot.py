# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-06-27T00:00:00Z type=config
# [OMNI] summary="Guardian 规则 OMNI-097 · plan.md 正文写绝对完成度百分比(指涉式进度快照, 该在 whatnow)。从 progress_steward metric 探针稳定复现项结晶成确定性规则(needs_judgment)。"
# [OMNI] why="进度唯一真源是 whatnow;plan.md 正文写'完成度80%'这种快照写一周就 stale。这是 progress_steward 反复命中、可无歧义确定性捕获的一类, 按 governance_semantic_first 结晶成规则。"
# [OMNI] tags=guardian,progress-ssot,crystallized-rule,OMNI-097
# [OMNI] material_id="material:core.guardian.rules.progress_snapshot_in_plan.py"
"""Guardian 规则 · OMNI-097 · plan.md 正文进度快照(needs_judgment)。

检查 docs/plans/**/plan.md 正文(非 OMNI 头)里出现绝对完成度百分比 '完成度 N%' / 'N% 完成' / '进度 N%'。
这类是指涉式进度快照 —— 进度该在 whatnow, 文件里只许自我陈述。命中 → 建议剥离/标'以 whatnow 为准'。
缺失不报。豁免归档。是 progress_steward.probe 的 metric 类里"无歧义"子集结晶成的确定性规则。
"""
from __future__ import annotations

import re
from pathlib import Path

from ._base import FileContext, GuardianRule, _has_content

_PCT_RE = re.compile(r"完成度\s*[:：]?\s*\d{1,3}\s*%|\d{1,3}\s*%\s*完成|进度\s*[:：]?\s*\d{1,3}\s*%")


def _is_plan_md(ctx: FileContext) -> bool:
    p = ctx.path.replace("\\", "/")
    return p.endswith("plan.md") and "docs/plans/" in p and "_archive" not in p


def _check_progress_snapshot(ctx: FileContext) -> bool:
    if not _has_content(ctx) or not _is_plan_md(ctx):
        return False
    in_fence = False
    for line in (ctx.content or "").split("\n"):
        s = line.lstrip()
        if s.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence or s.startswith(("<!-- [OMNI]", "# [OMNI]")):
            continue
        if _PCT_RE.search(line):
            return True
    return False


RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-097",
        name="progress-snapshot-in-plan",
        severity="LOW",
        description=(
            "plan.md 正文写绝对完成度百分比(完成度 N% / N% 完成 / 进度 N%)。"
            "进度唯一真源是 whatnow, 文件只许自我陈述;这类快照写一周就 stale。"
        ),
        check=_check_progress_snapshot,
        disposition=["warn"],
        certainty="needs_judgment",
        message_template=(
            "{path} 正文含绝对完成度百分比(指涉式进度快照).\n"
            "  进度真源是 whatnow(:8230);剥离/标注走 omni governance progress-strip <plan_id> --lines N.\n"
            "  自评进度走 omni governance plans-sync(自动写进 whatnow)."
        ),
    ),
]
