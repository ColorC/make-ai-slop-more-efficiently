# [OMNI] origin=claude-code created_by=omni-recover intent=apply-and-rollback-frozen-recovery-plans
"""Guarded apply, rollback, and project-owned probe execution."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Callable

from .evidence import RecoveryAction, RecoveryPlan, read_plan


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _has_link_component(path: Path, root: Path) -> bool:
    relative = path.resolve(strict=False).relative_to(root.resolve(strict=False))
    cursor = root.resolve(strict=False)
    for part in relative.parts[:-1]:
        cursor /= part
        if not cursor.exists():
            continue
        if cursor.is_symlink() or (hasattr(cursor, "is_junction") and cursor.is_junction()):
            return True
    return False


def _atomic_write_missing(target: Path, content: bytes) -> None:
    if target.exists():
        raise RuntimeError(f"target appeared after plan: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=target.parent, delete=False) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    if target.exists():
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"target appeared before replace: {target}")
    os.replace(temporary, target)


def _fail(failpoint: str | None, stage: str, index: int) -> None:
    if failpoint in {stage, f"{stage}:{index}"}:
        raise RuntimeError(f"injected recovery failure at {stage}:{index}")


def create_preapply_snapshot(plan: RecoveryPlan, snapshot_dir: Path) -> dict[str, Any]:
    plan.verify()
    snapshot_dir.mkdir(parents=True, exist_ok=False)
    records: list[dict[str, Any]] = []
    workspace = Path(plan.workspace_root).resolve(strict=False)
    for action in plan.actions:
        if not action.target_path:
            continue
        target = Path(action.target_path).resolve(strict=False)
        if not _under(target, workspace):
            raise RuntimeError(f"snapshot target escaped workspace: {target}")
        exists = target.is_file()
        before = target.read_bytes() if exists else None
        blob_name = None
        if before is not None:
            digest = _sha256(before)
            blob = snapshot_dir / "objects" / "sha256" / digest[:2] / digest
            blob.parent.mkdir(parents=True, exist_ok=True)
            if not blob.exists():
                blob.write_bytes(before)
            blob_name = str(blob.relative_to(snapshot_dir)).replace("\\", "/")
        records.append({
            "action_id": action.action_id,
            "target_path": str(target),
            "before_exists": exists,
            "before_sha256": _sha256(before) if before is not None else None,
            "before_blob": blob_name,
        })
    payload = {
        "schema_version": 1,
        "plan_sha256": plan.plan_sha256,
        "workspace_root": str(workspace),
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "records": records,
    }
    payload["snapshot_sha256"] = _sha256(_canonical(payload))
    manifest = snapshot_dir / "pre-apply-snapshot.json"
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Re-read what was persisted rather than trusting only in-memory values.
    persisted = json.loads(manifest.read_text(encoding="utf-8"))
    expected = persisted.pop("snapshot_sha256")
    if _sha256(_canonical(persisted)) != expected:
        raise RuntimeError("pre-apply snapshot verification failed")
    persisted["snapshot_sha256"] = expected
    return persisted


def verify_preapply_snapshot(
    plan: RecoveryPlan,
    snapshot_dir: Path,
    *,
    verify_workspace_guards: bool = True,
) -> dict[str, Any]:
    manifest = snapshot_dir / "pre-apply-snapshot.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    expected = payload.pop("snapshot_sha256")
    if _sha256(_canonical(payload)) != expected:
        raise RuntimeError("pre-apply snapshot hash mismatch")
    if payload.get("plan_sha256") != plan.plan_sha256:
        raise RuntimeError("pre-apply snapshot belongs to another plan")
    if Path(payload.get("workspace_root", "")).resolve(strict=False) != Path(plan.workspace_root).resolve(strict=False):
        raise RuntimeError("pre-apply snapshot workspace mismatch")
    if verify_workspace_guards:
        for record in payload.get("records", []):
            target = Path(record["target_path"])
            exists = target.is_file()
            if exists != bool(record["before_exists"]):
                raise RuntimeError(f"workspace guard changed after snapshot: {target}")
            if exists and _sha256(target.read_bytes()) != record.get("before_sha256"):
                raise RuntimeError(f"workspace bytes changed after snapshot: {target}")
    payload["snapshot_sha256"] = expected
    return payload


def apply_plan(
    plan: RecoveryPlan | Path,
    *,
    snapshot_dir: Path,
    confirm_workspace: str,
    artifact_dir: Path | None = None,
    failpoint: str | None = None,
) -> dict[str, Any]:
    """Apply missing files and quarantined artifacts with a durable journal.

    Existing files are never overwritten. ``failpoint`` is a test-only hook used
    to prove that partial writes remain discoverable and rollback-safe.
    """

    frozen = read_plan(plan) if isinstance(plan, Path) else plan
    frozen.verify()
    workspace = Path(frozen.workspace_root).resolve(strict=False)
    if confirm_workspace != str(workspace):
        raise RuntimeError("confirm_workspace must exactly equal the frozen workspace root")
    if (snapshot_dir / "pre-apply-snapshot.json").is_file():
        # Per-action guards below distinguish missing, already-applied, and
        # drifted targets. This makes an interrupted/repeated apply idempotent
        # without weakening the standalone strict snapshot verification command.
        snapshot = verify_preapply_snapshot(frozen, snapshot_dir, verify_workspace_guards=False)
    else:
        snapshot = create_preapply_snapshot(frozen, snapshot_dir)
    _fail(failpoint, "after-snapshot", 0)
    journal = snapshot_dir / "journal.jsonl"
    applied = 0
    already_applied = 0
    quarantined = 0

    for index, action in enumerate(frozen.actions):
        if action.decision not in {
            "restore-missing-mutation", "restore-missing-read-fallback", "quarantine-exact-artifact"
        }:
            continue
        if action.content_b64 is None or action.candidate_sha256 is None:
            raise RuntimeError(f"action has no frozen candidate: {action.action_id}")
        content = base64.b64decode(action.content_b64, validate=True)
        if _sha256(content) != action.candidate_sha256 or len(content) != action.candidate_length:
            raise RuntimeError(f"candidate drift: {action.action_id}")

        is_artifact = action.decision == "quarantine-exact-artifact"
        if is_artifact:
            if artifact_dir is None or not action.artifact_relative_path:
                continue
            root = artifact_dir.resolve(strict=False)
            target = (root / action.artifact_relative_path).resolve(strict=False)
        else:
            root = workspace
            if not action.target_path:
                raise RuntimeError(f"workspace action has no target: {action.action_id}")
            target = Path(action.target_path).resolve(strict=False)
        if not _under(target, root) or _has_link_component(target, root):
            raise RuntimeError(f"unsafe recovery target: {target}")
        if target.exists():
            current = _sha256(target.read_bytes()) if target.is_file() else None
            if current == action.candidate_sha256:
                already_applied += 1
                continue
            raise RuntimeError(f"target guard changed after plan: {target}")

        row = {
            "schema_version": 1,
            "action_id": action.action_id,
            "target_path": str(target),
            "artifact": is_artifact,
            "before_exists": False,
            "before_sha256": None,
            "after_sha256": action.candidate_sha256,
            "plan_sha256": frozen.plan_sha256,
            "snapshot_sha256": snapshot["snapshot_sha256"],
        }
        _fail(failpoint, "before-journal-intent", index)
        _append_jsonl(journal, {**row, "phase": "intent"})
        _fail(failpoint, "after-journal-intent", index)
        _fail(failpoint, "before-replace", index)
        _atomic_write_missing(target, content)
        _fail(failpoint, "after-replace", index)
        if _sha256(target.read_bytes()) != action.candidate_sha256:
            raise RuntimeError(f"post-write verification failed: {target}")
        _fail(failpoint, "before-journal-result", index)
        _append_jsonl(journal, {
            **row, "phase": "result", "status": "applied",
            "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        })
        if is_artifact:
            quarantined += 1
        else:
            applied += 1
    return {
        "schema_version": 1,
        "plan_sha256": frozen.plan_sha256,
        "snapshot": str(snapshot_dir / "pre-apply-snapshot.json"),
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "journal": str(journal),
        "applied": applied,
        "already_applied": already_applied,
        "quarantined_artifacts": quarantined,
    }


def rollback(
    journal: Path,
    *,
    confirm_workspace: str,
    confirm_artifact_root: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Reverse completed missing-file actions without clobbering later changes."""

    rows = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines() if line.strip()]
    intents: dict[str, dict[str, Any]] = {}
    completed: set[str] = set()
    for row in rows:
        if row.get("phase") == "intent":
            intents[row["action_id"]] = row
        if row.get("phase") == "result" and row.get("status") == "applied":
            intents[row["action_id"]] = row
            completed.add(row["action_id"])
    workspace = Path(confirm_workspace).resolve(strict=False)
    artifact_root = Path(confirm_artifact_root).resolve(strict=False) if confirm_artifact_root else None
    results: list[dict[str, Any]] = []
    for row in reversed(list(intents.values())):
        target = Path(row["target_path"]).resolve(strict=False)
        if row.get("artifact"):
            if artifact_root is None or not _under(target, artifact_root):
                results.append({"action_id": row["action_id"], "status": "refused-unconfirmed-artifact-root"})
                continue
        elif not _under(target, workspace):
            results.append({"action_id": row["action_id"], "status": "refused-outside-workspace"})
            continue
        if not target.exists():
            results.append({
                "action_id": row["action_id"],
                "status": "already-absent" if row["action_id"] in completed else "intent-not-applied",
            })
            continue
        if not target.is_file() or _sha256(target.read_bytes()) != row.get("after_sha256"):
            results.append({"action_id": row["action_id"], "status": "conflict-after-apply"})
            continue
        if row.get("before_exists"):
            results.append({"action_id": row["action_id"], "status": "unsupported-existing-file"})
            continue
        if not dry_run:
            target.unlink()
            _append_jsonl(journal, {
                **row, "phase": "rollback-result", "status": "rolled-back",
                "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            })
        results.append({
            "action_id": row["action_id"],
            "status": "would-remove" if dry_run else "rolled-back",
        })
    return {
        "schema_version": 1,
        "mode": "dry-run" if dry_run else "apply",
        "journal": str(journal),
        "results": results,
    }


def run_probe_spec(workspace_root: Path, spec: dict[str, Any]) -> dict[str, Any]:
    """Run deterministic project-owned probes; semantic review stays with its Skill."""

    workspace = workspace_root.resolve(strict=False)
    results: list[dict[str, Any]] = []
    for probe in spec.get("probes", []):
        kind = probe.get("kind")
        probe_id = str(probe.get("id") or kind or "probe")
        if kind == "file-sha256":
            target = (workspace / str(probe["path"])).resolve(strict=False)
            if not _under(target, workspace) or not target.is_file():
                results.append({"id": probe_id, "kind": kind, "passed": False, "reason": "missing-or-unsafe"})
                continue
            actual = _sha256(target.read_bytes())
            results.append({
                "id": probe_id, "kind": kind, "passed": actual == probe["sha256"],
                "actual_sha256": actual,
            })
        elif kind == "command":
            argv = probe.get("argv")
            if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
                results.append({"id": probe_id, "kind": kind, "passed": False, "reason": "invalid-argv"})
                continue
            cwd = (workspace / str(probe.get("cwd") or ".")).resolve(strict=False)
            if not _under(cwd, workspace):
                results.append({"id": probe_id, "kind": kind, "passed": False, "reason": "cwd-outside-workspace"})
                continue
            completed = subprocess.run(
                argv, cwd=cwd, shell=False, capture_output=True, text=True,
                timeout=float(probe.get("timeout_seconds", 60)), check=False,
            )
            results.append({
                "id": probe_id, "kind": kind, "passed": completed.returncode == int(probe.get("expect_exit", 0)),
                "returncode": completed.returncode,
                "stdout_sha256": _sha256(completed.stdout.encode("utf-8")),
                "stderr_sha256": _sha256(completed.stderr.encode("utf-8")),
            })
        else:
            results.append({"id": probe_id, "kind": kind, "passed": False, "reason": "unknown-probe-kind"})
    return {
        "schema_version": 1,
        "workspace_root": str(workspace),
        "project_owned": True,
        "semantic_review_required": bool(spec.get("semantic_review_required", True)),
        "passed": all(row["passed"] for row in results),
        "results": results,
    }
