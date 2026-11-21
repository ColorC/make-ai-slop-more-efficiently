# [OMNI] origin=codex domain=services/_core/lifecycle ts=2026-08-09 type=infra status=active
"""Fail-closed managed subagent completion receipts for canonical tasks.

The task remains ``review`` (displayed as ``NEEDS_VALIDATION``) after a managed
worker stops.  Only a fresh parent-side validation receipt permits ``done``.
Runtime records are evidence, not a second task store; canonical task state is
still owned by :mod:`omnicompany.packages.services._core.lifecycle.task`.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from omnicompany.packages.services._core.lifecycle.task import TaskStore

SCHEMA = "omnicompany.managed-agent-completion/v1"
RECEIPT_SCHEMA = "omnicompany.managed-agent-receipt/v1"
ACTIVE_STATES = {"ARMED", "RUNNING", "NEEDS_VALIDATION"}

ERR_NOT_ARMED = "INC_AGENT_NOT_ARMED"
ERR_AMBIGUOUS_ARM = "INC_AGENT_AMBIGUOUS_ARM"
ERR_PARENT_MISMATCH = "INC_AGENT_PARENT_MISMATCH"
ERR_TASK_MISMATCH = "INC_AGENT_TASK_MISMATCH"
ERR_WORKER_MISMATCH = "INC_AGENT_WORKER_MISMATCH"
ERR_TIMEOUT = "INC_AGENT_TIMEOUT"
ERR_RECEIPT_MISSING = "INC_AGENT_RECEIPT_MISSING"
ERR_RECEIPT_TAMPERED = "INC_AGENT_RECEIPT_TAMPERED"
ERR_FILESET_MISMATCH = "INC_AGENT_FILESET_MISMATCH"
ERR_CL_MISMATCH = "INC_AGENT_CL_MISMATCH"
ERR_P4_ACCEPTANCE_FAILED = "INC_AGENT_P4_ACCEPTANCE_FAILED"
ERR_GIT_ACCEPTANCE_FAILED = "INC_AGENT_GIT_ACCEPTANCE_FAILED"
ERR_TASK_TRANSITION_FAILED = "INC_AGENT_TASK_TRANSITION_FAILED"
ERR_VALIDATION_REQUIRED = "INC_AGENT_VALIDATION_REQUIRED"
ERR_ALREADY_ACTIVE = "INC_AGENT_ALREADY_ACTIVE"


class ManagedAgentCompletionError(RuntimeError):
    """Stable, machine-readable failure raised by the managed completion gate."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error_code": self.code,
            "message": str(self),
            "details": self.details,
        }


