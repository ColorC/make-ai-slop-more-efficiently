"""Small local state store shared by lightweight Arknights components.

This module deliberately provides no signatures, rollback anchors, dual audit
sinks, durable outbox, or deployment attestation. One-account serial execution
only needs atomic local read/modify/write semantics.
"""

from __future__ import annotations

import copy
import json
import sqlite3
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

__all__ = [
    "AtomicStateStore",
    "InMemoryAtomicStateStore",
    "LocalStateBindingV1",
    "PersistentStateError",
    "SQLiteAtomicStateStore",
    "require_local_state_store",
]


class PersistentStateError(RuntimeError):
    """A local state transaction or binding is invalid."""


@dataclass(frozen=True, slots=True)
class LocalStateBindingV1:
    """Plain local namespace for one lightweight tournament and run."""

    tournament_id: str
    run_scope: str

    def validate(self) -> None:
        if not self.tournament_id or len(self.tournament_id) > 256:
            raise PersistentStateError("local tournament id is required")
        if not self.run_scope or len(self.run_scope) > 256:
            raise PersistentStateError("local run scope is required")


StateMutator = Callable[[dict[str, Any] | None], Mapping[str, Any]]


class AtomicStateStore(Protocol):
    durable: bool
    fixture_only: bool

    def read(self, namespace: str, binding: LocalStateBindingV1) -> dict[str, Any] | None: ...

    def transact(
        self,
        namespace: str,
        binding: LocalStateBindingV1,
        mutator: StateMutator,
    ) -> dict[str, Any]: ...


def _state_key(namespace: str, binding: LocalStateBindingV1) -> str:
    namespace = namespace.strip()
    if not namespace or len(namespace) > 256:
        raise PersistentStateError("state namespace is required")
    binding.validate()
    return json.dumps(
        [namespace, binding.tournament_id, binding.run_scope],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _payload(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
        decoded = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PersistentStateError("state payload must be a finite JSON object") from exc
    if not isinstance(decoded, dict):
        raise PersistentStateError("state payload must be a JSON object")
    return decoded


def require_local_state_store(
    store: AtomicStateStore | None,
    *,
    persistent: bool,
    fixture_id: str,
) -> AtomicStateStore:
    """Select a simple local store; persistent callers only require SQLite."""

    selected: AtomicStateStore = store or InMemoryAtomicStateStore(fixture_id=fixture_id)
    if not callable(getattr(selected, "read", None)) or not callable(
        getattr(selected, "transact", None)
    ):
        raise PersistentStateError("local state store contract is incomplete")
    if persistent and (
        getattr(selected, "fixture_only", True) or not getattr(selected, "durable", False)
    ):
        raise PersistentStateError("persistent mode requires a local SQLite state store")
    return selected


class InMemoryAtomicStateStore:
    durable = False
    fixture_only = True

    def __init__(self, *, fixture_id: str) -> None:
        if not fixture_id.strip():
            raise PersistentStateError("memory state store needs an explicit id")
        self.fixture_id = fixture_id
        self._records: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def read(self, namespace: str, binding: LocalStateBindingV1) -> dict[str, Any] | None:
        with self._lock:
            current = self._records.get(_state_key(namespace, binding))
            return copy.deepcopy(current) if current is not None else None

    def transact(
        self,
        namespace: str,
        binding: LocalStateBindingV1,
        mutator: StateMutator,
    ) -> dict[str, Any]:
        with self._lock:
            key = _state_key(namespace, binding)
            current = copy.deepcopy(self._records.get(key))
            replacement = _payload(mutator(current))
            self._records[key] = replacement
            return copy.deepcopy(replacement)


class SQLiteAtomicStateStore:
    """A single-file local SQLite store with serialized transactions."""

    durable = True
    fixture_only = False

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS local_state "
                "(state_key TEXT PRIMARY KEY, payload_json TEXT NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def read(self, namespace: str, binding: LocalStateBindingV1) -> dict[str, Any] | None:
        key = _state_key(namespace, binding)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM local_state WHERE state_key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row[0])
        except (TypeError, json.JSONDecodeError) as exc:
            raise PersistentStateError("local SQLite state is corrupt") from exc
        if not isinstance(value, dict):
            raise PersistentStateError("local SQLite state is not an object")
        return value

    def transact(
        self,
        namespace: str,
        binding: LocalStateBindingV1,
        mutator: StateMutator,
    ) -> dict[str, Any]:
        key = _state_key(namespace, binding)
        with self._lock, self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT payload_json FROM local_state WHERE state_key = ?", (key,)
                ).fetchone()
                current = json.loads(row[0]) if row is not None else None
                if current is not None and not isinstance(current, dict):
                    raise PersistentStateError("local SQLite state is not an object")
                replacement = _payload(mutator(copy.deepcopy(current)))
                connection.execute(
                    "INSERT INTO local_state(state_key, payload_json) VALUES(?, ?) "
                    "ON CONFLICT(state_key) DO UPDATE SET payload_json=excluded.payload_json",
                    (
                        key,
                        json.dumps(
                            replacement,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return copy.deepcopy(replacement)
