# [OMNI] origin=codex created_by=codex intent=read-only-learning-observatory-orchestration
"""Application service for discovery, collection, evaluation and monitoring."""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .collectors import collect_project_snapshot
from .config import load_config
from .discovery import discover_portfolio
from .evaluator import evaluate_snapshot
from .evaluation_profiles import (
    DIMENSION_IDS,
    EvaluationProfileError,
    load_project_profile,
    profile_summary,
)
from .store import ObservatoryStore


class LearningObservatoryService:
    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        store: ObservatoryStore | None = None,
    ) -> None:
        self.config = config or load_config()
        self.store = store or ObservatoryStore()

    def discover(self, **kwargs: Any) -> dict[str, Any]:
        return discover_portfolio(self.config, **kwargs)

    def collect(
        self,
        project: dict[str, Any],
        *,
        time_window_days: int | None = None,
        business_ids: list[str] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        run_config = self.config
        if time_window_days is not None:
            run_config = deepcopy(self.config)
            run_config["collection"]["recent_file_days"] = max(1, int(time_window_days))
        snapshot = collect_project_snapshot(project, run_config)
        if business_ids:
            snapshot = _scope_snapshot_to_businesses(snapshot, business_ids)
        return self.store.save_snapshot(snapshot)

    def evaluate(
        self,
        snapshot: dict[str, Any],
        *,
        deterministic_only: bool = False,
        force: bool = False,
        agent_runner: Any = None,
    ) -> tuple[dict[str, Any], bool]:
        project_id = str(snapshot["project"]["id"])
        previous = self.store.latest_assessment(project_id)
        if (
            previous
            and not force
            and previous.get("snapshot_id") == snapshot.get("snapshot_id")
            and previous.get("evaluator", {}).get("model") == self.config["evaluation"]["model"]
            and previous.get("evaluator", {}).get("prompt_version") == self.config["evaluation"]["prompt_version"]
            and previous.get("evaluator", {}).get("schema_version") == self.config["evaluation"]["schema_version"]
            and (
                deterministic_only
                or previous.get("evaluator", {}).get("status") == "ok"
            )
        ):
            return previous, False
        assessment = evaluate_snapshot(
            snapshot,
            self.config,
            previous=previous,
            deterministic_only=deterministic_only,
            agent_runner=agent_runner,
            snapshot_path=self.store.snapshot_path(
                project_id, str(snapshot["snapshot_id"])
            ),
            previous_path=(
                self.store.assessment_path(
                    project_id, str(previous["assessment_id"])
                )
                if previous
                else None
            ),
        )
        return self.store.save_assessment(assessment)

    def run(
        self,
        *,
        project_ids: list[str] | None = None,
        deterministic_only: bool = False,
        dry_run: bool = False,
        force_evaluate: bool = False,
        changed_since: str | None = None,
        time_window_days: int | None = None,
        business_ids: list[str] | None = None,
        agent_runner: Any = None,
    ) -> dict[str, Any]:
        started_at = datetime.now(timezone.utc).isoformat()
        discovery = self.discover()
        if business_ids and not project_ids:
            project_ids = sorted({business_id.split("/", 1)[0] for business_id in business_ids if "/" in business_id})
        selected = discovery["selected"]
        if project_ids:
            requested = set(project_ids)
            selected = [row for row in discovery["selected"] + discovery.get("candidates", []) if row["project_id"] in requested]
            unique: dict[str, dict[str, Any]] = {row["project_id"]: row for row in selected}
            selected = [unique[pid] for pid in project_ids if pid in unique]
            missing = [pid for pid in project_ids if pid not in unique]
        else:
            missing = []
        if changed_since:
            try:
                cutoff = datetime.fromisoformat(changed_since.replace("Z", "+00:00"))
                if cutoff.tzinfo is None:
                    cutoff = cutoff.replace(tzinfo=timezone.utc)
            except ValueError as exc:
                raise ValueError(f"invalid changed_since ISO timestamp: {changed_since}") from exc
            selected = [row for row in selected if _is_changed_since(row.get("last_active"), cutoff)]
        if dry_run:
            return {"dry_run": True, "discovery": discovery, "selected": selected, "missing_projects": missing}

        results = []
        errors = [{"project_id": pid, "stage": "discovery", "error": "project not found"} for pid in missing]
        for project in selected:
            project_id = project["project_id"]
            try:
                snapshot, snapshot_created = self.collect(
                    project, time_window_days=time_window_days, business_ids=business_ids,
                )
            except Exception as exc:  # noqa: BLE001 - one project must not abort the portfolio run.
                errors.append({"project_id": project_id, "stage": "collect", "error": str(exc)})
                continue
            try:
                assessment, assessment_created = self.evaluate(
                    snapshot,
                    deterministic_only=deterministic_only,
                    force=force_evaluate,
                    agent_runner=agent_runner,
                )
                results.append({
                    "project_id": project_id,
                    "snapshot_id": snapshot["snapshot_id"],
                    "snapshot_created": snapshot_created,
                    "assessment_id": assessment["assessment_id"],
                    "assessment_created": assessment_created,
                    "evaluation_state": assessment.get("evaluation_state"),
                    "evaluator_status": assessment.get("evaluator", {}).get("status"),
                    "usage": assessment.get("evaluator", {}).get("usage"),
                    "estimated_cost_usd": assessment.get("evaluator", {}).get("estimated_cost_usd"),
                    "attempts": assessment.get("evaluator", {}).get("attempts"),
                    "latency_ms": assessment.get("evaluator", {}).get("latency_ms"),
                })
            except Exception as exc:  # noqa: BLE001
                errors.append({"project_id": project_id, "stage": "evaluate", "error": str(exc)})
        finished_at = datetime.now(timezone.utc).isoformat()
        degraded = any(
            row.get("evaluator_status") not in ({"deterministic_only"} if deterministic_only else {"ok"})
            for row in results
        )
        run = {
            "schema_version": 1,
            "run_id": f"run-{uuid.uuid4().hex[:20]}",
            "started_at": started_at,
            "finished_at": finished_at,
            "model": self.config["evaluation"]["model"],
            "deterministic_only": deterministic_only,
            "changed_since": changed_since,
            "time_window_days": time_window_days,
            "business_ids": business_ids or [],
            "discovery_gate": discovery.get("gate"),
            "selected_project_ids": [row["project_id"] for row in selected],
            "results": results,
            "errors": errors,
            "status": "ok" if results and not errors and not degraded else "partial" if results else "failed",
        }
        stored, _ = self.store.save_run(run)
        return stored

    def status(self) -> dict[str, Any]:
        overview = self.store.overview()
        discovery = self.discover()
        overview["selection"] = {
            "observed_at": discovery.get("observed_at"),
            "gate": discovery.get("gate"),
            "projects": [
                {key: value for key, value in row.items() if key != "registry"}
                for row in discovery.get("selected") or []
            ],
        }
        overview["evaluation_profiles"] = {
            row["project_id"]: _safe_profile_summary(row["project_id"], self.config)
            for row in discovery.get("selected") or []
        }
        overview["config"] = {
            "provider": self.config["evaluation"]["provider"],
            "model": self.config["evaluation"]["model"],
            "source_categories": self.config["collection"]["source_categories"],
            "minimum_projects": self.config["portfolio"]["minimum_projects"],
            "maximum_projects": self.config["portfolio"]["maximum_projects"],
            "correctness_is_gate": True,
            "no_composite_score": True,
            "dimensions": list(
                (self.config.get("evaluation_profiles") or {}).get(
                    "dimensions", DIMENSION_IDS
                )
            ),
        }
        return overview

    def show(self, record_id: str, *, project_id: str | None = None) -> dict[str, Any]:
        if record_id.startswith("snap-"):
            return self.store.read_snapshot(record_id, project_id=project_id)
        if record_id.startswith("assessment-"):
            return self.store.read_assessment(record_id, project_id=project_id)
        if record_id.startswith("run-"):
            return self.store.read_run(record_id)
        raise ValueError(f"unknown observatory record id: {record_id}")

    def evaluation_profile(self, project_id: str) -> dict[str, Any] | None:
        """Return the project-specific evaluation contract, if connected."""

        return load_project_profile(project_id, self.config)


__all__ = ["LearningObservatoryService"]


def _safe_profile_summary(project_id: str, config: dict[str, Any]) -> dict[str, Any]:
    try:
        return profile_summary(load_project_profile(project_id, config))
    except EvaluationProfileError as exc:
        return {
            **profile_summary(None),
            "status": "invalid",
            "error": str(exc),
        }


def _is_changed_since(value: Any, cutoff: datetime) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed >= cutoff


def _scope_snapshot_to_businesses(snapshot: dict[str, Any], business_ids: list[str]) -> dict[str, Any]:
    scoped = deepcopy(snapshot)
    requested = set(business_ids)
    units = scoped.get("work_units") or {}
    units["businesses"] = [row for row in units.get("businesses") or [] if row.get("business_id") in requested]
    units["tasks"] = [row for row in units.get("tasks") or [] if row.get("business_id") in requested]
    units["requested_business_ids"] = sorted(requested)
    scoped["evaluation_scope"] = {"business_ids": sorted(requested)}
    return scoped
