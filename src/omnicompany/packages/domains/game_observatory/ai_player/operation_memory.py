"""Canonical operation memory and runtime telemetry authority.

Legacy transition edges and skill versions remain immutable evidence-bearing sources.
This module compiles their executable actions into one operation per exact fingerprint
and retains every historical identity as an alias projection.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..gateway import DeviceGateway
from ..models import NormalizedAction, SourcePixelRect, utc_now
from .contracts import EvidenceReferenceV1, SkillRunV1, SkillVersionV1, TransitionEdgeV1
from .store import AIPlayerStore


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def operation_fingerprint(environment_id: str, action: NormalizedAction) -> str:
    payload = {
        "environment_id": environment_id,
        "action": action.model_dump(mode="json"),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"{prefix}.{digest}"


def _elapsed_ms(started_at: str | None, ended_at: str | None) -> float:
    if not started_at or not ended_at:
        return 0.0
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        ended = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return max((ended - started).total_seconds() * 1000, 0.0)


class OperationRecordV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: str = Field(default="game-observatory.ai-player.operation.v1", alias="schema")
    id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    fingerprint: str = Field(min_length=64, max_length=64)
    action: NormalizedAction
    status: Literal["candidate", "verified", "quarantined", "retired"] = "candidate"
    visit_count: int = Field(default=0, ge=0)
    success_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    evidence_refs: tuple[EvidenceReferenceV1, ...] = ()
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_identity(self) -> "OperationRecordV1":
        if self.fingerprint != operation_fingerprint(self.environment_id, self.action):
            raise ValueError("operation fingerprint does not match its executable action")
        if self.success_count + self.failure_count > self.visit_count:
            raise ValueError("operation outcomes cannot exceed visit count")
        return self


class OperationAliasV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: str = Field(
        default="game-observatory.ai-player.operation-alias.v1",
        alias="schema",
    )
    environment_id: str = Field(min_length=1)
    alias_kind: Literal["transition", "skill_step", "route", "navigation", "external_trace"]
    alias_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    created_at: str = Field(default_factory=utc_now)


class RuntimeTelemetryEventV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: str = Field(
        default="game-observatory.ai-player.runtime-telemetry-event.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    session_id: str | None = Field(default=None, min_length=1)
    operation_id: str | None = Field(default=None, min_length=1)
    event_type: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    duration_ms: float = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    action_count: int = Field(default=0, ge=0)
    progress: dict[str, Any] = Field(default_factory=dict)
    evidence_step_ids: tuple[str, ...] = ()
    payload: dict[str, Any] = Field(default_factory=dict)
    previous_event_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    event_sha256: str = Field(min_length=64, max_length=64)
    occurred_at: str = Field(default_factory=utc_now)

    def hash_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude={"event_sha256"})

    @model_validator(mode="after")
    def validate_event_hash(self) -> "RuntimeTelemetryEventV1":
        expected = hashlib.sha256(
            _canonical_json(self.hash_payload()).encode("utf-8")
        ).hexdigest()
        if self.event_sha256 != expected:
            raise ValueError("runtime telemetry event hash is invalid")
        return self


def build_runtime_telemetry_event(
    *,
    event_id: str,
    environment_id: str,
    event_type: str,
    provider: str,
    session_id: str | None = None,
    operation_id: str | None = None,
    duration_ms: float = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    action_count: int = 0,
    progress: dict[str, Any] | None = None,
    evidence_step_ids: tuple[str, ...] = (),
    payload: dict[str, Any] | None = None,
    previous_event_sha256: str | None = None,
    occurred_at: str | None = None,
) -> RuntimeTelemetryEventV1:
    values = {
        "id": event_id,
        "environment_id": environment_id,
        "session_id": session_id,
        "operation_id": operation_id,
        "event_type": event_type,
        "provider": provider,
        "duration_ms": duration_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "action_count": action_count,
        "progress": progress or {},
        "evidence_step_ids": evidence_step_ids,
        "payload": payload or {},
        "previous_event_sha256": previous_event_sha256,
        "occurred_at": occurred_at or utc_now(),
    }
    unhashed = RuntimeTelemetryEventV1.model_construct(
        **values,
        event_sha256="0" * 64,
    )
    digest = hashlib.sha256(
        _canonical_json(unhashed.hash_payload()).encode("utf-8")
    ).hexdigest()
    return RuntimeTelemetryEventV1(**values, event_sha256=digest)


def append_runtime_telemetry_locked(
    connection: sqlite3.Connection,
    *,
    event_id: str,
    environment_id: str,
    event_type: str,
    provider: str,
    session_id: str | None = None,
    operation_id: str | None = None,
    duration_ms: float = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    action_count: int = 0,
    progress: dict[str, Any] | None = None,
    evidence_step_ids: tuple[str, ...] = (),
    payload: dict[str, Any] | None = None,
    occurred_at: str | None = None,
) -> RuntimeTelemetryEventV1:
    existing = connection.execute(
        "SELECT body_json FROM ai_player_runtime_telemetry_events WHERE id=?",
        (event_id,),
    ).fetchone()
    if existing is not None:
        return RuntimeTelemetryEventV1.model_validate_json(existing["body_json"])
    previous = connection.execute(
        """
        SELECT event_sha256,occurred_at FROM ai_player_runtime_telemetry_events
        WHERE environment_id=? AND session_id IS ?
        ORDER BY occurred_at DESC, id DESC LIMIT 1
        """,
        (environment_id, session_id),
    ).fetchone()
    resolved_occurred_at = occurred_at
    if resolved_occurred_at is None:
        resolved_occurred_at = utc_now()
        if previous is not None:
            previous_at = datetime.fromisoformat(
                str(previous["occurred_at"]).replace("Z", "+00:00")
            )
            proposed_at = datetime.fromisoformat(
                resolved_occurred_at.replace("Z", "+00:00")
            )
            if proposed_at <= previous_at:
                resolved_occurred_at = (previous_at + timedelta(microseconds=1)).isoformat()
    event = build_runtime_telemetry_event(
        event_id=event_id,
        environment_id=environment_id,
        session_id=session_id,
        operation_id=operation_id,
        event_type=event_type,
        provider=provider,
        duration_ms=duration_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        action_count=action_count,
        progress=progress,
        evidence_step_ids=evidence_step_ids,
        payload=payload,
        previous_event_sha256=(str(previous["event_sha256"]) if previous else None),
        occurred_at=resolved_occurred_at,
    )
    connection.execute(
        """
        INSERT INTO ai_player_runtime_telemetry_events(
            id, environment_id, session_id, operation_id, event_type, provider,
            duration_ms, input_tokens, output_tokens, action_count,
            previous_event_sha256, event_sha256, body_json, occurred_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            event.id,
            event.environment_id,
            event.session_id,
            event.operation_id,
            event.event_type,
            event.provider,
            event.duration_ms,
            event.input_tokens,
            event.output_tokens,
            event.action_count,
            event.previous_event_sha256,
            event.event_sha256,
            event.model_dump_json(by_alias=True),
            event.occurred_at,
        ),
    )
    return event


