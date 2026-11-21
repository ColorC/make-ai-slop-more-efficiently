# [OMNI] origin=codex domain=services/session-companion ts=2026-08-11 type=service
# [OMNI] material_id="material:services.session-companion.policy.py"
"""Deterministic admission policy for companion-maintained records.

Conversation digests, durable decisions, and ReviewStage submissions are three
different sinks.  The low-cost model may propose all three, but it cannot turn
ordinary conversation or inferred user interest into a review material.
"""

from __future__ import annotations

import re

_REVIEW_REQUEST = re.compile(
    r"(?:提交|送交|送去|发起).{0,10}(?:审阅|审核|review|审阅台)|"
    r"(?:送审)(?:这份|该|最终|报告|方案|材料|成品)?|"
    r"(?:请|帮我)(?:直接)?(?:审阅|审核|review)(?:这份|该|以下|最终|报告|方案|材料|成品)|"
    r"(?:审阅|审核)(?:这份|该|以下|最终)(?:材料|方案|报告|成品)?|"
    r"\bsubmit.{0,12}review\b|\breview (?:this|the)\b",
    re.IGNORECASE | re.DOTALL,
)
_REVIEW_NEGATION = re.compile(
    r"(?:不要|不用|无需|别|取消).{0,12}(?:提交|送审|审阅|审核|review)|"
    r"\b(?:do not|don't|no need to)\s+(?:submit|review)\b",
    re.IGNORECASE | re.DOTALL,
)
_REVIEW_META_DISCUSSION = re.compile(
    r"(?:提交|送审|审阅|审核|review).{0,24}"
    r"(?:功能|机制|按钮|入口|流程|能力|逻辑|代码|实现|接口|正则|规则|策略|测试|场景|模块|"
    r"feature|mechanism|button|entry|flow|capability|implementation|api|regex|rule|test)|"
    r"(?:功能|机制|按钮|入口|流程|能力|逻辑|代码|实现|接口|正则|规则|策略|测试|场景|模块|"
    r"feature|mechanism|button|entry|flow|capability|implementation|api|regex|rule|test)"
    r".{0,24}(?:提交|送审|审阅|审核|review)",
    re.IGNORECASE | re.DOTALL,
)
_MATERIAL_ID = re.compile(r"\bmat_[0-9a-f]{8,}\b", re.IGNORECASE)
_FEEDBACK_CONTEXT = re.compile(
    r"(?:(?:材料|方案|报告|成品|审阅|审核).{0,16}(?:通过|接受|拒绝|驳回|修改|评论|反馈|意见)|"
    r"(?:通过|接受|拒绝|驳回|修改|评论|反馈|意见).{0,16}(?:材料|方案|报告|成品|审阅|审核))",
    re.IGNORECASE | re.DOTALL,
)
_DURABLE_DECISION = re.compile(
    r"(?:以后|今后|长期|永久|永远|默认|不再|一律|作为规则|规则(?:是|改为)|原则(?:是|改为)|"
    r"策略(?:是|改为)|决定|选择|确定(?:为|采用)|统一(?:采用|使用|规定)|"
    r"(?:不要|别).{0,8}(?:啥|什么|任何|所有|一切).{0,12}(?:都|一律)|"
    r"\b(?:from now on|long[- ]term|permanently|never|always|default to|decide|choose)\b)",
    re.IGNORECASE,
)


def explicit_review_requested(text: str) -> bool:
    """Return true only for a current-turn, direct request to review a deliverable."""

    value = str(text or "").strip()
    return bool(
        value
        and _REVIEW_REQUEST.search(value)
        and not _REVIEW_NEGATION.search(value)
        and not _REVIEW_META_DISCUSSION.search(value)
    )


def material_submission_allowed(user_input: str) -> bool:
    """ReviewStage is deny-by-default; inferred interest never opens the gate."""

    return explicit_review_requested(user_input)


def feedback_context_requested(user_input: str) -> bool:
    """Load existing review records only when the current turn points at feedback."""

    value = str(user_input or "").strip()
    return bool(value and (_MATERIAL_ID.search(value) or _FEEDBACK_CONTEXT.search(value)))


def referenced_material_ids(user_input: str) -> list[str]:
    return list(dict.fromkeys(match.group(0) for match in _MATERIAL_ID.finditer(str(user_input or ""))))


def explicit_decision_quote(user_input: str, quote: str) -> bool:
    """Accept only verbatim, durable user policy/choice evidence."""

    source = str(user_input or "")
    excerpt = str(quote or "").strip()
    return bool(excerpt and excerpt in source and _DURABLE_DECISION.search(excerpt))


__all__ = [
    "explicit_decision_quote",
    "explicit_review_requested",
    "feedback_context_requested",
    "material_submission_allowed",
    "referenced_material_ids",
]
