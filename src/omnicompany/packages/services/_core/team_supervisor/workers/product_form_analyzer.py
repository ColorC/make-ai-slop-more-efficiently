# [OMNI] origin=codex domain=services/team_supervisor/workers ts=2026-08-14T00:00:00Z type=contract
# [OMNI] material_id="material:core.team_supervisor.workers.product_form_analyzer.explorer.py"
"""Omni-owned contract for the external ProductFormAnalyzer position."""

from __future__ import annotations

from typing import Any, Final


PRODUCT_FORM_DESCRIPTION: Final = (
    "Q1 产物形式答案 · AGENT. 探索 target FORMAT_OUT schema + 末节点 worker + "
    "历史 trace, 产 product_form_brief 全自然语言句子."
)
PRODUCT_FORM_FORMAT_IN: Final = "team_supervisor.target_metadata"
PRODUCT_FORM_FORMAT_OUT: Final = "team_supervisor.product_form_brief"
PRODUCT_FORM_RUNTIME_INSTRUCTIONS: Final = """You are the ProductFormAnalyzer for team_supervisor.
Determine the concrete shape of the target Team's output. Ground the answer in
the actual FORMAT_OUT schema, the sink/assembler worker implementation, and a
historical output when one is available.

The business input is target_metadata and contains paths plus the FORMAT_OUT
identifier. Use only these read-only Cordis tools:
- omni_workspace_read: read a specific file inside the granted workspace;
- omni_workspace_list: list a directory inside the granted workspace;
- omni_workspace_search: literal recursive text search inside the workspace.

Inspect formats.py and the final worker implementation. `schema_fields_observed`
must contain real field names read from the registered schema. Failure signals
must be specific, observable conditions that can become deterministic oracles.
Never invent a path or field. Return only the requested JSON object. Do not
classify, score, rate, tier, tag, or otherwise judge the target.
"""
PRODUCT_FORM_OUTPUT_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "essence": {
            "type": "string",
            "minLength": 20,
            "description": "一句话说本质 · 完整自然语言句子",
        },
        "minimal_passing_evidence": {
            "type": "string",
            "minLength": 20,
            "description": "最低合格产物的可程序化特征",
        },
        "failure_signals": {
            "type": "array",
            "minItems": 2,
            "items": {"type": "string", "minLength": 10},
            "description": "具体、可观察的失败信号",
        },
        "concrete_examples": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "excerpt": {"type": "string"},
                    "note": {"type": "string", "minLength": 1},
                },
                "required": ["path", "note"],
            },
            "description": "历史产物或实现中的具体例子",
        },
        "schema_fields_observed": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
            "description": "FORMAT_OUT schema 中真实存在的字段",
        },
    },
    "required": [
        "essence",
        "minimal_passing_evidence",
        "failure_signals",
        "concrete_examples",
        "schema_fields_observed",
    ],
}


__all__ = [
    "PRODUCT_FORM_DESCRIPTION",
    "PRODUCT_FORM_FORMAT_IN",
    "PRODUCT_FORM_FORMAT_OUT",
    "PRODUCT_FORM_OUTPUT_SCHEMA",
    "PRODUCT_FORM_RUNTIME_INSTRUCTIONS",
]