def backfill_session_lifecycle_locked(connection: sqlite3.Connection) -> int:
    """Idempotently project every historical session lifecycle event into telemetry."""

    rows = connection.execute(
        """
        SELECT id,session_id,environment_id,event_type,actor,reason,command_id,
               previous_version,new_version,body_json,created_at
        FROM ai_player_session_lifecycle_events
        ORDER BY created_at,id
        """
    ).fetchall()
    inserted = 0
    for row in rows:
        event_id = f"runtime-telemetry.session.{row['command_id']}"
        if connection.execute(
            "SELECT 1 FROM ai_player_runtime_telemetry_events WHERE id=?",
            (event_id,),
        ).fetchone():
            continue
        body = json.loads(str(row["body_json"]))
        append_runtime_telemetry_locked(
            connection,
            event_id=event_id,
            environment_id=str(row["environment_id"]),
            session_id=str(row["session_id"]),
            event_type=f"session.{row['event_type']}",
            provider="session-control",
            action_count=0,
            payload={
                "source_event_id": str(row["id"]),
                "actor": str(row["actor"]),
                "reason": str(row["reason"]),
                "previous_state": body.get("previous_state"),
                "new_state": body.get("new_state"),
                "previous_version": int(row["previous_version"]),
                "new_version": int(row["new_version"]),
            },
            occurred_at=str(row["created_at"]),
        )
        inserted += 1
    return inserted


class RuntimeTelemetryStream:
    def __init__(self, store: AIPlayerStore) -> None:
        self.store = store

    def append(self, **values: Any) -> RuntimeTelemetryEventV1:
        with self.store._write_lock, self.store._connection() as connection:
            return append_runtime_telemetry_locked(connection, **values)

    def list(self, environment_id: str, *, session_id: str | None = None):
        query = (
            "SELECT body_json FROM ai_player_runtime_telemetry_events "
            "WHERE environment_id=?"
        )
        params: list[Any] = [environment_id]
        if session_id is not None:
            query += " AND session_id=?"
            params.append(session_id)
        query += " ORDER BY occurred_at,id"
        with self.store._connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [RuntimeTelemetryEventV1.model_validate_json(row["body_json"]) for row in rows]


class OperationMigrationReceiptV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: str = Field(
        default="game-observatory.ai-player.operation-migration-receipt.v1",
        alias="schema",
    )
    dry_run: bool
    transition_count: int = Field(ge=0)
    skill_action_step_count: int = Field(ge=0)
    unique_operation_count: int = Field(ge=0)
    alias_count: int = Field(ge=0)
    existing_operation_count: int = Field(ge=0)
    inserted_operation_count: int = Field(ge=0)
    inserted_alias_count: int = Field(ge=0)
    generated_at: str = Field(default_factory=utc_now)


class OperationVisitPlanV1(BaseModel):
    """Deterministic first/second/third-visit policy for one canonical operation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: str = Field(
        default="game-observatory.ai-player.operation-visit-plan.v1",
        alias="schema",
    )
    operation_id: str = Field(min_length=1)
    mode: Literal["learn", "candidate_replay", "warm_route"]
    requires_model: bool
    visit_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    status: Literal["candidate", "verified", "quarantined", "retired"]


class OperationHistoryReconciliationReceiptV1(BaseModel):
    """Bounded, idempotent bridge from trusted SkillRuns into OperationMemory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: str = Field(
        default="game-observatory.ai-player.operation-history-reconciliation.v1",
        alias="schema",
    )
    environment_id: str = Field(min_length=1)
    dry_run: bool
    source_skill_run_count: int = Field(ge=0)
    eligible_skill_run_count: int = Field(ge=0)
    skipped_skill_run_count: int = Field(ge=0)
    inserted_observation_count: int = Field(ge=0)
    inserted_skill_execution_count: int = Field(ge=0)
    existing_execution_count: int = Field(ge=0)
    would_insert_execution_count: int = Field(ge=0)
    complete_context_count: int = Field(ge=0)
    generated_at: str = Field(default_factory=utc_now)


