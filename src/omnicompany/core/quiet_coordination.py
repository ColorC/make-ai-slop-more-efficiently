"""Durable, pull-based coordination between OmniCompany sessions.

Coordination records are deliberately separate from provider prompts.  A record
can be stored, inspected, acknowledged, and used as a one-shot wake token, but
routine writing never starts a provider turn. An explicit bound-parent action
may reach its exact idle worker through the existing input route; busy workers
pull that action at a tool boundary. The store also keeps the original
human goal independently from coordination messages so a fork or replay cannot
replace the work the user asked for.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import unquote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from omnicompany.core.config import omni_workspace_root

SCHEMA = "omnicompany.quiet-coordination.v1"
DEFAULT_WAKE_POLICY = "never"
BLOCKER_PRIORITIES = ("low", "normal", "high", "critical")
_ENVELOPE_HEAD = re.compile(r"^\[OMNI AGENT MESSAGE v1 · ([^\]]+)\]$")
_INPUT_PATH = re.compile(r"^/cc/sessions/([^/]+)/input$")
_REQUIRED_SCOPE_FIELDS = ("project", "workstream", "plan", "task", "topic")


class QuietCoordinationError(ValueError):
    """Raised when a coordination record cannot pass its identity checks."""


@dataclass(frozen=True)
class ParsedAgentEnvelope:
    """The trusted fields extracted from the existing human-readable wire envelope."""

    message_id: str
    source_session_id: str
    source_provider: str
    source_project: str
    target_session_id: str
    target_provider: str
    target_project: str
    request: str
    raw_message: str
    source_scope: dict[str, str]
    target_scope: dict[str, str]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> Path:
    override = os.environ.get("OMNI_QUIET_COORDINATION_DB")
    return Path(override) if override else omni_workspace_root() / "data" / "lifecycle" / "quiet_coordination.sqlite3"


def _text(value: Any, limit: int = 4000) -> str:
    return " ".join(str(value or "").split())[:limit]


def _scope(value: str) -> dict[str, str]:
    result = {key: "" for key in _REQUIRED_SCOPE_FIELDS}
    for part in value.split(";"):
        key, separator, item = part.partition("=")
        if separator and key.strip() in result:
            result[key.strip()] = item.strip()
    return result


def _scope_value(scope: Mapping[str, str], key: str) -> str:
    value = str(scope.get(key) or "").strip()
    return "" if value in {"-", "unknown", "this session"} else value


def is_agent_envelope(value: Any) -> bool:
    """Return whether a provider input is the existing agent-message envelope."""

    if not isinstance(value, str):
        return False
    first = value.strip().splitlines()[:1]
    return bool(first and _ENVELOPE_HEAD.match(first[0])) and "来自另一个 AI 会话" in value


def parse_agent_envelope(message: str) -> ParsedAgentEnvelope:
    """Parse an existing ``omni agents`` envelope without treating it as a prompt."""

    raw = str(message or "").strip()
    lines = raw.splitlines()
    if not lines:
        raise QuietCoordinationError("协调信封为空")
    head = _ENVELOPE_HEAD.match(lines[0].strip())
    if not head or "来自另一个 AI 会话" not in raw:
        raise QuietCoordinationError("不是受支持的 agent 协调信封")

    source_line = next((line for line in lines if line.startswith("发件会话:")), "")
    sender_line = next((line for line in lines if line.startswith("Sender scope:")), "")
    target_line = next((line for line in lines if line.startswith("To:")), "")
    receiver_line = next((line for line in lines if line.startswith("Receiver scope:")), "")
    request_line = next((index for index, line in enumerate(lines) if line.startswith("Request:")), None)
    if not source_line or not sender_line or not target_line or not receiver_line or request_line is None:
        raise QuietCoordinationError("协调信封缺少来源、目标或正文")

    source_scope = _scope(sender_line.partition(":")[2].strip())
    target_scope = _scope(receiver_line.partition(":")[2].strip())
    source_match = re.search(r"\bsession=([^;\s]+)", source_line)
    target_match = re.search(r"\bsession=([^;\s]+)", target_line)
    source_provider_match = re.search(r"\bprovider=([^;\s]+)", source_line)
    target_provider_match = re.search(r"\bprovider=([^;\s]+)", target_line)
    source_session = _text(source_match.group(1) if source_match else "", 240)
    target_session = _text(target_match.group(1) if target_match else "", 240)
    request = "\n".join(lines[request_line:])[len("Request:") :].strip()
    if not source_session or not target_session or not request:
        raise QuietCoordinationError("协调信封缺少稳定 session 或请求正文")
    return ParsedAgentEnvelope(
        message_id=head.group(1).strip(),
        source_session_id=source_session,
        source_provider=_text(source_provider_match.group(1) if source_provider_match else source_scope.get("provider", ""), 40),
        source_project=_scope_value(source_scope, "project"),
        target_session_id=target_session,
        target_provider=_text(target_provider_match.group(1) if target_provider_match else target_scope.get("provider", ""), 40),
        target_project=_scope_value(target_scope, "project"),
        request=request[:4000],
        raw_message=raw[:20_000],
        source_scope=source_scope,
        target_scope=target_scope,
    )


def _payload_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class QuietCoordinationStore:
    """SQLite-backed typed inbox and goal ledger shared by CLI and ccdaemon."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else _db_path()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS coordination_messages (
                message_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                actor_type TEXT NOT NULL,
                origin_provider TEXT NOT NULL,
                source_session_id TEXT NOT NULL,
                target_session_id TEXT NOT NULL,
                parent_task_id TEXT NOT NULL DEFAULT '',
                run_id TEXT NOT NULL DEFAULT '',
                forked_from TEXT NOT NULL DEFAULT '',
                causation_id TEXT NOT NULL DEFAULT '',
                correlation_id TEXT NOT NULL DEFAULT '',
                idempotency_key TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                source_project TEXT NOT NULL DEFAULT '',
                target_project TEXT NOT NULL DEFAULT '',
                transport_target_id TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL,
                wake_policy TEXT NOT NULL DEFAULT 'never',
                status TEXT NOT NULL DEFAULT 'pending',
                rejection_reason TEXT NOT NULL DEFAULT '',
                wake_claimed_at TEXT,
                acked_at TEXT,
                acked_by_session_id TEXT NOT NULL DEFAULT '',
                acked_by_run_id TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_coordination_pending_target
                ON coordination_messages(target_session_id, status, created_at);
            CREATE TABLE IF NOT EXISTS session_goals (
                session_id TEXT PRIMARY KEY,
                original_goal TEXT NOT NULL,
                current_goal TEXT NOT NULL,
                goal_version INTEGER NOT NULL,
                source TEXT NOT NULL,
                forked_from TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            """
        )
        # Durable retry accounting for the terminal-wake sweep. Added by
        # migration so an existing ledger keeps its rows; both columns default
        # to "no retry scheduled yet", which is exactly a fresh record.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(coordination_messages)").fetchall()}
        if "wake_attempts" not in columns:
            conn.execute("ALTER TABLE coordination_messages ADD COLUMN wake_attempts INTEGER NOT NULL DEFAULT 0")
        if "wake_next_retry_at" not in columns:
            conn.execute("ALTER TABLE coordination_messages ADD COLUMN wake_next_retry_at TEXT")

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        try:
            result["payload"] = json.loads(result.pop("payload_json"))
        except (KeyError, TypeError, json.JSONDecodeError):
            result["payload"] = {}
        result["schema"] = SCHEMA
        return result

    def _find(self, conn: sqlite3.Connection, *, message_id: str = "", idempotency_key: str = "") -> dict[str, Any] | None:
        if message_id:
            row = conn.execute("SELECT * FROM coordination_messages WHERE message_id=?", (message_id,)).fetchone()
        elif idempotency_key:
            row = conn.execute("SELECT * FROM coordination_messages WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        else:
            return None
        return self._row(row)

    def put(
        self,
        *,
        message_id: str,
        event_type: str,
        actor_type: str,
        origin_provider: str,
        source_session_id: str,
        target_session_id: str,
        payload: Mapping[str, Any] | None = None,
        parent_task_id: str = "",
        run_id: str = "",
        forked_from: str = "",
        causation_id: str = "",
        correlation_id: str = "",
        idempotency_key: str | None = None,
        source_project: str = "",
        target_project: str = "",
        transport_target_id: str = "",
        wake_policy: str = DEFAULT_WAKE_POLICY,
        status: str = "pending",
        rejection_reason: str = "",
        created_at: str | None = None,
    ) -> dict[str, Any]:
        values = {
            "message_id": _text(message_id, 240),
            "event_type": _text(event_type, 80),
            "actor_type": _text(actor_type, 40),
            "origin_provider": _text(origin_provider, 40),
            "source_session_id": _text(source_session_id, 240),
            "target_session_id": _text(target_session_id, 240),
            "parent_task_id": _text(parent_task_id, 240),
            "run_id": _text(run_id, 240),
            "forked_from": _text(forked_from, 240),
            "causation_id": _text(causation_id, 240),
            "correlation_id": _text(correlation_id, 240),
            "idempotency_key": _text(idempotency_key or message_id, 240),
            "created_at": created_at or _now(),
            "source_project": _text(source_project, 160),
            "target_project": _text(target_project, 160),
            "transport_target_id": _text(transport_target_id, 240),
            "payload_json": _payload_json(payload or {}),
            "wake_policy": _text(wake_policy or DEFAULT_WAKE_POLICY, 40),
            "status": _text(status or "pending", 40),
            "rejection_reason": _text(rejection_reason, 400),
        }
        if not values["message_id"] or not values["source_session_id"] or not values["target_session_id"]:
            raise QuietCoordinationError("协调信封必须有 message_id、source_session_id、target_session_id")
        if values["actor_type"] not in {"agent", "system"}:
            raise QuietCoordinationError("跨会话协调 actor_type 只能是 agent 或 system")
        with self._connect() as conn:
            self._ensure_schema(conn)
            existing = self._find(conn, idempotency_key=values["idempotency_key"])
            if existing is None:
                existing = self._find(conn, message_id=values["message_id"])
            if existing is not None:
                comparable = ("event_type", "actor_type", "source_session_id", "target_session_id", "run_id", "payload")
                if any(
                    existing.get(key) != (dict(payload or {}) if key == "payload" else values[key])
                    for key in comparable
                ):
                    raise QuietCoordinationError("幂等键已绑定到不同的协调消息")
                return {"message": existing, "deduped": True}
            conn.execute(
                """INSERT INTO coordination_messages
                (message_id,event_type,actor_type,origin_provider,source_session_id,target_session_id,
                 parent_task_id,run_id,forked_from,causation_id,correlation_id,idempotency_key,created_at,
                 source_project,target_project,transport_target_id,payload_json,wake_policy,status,rejection_reason)
                VALUES (:message_id,:event_type,:actor_type,:origin_provider,:source_session_id,:target_session_id,
                        :parent_task_id,:run_id,:forked_from,:causation_id,:correlation_id,:idempotency_key,:created_at,
                        :source_project,:target_project,:transport_target_id,:payload_json,:wake_policy,:status,:rejection_reason)""",
                values,
            )
            conn.commit()
            return {"message": self._find(conn, message_id=values["message_id"]), "deduped": False}

    def ingest_wire_message(
        self,
        message: str,
        *,
        transport_target_id: str,
        target_candidates: Iterable[str] = (),
        receiver_project: str = "",
    ) -> dict[str, Any]:
        """Store an existing wire envelope and fail closed on target mismatch."""

        parsed = parse_agent_envelope(message)
        candidates = {str(item).strip() for item in target_candidates if str(item).strip()}
        candidates.add(str(transport_target_id).strip())
        rejection = ""
        if parsed.target_session_id not in candidates:
            rejection = "target_session_mismatch"
        elif receiver_project and parsed.target_project and receiver_project != parsed.target_project:
            rejection = "target_project_mismatch"
        payload = {
            "request": parsed.request,
            "raw_message": parsed.raw_message,
            "declared_target_session_id": parsed.target_session_id,
            "declared_target_provider": parsed.target_provider,
            "transport_target_id": str(transport_target_id),
            "target_candidates": sorted(candidates),
        }
        result = self.put(
            message_id=parsed.message_id,
            event_type="coordination_message",
            actor_type="agent",
            origin_provider=parsed.source_provider or "unknown",
            source_session_id=parsed.source_session_id,
            target_session_id=parsed.target_session_id,
            source_project=parsed.source_project,
            target_project=parsed.target_project,
            transport_target_id=transport_target_id,
            payload=payload,
            idempotency_key=parsed.message_id,
            wake_policy=DEFAULT_WAKE_POLICY,
            status="rejected" if rejection else "pending",
            rejection_reason=rejection,
        )
        return {**result, "accepted": not bool(rejection), "rejection_reason": rejection}

    def peek(self, target_session_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        """Read pending records for one exact target without acknowledging them."""

        target = _text(target_session_id, 240)
        if not target:
            raise QuietCoordinationError("收件 session 不能为空")
        cap = max(1, min(int(limit or 100), 500))
        with self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                "SELECT * FROM coordination_messages WHERE target_session_id=? AND status='pending' ORDER BY created_at, message_id LIMIT ?",
                (target, cap),
            ).fetchall()
        return [item for row in rows if (item := self._row(row)) is not None]

    def withdraw(self, message_id: str, *, source_session_id: str, reason: str) -> dict[str, Any]:
        """Withdraw only one's own unclaimed pending request; never impersonate an ack."""
        if not source_session_id or not reason.strip():
            raise QuietCoordinationError("source identity and withdrawal reason are required")
        with self._connect() as conn:
            self._ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            message = self._find(conn, message_id=message_id)
            if not message or message["source_session_id"] != source_session_id:
                raise QuietCoordinationError("only the original sender may withdraw this message")
            if message["actor_type"] != "agent" or message["event_type"] != "coordination_message":
                raise QuietCoordinationError("only agent coordination requests may be withdrawn")
            if message["status"] == "withdrawn":
                return {"ok": True, "withdrawn": True, "deduped": True, "message": message}
            if message["status"] != "pending" or message.get("wake_claimed_at"):
                raise QuietCoordinationError("message has already been claimed or delivered")
            payload = dict(message["payload"])
            payload["withdrawal"] = {"at": _now(), "source_session_id": source_session_id, "reason": reason.strip()}
            cursor = conn.execute(
                "UPDATE coordination_messages SET status='withdrawn', payload_json=? "
                "WHERE message_id=? AND source_session_id=? AND status='pending' AND wake_claimed_at IS NULL",
                (_payload_json(payload), message_id, source_session_id),
            )
            if cursor.rowcount != 1:
                raise QuietCoordinationError("message was concurrently claimed")
            conn.commit()
            return {"ok": True, "withdrawn": True, "deduped": False,
                    "message": self._find(conn, message_id=message_id)}

    def _record_diagnostic(self, conn: sqlite3.Connection, message: dict[str, Any], recipient_session_id: str, reason: str) -> dict[str, Any] | None:
        diagnostic_id = "diag-" + hashlib.sha256(
            f"{message['message_id']}:{recipient_session_id}:{reason}".encode("utf-8")
        ).hexdigest()[:20]
        values = {
            "message_id": diagnostic_id,
            "event_type": "admission_rejected",
            "actor_type": "system",
            "origin_provider": "omnicompany",
            "source_session_id": recipient_session_id or "unknown-recipient",
            "target_session_id": message["target_session_id"],
            "parent_task_id": message.get("parent_task_id") or "",
            "run_id": message.get("run_id") or "",
            "forked_from": "",
            "causation_id": message["message_id"],
            "correlation_id": message.get("correlation_id") or message["message_id"],
            "idempotency_key": diagnostic_id,
            "created_at": _now(),
            "source_project": "",
            "target_project": message.get("target_project") or "",
            "transport_target_id": "",
            "payload_json": _payload_json({"message_id": message["message_id"], "reason": reason}),
            "wake_policy": DEFAULT_WAKE_POLICY,
            "status": "rejected",
            "rejection_reason": reason,
        }
        conn.execute(
            """INSERT OR IGNORE INTO coordination_messages
            (message_id,event_type,actor_type,origin_provider,source_session_id,target_session_id,parent_task_id,
             run_id,forked_from,causation_id,correlation_id,idempotency_key,created_at,source_project,target_project,
             transport_target_id,payload_json,wake_policy,status,rejection_reason)
            VALUES (:message_id,:event_type,:actor_type,:origin_provider,:source_session_id,:target_session_id,:parent_task_id,
                    :run_id,:forked_from,:causation_id,:correlation_id,:idempotency_key,:created_at,:source_project,:target_project,
                    :transport_target_id,:payload_json,:wake_policy,:status,:rejection_reason)""",
            values,
        )
        return self._find(conn, message_id=diagnostic_id)

    def ack(
        self,
        message_id: str,
        *,
        recipient_session_id: str,
        recipient_project: str = "",
        recipient_run_id: str = "",
    ) -> dict[str, Any]:
        """Acknowledge once, only from the exact target and run generation."""

        with self._connect() as conn:
            self._ensure_schema(conn)
            message = self._find(conn, message_id=_text(message_id, 240))
            if message is None:
                raise QuietCoordinationError(f"未找到协调消息: {message_id}")
            reason = ""
            if recipient_session_id != message["target_session_id"]:
                reason = "target_session_mismatch"
            elif message.get("target_project") and recipient_project != message["target_project"]:
                reason = "target_project_mismatch"
            elif message.get("run_id") and recipient_run_id != message["run_id"]:
                reason = "run_generation_mismatch"
            if reason:
                diagnostic = self._record_diagnostic(conn, message, recipient_session_id, reason)
                conn.commit()
                return {"message": message, "acked": False, "deduped": False, "reason": reason, "diagnostic": diagnostic}
            if message["status"] == "acked":
                return {"message": message, "acked": True, "deduped": True}
            if message["status"] == "rejected":
                return {"message": message, "acked": False, "deduped": False, "reason": message.get("rejection_reason")}
            if message["status"] != "pending":
                return {"message": message, "acked": False, "deduped": False, "reason": message["status"]}
            conn.execute(
                "UPDATE coordination_messages SET status='acked', acked_at=?, acked_by_session_id=?, acked_by_run_id=? WHERE message_id=? AND status='pending'",
                (_now(), recipient_session_id, recipient_run_id, message["message_id"]),
            )
            conn.commit()
            updated = self._find(conn, message_id=message["message_id"])
            return {"message": updated, "acked": True, "deduped": False}

    def claim_wake(self, message_id: str) -> bool:
        """Claim the one allowed wake token for a terminal/blocker event."""

        with self._connect() as conn:
            self._ensure_schema(conn)
            cur = conn.execute(
                """UPDATE coordination_messages SET wake_claimed_at=?
                   WHERE message_id=? AND status='pending' AND wake_policy != 'never' AND wake_claimed_at IS NULL""",
                (_now(), _text(message_id, 240)),
            )
            conn.commit()
            return cur.rowcount == 1

    def release_wake(self, message_id: str) -> bool:
        """Return a claimed wake token when transport delivery failed.

        A terminal record remains durable, so a later supervisor sweep can
        retry delivery.  This is deliberately limited to still-pending rows.
        """
        with self._connect() as conn:
            self._ensure_schema(conn)
            cur = conn.execute(
                """UPDATE coordination_messages SET wake_claimed_at=NULL
                   WHERE message_id=? AND status='pending' AND wake_claimed_at IS NOT NULL""",
                (_text(message_id, 240),),
            )
            conn.commit()
            return cur.rowcount == 1

    def defer_wake_retry(self, message_id: str, *, next_retry_at: str) -> bool:
        """Return a claimed wake and schedule a backed-off retry, atomically.

        The terminal-wake sweep calls this when its own ``claim_wake`` succeeded
        but delivery then failed.  It combines ``release_wake``'s token return
        with a durable ``wake_attempts``/``wake_next_retry_at`` bump so the next
        sweep both re-discovers the row and honours a backoff window instead of
        hammering a parent whose transport is currently down.  Guarded on
        ``wake_claimed_at IS NOT NULL`` so it only ever acts on a token this
        caller actually holds — a concurrent hot-path claim is never disturbed.
        """
        with self._connect() as conn:
            self._ensure_schema(conn)
            cur = conn.execute(
                """UPDATE coordination_messages
                   SET wake_claimed_at=NULL,
                       wake_attempts=wake_attempts + 1,
                       wake_next_retry_at=?
                   WHERE message_id=? AND status='pending'
                     AND wake_policy != 'never' AND wake_claimed_at IS NOT NULL""",
                (_text(next_retry_at, 60), _text(message_id, 240)),
            )
            conn.commit()
            return cur.rowcount == 1

    def pending_terminal_wakes(
        self,
        *,
        target_session_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List durable wake-eligible records whose one wake was never delivered.

        ``release_wake`` and ``record_child_terminal`` both leave a still-pending
        row with ``wake_claimed_at IS NULL``: either the record was written but a
        crash intervened before the hot-path claim, or delivery failed and the
        token was returned.  For a terminated child the originating Stop/
        SubagentStop hook and PTY monitor never fire again, so without a sweep
        that re-discovers these rows the parent is never woken.  This read-only
        primitive is that discovery step; a sweep re-drives each row through the
        existing ``claim_wake`` CAS, which makes concurrent hot-path delivery
        harmless.  Successfully delivered-but-unacked wakes keep a non-null
        ``wake_claimed_at`` and are excluded, as are ``wake_policy='never'`` rows.
        """

        cap = max(1, min(int(limit or 100), 500))
        target = _text(target_session_id, 240)
        with self._connect() as conn:
            self._ensure_schema(conn)
            sql = (
                "SELECT * FROM coordination_messages "
                "WHERE status='pending' AND wake_policy != 'never' AND wake_claimed_at IS NULL"
            )
            params: list[Any] = []
            if target:
                sql += " AND target_session_id=?"
                params.append(target)
            sql += " ORDER BY created_at, message_id LIMIT ?"
            params.append(cap)
            rows = conn.execute(sql, params).fetchall()
        return [item for row in rows if (item := self._row(row)) is not None]

    def record_child_terminal(
        self,
        *,
        child_session_id: str,
        parent_session_id: str,
        parent_task_id: str,
        run_id: str,
        outcome: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a child result with a single parent-only wake token."""

        if not child_session_id or not parent_session_id or not parent_task_id or not run_id:
            raise QuietCoordinationError("child terminal 必须有 child、parent、task 和 run 身份")
        message_id = "child-terminal-" + hashlib.sha256(
            f"{child_session_id}:{parent_session_id}:{parent_task_id}:{run_id}".encode("utf-8")
        ).hexdigest()[:20]
        return self.put(
            message_id=message_id,
            event_type="child_terminal",
            actor_type="agent",
            origin_provider="omnicompany",
            source_session_id=child_session_id,
            target_session_id=parent_session_id,
            parent_task_id=parent_task_id,
            run_id=run_id,
            causation_id=run_id,
            correlation_id=parent_task_id,
            idempotency_key=message_id,
            payload={**dict(payload or {}), "outcome": _text(outcome, 80)},
            wake_policy="child_terminal_once",
        )

    def record_blocker(
        self,
        *,
        source_session_id: str,
        target_session_id: str,
        parent_task_id: str,
        run_id: str,
        reason: str,
        priority: str = "normal",
        payload: Mapping[str, Any] | None = None,
        source_project: str = "",
        target_project: str = "",
        blocker_id: str = "",
    ) -> dict[str, Any]:
        """Record a finite-priority blocker without granting a wake token."""

        level = _text(priority, 20).lower()
        if level not in BLOCKER_PRIORITIES:
            raise QuietCoordinationError(
                f"blocker priority 必须是: {', '.join(BLOCKER_PRIORITIES)}"
            )
        reason_text = _text(reason, 4000)
        if not source_session_id or not target_session_id or not parent_task_id or not run_id or not reason_text:
            raise QuietCoordinationError("blocker 必须有 source、target、task、run 和 reason")
        message_id = "blocker-" + hashlib.sha256(
            f"{blocker_id or reason_text}:{source_session_id}:{target_session_id}:{parent_task_id}:{run_id}".encode("utf-8")
        ).hexdigest()[:20]
        return self.put(
            message_id=message_id,
            event_type="blocker",
            actor_type="agent",
            origin_provider="omnicompany",
            source_session_id=source_session_id,
            target_session_id=target_session_id,
            parent_task_id=parent_task_id,
            run_id=run_id,
            source_project=source_project,
            target_project=target_project,
            causation_id=run_id,
            correlation_id=parent_task_id,
            idempotency_key=message_id,
            payload={**dict(payload or {}), "priority": level, "reason": reason_text},
            wake_policy=DEFAULT_WAKE_POLICY,
        )

    def record_user_goal(self, session_id: str, goal: str, *, source: str = "human") -> dict[str, Any]:
        """Persist the first human goal and keep later corrections versioned beside it."""

        sid = _text(session_id, 240)
        text = _text(goal, 4000)
        if not sid or not text:
            raise QuietCoordinationError("用户目标必须有 session_id 和正文")
        with self._connect() as conn:
            self._ensure_schema(conn)
            current = conn.execute("SELECT * FROM session_goals WHERE session_id=?", (sid,)).fetchone()
            if current is None:
                version = 1
                conn.execute(
                    "INSERT INTO session_goals(session_id,original_goal,current_goal,goal_version,source,forked_from,updated_at) VALUES (?,?,?,?,?,?,?)",
                    (sid, text, text, version, _text(source, 40) or "human", "", _now()),
                )
            elif current["current_goal"] != text:
                version = int(current["goal_version"]) + 1
                conn.execute(
                    "UPDATE session_goals SET current_goal=?, goal_version=?, source=?, updated_at=? WHERE session_id=?",
                    (text, version, _text(source, 40) or "human", _now(), sid),
                )
            conn.commit()
            row = conn.execute("SELECT * FROM session_goals WHERE session_id=?", (sid,)).fetchone()
        return {"goal": dict(row) if row is not None else {}, "changed": current is None or current["current_goal"] != text}

    def fork_goal(self, source_session_id: str, target_session_id: str) -> dict[str, Any] | None:
        """Copy goal history to a fork without copying inbox recipient or wake rights."""

        if not source_session_id or not target_session_id:
            raise QuietCoordinationError("fork goal 必须有源和目标 session")
        with self._connect() as conn:
            self._ensure_schema(conn)
            source = conn.execute("SELECT * FROM session_goals WHERE session_id=?", (source_session_id,)).fetchone()
            if source is None:
                return None
            conn.execute(
                """INSERT INTO session_goals(session_id,original_goal,current_goal,goal_version,source,forked_from,updated_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(session_id) DO UPDATE SET original_goal=excluded.original_goal,
                   current_goal=excluded.current_goal,goal_version=excluded.goal_version,
                   source=excluded.source,forked_from=excluded.forked_from,updated_at=excluded.updated_at""",
                (target_session_id, source["original_goal"], source["current_goal"], source["goal_version"], source["source"], source_session_id, _now()),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM session_goals WHERE session_id=?", (target_session_id,)).fetchone()
        return dict(row) if row is not None else None

    def get_user_goal(self, session_id: str) -> dict[str, Any] | None:
        """Read one session's original and current human goal."""

        with self._connect() as conn:
            self._ensure_schema(conn)
            row = conn.execute("SELECT * FROM session_goals WHERE session_id=?", (_text(session_id, 240),)).fetchone()
        return dict(row) if row is not None else None


def ingest_agent_message(
    message: str,
    *,
    transport_target_id: str,
    target_candidates: Iterable[str] = (),
    receiver_project: str = "",
    store: QuietCoordinationStore | None = None,
) -> dict[str, Any]:
    """Store a wire message for middleware, fallback code, and tests."""

    return (store or QuietCoordinationStore()).ingest_wire_message(
        message,
        transport_target_id=transport_target_id,
        target_candidates=target_candidates,
        receiver_project=receiver_project,
    )


def _runtime_target_context(transport_target_id: str) -> tuple[set[str], str]:
    """Resolve physical PTY/chat id to native ids and its bound project."""

    candidates = {transport_target_id}
    receiver_project = ""
    try:
        from omnicompany.dashboard.ccdaemon.pty import get_manager

        session = get_manager().get(transport_target_id)
        if session is not None:
            candidates.update(
                value
                for value in (getattr(session, "provider_session_id", None), getattr(session, "claude_session_id", None))
                if value
            )
        from omnicompany.packages.services._core.identity import get_session_binding

        for identity in (transport_target_id, *(str(item) for item in candidates)):
            binding = get_session_binding(identity)
            if binding.get("project"):
                receiver_project = str(binding["project"]).strip()
                break
    except Exception:
        pass
    if transport_target_id.startswith("chat-"):
        try:
            from omnicompany.dashboard.ccdaemon.chat import get_chat_manager

            session = get_chat_manager().get(transport_target_id)
            if session is not None and getattr(session, "claude_session_id", None):
                candidates.add(str(session.claude_session_id))
            from omnicompany.packages.services._core.identity import get_session_binding

            binding = get_session_binding(transport_target_id)
            if binding.get("project"):
                receiver_project = str(binding["project"]).strip()
        except Exception:
            pass
    if not receiver_project:
        try:
            from omnicompany.packages.services._core.identity import bindings_by_session_key

            bindings = bindings_by_session_key()
            for candidate in candidates:
                aliases = {f"pty:{candidate}", f"session:{candidate}"}
                aliases.update(key for key in bindings if key.endswith(f":{candidate}"))
                binding = next((bindings[key] for key in aliases if key in bindings), None)
                if binding and binding.get("project"):
                    receiver_project = str(binding["project"]).strip()
                    break
        except Exception:
            pass
    return {str(value) for value in candidates if str(value).strip()}, receiver_project


def _runtime_target_candidates(transport_target_id: str) -> set[str]:
    """Backward-compatible candidate-only view used by callers and tests."""

    return _runtime_target_context(transport_target_id)[0]


class QuietCoordinationMiddleware(BaseHTTPMiddleware):
    """Intercept agent envelopes before the existing provider-input route."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        match = _INPUT_PATH.match(request.url.path)
        if request.method.upper() != "POST" or match is None:
            return await call_next(request)
        try:
            body = await request.body()
            data = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return await call_next(request)
        message = data.get("data") if isinstance(data, dict) else None
        if not is_agent_envelope(message):
            # This is the only input role admitted to the goal ledger.  Agent
            # envelopes take the branch above and never become a user goal.
            if isinstance(data, dict) and bool(data.get("submit", True)) and isinstance(message, str):
                try:
                    prompt_goal_from_payload(
                        {"session_id": unquote(match.group(1)), "prompt": message},
                    )
                except Exception:
                    # Goal capture is durable context, not a reason to break
                    # the provider's existing human-input path.
                    pass
            return await call_next(request)
        transport_target_id = unquote(match.group(1))
        candidates, receiver_project = _runtime_target_context(transport_target_id)
        result = ingest_agent_message(
            message,
            transport_target_id=transport_target_id,
            target_candidates=candidates,
            receiver_project=receiver_project,
        )
        stored = result["message"] or {}
        if not result["accepted"]:
            return JSONResponse(
                {
                    "ok": False,
                    "id": stored.get("message_id"),
                    "kind": "coordination",
                    "submission": "rejected",
                    "quiet": True,
                    "reason": result["rejection_reason"],
                }
            )
        admitted_parent_action = False
        try:
            import asyncio
            from omnicompany.core.parent_action_delivery import claim_idle_parent_action

            admitted_parent_action = await asyncio.to_thread(claim_idle_parent_action, stored, message)
        except Exception:
            # Uncertain identity/activity keeps the original quiet inbox path.
            pass
        if admitted_parent_action:
            # Still an agent envelope. Do not call prompt_goal_from_payload.
            # The normal provider route owns submission and confirmation.
            from omnicompany.core.parent_action_delivery import record_parent_action_transport

            try:
                response = await call_next(request)
                response_body = b"".join([chunk async for chunk in response.body_iterator])
                try:
                    submission = json.loads(response_body).get("submission", "unconfirmed")
                except (ValueError, AttributeError):
                    submission = "unconfirmed"
                record_parent_action_transport(stored, submission=submission)
                return Response(content=response_body, status_code=response.status_code,
                                headers=dict(response.headers), background=response.background)
            except BaseException:
                record_parent_action_transport(stored)
                raise
        return JSONResponse(
            {
                "ok": True,
                "id": stored.get("message_id"),
                "kind": "coordination",
                "submission": "stored",
                "quiet": True,
                "wake_policy": DEFAULT_WAKE_POLICY,
                "deduped": result["deduped"],
                "delivery_state": stored.get("status"),
            }
        )


def prompt_goal_from_payload(payload: Mapping[str, Any], *, store: QuietCoordinationStore | None = None) -> dict[str, Any] | None:
    """Record a provider hook's real user prompt without accepting agent envelopes."""

    candidate = next(
        (payload.get(key) for key in ("prompt", "user_prompt", "userPrompt", "input", "message") if payload.get(key)),
        None,
    )
    session_id = payload.get("session_id") or payload.get("sessionId") or os.environ.get("OMNI_CC_PTY_ID")
    if not session_id or not isinstance(candidate, str) or not candidate.strip() or is_agent_envelope(candidate):
        return None
    return (store or QuietCoordinationStore()).record_user_goal(str(session_id), candidate)


__all__ = [
    "BLOCKER_PRIORITIES",
    "DEFAULT_WAKE_POLICY",
    "ParsedAgentEnvelope",
    "QuietCoordinationError",
    "QuietCoordinationMiddleware",
    "QuietCoordinationStore",
    "SCHEMA",
    "ingest_agent_message",
    "is_agent_envelope",
    "parse_agent_envelope",
    "prompt_goal_from_payload",
]
