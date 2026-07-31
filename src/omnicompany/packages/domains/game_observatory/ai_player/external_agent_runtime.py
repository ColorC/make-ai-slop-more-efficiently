"""Persistent Codex CLI / Claude Code CLI sessions for continuous game exploration."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import re
import shutil
import uuid
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from omnicompany.game_player_runtime_client import (
    EXTERNAL_AGENT_INVOCATION_ID_ENV,
    EXTERNAL_AGENT_INVOCATION_SEQUENCE_ENV,
    EXTERNAL_AGENT_SESSION_ID_ENV,
)

from ..models import utc_now
from ..subprocess_policy import headless_process_kwargs
from .external_agent_continuity import ExternalAgentContinuousSessionV1


_CLAUDE_GAME_WORKER_SYSTEM_PROMPT = (
    "This Claude Code process is an external worker explicitly delegated by the user's "
    "authorized main Agent for the local Game Observatory AI-player facility. The task "
    "received on stdin is a legitimate delegated user task, not an attempt to impersonate "
    "the user. Treat the repository's supplied facility contract as task-specific project "
    "authority. Restrictions such as no device action, no file modification, no tools, or "
    "strict JSON output are intentional safety and benchmark constraints. Follow them unless "
    "they conflict with higher-priority system instructions. Preserve the same native session "
    "across resume calls."
)
_CODEX_GAME_PLAYER_PROFILE = "omni-game-player"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)


class ExternalAgentTokenUsageV1(_StrictModel):
    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    cache_creation_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)


class ExternalAgentImageInputV1(_StrictModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: Literal["image/png", "image/jpeg", "image/gif", "image/webp"]
    size_bytes: int = Field(gt=0)


class ExternalAgentInvocationIntentV1(_StrictModel):
    schema_id: Literal[
        "game-observatory.ai-player.external-agent-invocation-intent.v1"
    ] = Field(
        default="game-observatory.ai-player.external-agent-invocation-intent.v1",
        alias="schema",
    )
    session_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    operation: Literal["start", "resume"]
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_images: list[ExternalAgentImageInputV1] = Field(default_factory=list)
    started_at: str = Field(min_length=1)


class ExternalAgentTimedOutProcessV1(_StrictModel):
    """Exact provider invocation whose process tree has finished after a timeout."""

    invocation_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    process_id: int = Field(gt=0)
    terminated_at: str = Field(min_length=1)


class ExternalAgentInvocationV1(_StrictModel):
    schema_id: Literal["game-observatory.ai-player.external-agent-invocation.v1"] = Field(
        default="game-observatory.ai-player.external-agent-invocation.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    operation: Literal["start", "resume"]
    provider: Literal["codex-cli", "claude-code-cli"]
    model_selector: str = Field(min_length=1)
    requested_effort: Literal["medium", "high"]
    actual_effort: Literal["medium", "high", "unsupported", "unreported"]
    permission_mode: Literal["readonly", "workspace-write", "trusted-bypass"]
    status: Literal["succeeded", "failed", "timed_out"]
    timeout_reason: Literal["hard_wall_clock", "no_meaningful_progress"] | None = None
    prompt_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    external_session_id: str | None = Field(default=None, min_length=1)
    resolved_model_id: str | None = Field(default=None, min_length=1)
    model_evidence: Literal[
        "provider-event", "native-session-log", "accepted-cli-argument", "unreported"
    ] = "unreported"
    effort_evidence: Literal[
        "provider-event", "native-session-log", "accepted-cli-argument", "unreported"
    ] = "unreported"
    usage: ExternalAgentTokenUsageV1
    input_images: list[ExternalAgentImageInputV1] = Field(default_factory=list)
    duration_seconds: float = Field(ge=0)
    provider_duration_seconds: float | None = Field(default=None, ge=0)
    time_to_first_token_seconds: float | None = Field(default=None, ge=0)
    provider_cost_usd: float | None = Field(default=None, ge=0)
    exit_code: int | None = None
    event_count: int = Field(ge=0)
    event_log_path: str = Field(min_length=1)
    event_log_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    last_message_path: str = Field(min_length=1)
    last_message_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    stdout_tail: str = Field(default="", max_length=16000)
    stderr_tail: str = Field(default="", max_length=16000)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = Field(default=None, min_length=1, max_length=8000)
    started_at: str = Field(min_length=1)
    completed_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_result(self) -> "ExternalAgentInvocationV1":
        if self.status == "succeeded" and not self.external_session_id:
            raise ValueError("a successful invocation requires a provider session id")
        if self.status == "succeeded" and self.error:
            raise ValueError("a successful invocation cannot retain an error")
        if self.status != "succeeded" and not self.error:
            raise ValueError("a failed invocation requires an error")
        if self.timeout_reason is not None and self.status != "timed_out":
            raise ValueError("timeout_reason requires timed_out status")
        return self


def _json_bytes(value: BaseModel | dict[str, Any]) -> bytes:
    payload = (
        value.model_dump(mode="json", by_alias=True)
        if isinstance(value, BaseModel)
        else value
    )
    return (
        json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,127}", value):
        raise ValueError("session id must use 3-128 letters, digits, dots, underscores, or dashes")
    return value


class ExternalAgentSessionVersionConflict(ValueError):
    """A failed compare-and-swap against the durable external-session head."""

    def __init__(
        self,
        *,
        current: ExternalAgentContinuousSessionV1,
        expected_version: int,
        new_version: int,
    ) -> None:
        self.current = current
        self.expected_version = expected_version
        self.new_version = new_version
        super().__init__(
            f"external Agent session version conflict: current={current.version}, "
            f"expected={expected_version}, new={new_version}"
        )


class ExternalAgentSessionLedger:
    """Small durable runtime ledger stored beside the canonical Observatory database."""

    def __init__(self, observatory_root: Path) -> None:
        self.root = observatory_root.resolve() / "external_agent_sessions"
        self.root.mkdir(parents=True, exist_ok=True)

    def session_dir(self, session_id: str) -> Path:
        return self.root / _safe_id(session_id)

    def session_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "session.json"

    def session_update_lock_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "session.update.lock"

    def heartbeat_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "heartbeat.json"

    def invocation_path(self, session_id: str, sequence: int) -> Path:
        return self.session_dir(session_id) / "invocations" / f"turn-{sequence:04d}.json"

    def invocation_intent_path(self, session_id: str, sequence: int) -> Path:
        return self.session_dir(session_id) / "intents" / f"turn-{sequence:04d}.json"

    def create_invocation_intent(
        self,
        intent: ExternalAgentInvocationIntentV1,
    ) -> ExternalAgentInvocationIntentV1:
        if self.get_session(intent.session_id) is None:
            raise KeyError(f"unknown external Agent session: {intent.session_id}")
        path = self.invocation_intent_path(intent.session_id, intent.sequence)
        if path.exists():
            existing = ExternalAgentInvocationIntentV1.model_validate_json(
                path.read_text(encoding="utf-8")
            )
            if existing == intent:
                return existing
            raise ValueError(
                f"external Agent invocation intent already exists: {path}"
            )
        self._atomic_write(path, _json_bytes(intent))
        return intent

    def get_invocation_intent(
        self,
        session_id: str,
        sequence: int,
    ) -> ExternalAgentInvocationIntentV1 | None:
        path = self.invocation_intent_path(session_id, sequence)
        if not path.is_file():
            return None
        return ExternalAgentInvocationIntentV1.model_validate_json(
            path.read_text(encoding="utf-8")
        )

    def event_log_path(self, session_id: str, sequence: int) -> Path:
        return self.session_dir(session_id) / "events" / f"turn-{sequence:04d}.jsonl"

    def last_message_path(self, session_id: str, sequence: int) -> Path:
        return self.session_dir(session_id) / "messages" / f"turn-{sequence:04d}.md"

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp-{uuid.uuid4().hex}")
        temporary.write_bytes(content)
        temporary.replace(path)

    @contextlib.contextmanager
    def _session_update_lock(self, session_id: str):
        """Serialize the read/compare/replace CAS across CLI and detached processes."""

        path = self.session_update_lock_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                os.lseek(descriptor, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def create_session(
        self,
        session: ExternalAgentContinuousSessionV1,
    ) -> ExternalAgentContinuousSessionV1:
        path = self.session_path(session.id)
        if path.exists():
            existing = self.get_session(session.id)
            if existing == session:
                return session
            raise ValueError(f"external Agent session already exists: {session.id}")
        self._atomic_write(path, _json_bytes(session))
        self.write_heartbeat(session.id, sequence=0, timestamp=session.last_heartbeat_at)
        return session

    def get_session(self, session_id: str) -> ExternalAgentContinuousSessionV1 | None:
        path = self.session_path(session_id)
        if not path.is_file():
            return None
        return ExternalAgentContinuousSessionV1.model_validate_json(
            path.read_text(encoding="utf-8")
        )

    def list_sessions(self) -> list[ExternalAgentContinuousSessionV1]:
        sessions = [
            ExternalAgentContinuousSessionV1.model_validate_json(
                path.read_text(encoding="utf-8")
            )
            for path in sorted(self.root.glob("*/session.json"))
        ]
        return sorted(sessions, key=lambda item: (item.updated_at, item.id), reverse=True)

    def update_session(
        self,
        session: ExternalAgentContinuousSessionV1,
        *,
        expected_version: int,
    ) -> ExternalAgentContinuousSessionV1:
        with self._session_update_lock(session.id):
            current = self.get_session(session.id)
            if current is None:
                raise KeyError(f"external Agent session does not exist: {session.id}")
            if (
                current.version != expected_version
                or session.version != expected_version + 1
            ):
                raise ExternalAgentSessionVersionConflict(
                    current=current,
                    expected_version=expected_version,
                    new_version=session.version,
                )
            self._atomic_write(self.session_path(session.id), _json_bytes(session))
            return session

    def append_invocation(
        self,
        invocation: ExternalAgentInvocationV1,
    ) -> ExternalAgentInvocationV1:
        if self.get_session(invocation.session_id) is None:
            raise KeyError(f"unknown external Agent session: {invocation.session_id}")
        path = self.invocation_path(invocation.session_id, invocation.sequence)
        if path.exists():
            existing = ExternalAgentInvocationV1.model_validate_json(
                path.read_text(encoding="utf-8")
            )
            if existing == invocation:
                return invocation
            raise ValueError(f"external Agent invocation already exists: {invocation.id}")
        previous = self.list_invocations(invocation.session_id)
        expected_sequence = (previous[-1].sequence + 1) if previous else 1
        if invocation.sequence != expected_sequence:
            raise ValueError(
                f"invocation sequence must be {expected_sequence}, got {invocation.sequence}"
            )
        self._atomic_write(path, _json_bytes(invocation))
        return invocation

    def list_invocations(self, session_id: str) -> list[ExternalAgentInvocationV1]:
        directory = self.session_dir(session_id) / "invocations"
        return [
            ExternalAgentInvocationV1.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(directory.glob("turn-*.json"))
        ] if directory.is_dir() else []

    def write_heartbeat(
        self,
        session_id: str,
        *,
        sequence: int,
        timestamp: str | None = None,
        process_id: int | None = None,
    ) -> None:
        self._atomic_write(
            self.heartbeat_path(session_id),
            _json_bytes(
                {
                    "schema": "game-observatory.ai-player.external-agent-heartbeat.v1",
                    "session_id": session_id,
                    "sequence": sequence,
                    "timestamp": timestamp or utc_now(),
                    "process_id": process_id,
                }
            ),
        )

    def read_heartbeat(self, session_id: str) -> dict[str, Any] | None:
        path = self.heartbeat_path(session_id)
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


class _ProviderSummary:
    def __init__(self) -> None:
        self.external_session_id: str | None = None
        self.resolved_model_id: str | None = None
        self.actual_effort: Literal["medium", "high", "unsupported", "unreported"] = (
            "unreported"
        )
        self.model_evidence: Literal[
            "provider-event", "native-session-log", "accepted-cli-argument", "unreported"
        ] = "unreported"
        self.effort_evidence: Literal[
            "provider-event", "native-session-log", "accepted-cli-argument", "unreported"
        ] = "unreported"
        self.usage = ExternalAgentTokenUsageV1()
        self.final_text = ""
        self.error = ""
        self.warnings: list[str] = []
        self.terminal_completed = False
        self.provider_duration_seconds: float | None = None
        self.time_to_first_token_seconds: float | None = None
        self.provider_cost_usd: float | None = None
        self.event_count = 0

    def consume(self, provider: str, value: dict[str, Any]) -> None:
        self.event_count += 1
        event_type = str(value.get("type") or "")
        if provider == "codex-cli":
            if event_type == "thread.started":
                self.external_session_id = str(value.get("thread_id") or "") or None
            if event_type == "turn.completed":
                self._set_usage(value.get("usage"))
                self.terminal_completed = True
            if event_type == "turn.failed":
                self.error = _error_text(value)
            elif event_type == "error":
                self.warnings.append(_error_text(value))
            item = value.get("item") if isinstance(value.get("item"), dict) else {}
            if item.get("type") == "agent_message" and item.get("text"):
                self.final_text = str(item["text"])
            elif item.get("type") == "error":
                self.warnings.append(_error_text(item))
        else:
            session_id = value.get("session_id")
            if session_id:
                self.external_session_id = str(session_id)
            if event_type == "system" and value.get("subtype") == "init":
                self.resolved_model_id = str(value.get("model") or "") or None
                if self.resolved_model_id:
                    self.model_evidence = "provider-event"
            if event_type == "result":
                if value.get("result"):
                    self.final_text = str(value["result"])
                self._set_usage(value.get("usage"))
                if value.get("is_error"):
                    self.error = _error_text(value)
                else:
                    self.terminal_completed = True
                self.provider_duration_seconds = _milliseconds_to_seconds(
                    value.get("duration_ms")
                )
                self.time_to_first_token_seconds = _milliseconds_to_seconds(
                    value.get("ttft_ms")
                )
                self.provider_cost_usd = _float_or_none(value.get("total_cost_usd"))
            message = value.get("message") if isinstance(value.get("message"), dict) else {}
            if message.get("model"):
                self.resolved_model_id = str(message["model"])
                self.model_evidence = "provider-event"
            if message.get("usage"):
                self._set_usage(message["usage"])
        effort = value.get("effort") or value.get("reasoning_effort")
        if effort in {"medium", "high"}:
            self.actual_effort = effort
            self.effort_evidence = "provider-event"
        model = value.get("model")
        if isinstance(model, str) and model:
            self.resolved_model_id = model
            self.model_evidence = "provider-event"

    def _set_usage(self, raw: Any) -> None:
        if not isinstance(raw, dict):
            return
        self.usage = ExternalAgentTokenUsageV1(
            input_tokens=max(self.usage.input_tokens, _integer(raw.get("input_tokens"))),
            cached_input_tokens=max(
                self.usage.cached_input_tokens,
                _integer(raw.get("cached_input_tokens"))
                + _integer(raw.get("cache_read_input_tokens")),
            ),
            cache_creation_input_tokens=max(
                self.usage.cache_creation_input_tokens,
                _integer(raw.get("cache_creation_input_tokens")),
            ),
            output_tokens=max(self.usage.output_tokens, _integer(raw.get("output_tokens"))),
            reasoning_tokens=max(
                self.usage.reasoning_tokens,
                _integer(raw.get("reasoning_output_tokens"))
                + _integer(raw.get("reasoning_tokens")),
            ),
        )


def _integer(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _float_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, result)


def _milliseconds_to_seconds(value: Any) -> float | None:
    milliseconds = _float_or_none(value)
    return None if milliseconds is None else milliseconds / 1000


def _error_text(value: Any) -> str:
    if isinstance(value, str):
        return value[-8000:]
    if not isinstance(value, dict):
        return str(value)[-8000:]
    candidate = value.get("error") or value.get("message") or value.get("result") or value
    return str(candidate)[-8000:]


class _ExternalAgentWaitTimeout(RuntimeError):
    def __init__(
        self,
        reason: Literal["hard_wall_clock", "no_meaningful_progress"],
    ) -> None:
        self.reason = reason
        super().__init__(reason)


def _is_meaningful_progress(
    provider: Literal["codex-cli", "claude-code-cli"],
    event: dict[str, Any],
) -> bool:
    event_type = str(event.get("type") or "")
    if provider == "codex-cli":
        if event_type in {"turn.completed", "turn.failed"}:
            return True
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        return item.get("type") in {
            "agent_message",
            "command_execution",
            "mcp_tool_call",
            "tool_call",
        }
    if event_type in {"assistant", "result"}:
        return True
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    content = message.get("content") if isinstance(message.get("content"), list) else []
    return any(
        isinstance(block, dict) and block.get("type") in {"text", "tool_use"}
        for block in content
    )


def _is_provider_maintenance(
    provider: Literal["codex-cli", "claude-code-cli"],
    event: dict[str, Any],
) -> bool:
    return bool(
        provider == "claude-code-cli"
        and event.get("type") == "system"
        and event.get("subtype") == "status"
        and event.get("status") == "compacting"
    )


async def _wait_for_process_progress(
    process: asyncio.subprocess.Process,
    progress_event: asyncio.Event,
    maintenance_event: asyncio.Event,
    *,
    hard_timeout_seconds: float,
    no_progress_timeout_seconds: float | None,
) -> Literal["completed", "hard_wall_clock", "no_meaningful_progress"]:
    loop = asyncio.get_running_loop()
    hard_deadline = loop.time() + hard_timeout_seconds
    progress_deadline = (
        loop.time() + no_progress_timeout_seconds
        if no_progress_timeout_seconds is not None
        else hard_deadline
    )
    process_wait = asyncio.create_task(process.wait())
    try:
        while True:
            now = loop.time()
            if now >= hard_deadline:
                return "hard_wall_clock"
            deadline = min(hard_deadline, progress_deadline)
            wait_seconds = max(0.0, deadline - now)
            progress_wait = asyncio.create_task(progress_event.wait())
            maintenance_wait = asyncio.create_task(maintenance_event.wait())
            done, _ = await asyncio.wait(
                {process_wait, progress_wait, maintenance_wait},
                timeout=wait_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if process_wait in done:
                progress_wait.cancel()
                maintenance_wait.cancel()
                await asyncio.gather(
                    progress_wait,
                    maintenance_wait,
                    return_exceptions=True,
                )
                return "completed"
            if maintenance_wait in done:
                maintenance_event.clear()
                progress_deadline = hard_deadline
            if progress_wait in done:
                progress_event.clear()
                now = loop.time()
                if now >= hard_deadline:
                    if maintenance_wait not in done:
                        maintenance_wait.cancel()
                    await asyncio.gather(
                        progress_wait,
                        maintenance_wait,
                        return_exceptions=True,
                    )
                    return "hard_wall_clock"
                if no_progress_timeout_seconds is not None:
                    progress_deadline = now + no_progress_timeout_seconds
            if done:
                for waiter in (progress_wait, maintenance_wait):
                    if waiter not in done:
                        waiter.cancel()
                await asyncio.gather(
                    progress_wait,
                    maintenance_wait,
                    return_exceptions=True,
                )
                continue
            progress_wait.cancel()
            maintenance_wait.cancel()
            await asyncio.gather(
                progress_wait,
                maintenance_wait,
                return_exceptions=True,
            )
            now = loop.time()
            if now >= hard_deadline:
                return "hard_wall_clock"
            if no_progress_timeout_seconds is not None and now >= progress_deadline:
                return "no_meaningful_progress"
    finally:
        if not process_wait.done():
            process_wait.cancel()
            await asyncio.gather(process_wait, return_exceptions=True)


def _process_started_at(pid: int) -> float | None:
    try:
        import psutil  # type: ignore[import-not-found]

        return float(psutil.Process(pid).create_time())
    except Exception:
        return None


def _process_identity_alive(pid: int | None, started_at: float | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        import psutil  # type: ignore[import-not-found]

        process = psutil.Process(pid)
        if started_at is None:
            return process.is_running()
        return process.is_running() and abs(process.create_time() - started_at) < 0.01
    except ImportError:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    except Exception:
        return False


class _ExternalAgentSessionLease:
    """Exclusive, crash-visible ownership for one native provider session turn."""

    def __init__(self, path: Path, payload: dict[str, Any]) -> None:
        self.path = path
        self.payload = payload

    @classmethod
    def acquire(
        cls,
        ledger: ExternalAgentSessionLedger,
        session_id: str,
        *,
        operation: str,
        prompt_sha256: str,
    ) -> "_ExternalAgentSessionLease":
        path = ledger.session_dir(session_id) / "invocation.lease.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "game-observatory.ai-player.external-agent-session-lease.v1",
            "session_id": session_id,
            "operation": operation,
            "prompt_sha256": prompt_sha256,
            "nonce": uuid.uuid4().hex,
            "owner_pid": os.getpid(),
            "owner_started_at": _process_started_at(os.getpid()),
            "provider_pid": None,
            "provider_started_at": None,
            "acquired_at": utc_now(),
        }
        encoded = _json_bytes(payload)
        for _ in range(3):
            try:
                descriptor = os.open(
                    path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                )
            except FileExistsError:
                try:
                    existing = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"external Agent session lease is unreadable: {path}"
                    ) from exc
                owner_alive = _process_identity_alive(
                    existing.get("owner_pid"), existing.get("owner_started_at")
                )
                provider_alive = _process_identity_alive(
                    existing.get("provider_pid"), existing.get("provider_started_at")
                )
                if owner_alive or provider_alive:
                    raise RuntimeError(
                        "external Agent session already has a live invocation lease: "
                        f"{session_id}"
                    )
                stale_path = path.with_name(
                    f"{path.name}.stale-{uuid.uuid4().hex}"
                )
                try:
                    path.replace(stale_path)
                    stale_path.unlink(missing_ok=True)
                except FileNotFoundError:
                    pass
                continue
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            return cls(path, payload)
        raise RuntimeError(f"could not acquire external Agent session lease: {session_id}")

    def bind_provider(self, pid: int) -> None:
        current = json.loads(self.path.read_text(encoding="utf-8"))
        if current.get("nonce") != self.payload["nonce"]:
            raise RuntimeError("external Agent session lease ownership changed")
        self.payload.update(
            {
                "provider_pid": pid,
                "provider_started_at": _process_started_at(pid),
                "provider_bound_at": utc_now(),
            }
        )
        ExternalAgentSessionLedger._atomic_write(self.path, _json_bytes(self.payload))

    def release(self) -> None:
        try:
            current = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return
        if current.get("nonce") == self.payload["nonce"]:
            self.path.unlink(missing_ok=True)


class ContinuousExternalAgentRunner:
    """Run one provider turn while preserving the provider's native session identity."""

    def __init__(
        self,
        ledger: ExternalAgentSessionLedger,
        *,
        codex_executable: str = "codex",
        claude_executable: str = "claude",
        heartbeat_interval_seconds: float = 20,
        heartbeat_hook: Callable[[str, int], None] | None = None,
        timeout_cleanup_hook: Callable[[ExternalAgentTimedOutProcessV1], Any] | None = None,
    ) -> None:
        self.ledger = ledger
        self.codex_executable = codex_executable
        self.claude_executable = claude_executable
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.heartbeat_hook = heartbeat_hook
        self.timeout_cleanup_hook = timeout_cleanup_hook

    async def start(
        self,
        session: ExternalAgentContinuousSessionV1,
        *,
        prompt: str,
        cwd: Path,
        timeout_seconds: float,
        no_progress_timeout_seconds: float | None = None,
        image_paths: list[Path] | tuple[Path, ...] = (),
    ) -> tuple[ExternalAgentContinuousSessionV1, ExternalAgentInvocationV1]:
        if session.status != "starting" or session.invocation_count != 0:
            raise ValueError("a new external Agent session must be in starting state")
        lease = _ExternalAgentSessionLease.acquire(
            self.ledger,
            session.id,
            operation="start",
            prompt_sha256=_sha256_bytes(prompt.encode("utf-8")),
        )
        try:
            self.ledger.create_session(session)
            invocation = await self._invoke(
                session,
                operation="start",
                prompt=prompt,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                no_progress_timeout_seconds=no_progress_timeout_seconds,
                image_paths=image_paths,
                lease=lease,
            )
            return self._commit_invocation(session, invocation), invocation
        finally:
            lease.release()

    async def resume(
        self,
        session_id: str,
        *,
        prompt: str,
        cwd: Path,
        timeout_seconds: float,
        no_progress_timeout_seconds: float | None = None,
        image_paths: list[Path] | tuple[Path, ...] = (),
    ) -> tuple[ExternalAgentContinuousSessionV1, ExternalAgentInvocationV1]:
        lease = _ExternalAgentSessionLease.acquire(
            self.ledger,
            session_id,
            operation="resume",
            prompt_sha256=_sha256_bytes(prompt.encode("utf-8")),
        )
        try:
            session = self.ledger.get_session(session_id)
            if session is None:
                raise KeyError(f"unknown external Agent session: {session_id}")
            recovered = self.recover_orphaned_invocation(session_id)
            if recovered is not None:
                return recovered
            if (
                session.status not in {"active", "suspended"}
                or not session.external_session_id
            ):
                raise ValueError(
                    f"external Agent session cannot resume from {session.status}"
                )
            invocation = await self._invoke(
                session,
                operation="resume",
                prompt=prompt,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                no_progress_timeout_seconds=no_progress_timeout_seconds,
                image_paths=image_paths,
                lease=lease,
            )
            return self._commit_invocation(session, invocation), invocation
        finally:
            lease.release()

    def recover_orphaned_invocation(
        self,
        session_id: str,
    ) -> tuple[ExternalAgentContinuousSessionV1, ExternalAgentInvocationV1] | None:
        """Settle a partial event log left by a killed wrapper before starting another turn."""

        session = self.ledger.get_session(session_id)
        if session is None:
            raise KeyError(f"unknown external Agent session: {session_id}")
        sequence = session.invocation_count + 1
        event_path = self.ledger.event_log_path(session.id, sequence)
        invocation_path = self.ledger.invocation_path(session.id, sequence)
        if invocation_path.is_file():
            invocation = ExternalAgentInvocationV1.model_validate_json(
                invocation_path.read_text(encoding="utf-8")
            )
            return self._commit_invocation(session, invocation), invocation
        if not event_path.is_file() or event_path.stat().st_size == 0:
            return None
        summary = _ProviderSummary()
        stdout_tail: deque[str] = deque(maxlen=200)
        stderr_tail: deque[str] = deque(maxlen=200)
        received_at: list[str] = []
        for line in event_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            timestamp = record.get("received_at")
            if isinstance(timestamp, str):
                received_at.append(timestamp)
            stream = record.get("stream")
            event = record.get("event")
            text = record.get("text")
            rendered = (
                json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                if isinstance(event, dict)
                else str(text or "")
            )
            if stream == "stdout":
                stdout_tail.append(rendered)
                if isinstance(event, dict):
                    summary.consume(session.provider, event)
            elif stream == "stderr":
                stderr_tail.append(rendered)
        intent = self.ledger.get_invocation_intent(session.id, sequence)
        terminal_completed = bool(
            intent is not None
            and summary.terminal_completed
            and summary.external_session_id
        )
        started_at = (
            intent.started_at
            if intent is not None
            else received_at[0]
            if received_at
            else utc_now()
        )
        message_path = self.ledger.last_message_path(session.id, sequence)
        message_existed = message_path.is_file()
        duration_warning = None
        if terminal_completed and received_at:
            completed_at = received_at[-1]
        else:
            boundary_candidates = list(received_at)
            for boundary_path in (
                event_path,
                message_path if message_existed else None,
            ):
                if boundary_path is not None and boundary_path.is_file():
                    boundary_candidates.append(
                        datetime.fromtimestamp(
                            boundary_path.stat().st_mtime,
                            tz=timezone.utc,
                        ).isoformat()
                    )
            parsed_boundaries: list[tuple[datetime, str]] = []
            for boundary in boundary_candidates:
                try:
                    parsed = datetime.fromisoformat(boundary.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                    parsed_boundaries.append((parsed, boundary))
            if parsed_boundaries:
                completed_at = max(parsed_boundaries, key=lambda item: item[0])[1]
                duration_warning = (
                    "duration is a persisted event/file lower bound; "
                    "provider completion is unknown"
                )
            else:
                completed_at = started_at
                duration_warning = (
                    "duration is unknown; recorded as a zero-second lower bound"
                )
        duration_seconds = _elapsed_seconds(started_at, completed_at)
        if not message_path.is_file():
            message_path.parent.mkdir(parents=True, exist_ok=True)
            message_path.write_text(summary.final_text, encoding="utf-8", newline="\n")
        external_session_id = summary.external_session_id or session.external_session_id
        resolved_model_id = summary.resolved_model_id or session.resolved_model_id
        actual_effort = summary.actual_effort
        if session.provider == "codex-cli" and external_session_id:
            native_model, native_effort = _resolve_codex_session_metadata(
                external_session_id
            )
            resolved_model_id = native_model or resolved_model_id
            actual_effort = native_effort or actual_effort
            if native_model:
                summary.model_evidence = "native-session-log"
            if native_effort:
                summary.effort_evidence = "native-session-log"
        if resolved_model_id is None and summary.terminal_completed:
            resolved_model_id = session.model_selector
            summary.model_evidence = "accepted-cli-argument"
        if actual_effort == "unreported" and summary.terminal_completed:
            actual_effort = session.requested_effort
            summary.effort_evidence = "accepted-cli-argument"
        event_bytes = event_path.read_bytes()
        message_bytes = message_path.read_bytes()
        invocation = ExternalAgentInvocationV1(
            id=f"external-agent-turn.{session.id}.{sequence:04d}",
            session_id=session.id,
            sequence=sequence,
            operation=(
                intent.operation
                if intent is not None
                else self._expected_operation(session)
            ),
            provider=session.provider,
            model_selector=session.model_selector,
            requested_effort=session.requested_effort,
            actual_effort=actual_effort,
            permission_mode=session.permission_mode,
            status="succeeded" if terminal_completed else "timed_out",
            prompt_sha256=intent.prompt_sha256 if intent is not None else None,
            external_session_id=external_session_id,
            resolved_model_id=resolved_model_id,
            model_evidence=summary.model_evidence,
            effort_evidence=summary.effort_evidence,
            usage=summary.usage,
            input_images=intent.input_images if intent is not None else [],
            duration_seconds=duration_seconds,
            provider_duration_seconds=summary.provider_duration_seconds,
            time_to_first_token_seconds=summary.time_to_first_token_seconds,
            provider_cost_usd=summary.provider_cost_usd,
            exit_code=None,
            event_count=summary.event_count,
            event_log_path=str(event_path.relative_to(self.ledger.root)),
            event_log_sha256=_sha256_bytes(event_bytes),
            last_message_path=str(message_path.relative_to(self.ledger.root)),
            last_message_sha256=_sha256_bytes(message_bytes),
            stdout_tail="\n".join(stdout_tail)[-16000:],
            stderr_tail="\n".join(stderr_tail)[-16000:],
            warnings=[
                *summary.warnings,
                "recovered orphaned invocation",
                *([duration_warning] if duration_warning else []),
            ],
            error=(
                None
                if terminal_completed
                else "external Agent runner exited before invocation commit"
            ),
            started_at=started_at,
            completed_at=completed_at,
        )
        return self._commit_invocation(session, invocation), invocation

    def _validate_invocation_against_session(
        self,
        session: ExternalAgentContinuousSessionV1,
        invocation: ExternalAgentInvocationV1,
    ) -> None:
        expected_sequence = session.invocation_count + 1
        expected_operation = self._expected_operation(session)
        if (
            invocation.session_id != session.id
            or invocation.sequence != expected_sequence
            or invocation.id
            != f"external-agent-turn.{session.id}.{expected_sequence:04d}"
            or invocation.operation != expected_operation
            or invocation.provider != session.provider
            or invocation.model_selector != session.model_selector
            or invocation.requested_effort != session.requested_effort
            or invocation.permission_mode != session.permission_mode
        ):
            raise ValueError("external Agent invocation does not match the session head")
        intent = self.ledger.get_invocation_intent(session.id, expected_sequence)
        if intent is None:
            raise ValueError("external Agent invocation intent is missing")
        if (
            intent.operation != invocation.operation
            or intent.prompt_sha256 != invocation.prompt_sha256
            or intent.input_images != invocation.input_images
        ):
            raise ValueError("external Agent invocation does not match its immutable intent")
        if (
            expected_operation == "resume"
            and session.external_session_id
            and invocation.external_session_id != session.external_session_id
        ):
            raise ValueError("external Agent provider session id changed during resume")
        for relative, expected_hash, label in (
            (invocation.event_log_path, invocation.event_log_sha256, "event log"),
            (invocation.last_message_path, invocation.last_message_sha256, "last message"),
        ):
            path = (self.ledger.root / relative).resolve()
            if not path.is_relative_to(self.ledger.root) or not path.is_file():
                raise ValueError(f"external Agent {label} is missing or escaped the ledger")
            if _sha256_bytes(path.read_bytes()) != expected_hash:
                raise ValueError(f"external Agent {label} hash changed")

    @staticmethod
    def _expected_operation(
        session: ExternalAgentContinuousSessionV1,
    ) -> Literal["start", "resume"]:
        """Map a local generation head onto its continuous native provider session."""

        if session.invocation_count > 0 or session.external_session_id:
            return "resume"
        return "start"

    def _previous_native_cumulative_usage(
        self,
        session: ExternalAgentContinuousSessionV1,
    ) -> ExternalAgentTokenUsageV1:
        current_invocations = self.ledger.list_invocations(session.id)
        if session.invocation_count:
            if len(current_invocations) < session.invocation_count:
                raise ValueError(
                    "external Agent invocation history is behind the session head"
                )
            # A provider receipt may already have been appended before a wrapper
            # crash or a competing interrupt advances the session head.  Account
            # from the invocation named by the caller's immutable head, not from
            # the newest receipt on disk, so retrying the same commit is idempotent.
            return current_invocations[session.invocation_count - 1].usage
        previous_session_id = session.previous_session_id
        visited: set[str] = set()
        while previous_session_id and previous_session_id not in visited:
            visited.add(previous_session_id)
            previous = self.ledger.get_session(previous_session_id)
            if previous is None:
                break
            if (
                previous.provider != session.provider
                or previous.external_session_id != session.external_session_id
            ):
                break
            previous_invocations = self.ledger.list_invocations(previous.id)
            if previous_invocations:
                return previous_invocations[-1].usage
            previous_session_id = previous.previous_session_id
        return ExternalAgentTokenUsageV1()

    @staticmethod
    def _usage_increment(
        session: ExternalAgentContinuousSessionV1,
        invocation: ExternalAgentInvocationV1,
        previous_cumulative: ExternalAgentTokenUsageV1,
    ) -> ExternalAgentTokenUsageV1:
        if session.provider != "codex-cli":
            return invocation.usage

        def delta(current: int, previous: int) -> int:
            return current - previous if current >= previous else current

        return ExternalAgentTokenUsageV1(
            input_tokens=delta(
                invocation.usage.input_tokens,
                previous_cumulative.input_tokens,
            ),
            cached_input_tokens=delta(
                invocation.usage.cached_input_tokens,
                previous_cumulative.cached_input_tokens,
            ),
            cache_creation_input_tokens=delta(
                invocation.usage.cache_creation_input_tokens,
                previous_cumulative.cache_creation_input_tokens,
            ),
            output_tokens=delta(
                invocation.usage.output_tokens,
                previous_cumulative.output_tokens,
            ),
            reasoning_tokens=delta(
                invocation.usage.reasoning_tokens,
                previous_cumulative.reasoning_tokens,
            ),
        )

    @staticmethod
    def _is_interrupt_only_head(
        stale: ExternalAgentContinuousSessionV1,
        current: ExternalAgentContinuousSessionV1,
    ) -> bool:
        """Recognize the exact head transition made by ``explore interrupt``."""

        if (
            stale.status != "active"
            or current.status != "suspended"
            or current.version != stale.version + 1
        ):
            return False
        stale_payload = stale.model_dump(mode="json", by_alias=True)
        current_payload = current.model_dump(mode="json", by_alias=True)
        for key in ("version", "status", "last_heartbeat_at", "updated_at"):
            current_payload[key] = stale_payload[key]
        return current_payload == stale_payload

    @staticmethod
    def _session_after_invocation(
        head: ExternalAgentContinuousSessionV1,
        invocation: ExternalAgentInvocationV1,
        usage_increment: ExternalAgentTokenUsageV1,
        *,
        preserve_suspension: bool,
    ) -> ExternalAgentContinuousSessionV1:
        succeeded = invocation.status == "succeeded"
        payload = head.model_dump(mode="json", by_alias=True)
        payload.update(
            {
                "version": head.version + 1,
                "external_session_id": invocation.external_session_id
                or head.external_session_id,
                "resolved_model_id": invocation.resolved_model_id
                or head.resolved_model_id,
                "actual_effort": invocation.actual_effort,
                "status": (
                    "closed"
                    if head.status == "closed"
                    else "suspended"
                    if preserve_suspension
                    else "active"
                    if succeeded
                    else "suspended"
                    if invocation.external_session_id or head.external_session_id
                    else "failed"
                ),
                "invocation_count": head.invocation_count + 1,
                "heartbeat_sequence": head.heartbeat_sequence + 1,
                "input_tokens": head.input_tokens + usage_increment.input_tokens,
                "cached_input_tokens": head.cached_input_tokens
                + usage_increment.cached_input_tokens,
                "cache_creation_input_tokens": head.cache_creation_input_tokens
                + usage_increment.cache_creation_input_tokens,
                "output_tokens": head.output_tokens + usage_increment.output_tokens,
                "reasoning_tokens": head.reasoning_tokens
                + usage_increment.reasoning_tokens,
                "total_duration_seconds": head.total_duration_seconds
                + invocation.duration_seconds,
                "last_invocation_id": invocation.id,
                "last_error": None if succeeded else invocation.error,
                "last_heartbeat_at": invocation.completed_at,
                "updated_at": invocation.completed_at,
            }
        )
        return ExternalAgentContinuousSessionV1.model_validate(payload)

    def _session_already_contains_invocation(
        self,
        stale: ExternalAgentContinuousSessionV1,
        current: ExternalAgentContinuousSessionV1,
        invocation: ExternalAgentInvocationV1,
        usage_increment: ExternalAgentTokenUsageV1,
    ) -> bool:
        """Prove that a stale retry's exact receipt is already in the session head."""

        normal = self._session_after_invocation(
            stale,
            invocation,
            usage_increment,
            preserve_suspension=False,
        )
        candidates = [normal]
        if stale.status == "active" and stale.external_session_id:
            interrupted_payload = stale.model_dump(mode="json", by_alias=True)
            interrupted_payload.update(
                {
                    "version": stale.version + 1,
                    "status": "suspended",
                }
            )
            interrupted_head = ExternalAgentContinuousSessionV1.model_validate(
                interrupted_payload
            )
            candidates.append(
                self._session_after_invocation(
                    interrupted_head,
                    invocation,
                    usage_increment,
                    preserve_suspension=True,
                )
            )
        current_payload = current.model_dump(mode="json", by_alias=True)
        for candidate in candidates:
            candidate_payload = candidate.model_dump(mode="json", by_alias=True)
            # The interrupt's wall-clock fields are intentionally overwritten by
            # the terminal provider receipt, so every persisted value is exact.
            if current_payload == candidate_payload:
                return True
        return False

    def _commit_invocation(
        self,
        session: ExternalAgentContinuousSessionV1,
        invocation: ExternalAgentInvocationV1,
    ) -> ExternalAgentContinuousSessionV1:
        self._validate_invocation_against_session(session, invocation)
        usage_increment = self._usage_increment(
            session,
            invocation,
            self._previous_native_cumulative_usage(session),
        )
        self.ledger.append_invocation(invocation)
        last_conflict: ExternalAgentSessionVersionConflict | None = None
        for _attempt in range(3):
            current = self.ledger.get_session(session.id)
            if current is None:
                raise KeyError(f"unknown external Agent session: {session.id}")
            if self._session_already_contains_invocation(
                session,
                current,
                invocation,
                usage_increment,
            ):
                return current
            if current == session:
                preserve_suspension = False
            elif self._is_interrupt_only_head(session, current):
                preserve_suspension = True
            else:
                raise ExternalAgentSessionVersionConflict(
                    current=current,
                    expected_version=session.version,
                    new_version=session.version + 1,
                )
            updated = self._session_after_invocation(
                current,
                invocation,
                usage_increment,
                preserve_suspension=preserve_suspension,
            )
            try:
                return self.ledger.update_session(
                    updated,
                    expected_version=current.version,
                )
            except ExternalAgentSessionVersionConflict as exc:
                # An interrupt may win after our read. Re-read and prove the
                # resulting head on the next iteration; unrelated writes remain
                # a hard conflict and are never swallowed.
                last_conflict = exc
        assert last_conflict is not None
        raise last_conflict

    async def _invoke(
        self,
        session: ExternalAgentContinuousSessionV1,
        *,
        operation: Literal["start", "resume"],
        prompt: str,
        cwd: Path,
        timeout_seconds: float,
        no_progress_timeout_seconds: float | None,
        image_paths: list[Path] | tuple[Path, ...],
        lease: _ExternalAgentSessionLease | None = None,
    ) -> ExternalAgentInvocationV1:
        if not prompt.strip():
            raise ValueError("external Agent prompt must not be blank")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if no_progress_timeout_seconds is not None and no_progress_timeout_seconds <= 0:
            raise ValueError("no_progress_timeout_seconds must be positive")
        working_directory = cwd.expanduser().resolve()
        if not working_directory.is_dir():
            raise ValueError(f"external Agent cwd does not exist: {working_directory}")
        sequence = session.invocation_count + 1
        images = _prepare_image_inputs(image_paths)
        invocation_id = f"external-agent-turn.{session.id}.{sequence:04d}"
        event_path = self.ledger.event_log_path(session.id, sequence)
        message_path = self.ledger.last_message_path(session.id, sequence)
        event_path.parent.mkdir(parents=True, exist_ok=True)
        message_path.parent.mkdir(parents=True, exist_ok=True)
        provider_session_id = session.external_session_id
        if session.provider == "claude-code-cli" and operation == "start":
            provider_session_id = str(uuid.uuid4())
        command = self._build_command(
            session,
            operation=operation,
            cwd=working_directory,
            message_path=message_path,
            provider_session_id=provider_session_id,
            image_paths=tuple(Path(item.path) for item in images),
        )
        started_at = utc_now()
        self.ledger.create_invocation_intent(
            ExternalAgentInvocationIntentV1(
                session_id=session.id,
                sequence=sequence,
                operation=operation,
                prompt_sha256=_sha256_bytes(prompt.encode("utf-8")),
                input_images=images,
                started_at=started_at,
            )
        )
        loop = asyncio.get_running_loop()
        started_monotonic = loop.time()
        summary = _ProviderSummary()
        stdout_tail: deque[str] = deque(maxlen=200)
        stderr_tail: deque[str] = deque(maxlen=200)
        error = ""
        timed_out = False
        timeout_reason: Literal["hard_wall_clock", "no_meaningful_progress"] | None = None
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(working_directory),
                env=_process_environment(
                    {
                        EXTERNAL_AGENT_INVOCATION_ID_ENV: invocation_id,
                        EXTERNAL_AGENT_SESSION_ID_ENV: session.id,
                        EXTERNAL_AGENT_INVOCATION_SEQUENCE_ENV: str(sequence),
                    }
                ),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # Claude/Codex stream one JSON event per line. Tool results may
                # legitimately contain large canonical status payloads; the
                # asyncio default (~64 KiB) can stop the pump while the provider
                # continues acting, causing a false no-progress timeout.
                limit=8 * 1024 * 1024,
                **headless_process_kwargs(),
            )
            if lease is not None:
                lease.bind_provider(process.pid)
            assert process.stdin is not None
            process.stdin.write(
                _provider_input_bytes(session.provider, prompt=prompt, images=images)
            )
            await process.stdin.drain()
            process.stdin.close()
            heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(session.id, process.pid, sequence)
            )
            progress_event = asyncio.Event()
            maintenance_event = asyncio.Event()
            pumps = [
                asyncio.create_task(
                    self._pump(
                        process.stdout,
                        "stdout",
                        event_path,
                        summary,
                        stdout_tail,
                        session.provider,
                        session.id,
                        sequence,
                        process.pid,
                        progress_event,
                        maintenance_event,
                    )
                ),
                asyncio.create_task(
                    self._pump(
                        process.stderr,
                        "stderr",
                        event_path,
                        summary,
                        stderr_tail,
                        session.provider,
                        session.id,
                        sequence,
                        process.pid,
                        progress_event,
                        maintenance_event,
                    )
                ),
            ]
            try:
                cleanup_reserve = min(5.0, max(1.0, timeout_seconds * 0.1))
                provider_timeout = max(0.1, timeout_seconds - cleanup_reserve)
                wait_outcome = await _wait_for_process_progress(
                    process,
                    progress_event,
                    maintenance_event,
                    hard_timeout_seconds=provider_timeout,
                    no_progress_timeout_seconds=no_progress_timeout_seconds,
                )
                if wait_outcome != "completed":
                    timed_out = True
                    timeout_reason = wait_outcome
                    error = (
                        "external Agent produced no meaningful progress for "
                        f"{no_progress_timeout_seconds:g}s"
                        if wait_outcome == "no_meaningful_progress"
                        and no_progress_timeout_seconds is not None
                        else f"external Agent turn timed out after {timeout_seconds:g}s"
                    )
                    await _terminate_process_tree(process)
                    if process.returncode is None:
                        raise RuntimeError(
                            "external Agent process tree did not terminate before timeout cleanup"
                        )
                    if self.timeout_cleanup_hook is not None:
                        try:
                            self.timeout_cleanup_hook(
                                ExternalAgentTimedOutProcessV1(
                                    invocation_id=invocation_id,
                                    session_id=session.id,
                                    sequence=sequence,
                                    process_id=process.pid,
                                    terminated_at=utc_now(),
                                )
                            )
                        except Exception as exc:  # noqa: BLE001 - preserve timed-out invocation
                            error = f"{error}; timeout resource cleanup failed: {exc}"
            finally:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*pumps, return_exceptions=True), timeout=3
                    )
                except asyncio.TimeoutError:
                    for pump in pumps:
                        pump.cancel()
                    await asyncio.gather(*pumps, return_exceptions=True)
        except FileNotFoundError:
            error = f"external Agent executable not found: {command[0]}"
        except OSError as exc:
            error = str(exc)
        completed_at = utc_now()
        duration = max(0.0, loop.time() - started_monotonic)
        if not event_path.exists():
            event_path.write_bytes(b"")
        final_text = summary.final_text
        if message_path.is_file():
            final_text = message_path.read_text(encoding="utf-8", errors="replace") or final_text
        else:
            message_path.write_text(final_text, encoding="utf-8", newline="\n")
        exit_code = process.returncode if process is not None else None
        if not error and summary.error:
            error = summary.error
        if not error and exit_code not in {0, None}:
            error = "\n".join(stderr_tail)[-8000:] or f"external Agent exited {exit_code}"
        provider_completed = summary.terminal_completed and exit_code in {0, None}
        if provider_completed and not timed_out:
            error = ""
        status: Literal["succeeded", "failed", "timed_out"] = (
            "timed_out"
            if timed_out
            else "succeeded"
            if provider_completed and not error
            else "failed"
            if error or exit_code not in {0, None}
            else "succeeded"
        )
        external_session_id = summary.external_session_id or provider_session_id
        if status == "succeeded" and not external_session_id:
            status = "failed"
            error = "provider completed without a resumable session id"
        event_bytes = event_path.read_bytes()
        message_bytes = message_path.read_bytes()
        resolved_model_id = summary.resolved_model_id
        actual_effort = summary.actual_effort
        if session.provider == "codex-cli" and external_session_id:
            native_model, native_effort = _resolve_codex_session_metadata(
                external_session_id
            )
            resolved_model_id = native_model or resolved_model_id
            actual_effort = native_effort or actual_effort
            if native_model:
                summary.model_evidence = "native-session-log"
            if native_effort:
                summary.effort_evidence = "native-session-log"
        if status == "succeeded" and resolved_model_id is None:
            resolved_model_id = session.model_selector
            summary.model_evidence = "accepted-cli-argument"
        if status == "succeeded" and actual_effort == "unreported":
            actual_effort = session.requested_effort
            summary.effort_evidence = "accepted-cli-argument"
        return ExternalAgentInvocationV1(
            id=invocation_id,
            session_id=session.id,
            sequence=sequence,
            operation=operation,
            provider=session.provider,
            model_selector=session.model_selector,
            requested_effort=session.requested_effort,
            actual_effort=actual_effort,
            permission_mode=session.permission_mode,
            status=status,
            timeout_reason=timeout_reason,
            prompt_sha256=_sha256_bytes(prompt.encode("utf-8")),
            external_session_id=external_session_id,
            resolved_model_id=resolved_model_id,
            model_evidence=summary.model_evidence,
            effort_evidence=summary.effort_evidence,
            usage=summary.usage,
            input_images=images,
            duration_seconds=duration,
            provider_duration_seconds=summary.provider_duration_seconds,
            time_to_first_token_seconds=summary.time_to_first_token_seconds,
            provider_cost_usd=summary.provider_cost_usd,
            exit_code=exit_code,
            event_count=summary.event_count,
            event_log_path=str(event_path.relative_to(self.ledger.root)),
            event_log_sha256=_sha256_bytes(event_bytes),
            last_message_path=str(message_path.relative_to(self.ledger.root)),
            last_message_sha256=_sha256_bytes(message_bytes),
            stdout_tail="\n".join(stdout_tail)[-16000:],
            stderr_tail="\n".join(stderr_tail)[-16000:],
            warnings=summary.warnings,
            error=error[-8000:] if error else None,
            started_at=started_at,
            completed_at=completed_at,
        )

    def _build_command(
        self,
        session: ExternalAgentContinuousSessionV1,
        *,
        operation: Literal["start", "resume"],
        cwd: Path,
        message_path: Path,
        provider_session_id: str | None,
        image_paths: tuple[Path, ...] = (),
    ) -> list[str]:
        if session.provider == "codex-cli":
            executable = _resolve_executable(self.codex_executable)
            _ensure_codex_game_player_profile()
            if operation == "start":
                command = [
                    executable,
                    "exec",
                    "--profile",
                    _CODEX_GAME_PLAYER_PROFILE,
                    "--disable",
                    "hooks",
                    "--json",
                    "--cd",
                    str(cwd),
                    "--output-last-message",
                    str(message_path),
                    "--model",
                    session.model_selector,
                    "-c",
                    f'model_reasoning_effort="{session.requested_effort}"',
                ]
                if session.permission_mode == "trusted-bypass":
                    command.append("--dangerously-bypass-approvals-and-sandbox")
                else:
                    sandbox = "read-only" if session.permission_mode == "readonly" else "workspace-write"
                    command.extend(["--sandbox", sandbox])
                for image_path in image_paths:
                    command.extend(["--image", str(image_path)])
                command.append("-")
                return command
            command = [
                executable,
                "exec",
                "--profile",
                _CODEX_GAME_PLAYER_PROFILE,
                "resume",
                "--disable",
                "hooks",
                "--json",
                "--output-last-message",
                str(message_path),
                "--model",
                session.model_selector,
                "-c",
                f'model_reasoning_effort="{session.requested_effort}"',
            ]
            if session.permission_mode == "trusted-bypass":
                command.append("--dangerously-bypass-approvals-and-sandbox")
            else:
                sandbox = (
                    "read-only"
                    if session.permission_mode == "readonly"
                    else "workspace-write"
                )
                # `codex exec resume` has no --sandbox option.  Without an
                # explicit config override later resumed turns can fall back to
                # read-only even though the canonical Session is workspace-write.
                command.extend(
                    [
                        "-c",
                        f'sandbox_mode="{sandbox}"',
                        "-c",
                        'approval_policy="never"',
                    ]
                )
            for image_path in image_paths:
                command.extend(["--image", str(image_path)])
            command.extend([str(provider_session_id), "-"])
            return command
        executable = _resolve_executable(self.claude_executable)
        permission = {
            "readonly": "dontAsk",
            "workspace-write": "auto",
            "trusted-bypass": "bypassPermissions",
        }[session.permission_mode]
        offline_benchmark = session.phase_id in {"EA-3.B0", "EA-3.B1"}
        command = [
            executable,
            "-p",
            "--system-prompt" if offline_benchmark else "--append-system-prompt",
            _CLAUDE_GAME_WORKER_SYSTEM_PROMPT,
            "--model",
            session.model_selector,
            "--effort",
            session.requested_effort,
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            permission,
        ]
        if offline_benchmark:
            command.extend(
                [
                    "--safe-mode",
                    "--disable-slash-commands",
                    "--strict-mcp-config",
                    "--tools",
                    "",
                ]
            )
        if session.permission_mode == "trusted-bypass":
            command.append("--dangerously-skip-permissions")
        if image_paths:
            command.extend(["--input-format", "stream-json"])
        command.extend(
            ["--session-id", str(provider_session_id)]
            if operation == "start"
            else ["--resume", str(provider_session_id)]
        )
        return command

    async def _heartbeat_loop(self, session_id: str, process_id: int, sequence: int) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval_seconds)
            self.ledger.write_heartbeat(
                session_id,
                sequence=sequence,
                process_id=process_id,
            )
            if self.heartbeat_hook is not None:
                self.heartbeat_hook(session_id, sequence)

    async def _pump(
        self,
        stream: asyncio.StreamReader | None,
        stream_name: Literal["stdout", "stderr"],
        event_path: Path,
        summary: _ProviderSummary,
        tail: deque[str],
        provider: Literal["codex-cli", "claude-code-cli"],
        session_id: str,
        sequence: int,
        process_id: int,
        progress_event: asyncio.Event,
        maintenance_event: asyncio.Event,
    ) -> None:
        if stream is None:
            return
        with event_path.open("a", encoding="utf-8", newline="\n") as handle:
            while True:
                raw = await stream.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                tail.append(line)
                parsed: dict[str, Any] | None = None
                if stream_name == "stdout":
                    try:
                        candidate = json.loads(line)
                        parsed = candidate if isinstance(candidate, dict) else None
                    except json.JSONDecodeError:
                        parsed = None
                if parsed is not None:
                    summary.consume(provider, parsed)
                    if _is_meaningful_progress(provider, parsed):
                        progress_event.set()
                    if _is_provider_maintenance(provider, parsed):
                        maintenance_event.set()
                record = {
                    "received_at": utc_now(),
                    "stream": stream_name,
                    "event": parsed,
                    "text": None if parsed is not None else line[-8000:],
                }
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                self.ledger.write_heartbeat(
                    session_id,
                    sequence=sequence,
                    process_id=process_id,
                )


