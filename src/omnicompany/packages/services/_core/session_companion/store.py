# [OMNI] origin=codex domain=services/session-companion ts=2026-08-11 type=service
# [OMNI] material_id="material:services.session-companion.store.py"
"""Crash-safe file queue and companion lane checkpoints.

One event per JSON file keeps hook writes atomic and avoids introducing another
shared SQLite database beside provider-owned state.  Queue records are runtime
coordination receipts, never a semantic session/material authority.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from omnicompany.core.config import omni_workspace_root

from .audit import audit_record, execution_digest
from .models import CompanionEvent, stable_digest


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_store_root() -> Path:
    override = os.environ.get("OMNI_SESSION_COMPANION_STATE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    daemon_state = os.environ.get("OMNI_CC_DAEMON_STATE_DIR")
    if daemon_state:
        return Path(daemon_state).expanduser().resolve() / "_runtime" / "session_companion"
    return omni_workspace_root() / "data" / "_runtime" / "session_companion"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _json_text(value: Any) -> str:
    """Serialize queue records even when a PTY supplied malformed Unicode."""
    raw = json.dumps(value, ensure_ascii=False, indent=2)
    return raw.encode("utf-8", errors="replace").decode("utf-8")


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(_json_text(value), encoding="utf-8")
    os.replace(tmp, path)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _archive_attempt(record: dict[str, Any], *, transition: str, reason: str) -> list[dict[str, Any]]:
    history = [row for row in record.get("attempt_history") or [] if isinstance(row, dict)]
    history.append({
        "attempt": int(record.get("attempt") or 0),
        "state": str(record.get("state") or ""),
        "queued_at": record.get("queued_at"),
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "last_error": record.get("last_error") or "",
        "receipts": record.get("receipts") or [],
        "agent": record.get("agent") or {},
        "proposal": record.get("proposal") or {},
        "proposal_digest": record.get("proposal_digest") or "",
        "receipts_digest": record.get("receipts_digest") or "",
        "execution_digest_version": record.get("execution_digest_version") or 1,
        "execution_digest": record.get("execution_digest") or "",
        "audit": audit_record(record),
        "archived_at": _now_iso(),
        "transition": transition,
        "reason": str(reason)[:1000],
    })
    return history


class LockBusy(RuntimeError):
    """Raised when a provider or store lock is already owned."""


class CompanionStore:
    """Durable queue with atomic claims and per-lane conversation checkpoints."""

    STATES = ("queued", "running", "done", "failed")

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root).resolve() if root is not None else default_store_root()
        for name in (*self.STATES, "seen", "turns", "lanes", "locks", "run_records", "workspaces"):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def _event_path(self, state: str, event_id: str) -> Path:
        if state not in self.STATES:
            raise ValueError(f"invalid companion queue state: {state}")
        return self.root / state / f"{event_id}.json"

    def find_event(self, event_id: str) -> tuple[str, Path] | None:
        for state in self.STATES:
            path = self._event_path(state, event_id)
            if path.is_file():
                return state, path
        return None

    def get(self, event_id: str) -> dict[str, Any]:
        """Return one complete queue record with its derived audit status."""

        found = self.find_event(event_id)
        if found is None:
            raise KeyError(event_id)
        state, path = found
        record = _read_json(path)
        if not record:
            raise KeyError(event_id)
        record["state"] = state
        record["audit"] = audit_record(record)
        record["path"] = str(path)
        return record

    def enqueue(self, event: CompanionEvent, *, dry_run: bool = False) -> dict[str, Any]:
        existing = self.find_event(event.event_id)
        if existing:
            state, path = existing
            return {"created": False, "state": state, "path": str(path), "event_id": event.event_id}
        path = self._event_path("queued", event.event_id)
        if dry_run:
            return {"created": False, "dry_run": True, "state": "queued", "path": str(path), "event": event.to_dict()}
        record = {
            "event": event.to_dict(),
            "state": "queued",
            "attempt": 0,
            "queued_at": _now_iso(),
            "receipts": [],
            "last_error": "",
        }
        # Immutable seen marker closes the race where a worker moves queued →
        # running between find_event() and O_EXCL creation of another queued copy.
        seen_path = self.root / "seen" / f"{event.event_id}.json"
        created = True
        try:
            fd = os.open(seen_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            created = False
            found = self.find_event(event.event_id)
            if found:
                state, found_path = found
                return {"created": False, "state": state, "path": str(found_path), "event_id": event.event_id}
            # A producer may have written the marker and not yet materialized
            # queued. Recover from the full marker rather than losing the event.
            saved = _read_json(seen_path)
            if saved:
                _atomic_json(path, saved)
                return {"created": False, "recovered": True, "state": "queued", "path": str(path), "event_id": event.event_id}
            return {"created": False, "state": "seen", "path": str(seen_path), "event_id": event.event_id}
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(_json_text(record))
        except Exception:
            seen_path.unlink(missing_ok=True)
            raise
        _atomic_json(path, record)
        return {"created": created, "state": "queued", "path": str(path), "event_id": event.event_id}

    def list(self, *, state: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        states = (state,) if state else self.STATES
        rows: list[dict[str, Any]] = []
        for item_state in states:
            if item_state not in self.STATES:
                raise ValueError(f"invalid companion queue state: {item_state}")
            for path in (self.root / item_state).glob("evt_*.json"):
                value = _read_json(path)
                if not value:
                    continue
                event = value.get("event") if isinstance(value.get("event"), dict) else {}
                audit = audit_record({**value, "state": item_state})
                rows.append({
                    "event_id": event.get("event_id") or path.stem,
                    "state": item_state,
                    "trigger": event.get("trigger"),
                    "trace_id": event.get("trace_id"),
                    "lane_key": event.get("lane_key"),
                    "attempt": value.get("attempt", 0),
                    "queued_at": value.get("queued_at"),
                    "started_at": value.get("started_at"),
                    "finished_at": value.get("finished_at"),
                    "last_error": value.get("last_error") or "",
                    "audit_status": audit["status"],
                    "finding_count": audit["finding_count"],
                    "action_counts": audit["action_counts"],
                    "path": str(path),
                })
        rows.sort(key=lambda row: str(row.get("queued_at") or ""), reverse=True)
        return rows[: max(1, limit)]

    def recover_stale_running(self, *, max_age_s: float = 1800.0) -> int:
        now = time.time()
        recovered = 0
        for path in (self.root / "running").glob("evt_*.json"):
            try:
                age = now - path.stat().st_mtime
            except OSError:
                continue
            if age < max_age_s:
                continue
            record = _read_json(path)
            reason = "recovered stale running claim"
            history = _archive_attempt(record, transition="stale_recovery", reason=reason)
            record.update({
                "state": "queued",
                "queued_at": record.get("queued_at") or _now_iso(),
                "started_at": "",
                "finished_at": "",
                "last_error": "",
                "receipts": [],
                "agent": {},
                "proposal": {},
                "proposal_digest": "",
                "receipts_digest": "",
                "execution_digest_version": 0,
                "execution_digest": "",
                "attempt_history": history,
                "recovered_at": _now_iso(),
            })
            record["audit"] = audit_record(record)
            target = self._event_path("queued", path.stem)
            _atomic_json(path, record)
            try:
                os.replace(path, target)
                recovered += 1
            except OSError:
                continue
        return recovered

    def claim_next(self) -> tuple[CompanionEvent, dict[str, Any]] | None:
        self.recover_stale_running()
        paths = sorted(
            (self.root / "queued").glob("evt_*.json"),
            # Recovery rewrites a file and therefore changes mtime. Keep the
            # original queue order so an older event can never jump behind a
            # later event in the same lane after a worker crash.
            key=lambda path: (str(_read_json(path).get("queued_at") or ""), path.name),
        )
        for source in paths:
            target = self._event_path("running", source.stem)
            try:
                os.replace(source, target)
            except OSError:
                continue
            record = _read_json(target)
            event = CompanionEvent.from_dict(record.get("event") or {})
            record.update({
                "state": "running",
                "attempt": int(record.get("attempt") or 0) + 1,
                "started_at": _now_iso(),
                "last_error": "",
            })
            _atomic_json(target, record)
            return event, record
        return None

    def complete(
        self,
        event_id: str,
        *,
        receipts: list[dict[str, Any]],
        agent: dict[str, Any],
        proposal: dict[str, Any] | None = None,
    ) -> Path:
        source = self._event_path("running", event_id)
        record = _read_json(source)
        if not record:
            raise KeyError(event_id)
        proposal_value = proposal or {}
        proposal_digest = stable_digest(proposal_value, size=32)
        receipts_digest = stable_digest(receipts, size=32)
        record.update({
            "state": "done",
            "finished_at": _now_iso(),
            "receipts": receipts,
            "agent": agent,
            "proposal": proposal_value,
            "proposal_digest": proposal_digest,
            "receipts_digest": receipts_digest,
            "execution_digest_version": 2,
            "last_error": "",
        })
        record["execution_digest"] = execution_digest(record)
        record["audit"] = audit_record(record)
        _atomic_json(source, record)
        target = self._event_path("done", event_id)
        os.replace(source, target)
        return target

    def fail(
        self,
        event_id: str,
        error: str,
        *,
        agent: dict[str, Any] | None = None,
        receipts: list[dict[str, Any]] | None = None,
        proposal: dict[str, Any] | None = None,
    ) -> Path:
        source = self._event_path("running", event_id)
        record = _read_json(source)
        if not record:
            raise KeyError(event_id)
        receipt_values = receipts or []
        proposal_value = proposal or {}
        record.update({
            "state": "failed",
            "finished_at": _now_iso(),
            "last_error": str(error)[:4000],
            "agent": agent or {},
            "receipts": receipt_values,
            "proposal": proposal_value,
            "proposal_digest": stable_digest(proposal_value, size=32),
            "receipts_digest": stable_digest(receipt_values, size=32),
            "execution_digest_version": 2,
        })
        record["execution_digest"] = execution_digest(record)
        record["audit"] = audit_record(record)
        _atomic_json(source, record)
        target = self._event_path("failed", event_id)
        os.replace(source, target)
        return target

    def retry(self, event_id: str, *, reason: str = "operator retry") -> Path:
        found = self.find_event(event_id)
        if not found or found[0] != "failed":
            raise KeyError(event_id)
        _, source = found
        record = _read_json(source)
        audit = audit_record(record)
        if not audit.get("retry_allowed"):
            raise ValueError(
                "failed event execution evidence is unsealed or failed integrity verification; retry is forbidden"
            )
        history = _archive_attempt(record, transition="retry", reason=reason)
        record.update({
            "state": "queued",
            "queued_at": _now_iso(),
            "started_at": "",
            "finished_at": "",
            "last_error": "",
            "receipts": [],
            "agent": {},
            "proposal": {},
            "proposal_digest": "",
            "receipts_digest": "",
            "execution_digest_version": 0,
            "execution_digest": "",
            "attempt_history": history,
            "retry_reason": str(reason)[:1000],
        })
        record["audit"] = audit_record(record)
        _atomic_json(source, record)
        target = self._event_path("queued", event_id)
        os.replace(source, target)
        return target

    def replay_action_required(self, event_id: str, *, reason: str) -> Path:
        """Requeue a completed event only when deterministic audit requires action."""

        found = self.find_event(event_id)
        if not found or found[0] != "done":
            raise KeyError(event_id)
        _, source = found
        record = _read_json(source)
        audit = audit_record(record)
        if audit.get("status") != "action_required":
            raise ValueError(f"event audit status is {audit.get('status')}, not action_required")
        if not audit.get("replay_allowed"):
            raise ValueError("event execution evidence failed integrity verification; replay is forbidden")
        history = _archive_attempt(
            record,
            transition="replay",
            reason=str(reason or "operator remediation"),
        )
        record.update({
            "state": "queued",
            "queued_at": _now_iso(),
            "started_at": "",
            "finished_at": "",
            "receipts": [],
            "agent": {},
            "proposal": {},
            "proposal_digest": "",
            "receipts_digest": "",
            "execution_digest_version": 0,
            "execution_digest": "",
            "attempt_history": history,
            "last_error": "",
            "replay_reason": str(reason or "operator remediation")[:1000],
        })
        record["audit"] = audit_record(record)
        _atomic_json(source, record)
        target = self._event_path("queued", event_id)
        os.replace(source, target)
        return target

    def _identity_path(self, directory: str, key: str) -> Path:
        return self.root / directory / f"{stable_digest(key, size=32)}.json"

    def capture_user_turn(self, trace_id: str, text: str) -> dict[str, Any]:
        if not trace_id or not text.strip():
            return {}
        path = self._identity_path("turns", trace_id)
        current = _read_json(path)
        now = _now_iso()
        value = {
            "trace_id": trace_id,
            "first_user_input": current.get("first_user_input") or text.strip(),
            "latest_user_input": text.strip(),
            "turn_index": int(current.get("turn_index") or 0) + 1,
            "updated_at": now,
        }
        _atomic_json(path, value)
        return value

    def user_turn(self, trace_id: str) -> dict[str, Any]:
        return _read_json(self._identity_path("turns", trace_id))

    def lane_state(self, lane_key: str) -> dict[str, Any]:
        return _read_json(self._identity_path("lanes", lane_key))

    def save_lane_state(self, lane_key: str, patch: dict[str, Any]) -> dict[str, Any]:
        path = self._identity_path("lanes", lane_key)
        value = self.lane_state(lane_key)
        value.update(patch)
        value["lane_key"] = lane_key
        value["updated_at"] = _now_iso()
        _atomic_json(path, value)
        return value

    @contextmanager
    def lock(self, key: str, *, stale_after_s: float = 3600.0) -> Iterator[None]:
        """Acquire one cross-process directory lock without blocking hooks."""

        lock_dir = self.root / "locks" / stable_digest(key, size=32)
        try:
            lock_dir.mkdir()
        except FileExistsError:
            owner = _read_json(lock_dir / "owner.json")
            try:
                owner_pid = int(owner.get("pid") or 0)
            except (TypeError, ValueError):
                owner_pid = 0
            if owner_pid and _pid_alive(owner_pid):
                raise LockBusy(key)
            try:
                age = time.time() - lock_dir.stat().st_mtime
            except OSError:
                age = 0.0
            if not owner_pid and age <= stale_after_s:
                raise LockBusy(key)
            shutil.rmtree(lock_dir, ignore_errors=True)
            try:
                lock_dir.mkdir()
            except FileExistsError as exc:
                raise LockBusy(key) from exc
        try:
            _atomic_json(lock_dir / "owner.json", {"pid": os.getpid(), "acquired_at": _now_iso(), "key": key})
            yield
        finally:
            shutil.rmtree(lock_dir, ignore_errors=True)


__all__ = ["CompanionStore", "LockBusy", "default_store_root"]
