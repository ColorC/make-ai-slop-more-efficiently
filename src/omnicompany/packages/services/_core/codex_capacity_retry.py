# [OMNI] origin=codex created_by=codex intent="Codex 容量错误的持久低频原地重试状态机"
# [OMNI] domain=core type=module status=active tags=codex,capacity,retry
"""Durable, fail-closed retry state machine for Codex capacity failures.

The service intentionally knows nothing about a terminal implementation.  The
daemon supplies an adapter which re-validates the live native handle and sends
through the normal PTY/chat/LiTeWork input path.  This keeps quoted transcript
text out of the trigger path and makes the timing/state machine independently
testable.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol

from omnicompany.packages.services._core.codex_capacity_migration import (
    MIGRATION_STATE_DONE,
    MIGRATION_STATE_EXHAUSTED,
    MIGRATION_STATE_INHERITED,
    MIGRATION_STATE_NONE,
    MIGRATION_STATE_RUNNING,
    MAX_MIGRATION_FAILURES,
    evaluate_migration,
)


CAPACITY_ERROR = "Selected model is at capacity. Please try a different model."
RETRY_INTERVAL_SECONDS = 900.0
CONTINUATION_PROMPT = "continue"
SCHEMA = "omnicompany.codex-capacity-retry.v1"


def _text(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").strip()


def _provider_is_codex(value: Any) -> bool:
    return _text(value).lower().replace("-", "_").startswith("codex")


def is_capacity_error_event(event: Mapping[str, Any]) -> bool:
    """Accept only a provider-owned, current error envelope.

    ``provider_owned`` is deliberately mandatory.  A raw transcript, test
    fixture, quoted prompt, or UI ring buffer can contain the same words but
    cannot satisfy this envelope.
    """

    if event.get("provider_owned") is not True:
        return False
    if _text(event.get("kind")) not in {"error", "provider_error"}:
        return False
    if not _provider_is_codex(event.get("provider")):
        return False
    if not _text(event.get("session_id") or event.get("sessionId")):
        return False
    message = _text(event.get("error") or event.get("message") or event.get("content"))
    return message == CAPACITY_ERROR


def _digest_event(event: Mapping[str, Any]) -> str:
    stable = {
        "session_id": _text(event.get("session_id") or event.get("sessionId")),
        "provider_session_id": _text(event.get("provider_session_id") or event.get("providerSessionId")),
        "generation": _text(event.get("generation")),
        "sequence": event.get("sequence"),
        "event_id": _text(event.get("event_id") or event.get("id")),
        "observed_at": event.get("observed_at"),
        "message": CAPACITY_ERROR,
    }
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode("utf-8")).hexdigest()


def pty_capacity_error_in_chunk(chunk: str) -> bool:
    """Recognize only an exact provider error line in the current host chunk.

    This is a narrow helper for the authenticated PTY host reader.  It never
    scans a replay ring/tail, and deliberately does not match a sentence,
    Markdown quote, or a line embedded in a larger output record.
    """

    for line in str(chunk or "").replace("\r\n", "\n").split("\n"):
        plain = line.strip()
        # ANSI SGR is the only decoration Codex uses around this error line.
        while "\x1b[" in plain:
            start = plain.find("\x1b[")
            end = start + 2
            while end < len(plain) and not plain[end].isalpha():
                end += 1
            if end < len(plain):
                plain = plain[:start] + plain[end + 1:]
            else:
                break
        if plain == CAPACITY_ERROR:
            return True
    return False


@dataclass(frozen=True)
class SessionSnapshot:
    session_id: str
    provider: str
    provider_session_id: str
    generation: str
    runtime_host: str
    alive: bool
    generation_started_at: float = 0.0
    identity_known: bool = True
    permission_known: bool = True
    working: bool = False
    working_known: bool = False
    has_user_turn: bool = False
    human_paused: bool = False
    human_closed: bool = False
    goal_state: Mapping[str, Any] = field(default_factory=dict)
    goal_resumable: bool = False
    goal_known: bool = True
    input_at: float = 0.0
    pending_input: bool = False
    capacity_failure: Mapping[str, Any] | None = None
    # Migration inputs.  ``progress_known`` false means "we cannot tell", which
    # is never enough to spawn a successor session.
    last_provider_progress_at: float = 0.0
    progress_known: bool = False
    block_reason: str = ""


class RetryAdapter(Protocol):
    async def snapshots(self) -> list[SessionSnapshot]: ...

    async def send(self, snapshot: SessionSnapshot, prompt: str) -> bool: ...


class MigrationAdapter(Protocol):
    """Optional capability: perform the one-shot successor migration.

    The adapter owns every side effect (sealed handoff, claim release, spawning
    the new formal session).  It returns a mapping with ``ok`` plus the
    successor identity so the chain can be closed durably.
    """

    async def migrate(self, record: "RetryRecord", snapshot: SessionSnapshot) -> Mapping[str, Any]: ...


@dataclass
class RetryRecord:
    key: str
    session_id: str
    provider: str
    provider_session_id: str
    generation: str
    runtime_host: str
    first_seen_at: float
    next_due_at: float
    last_attempt_at: float | None = None
    attempts: int = 0
    state: str = "waiting"
    last_event_digest: str = ""
    last_prompt: str | None = None
    reason: str | None = None
    submission: str | None = None
    updated_at: float = 0.0
    # ── one-shot successor migration (see codex_capacity_migration) ──
    prev_attempt_at: float | None = None
    recovered_at: float | None = None
    chain_key: str = ""
    migration_state: str = MIGRATION_STATE_NONE
    migration_trigger: str = ""
    migration_reason: str = ""
    migration_failures: int = 0
    migrated_at: float | None = None
    successor_session_id: str = ""
    handoff_id: str = ""


class RetryStore:
    """Small atomic JSON store with a cross-process mkdir lock."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._thread_lock = threading.RLock()

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            value = {}
        return value if isinstance(value, dict) and isinstance(value.get("records", {}), dict) else {"records": {}}

    def _write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def _locked(self):
        class _Lock:
            def __init__(inner, store: RetryStore) -> None:
                inner.store = store
                inner.held = False

            def __enter__(inner):
                inner.store._thread_lock.acquire()
                inner.store.lock_path.parent.mkdir(parents=True, exist_ok=True)
                deadline = time.time() + 10
                while True:
                    try:
                        inner.store.lock_path.mkdir()
                        (inner.store.lock_path / "owner").write_text(
                            json.dumps({"pid": os.getpid(), "at": time.time()}), encoding="utf-8"
                        )
                        inner.held = True
                        return inner.store
                    except FileExistsError:
                        # A crashed process cannot leave a retry blocked forever.
                        try:
                            age = time.time() - inner.store.lock_path.stat().st_mtime
                        except OSError:
                            age = 0
                        if age > 30:
                            try:
                                (inner.store.lock_path / "owner").unlink(missing_ok=True)
                                inner.store.lock_path.rmdir()
                            except OSError:
                                pass
                        if time.time() >= deadline:
                            inner.store._thread_lock.release()
                            raise TimeoutError("codex capacity retry store lock timeout")
                        time.sleep(0.01)

            def __exit__(inner, *_args):
                if inner.held:
                    try:
                        (inner.store.lock_path / "owner").unlink(missing_ok=True)
                        inner.store.lock_path.rmdir()
                    except OSError:
                        pass
                inner.store._thread_lock.release()

        return _Lock(self)

    def list(self) -> list[RetryRecord]:
        with self._locked():
            return [RetryRecord(**row) for row in self._read().get("records", {}).values()]

    def atomic(self, mutator: Callable[[dict[str, Any]], Any]) -> Any:
        """Run one read/modify/write transaction under the process lock."""
        with self._locked():
            data = self._read()
            result = mutator(data)
            self._write(data)
            return result

    def claim_due(self, key: str, *, now: float, prompt: str) -> RetryRecord | None:
        """Atomically transition one due record to ``attempting``."""
        def mutate(data: dict[str, Any]) -> RetryRecord | None:
            row = data.setdefault("records", {}).get(key)
            if not isinstance(row, dict) or row.get("state") != "waiting":
                return None
            if float(row.get("next_due_at") or 0) > now:
                return None
            row["state"] = "attempting"
            row["prev_attempt_at"] = row.get("last_attempt_at")
            row["last_attempt_at"] = now
            row["next_due_at"] = now + RETRY_INTERVAL_SECONDS
            row["attempts"] = int(row.get("attempts") or 0) + 1
            row["last_prompt"] = prompt
            row["reason"] = None
            row["updated_at"] = now
            return RetryRecord(**row)

        return self.atomic(mutate)

    def upsert(self, record: RetryRecord) -> None:
        with self._locked():
            data = self._read()
            data.setdefault("schema", SCHEMA)
            data.setdefault("records", {})[record.key] = asdict(record)
            self._write(data)

    def record_submission(self, key: str, attempt_at: float | None, submission: str, reason: str | None = None) -> None:
        def mutate(data: dict[str, Any]) -> None:
            row = data.setdefault("records", {}).get(key)
            if not isinstance(row, dict) or row.get("last_attempt_at") != attempt_at:
                return
            row["submission"] = submission
            if row.get("state") == "attempting":
                row["reason"] = reason
        self.atomic(mutate)

    def claim_migration(self, key: str, *, now: float, trigger: str, reason: str) -> RetryRecord | None:
        """Atomically reserve the single migration of one failure chain.

        Persisting ``migrating`` before any side effect is deliberate: if the
        process dies mid-migration the chain stays reserved and no second
        session can be spawned.  A stuck reservation is visible in ``status``.
        """
        def mutate(data: dict[str, Any]) -> RetryRecord | None:
            row = data.setdefault("records", {}).get(key)
            if not isinstance(row, dict):
                return None
            if str(row.get("migration_state") or MIGRATION_STATE_NONE) != MIGRATION_STATE_NONE:
                return None
            row["migration_state"] = MIGRATION_STATE_RUNNING
            row["migration_trigger"] = trigger
            row["migration_reason"] = reason
            row["updated_at"] = now
            return RetryRecord(**row)

        return self.atomic(mutate)

    def finish_migration(
        self,
        key: str,
        *,
        now: float,
        ok: bool,
        successor_session_id: str = "",
        handoff_id: str = "",
        reason: str = "",
    ) -> RetryRecord | None:
        def mutate(data: dict[str, Any]) -> RetryRecord | None:
            row = data.setdefault("records", {}).get(key)
            if not isinstance(row, dict) or row.get("migration_state") != MIGRATION_STATE_RUNNING:
                return None
            if ok:
                row["migration_state"] = MIGRATION_STATE_DONE
                row["migrated_at"] = now
                row["successor_session_id"] = successor_session_id
                row["handoff_id"] = handoff_id
                row["state"] = "migrated"
                row["reason"] = reason or "migrated-to-successor"
            elif str(reason or "").startswith("unsupported-"):
                # A host that can never support migration is closed out once,
                # not retried as if it were a transient failure.
                row["migration_state"] = MIGRATION_STATE_EXHAUSTED
                row["migration_reason"] = reason
            else:
                failures = int(row.get("migration_failures") or 0) + 1
                row["migration_failures"] = failures
                row["migration_state"] = (
                    MIGRATION_STATE_EXHAUSTED if failures >= MAX_MIGRATION_FAILURES else MIGRATION_STATE_NONE
                )
                row["migration_reason"] = reason or "migration-failed"
            row["updated_at"] = now
            return RetryRecord(**row)

        return self.atomic(mutate)

    def remove(self, key: str) -> None:
        with self._locked():
            data = self._read()
            data.setdefault("records", {}).pop(key, None)
            self._write(data)


