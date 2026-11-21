"""Deterministic routing judge for BOSS SIGHT controller decisions.

The judge returns abstract model tiers only. Real model names remain owned by
controller/model_resolver.py.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class JudgeTierDecision:
    model_hint: str = "default"
    needs_orchestration: bool = False
    confidence: float = 0.5
    reasons: list[str] = field(default_factory=list)
    warning_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decide_tier(
    *,
    kind: str,
    tier: str,
    title: str,
    content: str = "",
    structure_warnings: list[dict[str, Any]] | None = None,
    context: str = "",
) -> JudgeTierDecision:
    text = f"{title}\n{content[:6000]}\n{context}".lower()
    warnings = structure_warnings or []
    reasons: list[str] = []
    score = 0

    if tier == "mandatory":
        score += 3
        reasons.append("mandatory material")
    elif tier == "important":
        score += 1
        reasons.append("important material")
    elif tier == "ignored":
        score -= 1
        reasons.append("ignored tier")

    if kind in {"html", "custom_web_template"}:
        score += 2
        reasons.append(f"{kind} needs structure/rendering judgment")
    elif kind == "key_question":
        score += 1
        reasons.append("key_question affects downstream direction")

    if warnings:
        score += min(2, len(warnings))
        reasons.append(f"{len(warnings)} structure warning(s)")

    complex_terms = (
        "architecture", "migration", "integration", "security", "guard",
        "roadmap", "orchestration", "multi-agent", "多 agent", "架构", "迁移",
        "安全", "集成", "路线", "编排",
    )
    if any(term in text for term in complex_terms):
        score += 2
        reasons.append("complexity keywords detected")

    if len(content) > 4000:
        score += 1
        reasons.append("long material")

    if score >= 4:
        model_hint = "high"
        confidence = 0.78
    elif score <= 0:
        model_hint = "low"
        confidence = 0.68
    else:
        model_hint = "default"
        confidence = 0.62

    needs_orchestration = score >= 3 or kind in {"html", "custom_web_template"} or len(warnings) >= 2
    if not reasons:
        reasons.append("no strong routing signal")

    return JudgeTierDecision(
        model_hint=model_hint,
        needs_orchestration=needs_orchestration,
        confidence=confidence,
        reasons=reasons,
        warning_count=len(warnings),
    )


__all__ = ["JudgeTierDecision", "decide_tier"]
