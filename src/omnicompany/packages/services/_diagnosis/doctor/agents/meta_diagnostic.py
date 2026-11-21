"""Team-level Doctor diagnosis through the external read-only AgentRuntime."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from omnicompany.packages.services._core.agent import AgentSpec
from omnicompany.runtime.agent_runtime import READONLY_AGENT_TOOLS

from ._external_diagnostic import DIAGNOSIS_RESULT_SCHEMA, ExternalDiagnosticWorker


_ANTI_PATTERNS = (
    "docs/plans/diagnosis/[2026-05-05]诊断重整/data/anti_patterns/archetypes.yaml"
)
_AUTHORITY_MAP = (
    "docs/plans/diagnosis/[2026-05-05]诊断重整/data/"
    "canonical_anchors/standards_authority_map.yaml"
)

META_DIAGNOSIS_RESULT_SCHEMA = deepcopy(DIAGNOSIS_RESULT_SCHEMA)
META_DIAGNOSIS_RESULT_SCHEMA["required"] = [
    *META_DIAGNOSIS_RESULT_SCHEMA["required"],
    "answers",
    "recommended_facilities",
]
META_DIAGNOSIS_RESULT_SCHEMA["properties"].update(
    {
        "answers": {
            "type": "array",
            "minItems": 1,
            "maxItems": 10,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["question", "answer", "evidence_paths"],
                "properties": {
                    "question": {"type": "integer", "minimum": 1, "maximum": 10},
                    "answer": {"type": "string", "minLength": 30, "maxLength": 4000},
                    "evidence_paths": {
                        "type": "array",
                        "maxItems": 30,
                        "items": {"type": "string", "minLength": 1, "maxLength": 1000},
                    },
                },
            },
        },
        "recommended_facilities": {
            "type": "array",
            "maxItems": 30,
            "items": {"type": "string", "minLength": 20, "maxLength": 2000},
        },
    }
)

_INSTRUCTIONS = """\
You are OmniCompany Doctor's team-level meta-diagnosis investigator. Inspect the
canonical team directory, the supplied anti-pattern catalogue, and the standards
authority map. You have read-only workspace and Git evidence tools. Do not run
tests, modify files, persist findings, publish Materials, rank or mutate hypotheses,
or call another agent. If execution evidence is unavailable, state that explicitly.

Answer exactly the requested subset of these questions, once each and in numeric
order: (1) whether verification facilities are needed; (2) how to sample or ask a
human if dedicated facilities are unnecessary; (3) what facilities actually exist;
(4) what they verify; (5) which important Materials are unverified; (6) facility
quality and discriminating evidence; (7) repair history visible in Git; (8) team
health and concrete anti-pattern matches; (9) what can and cannot be concluded
without executing facilities; (10) the facilities the team should have.