_MIGRATION_CARRY_FIELDS = (
    "migration_state", "migration_trigger", "migration_reason", "migration_failures",
    "migrated_at", "successor_session_id", "handoff_id", "chain_key",
)


def _inherited_migration(data: Mapping[str, Any], session_id: str, previous: Mapping[str, Any]) -> dict[str, Any]:
    """Carry a used migration forward so one lineage can never migrate twice."""
    carried = str(previous.get("migration_state") or MIGRATION_STATE_NONE)
    if carried != MIGRATION_STATE_NONE:
        return {field: previous.get(field) for field in _MIGRATION_CARRY_FIELDS if previous.get(field) is not None}
    if not session_id:
        return {}
    for row in (data.get("records") or {}).values():
        if not isinstance(row, dict) or str(row.get("successor_session_id") or "") != session_id:
            continue
        # This session exists only because that chain already migrated once.
        return {
            "migration_state": MIGRATION_STATE_INHERITED,
            "migration_reason": f"inherited-from:{row.get('key')}",
            "chain_key": str(row.get("chain_key") or row.get("key") or ""),
        }
    return {}


class CapacityRetryService:
    def __init__(self, store: RetryStore, adapter: RetryAdapter) -> None:
        self.store = store
        self.adapter = adapter
        self._session_locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, key: str) -> asyncio.Lock:
        return self._session_locks.setdefault(key, asyncio.Lock())

    async def observe(self, event: Mapping[str, Any], *, now: float | None = None) -> bool:
        """Record a fresh provider event; return whether it changed state."""

        now = time.time() if now is None else float(now)
        session_id = _text(event.get("session_id") or event.get("sessionId"))
        provider_session_id = _text(event.get("provider_session_id") or event.get("providerSessionId"))
        generation = _text(event.get("generation"))
        key = hashlib.sha256(
            f"{event.get('runtime_host')}|{session_id}|{provider_session_id}|{generation}".encode()
        ).hexdigest()[:32]
        if is_capacity_error_event(event):
            digest = _digest_event(event)
            def mutate(data: dict[str, Any]) -> bool:
                row = data.setdefault("records", {}).get(key)
                if isinstance(row, dict) and row.get("last_event_digest") == digest:
                    if event.get("recheck_goal") is True and row.get("reason") == "goal-not-resumable":
                        row["state"] = "waiting"
                        row["next_due_at"] = min(float(row.get("next_due_at") or now), now)
                        row["reason"] = None
                        row["updated_at"] = now
                        return True
                    return False
                first_seen_at = float(event.get("first_seen_at") or now)
                next_due_at = float(event.get("next_due_at") or (first_seen_at + RETRY_INTERVAL_SECONDS))
                if not isinstance(row, dict) or row.get("state") in {"cancelled", "succeeded"}:
                    previous = row if isinstance(row, dict) else {}
                    row = asdict(RetryRecord(
                        key=key,
                        session_id=session_id,
                        provider=_text(event.get("provider")),
                        provider_session_id=provider_session_id,
                        generation=generation,
                        runtime_host=_text(event.get("runtime_host")),
                        first_seen_at=first_seen_at,
                        next_due_at=next_due_at,
                        chain_key=key,
                    ))
                    # A reset row starts a new failure chain, but it can never
                    # regain a migration this lineage has already used.
                    row.update(_inherited_migration(data, session_id, previous))
                else:
                    row["next_due_at"] = max(next_due_at, float(row.get("next_due_at") or 0))
                row["state"] = "waiting"
                row["reason"] = None
                row["last_event_digest"] = digest
                row["updated_at"] = now
                data.setdefault("records", {})[key] = row
                return True

            return bool(self.store.atomic(mutate))

        if not event.get("provider_owned") or not _provider_is_codex(event.get("provider")):
            return False
        if _text(event.get("kind")) not in {
            "text", "tool_use", "tool_result", "complete", "progress", "status"
        }:
            return False

        def mutate(data: dict[str, Any]) -> bool:
            row = data.setdefault("records", {}).get(key)
            if not isinstance(row, dict) or row.get("state") != "attempting":
                return False
            row["state"] = "succeeded"
            row["reason"] = "provider-progress"
            row["recovered_at"] = now
            row["updated_at"] = now
            return True

        return bool(self.store.atomic(mutate))

    async def cancel_for_session(self, session_id: str, reason: str, *, now: float | None = None) -> int:
        now = time.time() if now is None else float(now)
        changed = 0
        for record in self.store.list():
            if record.session_id != session_id or record.state not in {"waiting", "attempting"}:
                continue
            record.state = "cancelled"
            record.reason = reason
            record.updated_at = now
            self.store.upsert(record)
            changed += 1
        return changed

    async def tick(self, *, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else float(now)
        reconcile = getattr(self.adapter, "reconcile_attempts", None)
        if reconcile is not None:
            await reconcile(self)
        seed_backlog = getattr(self.adapter, "seed_backlog", None)
        if seed_backlog is not None:
            await seed_backlog(self)
        snapshots = {item.session_id: item for item in await self.adapter.snapshots()}
        attempted = 0
        skipped: list[dict[str, str]] = []
        for record in self.store.list():
            if record.state != "waiting" or record.next_due_at > now:
                continue
            snapshot = snapshots.get(record.session_id)
            if snapshot is None:
                skipped.append({"session_id": record.session_id, "reason": "session-missing"})
                continue
            if not snapshot.identity_known or not snapshot.permission_known:
                skipped.append({"session_id": record.session_id, "reason": "identity-or-permission-unknown"})
                continue
            if snapshot.runtime_host == "omni-pty" and snapshot.capacity_failure is not None:
                if not snapshot.capacity_failure:
                    skipped.append({"session_id": record.session_id, "reason": "current-capacity-evidence-unavailable"})
                    continue
                if snapshot.pending_input or snapshot.input_at > float(snapshot.capacity_failure["failed_at"]):
                    record.state = "cancelled"
                    record.reason = "superseded-input"
                    record.updated_at = now
                    self.store.upsert(record)
                    continue
            if not snapshot.goal_known:
                record.next_due_at = now + RETRY_INTERVAL_SECONDS
                record.reason = "goal-unknown"
                record.updated_at = now
                self.store.upsert(record)
                skipped.append({"session_id": record.session_id, "reason": record.reason})
                continue
            if snapshot.generation != record.generation or snapshot.provider_session_id != record.provider_session_id:
                record.state = "cancelled"
                record.reason = "stale-session-generation"
                record.updated_at = now
                self.store.upsert(record)
                skipped.append({"session_id": record.session_id, "reason": record.reason})
                continue
            if not snapshot.alive or snapshot.human_closed or snapshot.human_paused:
                record.state = "cancelled"
                record.reason = "closed-or-paused"
                record.updated_at = now
                self.store.upsert(record)
                continue
            goal_status = _text(snapshot.goal_state.get("status")).lower()
            if goal_status in {"paused", "blocked", "usage_limited", "budget_limited", "complete", "completed", "cancelled"} and not snapshot.goal_resumable:
                record.state = "cancelled"
                record.reason = "goal-not-resumable"
                record.updated_at = now
                self.store.upsert(record)
                skipped.append({"session_id": record.session_id, "reason": record.reason})
                continue
            if goal_status == "active" and not snapshot.goal_resumable:
                record.state = "cancelled"
                record.reason = "goal-not-resumable"
                record.updated_at = now
                self.store.upsert(record)
                skipped.append({"session_id": record.session_id, "reason": record.reason})
                continue
            if not snapshot.working_known:
                record.next_due_at = now + RETRY_INTERVAL_SECONDS
                record.reason = "activity-unknown"
                record.updated_at = now
                self.store.upsert(record)
                skipped.append({"session_id": record.session_id, "reason": record.reason})
                continue
            if snapshot.working:
                record.next_due_at = now + RETRY_INTERVAL_SECONDS
                record.reason = "busy"
                record.updated_at = now
                self.store.upsert(record)
                skipped.append({"session_id": record.session_id, "reason": record.reason})
                continue
            lock = self._lock_for(record.key)
            if lock.locked():
                skipped.append({"session_id": record.session_id, "reason": "attempt-in-flight"})
                continue
            async with lock:
                prompt = "/goal resume" if snapshot.goal_resumable else CONTINUATION_PROMPT
                latest = self.store.claim_due(record.key, now=now, prompt=prompt)
                if latest is None:
                    skipped.append({"session_id": record.session_id, "reason": "attempt-in-flight"})
                    continue
                try:
                    sent = await self.adapter.send(snapshot, prompt)
                    if isinstance(sent, Mapping):
                        latest.submission = str(sent.get("submission") or "unconfirmed")
                        latest.reason = "submission-pending" if sent.get("pending") else None
                        self.store.record_submission(latest.key, latest.last_attempt_at, latest.submission, latest.reason)
                        sent = bool(sent.get("accepted") or sent.get("pending"))
                except Exception as exc:  # keep the schedule durable on transport errors
                    sent = False
                    latest.reason = f"send-failed:{type(exc).__name__}"
                if not sent:
                    latest.state = "waiting"
                    latest.reason = latest.reason or "send-rejected"
                    latest.updated_at = now
                    self.store.upsert(latest)
                attempted += 1
        migrations = await self._migration_pass(snapshots, now=now)
        return {"now": now, "attempted": attempted, "skipped": skipped,
                "migrations": migrations, "records": [asdict(r) for r in self.store.list()]}

    async def _migration_pass(self, snapshots: Mapping[str, SessionSnapshot], *, now: float) -> list[dict[str, Any]]:
        """Give one exhausted failure chain its single successor session.

        In-place retry stays the primary path; this only runs after the chain
        has proven, with current provider-owned evidence, that staying in place
        is not working.
        """
        migrate = getattr(self.adapter, "migrate", None)
        if migrate is None:
            return []
        results: list[dict[str, Any]] = []
        for record in self.store.list():
            snapshot = snapshots.get(record.session_id)
            decision = evaluate_migration(record, snapshot, now=now)
            if not decision.eligible:
                continue
            lock = self._lock_for(record.key)
            if lock.locked():
                continue
            async with lock:
                reserved = self.store.claim_migration(
                    record.key, now=now, trigger=decision.trigger, reason=decision.reason
                )
                if reserved is None:
                    # Another service/process won the same chain.
                    continue
                try:
                    outcome = await migrate(reserved, snapshot)
                    outcome = dict(outcome) if isinstance(outcome, Mapping) else {"ok": bool(outcome)}
                except Exception as exc:  # a broken spawn path must not lose the chain
                    outcome = {"ok": False, "reason": f"migrate-failed:{type(exc).__name__}"}
                final = self.store.finish_migration(
                    record.key,
                    now=now,
                    ok=bool(outcome.get("ok")),
                    successor_session_id=str(outcome.get("successor_session_id") or ""),
                    handoff_id=str(outcome.get("handoff_id") or ""),
                    reason=str(outcome.get("reason") or ""),
                )
                results.append({
                    "key": record.key,
                    "session_id": record.session_id,
                    "trigger": decision.trigger,
                    "decision": decision.reason,
                    "ok": bool(outcome.get("ok")),
                    "reason": outcome.get("reason") or "",
                    "handoff_id": outcome.get("handoff_id") or "",
                    "successor_session_id": outcome.get("successor_session_id") or "",
                    "migration_state": final.migration_state if final else "",
                })
        return results


def default_store_path(root: Path | None = None) -> Path:
    if root is None:
        from omnicompany.core.config import omni_workspace_root

        root = omni_workspace_root()
    return Path(root) / "data" / "_runtime" / "codex_capacity_retry" / "state.json"


__all__ = [
    "CAPACITY_ERROR",
    "CONTINUATION_PROMPT",
    "CapacityRetryService",
    "MigrationAdapter",
    "RetryAdapter",
    "RetryRecord",
    "RetryStore",
    "SessionSnapshot",
    "default_store_path",
    "is_capacity_error_event",
    "pty_capacity_error_in_chunk",
]
