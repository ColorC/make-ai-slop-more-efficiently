"""Read-only maintenance triggers for evidence-bound game models.

The trigger classifies an incoming canonical change and validates the affected
model surface.  It schedules follow-up work as data; it does not mutate models,
touch a device, or create a second evidence/benchmark authority.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .game_model_query import GameModelCatalog, GameModelQueryV1


MaintenanceEventKind = Literal[
    "evidence_appended",
    "model_changed",
    "strategy_trial_appended",
    "benchmark_receipt_appended",
    "build_changed",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GameModelMaintenanceEventV1(_StrictModel):
    event_id: str = Field(min_length=1)
    kind: MaintenanceEventKind
    game_id: str = Field(min_length=1)
    source_path: str | None = Field(default=None, min_length=1)
    previous_build_scope_id: str | None = Field(default=None, min_length=1)
    current_build_scope_id: str | None = Field(default=None, min_length=1)


class GameModelMaintenanceResultV1(_StrictModel):
    schema_id: Literal["game-observatory.game-model-maintenance-result.v1"] = Field(
        default="game-observatory.game-model-maintenance-result.v1",
        alias="schema",
    )
    event_id: str = Field(min_length=1)
    game_id: str = Field(min_length=1)
    status: Literal["PASS", "FAIL"]
    triggered_checks: list[str] = Field(min_length=1)
    canonical_document_count: int = Field(ge=0)
    strategy_count: int = Field(ge=0)
    benchmark_count: int = Field(ge=0)
    follow_up_actions: list[str] = Field(min_length=1)
    issues: list[str] = Field(default_factory=list)


_CHECKS_BY_KIND: dict[MaintenanceEventKind, tuple[str, ...]] = {
    "evidence_appended": (
        "model_revalidate",
        "fact_status_reconcile",
        "content_projection_refresh",
    ),
    "model_changed": (
        "model_revalidate",
        "content_projection_refresh",
        "unknown_frontier_reconcile",
    ),
    "strategy_trial_appended": (
        "model_revalidate",
        "strategy_reevaluate",
        "strategy_counterexample_retention",
    ),
    "benchmark_receipt_appended": (
        "model_revalidate",
        "benchmark_regression",
        "safety_receipt_regression",
    ),
    "build_changed": (
        "model_revalidate",
        "applicability_revalidation",
        "locator_route_invalidation_review",
    ),
}


def evaluate_maintenance_event(
    event: GameModelMaintenanceEventV1,
    *,
    model_root: Path,
    workspace_root: Path | None = None,
) -> GameModelMaintenanceResultV1:
    """Validate the affected catalog and return explicit follow-up work."""

    issues: list[str] = []
    checks = list(_CHECKS_BY_KIND[event.kind])
    source = Path(event.source_path) if event.source_path else None
    if source is not None:
        if not source.is_absolute() and workspace_root is not None:
            source = workspace_root / source
        if not source.is_file():
            issues.append(f"maintenance source path is missing: {event.source_path}")
    if event.kind == "build_changed":
        if not event.previous_build_scope_id or not event.current_build_scope_id:
            issues.append("build_changed requires previous and current build scope ids")
        elif event.previous_build_scope_id == event.current_build_scope_id:
            issues.append("build_changed requires distinct build scope ids")

    try:
        catalog = GameModelCatalog(model_root)
        result = catalog.query(GameModelQueryV1(game_id=event.game_id))
    except Exception as exc:  # noqa: BLE001 - the fail-closed result retains the exact issue
        issues.append(f"model catalog validation failed: {exc}")
        document_count = strategy_count = benchmark_count = 0
    else:
        document_count = len(result.documents)
        strategy_count = len(result.strategies)
        benchmark_count = len(result.benchmarks)
        if document_count == 0:
            issues.append(f"no canonical documents found for game_id={event.game_id}")
        if event.kind == "strategy_trial_appended" and strategy_count == 0:
            issues.append("strategy maintenance event has no strategy record")
        if event.kind == "benchmark_receipt_appended" and benchmark_count == 0:
            issues.append("benchmark maintenance event has no benchmark record")

    follow_up = [
        "persist this result beside the scheduler/event receipt",
        "open a canonical task only for checks whose inputs changed",
    ]
    if event.kind == "build_changed":
        follow_up.append("keep affected locators and routes fail-closed until revalidated")
    elif event.kind == "strategy_trial_appended":
        follow_up.append("recompute the strategy conclusion without deleting failed trials")
    elif event.kind == "benchmark_receipt_appended":
        follow_up.append("recompute consecutive gates from the complete occurrence order")
    else:
        follow_up.append("refresh model projections without promoting inferred or unknown facts")
    return GameModelMaintenanceResultV1(
        event_id=event.event_id,
        game_id=event.game_id,
        status="FAIL" if issues else "PASS",
        triggered_checks=checks,
        canonical_document_count=document_count,
        strategy_count=strategy_count,
        benchmark_count=benchmark_count,
        follow_up_actions=follow_up,
        issues=issues,
    )


__all__ = [
    "GameModelMaintenanceEventV1",
    "GameModelMaintenanceResultV1",
    "evaluate_maintenance_event",
]