def _root() -> Path:
    override = os.environ.get("OMNI_MANAGED_AGENT_COMPLETION_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    from omnicompany.core.config import omni_workspace_root
    return omni_workspace_root() / "data" / "managed_agent_completion"


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _seal(payload: dict[str, Any]) -> dict[str, Any]:
    body = {k: v for k, v in payload.items() if k != "digest"}
    return {**body, "digest": hashlib.sha256(_canonical(body)).hexdigest()}


def _verify_seal(payload: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ManagedAgentCompletionError(ERR_RECEIPT_TAMPERED, f"{label} is not a JSON object")
    actual = str(payload.get("digest") or "")
    expected = _seal(payload)["digest"]
    if not actual or actual != expected:
        raise ManagedAgentCompletionError(
            ERR_RECEIPT_TAMPERED,
            f"{label} digest mismatch",
            details={"expected": expected, "actual": actual},
        )
    return payload


def _now_iso(now: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))


def _path_key(path: str) -> str:
    return path.replace("\\", "/").casefold()


def _git_head(workspace: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
    )
    if proc.returncode != 0:
        raise ManagedAgentCompletionError(
            ERR_GIT_ACCEPTANCE_FAILED,
            "workspace is not a readable Git worktree",
            details={"workspace": str(workspace), "stderr": proc.stderr.strip()},
        )
    return proc.stdout.strip()


def _git_commit_paths(workspace: Path, before: str, after: str) -> list[str]:
    if before == after:
        return []
    proc = subprocess.run(
        ["git", "-C", str(workspace), "diff", "--name-only", f"{before}..{after}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    if proc.returncode != 0:
        raise ManagedAgentCompletionError(
            ERR_GIT_ACCEPTANCE_FAILED,
            "cannot inspect commits made by managed worker",
            details={"stderr": proc.stderr.strip()},
        )
    return [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]


def _artifact_digests(workspace: Path, files: list[str]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for rel in files:
        path = workspace / rel
        result[rel] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return result


def _normalize_files(workspace: Path, files: list[str]) -> list[str]:
    normalized: list[str] = []
    root = workspace.resolve()
    for item in files:
        candidate = Path(item)
        absolute = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        try:
            rel = absolute.relative_to(root).as_posix()
        except ValueError as exc:
            raise ManagedAgentCompletionError(
                ERR_FILESET_MISMATCH,
                "expected file is outside managed workspace",
                details={"workspace": str(root), "file": str(absolute)},
            ) from exc
        if rel not in normalized:
            normalized.append(rel)
    if not normalized:
        raise ManagedAgentCompletionError(ERR_FILESET_MISMATCH, "at least one exact expected file is required")
    return sorted(normalized, key=str.casefold)


def _load_p4(workspace: Path):
    try:
        p4_mod = importlib.import_module("demogame_auto_config_cli.change.p4_acceptance")
        policy_mod = importlib.import_module("demogame_auto_config_cli.change.policy")
        return p4_mod.ExactP4Acceptance, p4_mod.P4AcceptanceError, policy_mod.Artifact
    except ImportError:
        candidates = [
            workspace / "cli" / "demogame-auto-config-cli",
            Path(os.environ.get("demogame_AUTO_CONFIG_CLI_ROOT", "")),
        ]
        for candidate in candidates:
            if str(candidate) and (candidate / "demogame_auto_config_cli").is_dir():
                sys.path.insert(0, str(candidate))
                try:
                    p4_mod = importlib.import_module("demogame_auto_config_cli.change.p4_acceptance")
                    policy_mod = importlib.import_module("demogame_auto_config_cli.change.policy")
                    return p4_mod.ExactP4Acceptance, p4_mod.P4AcceptanceError, policy_mod.Artifact
                finally:
                    try:
                        sys.path.remove(str(candidate))
                    except ValueError:
                        pass
        raise ManagedAgentCompletionError(
            ERR_P4_ACCEPTANCE_FAILED,
            "ExactP4Acceptance is unavailable from the configured P4 workspace",
            details={"workspace": str(workspace)},
        )


def _p4_artifacts(workspace: Path, files: list[str], artifact_cls: Any) -> list[Any]:
    return [
        artifact_cls(
            kind=Path(rel).suffix.lstrip(".") or "file",
            table="managed-agent",
            partition="official",
            logical_name=Path(rel).name,
            local_path=str((workspace / rel).resolve()),
            action="submit",
            source_workbook="",
            source_sheet="",
        )
        for rel in files
    ]


class ManagedAgentCompletion:
    def __init__(
        self,
        root: Path | None = None,
        *,
        task_store: "TaskStore | None" = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = (root or _root()).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.task_store = task_store
        self.clock = clock

    def _task_store(self) -> "TaskStore":
        if self.task_store is None:
            from omnicompany.packages.services._core.lifecycle.task import TaskStore
            self.task_store = TaskStore()
        return self.task_store

    def _path(self, arm_id: str) -> Path:
        safe = "".join(c for c in arm_id if c.isalnum() or c in "-_")
        if not safe or safe != arm_id:
            raise ManagedAgentCompletionError(ERR_NOT_ARMED, "invalid arm id")
        return self.root / f"{safe}.json"

    def _write(self, record: dict[str, Any]) -> dict[str, Any]:
        sealed = _seal(record)
        target = self._path(str(sealed["arm_id"]))
        tmp = target.with_suffix(f".tmp.{uuid.uuid4().hex}")
        tmp.write_text(json.dumps(sealed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)
        return sealed

    def load(self, arm_id: str) -> dict[str, Any]:
        path = self._path(arm_id)
        if not path.is_file():
            raise ManagedAgentCompletionError(ERR_NOT_ARMED, f"managed arm not found: {arm_id}")
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ManagedAgentCompletionError(ERR_RECEIPT_TAMPERED, f"managed arm is unreadable: {arm_id}") from exc
        _verify_seal(record, label=f"arm {arm_id}")
        if record.get("schema") != SCHEMA:
            raise ManagedAgentCompletionError(ERR_RECEIPT_TAMPERED, f"unsupported managed arm schema: {record.get('schema')}")
        for name in ("completion_receipt", "validation_receipt"):
            if record.get(name) is not None:
                _verify_seal(record[name], label=f"{arm_id} {name}")
        return record

    def list(self, *, task_id: str | None = None, plan_id: str | None = None) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*.json")):
            record = self.load(path.stem)
            if task_id is not None and str(record.get("task_id")) != str(task_id):
                continue
            if plan_id is not None and str(record.get("plan_id")) != str(plan_id):
                continue
            records.append(record)
        return sorted(records, key=lambda item: float(item.get("created_at") or 0), reverse=True)

    def arm(
        self,
        *,
        task_id: str,
        plan_id: str,
        workspace: str | Path,
        files: list[str],
        kind: str = "git",
        changelist: str | None = None,
        timeout_seconds: int = 3600,
        provider: str = "codex",
        parent_session_id: str | None = None,
        parent_trace_id: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        now = self.clock()
        workspace_path = Path(workspace).expanduser().resolve()
        if kind not in {"git", "p4", "auto"}:
            raise ManagedAgentCompletionError(ERR_GIT_ACCEPTANCE_FAILED, f"unsupported workspace kind: {kind}")
        if kind == "auto":
            kind = "git" if (workspace_path / ".git").exists() else "p4"
        if timeout_seconds <= 0:
            raise ManagedAgentCompletionError(ERR_TIMEOUT, "timeout must be positive")
        expected = _normalize_files(workspace_path, files)
        if kind == "p4" and not str(changelist or "").isdigit():
            raise ManagedAgentCompletionError(ERR_CL_MISMATCH, "P4 managed work requires an exact numeric changelist")

        from omnicompany.packages.services._core.identity.resolver import current_session_meta, get_session_binding
        meta = current_session_meta()
        trace_id = parent_trace_id or str(meta.get("trace_id") or "")
        binding = get_session_binding(trace_id) if trace_id else None
        session_id = parent_session_id or str(meta.get("session_id") or (binding or {}).get("session_id") or (binding or {}).get("claude_session_id") or "")
        bound_task = str(meta.get("task_id") or (binding or {}).get("task_id") or "")
        bound_plan = str(meta.get("active_plan") or (binding or {}).get("active_plan") or "")
        if bound_task and bound_task != str(task_id):
            raise ManagedAgentCompletionError(ERR_TASK_MISMATCH, "current parent session is bound to another task", details={"bound": bound_task, "requested": task_id})
        if bound_plan and bound_plan != str(plan_id):
            raise ManagedAgentCompletionError(ERR_TASK_MISMATCH, "current parent session is bound to another plan", details={"bound": bound_plan, "requested": plan_id})
        if not session_id or not trace_id:
            raise ManagedAgentCompletionError(ERR_PARENT_MISMATCH, "managed arm requires a resolved parent session and trace identity")
        if any(record.get("state") in ACTIVE_STATES for record in self.list(task_id=task_id, plan_id=plan_id)):
            raise ManagedAgentCompletionError(ERR_ALREADY_ACTIVE, "task already has an active managed worker arm")

        task = self._task_store().get(task_id, plan_id)
        if task is None:
            raise ManagedAgentCompletionError(ERR_TASK_MISMATCH, "canonical task does not exist", details={"task_id": task_id, "plan_id": plan_id})
        if task.status == "pending":
            self._task_store().set_status(task_id, "in_progress", plan_id)
        elif task.status not in {"in_progress", "blocked"}:
            raise ManagedAgentCompletionError(ERR_TASK_MISMATCH, f"task cannot be armed from status {task.status}")
        elif task.status == "blocked":
            self._task_store().set_status(task_id, "in_progress", plan_id)

        baseline: dict[str, Any]
        if kind == "git":
            from omnicompany.packages.services._core.agent.external_workers.codex import _git_changed_files, _snapshot_watch_paths
            changed = sorted(set(_git_changed_files(workspace_path)), key=str.casefold)
            watched = sorted(set(expected) | set(changed), key=str.casefold)
            baseline = {
                "head": _git_head(workspace_path),
                "changed_files": changed,
                "watch": _snapshot_watch_paths(workspace_path, [workspace_path / rel for rel in watched]),
            }
        else:
            exact_cls, _error_cls, artifact_cls = _load_p4(workspace_path)
            artifacts = _p4_artifacts(workspace_path, expected, artifact_cls)
            baseline = {"preflight": exact_cls().capture_preflight(artifacts, workspace_path)}
            blockers = list(baseline["preflight"].get("blockers") or [])
            if blockers:
                raise ManagedAgentCompletionError(ERR_P4_ACCEPTANCE_FAILED, "P4 preflight is not acceptable", details={"blockers": blockers})

        arm_id = f"ma-{uuid.uuid4().hex}"
        record = {
            "schema": SCHEMA,
            "arm_id": arm_id,
            "state": "ARMED",
            "display_status": "ARMED",
            "task_id": str(task_id),
            "plan_id": str(plan_id),
            "project": project or meta.get("project"),
            "provider": provider,
            "parent": {"session_id": session_id, "trace_id": trace_id},
            "worker": None,
            "workspace": {"kind": kind, "root": str(workspace_path)},
            "expected_files": expected,
            "expected_changelist": str(changelist or ""),
            "created_at": now,
            "created_at_iso": _now_iso(now),
            "deadline": now + timeout_seconds,
            "baseline": baseline,
            "completion_receipt": None,
            "validation_receipt": None,
            "failure": None,
        }
        record = self._write(record)
        self._notify(record, "managed_agent_completion.armed")
        return record

    def _select(self, *, states: set[str], parent_session_id: str | None, task_id: str | None = None, worker_id: str | None = None) -> dict[str, Any] | None:
        candidates = [record for record in self.list(task_id=task_id) if record.get("state") in states]
        exact = [record for record in candidates if not parent_session_id or record.get("parent", {}).get("session_id") == parent_session_id]
        if worker_id:
            worker_exact = [record for record in exact if (record.get("worker") or {}).get("session_id") == worker_id]
            if worker_exact:
                exact = worker_exact
        if not exact:
            if candidates:
                record = candidates[0]
                self._fail(record, ERR_PARENT_MISMATCH, "hook parent identity does not match managed arm", details={"actual_parent": parent_session_id})
            return None
        if len(exact) > 1:
            raise ManagedAgentCompletionError(ERR_AMBIGUOUS_ARM, "multiple managed arms match hook identity")
        return exact[0]

    def subagent_start(self, *, parent_session_id: str, worker_session_id: str, task_id: str | None = None, provider: str = "codex") -> dict[str, Any] | None:
        record = self._select(states={"ARMED"}, parent_session_id=parent_session_id, task_id=task_id)
        if record is None:
            return None
        if task_id and str(record["task_id"]) != str(task_id):
            return self._fail(record, ERR_TASK_MISMATCH, "hook task does not match managed arm")
        if self.clock() > float(record["deadline"]):
            return self._fail(record, ERR_TIMEOUT, "managed worker did not start before deadline")
        record["worker"] = {"session_id": worker_session_id, "provider": provider, "started_at": self.clock()}
        record["state"] = "RUNNING"
        record["display_status"] = "RUNNING"
        record = self._write(record)
        from omnicompany.packages.services._core.identity.resolver import bind_task_for_session
        bind_task_for_session(provider, worker_session_id, task_id=record["task_id"], plan_id=record["plan_id"], project=record.get("project"))
        self._notify(record, "managed_agent_completion.started")
        return record

    def _accept(self, record: dict[str, Any], *, reported_changelist: str | None = None) -> dict[str, Any]:
        workspace = Path(record["workspace"]["root"])
        expected = list(record["expected_files"])
        if record["workspace"]["kind"] == "git":
            from omnicompany.packages.services._core.agent.external_workers.codex import _diff_watch_snapshots, _git_changed_files, _snapshot_watch_paths
            baseline = record["baseline"]
            watched = list((baseline.get("watch") or {}).get("roots") or expected)
            after_watch = _snapshot_watch_paths(workspace, [workspace / rel for rel in watched])
            watch_diff = _diff_watch_snapshots(baseline["watch"], after_watch)
            changed_watch = set(watch_diff.get("created") or []) | set(watch_diff.get("modified") or []) | set(watch_diff.get("deleted") or [])
            before_changed = set(baseline.get("changed_files") or [])
            after_changed = set(_git_changed_files(workspace))
            head = _git_head(workspace)
            commit_paths = set(_git_commit_paths(workspace, str(baseline["head"]), head))
            observed = {p.replace("\\", "/") for p in changed_watch | (after_changed - before_changed) | commit_paths}
            observed_files = sorted((p for p in observed if p and not p.endswith("/")), key=str.casefold)
            if {_path_key(p) for p in observed_files} != {_path_key(p) for p in expected}:
                raise ManagedAgentCompletionError(
                    ERR_FILESET_MISMATCH,
                    "managed Git worker changed a non-exact file set",
                    details={"expected": expected, "observed": observed_files},
                )
            return {
                "kind": "git",
                "head": head,
                "files": observed_files,
                "artifact_digests": _artifact_digests(workspace, expected),
                "diff": watch_diff,
            }

        expected_cl = str(record.get("expected_changelist") or "")
        if reported_changelist and str(reported_changelist) != expected_cl:
            raise ManagedAgentCompletionError(ERR_CL_MISMATCH, "worker reported a different changelist", details={"expected": expected_cl, "actual": str(reported_changelist)})
        exact_cls, error_cls, artifact_cls = _load_p4(workspace)
        artifacts = _p4_artifacts(workspace, expected, artifact_cls)
        try:
            receipt = exact_cls().verify_changelist(
                artifacts,
                record["baseline"]["preflight"],
                workspace,
                changelist=expected_cl,
            )
        except error_cls as exc:
            raise ManagedAgentCompletionError(
                ERR_P4_ACCEPTANCE_FAILED,
                "ExactP4Acceptance rejected managed P4 output",
                details={"error": str(exc), "receipt": getattr(exc, "receipt", {})},
            ) from exc
        return {"kind": "p4", "changelist": expected_cl, "receipt": receipt, "artifact_digests": _artifact_digests(workspace, expected)}

    def subagent_stop(self, *, parent_session_id: str, worker_session_id: str, task_id: str | None = None, changelist: str | None = None) -> dict[str, Any] | None:
        record = self._select(states={"ARMED", "RUNNING"}, parent_session_id=parent_session_id, task_id=task_id, worker_id=worker_session_id)
        if record is None:
            return None
        if not worker_session_id:
            return self._fail(record, ERR_WORKER_MISMATCH, "SubagentStop did not provide a worker identity")
        if record.get("state") == "ARMED":
            record["worker"] = {"session_id": worker_session_id, "provider": record.get("provider") or "codex", "started_at": None}
        elif (record.get("worker") or {}).get("session_id") != worker_session_id:
            return self._fail(record, ERR_WORKER_MISMATCH, "stopping worker does not match managed worker identity")
        if task_id and str(record["task_id"]) != str(task_id):
            return self._fail(record, ERR_TASK_MISMATCH, "stopping worker task does not match managed arm")
        if self.clock() > float(record["deadline"]):
            return self._fail(record, ERR_TIMEOUT, "managed worker exceeded its deadline")
        try:
            acceptance = self._accept(record, reported_changelist=changelist)
        except ManagedAgentCompletionError as exc:
            self._fail(record, exc.code, str(exc), details=exc.details)
            raise
        now = self.clock()
        record["completion_receipt"] = _seal({
            "schema": RECEIPT_SCHEMA,
            "kind": "completion",
            "arm_id": record["arm_id"],
            "task_id": record["task_id"],
            "plan_id": record["plan_id"],
            "parent": record["parent"],
            "worker": record["worker"],
            "accepted_at": now,
            "acceptance": acceptance,
        })
        try:
            self._task_store().set_status(record["task_id"], "review", record["plan_id"])
        except Exception as exc:
            return self._fail(record, ERR_TASK_TRANSITION_FAILED, "cannot move canonical task to review", details={"error": str(exc)})
        record["state"] = "NEEDS_VALIDATION"
        record["display_status"] = "NEEDS_VALIDATION"
        record = self._write(record)
        self._notify(record, "managed_agent_completion.needs_validation")
        return record

    def validate(self, arm_id: str, *, parent_session_id: str | None = None, parent_trace_id: str | None = None) -> dict[str, Any]:
        record = self.load(arm_id)
        if record.get("state") != "NEEDS_VALIDATION" or not record.get("completion_receipt"):
            code = ERR_RECEIPT_MISSING if not record.get("completion_receipt") else ERR_VALIDATION_REQUIRED
            raise ManagedAgentCompletionError(code, "managed completion receipt is not ready for validation")
        expected_parent = record["parent"]
        if parent_session_id and parent_session_id != expected_parent.get("session_id"):
            self._fail(record, ERR_PARENT_MISMATCH, "validator session does not own managed arm")
            raise ManagedAgentCompletionError(ERR_PARENT_MISMATCH, "validator session does not own managed arm")
        if parent_trace_id and parent_trace_id != expected_parent.get("trace_id"):
            self._fail(record, ERR_PARENT_MISMATCH, "validator trace does not own managed arm")
            raise ManagedAgentCompletionError(ERR_PARENT_MISMATCH, "validator trace does not own managed arm")
        acceptance = self._accept(record)
        original = record["completion_receipt"]["acceptance"]
        if acceptance.get("artifact_digests") != original.get("artifact_digests"):
            code = ERR_GIT_ACCEPTANCE_FAILED if acceptance.get("kind") == "git" else ERR_P4_ACCEPTANCE_FAILED
            self._fail(record, code, "managed artifacts drifted after worker receipt")
            raise ManagedAgentCompletionError(code, "managed artifacts drifted after worker receipt")
        now = self.clock()
        record["validation_receipt"] = _seal({
            "schema": RECEIPT_SCHEMA,
            "kind": "validation",
            "ok": True,
            "arm_id": arm_id,
            "task_id": record["task_id"],
            "plan_id": record["plan_id"],
            "parent": expected_parent,
            "validated_at": now,
            "acceptance": acceptance,
        })
        record = self._write(record)
        try:
            task = self._task_store().set_status(record["task_id"], "done", record["plan_id"])
        except Exception as exc:
            record["validation_receipt"] = None
            self._write(record)
            return self._fail(record, ERR_TASK_TRANSITION_FAILED, "validation passed but canonical task could not complete", details={"error": str(exc)})
        record["state"] = "VALIDATED"
        record["display_status"] = "COMPLETE"
        record = self._write(record)
        self._notify(record, "managed_agent_completion.validated")
        return {"ok": True, "record": record, "task": task.to_dict()}

    def stop(self, *, parent_session_id: str) -> list[dict[str, Any]]:
        failures: list[dict[str, Any]] = []
        for record in self.list():
            if record.get("state") not in {"ARMED", "RUNNING"}:
                continue
            if record.get("parent", {}).get("session_id") != parent_session_id:
                continue
            code = ERR_TIMEOUT if self.clock() > float(record["deadline"]) else ERR_RECEIPT_MISSING
            message = "managed worker timed out without a receipt" if code == ERR_TIMEOUT else "parent stopped before managed worker produced a receipt"
            failed = self._fail(record, code, message)
            failures.append(failed)
        return failures

    def expire(self) -> list[dict[str, Any]]:
        failures: list[dict[str, Any]] = []
        for record in self.list():
            if record.get("state") in {"ARMED", "RUNNING", "NEEDS_VALIDATION"} and self.clock() > float(record["deadline"]):
                failures.append(self._fail(record, ERR_TIMEOUT, "managed completion deadline expired"))
        return failures

    def _fail(self, record: dict[str, Any], code: str, message: str, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
        record["state"] = "FAILED"
        record["display_status"] = "FAILED"
        record["failure"] = {"code": code, "message": message, "details": details or {}, "at": self.clock()}
        record = self._write(record)
        try:
            task = self._task_store().get(record["task_id"], record["plan_id"])
            if task and task.status != "blocked":
                self._task_store().set_status(record["task_id"], "blocked", record["plan_id"])
        except Exception as exc:
            record["failure"]["task_transition_error"] = str(exc)
            record = self._write(record)
        self._notify(record, "managed_agent_completion.failed", alert=True)
        return record

    @staticmethod
    def _notify(record: dict[str, Any], event_type: str, *, alert: bool = False) -> None:
        payload = {
            "arm_id": record.get("arm_id"),
            "task_id": record.get("task_id"),
            "plan_id": record.get("plan_id"),
            "state": record.get("state"),
            "display_status": record.get("display_status"),
            "failure": record.get("failure"),
        }
        try:
            from omnicompany.dashboard.ccdaemon.api import hooks_shared as sh
            sh.emit_event(
                trace_id=str(record.get("parent", {}).get("trace_id") or "managed-agent"),
                event_type=event_type,
                payload=payload,
                source="codex",
                tags=["managed-agent", "alert" if alert else "lifecycle"],
            )
            sh.append_audit("managed_agent_completion", payload)
        except Exception:
            pass


def assert_task_completion_allowed(task_id: str, plan_id: str | None = None) -> None:
    """TaskStore gate: a managed task cannot become done without validation."""
    manager = ManagedAgentCompletion()
    records = manager.list(task_id=str(task_id), plan_id=plan_id)
    if not records:
        return
    active_or_failed = [
        record for record in records
        if record.get("state") != "VALIDATED"
        and not ((record.get("validation_receipt") or {}).get("ok") is True)
    ]
    if active_or_failed:
        record = active_or_failed[0]
        raise ManagedAgentCompletionError(
            ERR_VALIDATION_REQUIRED,
            "managed task requires a successful parent validation receipt before COMPLETE",
            details={"arm_id": record.get("arm_id"), "state": record.get("state")},
        )
    latest = records[0]
    receipt = latest.get("validation_receipt")
    if not receipt or receipt.get("ok") is not True:
        raise ManagedAgentCompletionError(ERR_VALIDATION_REQUIRED, "managed task has no successful validation receipt")
