"""Plan diagnosis through the external AgentRuntime boundary."""

from __future__ import annotations

from omnicompany.packages.services._core.agent import AgentSpec

from ._external_diagnostic import ExternalDiagnosticWorker


PLAN_DIAGNOSTIC_SPEC = AgentSpec(
    id="doctor.plan_diagnostic",
    name="PlanDiagnosticAgent",
    domain="doctor",
    llm_model="deepseek-v4-flash",
    llm_max_tokens=12_000,
    llm_max_turns=24,
    llm_timeout_seconds=600,
    output_materials=("doctor.plan_diagnosis.verdict",),
    primary_output="doctor.plan_diagnosis.verdict",
    trigger_materials=("doctor.plan_diagnosis.request",),
    accepted_input_materials=("doctor.plan_diagnosis.request",),
    forbidden_input_materials=(
        "doctor.plan_diagnosis.verdict",
        "doctor.health_finding",
    ),
    user_input_required_fields=(
        "target_plan_path",
        "applicable_template_paths",
        "check_modes",
    ),
    tools=(),
    workspace={"permission_mode": "readonly", "write_prefixes": ()},
    test_baseline={
        "green_samples": (
            "docs/plans/diagnosis/[2026-05-05]????/samples/sample_compliant_plan_exemplar_library.md",
        ),
        "red_samples": (
            "src/omnicompany/packages/services/_diagnosis/doctor/_test_fixtures/red_plans/red_minimal_plan.md",
        ),
    },
)


class PlanDiagnosticAgent(ExternalDiagnosticWorker):
    """Propose a plan verdict; OmniCompany owns acceptance."""

    SPEC = PLAN_DIAGNOSTIC_SPEC
    DESCRIPTION = (
        "Read-only plan diagnosis through the external AgentRuntime; "
        "OmniCompany validates and publishes the proposed verdict."
    )
    FORMAT_IN = "doctor.plan_diagnosis.request"
    FORMAT_OUT = "doctor.plan_diagnosis.verdict"
    DIAGNOSIS_KIND = "plan"
    TARGET_FIELD = "target_plan_path"
    TARGET_KIND_FIELD = None
    FIXED_TARGET_KIND = "plan"
    REFERENCE_FIELD = "applicable_template_paths"
    EXTRA_FIELDS = ("check_modes",)
    CALLER = "doctor.plan-diagnostic"


__all__ = ["PLAN_DIAGNOSTIC_SPEC", "PlanDiagnosticAgent"]
