# [OMNI] origin=codex created_by=codex intent="persist and reconcile at-most-once Arknights input intents"
"""Durable at-most-once intent primitives for the live Arknights facility.

The store commits an effect marker *before* the device callback is invoked.
That makes retries safe, but it cannot prove exactly-once external effects: a
process can crash after the commit and before the device receives the input.
Such records remain ``input_sent_unverified`` and require state reconciliation;
they are never blindly replayed.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from dataclasses import dataclass
from omnicompany.core.pycompat import StrEnum
from pathlib import Path
from typing import Any, Callable, Mapping


class IntentConflictError(RuntimeError):
    """An intent id was reused for a different semantic request."""


class IntentTransitionError(RuntimeError):
    """A caller requested an impossible or unsafe status transition."""


class IntentStatus(StrEnum):
    REQUESTED = "requested"
    REJECTED_BEFORE_INPUT = "rejected_before_input"
    FAILED_BEFORE_INPUT = "failed_before_input"
    INPUT_SENT_UNVERIFIED = "input_sent_unverified"
    VERIFIED_SUCCESS = "verified_success"
    SAFETY_HOLD = "safety_hold"


class SnapshotValidity(StrEnum):
    VERIFIED = "verified"
    UNAVAILABLE = "unavailable"
    CONFLICTING = "conflicting"
    REJECTED = "rejected"


class StepEffectStatus(StrEnum):
    RESERVED = "reserved"
    INPUT_SENT_UNVERIFIED = "input_sent_unverified"
    VERIFIED_SUCCESS = "verified_success"
    VERIFIED_NO_EFFECT = "verified_no_effect"
    DECISION_REQUIRED = "decision_required"


@dataclass(frozen=True, slots=True)
class StepEffectRecord:
    """Durable per-plan-step external-effect journal entry."""

    intent_id: str
    step_id: str
    request_hash: str
    status: StepEffectStatus
    detail: str
    created_at_ns: int
    updated_at_ns: int


@dataclass(frozen=True, slots=True)
class StepEffectReservation:
    record: StepEffectRecord
    invoke_now: bool


@dataclass(frozen=True, slots=True)
class BattleControlSnapshot:
    """A compact, provenance-carrying battle-control observation."""

    loaded: bool | None
    paused: bool | None
    state_version: int
    captured_monotonic: float
    source: str
    validity: SnapshotValidity = SnapshotValidity.VERIFIED

    def safety_reason(
        self,
        *,
        now_monotonic: float,
        max_age_seconds: float,
        requested_state_version: int,
    ) -> str | None:
        if not math.isfinite(now_monotonic) or not math.isfinite(
            self.captured_monotonic
        ):
            return "snapshot time is non-finite"
        if (
            isinstance(self.state_version, bool)
            or not isinstance(self.state_version, int)
            or self.state_version < 0
        ):
            return "snapshot state version is not a non-negative integer"
        if (
            isinstance(requested_state_version, bool)
            or not isinstance(requested_state_version, int)
            or requested_state_version < 0
        ):
            return "requested state version is not a non-negative integer"
        if not math.isfinite(max_age_seconds) or max_age_seconds < 0:
            return "snapshot freshness bound is invalid"
        if self.validity is not SnapshotValidity.VERIFIED:
            return f"snapshot validity is {self.validity.value}"
        age = now_monotonic - self.captured_monotonic
        if age < 0 or age > max_age_seconds:
            return f"snapshot is stale or future-dated (age={age:.3f}s)"
        if self.state_version < requested_state_version:
            return (
                f"snapshot version {self.state_version} precedes requested "
                f"version {requested_state_version}"
            )
        if self.loaded is None or self.paused is None:
            return "snapshot lacks loaded/paused proof"
        if self.paused and not self.loaded:
            return "snapshot is internally conflicting: paused but not loaded"
        if not self.source.strip():
            return "snapshot has no provenance source"
        return None


@dataclass(frozen=True, slots=True)
class IntentRecord:
    intent_id: str
    kind: str
    request_hash: str
    requested_state_version: int
    status: IntentStatus
    detail: str
    primary_input_sent: bool
    pause_input_sent: bool
    created_at_ns: int
    updated_at_ns: int


@dataclass(frozen=True, slots=True)
class IntentEvent:
    intent_id: str
    kind: str
    created_at_ns: int


_FINAL_STATUSES = {
    IntentStatus.REJECTED_BEFORE_INPUT,
    IntentStatus.FAILED_BEFORE_INPUT,
    IntentStatus.VERIFIED_SUCCESS,
}

_ALLOWED_TRANSITIONS: dict[IntentStatus, set[IntentStatus]] = {
    IntentStatus.REQUESTED: {
        IntentStatus.REJECTED_BEFORE_INPUT,
        IntentStatus.FAILED_BEFORE_INPUT,
        IntentStatus.INPUT_SENT_UNVERIFIED,
        IntentStatus.VERIFIED_SUCCESS,
        IntentStatus.SAFETY_HOLD,
    },
    IntentStatus.INPUT_SENT_UNVERIFIED: {
        IntentStatus.INPUT_SENT_UNVERIFIED,
        IntentStatus.VERIFIED_SUCCESS,
        IntentStatus.SAFETY_HOLD,
    },
    IntentStatus.SAFETY_HOLD: {
        IntentStatus.INPUT_SENT_UNVERIFIED,
        IntentStatus.VERIFIED_SUCCESS,
        IntentStatus.SAFETY_HOLD,
    },
    IntentStatus.REJECTED_BEFORE_INPUT: set(),
    IntentStatus.FAILED_BEFORE_INPUT: set(),
    IntentStatus.VERIFIED_SUCCESS: set(),
}


def _canonical_request(
    kind: str, payload: Mapping[str, Any], requested_state_version: int
) -> str:
    body = json.dumps(
        {
            "kind": kind,
            "payload": payload,
            "requested_state_version": requested_state_version,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


class IntentStore:
    """Small SQLite journal whose transactions precede external input."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS intents (
                    intent_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    requested_state_version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    primary_input_sent INTEGER NOT NULL DEFAULT 0,
                    pause_input_sent INTEGER NOT NULL DEFAULT 0,
                    created_at_ns INTEGER NOT NULL,
                    updated_at_ns INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS step_effects (
                    intent_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at_ns INTEGER NOT NULL,
                    updated_at_ns INTEGER NOT NULL,
                    PRIMARY KEY (intent_id, step_id),
                    FOREIGN KEY (intent_id) REFERENCES intents(intent_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS intent_events (
                    intent_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    created_at_ns INTEGER NOT NULL,
                    PRIMARY KEY (intent_id, kind),
                    FOREIGN KEY (intent_id) REFERENCES intents(intent_id)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _record(row: sqlite3.Row) -> IntentRecord:
        return IntentRecord(
            intent_id=str(row["intent_id"]),
            kind=str(row["kind"]),
            request_hash=str(row["request_hash"]),
            requested_state_version=int(row["requested_state_version"]),
            status=IntentStatus(str(row["status"])),
            detail=str(row["detail"]),
            primary_input_sent=bool(row["primary_input_sent"]),
            pause_input_sent=bool(row["pause_input_sent"]),
            created_at_ns=int(row["created_at_ns"]),
            updated_at_ns=int(row["updated_at_ns"]),
        )

    def get(self, intent_id: str) -> IntentRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
        return None if row is None else self._record(row)

    def begin(
        self,
        intent_id: str,
        *,
        kind: str,
        payload: Mapping[str, Any],
        requested_state_version: int,
    ) -> IntentRecord:
        if not intent_id.strip() or not kind.strip():
            raise ValueError("intent_id and kind must be non-empty")
        if (
            isinstance(requested_state_version, bool)
            or not isinstance(requested_state_version, int)
            or requested_state_version < 0
        ):
            raise ValueError("requested_state_version must be non-negative")
        request_hash = _canonical_request(kind, payload, requested_state_version)
        now = time.time_ns()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO intents (
                        intent_id, kind, request_hash, requested_state_version,
                        status, detail, created_at_ns, updated_at_ns
                    ) VALUES (?, ?, ?, ?, ?, '', ?, ?)
                    """,
                    (
                        intent_id,
                        kind,
                        request_hash,
                        requested_state_version,
                        IntentStatus.REQUESTED.value,
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM intents WHERE intent_id=?", (intent_id,)
                ).fetchone()
            elif str(row["request_hash"]) != request_hash:
                raise IntentConflictError(
                    f"intent {intent_id!r} already belongs to another request"
                )
        assert row is not None
        return self._record(row)

    def transition(
        self,
        intent_id: str,
        status: IntentStatus,
        *,
        detail: str = "",
        verified_snapshot: BattleControlSnapshot | None = None,
        now_monotonic: float | None = None,
        max_age_seconds: float = 0.5,
    ) -> IntentRecord:
        now = time.time_ns()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
            if row is None:
                raise KeyError(intent_id)
            current = IntentStatus(str(row["status"]))
            if status == current:
                return self._record(row)
            if status is IntentStatus.INPUT_SENT_UNVERIFIED and not (
                bool(row["primary_input_sent"]) or bool(row["pause_input_sent"])
            ):
                raise IntentTransitionError(
                    "input_sent_unverified requires a durable effect marker"
                )
            if status is IntentStatus.VERIFIED_SUCCESS:
                if verified_snapshot is None or now_monotonic is None:
                    raise IntentTransitionError(
                        "verified_success requires a fresh battle snapshot"
                    )
                unsafe = verified_snapshot.safety_reason(
                    now_monotonic=now_monotonic,
                    max_age_seconds=max_age_seconds,
                    requested_state_version=int(row["requested_state_version"]),
                )
                if unsafe is not None or not verified_snapshot.loaded or not verified_snapshot.paused:
                    raise IntentTransitionError(
                        "verified_success lacks fresh loaded+paused proof"
                        + (f": {unsafe}" if unsafe else "")
                    )
                if str(row["kind"]) == "battle.start-safe" and not bool(
                    row["primary_input_sent"]
                ):
                    raise IntentTransitionError(
                        "battle.start-safe cannot succeed before start input is reserved"
                    )
            if current in _FINAL_STATUSES or status not in _ALLOWED_TRANSITIONS[current]:
                raise IntentTransitionError(
                    f"unsafe intent transition {current.value} -> {status.value}"
                )
            connection.execute(
                "UPDATE intents SET status=?, detail=?, updated_at_ns=? WHERE intent_id=?",
                (status.value, detail, now, intent_id),
            )
            if (
                status is IntentStatus.VERIFIED_SUCCESS
                and str(row["kind"]) == "battle.start-safe"
            ):
                connection.execute(
                    "INSERT OR IGNORE INTO intent_events "
                    "(intent_id, kind, created_at_ns) VALUES (?, ?, ?)",
                    (intent_id, "memory_warmup_requested", now),
                )
            updated = connection.execute(
                "SELECT * FROM intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
        assert updated is not None
        return self._record(updated)

    def mark_effect_sent(self, intent_id: str, effect: str) -> tuple[IntentRecord, bool]:
        """Durably reserve one external effect; ``True`` means invoke it now."""

        column = {
            "primary": "primary_input_sent",
            "pause": "pause_input_sent",
        }.get(effect)
        if column is None:
            raise ValueError("effect must be 'primary' or 'pause'")
        now = time.time_ns()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
            if row is None:
                raise KeyError(intent_id)
            current = IntentStatus(str(row["status"]))
            if current in _FINAL_STATUSES:
                return self._record(row), False
            if effect == "pause" and not bool(row["primary_input_sent"]):
                raise IntentTransitionError(
                    "secondary pause input cannot precede primary/start input"
                )
            if bool(row[column]):
                return self._record(row), False
            connection.execute(
                f"UPDATE intents SET {column}=1, status=?, detail=?, updated_at_ns=? "
                "WHERE intent_id=?",
                (
                    IntentStatus.INPUT_SENT_UNVERIFIED.value,
                    f"{effect} input durably reserved; awaiting state proof",
                    now,
                    intent_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
        assert updated is not None
        return self._record(updated), True

    @staticmethod
    def _step_record(row: sqlite3.Row) -> StepEffectRecord:
        return StepEffectRecord(
            intent_id=str(row["intent_id"]), step_id=str(row["step_id"]),
            request_hash=str(row["request_hash"]),
            status=StepEffectStatus(str(row["status"])), detail=str(row["detail"]),
            created_at_ns=int(row["created_at_ns"]), updated_at_ns=int(row["updated_at_ns"]),
        )

    def step_effect(self, intent_id: str, step_id: str) -> StepEffectRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM step_effects WHERE intent_id=? AND step_id=?",
                (intent_id, step_id),
            ).fetchone()
        return None if row is None else self._step_record(row)

    def reserve_step_effect(
        self, intent_id: str, step_id: str, *, request_hash: str, detail: str = ""
    ) -> StepEffectReservation:
        """Commit a per-step reservation before the caller invokes external input."""
        if not intent_id.strip() or not step_id.strip() or not request_hash.strip():
            raise ValueError("intent_id, step_id, and request_hash must be non-empty")
        now = time.time_ns()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM intents WHERE intent_id=?", (intent_id,)).fetchone() is None:
                raise KeyError(intent_id)
            row = connection.execute(
                "SELECT * FROM step_effects WHERE intent_id=? AND step_id=?", (intent_id, step_id)
            ).fetchone()
            if row is not None:
                if str(row["request_hash"]) != request_hash:
                    raise IntentConflictError(f"step {step_id!r} already belongs to another request")
                return StepEffectReservation(self._step_record(row), False)
            connection.execute(
                "INSERT INTO step_effects VALUES (?, ?, ?, ?, ?, ?, ?)",
                (intent_id, step_id, request_hash, StepEffectStatus.RESERVED.value, detail, now, now),
            )
            row = connection.execute(
                "SELECT * FROM step_effects WHERE intent_id=? AND step_id=?", (intent_id, step_id)
            ).fetchone()
        assert row is not None
        return StepEffectReservation(self._step_record(row), True)

    def reconcile_step_effect(
        self, intent_id: str, step_id: str, *, proof: str, effect_observed: bool | None
    ) -> StepEffectRecord:
        """Apply explicit proof; no proof means the effect remains unverified."""
        if not proof.strip():
            raise ValueError("explicit proof is required")
        current = self.step_effect(intent_id, step_id)
        if current is None:
            raise KeyError((intent_id, step_id))
        if effect_observed is None and current.status in {
            StepEffectStatus.VERIFIED_SUCCESS,
            StepEffectStatus.VERIFIED_NO_EFFECT,
            StepEffectStatus.DECISION_REQUIRED,
        }:
            return current
        if effect_observed is True:
            status = StepEffectStatus.VERIFIED_SUCCESS
        elif effect_observed is False:
            status = StepEffectStatus.VERIFIED_NO_EFFECT
        else:
            status = StepEffectStatus.INPUT_SENT_UNVERIFIED
        now = time.time_ns()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE step_effects SET status=?, detail=?, updated_at_ns=? WHERE intent_id=? AND step_id=?",
                (status.value, proof, now, intent_id, step_id),
            )
            row = connection.execute(
                "SELECT * FROM step_effects WHERE intent_id=? AND step_id=?", (intent_id, step_id)
            ).fetchone()
        assert row is not None
        return self._step_record(row)

    def mark_step_input_sent_unverified(
        self, intent_id: str, step_id: str, *, detail: str
    ) -> StepEffectRecord:
        """Persist that the reserved input was invoked but its effect is not proved."""

        if not detail.strip():
            raise ValueError("input evidence detail must be non-empty")
        now = time.time_ns()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM step_effects WHERE intent_id=? AND step_id=?",
                (intent_id, step_id),
            ).fetchone()
            if row is None:
                raise KeyError((intent_id, step_id))
            current = StepEffectStatus(str(row["status"]))
            if current is StepEffectStatus.RESERVED:
                connection.execute(
                    "UPDATE step_effects SET status=?, detail=?, updated_at_ns=? "
                    "WHERE intent_id=? AND step_id=?",
                    (
                        StepEffectStatus.INPUT_SENT_UNVERIFIED.value,
                        detail,
                        now,
                        intent_id,
                        step_id,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM step_effects WHERE intent_id=? AND step_id=?",
                    (intent_id, step_id),
                ).fetchone()
        assert row is not None
        return self._step_record(row)

    def emit_event(self, intent_id: str, kind: str) -> IntentEvent:
        if not kind.strip():
            raise ValueError("event kind must be non-empty")
        created_at_ns = time.time_ns()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM intents WHERE intent_id=?", (intent_id,)
            ).fetchone() is None:
                raise KeyError(intent_id)
            connection.execute(
                "INSERT OR IGNORE INTO intent_events "
                "(intent_id, kind, created_at_ns) VALUES (?, ?, ?)",
                (intent_id, kind, created_at_ns),
            )
            row = connection.execute(
                "SELECT intent_id, kind, created_at_ns FROM intent_events "
                "WHERE intent_id=? AND kind=?",
                (intent_id, kind),
            ).fetchone()
        assert row is not None
        return IntentEvent(
            intent_id=str(row["intent_id"]),
            kind=str(row["kind"]),
            created_at_ns=int(row["created_at_ns"]),
        )

    def reserve_event_once(self, intent_id: str, kind: str) -> bool:
        """Durably reserve a named recovery effect exactly once.

        The caller invokes the external effect only when this returns true.
        This extends the same commit-before-input contract used by the primary
        and pause markers to a small, explicitly named bounded retry set.
        """

        if not kind.strip():
            raise ValueError("event kind must be non-empty")
        created_at_ns = time.time_ns()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM intents WHERE intent_id=?", (intent_id,)
            ).fetchone() is None:
                raise KeyError(intent_id)
            cursor = connection.execute(
                "INSERT OR IGNORE INTO intent_events "
                "(intent_id, kind, created_at_ns) VALUES (?, ?, ?)",
                (intent_id, kind, created_at_ns),
            )
        return cursor.rowcount == 1

    def events(self, intent_id: str) -> tuple[IntentEvent, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT intent_id, kind, created_at_ns FROM intent_events "
                "WHERE intent_id=? ORDER BY created_at_ns, kind",
                (intent_id,),
            ).fetchall()
        return tuple(
            IntentEvent(
                intent_id=str(row["intent_id"]),
                kind=str(row["kind"]),
                created_at_ns=int(row["created_at_ns"]),
            )
            for row in rows
        )

    def tail_events(self, limit: int = 20) -> tuple[IntentEvent, ...]:
        if not 1 <= limit <= 200:
            raise ValueError("event tail limit must be between 1 and 200")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT intent_id, kind, created_at_ns FROM intent_events "
                "ORDER BY created_at_ns DESC, intent_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(
            IntentEvent(
                intent_id=str(row["intent_id"]),
                kind=str(row["kind"]),
                created_at_ns=int(row["created_at_ns"]),
            )
            for row in rows
        )

    def reconcile_success_events(self, intent_id: str) -> IntentRecord:
        """Repair idempotent completion events for journals from an older/crashed writer."""

        created_at_ns = time.time_ns()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
            if row is None:
                raise KeyError(intent_id)
            if (
                IntentStatus(str(row["status"])) is IntentStatus.VERIFIED_SUCCESS
                and str(row["kind"]) == "battle.start-safe"
            ):
                connection.execute(
                    "INSERT OR IGNORE INTO intent_events "
                    "(intent_id, kind, created_at_ns) VALUES (?, ?, ?)",
                    (intent_id, "memory_warmup_requested", created_at_ns),
                )
        return self._record(row)


def ensure_paused(
    store: IntentStore,
    *,
    intent_id: str,
    requested_state_version: int,
    snapshot: BattleControlSnapshot,
    pause_input: Callable[[], None],
    now_monotonic: float,
    max_age_seconds: float = 0.5,
) -> IntentRecord:
    """Ensure pause from fresh proof without ever retrying an unknown input."""

    if not math.isfinite(max_age_seconds) or max_age_seconds < 0:
        raise ValueError("max_age_seconds must be finite and non-negative")

    record = store.begin(
        intent_id,
        kind="battle.ensure-paused",
        payload={"max_age_seconds": max_age_seconds},
        requested_state_version=requested_state_version,
    )
    if record.status in _FINAL_STATUSES:
        return record
    unsafe = snapshot.safety_reason(
        now_monotonic=now_monotonic,
        max_age_seconds=max_age_seconds,
        requested_state_version=record.requested_state_version,
    )
    if unsafe is not None:
        return store.transition(intent_id, IntentStatus.SAFETY_HOLD, detail=unsafe)
    if snapshot.paused:
        return store.transition(
            intent_id,
            IntentStatus.VERIFIED_SUCCESS,
            detail=f"paused verified by {snapshot.source}",
            verified_snapshot=snapshot,
            now_monotonic=now_monotonic,
            max_age_seconds=max_age_seconds,
        )
    if not snapshot.loaded:
        if record.primary_input_sent:
            return store.transition(
                intent_id,
                IntentStatus.SAFETY_HOLD,
                detail="input was reserved but battle is no longer loaded",
            )
        return store.transition(
            intent_id,
            IntentStatus.REJECTED_BEFORE_INPUT,
            detail="fresh state proves no battle is loaded",
        )
    if record.primary_input_sent:
        return store.transition(
            intent_id,
            IntentStatus.SAFETY_HOLD,
            detail="pause input was already reserved but running state persists",
        )
    record, invoke = store.mark_effect_sent(intent_id, "primary")
    if invoke:
        pause_input()
    return record


class SafeBattleStart:
    """Step-driven start/pause controller with no sleeping or device knowledge."""

    def __init__(
        self,
        store: IntentStore,
        *,
        intent_id: str,
        requested_state_version: int,
        max_age_seconds: float = 0.5,
        request_payload: Mapping[str, Any] | None = None,
    ) -> None:
        if not math.isfinite(max_age_seconds) or max_age_seconds < 0:
            raise ValueError("max_age_seconds must be finite and non-negative")
        self.store = store
        self.intent_id = intent_id
        self.requested_state_version = requested_state_version
        self.max_age_seconds = max_age_seconds
        self.request_payload = dict(request_payload or {})
        if "max_age_seconds" in self.request_payload:
            raise ValueError("request_payload cannot override max_age_seconds")
        self.store.begin(
            intent_id,
            kind="battle.start-safe",
            payload={
                **self.request_payload,
                "max_age_seconds": max_age_seconds,
            },
            requested_state_version=requested_state_version,
        )

    def start(self, start_input: Callable[[], None]) -> IntentRecord:
        record, invoke = self.store.mark_effect_sent(self.intent_id, "primary")
        if invoke:
            start_input()
        return record

    def feed(
        self,
        snapshot: BattleControlSnapshot,
        *,
        pause_input: Callable[[], None],
        now_monotonic: float,
    ) -> IntentRecord:
        record = self.store.get(self.intent_id)
        if record is None:
            raise KeyError(self.intent_id)
        if record.status in _FINAL_STATUSES:
            if record.status is IntentStatus.VERIFIED_SUCCESS:
                return self.store.reconcile_success_events(self.intent_id)
            return record
        if not record.primary_input_sent:
            raise IntentTransitionError("battle snapshots cannot precede start input")
        unsafe = snapshot.safety_reason(
            now_monotonic=now_monotonic,
            max_age_seconds=self.max_age_seconds,
            requested_state_version=self.requested_state_version,
        )
        if unsafe is not None:
            return self.store.transition(
                self.intent_id, IntentStatus.SAFETY_HOLD, detail=unsafe
            )
        if snapshot.paused:
            verified = self.store.transition(
                self.intent_id,
                IntentStatus.VERIFIED_SUCCESS,
                detail=f"paused_verified by {snapshot.source}",
                verified_snapshot=snapshot,
                now_monotonic=now_monotonic,
                max_age_seconds=self.max_age_seconds,
            )
            return verified
        if not snapshot.loaded:
            return self.store.transition(
                self.intent_id,
                IntentStatus.INPUT_SENT_UNVERIFIED,
                detail="start input reserved; waiting for loaded battle proof",
            )
        if record.pause_input_sent:
            return self.store.transition(
                self.intent_id,
                IntentStatus.SAFETY_HOLD,
                detail="pause input reserved but fresh running state persists",
            )
        record, invoke = self.store.mark_effect_sent(self.intent_id, "pause")
        if invoke:
            pause_input()
        return record
