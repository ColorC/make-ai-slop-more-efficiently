"""Specification diagnosis through the external AgentRuntime boundary."""

from __future__ import annotations

from omnicompany.packages.services._core.agent import AgentSpec

from ._external_diagnostic import ExternalDiagnosticWorker


SPEC_DIAGNOSTIC_SPEC = AgentSpec(
    id="doctor.spec_diagnostic",
    name="SpecDiagnosticAgent",
    domain="doctor",
    llm_model="deepseek-v4-flash",
    llm_max_tokens=12_000,
    llm_max_turns=24,
    llm_timeout_seconds=600,
    output_materials=("doctor.spec_diagnosis.verdict",),
    primary_output="doctor.spec_diagnosis.verdict",
    trigger_materials=("doctor.spec_diagnosis.request",),
    accepted_input_materials=("doctor.spec_diagnosis.request",),
    forbidden_input_materials=(
        "doctor.spec_diagnosis.verdict",
        "doctor.health_finding",
    ),
    user_input_required_fields=(
        "target_entity_path",
        "target_entity_kind",
        "applicable_standards",
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


class SpecDiagnosticAgent(ExternalDiagnosticWorker):
    """Propose a standards-based verdict; OmniCompany owns acceptance."""

    SPEC = SPEC_DIAGNOSTIC_SPEC
    DESCRIPTION = (
        "Read-only specification diagnosis through the external AgentRuntime; "
        "OmniCompany validates and publishes the proposed verdict."
    )
    FORMAT_IN = "doctor.spec_diagnosis.request"
    FORMAT_OUT = "doctor.spec_diagnosis.verdict"
    DIAGNOSIS_KIND = "spec"
    TARGET_FIELD = "target_entity_path"
    REFERENCE_FIELD = "applicable_standards"
    CALLER = "doctor.spec-diagnostic"


__all__ = ["SPEC_DIAGNOSTIC_SPEC", "SpecDiagnosticAgent"]
