# [OMNI] origin=codex domain=services/team_supervisor/workers ts=2026-08-14T00:00:00Z type=contract
# [OMNI] material_id="material:core.team_supervisor.workers.health_criteria_engine.py"
"""Omni-owned contract for the external HealthCriteriaDesigner position.

The TeamSpec and these input/output semantics remain authoritative in Omni.
Execution belongs to the replaceable AgentRuntime implementation selected by
the Team binding; this module deliberately contains no legacy Agent loop or
provider-specific type.
"""

from __future__ import annotations

from typing import Any, Final


HEALTH_CRITERIA_DESCRIPTION: Final = (
    "Q3 健康判据 · SOFT. 综合 Q1+Q2 brief 一次性产 health_criteria 全自然语言句子."
)
HEALTH_CRITERIA_FORMAT_IN: Final = [
    "team_supervisor.product_form_brief",
    "team_supervisor.design_purpose_brief",
]
HEALTH_CRITERIA_FORMAT_OUT: Final = "team_supervisor.health_criteria"

HEALTH_CRITERIA_RUNTIME_INSTRUCTIONS: Final = """You are the HealthCriteriaDesigner for team_supervisor.
Synthesize Q1 product-form and Q2 design-purpose briefs into health criteria.
Return only the requested structured JSON object.
Requirements:
- key_observations: at least 3 concrete natural-language observations, each at least 10 characters.
- red_flags: at least 2 concrete natural-language warning signs, each at least 10 characters.
- oracle_strategies: at least 3 objects with what_to_check and implementation_hint; both are natural-language sentences of at least 15 characters.
- Derive oracles from Q1 failure signals and red flags from Q2 non-goals.
- Do not add severity, priority_level, metric, threshold, or score fields.
- Do not classify or score the target Team. Describe evidence and executable checks.
"""

HEALTH_CRITERIA_OUTPUT_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "key_observations": {
            "type": "array",
            "minItems": 3,
            "items": {"type": "string", "minLength": 10},
            "description": "应观察什么 · 句子列表",
        },
        "red_flags": {
            "type": "array",
            "minItems": 2,
            "items": {"type": "string", "minLength": 10},
            "description": "红旗信号 · 具体特征句",
        },
        "oracle_strategies": {
            "type": "array",
            "minItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "what_to_check": {"type": "string", "minLength": 15},
                    "implementation_hint": {"type": "string", "minLength": 15},
                },
                "required": ["what_to_check", "implementation_hint"],
            },
            "description": "验证策略 · 全句子",
        },
    },
    "required": ["key_observations", "red_flags", "oracle_strategies"],
}


__all__ = [
    "HEALTH_CRITERIA_DESCRIPTION",
    "HEALTH_CRITERIA_FORMAT_IN",
    "HEALTH_CRITERIA_FORMAT_OUT",
    "HEALTH_CRITERIA_OUTPUT_SCHEMA",
    "HEALTH_CRITERIA_RUNTIME_INSTRUCTIONS",
]
