# [OMNI] origin=codex created_by=codex intent=versioned-observatory-evidence-store
"""Immutable evidence/assessment store with a rebuildable latest index."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.core.config import resolve_service_data_dir


class ObservatoryStore:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root).resolve() if root else resolve_service_data_dir("ai_learning_observatory")

    def save_snapshot(self, snapshot: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        project_id = str(snapshot["project"]["id"])
        stable = deepcopy(snapshot)
        stable.pop("collected_at", None)
        snapshot_id = f"snap-{_safe(project_id)}-{_digest(stable)[:16]}"
        record = {**snapshot, "snapshot_id": snapshot_id}
        path = self.root / "snapshots" / _safe(project_id) / f"{snapshot_id}.json"
        created = self._write_once(path, record)
        self._update_index(project_id, snapshot=record)
        return self.read_snapshot(snapshot_id, project_id=project_id), created

    def save_assessment(self, assessment: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        project_id = str(assessment["project_id"])
        stable = deepcopy(assessment)
        stable.pop("evaluated_at", None)
        stable.pop("assessment_id", None)
        stable.pop("supersedes", None)
        assessment_id = f"assessment-{_safe(project_id)}-{_digest(stable)[:16]}"
        previous = (self._read_index().get("projects") or {}).get(project_id, {}).get("latest_assessment_id")
        record = {**assessment, "assessment_id": assessment_id, "supersedes": previous if previous != assessment_id else None}
        path = self.root / "assessments" / _safe(project_id) / f"{assessment_id}.json"
        created = self._write_once(path, record)
        self._update_index(project_id, assessment=record)
        return self.read_assessment(assessment_id, project_id=project_id), created

    def save_run(self, run: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        stable = deepcopy(run)
        stable.pop("started_at", None)
        stable.pop("finished_at", None)
        run_id = str(run.get("run_id") or f"run-{_digest(stable)[:20]}")
        record = {**run, "run_id": run_id}
        created = self._write_once(self.root / "runs" / f"{run_id}.json", record)
        index = self._read_index()
        index["latest_run_id"] = run_id
        index["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._atomic_json(self.root / "index.json", index)
        return self.read_run(run_id), created

    def read_snapshot(self, snapshot_id: str, *, project_id: str | None = None) -> dict[str, Any]:
        return self._read_record("snapshots", snapshot_id, project_id)

    def read_assessment(self, assessment_id: str, *, project_id: str | None = None) -> dict[str, Any]:
        return self._read_record("assessments", assessment_id, project_id)

    def read_run(self, run_id: str) -> dict[str, Any]:
        return self._read_json(self.root / "runs" / f"{run_id}.json")

    def snapshot_path(self, project_id: str, snapshot_id: str) -> Path:
        """Return the immutable snapshot path used as an Agent authority entry."""

        return self.root / "snapshots" / _safe(project_id) / f"{snapshot_id}.json"

    def assessment_path(self, project_id: str, assessment_id: str) -> Path:
        """Return the immutable assessment path used as an Agent authority entry."""

        return self.root / "assessments" / _safe(project_id) / f"{assessment_id}.json"

    def latest_snapshot(self, project_id: str) -> dict[str, Any] | None:
        entry = (self._read_index().get("projects") or {}).get(project_id) or {}
        record_id = entry.get("latest_snapshot_id")
        return self.read_snapshot(record_id, project_id=project_id) if record_id else None

    def latest_assessment(self, project_id: str) -> dict[str, Any] | None:
        entry = (self._read_index().get("projects") or {}).get(project_id) or {}
        record_id = entry.get("latest_assessment_id")
        return self.read_assessment(record_id, project_id=project_id) if record_id else None

    def remove_current_project(self, project_id: str) -> bool:
        """Remove a retired/misattributed project from the rebuildable current view.

        Immutable snapshots, assessments, and run records stay on disk as audit
        evidence.  This only prevents an invalid historical identity from
        continuing to appear as a currently monitored Project.
        """
        index = self._read_index()
        projects = index.setdefault("projects", {})
        if project_id not in projects:
            return False
        projects.pop(project_id)
        index["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._atomic_json(self.root / "index.json", index)
        return True

    def overview(self) -> dict[str, Any]:
        index = self._read_index()
        projects = []
        for project_id, entry in sorted((index.get("projects") or {}).items()):
            snapshot = self.latest_snapshot(project_id)
            assessment = self.latest_assessment(project_id)
            projects.append({
                "project_id": project_id,
                "snapshot_id": entry.get("latest_snapshot_id"),
                "assessment_id": entry.get("latest_assessment_id"),
                "collected_at": snapshot.get("collected_at") if snapshot else None,
                "coverage": snapshot.get("coverage") if snapshot else None,
                "assessment": _assessment_summary(assessment),
            })
        return {
            "schema_version": 1,
            "data_root": str(self.root),
            "updated_at": index.get("updated_at"),
            "latest_run_id": index.get("latest_run_id"),
            "projects": projects,
        }

    def _read_record(self, bucket: str, record_id: str, project_id: str | None) -> dict[str, Any]:
        if not record_id:
            raise FileNotFoundError("record id is empty")
        if project_id:
            return self._read_json(self.root / bucket / _safe(project_id) / f"{record_id}.json")
        matches = list((self.root / bucket).glob(f"*/{record_id}.json")) if (self.root / bucket).exists() else []
        if len(matches) != 1:
            raise FileNotFoundError(record_id)
        return self._read_json(matches[0])

    def _update_index(
        self,
        project_id: str,
        *,
        snapshot: dict[str, Any] | None = None,
        assessment: dict[str, Any] | None = None,
    ) -> None:
        index = self._read_index()
        entry = index.setdefault("projects", {}).setdefault(project_id, {})
        if snapshot:
            entry["latest_snapshot_id"] = snapshot["snapshot_id"]
            entry["collected_at"] = snapshot.get("collected_at")
        if assessment:
            entry["latest_assessment_id"] = assessment["assessment_id"]
            entry["evaluated_at"] = assessment.get("evaluated_at")
        index["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._atomic_json(self.root / "index.json", index)

    def _read_index(self) -> dict[str, Any]:
        path = self.root / "index.json"
        if not path.exists():
            return {"schema_version": 1, "projects": {}}
        try:
            data = self._read_json(path)
        except (OSError, json.JSONDecodeError):
            return {"schema_version": 1, "projects": {}}
        data.setdefault("projects", {})
        return data

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_once(self, path: Path, value: dict[str, Any]) -> bool:
        if path.exists():
            return False
        self._atomic_json(path, value)
        return True

    @staticmethod
    def _atomic_json(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(encoded)
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


def _assessment_summary(assessment: dict[str, Any] | None) -> dict[str, Any] | None:
    if not assessment:
        return None
    semantic = assessment.get("semantic") or {}
    correctness = semantic.get("correctness_gate") or {}
    comparison = semantic.get("comparison") or {}
    dimensions = {
        str(row.get("dimension")): row.get("status")
        for row in semantic.get("dimensions") or []
        if isinstance(row, dict) and row.get("dimension")
    }
    # Keep legacy fields readable for immutable v1-v3 assessments while new
    # clients use correctness/comparison/five-dimensional fields.
    absolute = semantic.get("absolute_assessment") or {}
    trajectory = semantic.get("trajectory_assessment") or {}
    return {
        "evaluation_state": semantic.get("evaluation_state") or assessment.get("evaluation_state"),
        "correctness_status": correctness.get("status") or "unknown",
        "comparison_status": comparison.get("status") or assessment.get("deterministic", {}).get("comparison_status"),
        "dimension_statuses": dimensions,
        "absolute_status": absolute.get("status") or "unknown",
        "trajectory_status": trajectory.get("status") or assessment.get("deterministic", {}).get("comparison_status"),
        "summary": semantic.get("summary") or assessment.get("deterministic", {}).get("summary"),
        "evaluator": assessment.get("evaluator"),
        "evaluated_at": assessment.get("evaluated_at"),
    }


def _safe(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "-" for char in value).strip("-") or "unknown"


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


__all__ = ["ObservatoryStore"]
