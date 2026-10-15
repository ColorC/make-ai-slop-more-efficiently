# [OMNI] origin=codex created_by=codex intent="consume canonical game models without device access"
"""Read-only query and summary surface for canonical Game Observatory models."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .game_modeling import (
    ExecutionAttemptLogV1,
    ExecutionBenchmarkV1,
    FactRecordV1,
    FactStatus,
    FailureRecoveryBoundaryV1,
    GameContentGraphV1,
    GameSystemDossierV1,
    GameSystemRegistryV1,
    MODEL_BY_SCHEMA_ID,
    NewSessionPreparationV1,
    NextStatePredictionV1,
    PlayStrategyRecordV1,
    PredictionAdjudicationV1,
    RecoveryRoutePreparationV1,
    StrategyTrialResultV1,
    SystemStateV1,
    SystemTransitionV1,
    validate_model_document,
)

FACT_STATUSES: tuple[FactStatus, ...] = (
    "verified",
    "inferred",
    "unknown",
    "contradicted",
)
ModelKind = Literal[
    "registry",
    "dossier",
    "content_graph",
    "strategy",
    "benchmark",
    "execution_attempt_log",
    "prediction",
    "adjudication",
    "preparation",
    "recovery_route",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GameModelQueryV1(_StrictModel):
    game_id: str | None = Field(default=None, min_length=1)
    system_id: str | None = Field(default=None, min_length=1)
    entity_id: str | None = Field(default=None, min_length=1)
    relation_type: str | None = Field(default=None, min_length=1)
    fact_status: FactStatus | None = None
    text: str | None = Field(default=None, min_length=1)


class ModelDocumentHitV1(_StrictModel):
    path: str = Field(min_length=1)
    kind: ModelKind
    schema_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    game_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)


class SkippedModelDocumentV1(_StrictModel):
    path: str = Field(min_length=1)
    schema_id: str | None = Field(default=None, min_length=1)
    reason: Literal["unsupported_schema"] = "unsupported_schema"


class FactQueryHitV1(_StrictModel):
    model_path: str = Field(min_length=1)
    model_kind: Literal["dossier", "content_graph"]
    scope_kind: str = Field(min_length=1)
    scope_id: str = Field(min_length=1)
    system_id: str | None = Field(default=None, min_length=1)
    entity_ids: list[str] = Field(default_factory=list)
    relation_type: str | None = Field(default=None, min_length=1)
    fact: FactRecordV1


class StateQueryHitV1(_StrictModel):
    model_path: str = Field(min_length=1)
    system_id: str = Field(min_length=1)
    state: SystemStateV1


class TransitionQueryHitV1(_StrictModel):
    model_path: str = Field(min_length=1)
    system_id: str = Field(min_length=1)
    transition: SystemTransitionV1


class FailureRecoveryBoundaryHitV1(_StrictModel):
    model_path: str = Field(min_length=1)
    system_id: str = Field(min_length=1)
    boundary: FailureRecoveryBoundaryV1


class UnknownQuestionHitV1(_StrictModel):
    model_path: str = Field(min_length=1)
    system_id: str = Field(min_length=1)
    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    priority: Literal["critical", "high", "medium", "low"]
    next_observation: str = Field(min_length=1)
    stop_reason: str | None = Field(default=None, min_length=1)
    related_fact_ids: list[str] = Field(default_factory=list)


class SystemQueryHitV1(_StrictModel):
    model_path: str = Field(min_length=1)
    system_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    classification: str = Field(min_length=1)
    modeling_status: str = Field(min_length=1)
    dossier_path: str | None = Field(default=None, min_length=1)
    unknown_question_ids: list[str] = Field(default_factory=list)


class EntityQueryHitV1(_StrictModel):
    model_path: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    label: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)


class RelationQueryHitV1(_StrictModel):
    model_path: str = Field(min_length=1)
    relation_id: str = Field(min_length=1)
    source_entity_id: str = Field(min_length=1)
    relation_type: str = Field(min_length=1)
    target_entity_id: str = Field(min_length=1)


class StrategyQueryHitV1(_StrictModel):
    model_path: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)
    decision_problem: str = Field(min_length=1)
    baseline_candidate_id: str = Field(min_length=1)
    selected_candidate_id: str | None = Field(default=None, min_length=1)
    conclusion: str = Field(min_length=1)
    candidate_ids: list[str] = Field(min_length=1)
    completed_trial_count: int = Field(ge=0)
    trial_results: list[StrategyTrialResultV1] = Field(default_factory=list)


class BenchmarkQueryHitV1(_StrictModel):
    model_path: str = Field(min_length=1)
    benchmark_id: str = Field(min_length=1)
    workload_kind: str = Field(min_length=1)
    target_effective_actions_per_minute: float | None = Field(default=None, gt=0)
    gate_status: str = Field(min_length=1)
    batch_count: int = Field(ge=0)
    passed_batch_count: int = Field(ge=0)


class GameModelQueryResultV1(_StrictModel):
    schema_id: Literal["game-observatory.game-model-query-result.v1"] = Field(
        default="game-observatory.game-model-query-result.v1", alias="schema"
    )
    model_root: str = Field(min_length=1)
    query: GameModelQueryV1
    documents: list[ModelDocumentHitV1] = Field(default_factory=list)
    skipped_documents: list[SkippedModelDocumentV1] = Field(default_factory=list)
    systems: list[SystemQueryHitV1] = Field(default_factory=list)
    entities: list[EntityQueryHitV1] = Field(default_factory=list)
    relations: list[RelationQueryHitV1] = Field(default_factory=list)
    strategies: list[StrategyQueryHitV1] = Field(default_factory=list)
    benchmarks: list[BenchmarkQueryHitV1] = Field(default_factory=list)
    execution_attempt_logs: list[ExecutionAttemptLogV1] = Field(default_factory=list)
    states: list[StateQueryHitV1] = Field(default_factory=list)
    transitions: list[TransitionQueryHitV1] = Field(default_factory=list)
    failure_recovery_boundaries: list[FailureRecoveryBoundaryHitV1] = Field(
        default_factory=list
    )
    facts_by_status: dict[FactStatus, list[FactQueryHitV1]]
    unknown_questions: list[UnknownQuestionHitV1] = Field(default_factory=list)
    predictions: list[NextStatePredictionV1] = Field(default_factory=list)
    adjudications: list[PredictionAdjudicationV1] = Field(default_factory=list)
    recovery_routes: list[RecoveryRoutePreparationV1] = Field(default_factory=list)


class GameModelSummaryV1(_StrictModel):
    schema_id: Literal["game-observatory.game-model-summary.v1"] = Field(
        default="game-observatory.game-model-summary.v1", alias="schema"
    )
    model_root: str = Field(min_length=1)
    game_ids: list[str] = Field(default_factory=list)
    environment_ids: list[str] = Field(default_factory=list)
    document_count: int = Field(ge=0)
    skipped_document_count: int = Field(ge=0)
    skipped_documents: list[SkippedModelDocumentV1] = Field(default_factory=list)
    document_counts_by_kind: dict[str, int]
    fact_counts_by_status: dict[FactStatus, int]
    system_count: int = Field(ge=0)
    entity_count: int = Field(ge=0)
    relation_count: int = Field(ge=0)
    unknown_question_count: int = Field(ge=0)
    strategy_conclusions: dict[str, str]
    benchmark_gates: dict[str, str]
    prediction_counts_by_disposition: dict[str, int]
    readiness: Literal[
        "baseline_only",
        "prepared_not_executed",
        "attempted_unsettled",
        "has_terminal_adjudication",
    ]
    caveats: list[str] = Field(default_factory=list)


def _model_identity(model: BaseModel) -> tuple[ModelKind, str, str, str, str]:
    schema_id = str(getattr(model, "schema_id"))
    game_id = str(getattr(model, "game_id"))
    environment_id = str(getattr(model, "environment_id"))
    if isinstance(model, GameSystemRegistryV1):
        return "registry", schema_id, f"registry.{game_id}", game_id, environment_id
    if isinstance(model, GameSystemDossierV1):
        return "dossier", schema_id, model.id, game_id, environment_id
    if isinstance(model, GameContentGraphV1):
        return "content_graph", schema_id, model.id, game_id, environment_id
    if isinstance(model, PlayStrategyRecordV1):
        return "strategy", schema_id, model.id, game_id, environment_id
    if isinstance(model, ExecutionBenchmarkV1):
        return "benchmark", schema_id, model.id, game_id, environment_id
    if isinstance(model, ExecutionAttemptLogV1):
        return "execution_attempt_log", schema_id, model.id, game_id, environment_id
    if isinstance(model, NextStatePredictionV1):
        return "prediction", schema_id, model.id, game_id, environment_id
    if isinstance(model, PredictionAdjudicationV1):
        return "adjudication", schema_id, model.id, game_id, environment_id
    if isinstance(model, NewSessionPreparationV1):
        return "preparation", schema_id, model.id, game_id, environment_id
    if isinstance(model, RecoveryRoutePreparationV1):
        return "recovery_route", schema_id, model.route_id, game_id, environment_id
    raise TypeError(f"unsupported model document: {type(model)!r}")


def _contains(text: str | None, *values: Any) -> bool:
    if text is None:
        return True
    needle = text.casefold()
    return any(needle in str(value).casefold() for value in values if value is not None)


def _dossier_fact_hits(path: str, dossier: GameSystemDossierV1) -> Iterable[FactQueryHitV1]:
    groups: list[tuple[str, str, Iterable[FactRecordV1]]] = [
        ("objective", dossier.id, dossier.objective),
        ("entry_and_unlock", dossier.id, dossier.entry_and_unlock),
    ]
    groups.extend(("state", item.id, item.facts) for item in dossier.states)
    groups.extend(("transition", item.id, item.facts) for item in dossier.transitions)
    groups.extend(("resource_flow", item.id, item.facts) for item in dossier.resource_flows)
    groups.extend(("system_relation", item.id, item.facts) for item in dossier.relations)
    groups.extend(
        ("failure_recovery_boundary", item.id, item.facts)
        for item in dossier.failure_recovery_boundaries
    )
    for scope_kind, scope_id, facts in groups:
        for fact in facts:
            yield FactQueryHitV1(
                model_path=path,
                model_kind="dossier",
                scope_kind=scope_kind,
                scope_id=scope_id,
                system_id=dossier.id,
                fact=fact,
            )


def _graph_fact_hits(path: str, graph: GameContentGraphV1) -> Iterable[FactQueryHitV1]:
    for entity in graph.entities:
        for fact in entity.facts:
            yield FactQueryHitV1(
                model_path=path,
                model_kind="content_graph",
                scope_kind="entity",
                scope_id=entity.id,
                entity_ids=[entity.id],
                fact=fact,
            )
    for relation in graph.relations:
        for fact in relation.facts:
            yield FactQueryHitV1(
                model_path=path,
                model_kind="content_graph",
                scope_kind="relation",
                scope_id=relation.id,
                entity_ids=[relation.source_entity_id, relation.target_entity_id],
                relation_type=relation.relation_type,
                fact=fact,
            )


class GameModelCatalog:
    """Validated, read-only view over one canonical game-model directory."""

    def __init__(self, model_root: str | Path):
        self.root = Path(model_root).resolve()
        if not self.root.is_dir():
            raise FileNotFoundError(f"game model root does not exist: {self.root}")
        self.documents: list[tuple[str, BaseModel]] = []
        self.skipped_documents: list[SkippedModelDocumentV1] = []
        for path in sorted(self.root.rglob("*.json")):
            relative = path.relative_to(self.root).as_posix()
            document = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                raise ValueError(f"game model document must contain one JSON object: {relative}")
            schema_id = document.get("schema")
            if not isinstance(schema_id, str) or schema_id not in MODEL_BY_SCHEMA_ID:
                self.skipped_documents.append(
                    SkippedModelDocumentV1(
                        path=relative,
                        schema_id=schema_id if isinstance(schema_id, str) else None,
                    )
                )
                continue
            self.documents.append((relative, validate_model_document(document)))

    def query(self, query: GameModelQueryV1 | None = None) -> GameModelQueryResultV1:
        selected = query or GameModelQueryV1()
        documents: list[ModelDocumentHitV1] = []
        systems: list[SystemQueryHitV1] = []
        entities: list[EntityQueryHitV1] = []
        relations: list[RelationQueryHitV1] = []
        strategies: list[StrategyQueryHitV1] = []
        benchmarks: list[BenchmarkQueryHitV1] = []
        execution_attempt_logs: list[ExecutionAttemptLogV1] = []
        states: list[StateQueryHitV1] = []
        transitions: list[TransitionQueryHitV1] = []
        failure_recovery_boundaries: list[FailureRecoveryBoundaryHitV1] = []
        facts: dict[FactStatus, list[FactQueryHitV1]] = {status: [] for status in FACT_STATUSES}
        unknown_questions: list[UnknownQuestionHitV1] = []
        predictions: list[NextStatePredictionV1] = []
        adjudications: list[PredictionAdjudicationV1] = []
        recovery_routes: list[RecoveryRoutePreparationV1] = []

        for path, model in self.documents:
            kind, schema_id, model_id, game_id, environment_id = _model_identity(model)
            if selected.game_id is not None and game_id != selected.game_id:
                continue
            documents.append(
                ModelDocumentHitV1(
                    path=path,
                    kind=kind,
                    schema_id=schema_id,
                    model_id=model_id,
                    game_id=game_id,
                    environment_id=environment_id,
                )
            )
            if isinstance(model, GameSystemRegistryV1):
                if (
                    selected.entity_id is not None
                    or selected.relation_type is not None
                    or selected.fact_status is not None
                ):
                    continue
                for item in model.systems:
                    if selected.system_id is not None and item.system_id != selected.system_id:
                        continue
                    if not _contains(
                        selected.text, item.system_id, item.title, item.modeling_status
                    ):
                        continue
                    systems.append(
                        SystemQueryHitV1(
                            model_path=path,
                            system_id=item.system_id,
                            title=item.title,
                            classification=item.classification,
                            modeling_status=item.modeling_status,
                            dossier_path=item.dossier_path,
                            unknown_question_ids=item.unknown_question_ids,
                        )
                    )
            elif isinstance(model, GameSystemDossierV1):
                if selected.entity_id is not None or selected.relation_type is not None:
                    continue
                if selected.system_id is not None and model.id != selected.system_id:
                    continue
                for item in model.states:
                    if _contains(
                        selected.text,
                        item.id,
                        item.label,
                        item.description,
                        *(fact.statement for fact in item.facts),
                    ):
                        states.append(
                            StateQueryHitV1(
                                model_path=path, system_id=model.id, state=item
                            )
                        )
                for item in model.transitions:
                    if _contains(
                        selected.text,
                        item.id,
                        item.from_state_id,
                        item.to_state_id,
                        item.trigger,
                        *item.preconditions,
                        *item.effects,
                        *(fact.statement for fact in item.facts),
                    ):
                        transitions.append(
                            TransitionQueryHitV1(
                                model_path=path, system_id=model.id, transition=item
                            )
                        )
                for item in model.failure_recovery_boundaries:
                    if _contains(
                        selected.text,
                        item.id,
                        item.kind,
                        item.condition,
                        item.response,
                        *(fact.statement for fact in item.facts),
                    ):
                        failure_recovery_boundaries.append(
                            FailureRecoveryBoundaryHitV1(
                                model_path=path, system_id=model.id, boundary=item
                            )
                        )
                matching_dossier_fact_ids: set[str] = set()
                for hit in _dossier_fact_hits(path, model):
                    if selected.fact_status is not None and hit.fact.status != selected.fact_status:
                        continue
                    if not _contains(
                        selected.text,
                        hit.scope_id,
                        hit.fact.id,
                        hit.fact.statement,
                        hit.fact.rationale,
                    ):
                        continue
                    facts[hit.fact.status].append(hit)
                    matching_dossier_fact_ids.add(hit.fact.id)
                for item in model.unknown_questions:
                    directly_matches = _contains(
                        selected.text,
                        item.id,
                        item.question,
                        item.next_observation,
                        item.stop_reason,
                        *item.related_fact_ids,
                    )
                    linked_fact_matches = bool(
                        matching_dossier_fact_ids.intersection(item.related_fact_ids)
                    )
                    if not directly_matches and not linked_fact_matches:
                        continue
                    unknown_questions.append(
                        UnknownQuestionHitV1(
                            model_path=path,
                            system_id=model.id,
                            id=item.id,
                            question=item.question,
                            priority=item.priority,
                            next_observation=item.next_observation,
                            stop_reason=item.stop_reason,
                            related_fact_ids=item.related_fact_ids,
                        )
                    )
            elif isinstance(model, GameContentGraphV1):
                if selected.system_id is not None:
                    continue
                for item in model.entities:
                    if selected.entity_id is not None and item.id != selected.entity_id:
                        continue
                    if not _contains(selected.text, item.id, item.kind, item.label, *item.aliases):
                        continue
                    entities.append(
                        EntityQueryHitV1(
                            model_path=path,
                            entity_id=item.id,
                            kind=item.kind,
                            label=item.label,
                            aliases=item.aliases,
                        )
                    )
                for item in model.relations:
                    if selected.entity_id is not None and selected.entity_id not in {
                        item.source_entity_id,
                        item.target_entity_id,
                    }:
                        continue
                    if selected.relation_type is not None and (
                        item.relation_type != selected.relation_type
                    ):
                        continue
                    if not _contains(
                        selected.text,
                        item.id,
                        item.source_entity_id,
                        item.relation_type,
                        item.target_entity_id,
                    ):
                        continue
                    relations.append(
                        RelationQueryHitV1(
                            model_path=path,
                            relation_id=item.id,
                            source_entity_id=item.source_entity_id,
                            relation_type=item.relation_type,
                            target_entity_id=item.target_entity_id,
                        )
                    )
                for hit in _graph_fact_hits(path, model):
                    if selected.entity_id is not None and selected.entity_id not in hit.entity_ids:
                        continue
                    if selected.relation_type is not None and (
                        hit.relation_type != selected.relation_type
                    ):
                        continue
                    if selected.fact_status is not None and hit.fact.status != selected.fact_status:
                        continue
                    if not _contains(
                        selected.text,
                        hit.scope_id,
                        hit.relation_type,
                        hit.fact.id,
                        hit.fact.statement,
                        hit.fact.rationale,
                    ):
                        continue
                    facts[hit.fact.status].append(hit)
            elif isinstance(model, PlayStrategyRecordV1):
                if any(
                    value is not None
                    for value in (
                        selected.system_id,
                        selected.entity_id,
                        selected.relation_type,
                        selected.fact_status,
                    )
                ):
                    continue
                if not _contains(
                    selected.text,
                    model.id,
                    model.decision_problem,
                    model.conclusion,
                    *(item.id for item in model.candidates),
                    *(item.id for item in model.trial_results),
                    *(item.candidate_id for item in model.trial_results),
                    *(item.status for item in model.trial_results),
                    *model.conclusion_notes,
                    *model.counterexamples,
                    *(value for item in model.trial_results for value in item.deviations),
                    *(value for item in model.trial_results for value in item.confounders),
                ):
                    continue
                strategies.append(
                    StrategyQueryHitV1(
                        model_path=path,
                        strategy_id=model.id,
                        decision_problem=model.decision_problem,
                        baseline_candidate_id=model.baseline_candidate_id,
                        selected_candidate_id=model.selected_candidate_id,
                        conclusion=model.conclusion,
                        candidate_ids=[item.id for item in model.candidates],
                        completed_trial_count=sum(
                            item.status == "completed" for item in model.trial_results
                        ),
                        trial_results=model.trial_results,
                    )
                )
            elif isinstance(model, ExecutionBenchmarkV1):
                if any(
                    value is not None
                    for value in (
                        selected.system_id,
                        selected.entity_id,
                        selected.relation_type,
                        selected.fact_status,
                    )
                ):
                    continue
                if not _contains(
                    selected.text,
                    model.id,
                    model.workload_kind,
                    model.gate_status,
                ):
                    continue
                benchmarks.append(
                    BenchmarkQueryHitV1(
                        model_path=path,
                        benchmark_id=model.id,
                        workload_kind=model.workload_kind,
                        target_effective_actions_per_minute=(
                            model.target_effective_actions_per_minute
                        ),
                        gate_status=model.gate_status,
                        batch_count=len(model.batches),
                        passed_batch_count=sum(
                            item.gate_status == "passed" for item in model.batches
                        ),
                    )
                )
            elif isinstance(model, ExecutionAttemptLogV1):
                if any(
                    value is not None
                    for value in (
                        selected.system_id,
                        selected.entity_id,
                        selected.relation_type,
                        selected.fact_status,
                    )
                ):
                    continue
                attempt_text = [
                    value
                    for attempt in model.attempts
                    for value in (
                        attempt.id,
                        attempt.planned_action_id,
                        attempt.action_kind,
                        attempt.outcome,
                        attempt.business_change,
                        attempt.observed_detail,
                        attempt.failure_reason,
                    )
                ]
                if _contains(
                    selected.text,
                    model.id,
                    model.task_id,
                    model.purpose,
                    model.benchmark_exclusion_reason,
                    *model.prediction_ids,
                    *model.conflicting_assessment_ids,
                    *model.notes,
                    *attempt_text,
                ):
                    execution_attempt_logs.append(model)
            elif isinstance(model, PredictionAdjudicationV1):
                if any(
                    value is not None
                    for value in (
                        selected.system_id,
                        selected.entity_id,
                        selected.relation_type,
                        selected.fact_status,
                    )
                ):
                    continue
                if _contains(
                    selected.text,
                    model.id,
                    model.prediction_id,
                    model.disposition,
                    model.explanation,
                ):
                    adjudications.append(model)
            elif isinstance(model, NextStatePredictionV1):
                if any(
                    value is not None
                    for value in (selected.entity_id, selected.relation_type, selected.fact_status)
                ):
                    continue
                if selected.system_id is not None and model.system_id != selected.system_id:
                    continue
                if _contains(
                    selected.text,
                    model.id,
                    model.system_id,
                    model.predicted_next_state,
                ):
                    predictions.append(model)
            elif isinstance(model, NewSessionPreparationV1):
                if any(
                    value is not None
                    for value in (selected.entity_id, selected.relation_type, selected.fact_status)
                ):
                    continue
                matching_predictions = [
                    item
                    for item in model.predictions
                    if (selected.system_id is None or item.system_id == selected.system_id)
                    and _contains(
                        selected.text,
                        item.id,
                        item.system_id,
                        item.predicted_next_state,
                    )
                ]
                predictions.extend(matching_predictions)
                matching_prediction_ids = {item.id for item in matching_predictions}
                adjudications.extend(
                    item
                    for item in model.adjudications
                    if item.prediction_id in matching_prediction_ids
                    and _contains(
                        selected.text,
                        item.id,
                        item.prediction_id,
                        item.disposition,
                        item.explanation,
                    )
                )
            elif isinstance(model, RecoveryRoutePreparationV1):
                if any(
                    value is not None
                    for value in (
                        selected.system_id,
                        selected.entity_id,
                        selected.relation_type,
                        selected.fact_status,
                    )
                ):
                    continue
                if _contains(
                    selected.text,
                    model.candidate_id,
                    model.route_id,
                    model.execution_status,
                    model.reference_artifact_id,
                    model.source_locator_id,
                    model.target_locator_id,
                    *model.activation_requirements,
                    *model.blocking_reasons,
                    *(item.id for item in model.locators),
                    *(item.target_name for item in model.locators),
                ):
                    recovery_routes.append(model)

        return GameModelQueryResultV1(
            model_root=str(self.root),
            query=selected,
            documents=documents,
            skipped_documents=self.skipped_documents,
            systems=systems,
            entities=entities,
            relations=relations,
            strategies=strategies,
            benchmarks=benchmarks,
            execution_attempt_logs=execution_attempt_logs,
            states=states,
            transitions=transitions,
            failure_recovery_boundaries=failure_recovery_boundaries,
            facts_by_status=facts,
            unknown_questions=unknown_questions,
            predictions=predictions,
            adjudications=adjudications,
            recovery_routes=recovery_routes,
        )

    def summary(self, game_id: str | None = None) -> GameModelSummaryV1:
        result = self.query(GameModelQueryV1(game_id=game_id))
        kinds: dict[str, int] = {}
        for item in result.documents:
            kinds[item.kind] = kinds.get(item.kind, 0) + 1
        dispositions: dict[str, int] = {
            "pending": 0,
            "confirmed": 0,
            "corrected": 0,
            "refuted": 0,
        }
        for item in result.adjudications:
            dispositions[item.disposition] += 1
        readiness: Literal[
            "baseline_only",
            "prepared_not_executed",
            "attempted_unsettled",
            "has_terminal_adjudication",
        ] = "baseline_only"
        if any(value for key, value in dispositions.items() if key != "pending"):
            readiness = "has_terminal_adjudication"
        elif result.execution_attempt_logs:
            readiness = "attempted_unsettled"
        elif result.predictions:
            readiness = "prepared_not_executed"
        strategy_conclusions = {item.strategy_id: item.conclusion for item in result.strategies}
        benchmark_gates = {item.benchmark_id: item.gate_status for item in result.benchmarks}
        caveats: list[str] = []
        if result.skipped_documents:
            caveats.append(
                "Unsupported auxiliary JSON documents were skipped and are listed for audit."
            )
        if any(value != "verified_better" for value in strategy_conclusions.values()):
            caveats.append("No strategy is currently proven better than its baseline.")
        if any(value != "passed" for value in benchmark_gates.values()):
            caveats.append("One or more execution benchmarks have not passed their gates.")
        terminal_count = sum(value for key, value in dispositions.items() if key != "pending")
        if dispositions["pending"] and terminal_count:
            caveats.append(
                "Some predictions have terminal adjudication, while others remain pending."
            )
        elif dispositions["pending"] and result.execution_attempt_logs:
            caveats.append(
                "Predictions remain pending after retained execution attempts without terminal adjudication."
            )
        elif dispositions["pending"]:
            caveats.append("Predictions are predeclared but still lack later Session evidence.")
        return GameModelSummaryV1(
            model_root=str(self.root),
            game_ids=sorted({item.game_id for item in result.documents}),
            environment_ids=sorted({item.environment_id for item in result.documents}),
            document_count=len(result.documents),
            skipped_document_count=len(result.skipped_documents),
            skipped_documents=result.skipped_documents,
            document_counts_by_kind=kinds,
            fact_counts_by_status={
                status: len(result.facts_by_status[status]) for status in FACT_STATUSES
            },
            system_count=len(result.systems),
            entity_count=len(result.entities),
            relation_count=len(result.relations),
            unknown_question_count=len(result.unknown_questions),
            strategy_conclusions=strategy_conclusions,
            benchmark_gates=benchmark_gates,
            prediction_counts_by_disposition=dispositions,
            readiness=readiness,
            caveats=caveats,
        )


def _print_json(value: BaseModel) -> None:
    print(
        json.dumps(
            value.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_root", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)
    summary_parser = subparsers.add_parser("summary")
    summary_parser.add_argument("--game-id")
    query_parser = subparsers.add_parser("query")
    query_parser.add_argument("--game-id")
    query_parser.add_argument("--system-id")
    query_parser.add_argument("--entity-id")
    query_parser.add_argument("--relation-type")
    query_parser.add_argument("--fact-status", choices=FACT_STATUSES)
    query_parser.add_argument("--text")
    args = parser.parse_args(argv)
    catalog = GameModelCatalog(args.model_root)
    if args.command == "summary":
        _print_json(catalog.summary(args.game_id))
        return 0
    _print_json(
        catalog.query(
            GameModelQueryV1(
                game_id=args.game_id,
                system_id=args.system_id,
                entity_id=args.entity_id,
                relation_type=args.relation_type,
                fact_status=args.fact_status,
                text=args.text,
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
