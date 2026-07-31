"""Fail-closed administrative closeout for abandoned AI-player evidence runs.

The reconciler is intentionally detached from the device gateway and provider
runner.  It only reads durable ownership receipts and uses
``EvidenceRecorder.stop_run`` after every liveness condition has been proven.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from ..evidence import EvidenceRecorder
from ..models import DeviceLease, EvidenceRun, EvidenceStep
from ..store import ObservatoryStore
from .external_agent_runtime import ExternalAgentInvocationV1


_OPEN_RUN_STATUSES = {"running", "paused"}
_TERMINAL_STEP_STATUSES = {"passed", "failed", "stopped"}
_SAFE_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,127}")

ProcessIdentityProbe = Callable[[int, float | None], bool]


@dataclass(frozen=True)
class EvidenceRunReconciliationItem:
    evidence_run_id: str
    disposition: Literal["reconciled", "skipped"]
    reason_code: str
    detail: str


@dataclass(frozen=True)
class EvidenceRunReconciliationResult:
    scanned: int
    reconciled: int
    items: tuple[EvidenceRunReconciliationItem, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "game-observatory.ai-player.evidence-run-reconciliation.v1",
            "scanned": self.scanned,
            "reconciled": self.reconciled,
            "items": [asdict(item) for item in self.items],
        }


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _default_process_identity_probe(pid: int, started_at: float | None) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil  # type: ignore[import-not-found]

        process = psutil.Process(pid)
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return False
        return started_at is None or abs(process.create_time() - started_at) < 0.01
    except ImportError:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    except Exception:
        return False


def _normalized_sequence(value: object) -> int | None:
    try:
        sequence = int(value)
    except (TypeError, ValueError):
        return None
    return sequence if sequence >= 1 else None


class EvidenceRunReconciler:
    """Safely stop stale runs owned by an already-terminal external invocation."""

    def __init__(
        self,
        store: ObservatoryStore,
        *,
        stale_after_seconds: float = 300.0,
        clock: Callable[[], datetime] | None = None,
        process_identity_probe: ProcessIdentityProbe = _default_process_identity_probe,
    ) -> None:
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        self.store = store
        self.stale_after_seconds = float(stale_after_seconds)
        self.clock = clock or _now_utc
        self.process_identity_probe = process_identity_probe
        self.external_sessions_root = self.store.root / "external_agent_sessions"

    def reconcile(
        self,
        *,
        evidence_run_id: str | None = None,
        limit: int = 1000,
    ) -> EvidenceRunReconciliationResult:
        if evidence_run_id is not None:
            run = self.store.get_evidence_run(evidence_run_id)
            runs = [run] if run is not None else []
        else:
            runs = self.store.list_evidence_runs(limit=max(1, min(limit, 1000)))
        items = tuple(self._reconcile_one(run) for run in runs)
        return EvidenceRunReconciliationResult(
            scanned=len(runs),
            reconciled=sum(item.disposition == "reconciled" for item in items),
            items=items,
        )

    @staticmethod
    def _skipped(run: EvidenceRun, code: str, detail: str) -> EvidenceRunReconciliationItem:
        return EvidenceRunReconciliationItem(
            evidence_run_id=run.id,
            disposition="skipped",
            reason_code=code,
            detail=detail,
        )

    def _owner(self, run: EvidenceRun) -> tuple[str, str, int] | None:
        invocation_id = run.environment.get("external_agent_invocation_id")
        session_id = run.environment.get("external_agent_session_id")
        sequence = _normalized_sequence(
            run.environment.get("external_agent_invocation_sequence")
        )
        if (
            not isinstance(invocation_id, str)
            or not invocation_id
            or not isinstance(session_id, str)
            or _SAFE_SESSION_ID.fullmatch(session_id) is None
            or sequence is None
        ):
            return None
        return invocation_id, session_id, sequence

    def _terminal_invocation(
        self,
        owner: tuple[str, str, int],
    ) -> ExternalAgentInvocationV1 | None:
        invocation_id, session_id, sequence = owner
        path = (
            self.external_sessions_root
            / session_id
            / "invocations"
            / f"turn-{sequence:04d}.json"
        )
        if not path.is_file():
            return None
        try:
            invocation = ExternalAgentInvocationV1.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return None
        if (
            invocation.id != invocation_id
            or invocation.session_id != session_id
            or invocation.sequence != sequence
        ):
            return None
        return invocation

    def _owned_lease_is_active(
        self,
        run: EvidenceRun,
        owner: tuple[str, str, int],
        now: datetime,
    ) -> bool:
        invocation_id, session_id, sequence = owner
        declared_lease_id = run.environment.get("device_gateway_lease_id")
        for lease in self.store.list_leases(run.target_id):
            if not self._lease_belongs_to_run(
                lease,
                declared_lease_id=declared_lease_id,
                invocation_id=invocation_id,
                session_id=session_id,
                sequence=sequence,
            ):
                continue
            if lease.status != "active":
                continue
            expires_at = _parse_timestamp(lease.expires_at)
            if expires_at is None or expires_at > now:
                return True
        return False

    @staticmethod
    def _lease_belongs_to_run(
        lease: DeviceLease,
        *,
        declared_lease_id: object,
        invocation_id: str,
        session_id: str,
        sequence: int,
    ) -> bool:
        if isinstance(declared_lease_id, str) and declared_lease_id == lease.id:
            return True
        context = lease.owner_context
        return (
            context.get("external_agent_invocation_id") == invocation_id
            and context.get("external_agent_session_id") == session_id
            and _normalized_sequence(
                context.get("external_agent_invocation_sequence")
            )
            == sequence
        )

    def _owner_process_is_active(self, session_id: str, sequence: int) -> tuple[bool, str]:
        session_dir = self.external_sessions_root / session_id
        lease_path = session_dir / "invocation.lease.json"
        if lease_path.exists():
            try:
                lease = json.loads(lease_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return True, "external invocation lease is unreadable"
            if lease.get("session_id") != session_id:
                return True, "external invocation lease ownership is inconsistent"
            for role in ("owner", "provider"):
                pid = _normalized_process_id(lease.get(f"{role}_pid"))
                started_at = _normalized_process_start(lease.get(f"{role}_started_at"))
                if pid is not None and self.process_identity_probe(pid, started_at):
                    return True, f"external invocation {role} process is still active"

        heartbeat_path = session_dir / "heartbeat.json"
        if heartbeat_path.exists():
            try:
                heartbeat = json.loads(heartbeat_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return True, "external invocation heartbeat is unreadable"
            if (
                heartbeat.get("session_id") == session_id
                and _normalized_sequence(heartbeat.get("sequence")) == sequence
            ):
                pid = _normalized_process_id(heartbeat.get("process_id"))
                if pid is not None and self.process_identity_probe(pid, None):
                    return True, "external invocation heartbeat process is still active"
        return False, "no active external invocation process"

    def _freshness_boundary(
        self,
        run: EvidenceRun,
        invocation: ExternalAgentInvocationV1,
        steps: list[EvidenceStep],
    ) -> datetime | None:
        values = [run.started_at, invocation.completed_at, *(step.ended_at for step in steps)]
        parsed = [_parse_timestamp(value) for value in values]
        return max(parsed) if all(value is not None for value in parsed) else None

    def _reconcile_one(self, run: EvidenceRun) -> EvidenceRunReconciliationItem:
        if run.status not in _OPEN_RUN_STATUSES:
            return self._skipped(run, "already_terminal", f"run status is {run.status}")

        owner = self._owner(run)
        if owner is None:
            return self._skipped(
                run,
                "owner_unverifiable",
                "external invocation ownership is missing or invalid",
            )
        invocation = self._terminal_invocation(owner)
        if invocation is None:
            return self._skipped(
                run,
                "owner_not_terminal",
                "matching terminal external invocation receipt is absent or invalid",
            )

        steps = self.store.list_evidence_steps(run.id)
        if not steps:
            return self._skipped(
                run,
                "terminal_step_absent",
                "run has no durable EvidenceStep to preserve",
            )
        unfinished = [step.id for step in steps if step.status not in _TERMINAL_STEP_STATUSES]
        if unfinished:
            return self._skipped(
                run,
                "step_not_terminal",
                f"run still has non-terminal steps: {', '.join(unfinished)}",
            )

        now = _as_utc(self.clock())
        boundary = self._freshness_boundary(run, invocation, steps)
        if boundary is None:
            return self._skipped(
                run,
                "timestamp_unverifiable",
                "run, invocation, or step timestamp is missing or invalid",
            )
        age_seconds = (now - boundary).total_seconds()
        if age_seconds < self.stale_after_seconds:
            return self._skipped(
                run,
                "run_not_stale",
                f"run is only {age_seconds:.3f}s past its latest durable activity",
            )

        if self._owned_lease_is_active(run, owner, now):
            return self._skipped(
                run,
                "owned_lease_active",
                "an unexpired active device lease still belongs to this run",
            )
        process_active, process_detail = self._owner_process_is_active(owner[1], owner[2])
        if process_active:
            return self._skipped(run, "owner_process_active", process_detail)

        reason = (
            "administrative reconciliation of stale AI-player evidence run after terminal "
            f"owner invocation {invocation.id}; preserved {len(steps)} terminal EvidenceStep(s); "
            "no device action or unknown-action replay was performed"
        )
        stopped = EvidenceRecorder(self.store, None).stop_run(run.id, reason=reason)
        self.store.append_event(
            run.id,
            "evidence_run_reconciled",
            {
                "reason": reason,
                "owner_invocation_id": invocation.id,
                "owner_invocation_status": invocation.status,
                "owner_session_id": owner[1],
                "owner_invocation_sequence": owner[2],
                "terminal_step_ids": [step.id for step in steps],
                "stale_after_seconds": self.stale_after_seconds,
                "age_seconds": age_seconds,
                "result_status": stopped.status,
                "device_accessed": False,
                "provider_accessed": False,
                "unknown_action_replayed": False,
            },
        )
        return EvidenceRunReconciliationItem(
            evidence_run_id=run.id,
            disposition="reconciled",
            reason_code="stale_run_stopped",
            detail=reason,
        )


def _normalized_process_id(value: object) -> int | None:
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _normalized_process_start(value: object) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Safely settle stale AI-player EvidenceRun records without device access."
    )
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--stale-after-seconds", type=float, default=300.0)
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args(argv)
    root = args.root.expanduser().resolve()
    if not (root / "observatory.sqlite3").is_file():
        parser.error(f"Observatory database does not exist under: {root}")
    result = EvidenceRunReconciler(
        ObservatoryStore(root),
        stale_after_seconds=args.stale_after_seconds,
    ).reconcile(evidence_run_id=args.run_id, limit=args.limit)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main(argv)
    raise SystemExit(main())