def _resolve_executable(executable: str) -> str:
    candidate = Path(executable)
    if candidate.is_absolute() or candidate.parent != Path("."):
        return executable
    return shutil.which(executable) or executable


def _prepare_image_inputs(
    image_paths: list[Path] | tuple[Path, ...],
) -> list[ExternalAgentImageInputV1]:
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    prepared: list[ExternalAgentImageInputV1] = []
    for raw_path in image_paths:
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"external Agent input image does not exist: {path}")
        media_type = media_types.get(path.suffix.lower())
        if media_type is None:
            raise ValueError(f"unsupported external Agent image type: {path.suffix}")
        payload = path.read_bytes()
        prepared.append(
            ExternalAgentImageInputV1(
                path=str(path),
                sha256=_sha256_bytes(payload),
                media_type=media_type,
                size_bytes=len(payload),
            )
        )
    return prepared


def _provider_input_bytes(
    provider: Literal["codex-cli", "claude-code-cli"],
    *,
    prompt: str,
    images: list[ExternalAgentImageInputV1],
) -> bytes:
    if provider != "claude-code-cli" or not images:
        return prompt.encode("utf-8")
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for image in images:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image.media_type,
                    "data": base64.b64encode(Path(image.path).read_bytes()).decode("ascii"),
                },
            }
        )
    return (
        json.dumps(
            {
                "type": "user",
                "message": {"role": "user", "content": content},
                "parent_tool_use_id": None,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill.exe",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            **headless_process_kwargs(),
        )
        try:
            await asyncio.wait_for(killer.wait(), timeout=3)
        except asyncio.TimeoutError:
            killer.kill()
            await killer.wait()
    else:
        process.terminate()
    if process.returncode is None:
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()


def _process_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    environment = os.environ.copy()
    environment.setdefault("PYTHONUTF8", "1")
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    environment.setdefault("NO_COLOR", "1")
    environment.setdefault("FORCE_COLOR", "0")
    environment.update(extra or {})
    return environment


def _ensure_codex_game_player_profile() -> Path:
    """Generate a narrow Codex profile without touching the user's default profile."""

    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").resolve()
    if not codex_home.is_dir():
        raise FileNotFoundError(f"CODEX_HOME does not exist: {codex_home}")
    global_skill_roots = [codex_home / "skills", Path.home() / ".agents" / "skills"]
    skill_paths = sorted(
        {
            path.resolve()
            for root in global_skill_roots
            if root.is_dir()
            for path in root.rglob("SKILL.md")
        },
        key=lambda path: str(path).lower(),
    )
    lines = [
        "# Generated by omni game player. User default config remains unchanged.",
        "[features]",
        "plugins = false",
        "apps = false",
        "browser_use = false",
        "computer_use = false",
        "image_generation = false",
        "multi_agent = false",
        "memories = false",
        "",
    ]
    for skill_path in skill_paths:
        lines.extend(
            [
                "[[skills.config]]",
                f"path = {json.dumps(str(skill_path), ensure_ascii=False)}",
                "enabled = false",
                "",
            ]
        )
    content = ("\n".join(lines).rstrip() + "\n").encode("utf-8")
    profile_path = codex_home / f"{_CODEX_GAME_PLAYER_PROFILE}.config.toml"
    if not profile_path.is_file() or profile_path.read_bytes() != content:
        temporary = profile_path.with_suffix(f".tmp-{uuid.uuid4().hex}")
        temporary.write_bytes(content)
        temporary.replace(profile_path)
    return profile_path


def _elapsed_seconds(started_at: str, completed_at: str) -> float:
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        completed = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return max(0.0, (completed - started).total_seconds())


def _resolve_codex_session_metadata(
    external_session_id: str,
) -> tuple[str | None, Literal["medium", "high"] | None]:
    """Read the native Codex ledger because `codex exec --json` omits settings."""

    sessions_root = Path.home() / ".codex" / "sessions"
    if not sessions_root.is_dir():
        return None, None
    matches = sorted(
        sessions_root.glob(f"*/*/*/*{external_session_id}.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not matches:
        return None, None
    model: str | None = None
    effort: Literal["medium", "high"] | None = None
    try:
        with matches[0].open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = event.get("payload") if isinstance(event, dict) else None
                if not isinstance(payload, dict):
                    continue
                if payload.get("type") != "thread_settings_applied":
                    continue
                settings = payload.get("thread_settings")
                if not isinstance(settings, dict):
                    continue
                candidate_model = settings.get("model")
                candidate_effort = settings.get("reasoning_effort")
                if isinstance(candidate_model, str) and candidate_model:
                    model = candidate_model
                if candidate_effort in {"medium", "high"}:
                    effort = candidate_effort
    except OSError:
        return None, None
    return model, effort


__all__ = [
    "ContinuousExternalAgentRunner",
    "ExternalAgentInvocationV1",
    "ExternalAgentImageInputV1",
    "ExternalAgentSessionLedger",
    "ExternalAgentTokenUsageV1",
]
