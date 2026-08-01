"""Deterministically export the public AI-player contracts as JSON Schema."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic.json_schema import GenerateJsonSchema

from .account_metric_observation import (
    AccountMetricDefinitionV1,
    AccountMetricDeltaDerivationV1,
    AccountMetricObservationRequestV1,
    AccountMetricObservationV1,
    AuthoritativeMetricSnapshotPayloadV1,
    MetricOCRExtractionPayloadV1,
)
from .acceptance_executor import (
    AcceptanceEvidenceIndexV1,
    AcceptanceEvidenceReceiptV1,
    AcceptanceExecutionResultV1,
    AcceptanceRunRequestV1,
    AcceptanceTrustPolicyV1,
)
from .baseline import ExplorationBaselineFixtureV1, ExplorationBaselineResultV1
from .action_quality_producer import (
    ActionDecisionTelemetryV1,
    ActionQualityHistoryContextV1,
)
from .afk_human_truth_freeze import (
    AFKFrozenTruthCollectionV1,
    AFKFrozenTruthItemV1,
    AFKFrozenTruthManifestV1,
    AFKHumanTruthAttestationV1,
    AFKHumanTruthImportManifestV1,
    AFKHumanTruthReviewV1,
)
from .contracts import (
    AccountActionPolicyV1,
    ActionQualitySampleV1,
    EnvironmentPromotionV1,
    EnvironmentSelectionV1,
    EnvironmentScopeV1,
    EvidenceReferenceV1,
    FrontierTaskV1,
    GameplayCandidateV1,
    GuideKnowledgeV1,
    MemoryRecordV1,
    NavigationFrameV1,
    NavigationStackV1,
    PendingActionV1,
    PlayerIterationAssessmentV1,
    PlayerMetricDeltaV1,
    PlayerSoftSignalReviewAttestationV1,
    PlayerSoftSignalReviewRequestV1,
    PlayerSoftSignalReviewV1,
    PlayerSoftSignalV1,
    NormalizedSurfaceRectV1,
    SemanticStateV1,
    SemanticSurfaceAnchorV1,
    SemanticSurfaceProfileV1,
    SessionCapsuleV1,
    SkillApplicabilityScopeV1,
    SkillLocatorV1,
    SkillRunAttestationV1,
    SkillRunV1,
    SkillStepV1,
    SkillValidationV1,
    SkillVersionV1,
    SpeechEventV1,
    SpeechIntentV1,
    StateAssignmentV1,
    StateMatchV1,
    StateObservationFeaturesV1,
    StateObservationV1,
    StateRecognitionDecisionV1,
    TransitionEdgeV1,
)
from .crystallizer import SkillCrystallizationRequestV1
from .external_agent_continuity import (
    AFKJExternalAgentContinuityManifestV1,
    ExternalAgentContinuousSessionV1,
    PlayerFacilityContractV1,
)
from .external_agent_benchmark import (
    AFKJB0BenchmarkResultV1,
    AFKJB1BenchmarkResultV1,
    AFKJB3BenchmarkResultV1,
)
from .external_agent_runtime import (
    ExternalAgentInvocationIntentV1,
    ExternalAgentInvocationV1,
)
from .guide_refresh import (
    GuideRefreshReceiptV1,
    GuideRefreshRequestV1,
    GuideRefreshWorkItemV1,
    GuideResearchResultBundleV1,
)
from .route_replay import RouteReplayAssessmentV1
from .planner_measurement import PlannerMeasurementReceiptV1
from .remediation import (
    IterationRemediationGateV1,
    Tier1RemediationAttestationV1,
    Tier1RemediationCaseMetricsV1,
    Tier1RemediationRegressionCaseV1,
    Tier1RemediationRegressionFixtureV1,
    Tier1RemediationRegressionObservationV1,
    Tier1RemediationRegressionResultV1,
    Tier1RemediationVerificationV1,
)
from .route_replay_suite import RouteReplaySuiteAssessmentV1, RouteReplaySuiteInputV1
from .sanguo_daily_continuity import (
    DAILY_DUTIES,
    SanguoDailyContinuityAssessmentV1,
    SanguoDailyContinuityDayV1,
    SanguoDailyContinuityEventV1,
    SanguoDailyContinuityScheduleV1,
)
from .state_graph import StateRouteV1
from .surface_anchor_action import SurfaceAnchorActionPlanV1
from .state_adjudication import (
    StateAdjudicationAttestationV1,
    StateAdjudicationResultV1,
    StateAdjudicationSeedV1,
    StateReviewPacketV1,
)


SchemaModel = type[BaseModel]
SchemaExport = tuple[str, SchemaModel]

JSON_SCHEMA_DIALECT = GenerateJsonSchema.schema_dialect
DEFAULT_SCHEMA_DIR = Path(__file__).with_name("schemas")

# The filenames form a public, versioned artifact contract. Keep this registry explicit so
# model renames or newly exported classes cannot silently rename committed schema files.
CONTRACT_SCHEMA_EXPORTS: tuple[SchemaExport, ...] = (
    ("account-action-policy.v1.schema.json", AccountActionPolicyV1),
    ("action-quality-sample.v1.schema.json", ActionQualitySampleV1),
    ("environment-promotion.v1.schema.json", EnvironmentPromotionV1),
    ("environment-scope.v1.schema.json", EnvironmentScopeV1),
    ("environment-selection.v1.schema.json", EnvironmentSelectionV1),
    ("evidence-reference.v1.schema.json", EvidenceReferenceV1),
    ("frontier-task.v1.schema.json", FrontierTaskV1),
    ("gameplay-candidate.v1.schema.json", GameplayCandidateV1),
    ("guide-knowledge.v1.schema.json", GuideKnowledgeV1),
    ("memory-record.v1.schema.json", MemoryRecordV1),
    ("navigation-frame.v1.schema.json", NavigationFrameV1),
    ("navigation-stack.v1.schema.json", NavigationStackV1),
    ("pending-action.v1.schema.json", PendingActionV1),
    ("player-iteration-assessment.v1.schema.json", PlayerIterationAssessmentV1),
    ("player-metric-delta.v1.schema.json", PlayerMetricDeltaV1),
    (
        "player-soft-signal-review-attestation.v1.schema.json",
        PlayerSoftSignalReviewAttestationV1,
    ),
    ("player-soft-signal-review-request.v1.schema.json", PlayerSoftSignalReviewRequestV1),
    ("player-soft-signal-review.v1.schema.json", PlayerSoftSignalReviewV1),
    ("player-soft-signal.v1.schema.json", PlayerSoftSignalV1),
    ("normalized-surface-rect.v1.schema.json", NormalizedSurfaceRectV1),
    ("semantic-state.v1.schema.json", SemanticStateV1),
    ("semantic-surface-anchor.v1.schema.json", SemanticSurfaceAnchorV1),
    ("semantic-surface-profile.v1.schema.json", SemanticSurfaceProfileV1),
    ("session-capsule.v1.schema.json", SessionCapsuleV1),
    ("skill-applicability-scope.v1.schema.json", SkillApplicabilityScopeV1),
    ("skill-locator.v1.schema.json", SkillLocatorV1),
    ("skill-run-attestation.v1.schema.json", SkillRunAttestationV1),
    ("skill-run.v1.schema.json", SkillRunV1),
    ("skill-step.v1.schema.json", SkillStepV1),
    ("skill-validation.v1.schema.json", SkillValidationV1),
    ("skill-version.v1.schema.json", SkillVersionV1),
    ("speech-event.v1.schema.json", SpeechEventV1),
    ("speech-intent.v1.schema.json", SpeechIntentV1),
    ("state-assignment.v1.schema.json", StateAssignmentV1),
    ("state-match.v1.schema.json", StateMatchV1),
    ("state-observation-features.v1.schema.json", StateObservationFeaturesV1),
    ("state-observation.v1.schema.json", StateObservationV1),
    ("state-recognition-decision.v1.schema.json", StateRecognitionDecisionV1),
    ("transition-edge.v1.schema.json", TransitionEdgeV1),
)

SCHEMA_EXPORTS: tuple[SchemaExport, ...] = tuple(sorted((
    *CONTRACT_SCHEMA_EXPORTS,
    ("surface-anchor-action-plan.v1.schema.json", SurfaceAnchorActionPlanV1),
    (
        "acceptance-evidence-index.v1.schema.json",
        AcceptanceEvidenceIndexV1,
    ),
    (
        "acceptance-evidence-receipt.v1.schema.json",
        AcceptanceEvidenceReceiptV1,
    ),
    ("acceptance-results.v1.schema.json", AcceptanceExecutionResultV1),
    ("acceptance-run-request.v1.schema.json", AcceptanceRunRequestV1),
    ("acceptance-trust-policy.v1.schema.json", AcceptanceTrustPolicyV1),
    ("guide-refresh-receipt.v1.schema.json", GuideRefreshReceiptV1),
    ("guide-refresh-request.v1.schema.json", GuideRefreshRequestV1),
    ("guide-refresh-work-item.v1.schema.json", GuideRefreshWorkItemV1),
    ("guide-research-result.v1.schema.json", GuideResearchResultBundleV1),
    (
        "skill-crystallization-request.v1.schema.json",
        SkillCrystallizationRequestV1,
    ),
    (
        "state-adjudication-attestation.v1.schema.json",
        StateAdjudicationAttestationV1,
    ),
    ("state-adjudication-result.v1.schema.json", StateAdjudicationResultV1),
    ("state-adjudication-seed.v1.schema.json", StateAdjudicationSeedV1),
    ("state-review-packet.v1.schema.json", StateReviewPacketV1),
    ("account-metric-definition.v1.schema.json", AccountMetricDefinitionV1),
    (
        "account-metric-delta-derivation.v1.schema.json",
        AccountMetricDeltaDerivationV1,
    ),
    (
        "account-metric-observation-request.v1.schema.json",
        AccountMetricObservationRequestV1,
    ),
    ("account-metric-observation.v1.schema.json", AccountMetricObservationV1),
    ("action-decision-telemetry.v1.schema.json", ActionDecisionTelemetryV1),
    ("action-quality-history-context.v1.schema.json", ActionQualityHistoryContextV1),
    (
        "afkj-external-agent-b0-result.v1.schema.json",
        AFKJB0BenchmarkResultV1,
    ),
    (
        "afkj-external-agent-b1-result.v2.schema.json",
        AFKJB1BenchmarkResultV1,
    ),
    (
        "afkj-external-agent-b3-result.v1.schema.json",
        AFKJB3BenchmarkResultV1,
    ),
    (
        "afkj-external-agent-continuity-manifest.v1.schema.json",
        AFKJExternalAgentContinuityManifestV1,
    ),
    ("afk-frozen-truth-collection.v1.schema.json", AFKFrozenTruthCollectionV1),
    ("afk-frozen-truth-item.v1.schema.json", AFKFrozenTruthItemV1),
    ("afk-frozen-truth-manifest.v1.schema.json", AFKFrozenTruthManifestV1),
    ("afk-human-truth-attestation.v1.schema.json", AFKHumanTruthAttestationV1),
    ("afk-human-truth-import.v1.schema.json", AFKHumanTruthImportManifestV1),
    ("afk-human-truth-review.v1.schema.json", AFKHumanTruthReviewV1),
    (
        "authoritative-metric-snapshot.v1.schema.json",
        AuthoritativeMetricSnapshotPayloadV1,
    ),
    ("exploration-baseline-fixture.v1.schema.json", ExplorationBaselineFixtureV1),
    ("exploration-baseline-result.v1.schema.json", ExplorationBaselineResultV1),
    (
        "external-agent-continuous-session.v1.schema.json",
        ExternalAgentContinuousSessionV1,
    ),
    ("external-agent-invocation.v1.schema.json", ExternalAgentInvocationV1),
    (
        "external-agent-invocation-intent.v1.schema.json",
        ExternalAgentInvocationIntentV1,
    ),
    ("metric-ocr-extraction.v1.schema.json", MetricOCRExtractionPayloadV1),
    ("planner-measurement-receipt.v1.schema.json", PlannerMeasurementReceiptV1),
    ("player-facility-contract.v1.schema.json", PlayerFacilityContractV1),
    ("iteration-remediation-gate.v1.schema.json", IterationRemediationGateV1),
    ("tier1-remediation-attestation.v1.schema.json", Tier1RemediationAttestationV1),
    ("tier1-remediation-case-metrics.v1.schema.json", Tier1RemediationCaseMetricsV1),
    ("tier1-remediation-regression-case.v1.schema.json", Tier1RemediationRegressionCaseV1),
    (
        "tier1-remediation-regression-fixture.v1.schema.json",
        Tier1RemediationRegressionFixtureV1,
    ),
    (
        "tier1-remediation-regression-observation.v1.schema.json",
        Tier1RemediationRegressionObservationV1,
    ),
    (
        "tier1-remediation-regression-result.v1.schema.json",
        Tier1RemediationRegressionResultV1,
    ),
    ("tier1-remediation-verification.v1.schema.json", Tier1RemediationVerificationV1),
    ("route-replay-assessment.v1.schema.json", RouteReplayAssessmentV1),
    ("route-replay-suite-assessment.v1.schema.json", RouteReplaySuiteAssessmentV1),
    ("route-replay-suite-input.v1.schema.json", RouteReplaySuiteInputV1),
    (
        "sanguo-daily-continuity-assessment.v1.schema.json",
        SanguoDailyContinuityAssessmentV1,
    ),
    ("sanguo-daily-continuity-day.v1.schema.json", SanguoDailyContinuityDayV1),
    ("sanguo-daily-continuity-event.v1.schema.json", SanguoDailyContinuityEventV1),
    (
        "sanguo-daily-continuity-schedule.v1.schema.json",
        SanguoDailyContinuityScheduleV1,
    ),
    ("state-route.v1.schema.json", StateRouteV1),
), key=lambda item: item[0]))

SERIALIZATION_SCHEMA_MODELS = frozenset(
    {RouteReplayAssessmentV1, RouteReplaySuiteAssessmentV1, StateRouteV1}
)


def _daily_day_semantics(schema: dict[str, Any]) -> None:
    duty_ref = "#/$defs/SanguoDailyDutyCompletionV1"
    schema["properties"]["duties"].update(
        {
            "prefixItems": [
                {
                    "allOf": [
                        {"$ref": duty_ref},
                        {
                            "properties": {"duty": {"const": duty}},
                            "required": ["duty"],
                        },
                    ]
                }
                for duty in DAILY_DUTIES
            ],
            "items": False,
        }
    )
    duty_definition = schema["$defs"]["SanguoDailyDutyCompletionV1"]
    duty_definition["allOf"] = [
        {
            "if": {
                "properties": {
                    "duty": {
                        "enum": [
                            "current_goal_update",
                            "reachable_business_progress",
                            "next_day_task_generation",
                        ]
                    }
                },
                "required": ["duty"],
            },
            "then": {
                "properties": {"task_snapshots": {"minItems": 1}},
                "required": ["task_snapshots"],
            },
        },
        {
            "if": {
                "properties": {"duty": {"const": "guide_freshness_check"}},
                "required": ["duty"],
            },
            "then": {
                "properties": {
                    "guide_refs": {"minItems": 1},
                    "guide_freshness": {"$ref": "#/$defs/DailyGuideFreshnessV1"},
                },
                "required": ["guide_refs", "guide_freshness"],
            },
        },
        {
            "if": {
                "properties": {"duty": {"const": "end_of_day_memory_consolidation"}},
                "required": ["duty"],
            },
            "then": {
                "properties": {"memory_record_ids": {"minItems": 1}},
                "required": ["memory_record_ids"],
            },
        },
    ]
    schema["allOf"] = [
        {
            "if": {
                "properties": {"state": {"const": "sealed"}},
                "required": ["state"],
            },
            "then": {
                "properties": {
                    "duties": {"minItems": len(DAILY_DUTIES)},
                    "sealed_at": {"type": "string", "minLength": 1},
                },
                "required": ["sealed_at"],
            },
            "else": {"properties": {"sealed_at": {"type": "null"}}},
        }
    ]


def _daily_event_semantics(schema: dict[str, Any]) -> None:
    operation_event_pairs = (
        ("record_duty", "duty_recorded"),
        ("interrupt", "interrupted"),
        ("resume", "resumed"),
        ("seal", "sealed"),
    )
    schema["allOf"] = [
        {
            "oneOf": [
                {
                    "properties": {
                        "operation": {"const": operation},
                        "event_type": {"const": event_type},
                    },
                    "required": ["operation", "event_type"],
                }
                for operation, event_type in operation_event_pairs
            ]
        },
        {
            "if": {
                "properties": {"operation": {"const": "seal"}},
                "required": ["operation"],
            },
            "then": {"properties": {"evidence_refs": {"maxItems": 0}}},
            "else": {"properties": {"evidence_refs": {"minItems": 1}}},
        },
    ]


def _daily_assessment_semantics(schema: dict[str, Any]) -> None:
    schema["allOf"] = [
        {
            "if": {
                "properties": {"continuity_component_passed": {"const": True}},
                "required": ["continuity_component_passed"],
            },
            "then": {
                "properties": {
                    "recorded_natural_days": {"const": 7},
                    "sealed_natural_days": {"const": 7},
                    "reasons": {"maxItems": 0},
                }
            },
            "else": {"properties": {"reasons": {"minItems": 1}}},
        }
    ]


def _add_semantic_constraints(model: SchemaModel, schema: dict[str, Any]) -> None:
    if model is SanguoDailyContinuityDayV1:
        _daily_day_semantics(schema)
    elif model is SanguoDailyContinuityEventV1:
        _daily_event_semantics(schema)
    elif model is SanguoDailyContinuityAssessmentV1:
        _daily_assessment_semantics(schema)


def build_schema(model: SchemaModel) -> dict[str, Any]:
    """Return a validation schema, including cross-field public invariants."""

    mode = "serialization" if model in SERIALIZATION_SCHEMA_MODELS else "validation"
    schema = model.model_json_schema(by_alias=True, mode=mode)
    _add_semantic_constraints(model, schema)
    return {"$schema": JSON_SCHEMA_DIALECT, **schema}


def render_schema(model: SchemaModel) -> bytes:
    """Serialize one schema with stable ordering, formatting, and UTF-8 encoding."""

    document = json.dumps(
        build_schema(model),
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    )
    return f"{document}\n".encode("utf-8")


def export_schemas(output_dir: str | Path = DEFAULT_SCHEMA_DIR) -> tuple[Path, ...]:
    """Write all public contract schemas and return their stable output paths."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for filename, model in SCHEMA_EXPORTS:
        output_path = destination / filename
        output_path.write_bytes(render_schema(model))
        written.append(output_path)
    return tuple(written)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_SCHEMA_DIR,
        help="schema destination (defaults to the committed ai_player/schemas directory)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    for path in export_schemas(args.output_dir):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
