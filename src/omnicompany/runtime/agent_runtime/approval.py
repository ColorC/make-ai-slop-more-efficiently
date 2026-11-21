"""Omni-owned durable human gate in front of an external AgentRuntime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any

from omnicompany.core.config import resolve_runtime_data_dir
from omnicompany.runtime.buses import HumanBus, HumanKind
from omnicompany.runtime.buses.human_bus import (
    HumanTarget,
    QuestionStatus,
    TARGET_ANY_HUMAN,
)

from .client import AgentRuntimeProcessClient, RuntimeEventSink, RuntimeMessage


_APPROVE_ANSWERS = {"approve", "approved", "yes", "y", "同意", "批准"}
_REJECT_ANSWERS = {"reject", "rejected", "no", "n", "拒绝", "驳回"}


class AgentRuntimeApprovalError(RuntimeError):
    """A durable runtime approval cannot be advanced safely."""


@dataclass(frozen=True)
class AwaitingHumanRun:
    approval_id: str
    checkpoint_ref: str
    event: RuntimeMessage


class _ApprovalStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtime_approvals (
                    approval_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE,
                    request_json TEXT NOT NULL,
                    action TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    state TEXT NOT NULL,
                    result_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_approval_state
                    ON runtime_approvals(state);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        return connection

    def create(
        self,
        approval_id: str,
        request: RuntimeMessage,
        *,
        action: str,
        reason: str,
    ) -> None:
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO runtime_approvals
                   (approval_id, run_id, request_json, action, reason, state,
                    result_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'awaiting_human', NULL, ?, ?)""",
                (
                    approval_id,
                    request["run_id"],
                    json.dumps(request, ensure_ascii=False, separators=(",", ":")),
                    action,
                    reason,
                    now,
                    now,
                ),
            )

    def get(self, approval_id: str) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM runtime_approvals WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()

    def claim(self, approval_id: str) -> tuple[sqlite3.Row, bool]:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM runtime_approvals WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            if row is None:
                raise AgentRuntimeApprovalError(f"approval checkpoint not found: {approval_id}")
            claimed = False
            if row["state"] == "awaiting_human":
                cursor = connection.execute(
                    "UPDATE runtime_approvals SET state = 'running', updated_at = ? "
                    "WHERE approval_id = ? AND state = 'awaiting_human'",
                    (time.time(), approval_id),
                )
                claimed = cursor.rowcount == 1
                row = connection.execute(
                    "SELECT * FROM runtime_approvals WHERE approval_id = ?",
                    (approval_id,),
                ).fetchone()
            return row, claimed

    def finish(self, approval_id: str, state: str, result: RuntimeMessage) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE runtime_approvals
                   SET state = ?, result_json = ?, updated_at = ?
                   WHERE approval_id = ?""",
                (
                    state,
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    time.time(),
                    approval_id,
                ),
            )

    def fail(self, approval_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE runtime_approvals SET state = 'failed', updated_at = ? "
                "WHERE approval_id = ? AND state = 'running'",
                (time.time(), approval_id),
            )


class AgentRuntimeApprovalCoordinator:
    """Persist approval before launch and resume through the same runtime port later."""

    def __init__(
        self,
        client: AgentRuntimeProcessClient,
        *,
        human_bus: HumanBus | None = None,
        checkpoint_path: str | Path | None = None,
    ) -> None:
        self.client = client
        self.human_bus = human_bus or HumanBus()
        path = Path(checkpoint_path) if checkpoint_path else (
            resolve_runtime_data_dir("agent_runtime") / "approvals.db"
        )
        self._store = _ApprovalStore(path.resolve())

    def request_approval(
        self,
        request: RuntimeMessage,
        *,
        action: str,
        reason: str,
        target: HumanTarget | None = None,
    ) -> AwaitingHumanRun:
        self.client.validate(request)
        if request.get("kind") != "run.start":
            raise ValueError("approval requires a run.start message")
        if not action.strip() or not reason.strip():
            raise ValueError("approval action and reason must be non-blank")
        question = self.human_bus.ask(
            f"Approve AgentRuntime action: {action}?",
            kind=HumanKind.HUMAN_BLOCKING,
            source="agent_runtime",
            target=target or HumanTarget(kind=TARGET_ANY_HUMAN),
            context={
                "schema_version": 1,
                "run_id": request["run_id"],
                "request_id": request["request_id"],
                "action": action,
                "reason": reason,
                "capability_grant_id": request["capability_grant"]["grant_id"],
            },
        )
        self._store.create(question.id, request, action=action, reason=reason)
        return self._awaiting(question.id, str(request["run_id"]))

    async def resume(
        self,
        approval_id: str,
        event_sink: RuntimeEventSink | None = None,
    ) -> AwaitingHumanRun | RuntimeMessage:
        checkpoint = self._store.get(approval_id)
        if checkpoint is None:
            raise AgentRuntimeApprovalError(f"approval checkpoint not found: {approval_id}")
        request = json.loads(checkpoint["request_json"])
        state = str(checkpoint["state"])
        if state in {"completed", "rejected"}:
            return json.loads(checkpoint["result_json"])
        if state in {"running", "failed"}:
            raise AgentRuntimeApprovalError(
                f"approval {approval_id} is {state}; automatic replay is forbidden"
            )

        question = self.human_bus.get(approval_id)
        if question is None:
            raise AgentRuntimeApprovalError(f"HumanBus question not found: {approval_id}")
        if question.status == QuestionStatus.PENDING:
            return self._awaiting(approval_id, str(request["run_id"]))
        if question.status != QuestionStatus.RESOLVED or not question.answer:
            raise AgentRuntimeApprovalError(
                f"approval {approval_id} has unusable HumanBus state {question.status.value}"
            )

        answer = question.answer.strip().casefold()
        if answer in _REJECT_ANSWERS:
            result = self._rejected_result(request, approval_id)
            self._store.finish(approval_id, "rejected", result)
            return result
        if answer not in _APPROVE_ANSWERS:
            raise AgentRuntimeApprovalError(
                f"approval answer must be explicit approve/reject, got {question.answer!r}"
            )

        claimed, acquired = self._store.claim(approval_id)
        if not acquired:
            if claimed["state"] in {"completed", "rejected"} and claimed["result_json"]:
                return json.loads(claimed["result_json"])
            raise AgentRuntimeApprovalError(
                f"approval {approval_id} cannot launch from state {claimed['state']}"
            )
        try:
            result = await self.client.run(request, event_sink)
        except Exception:
            self._store.fail(approval_id)
            raise
        self._store.finish(approval_id, "completed", result)
        return result

    def _awaiting(self, approval_id: str, run_id: str) -> AwaitingHumanRun:
        checkpoint_ref = f"humanbus://question/{approval_id}"
        receipt = AwaitingHumanRun(
            approval_id=approval_id,
            checkpoint_ref=checkpoint_ref,
            event={
                "kind": "run.state",
                "protocol_version": "0.1.0",
                "run_id": run_id,
                "sequence": 0,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "state": "awaiting_human",
                "checkpoint_ref": checkpoint_ref,
            },
        )
        self.client.validate(receipt.event)
        return receipt

    def _rejected_result(self, request: RuntimeMessage, approval_id: str) -> RuntimeMessage:
        result = {
            "kind": "run.result",
            "protocol_version": "0.1.0",
            "request_id": request["request_id"],
            "run_id": request["run_id"],
            "session_id": request["session_id"],
            "status": "permission_denied",
            "final_output": {"text": ""},
            "material_refs": [],
            "artifact_refs": [],
            "usage": {"duration_ms": 0},
            "error": {
                "code": "approval_rejected",
                "message": f"human approval rejected: {approval_id}",
                "retryable": False,
            },
        }
        self.client.validate(result)
        return result