class OperationMemory:
    def __init__(self, store: AIPlayerStore) -> None:
        self.store = store

    @staticmethod
    def _id(fingerprint: str) -> str:
        return f"operation.{fingerprint[:32]}"

    def _put_operation_locked(
        self,
        connection: sqlite3.Connection,
        *,
        environment_id: str,
        action: NormalizedAction,
        evidence_refs: tuple[EvidenceReferenceV1, ...],
        created_at: str,
    ) -> tuple[OperationRecordV1, bool]:
        fingerprint = operation_fingerprint(environment_id, action)
        row = connection.execute(
            """
            SELECT body_json FROM ai_player_operations
            WHERE environment_id=? AND fingerprint=?
            """,
            (environment_id, fingerprint),
        ).fetchone()
        if row is not None:
            return OperationRecordV1.model_validate_json(row["body_json"]), False
        operation = OperationRecordV1(
            id=self._id(fingerprint),
            environment_id=environment_id,
            fingerprint=fingerprint,
            action=action,
            evidence_refs=evidence_refs,
            created_at=created_at,
            updated_at=created_at,
        )
        connection.execute(
            """
            INSERT INTO ai_player_operations(
                environment_id,id,fingerprint,status,body_json,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                operation.environment_id,
                operation.id,
                operation.fingerprint,
                operation.status,
                operation.model_dump_json(by_alias=True),
                operation.created_at,
                operation.updated_at,
            ),
        )
        return operation, True

    @staticmethod
    def _put_alias_locked(
        connection: sqlite3.Connection,
        alias: OperationAliasV1,
    ) -> bool:
        body = alias.model_dump_json(by_alias=True)
        existing = connection.execute(
            """
            SELECT body_json FROM ai_player_operation_aliases
            WHERE environment_id=? AND alias_kind=? AND alias_id=? AND source_version=?
            """,
            (alias.environment_id, alias.alias_kind, alias.alias_id, alias.source_version),
        ).fetchone()
        if existing is not None:
            if str(existing["body_json"]) != body:
                raise ValueError("operation alias identity contains different content")
            return False
        connection.execute(
            """
            INSERT INTO ai_player_operation_aliases(
                environment_id,alias_kind,alias_id,operation_id,source_version,
                body_json,created_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                alias.environment_id,
                alias.alias_kind,
                alias.alias_id,
                alias.operation_id,
                alias.source_version,
                body,
                alias.created_at,
            ),
        )
        return True

    def compile_transition(self, transition: TransitionEdgeV1) -> OperationRecordV1:
        with self.store._write_lock, self.store._connection() as connection:
            operation, _ = self._put_operation_locked(
                connection,
                environment_id=transition.environment_id,
                action=transition.action,
                evidence_refs=tuple(transition.evidence_refs),
                created_at=transition.created_at,
            )
            self._put_alias_locked(
                connection,
                OperationAliasV1(
                    environment_id=transition.environment_id,
                    alias_kind="transition",
                    alias_id=transition.id,
                    source_version=str(transition.version),
                    operation_id=operation.id,
                    created_at=transition.created_at,
                ),
            )
        return operation

    def compile_skill(self, skill: SkillVersionV1) -> tuple[OperationRecordV1, ...]:
        """Compile every executable skill step without inventing another identity."""

        operations: list[OperationRecordV1] = []
        with self.store._write_lock, self.store._connection() as connection:
            for step in skill.steps:
                if step.action is None:
                    continue
                operation, _ = self._put_operation_locked(
                    connection,
                    environment_id=skill.environment_id,
                    action=step.action,
                    evidence_refs=tuple(skill.evidence_refs),
                    created_at=skill.created_at,
                )
                self._put_alias_locked(
                    connection,
                    OperationAliasV1(
                        environment_id=skill.environment_id,
                        alias_kind="skill_step",
                        alias_id=f"{skill.id}#{step.id}",
                        source_version=str(skill.version),
                        operation_id=operation.id,
                        created_at=skill.created_at,
                    ),
                )
                operations.append(operation)
        return tuple(operations)

    def ensure_operation(
        self,
        *,
        environment_id: str,
        action: NormalizedAction,
        alias_kind: Literal[
            "transition",
            "skill_step",
            "route",
            "navigation",
            "external_trace",
        ]
        | None = None,
        alias_id: str | None = None,
        source_version: str = "1",
        evidence_refs: tuple[EvidenceReferenceV1, ...] = (),
        created_at: str | None = None,
    ) -> OperationRecordV1:
        """Create or resolve the sole canonical operation and an optional legacy alias."""

        if (alias_kind is None) != (alias_id is None):
            raise ValueError("operation alias kind and id must be supplied together")
        timestamp = created_at or utc_now()
        with self.store._write_lock, self.store._connection() as connection:
            operation, _ = self._put_operation_locked(
                connection,
                environment_id=environment_id,
                action=action,
                evidence_refs=evidence_refs,
                created_at=timestamp,
            )
            if alias_kind is not None and alias_id is not None:
                self._put_alias_locked(
                    connection,
                    OperationAliasV1(
                        environment_id=environment_id,
                        alias_kind=alias_kind,
                        alias_id=alias_id,
                        source_version=source_version,
                        operation_id=operation.id,
                        created_at=timestamp,
                    ),
                )
        return operation

    def migrate_legacy(self, *, dry_run: bool) -> OperationMigrationReceiptV1:
        transitions: list[TransitionEdgeV1] = []
        skills: list[SkillVersionV1] = []
        for environment in self.store.list_environments():
            transitions.extend(self.store.list_transition_edges(environment.id))
            skills.extend(self.store.list_skill_versions(environment.id))
        items: list[
            tuple[
                str,
                NormalizedAction,
                tuple[EvidenceReferenceV1, ...],
                str,
                str,
                str,
                str,
            ]
        ] = []
        for edge in transitions:
            items.append(
                (
                    edge.environment_id,
                    edge.action,
                    tuple(edge.evidence_refs),
                    "transition",
                    edge.id,
                    str(edge.version),
                    edge.created_at,
                )
            )
        skill_action_count = 0
        for skill in skills:
            for step in skill.steps:
                if step.action is None:
                    continue
                skill_action_count += 1
                items.append(
                    (
                        skill.environment_id,
                        step.action,
                        tuple(skill.evidence_refs),
                        "skill_step",
                        f"{skill.id}#{step.id}",
                        str(skill.version),
                        skill.created_at,
                    )
                )
        fingerprints = {
            (environment_id, operation_fingerprint(environment_id, action))
            for environment_id, action, *_rest in items
        }
        with self.store._connection() as connection:
            existing_count = int(
                connection.execute("SELECT COUNT(*) FROM ai_player_operations").fetchone()[0]
            )
        inserted_operations = 0
        inserted_aliases = 0
        if not dry_run:
            with self.store._write_lock, self.store._connection() as connection:
                for (
                    environment_id,
                    action,
                    evidence,
                    kind,
                    alias_id,
                    version,
                    source_created_at,
                ) in items:
                    operation, inserted = self._put_operation_locked(
                        connection,
                        environment_id=environment_id,
                        action=action,
                        evidence_refs=evidence,
                        created_at=source_created_at,
                    )
                    inserted_operations += int(inserted)
                    inserted_aliases += int(
                        self._put_alias_locked(
                            connection,
                            OperationAliasV1(
                                environment_id=environment_id,
                                alias_kind=kind,
                                alias_id=alias_id,
                                source_version=version,
                                operation_id=operation.id,
                                created_at=source_created_at,
                            ),
                        )
                    )
        return OperationMigrationReceiptV1(
            dry_run=dry_run,
            transition_count=len(transitions),
            skill_action_step_count=skill_action_count,
            unique_operation_count=len(fingerprints),
            alias_count=len(items),
            existing_operation_count=existing_count,
            inserted_operation_count=inserted_operations,
            inserted_alias_count=inserted_aliases,
        )

    @staticmethod
    def _context_key(source_state_id: str, terminal_state_id: str) -> tuple[str, str]:
        return source_state_id, terminal_state_id

    @staticmethod
    def _execution_body_context(body: dict[str, Any]) -> tuple[str, str] | None:
        context = body.get("context")
        if not isinstance(context, dict):
            return None
        source = context.get("source_state_id")
        terminal = context.get("terminal_state_id")
        if not isinstance(source, str) or not isinstance(terminal, str):
            return None
        return source, terminal

    @staticmethod
    def _context_rows_locked(
        connection: sqlite3.Connection,
        *,
        environment_id: str,
        operation_id: str,
        source_state_id: str,
        terminal_state_id: str,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT body_json FROM ai_player_operation_executions
            WHERE environment_id=? AND operation_id=?
            ORDER BY created_at,id
            """,
            (environment_id, operation_id),
        ).fetchall()
        expected = (source_state_id, terminal_state_id)
        bodies = [json.loads(str(row["body_json"])) for row in rows]
        return [
            body
            for body in bodies
            if OperationMemory._execution_body_context(body) == expected
        ]

    def plan_context_visit(
        self,
        environment_id: str,
        operation_id: str,
        *,
        source_state_id: str,
        terminal_state_id: str,
    ) -> OperationVisitPlanV1:
        """Plan reuse only inside the same guarded source-to-terminal context."""

        operation = self.get(environment_id, operation_id)
        if operation is None:
            raise ValueError(f"unknown operation: {environment_id}/{operation_id}")
        with self.store._connection() as connection:
            rows = self._context_rows_locked(
                connection,
                environment_id=environment_id,
                operation_id=operation_id,
                source_state_id=source_state_id,
                terminal_state_id=terminal_state_id,
            )
        successful_modes = {
            str(item.get("visit_mode"))
            for item in rows
            if item.get("outcome") == "success"
        }
        latest_requires_recovery = bool(rows) and rows[-1].get("outcome") in {
            "failed",
            "no_effect",
            "interrupted",
        }
        if operation.status == "retired" or latest_requires_recovery:
            mode: Literal["learn", "candidate_replay", "warm_route"] = "learn"
            requires_model = True
        elif {"learn", "candidate_replay"}.issubset(successful_modes):
            mode = "warm_route"
            requires_model = False
        elif "learn" in successful_modes:
            mode = "candidate_replay"
            requires_model = False
        else:
            mode = "learn"
            requires_model = True
        return OperationVisitPlanV1(
            operation_id=operation.id,
            mode=mode,
            requires_model=requires_model,
            visit_count=len(rows),
            success_count=sum(item.get("outcome") == "success" for item in rows),
            status=operation.status,
        )

    def _record_context_execution(
        self,
        *,
        operation: OperationRecordV1,
        execution_id: str,
        session_id: str,
        evidence_step_id: str,
        source_state_id: str,
        terminal_state_id: str,
        source_kind: Literal["observed_transition", "skill_run"],
        source_entity_id: str,
        source_created_at: str,
        visit_plan: OperationVisitPlanV1,
        outcome: Literal["success", "no_effect", "failed", "interrupted"],
        duration_ms: float,
        model_input_tokens: int | None,
        title: str,
        evidence_run_id: str,
        skill_version_id: str | None = None,
    ) -> bool:
        """Append one source-linked execution and advance its operation atomically."""

        with self.store._write_lock, self.store._connection() as connection:
            existing = connection.execute(
                "SELECT 1 FROM ai_player_operation_executions WHERE id=?",
                (execution_id,),
            ).fetchone()
            if existing is not None:
                return False
            current = connection.execute(
                """
                SELECT body_json FROM ai_player_operations
                WHERE environment_id=? AND id=?
                """,
                (operation.environment_id, operation.id),
            ).fetchone()
            if current is None:
                raise ValueError(f"unknown operation: {operation.id}")
            started = append_runtime_telemetry_locked(
                connection,
                event_id=f"runtime-telemetry.{execution_id}.started",
                environment_id=operation.environment_id,
                session_id=session_id,
                operation_id=operation.id,
                event_type="operation.started",
                provider="operation-memory-reconciliation",
                payload={
                    "source_kind": source_kind,
                    "source_entity_id": source_entity_id,
                    "source_created_at": source_created_at,
                    "visit_plan": visit_plan.model_dump(mode="json", by_alias=True),
                    "measurement_status": (
                        "measured" if model_input_tokens is not None else "unavailable"
                    ),
                },
            )
            completed = append_runtime_telemetry_locked(
                connection,
                event_id=f"runtime-telemetry.{execution_id}.completed",
                environment_id=operation.environment_id,
                session_id=session_id,
                operation_id=operation.id,
                event_type="operation.completed",
                provider="operation-memory-reconciliation",
                duration_ms=duration_ms,
                input_tokens=model_input_tokens or 0,
                action_count=1,
                evidence_step_ids=(evidence_step_id,),
                payload={
                    "source_kind": source_kind,
                    "source_entity_id": source_entity_id,
                    "outcome": outcome,
                    "measurement_status": (
                        "measured" if model_input_tokens is not None else "unavailable"
                    ),
                },
            )
            body = {
                "schema": "game-observatory.ai-player.operation-execution.v1",
                "id": execution_id,
                "environment_id": operation.environment_id,
                "operation_id": operation.id,
                "session_id": session_id,
                "started_event_id": started.id,
                "completed_event_id": completed.id,
                "evidence_step_id": evidence_step_id,
                "evidence_run_id": evidence_run_id,
                "outcome": outcome,
                "visit_mode": visit_plan.mode,
                "requires_model": visit_plan.requires_model,
                "context": {
                    "source_state_id": source_state_id,
                    "terminal_state_id": terminal_state_id,
                },
                "source_kind": source_kind,
                "source_entity_id": source_entity_id,
                "source_created_at": source_created_at,
                "skill_version_id": skill_version_id,
                "title": title,
                "decision_latency_ms": duration_ms,
                "model_input_tokens": model_input_tokens,
                "model_token_measurement": (
                    "measured" if model_input_tokens is not None else "unavailable"
                ),
                "created_at": completed.occurred_at,
            }
            connection.execute(
                """
                INSERT INTO ai_player_operation_executions(
                    id,environment_id,operation_id,session_id,started_event_id,
                    completed_event_id,evidence_step_id,outcome,body_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    execution_id,
                    operation.environment_id,
                    operation.id,
                    session_id,
                    started.id,
                    completed.id,
                    evidence_step_id,
                    outcome,
                    _canonical_json(body),
                    completed.occurred_at,
                ),
            )
            self._record_outcome_locked(
                connection,
                OperationRecordV1.model_validate_json(current["body_json"]),
                outcome,
            )
        return True

    def _resolve_observed_transition(
        self,
        transition: TransitionEdgeV1,
    ) -> dict[str, Any] | None:
        if (
            transition.action.type == "wait"
            or transition.to_state_id is None
            or transition.outcome in {"failed", "forbidden"}
        ):
            return None
        for reference in transition.evidence_refs:
            for step_id in reference.evidence_step_ids:
                step = self.store.observatory_store.get_evidence_step(step_id)
                if (
                    step is None
                    or step.status != "passed"
                    or step.action != transition.action
                ):
                    continue
                evidence_run = self.store.observatory_store.get_evidence_run(
                    step.evidence_run_id
                )
                if evidence_run is None or evidence_run.status != "passed":
                    continue
                environment = evidence_run.environment
                guard = environment.get("source_state_guard")
                guarded_source = guard.get("semantic_state_id") if isinstance(guard, dict) else None
                expected_terminal = environment.get("expected_semantic_state_id")
                if guarded_source not in {None, transition.from_state_id}:
                    continue
                if expected_terminal not in {None, transition.to_state_id}:
                    continue
                return {
                    "evidence_run_id": evidence_run.id,
                    "evidence_step_id": step.id,
                    "session_id": str(
                        environment.get("ai_player_session_id")
                        or f"operation-history.{transition.environment_id}"
                    ),
                    "duration_ms": _elapsed_ms(
                        step.action_started_at or step.started_at,
                        step.action_ended_at or step.ended_at,
                    ),
                    "source_created_at": step.started_at,
                }
        return None

    def record_observed_transition(self, transition: TransitionEdgeV1) -> bool:
        """Seed the first visit only from a passed physical transition step."""

        resolved = self._resolve_observed_transition(transition)
        if resolved is None or transition.to_state_id is None:
            return False
        operation = self.compile_transition(transition)
        source_state_id = transition.from_state_id
        terminal_state_id = transition.to_state_id
        execution_id = _stable_id(
            "operation-execution.observed",
            operation.environment_id,
            operation.id,
            source_state_id,
            terminal_state_id,
        )
        visit_plan = OperationVisitPlanV1(
            operation_id=operation.id,
            mode="learn",
            requires_model=True,
            visit_count=0,
            success_count=0,
            status=operation.status,
        )
        return self._record_context_execution(
            operation=operation,
            execution_id=execution_id,
            session_id=resolved["session_id"],
            evidence_step_id=resolved["evidence_step_id"],
            source_state_id=source_state_id,
            terminal_state_id=terminal_state_id,
            source_kind="observed_transition",
            source_entity_id=transition.id,
            source_created_at=resolved["source_created_at"],
            visit_plan=visit_plan,
            outcome="success",
            duration_ms=resolved["duration_ms"],
            model_input_tokens=None,
            title=transition.expected_change,
            evidence_run_id=resolved["evidence_run_id"],
        )

    def _resolve_skill_run(self, run: SkillRunV1) -> dict[str, Any] | None:
        persisted = self.store.get_skill_run(run.environment_id, run.id)
        if persisted != run or run.action_count != 1:
            return None
        skill = self.store.get_skill_version_by_id(run.environment_id, run.skill_version_id)
        if (
            skill is None
            or skill.skill_layer != "atomic"
            or skill.executor_kind != "normalized_actions"
        ):
            return None
        action_steps = [step for step in skill.steps if step.action is not None]
        terminal_state_ids = [
            step.expected_state_id
            for step in skill.steps
            if step.kind == "assert" and step.expected_state_id is not None
        ]
        if len(action_steps) != 1 or len(terminal_state_ids) != 1:
            return None
        action_step = action_steps[0]
        action = action_step.action
        assert action is not None
        for reference in run.evidence_refs:
            for evidence_run_id in reference.evidence_run_ids:
                evidence_run = self.store.observatory_store.get_evidence_run(evidence_run_id)
                if evidence_run is None:
                    continue
                environment = evidence_run.environment
                if environment.get("skill_replay_version_id") != skill.id:
                    continue
                guard = environment.get("source_state_guard")
                source_state_id = (
                    guard.get("semantic_state_id") if isinstance(guard, dict) else None
                )
                terminal_state_id = environment.get("expected_semantic_state_id")
                if (
                    not isinstance(source_state_id, str)
                    or not isinstance(terminal_state_id, str)
                    or source_state_id not in skill.applicability_scope.required_state_ids
                    or terminal_state_id != terminal_state_ids[0]
                ):
                    continue
                for step_id in evidence_run.step_ids:
                    step = self.store.observatory_store.get_evidence_step(step_id)
                    if step is None or step.action != action:
                        continue
                    if run.outcome == "success" and step.status != "passed":
                        continue
                    return {
                        "skill": skill,
                        "action_step": action_step,
                        "evidence_run_id": evidence_run.id,
                        "evidence_step_id": step.id,
                        "session_id": str(
                            environment.get("ai_player_session_id")
                            or f"operation-history.{run.environment_id}"
                        ),
                        "source_state_id": source_state_id,
                        "terminal_state_id": terminal_state_id,
                        "source_created_at": step.started_at,
                    }
        return None

    def record_skill_run(self, run: SkillRunV1) -> dict[str, Any]:
        """Record one runtime increment while reusing its canonical read handles."""

        with self.store.read_session():
            return self._record_skill_run_in_session(run)

    def _record_skill_run_in_session(self, run: SkillRunV1) -> dict[str, Any]:
        """Project one trusted single-action SkillRun into its contextual lifecycle."""

        resolved = self._resolve_skill_run(run)
        if resolved is None:
            return {"eligible": False, "reason": "no_exact_physical_single_action_context"}
        skill: SkillVersionV1 = resolved["skill"]
        action_step = resolved["action_step"]
        operations = self.compile_skill(skill)
        operation = next(
            item for item in operations if item.action == action_step.action
        )
        observation_inserted = 0
        for transition_id in skill.source_transition_ids:
            transition = self.store.get_transition_edge(run.environment_id, transition_id)
            if (
                transition is not None
                and transition.action == operation.action
                and transition.from_state_id == resolved["source_state_id"]
                and transition.to_state_id == resolved["terminal_state_id"]
            ):
                observation_inserted += int(self.record_observed_transition(transition))
        visit_plan = self.plan_context_visit(
            run.environment_id,
            operation.id,
            source_state_id=resolved["source_state_id"],
            terminal_state_id=resolved["terminal_state_id"],
        )
        outcome: Literal["success", "no_effect", "failed", "interrupted"]
        if run.outcome == "success":
            outcome = "success"
        elif run.outcome == "interrupted":
            outcome = "interrupted"
        else:
            outcome = "failed"
        execution_id = _stable_id(
            "operation-execution.skill-run",
            run.environment_id,
            operation.id,
            run.id,
            action_step.id,
        )
        inserted = self._record_context_execution(
            operation=operation,
            execution_id=execution_id,
            session_id=resolved["session_id"],
            evidence_step_id=resolved["evidence_step_id"],
            source_state_id=resolved["source_state_id"],
            terminal_state_id=resolved["terminal_state_id"],
            source_kind="skill_run",
            source_entity_id=run.id,
            source_created_at=resolved["source_created_at"],
            visit_plan=visit_plan,
            outcome=outcome,
            duration_ms=float(run.decision_latency_ms),
            model_input_tokens=int(run.model_input_tokens),
            title=skill.title,
            evidence_run_id=resolved["evidence_run_id"],
            skill_version_id=skill.id,
        )
        return {
            "eligible": True,
            "operation_id": operation.id,
            "execution_id": execution_id,
            "visit_mode": visit_plan.mode,
            "observation_inserted": observation_inserted,
            "skill_execution_inserted": int(inserted),
        }

    def reconcile_skill_history(
        self,
        environment_id: str,
        *,
        dry_run: bool = False,
    ) -> OperationHistoryReconciliationReceiptV1:
        """Run one bounded reconciliation while reusing canonical read handles."""

        with self.store.read_session():
            return self._reconcile_skill_history_in_session(
                environment_id,
                dry_run=dry_run,
            )

    def _reconcile_skill_history_in_session(
        self,
        environment_id: str,
        *,
        dry_run: bool = False,
    ) -> OperationHistoryReconciliationReceiptV1:
        """Periodically bridge only evidence-backed SkillRuns, never all raw actions."""

        runs = self.store.list_skill_runs(environment_id)
        eligible = 0
        skipped = 0
        inserted_observations = 0
        inserted_skill_executions = 0
        considered_ids: set[str] = set()
        would_insert_ids: set[str] = set()
        with self.store._connection() as connection:
            existing_ids = {
                str(row["id"])
                for row in connection.execute(
                    """
                    SELECT id FROM ai_player_operation_executions
                    WHERE environment_id=?
                    """,
                    (environment_id,),
                ).fetchall()
            }
        for run in runs:
            resolved = self._resolve_skill_run(run)
            if resolved is None:
                skipped += 1
                continue
            eligible += 1
            skill: SkillVersionV1 = resolved["skill"]
            action_step = resolved["action_step"]
            operation_id = self._id(operation_fingerprint(environment_id, action_step.action))
            skill_execution_id = _stable_id(
                "operation-execution.skill-run",
                environment_id,
                operation_id,
                run.id,
                action_step.id,
            )
            candidate_ids = {skill_execution_id}
            for transition_id in skill.source_transition_ids:
                transition = self.store.get_transition_edge(environment_id, transition_id)
                if (
                    transition is not None
                    and transition.action == action_step.action
                    and transition.from_state_id == resolved["source_state_id"]
                    and transition.to_state_id == resolved["terminal_state_id"]
                    and self._resolve_observed_transition(transition) is not None
                ):
                    candidate_ids.add(
                        _stable_id(
                            "operation-execution.observed",
                            environment_id,
                            operation_id,
                            resolved["source_state_id"],
                            resolved["terminal_state_id"],
                        )
                    )
            considered_ids.update(candidate_ids)
            would_insert_ids.update(candidate_ids.difference(existing_ids))
            if dry_run:
                continue
            result = self.record_skill_run(run)
            inserted_observations += int(result.get("observation_inserted", 0))
            inserted_skill_executions += int(result.get("skill_execution_inserted", 0))
        health = self.learning_health(environment_id, detail_limit=0)
        return OperationHistoryReconciliationReceiptV1(
            environment_id=environment_id,
            dry_run=dry_run,
            source_skill_run_count=len(runs),
            eligible_skill_run_count=eligible,
            skipped_skill_run_count=skipped,
            inserted_observation_count=inserted_observations,
            inserted_skill_execution_count=inserted_skill_executions,
            existing_execution_count=len(considered_ids.intersection(existing_ids)),
            would_insert_execution_count=len(would_insert_ids),
            complete_context_count=health["complete_context_count"],
        )

    def resolve_alias(
        self,
        environment_id: str,
        alias_kind: str,
        alias_id: str,
    ) -> OperationRecordV1 | None:
        with self.store._connection() as connection:
            row = connection.execute(
                """
                SELECT operation.body_json
                FROM ai_player_operation_aliases AS alias
                JOIN ai_player_operations AS operation
                  ON operation.environment_id=alias.environment_id
                 AND operation.id=alias.operation_id
                WHERE alias.environment_id=? AND alias.alias_kind=? AND alias.alias_id=?
                ORDER BY alias.source_version DESC LIMIT 1
                """,
                (environment_id, alias_kind, alias_id),
            ).fetchone()
        return OperationRecordV1.model_validate_json(row["body_json"]) if row else None

    def get(self, environment_id: str, operation_id: str) -> OperationRecordV1 | None:
        with self.store._connection() as connection:
            row = connection.execute(
                """
                SELECT body_json FROM ai_player_operations
                WHERE environment_id=? AND id=?
                """,
                (environment_id, operation_id),
            ).fetchone()
        return OperationRecordV1.model_validate_json(row["body_json"]) if row else None

    def plan_visit(
        self,
        environment_id: str,
        operation_id: str,
    ) -> OperationVisitPlanV1:
        """Choose the replay lane without creating a second equivalent operation."""

        operation = self.get(environment_id, operation_id)
        if operation is None:
            raise ValueError(f"unknown operation: {environment_id}/{operation_id}")
        if operation.status in {"quarantined", "retired"}:
            mode: Literal["learn", "candidate_replay", "warm_route"] = "learn"
            requires_model = True
        elif operation.status == "verified" and operation.success_count >= 2:
            mode = "warm_route"
            requires_model = False
        elif operation.visit_count == 0:
            mode = "learn"
            requires_model = True
        else:
            mode = "candidate_replay"
            requires_model = False
        return OperationVisitPlanV1(
            operation_id=operation.id,
            mode=mode,
            requires_model=requires_model,
            visit_count=operation.visit_count,
            success_count=operation.success_count,
            status=operation.status,
        )

    @staticmethod
    def _record_outcome_locked(
        connection: sqlite3.Connection,
        operation: OperationRecordV1,
        outcome: Literal["success", "no_effect", "failed", "interrupted"],
    ) -> OperationRecordV1:
        success_count = operation.success_count + int(outcome == "success")
        failure_count = operation.failure_count + int(outcome in {"no_effect", "failed"})
        status = operation.status
        if outcome in {"no_effect", "failed"}:
            status = "quarantined"
        elif success_count >= 2 and status not in {"quarantined", "retired"}:
            status = "verified"
        updated = operation.model_copy(
            update={
                "status": status,
                "visit_count": operation.visit_count + 1,
                "success_count": success_count,
                "failure_count": failure_count,
                "updated_at": utc_now(),
            }
        )
        connection.execute(
            """
            UPDATE ai_player_operations
            SET status=?,body_json=?,updated_at=?
            WHERE environment_id=? AND id=?
            """,
            (
                updated.status,
                updated.model_dump_json(by_alias=True),
                updated.updated_at,
                updated.environment_id,
                updated.id,
            ),
        )
        return updated

    def record_outcome(
        self,
        environment_id: str,
        operation_id: str,
        outcome: Literal["success", "no_effect", "failed", "interrupted"],
    ) -> OperationRecordV1:
        """Advance replay confidence after one bounded visit or execution."""

        with self.store._write_lock, self.store._connection() as connection:
            row = connection.execute(
                """
                SELECT body_json FROM ai_player_operations
                WHERE environment_id=? AND id=?
                """,
                (environment_id, operation_id),
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown operation: {environment_id}/{operation_id}")
            return self._record_outcome_locked(
                connection,
                OperationRecordV1.model_validate_json(row["body_json"]),
                outcome,
            )

    def learning_health(
        self,
        environment_id: str,
        *,
        detail_limit: int = 10,
    ) -> dict[str, Any]:
        """Read the canonical contextual first/second/third-visit ledger."""

        if not 0 <= detail_limit <= 40:
            raise ValueError("detail_limit must be between 0 and 40")
        with self.store._connection() as connection:
            operation_rows = connection.execute(
                """
                SELECT id,status,body_json FROM ai_player_operations
                WHERE environment_id=? ORDER BY created_at,id
                """,
                (environment_id,),
            ).fetchall()
            execution_rows = connection.execute(
                """
                SELECT id,operation_id,body_json FROM ai_player_operation_executions
                WHERE environment_id=? ORDER BY created_at,id
                """,
                (environment_id,),
            ).fetchall()
        operation_status_counts: defaultdict[str, int] = defaultdict(int)
        for row in operation_rows:
            operation_status_counts[str(row["status"])] += 1
        contexts: defaultdict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        unscoped_execution_count = 0
        for row in execution_rows:
            body = json.loads(str(row["body_json"]))
            context = self._execution_body_context(body)
            if context is None:
                unscoped_execution_count += 1
                continue
            contexts[(str(row["operation_id"]), *context)].append(body)

        details: list[dict[str, Any]] = []
        complete_context_count = 0
        candidate_context_count = 0
        observed_context_count = 0
        failed_context_count = 0
        historical_failure_context_count = 0
        measured_zero_model_replay_count = 0
        measured_model_assisted_replay_count = 0
        unavailable_token_measurement_count = 0
        for (operation_id, source_state_id, terminal_state_id), rows in contexts.items():
            successful_by_mode: dict[str, dict[str, Any]] = {}
            for row in rows:
                token_count = row.get("model_input_tokens")
                if token_count is None:
                    unavailable_token_measurement_count += 1
                elif row.get("source_kind") == "skill_run" and int(token_count) == 0:
                    measured_zero_model_replay_count += 1
                elif row.get("source_kind") == "skill_run" and int(token_count) > 0:
                    measured_model_assisted_replay_count += 1
                if row.get("outcome") == "success":
                    successful_by_mode.setdefault(str(row.get("visit_mode")), row)
            learn = successful_by_mode.get("learn")
            candidate = successful_by_mode.get("candidate_replay")
            warm = successful_by_mode.get("warm_route")
            zero_model_replays = all(
                item is not None and item.get("model_input_tokens") == 0
                for item in (candidate, warm)
            )
            complete = learn is not None and candidate is not None and warm is not None
            complete = complete and zero_model_replays
            has_failure = any(item.get("outcome") in {"failed", "no_effect"} for item in rows)
            unresolved_failure = rows[-1].get("outcome") in {
                "failed",
                "no_effect",
                "interrupted",
            }
            complete = complete and not unresolved_failure
            if unresolved_failure:
                stage = "recovery_required"
            elif complete:
                stage = "complete"
                complete_context_count += 1
            elif warm is not None:
                stage = "warm_route_needs_zero_model_proof"
                candidate_context_count += 1
            elif candidate is not None:
                stage = "candidate_replay_verified"
                candidate_context_count += 1
            elif learn is not None:
                stage = "observed_once"
                observed_context_count += 1
            else:
                stage = "failed"
            failed_context_count += int(unresolved_failure)
            historical_failure_context_count += int(has_failure)
            sequence = [
                {
                    "visit_mode": mode,
                    "execution_id": item.get("id"),
                    "source_kind": item.get("source_kind"),
                    "source_entity_id": item.get("source_entity_id"),
                    "session_id": item.get("session_id"),
                    "evidence_run_id": item.get("evidence_run_id"),
                    "evidence_step_id": item.get("evidence_step_id"),
                    "model_input_tokens": item.get("model_input_tokens"),
                    "model_token_measurement": item.get("model_token_measurement"),
                    "decision_latency_ms": item.get("decision_latency_ms"),
                    "source_created_at": item.get("source_created_at"),
                }
                for mode, item in (
                    ("learn", learn),
                    ("candidate_replay", candidate),
                    ("warm_route", warm),
                )
                if item is not None
            ]
            title = next(
                (
                    str(item["title"])
                    for item in reversed(rows)
                    if isinstance(item.get("title"), str) and item["title"]
                ),
                operation_id,
            )
            details.append(
                {
                    "operation_id": operation_id,
                    "title": title,
                    "source_state_id": source_state_id,
                    "terminal_state_id": terminal_state_id,
                    "stage": stage,
                    "sequence_complete": complete,
                    "execution_count": len(rows),
                    "failure_count": sum(
                        item.get("outcome") in {"failed", "no_effect"} for item in rows
                    ),
                    "sequence": sequence,
                }
            )
        details.sort(
            key=lambda item: (
                not item["sequence_complete"],
                item["stage"],
                item["title"],
                item["operation_id"],
                item["source_state_id"],
            )
        )
        recorded_operation_ids = {str(row["operation_id"]) for row in execution_rows}
        if not contexts:
            status = "empty"
        elif failed_context_count or measured_model_assisted_replay_count:
            status = "attention"
        elif complete_context_count == len(contexts):
            status = "healthy"
        else:
            status = "learning"
        return {
            "schema": "game-observatory.ai-player.operation-learning-health.v1",
            "environment_id": environment_id,
            "status": status,
            "measurement_authority": "ai_player_operation_executions.context.v1",
            "operation_count": len(operation_rows),
            "recorded_operation_count": len(recorded_operation_ids),
            "unvisited_operation_count": len(operation_rows) - len(recorded_operation_ids),
            "operation_status_counts": dict(sorted(operation_status_counts.items())),
            "execution_count": len(execution_rows),
            "contextual_execution_count": len(execution_rows) - unscoped_execution_count,
            "unscoped_execution_count": unscoped_execution_count,
            "context_count": len(contexts),
            "complete_context_count": complete_context_count,
            "candidate_context_count": candidate_context_count,
            "observed_context_count": observed_context_count,
            "failed_context_count": failed_context_count,
            "historical_failure_context_count": historical_failure_context_count,
            "second_visit_completion_rate": (
                round(
                    sum(
                        any(
                            item.get("outcome") == "success"
                            and item.get("visit_mode") == "candidate_replay"
                            for item in rows
                        )
                        for rows in contexts.values()
                    )
                    / len(contexts),
                    4,
                )
                if contexts
                else 0.0
            ),
            "third_visit_completion_rate": (
                round(complete_context_count / len(contexts), 4) if contexts else 0.0
            ),
            "measured_zero_model_replay_count": measured_zero_model_replay_count,
            "measured_model_assisted_replay_count": measured_model_assisted_replay_count,
            "unavailable_token_measurement_count": unavailable_token_measurement_count,
            "contexts": details[:detail_limit],
        }

    def execute(
        self,
        operation: OperationRecordV1,
        *,
        gateway: DeviceGateway,
        evidence_run_id: str,
        lease_token: str,
        session_id: str,
        target_bounds: SourcePixelRect | None = None,
        record_options: dict[str, Any] | None = None,
    ):
        if gateway.store.db_path.resolve() != self.store.observatory_store.db_path.resolve():
            raise ValueError("OperationMemory and DeviceGateway must share canonical storage")
        started_at = time.perf_counter()
        execution_id = f"operation-execution.{uuid.uuid4().hex}"
        stream = RuntimeTelemetryStream(self.store)
        started = stream.append(
            event_id=f"runtime-telemetry.{execution_id}.started",
            environment_id=operation.environment_id,
            session_id=session_id,
            operation_id=operation.id,
            event_type="operation.started",
            provider="operation-memory",
            payload={
                "evidence_run_id": evidence_run_id,
                "visit_plan": self.plan_visit(
                    operation.environment_id,
                    operation.id,
                ).model_dump(mode="json", by_alias=True),
            },
        )
        try:
            options = dict(record_options or {})
            if "target_bounds" in options:
                raise ValueError("record_options cannot override canonical target_bounds")
            step = gateway.record_evidence_step(
                evidence_run_id,
                lease_token,
                operation.action,
                target_bounds=target_bounds,
                **options,
            )
            outcome = "success" if step.status == "passed" else "failed"
            completed = stream.append(
                event_id=f"runtime-telemetry.{execution_id}.completed",
                environment_id=operation.environment_id,
                session_id=session_id,
                operation_id=operation.id,
                event_type="operation.completed",
                provider="operation-memory",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                action_count=1,
                evidence_step_ids=(step.id,),
                payload={"status": step.status},
            )
        except Exception as error:
            stream.append(
                event_id=f"runtime-telemetry.{execution_id}.failed",
                environment_id=operation.environment_id,
                session_id=session_id,
                operation_id=operation.id,
                event_type="operation.failed",
                provider="operation-memory",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                payload={"error_type": type(error).__name__, "error": str(error)},
            )
            raise
        body = {
            "schema": "game-observatory.ai-player.operation-execution.v1",
            "id": execution_id,
            "environment_id": operation.environment_id,
            "operation_id": operation.id,
            "session_id": session_id,
            "started_event_id": started.id,
            "completed_event_id": completed.id,
            "evidence_step_id": step.id,
            "outcome": outcome,
            "created_at": completed.occurred_at,
        }
        with self.store._write_lock, self.store._connection() as connection:
            connection.execute(
                """
                INSERT INTO ai_player_operation_executions(
                    id,environment_id,operation_id,session_id,started_event_id,
                    completed_event_id,evidence_step_id,outcome,body_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    execution_id,
                    operation.environment_id,
                    operation.id,
                    session_id,
                    started.id,
                    completed.id,
                    step.id,
                    outcome,
                    _canonical_json(body),
                    completed.occurred_at,
                ),
            )
            current = connection.execute(
                """
                SELECT body_json FROM ai_player_operations
                WHERE environment_id=? AND id=?
                """,
                (operation.environment_id, operation.id),
            ).fetchone()
            if current is None:
                raise ValueError(f"operation disappeared during execution: {operation.id}")
            self._record_outcome_locked(
                connection,
                OperationRecordV1.model_validate_json(current["body_json"]),
                outcome,
            )
        return step


class CanonicalStateMigrationReceiptV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: str = Field(
        default="game-observatory.ai-player.canonical-state-migration-receipt.v1",
        alias="schema",
    )
    dry_run: bool
    active_state_count: int = Field(ge=0)
    fingerprint_count: int = Field(ge=0)
    duplicate_state_count: int = Field(ge=0)
    inserted_registry_count: int = Field(ge=0)
    inserted_alias_count: int = Field(ge=0)
    generated_at: str = Field(default_factory=utc_now)


class CanonicalStateRegistry:
    def __init__(self, store: AIPlayerStore) -> None:
        self.store = store

    def reconcile(self, *, dry_run: bool) -> CanonicalStateMigrationReceiptV1:
        with self.store._connection() as connection:
            rows = connection.execute(
                """
                SELECT environment_id,id,version,status,semantic_fingerprint,body_json,created_at
                FROM ai_player_semantic_states
                ORDER BY environment_id,id,version DESC
                """
            ).fetchall()
        latest: dict[tuple[str, str], sqlite3.Row] = {}
        for row in rows:
            latest.setdefault((str(row["environment_id"]), str(row["id"])), row)
        active = [row for row in latest.values() if row["status"] in {"accepted", "candidate"}]
        groups: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
        for row in active:
            groups[(str(row["environment_id"]), str(row["semantic_fingerprint"]))].append(row)
        choices: dict[tuple[str, str], sqlite3.Row] = {}
        for key, values in groups.items():
            choices[key] = sorted(
                values,
                key=lambda row: (
                    0 if row["status"] == "accepted" else 1,
                    -int(row["version"]),
                    str(row["created_at"]),
                    str(row["id"]),
                ),
            )[0]
        inserted_registry = 0
        inserted_alias = 0
        if not dry_run:
            with self.store._write_lock, self.store._connection() as connection:
                for (environment_id, fingerprint), canonical in choices.items():
                    now = utc_now()
                    registry_body = _canonical_json(
                        {
                            "schema": "game-observatory.ai-player.canonical-state-registry.v1",
                            "environment_id": environment_id,
                            "semantic_fingerprint": fingerprint,
                            "canonical_state_id": str(canonical["id"]),
                            "selection": "accepted_then_latest_version_then_stable_id",
                            "created_at": now,
                        }
                    )
                    inserted_registry += connection.execute(
                        """
                        INSERT OR IGNORE INTO ai_player_canonical_state_registry(
                            environment_id,semantic_fingerprint,canonical_state_id,
                            body_json,created_at,updated_at
                        ) VALUES(?,?,?,?,?,?)
                        """,
                        (environment_id, fingerprint, canonical["id"], registry_body, now, now),
                    ).rowcount
                    for row in groups[(environment_id, fingerprint)]:
                        if row["id"] == canonical["id"]:
                            continue
                        alias_body = _canonical_json(
                            {
                                "schema": "game-observatory.ai-player.canonical-state-alias.v1",
                                "environment_id": environment_id,
                                "state_id": str(row["id"]),
                                "canonical_state_id": str(canonical["id"]),
                                "semantic_fingerprint": fingerprint,
                                "reason": "same_semantic_fingerprint",
                                "created_at": now,
                            }
                        )
                        inserted_alias += connection.execute(
                            """
                            INSERT OR IGNORE INTO ai_player_canonical_state_aliases(
                                environment_id,state_id,canonical_state_id,
                                semantic_fingerprint,reason,body_json,created_at
                            ) VALUES(?,?,?,?,?,?,?)
                            """,
                            (
                                environment_id,
                                row["id"],
                                canonical["id"],
                                fingerprint,
                                "same_semantic_fingerprint",
                                alias_body,
                                now,
                            ),
                        ).rowcount
        return CanonicalStateMigrationReceiptV1(
            dry_run=dry_run,
            active_state_count=len(active),
            fingerprint_count=len(groups),
            duplicate_state_count=sum(max(0, len(values) - 1) for values in groups.values()),
            inserted_registry_count=inserted_registry,
            inserted_alias_count=inserted_alias,
        )

    def resolve(self, environment_id: str, state_id: str) -> str:
        with self.store._connection() as connection:
            row = connection.execute(
                """
                SELECT canonical_state_id FROM ai_player_canonical_state_aliases
                WHERE environment_id=? AND state_id=?
                """,
                (environment_id, state_id),
            ).fetchone()
        return str(row["canonical_state_id"]) if row else state_id
