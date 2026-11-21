"""Hypothesis diagnosis through the external AgentRuntime boundary."""

from __future__ import annotations

from omnicompany.packages.services._core.agent import AgentSpec

from ._external_diagnostic import ExternalDiagnosticWorker


HYPOTHESIS_DIAGNOSTIC_SPEC = AgentSpec(
    id="doctor.hypothesis_diagnostic",
    name="HypothesisDiagnosticAgent",
    domain="doctor",
    llm_model="deepseek-v4-flash",
    llm_max_tokens=12_000,
    llm_max_turns=24,
    llm_timeout_seconds=600,
    output_materials=("doctor.hypothesis_diagnosis.verdict",),
    primary_output="doctor.hypothesis_diagnosis.verdict",
    trigger_materials=("doctor.hypothesis_diagnosis.request",),
    accepted_input_materials=("doctor.hypothesis_diagnosis.request",),
    forbidden_input_materials=(
        "doctor.hypothesis_diagnosis.verdict",
        "doctor.health_finding",
    ),
    user_input_required_fields=(
        "target_entity_path",
        "target_entity_kind",
        "applicable_hypothesis_paths",
    ),
    tools=(),
    workspace={"permission_mode": "readonly", "write_prefixes": ()},
    test_baseline={
        "green_samples": (
            "src/omnicompany/packages/services/_utility/csv_to_md/workers/csv_reader.py",
        ),
        "red_samples": (
            "src/omnicompany/packages/services/_diagnosis/doctor/_test_fixtures/red_workers/red_minimal_worker.py",
        ),
    },
)


class HypothesisDiagnosticAgent(ExternalDiagnosticWorker):
    """Propose a hypothesis-based verdict; OmniCompany owns acceptance."""

    SPEC = HYPOTHESIS_DIAGNOSTIC_SPEC
    DESCRIPTION = (
        "Read-only hypothesis diagnosis through the external AgentRuntime; "
        "OmniCompany validates and publishes the proposed verdict."
    )
    FORMAT_IN = "doctor.hypothesis_diagnosis.request"
    FORMAT_OUT = "doctor.hypothesis_diagnosis.verdict"
    DIAGNOSIS_KIND = "hypothesis"
    TARGET_FIELD = "target_entity_path"
    REFERENCE_FIELD = "applicable_hypothesis_paths"
    CALLER = "doctor.hypothesis-diagnostic"


__all__ = ["HYPOTHESIS_DIAGNOSTIC_SPEC", "HypothesisDiagnosticAgent"]
