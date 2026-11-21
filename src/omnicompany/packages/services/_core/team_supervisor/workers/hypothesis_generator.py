# [OMNI] origin=codex domain=services/team_supervisor/workers ts=2026-08-14T00:00:00Z type=contract
# [OMNI] material_id="material:core.team_supervisor.workers.hypothesis_generator.engine.py"
"""Omni-owned contract for the external HypothesisGenerator position."""

from __future__ import annotations

from typing import Any, Final


HYPOTHESIS_DESCRIPTION: Final = (
    "假设产生 · AGENT. 综合三问 brief，产 ≥10 条 (条件→预期) 假设，"
    "全字段自然语言句子."
)
HYPOTHESIS_FORMAT_IN: Final = [
    "team_supervisor.product_form_brief",
    "team_supervisor.design_purpose_brief",
    "team_supervisor.health_criteria",
]
HYPOTHESIS_FORMAT_OUT: Final = "team_supervisor.hypothesis_set"
HYPOTHESIS_RUNTIME_INSTRUCTIONS: Final = """You are the HypothesisGenerator for team_supervisor.
Synthesize the Q1 product-form brief, Q2 design-purpose brief, and Q3 health
criteria from the business input into at least ten concrete condition-to-
expectation hypotheses. The three `_from_*` objects are the authoritative
inputs; no workspace exploration is required or permitted.

Derive hypotheses from Q1 failure signals and minimum evidence, Q2 non-goals
and stakeholder use, and Q3 red flags and oracle strategies. Every oracle hint
must describe a programmatic check. IDs must be unique and sequential in the
form H-001, H-002, and so on. Return only the requested JSON object. Do not add
priority, severity, category, tier, score, or other classification fields.
"""
HYPOTHESIS_OUTPUT_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "hypotheses": {
            "type": "array",
            "minItems": 10,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "pattern": "^H-\\d{3}$"},
                    "condition": {"type": "string", "minLength": 20},
                    "expectation": {"type": "string", "minLength": 20},
                    "oracle_code_hint": {"type": "string", "minLength": 15},
                    "rationale": {"type": "string", "minLength": 15},
                },
                "required": [
                    "id",
                    "condition",
                    "expectation",
                    "oracle_code_hint",
                    "rationale",
                ],
            },
        },
    },
    "required": ["hypotheses"],
}


__all__ = [
    "HYPOTHESIS_DESCRIPTION",
    "HYPOTHESIS_FORMAT_IN",
    "HYPOTHESIS_FORMAT_OUT",
    "HYPOTHESIS_OUTPUT_SCHEMA",
    "HYPOTHESIS_RUNTIME_INSTRUCTIONS",
]
