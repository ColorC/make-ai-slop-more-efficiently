# [OMNI] origin=codex domain=services/team_supervisor/workers ts=2026-08-14T00:00:00Z type=contract
# [OMNI] material_id="material:core.team_supervisor.workers.purpose_interpreter.explorer.py"
"""Omni-owned contract for the external PurposeInterpreter position.

The TeamSpec, task semantics, and JSON output schema remain authoritative in
Omni. The replaceable AgentRuntime owns execution and Cordis supplies only the
three grant-checked workspace read tools declared by the binding.
"""

from __future__ import annotations

from typing import Any, Final


PURPOSE_DESCRIPTION: Final = (
    "Q2 设计目的答案 · AGENT. 探索 DESIGN.md + team.py docstring + worker docstring + "
    "调用方代码, 产 design_purpose_brief 全自然语言句子."
)
PURPOSE_FORMAT_IN: Final = "team_supervisor.target_metadata"
PURPOSE_FORMAT_OUT: Final = "team_supervisor.design_purpose_brief"
PURPOSE_RUNTIME_INSTRUCTIONS: Final = """You are the PurposeInterpreter for team_supervisor.
Determine why the target Team exists, what concrete problem it solves, what it
replaces, what it deliberately does not do, and how a stakeholder uses its
output. Ground every claim in files you actually inspect.

The business input is target_metadata and contains repository-relative paths.
Use only these read-only Cordis tools when evidence is needed:
- omni_workspace_read: read a specific file inside the granted workspace;
- omni_workspace_list: list a directory inside the granted workspace;
- omni_workspace_search: literal recursive text search inside the workspace.

Inspect the target DESIGN.md when present, team.py, representative worker
docstrings, and at least one caller or other source that explains the Team's
purpose. Never invent a file path. Return only the requested JSON object.
All semantic fields must be natural-language statements, not classifications,
scores, maturity judgments, categories, kinds, levels, tiers, or tags.
"""
PURPOSE_OUTPUT_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "essence": {
            "type": "string",
            "minLength": 30,
            "description": "解决什么具体问题 · 完整句子",
        },
        "replaces": {
            "type": "string",
            "minLength": 10,
            "description": "没有它时用什么手段 · 句子",
        },
        "non_goals": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 10},
            "description": "它不做什么 · 反向定义",
        },
        "stakeholder_use": {
            "type": "string",
            "minLength": 20,
            "description": "谁消费产出怎么用 · 具体场景句",
        },
        "evidence_sources": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "file": {"type": "string", "minLength": 1},
                    "section": {"type": "string"},
                },
                "required": ["file"],
            },
            "description": "推断依据来源 · 必须引用真实文件",
        },
    },
    "required": [
        "essence",
        "replaces",
        "non_goals",
        "stakeholder_use",
        "evidence_sources",
    ],
}


__all__ = [
    "PURPOSE_DESCRIPTION",
    "PURPOSE_FORMAT_IN",
    "PURPOSE_FORMAT_OUT",
    "PURPOSE_OUTPUT_SCHEMA",
    "PURPOSE_RUNTIME_INSTRUCTIONS",
]