Return a proposal only. Every finding must use entity_id equal to the canonical
team path and entity_kind=team. Finding kinds may be spec, hypothesis, exemplar,
or plan. Cite only canonical existing workspace paths in consulted_references and
answer evidence_paths. Do not use severity, score, confidence, tier, grade, rating,
or level fields. Return exactly one schema-valid JSON object without Markdown.
"""


META_DIAGNOSTIC_SPEC = AgentSpec(
    id="doctor.meta_diagnostic",
    name="MetaDiagnosticAgent",
    domain="doctor",
    llm_model="deepseek-v4-flash",
    llm_max_tokens=16_000,
    llm_max_turns=32,
    llm_timeout_seconds=600,
    output_materials=("doctor.meta_diagnosis.verdict",),
    primary_output="doctor.meta_diagnosis.verdict",
    trigger_materials=("doctor.meta_diagnosis.request",),
    accepted_input_materials=("doctor.meta_diagnosis.request",),
    forbidden_input_materials=(
        "doctor.meta_diagnosis.verdict",
        "doctor.health_finding",
    ),
    user_input_required_fields=("team_path", "focus_questions", "depth"),
    tools=(),
    workspace={"permission_mode": "readonly", "write_prefixes": ()},
    test_baseline={
        "green_samples": ("src/omnicompany/packages/services/_utility/csv_to_md/",),
        "red_samples": (
            "src/omnicompany/packages/services/_diagnosis/doctor/"
            "_test_fixtures/red_teams/red_minimal_team/",
        ),
    },
)


class MetaDiagnosticAgent(ExternalDiagnosticWorker):
    """Propose a team health verdict; Omni owns publication and follow-up work."""

    SPEC = META_DIAGNOSTIC_SPEC
    DESCRIPTION = (
        "Read-only team meta-diagnosis through the external AgentRuntime; "
        "OmniCompany validates the complete proposal."
    )
    FORMAT_IN = "doctor.meta_diagnosis.request"
    FORMAT_OUT = "doctor.meta_diagnosis.verdict"
    DIAGNOSIS_KIND = "meta"
    TARGET_FIELD = "team_path"
    FIXED_TARGET_KIND = "team"
    FIXED_REFERENCES = (_ANTI_PATTERNS, _AUTHORITY_MAP)
    EXTRA_FIELDS = ("focus_questions", "depth")
    CALLER = "doctor.meta-diagnostic"
    RESULT_SCHEMA = META_DIAGNOSIS_RESULT_SCHEMA
    INSTRUCTIONS = _INSTRUCTIONS
    ALLOWED_FINDING_KINDS = ("spec", "hypothesis", "exemplar", "plan")
    ALLOWED_TOOLS = READONLY_AGENT_TOOLS

    def _build_request(self, input_data: Any) -> tuple[dict[str, Any], str, str, list[str]]:
        request, target, target_kind, references = super()._build_request(input_data)
        focus = input_data.get("focus_questions") if isinstance(input_data, dict) else None
        if (
            not isinstance(focus, list)
            or not focus
            or any(type(item) is not int or item < 1 or item > 10 for item in focus)
            or len(set(focus)) != len(focus)
        ):
            raise ValueError("focus_questions must be unique integers from 1 through 10")
        depth = str(input_data.get("depth") or "").strip()
        if depth not in {"quick", "full", "deep"}:
            raise ValueError("depth must be quick, full, or deep")
        request["focus_questions"] = sorted(focus)
        request["depth"] = depth
        return request, target, target_kind, references

    def _validate_result(
        self,
        result: dict[str, Any],
        *,
        request: dict[str, Any],
        target: str,
        target_kind: str,
        references: list[str],
    ) -> str | None:
        error = super()._validate_result(
            result,
            request=request,
            target=target,
            target_kind=target_kind,
            references=references,
        )
        if error:
            return error
        answers = result.get("answers")
        if not isinstance(answers, list):
            return "answers must be a list"
        questions = [item.get("question") for item in answers if isinstance(item, dict)]
        if questions != request["focus_questions"]:
            return "answers must exactly match requested questions in numeric order"
        for index, answer in enumerate(answers):
            if not isinstance(answer, dict):
                return f"answers[{index}] must be an object"
            if len(str(answer.get("answer") or "").strip()) < 30:
                return f"answers[{index}].answer is too short"
            evidence_paths = answer.get("evidence_paths")
            if not isinstance(evidence_paths, list):
                return f"answers[{index}].evidence_paths must be a list"
            # The canonical team root is the primary request target and may be cited
            # as directory-level evidence without being duplicated in the list of
            # additional consulted reference files.
            unknown = sorted(
                set(evidence_paths) - set(result["consulted_references"]) - {target}
            )
            if unknown:
                return f"answers[{index}] cites unconsulted evidence paths: {unknown}"
        recommendations = result.get("recommended_facilities")
        if not isinstance(recommendations, list):
            return "recommended_facilities must be a list"
        if 10 in request["focus_questions"] and not recommendations:
            return "question 10 requires at least one recommended facility"
        return None


__all__ = [
    "META_DIAGNOSIS_RESULT_SCHEMA",
    "META_DIAGNOSTIC_SPEC",
    "MetaDiagnosticAgent",
]
