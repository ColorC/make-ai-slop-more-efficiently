"""Canonical, device-independent control plane for long-running AI-player sessions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..models import utc_now
from .contracts import EvidenceReferenceV1
from .store import AIPlayerStore


SessionState = Literal["created", "running", "paused", "safe_stopped", "completed"]
SessionEventType = Literal[
    "created",
    "started",
    "paused",
    "resumed",
    "safe_stopped",
    "completed",
    "checkpointed",
    "heartbeat",
    "stale_reconciled",
]
SessionLeaseStatus = Literal["active", "released", "expired"]
DEFAULT_SESSION_LEASE_TTL_SECONDS = 900


class _StrictSessionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)


def _non_blank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("session lease timestamps must include a UTC offset")
    return parsed


def _expires_at(timestamp: str, ttl_seconds: int) -> str:
    return (_timestamp(timestamp) + timedelta(seconds=ttl_seconds)).isoformat()


class AIPlayerSessionV1(_StrictSessionModel):
    """Current canonical snapshot; lifecycle history lives in the event ledger."""

    schema_id: Literal["game-observatory.ai-player.session.v1"] = Field(
        default="game-observatory.ai-player.session.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    requested_environment_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    lineage_path: list[str] = Field(min_length=1)
    objective: str = Field(min_length=1)
    state: SessionState = "created"
    version: int = Field(default=1, ge=1)
    action_budget: int = Field(ge=0)
    remaining_action_budget: int = Field(ge=0)
    token_budget: int | None = Field(default=None, ge=0)
    remaining_token_budget: int | None = Field(default=None, ge=0)
    time_budget_seconds: float = Field(gt=0)
    remaining_time_seconds: float = Field(ge=0)
    active_task_ids: list[str] = Field(default_factory=list)
    last_capsule_id: str | None = Field(default=None, min_length=1)
    last_evidence_refs: list[EvidenceReferenceV1] = Field(default_factory=list)
    pause_reason: str | None = Field(default=None, min_length=1)
    safe_stop_reason: str | None = Field(default=None, min_length=1)
    lease_id: str | None = Field(default=None, min_length=1)
    lease_holder: str | None = Field(default=None, min_length=1)
    lease_acquired_at: str | None = Field(default=None, min_length=1)
    last_heartbeat_at: str | None = Field(default=None, min_length=1)
    lease_expires_at: str | None = Field(default=None, min_length=1)
    created_at: str = Field(default_factory=utc_now)
    started_at: str | None = Field(default=None, min_length=1)
    paused_at: str | None = Field(default=None, min_length=1)
    resumed_at: str | None = Field(default=None, min_length=1)
    safe_stopped_at: str | None = Field(default=None, min_length=1)
    completed_at: str | None = Field(default=None, min_length=1)
    updated_at: str = Field(default_factory=utc_now)

    @field_validator("id", "requested_environment_id", "environment_id", "objective")
    @classmethod
    def validate_required_text(cls, value: str, info: Any) -> str:
        return _non_blank(value, str(info.field_name))

    @field_validator("lineage_path", "active_task_ids")
    @classmethod
    def validate_unique_ids(cls, value: list[str], info: Any) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError(f"{info.field_name} must contain non-blank ids")
        if len(value) != len(set(value)):
            raise ValueError(f"{info.field_name} must not contain duplicates")
        return value

    @model_validator(mode="after")
    def validate_binding_budget_and_terminal_state(self) -> "AIPlayerSessionV1":
        if self.lineage_path[0] != self.requested_environment_id:
            raise ValueError("lineage path must begin at the requested environment")
        if self.lineage_path[-1] != self.environment_id:
            raise ValueError("session must bind to the current environment leaf")
        if self.remaining_action_budget > self.action_budget:
            raise ValueError("remaining action budget cannot exceed its initial budget")
        if (self.token_budget is None) != (self.remaining_token_budget is None):
            raise ValueError("token budget and remaining token budget must both be set or null")
        if (
            self.token_budget is not None
            and self.remaining_token_budget is not None
            and self.remaining_token_budget > self.token_budget
        ):
            raise ValueError("remaining token budget cannot exceed its initial budget")
        if self.remaining_time_seconds > self.time_budget_seconds:
            raise ValueError("remaining time cannot exceed its initial budget")
        if self.state == "safe_stopped" and (
            not self.safe_stop_reason or not self.safe_stopped_at
        ):
            raise ValueError("safe-stopped session requires its reason and timestamp")
        if self.state == "completed" and not self.completed_at:
            raise ValueError("completed session requires its timestamp")
        lease_values = (
            self.lease_id,
            self.lease_holder,
            self.lease_acquired_at,
            self.last_heartbeat_at,
            self.lease_expires_at,
        )
        if any(value is not None for value in lease_values) and not all(
            value is not None for value in lease_values
        ):
            raise ValueError("session lease fields must be populated or cleared together")
        if self.state != "running" and any(value is not None for value in lease_values):
            raise ValueError("only a running session may retain an active lease")
        if self.lease_acquired_at is not None and self.lease_expires_at is not None:
            acquired = _timestamp(self.lease_acquired_at)
            heartbeat = _timestamp(self.last_heartbeat_at or self.lease_acquired_at)
            expires = _timestamp(self.lease_expires_at)
            if heartbeat < acquired or expires <= heartbeat:
                raise ValueError("session lease timestamps are not monotonic")
        if any(ref.environment_id != self.environment_id for ref in self.last_evidence_refs):
            raise ValueError("session evidence must belong to the current environment leaf")
        return self


class AIPlayerSessionEventV1(_StrictSessionModel):
    """One immutable lifecycle fact produced by an idempotent command."""

    schema_id: Literal["game-observatory.ai-player.session-event.v1"] = Field(
        default="game-observatory.ai-player.session-event.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    event_type: SessionEventType
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    command_id: str = Field(min_length=1)
    previous_state: SessionState | None = None
    new_state: SessionState
    previous_version: int = Field(ge=0)
    new_version: int = Field(ge=1)
    timestamp: str = Field(default_factory=utc_now)

    @field_validator("id", "session_id", "environment_id", "actor", "reason", "command_id")
    @classmethod
    def validate_event_text(cls, value: str, info: Any) -> str:
        return _non_blank(value, str(info.field_name))

    @model_validator(mode="after")
    def validate_versions(self) -> "AIPlayerSessionEventV1":
        if self.new_version != self.previous_version + 1:
            raise ValueError("a lifecycle event must advance exactly one session version")
        if self.event_type == "created" and (
            self.previous_state is not None
            or self.previous_version != 0
            or self.new_state != "created"
        ):
            raise ValueError("created event must establish session version 1")
        return self


class AIPlayerSessionLeaseV1(_StrictSessionModel):
    """Canonical lease snapshot; immutable history remains in lifecycle events."""

    schema_id: Literal["game-observatory.ai-player.session-lease.v1"] = Field(
        default="game-observatory.ai-player.session-lease.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    holder: str = Field(min_length=1)
    status: SessionLeaseStatus = "active"
    acquired_at: str = Field(min_length=1)
    last_heartbeat_at: str = Field(min_length=1)
    expires_at: str = Field(min_length=1)
    released_at: str | None = Field(default=None, min_length=1)
    release_reason: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_lease_timestamps(self) -> "AIPlayerSessionLeaseV1":
        acquired = _timestamp(self.acquired_at)
        heartbeat = _timestamp(self.last_heartbeat_at)
        expires = _timestamp(self.expires_at)
        if heartbeat < acquired or expires <= heartbeat:
            raise ValueError("session lease timestamps are not monotonic")
        if self.status == "active" and (
            self.released_at is not None or self.release_reason is not None
        ):
            raise ValueError("active session lease cannot have release metadata")
        if self.status != "active" and (
            self.released_at is None or self.release_reason is None
        ):
            raise ValueError("closed session lease requires release metadata")
        if self.released_at is not None:
            _timestamp(self.released_at)
        return self


class CreateAIPlayerSessionCommand(_StrictSessionModel):
    command_id: str = Field(min_length=1)
    requested_environment_id: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    action_budget: int = Field(ge=0)
    token_budget: int | None = Field(default=None, ge=0)
    time_budget_seconds: float = Field(gt=0)
    active_task_ids: list[str] = Field(default_factory=list)
    last_capsule_id: str | None = Field(default=None, min_length=1)
    last_evidence_refs: list[EvidenceReferenceV1] = Field(default_factory=list)
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    session_id: str | None = Field(default=None, min_length=1)

    @field_validator("command_id", "requested_environment_id", "objective", "actor", "reason")
    @classmethod
    def validate_command_text(cls, value: str, info: Any) -> str:
        return _non_blank(value, str(info.field_name))

    @field_validator("active_task_ids")
    @classmethod
    def validate_task_ids(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value) or len(value) != len(set(value)):
            raise ValueError("active task ids must be unique, non-blank ids")
        return value


class AIPlayerSessionCommand(_StrictSessionModel):
    command_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    expected_version: int = Field(ge=1)
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    lease_holder: str | None = Field(default=None, min_length=1)
    lease_ttl_seconds: int = Field(
        default=DEFAULT_SESSION_LEASE_TTL_SECONDS,
        ge=15,
        le=3600,
    )

    @field_validator("command_id", "environment_id", "actor", "reason")
    @classmethod
    def validate_command_text(cls, value: str, info: Any) -> str:
        return _non_blank(value, str(info.field_name))


class AIPlayerSessionCheckpointCommand(AIPlayerSessionCommand):
    remaining_action_budget: int = Field(ge=0)
    remaining_token_budget: int | None = Field(default=None, ge=0)
    remaining_time_seconds: float = Field(ge=0)
    active_task_ids: list[str] = Field(default_factory=list)
    last_capsule_id: str | None = Field(default=None, min_length=1)
    last_evidence_refs: list[EvidenceReferenceV1] = Field(default_factory=list)


class AIPlayerSessionBudgetCorrectionCommand(AIPlayerSessionCommand):
    """Evidence-backed repair for a proven accounting defect; never gameplay budget expansion."""

    expected_remaining_action_budget: int = Field(ge=0)
    corrected_remaining_action_budget: int = Field(ge=0)
    evidence_refs: list[EvidenceReferenceV1] = Field(min_length=1)


class AIPlayerSessionHeartbeatCommand(AIPlayerSessionCommand):
    lease_id: str = Field(min_length=1)
    lease_holder: str = Field(min_length=1)


class AIPlayerSessionReconcileCommand(_StrictSessionModel):
    command_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    checked_at: str = Field(default_factory=utc_now, min_length=1)

    @field_validator("command_id", "environment_id", "actor", "reason")
    @classmethod
    def validate_command_text(cls, value: str, info: Any) -> str:
        return _non_blank(value, str(info.field_name))

    @field_validator("checked_at")
    @classmethod
    def validate_checked_at(cls, value: str) -> str:
        _timestamp(value)
        return value


class AIPlayerSessionError(RuntimeError):
    """Stable machine code plus a Chinese operator-facing explanation."""

    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


_ALLOWED_TRANSITIONS: dict[str, tuple[SessionState, set[SessionState]]] = {
    "start": ("running", {"created"}),
    "pause": ("paused", {"running"}),
    "resume": ("running", {"paused"}),
    "safe_stop": ("safe_stopped", {"created", "running", "paused"}),
    "complete": ("completed", {"running", "paused"}),
}
_EVENT_TYPES: dict[str, SessionEventType] = {
    "start": "started",
    "pause": "paused",
    "resume": "resumed",
    "safe_stop": "safe_stopped",
    "complete": "completed",
}


class AIPlayerSessionControl:
    """Transactional session state machine sharing the canonical AI-player database."""

    def __init__(self, player_store: AIPlayerStore) -> None:
        self.player_store = player_store

    @staticmethod
    def _json(model: BaseModel) -> str:
        return json.dumps(
            model.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _request_hash(operation: str, command: BaseModel) -> str:
        raw = json.dumps(
            {
                "operation": operation,
                "command": command.model_dump(mode="json", by_alias=True),
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _stable_id(prefix: str, command_id: str) -> str:
        digest = hashlib.sha256(command_id.encode("utf-8")).hexdigest()[:32]
        return f"{prefix}.{digest}"

    def _idempotent_result(
        self,
        connection: sqlite3.Connection,
        *,
        command_id: str,
        request_sha256: str,
    ) -> AIPlayerSessionV1 | None:
        row = connection.execute(
            """
            SELECT request_sha256, result_session_json
            FROM ai_player_session_lifecycle_events WHERE command_id=?
            """,
            (command_id,),
        ).fetchone()
        if row is None:
            return None
        if row["request_sha256"] != request_sha256:
            raise AIPlayerSessionError(
                "idempotency_conflict",
                "这个 command_id 已用于不同的会话命令，请生成新的 command_id。",
            )
        return AIPlayerSessionV1.model_validate_json(row["result_session_json"])

    def _existing_command_result(
        self,
        *,
        command_id: str,
        request_sha256: str,
    ) -> AIPlayerSessionV1 | None:
        """Fast replay path; every writer repeats this check inside its transaction."""

        with self.player_store._connection() as connection:
            return self._idempotent_result(
                connection,
                command_id=command_id,
                request_sha256=request_sha256,
            )

    def _validate_links(
        self,
        *,
        environment_id: str,
        active_task_ids: list[str],
        last_capsule_id: str | None,
        evidence_refs: list[EvidenceReferenceV1],
    ) -> None:
        for task_id in active_task_ids:
            if self.player_store.get_task(environment_id, task_id) is None:
                raise AIPlayerSessionError(
                    "task_environment_mismatch",
                    f"任务 {task_id} 不存在于当前环境叶节点，不能绑定到会话。",
                )
        if last_capsule_id is not None and self.player_store.get_session_capsule(
            environment_id, last_capsule_id
        ) is None:
            raise AIPlayerSessionError(
                "capsule_environment_mismatch",
                f"检查点 {last_capsule_id} 不存在于当前环境叶节点。",
            )
        if any(reference.environment_id != environment_id for reference in evidence_refs):
            raise AIPlayerSessionError(
                "evidence_environment_mismatch",
                "最近证据包含其他环境的数据，已拒绝跨环境绑定。",
            )
        try:
            self.player_store.resolve_evidence_references(evidence_refs)
        except (KeyError, ValueError) as exc:
            raise AIPlayerSessionError(
                "invalid_evidence_reference",
                f"最近证据无法由 canonical store 解析：{exc}",
            ) from exc

    def create_session(
        self,
        command: CreateAIPlayerSessionCommand,
    ) -> AIPlayerSessionV1:
        request_sha256 = self._request_hash("create", command)
        replay = self._existing_command_result(
            command_id=command.command_id,
            request_sha256=request_sha256,
        )
        if replay is not None:
            return replay
        try:
            selection = self.player_store.select_current_environment(
                command.requested_environment_id
            )
        except (KeyError, ValueError) as exc:
            raise AIPlayerSessionError(
                "unknown_environment",
                f"无法找到唯一的当前环境叶节点：{command.requested_environment_id}",
                status_code=404,
            ) from exc
        environment_id = selection.selected_environment_id
        self._validate_links(
            environment_id=environment_id,
            active_task_ids=command.active_task_ids,
            last_capsule_id=command.last_capsule_id,
            evidence_refs=command.last_evidence_refs,
        )
        session_id = command.session_id or self._stable_id(
            "ai-player-session", command.command_id
        )
        timestamp = utc_now()
        session = AIPlayerSessionV1(
            id=session_id,
            requested_environment_id=command.requested_environment_id,
            environment_id=environment_id,
            lineage_path=selection.lineage_path,
            objective=command.objective,
            action_budget=command.action_budget,
            remaining_action_budget=command.action_budget,
            token_budget=command.token_budget,
            remaining_token_budget=command.token_budget,
            time_budget_seconds=command.time_budget_seconds,
            remaining_time_seconds=command.time_budget_seconds,
            active_task_ids=command.active_task_ids,
            last_capsule_id=command.last_capsule_id,
            last_evidence_refs=command.last_evidence_refs,
            created_at=timestamp,
            updated_at=timestamp,
        )
        event = AIPlayerSessionEventV1(
            id=self._stable_id("ai-player-session-event", command.command_id),
            session_id=session.id,
            environment_id=environment_id,
            event_type="created",
            actor=command.actor,
            reason=command.reason,
            command_id=command.command_id,
            previous_state=None,
            new_state="created",
            previous_version=0,
            new_version=1,
            timestamp=timestamp,
        )
        session_json = self._json(session)
        with self.player_store._write_lock, self.player_store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._idempotent_result(
                connection,
                command_id=command.command_id,
                request_sha256=request_sha256,
            )
            if replay is not None:
                return replay
            if connection.execute(
                "SELECT 1 FROM ai_player_sessions WHERE id=?", (session.id,)
            ).fetchone():
                raise AIPlayerSessionError(
                    "session_id_conflict",
                    f"会话 ID 已存在：{session.id}",
                )
            connection.execute(
                """
                INSERT INTO ai_player_sessions(
                    id, requested_environment_id, environment_id, objective, state,
                    version, body_json, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    session.id,
                    session.requested_environment_id,
                    session.environment_id,
                    session.objective,
                    session.state,
                    session.version,
                    session_json,
                    session.created_at,
                    session.updated_at,
                ),
            )
            self._insert_event(connection, event, request_sha256, session_json)
            if session.last_evidence_refs:
                self.player_store._record_evidence(
                    connection,
                    environment_id,
                    "ai_player_session",
                    session.id,
                    str(session.version),
                    session.last_evidence_refs,
                )
        return session

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        event: AIPlayerSessionEventV1,
        request_sha256: str,
        session_json: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO ai_player_session_lifecycle_events(
                id, session_id, environment_id, event_type, actor, reason,
                command_id, request_sha256, previous_version, new_version,
                body_json, result_session_json, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                event.id,
                event.session_id,
                event.environment_id,
                event.event_type,
                event.actor,
                event.reason,
                event.command_id,
                request_sha256,
                event.previous_version,
                event.new_version,
                self._json(event),
                session_json,
                event.timestamp,
            ),
        )
        from .operation_memory import append_runtime_telemetry_locked

        append_runtime_telemetry_locked(
            connection,
            event_id=f"runtime-telemetry.session.{event.command_id}",
            environment_id=event.environment_id,
            session_id=event.session_id,
            event_type=f"session.{event.event_type}",
            provider="session-control",
            action_count=0,
            payload={
                "source_event_id": event.id,
                "actor": event.actor,
                "reason": event.reason,
                "previous_state": event.previous_state,
                "new_state": event.new_state,
                "previous_version": event.previous_version,
                "new_version": event.new_version,
            },
            occurred_at=event.timestamp,
        )

    def _active_lease_locked(
        self,
        connection: sqlite3.Connection,
        session_id: str,
    ) -> AIPlayerSessionLeaseV1 | None:
        row = connection.execute(
            "SELECT body_json FROM ai_player_session_leases "
            "WHERE session_id=? AND status='active'",
            (session_id,),
        ).fetchone()
        return AIPlayerSessionLeaseV1.model_validate_json(row["body_json"]) if row else None

    @staticmethod
    def _lease_matches_session(
        session: AIPlayerSessionV1,
        lease: AIPlayerSessionLeaseV1 | None,
        checked_at: str,
    ) -> bool:
        return bool(
            lease is not None
            and lease.status == "active"
            and session.state == "running"
            and session.lease_id == lease.id
            and session.lease_holder == lease.holder
            and session.lease_acquired_at == lease.acquired_at
            and session.last_heartbeat_at == lease.last_heartbeat_at
            and session.lease_expires_at == lease.expires_at
            and _timestamp(lease.expires_at) > _timestamp(checked_at)
        )

    def _save_lease_locked(
        self,
        connection: sqlite3.Connection,
        lease: AIPlayerSessionLeaseV1,
    ) -> None:
        body_json = self._json(lease)
        existing = connection.execute(
            "SELECT 1 FROM ai_player_session_leases WHERE id=?",
            (lease.id,),
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO ai_player_session_leases(
                    id, session_id, environment_id, holder, status,
                    acquired_at, last_heartbeat_at, expires_at, body_json, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    lease.id,
                    lease.session_id,
                    lease.environment_id,
                    lease.holder,
                    lease.status,
                    lease.acquired_at,
                    lease.last_heartbeat_at,
                    lease.expires_at,
                    body_json,
                    lease.last_heartbeat_at if lease.status == "active" else lease.released_at,
                ),
            )
            return
        connection.execute(
            """
            UPDATE ai_player_session_leases
            SET status=?, last_heartbeat_at=?, expires_at=?, body_json=?, updated_at=?
            WHERE id=?
            """,
            (
                lease.status,
                lease.last_heartbeat_at,
                lease.expires_at,
                body_json,
                lease.last_heartbeat_at if lease.status == "active" else lease.released_at,
                lease.id,
            ),
        )

    def _close_lease_locked(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        *,
        checked_at: str,
        reason: str,
        force_expired: bool = False,
    ) -> AIPlayerSessionLeaseV1 | None:
        lease = self._active_lease_locked(connection, session_id)
        if lease is None:
            return None
        expired = force_expired or _timestamp(lease.expires_at) <= _timestamp(checked_at)
        closed = lease.model_copy(
            update={
                "status": "expired" if expired else "released",
                "released_at": checked_at,
                "release_reason": reason,
            }
        )
        self._save_lease_locked(connection, closed)
        return closed

    def _reconcile_stale_running_locked(
        self,
        connection: sqlite3.Connection,
        command: AIPlayerSessionReconcileCommand,
    ) -> list[AIPlayerSessionV1]:
        rows = connection.execute(
            """
            SELECT body_json FROM ai_player_sessions
            WHERE environment_id=? AND state='running'
            ORDER BY updated_at, id
            """,
            (command.environment_id,),
        ).fetchall()
        reconciled: list[AIPlayerSessionV1] = []
        for row in rows:
            current = AIPlayerSessionV1.model_validate_json(row["body_json"])
            lease = self._active_lease_locked(connection, current.id)
            if self._lease_matches_session(current, lease, command.checked_at):
                continue
            self._close_lease_locked(
                connection,
                current.id,
                checked_at=command.checked_at,
                reason="会话租约已过期，由生命周期恢复器收口。",
                force_expired=True,
            )
            reason = f"检测到无有效 worker 租约的 running 会话；{command.reason}"
            values = current.model_dump(mode="python", by_alias=False)
            values.update(
                state="paused",
                version=current.version + 1,
                pause_reason=reason,
                paused_at=command.checked_at,
                lease_id=None,
                lease_holder=None,
                lease_acquired_at=None,
                last_heartbeat_at=None,
                lease_expires_at=None,
                updated_at=command.checked_at,
            )
            updated = AIPlayerSessionV1.model_validate(values)
            session_json = self._json(updated)
            changed = connection.execute(
                """
                UPDATE ai_player_sessions SET state=?, version=?, body_json=?, updated_at=?
                WHERE id=? AND environment_id=? AND version=? AND state='running'
                """,
                (
                    updated.state,
                    updated.version,
                    session_json,
                    updated.updated_at,
                    updated.id,
                    updated.environment_id,
                    current.version,
                ),
            ).rowcount
            if changed != 1:
                raise AIPlayerSessionError(
                    "version_conflict",
                    "陈旧会话收口期间发生并发更新，请重新执行恢复检查。",
                )
            suffix = hashlib.sha256(
                f"{current.id}:{current.version}".encode("utf-8")
            ).hexdigest()[:16]
            command_id = f"{command.command_id}.stale.{suffix}"
            event_command = AIPlayerSessionCommand(
                command_id=command_id,
                environment_id=current.environment_id,
                expected_version=current.version,
                actor=command.actor,
                reason=reason,
            )
            event = AIPlayerSessionEventV1(
                id=self._stable_id("ai-player-session-event", command_id),
                session_id=updated.id,
                environment_id=updated.environment_id,
                event_type="stale_reconciled",
                actor=command.actor,
                reason=reason,
                command_id=command_id,
                previous_state=current.state,
                new_state=updated.state,
                previous_version=current.version,
                new_version=updated.version,
                timestamp=command.checked_at,
            )
            self._insert_event(
                connection,
                event,
                self._request_hash("stale_reconcile", event_command),
                session_json,
            )
            reconciled.append(updated)
        return reconciled

    def _acquire_lease_locked(
        self,
        connection: sqlite3.Connection,
        session: AIPlayerSessionV1,
        command: AIPlayerSessionCommand,
        timestamp: str,
    ) -> AIPlayerSessionLeaseV1:
        active = connection.execute(
            "SELECT session_id FROM ai_player_session_leases "
            "WHERE environment_id=? AND status='active'",
            (session.environment_id,),
        ).fetchone()
        if active is not None:
            raise AIPlayerSessionError(
                "environment_session_lease_conflict",
                f"当前环境已有会话 {active['session_id']} 持有有效运行租约。",
            )
        lease = AIPlayerSessionLeaseV1(
            id=self._stable_id("ai-player-session-lease", command.command_id),
            session_id=session.id,
            environment_id=session.environment_id,
            holder=command.lease_holder or command.actor,
            acquired_at=timestamp,
            last_heartbeat_at=timestamp,
            expires_at=_expires_at(timestamp, command.lease_ttl_seconds),
        )
        self._save_lease_locked(connection, lease)
        return lease

    def _transition(
        self,
        session_id: str,
        operation: str,
        command: AIPlayerSessionCommand,
    ) -> AIPlayerSessionV1:
        target_state, allowed_from = _ALLOWED_TRANSITIONS[operation]
        request_sha256 = self._request_hash(operation, command)
        timestamp = utc_now()
        with self.player_store._write_lock, self.player_store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._idempotent_result(
                connection,
                command_id=command.command_id,
                request_sha256=request_sha256,
            )
            if replay is not None:
                return replay
            row = connection.execute(
                "SELECT environment_id, version, body_json FROM ai_player_sessions WHERE id=?",
                (session_id,),
            ).fetchone()
            if row is None or row["environment_id"] != command.environment_id:
                raise AIPlayerSessionError(
                    "session_not_found",
                    "当前环境中没有这个 AI 玩家会话。",
                    status_code=404,
                )
            current = AIPlayerSessionV1.model_validate_json(row["body_json"])
            if current.version != command.expected_version:
                raise AIPlayerSessionError(
                    "version_conflict",
                    f"会话已更新：期望版本 {command.expected_version}，当前版本 {current.version}。",
                )
            if current.state in {"safe_stopped", "completed"}:
                raise AIPlayerSessionError(
                    "session_terminal",
                    "该会话已经终止；如需继续游玩，请创建新会话。",
                )
            if current.state not in allowed_from:
                raise AIPlayerSessionError(
                    "invalid_transition",
                    f"会话处于 {current.state}，不能执行 {operation}。",
                )
            lease: AIPlayerSessionLeaseV1 | None = None
            if operation in {"start", "resume"}:
                self._reconcile_stale_running_locked(
                    connection,
                    AIPlayerSessionReconcileCommand(
                        command_id=f"{command.command_id}.preflight",
                        environment_id=current.environment_id,
                        actor=command.actor,
                        reason=f"{operation} 前自动检查环境中的陈旧运行会话。",
                        checked_at=timestamp,
                    ),
                )
                lease = self._acquire_lease_locked(
                    connection,
                    current,
                    command,
                    timestamp,
                )
            else:
                self._close_lease_locked(
                    connection,
                    current.id,
                    checked_at=timestamp,
                    reason=command.reason,
                )
            values = current.model_dump(mode="python", by_alias=False)
            values.update(state=target_state, version=current.version + 1, updated_at=timestamp)
            if operation == "start":
                values["started_at"] = timestamp
            elif operation == "pause":
                values.update(paused_at=timestamp, pause_reason=command.reason)
            elif operation == "resume":
                values["resumed_at"] = timestamp
            elif operation == "safe_stop":
                values.update(safe_stopped_at=timestamp, safe_stop_reason=command.reason)
            elif operation == "complete":
                values["completed_at"] = timestamp
            if lease is not None:
                values.update(
                    lease_id=lease.id,
                    lease_holder=lease.holder,
                    lease_acquired_at=lease.acquired_at,
                    last_heartbeat_at=lease.last_heartbeat_at,
                    lease_expires_at=lease.expires_at,
                )
            else:
                values.update(
                    lease_id=None,
                    lease_holder=None,
                    lease_acquired_at=None,
                    last_heartbeat_at=None,
                    lease_expires_at=None,
                )
            updated = AIPlayerSessionV1.model_validate(values)
            session_json = self._json(updated)
            changed = connection.execute(
                """
                UPDATE ai_player_sessions SET state=?, version=?, body_json=?, updated_at=?
                WHERE id=? AND environment_id=? AND version=?
                """,
                (
                    updated.state,
                    updated.version,
                    session_json,
                    updated.updated_at,
                    updated.id,
                    updated.environment_id,
                    current.version,
                ),
            ).rowcount
            if changed != 1:
                raise AIPlayerSessionError(
                    "version_conflict",
                    "会话刚刚被其他控制者更新，请刷新后重试。",
                )
            event = AIPlayerSessionEventV1(
                id=self._stable_id("ai-player-session-event", command.command_id),
                session_id=updated.id,
                environment_id=updated.environment_id,
                event_type=_EVENT_TYPES[operation],
                actor=command.actor,
                reason=command.reason,
                command_id=command.command_id,
                previous_state=current.state,
                new_state=updated.state,
                previous_version=current.version,
                new_version=updated.version,
                timestamp=timestamp,
            )
            self._insert_event(connection, event, request_sha256, session_json)
        return updated

    def start(self, session_id: str, command: AIPlayerSessionCommand) -> AIPlayerSessionV1:
        return self._transition(session_id, "start", command)

    def pause(self, session_id: str, command: AIPlayerSessionCommand) -> AIPlayerSessionV1:
        return self._transition(session_id, "pause", command)

    def resume(self, session_id: str, command: AIPlayerSessionCommand) -> AIPlayerSessionV1:
        return self._transition(session_id, "resume", command)

    def safe_stop(
        self, session_id: str, command: AIPlayerSessionCommand
    ) -> AIPlayerSessionV1:
        return self._transition(session_id, "safe_stop", command)

    def complete(self, session_id: str, command: AIPlayerSessionCommand) -> AIPlayerSessionV1:
        return self._transition(session_id, "complete", command)

    def heartbeat(
        self,
        session_id: str,
        command: AIPlayerSessionHeartbeatCommand,
    ) -> AIPlayerSessionV1:
        """Renew the active worker lease and append an immutable heartbeat event."""

        request_sha256 = self._request_hash("heartbeat", command)
        timestamp = utc_now()
        with self.player_store._write_lock, self.player_store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._idempotent_result(
                connection,
                command_id=command.command_id,
                request_sha256=request_sha256,
            )
            if replay is not None:
                return replay
            row = connection.execute(
                "SELECT environment_id, body_json FROM ai_player_sessions WHERE id=?",
                (session_id,),
            ).fetchone()
            if row is None or row["environment_id"] != command.environment_id:
                raise AIPlayerSessionError(
                    "session_not_found",
                    "当前环境中没有这个 AI 玩家会话。",
                    status_code=404,
                )
            current = AIPlayerSessionV1.model_validate_json(row["body_json"])
            if current.version != command.expected_version:
                raise AIPlayerSessionError(
                    "version_conflict",
                    f"会话已更新：期望版本 {command.expected_version}，当前版本 {current.version}。",
                )
            if current.state != "running":
                raise AIPlayerSessionError(
                    "session_not_running",
                    "只有运行中的会话可以续租 worker 心跳。",
                )
            lease = self._active_lease_locked(connection, current.id)
            if (
                lease is None
                or lease.id != command.lease_id
                or lease.holder != command.lease_holder
                or not self._lease_matches_session(current, lease, timestamp)
            ):
                raise AIPlayerSessionError(
                    "session_lease_stale",
                    "会话 worker 租约缺失、已过期或与心跳凭据不一致。",
                )
            elapsed_seconds = max(
                0.0,
                (_timestamp(timestamp) - _timestamp(current.updated_at)).total_seconds(),
            )
            renewed = lease.model_copy(
                update={
                    "last_heartbeat_at": timestamp,
                    "expires_at": _expires_at(timestamp, command.lease_ttl_seconds),
                }
            )
            self._save_lease_locked(connection, renewed)
            values = current.model_dump(mode="python", by_alias=False)
            values.update(
                version=current.version + 1,
                remaining_time_seconds=max(
                    0.0,
                    current.remaining_time_seconds - elapsed_seconds,
                ),
                last_heartbeat_at=renewed.last_heartbeat_at,
                lease_expires_at=renewed.expires_at,
                updated_at=timestamp,
            )
            updated = AIPlayerSessionV1.model_validate(values)
            session_json = self._json(updated)
            changed = connection.execute(
                """
                UPDATE ai_player_sessions SET version=?, body_json=?, updated_at=?
                WHERE id=? AND environment_id=? AND version=? AND state='running'
                """,
                (
                    updated.version,
                    session_json,
                    updated.updated_at,
                    updated.id,
                    updated.environment_id,
                    current.version,
                ),
            ).rowcount
            if changed != 1:
                raise AIPlayerSessionError(
                    "version_conflict",
                    "会话心跳续租期间发生并发更新，请刷新后重试。",
                )
            event = AIPlayerSessionEventV1(
                id=self._stable_id("ai-player-session-event", command.command_id),
                session_id=updated.id,
                environment_id=updated.environment_id,
                event_type="heartbeat",
                actor=command.actor,
                reason=command.reason,
                command_id=command.command_id,
                previous_state=current.state,
                new_state=updated.state,
                previous_version=current.version,
                new_version=updated.version,
                timestamp=timestamp,
            )
            self._insert_event(connection, event, request_sha256, session_json)
        return updated

    def reconcile_stale_sessions(
        self,
        command: AIPlayerSessionReconcileCommand,
    ) -> list[AIPlayerSessionV1]:
        """Pause every running session without a live canonical lease, without device I/O."""

        if self.player_store.get_environment(command.environment_id) is None:
            raise AIPlayerSessionError(
                "unknown_environment",
                "无法为不存在的环境执行会话恢复检查。",
                status_code=404,
            )
        with self.player_store._write_lock, self.player_store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._reconcile_stale_running_locked(connection, command)

    def get_session_lease(
        self,
        environment_id: str,
        session_id: str,
    ) -> AIPlayerSessionLeaseV1 | None:
        with self.player_store._connection() as connection:
            row = connection.execute(
                """
                SELECT body_json FROM ai_player_session_leases
                WHERE environment_id=? AND session_id=?
                ORDER BY acquired_at DESC, id DESC LIMIT 1
                """,
                (environment_id, session_id),
            ).fetchone()
        return AIPlayerSessionLeaseV1.model_validate_json(row["body_json"]) if row else None

    def get_session(
        self,
        environment_id: str,
        session_id: str,
    ) -> AIPlayerSessionV1 | None:
        with self.player_store._connection() as connection:
            row = connection.execute(
                """
                SELECT body_json FROM ai_player_sessions
                WHERE environment_id=? AND id=?
                """,
                (environment_id, session_id),
            ).fetchone()
        return AIPlayerSessionV1.model_validate_json(row["body_json"]) if row else None

    def list_sessions(
        self,
        environment_id: str,
        *,
        limit: int = 100,
    ) -> list[AIPlayerSessionV1]:
        with self.player_store._connection() as connection:
            rows = connection.execute(
                """
                SELECT body_json FROM ai_player_sessions
                WHERE environment_id=?
                ORDER BY updated_at DESC, id DESC LIMIT ?
                """,
                (environment_id, max(1, min(limit, 500))),
            ).fetchall()
        return [AIPlayerSessionV1.model_validate_json(row["body_json"]) for row in rows]

    def list_events(
        self,
        environment_id: str,
        session_id: str,
    ) -> list[AIPlayerSessionEventV1]:
        if self.get_session(environment_id, session_id) is None:
            return []
        with self.player_store._connection() as connection:
            rows = connection.execute(
                """
                SELECT body_json FROM ai_player_session_lifecycle_events
                WHERE environment_id=? AND session_id=? ORDER BY new_version, created_at, id
                """,
                (environment_id, session_id),
            ).fetchall()
        return [AIPlayerSessionEventV1.model_validate_json(row["body_json"]) for row in rows]

    def assert_session_lease_active(
        self,
        environment_id: str,
        session_id: str,
    ) -> AIPlayerSessionV1:
        """Validate the worker lease for new or already-reserved device work."""
        session = self.get_session(environment_id, session_id)
        if session is None:
            raise AIPlayerSessionError(
                "session_not_found",
                "当前环境中没有这个 AI 玩家会话。",
                status_code=404,
            )
        if session.state != "running":
            code = "session_terminal" if session.state in {"safe_stopped", "completed"} else "session_not_running"
            message = (
                "该会话已经终止，不能再执行会话动作。"
                if code == "session_terminal"
                else "AI 玩家会话尚未运行或已暂停。"
            )
            raise AIPlayerSessionError(code, message)
        checked_at_text = utc_now()
        with self.player_store._connection() as connection:
            lease = self._active_lease_locked(connection, session.id)
        if not self._lease_matches_session(session, lease, checked_at_text):
            raise AIPlayerSessionError(
                "session_lease_stale",
                "运行会话没有有效 worker 租约；请先执行陈旧会话恢复检查。",
            )
        updated_at = datetime.fromisoformat(session.updated_at.replace("Z", "+00:00"))
        checked_at = datetime.fromisoformat(checked_at_text.replace("Z", "+00:00"))
        elapsed_seconds = max(0.0, (checked_at - updated_at).total_seconds())
        effective_remaining_time = max(
            0.0,
            session.remaining_time_seconds - elapsed_seconds,
        )
        if effective_remaining_time <= 0:
            raise AIPlayerSessionError("budget_exhausted", "会话的时间预算已经耗尽。")
        if session.remaining_token_budget is not None and session.remaining_token_budget <= 0:
            raise AIPlayerSessionError("budget_exhausted", "会话的 token 预算已经耗尽。")
        return session.model_copy(
            update={"remaining_time_seconds": effective_remaining_time}
        )

    def assert_session_can_act(self, environment_id: str, session_id: str) -> AIPlayerSessionV1:
        session = self.assert_session_lease_active(environment_id, session_id)
        if session.remaining_action_budget <= 0:
            raise AIPlayerSessionError("budget_exhausted", "会话的动作预算已经耗尽。")
        return session

    def reserve_action(
        self,
        environment_id: str,
        session_id: str,
        *,
        command_id: str,
        actor: str,
        reason: str,
    ) -> AIPlayerSessionV1:
        """Atomically consume one action attempt before a bound adapter call."""

        session = self.assert_session_can_act(environment_id, session_id)
        return self.checkpoint(
            session_id,
            AIPlayerSessionCheckpointCommand(
                command_id=command_id,
                environment_id=environment_id,
                expected_version=session.version,
                actor=actor,
                reason=reason,
                remaining_action_budget=session.remaining_action_budget - 1,
                remaining_token_budget=session.remaining_token_budget,
                remaining_time_seconds=session.remaining_time_seconds,
                active_task_ids=session.active_task_ids,
                last_capsule_id=session.last_capsule_id,
                last_evidence_refs=session.last_evidence_refs,
            ),
        )

    def checkpoint(
        self,
        session_id: str,
        command: AIPlayerSessionCheckpointCommand,
    ) -> AIPlayerSessionV1:
        """Persist orchestrator progress without invoking a device or gameplay adapter."""

        request_sha256 = self._request_hash("checkpoint", command)
        replay = self._existing_command_result(
            command_id=command.command_id,
            request_sha256=request_sha256,
        )
        if replay is not None:
            return replay
        self._validate_links(
            environment_id=command.environment_id,
            active_task_ids=command.active_task_ids,
            last_capsule_id=command.last_capsule_id,
            evidence_refs=command.last_evidence_refs,
        )
        timestamp = utc_now()
        with self.player_store._write_lock, self.player_store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._idempotent_result(
                connection,
                command_id=command.command_id,
                request_sha256=request_sha256,
            )
            if replay is not None:
                return replay
            row = connection.execute(
                "SELECT environment_id, body_json FROM ai_player_sessions WHERE id=?",
                (session_id,),
            ).fetchone()
            if row is None or row["environment_id"] != command.environment_id:
                raise AIPlayerSessionError(
                    "session_not_found", "当前环境中没有这个 AI 玩家会话。", status_code=404
                )
            current = AIPlayerSessionV1.model_validate_json(row["body_json"])
            if current.version != command.expected_version:
                raise AIPlayerSessionError(
                    "version_conflict",
                    f"会话已更新：期望版本 {command.expected_version}，当前版本 {current.version}。",
                )
            if current.state in {"safe_stopped", "completed"}:
                raise AIPlayerSessionError(
                    "session_terminal", "该会话已经终止，不能再写入会话进度。"
                )
            if current.state != "running":
                raise AIPlayerSessionError(
                    "session_not_running", "只有运行中的 AI 玩家会话可以写入进度。"
                )
            lease = self._active_lease_locked(connection, current.id)
            if not self._lease_matches_session(current, lease, timestamp):
                raise AIPlayerSessionError(
                    "session_lease_stale",
                    "运行会话没有有效 worker 租约，不能写入进度或预约动作。",
                )
            if command.remaining_action_budget > current.remaining_action_budget:
                raise AIPlayerSessionError("budget_increase_forbidden", "动作预算不能在会话中途增加。")
            if command.remaining_time_seconds > current.remaining_time_seconds:
                raise AIPlayerSessionError("budget_increase_forbidden", "时间预算不能在会话中途增加。")
            if (current.remaining_token_budget is None) != (
                command.remaining_token_budget is None
            ) or (
                current.remaining_token_budget is not None
                and command.remaining_token_budget is not None
                and command.remaining_token_budget > current.remaining_token_budget
            ):
                raise AIPlayerSessionError("budget_increase_forbidden", "token 预算不能在会话中途增加。")
            values = current.model_dump(mode="python", by_alias=False)
            values.update(
                version=current.version + 1,
                remaining_action_budget=command.remaining_action_budget,
                remaining_token_budget=command.remaining_token_budget,
                remaining_time_seconds=command.remaining_time_seconds,
                active_task_ids=command.active_task_ids,
                last_capsule_id=command.last_capsule_id,
                last_evidence_refs=command.last_evidence_refs,
                updated_at=timestamp,
            )
            updated = AIPlayerSessionV1.model_validate(values)
            session_json = self._json(updated)
            changed = connection.execute(
                """
                UPDATE ai_player_sessions SET version=?, body_json=?, updated_at=?
                WHERE id=? AND environment_id=? AND version=?
                """,
                (
                    updated.version,
                    session_json,
                    updated.updated_at,
                    updated.id,
                    updated.environment_id,
                    current.version,
                ),
            ).rowcount
            if changed != 1:
                raise AIPlayerSessionError(
                    "version_conflict", "会话刚刚被其他控制者更新，请刷新后重试。"
                )
            event = AIPlayerSessionEventV1(
                id=self._stable_id("ai-player-session-event", command.command_id),
                session_id=updated.id,
                environment_id=updated.environment_id,
                event_type="checkpointed",
                actor=command.actor,
                reason=command.reason,
                command_id=command.command_id,
                previous_state=current.state,
                new_state=updated.state,
                previous_version=current.version,
                new_version=updated.version,
                timestamp=timestamp,
            )
            self._insert_event(connection, event, request_sha256, session_json)
            if updated.last_evidence_refs:
                self.player_store._record_evidence(
                    connection,
                    updated.environment_id,
                    "ai_player_session",
                    updated.id,
                    str(updated.version),
                    updated.last_evidence_refs,
                )
        return updated

    def correct_action_budget(
        self,
        session_id: str,
        command: AIPlayerSessionBudgetCorrectionCommand,
    ) -> AIPlayerSessionV1:
        """Repair an accounting defect with immutable evidence and optimistic locking."""

        request_sha256 = self._request_hash("correct_action_budget", command)
        replay = self._existing_command_result(
            command_id=command.command_id,
            request_sha256=request_sha256,
        )
        if replay is not None:
            return replay
        self._validate_links(
            environment_id=command.environment_id,
            active_task_ids=[],
            last_capsule_id=None,
            evidence_refs=command.evidence_refs,
        )
        timestamp = utc_now()
        with self.player_store._write_lock, self.player_store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._idempotent_result(
                connection,
                command_id=command.command_id,
                request_sha256=request_sha256,
            )
            if replay is not None:
                return replay
            row = connection.execute(
                "SELECT environment_id, body_json FROM ai_player_sessions WHERE id=?",
                (session_id,),
            ).fetchone()
            if row is None or row["environment_id"] != command.environment_id:
                raise AIPlayerSessionError(
                    "session_not_found", "当前环境中没有这个 AI 玩家会话。", status_code=404
                )
            current = AIPlayerSessionV1.model_validate_json(row["body_json"])
            if current.version != command.expected_version:
                raise AIPlayerSessionError(
                    "version_conflict",
                    f"会话已更新：期望版本 {command.expected_version}，当前版本 {current.version}。",
                )
            if current.state != "running":
                raise AIPlayerSessionError(
                    "session_not_running", "只有运行中的 AI 玩家会话可以修正动作账本。"
                )
            lease = self._active_lease_locked(connection, current.id)
            if not self._lease_matches_session(current, lease, timestamp):
                raise AIPlayerSessionError(
                    "session_lease_stale", "运行会话没有有效 worker 租约，不能修正动作账本。"
                )
            if current.remaining_action_budget != command.expected_remaining_action_budget:
                raise AIPlayerSessionError(
                    "budget_correction_conflict",
                    "当前剩余动作预算与纠错请求记录的旧值不一致。",
                )
            corrected = command.corrected_remaining_action_budget
            if corrected <= current.remaining_action_budget or corrected > current.action_budget:
                raise AIPlayerSessionError(
                    "invalid_budget_correction",
                    "纠错值必须高于当前剩余动作预算，且不能超过会话初始预算。",
                )
            values = current.model_dump(mode="python", by_alias=False)
            values.update(
                version=current.version + 1,
                remaining_action_budget=corrected,
                updated_at=timestamp,
            )
            updated = AIPlayerSessionV1.model_validate(values)
            session_json = self._json(updated)
            changed = connection.execute(
                """
                UPDATE ai_player_sessions SET version=?, body_json=?, updated_at=?
                WHERE id=? AND environment_id=? AND version=?
                """,
                (
                    updated.version,
                    session_json,
                    updated.updated_at,
                    updated.id,
                    updated.environment_id,
                    current.version,
                ),
            ).rowcount
            if changed != 1:
                raise AIPlayerSessionError(
                    "version_conflict", "会话刚刚被其他控制者更新，请刷新后重试。"
                )
            event = AIPlayerSessionEventV1(
                id=self._stable_id("ai-player-session-event", command.command_id),
                session_id=updated.id,
                environment_id=updated.environment_id,
                event_type="checkpointed",
                actor=command.actor,
                reason=command.reason,
                command_id=command.command_id,
                previous_state=current.state,
                new_state=updated.state,
                previous_version=current.version,
                new_version=updated.version,
                timestamp=timestamp,
            )
            self._insert_event(connection, event, request_sha256, session_json)
            self.player_store._record_evidence(
                connection,
                updated.environment_id,
                "ai_player_session",
                updated.id,
                str(updated.version),
                command.evidence_refs,
            )
        return updated


__all__ = [
    "DEFAULT_SESSION_LEASE_TTL_SECONDS",
    "AIPlayerSessionBudgetCorrectionCommand",
    "AIPlayerSessionCheckpointCommand",
    "AIPlayerSessionCommand",
    "AIPlayerSessionControl",
    "AIPlayerSessionError",
    "AIPlayerSessionEventV1",
    "AIPlayerSessionHeartbeatCommand",
    "AIPlayerSessionLeaseV1",
    "AIPlayerSessionReconcileCommand",
    "AIPlayerSessionV1",
    "CreateAIPlayerSessionCommand",
]
