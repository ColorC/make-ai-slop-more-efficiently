"""ccdaemon adapter for the shared Codex capacity retry state machine."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping

from omnicompany.packages.services._core.codex_capacity_retry import (
    CapacityRetryService,
    RetryStore,
    SessionSnapshot,
    default_store_path,
    is_capacity_error_event,
    pty_capacity_error_in_chunk,
    _STORE_EXECUTOR,
)
from .codex_capacity_goal import read_native_goal
from .codex_runtime_identity import CODEX_IDENTITY_WATCH_EXECUTOR, resolve_codex_runtime_identity


_CAPACITY_LOG_SUFFIX = "Turn error: Selected model is at capacity. Please try a different model."
_CAPACITY_SNAPSHOT_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="codex-capacity-snapshot",
)


async def _retry_store_call(store: RetryStore, method: str, *args: Any, **kwargs: Any) -> Any:
    """Keep cross-process retry-store locks out of ccdaemon's default pool."""
    loop = asyncio.get_running_loop()
    fn = getattr(store, method)
    return await loop.run_in_executor(
        _STORE_EXECUTOR,
        lambda: fn(*args, **kwargs),
    )


def _generation(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(part or "") for part in parts).encode()).hexdigest()[:32]


def _goal_resumable(goal: Mapping[str, Any] | None) -> bool:
    if not isinstance(goal, Mapping) or not str(goal.get("objective") or "").strip():
        return False
    return str(goal.get("status") or "active").lower() == "active"


def _declared_block_reason(goal: Any) -> str:
    """Only a reason the runtime itself declared; never inferred from symptoms."""
    if not isinstance(goal, Mapping):
        return ""
    return str(goal.get("block_reason") or goal.get("blocked_reason") or "")


def _native_goal(provider: str, provider_session_id: str, home: Path) -> dict[str, Any]:
    if not provider_session_id:
        return {"known": False, "status": "unknown", "resume_command": None}
    try:
        return read_native_goal(provider, provider_session_id, provider_home=home)
    except Exception:
        # Goal metadata is advisory input protection.  A read failure must
        # never downgrade a native goal to an ordinary continuation.
        return {"known": False, "status": "unknown", "resume_command": None}


class CcDaemonRetryAdapter:
    def __init__(self) -> None:
        self._native_failures: dict[tuple[str, str], dict[str, Any]] = {}
        self._store: RetryStore | None = None

    async def _recovery_watch(self) -> dict[str, float]:
        """Sessions whose record is recovered and could still migrate.

        Reading the rollout to prove "no provider progress" is only worth its
        cost for these; every other session skips the scan entirely.
        """
        if self._store is None:
            self._store = RetryStore(default_store_path())
        try:
            records = await _retry_store_call(self._store, "list")
        except (OSError, TypeError, TimeoutError):
            return {}
        return {
            record.session_id: float(record.recovered_at or 0)
            for record in records
            if record.state == "succeeded"
            and float(record.recovered_at or 0) > 0
            and record.migration_state == "none"
        }

    def _provider_progress(self, home: Path | None, native_session_id: str, since: float) -> tuple[float, bool]:
        from omnicompany.packages.services._core.codex_capacity_evidence import read_last_provider_progress

        if home is None or not native_session_id:
            return 0.0, False
        try:
            return read_last_provider_progress(home, native_session_id, since=since)
        except Exception:  # noqa: BLE001 - unknown silence is never evidence
            return 0.0, False

    def _native_capacity_failure(self, home: Path, native_session_id: str, *, started_at: float, goal_updated_at: float, process_uuid: str) -> dict[str, Any] | None:
        from omnicompany.packages.services._core.codex_capacity_evidence import read_capacity_failure

        return read_capacity_failure(home, native_session_id,
                                     generation_started_at=started_at, goal_updated_at=goal_updated_at, process_uuid=process_uuid)

    def _snapshot_evidence(self, sess: Any, identity: Mapping[str, Any], recovery_since: float | None):
        """Read SQLite, host state and rollout evidence only on the snapshot worker."""
        from .pty import _read_pty_host_state, _pty_host_state_path

        provider_sid = str(identity.get("native_session_id") or "") if identity.get("known") else ""
        home = Path(identity["provider_home"]) if identity.get("known") else None
        goal = _native_goal(sess.provider, provider_sid, home) if home else {"known": False}
        goal_updated_at = float(goal.get("updated_at_ms") or 0) / 1000
        started_at = max(float(sess.started_at or 0), float(identity.get("process_created_at") or 0))
        failure = self._native_capacity_failure(
            home, provider_sid, started_at=started_at, goal_updated_at=goal_updated_at,
            process_uuid=identity.get("process_uuid", ""),
        ) if home else None
        host_state = _read_pty_host_state(sess.id)
        try:
            live_state = json.loads(_pty_host_state_path(sess.id).read_text(encoding="utf-8"))
            if live_state.get("host_pid") == sess.host_pid and float(live_state.get("updated_at") or 0) > float(host_state.get("updated_at") or 0):
                host_state = live_state
        except (OSError, ValueError):
            pass
        input_at = max(float(sess.last_submit_at or 0), float(host_state.get("last_input_at") or 0))
        progress_at, progress_known = (0.0, False)
        if recovery_since is not None:
            progress_at, progress_known = self._provider_progress(home, provider_sid, recovery_since)
        return provider_sid, goal, started_at, failure, input_at, progress_at, progress_known

    async def snapshots(self) -> list[SessionSnapshot]:
        from .litework_bridge import get_litework_bridge
        from .pty import get_manager
        from .chat import get_chat_manager

        manager = get_manager()
        self._native_failures.clear()
        recovery_watch = await self._recovery_watch()
        # Discovery and metadata projection touch process state and several
        # JSON registries.  Keep the whole synchronous catalogue pass off the
        # ccdaemon event loop; identity checks below already use their own
        # dedicated executor.
        manager_meta = await asyncio.get_running_loop().run_in_executor(
            _CAPACITY_SNAPSHOT_EXECUTOR,
            manager.list_meta,
        )
        rows: list[SessionSnapshot] = []
        for meta in manager_meta:
            if not str(meta.get("provider") or "").lower().startswith("codex"):
                continue
            sess = manager.get(str(meta.get("id") or ""))
            if sess is None:
                continue
            identity = await asyncio.get_running_loop().run_in_executor(
                CODEX_IDENTITY_WATCH_EXECUTOR, resolve_codex_runtime_identity, sess.child_pid,
            )
            (provider_sid, goal, started_at, failure, input_at,
             progress_at, progress_known) = await asyncio.get_running_loop().run_in_executor(
                _CAPACITY_SNAPSHOT_EXECUTOR,
                self._snapshot_evidence, sess, identity, recovery_watch.get(sess.id),
            )
            goal_status = str(goal.get("status") or "unknown").lower()
            if failure is not None:
                self._native_failures[(str(sess.provider), provider_sid)] = failure
            capacity_blocked = goal_status == "blocked" and bool((failure or {}).get("capacity_blocked"))
            token = hashlib.sha256(str(sess.host_token or "").encode()).hexdigest()[:16]
            rows.append(SessionSnapshot(
                session_id=sess.id,
                provider=sess.provider,
                provider_session_id=provider_sid,
                generation=_generation(sess.id, sess.started_at, sess.host_pid, sess.host_port, token, sess.child_pid),
                runtime_host="omni-pty",
                alive=bool(meta.get("alive")),
                generation_started_at=started_at,
                identity_known=bool(identity.get("known") and (sess.host_port or sess.pty is not None)),
                permission_known=True,
                working=bool(meta.get("working")),
                working_known=bool(meta.get("working_known")),
                has_user_turn=bool(sess.has_user_turn),
                input_at=input_at,
                pending_input=bool(sess.pending_input_len),
                capacity_failure=failure or {},
                goal_state={"status": goal_status, "capacity_blocked": capacity_blocked},
                goal_resumable=(goal_status == "active" and goal.get("resume_command") == "/goal resume") or capacity_blocked,
                goal_known=bool(goal.get("known")),
                last_provider_progress_at=progress_at,
                progress_known=progress_known,
                # A native Codex goal carries no machine-readable block reason;
                # an unnamed block is never treated as a real business block.
                block_reason="",
            ))

        chat = get_chat_manager()
        for sess in chat._sessions.values():
            if sess.ended_at is not None or not str(sess.provider or "").lower().startswith("codex"):
                continue
            provider_sid = str(sess.claude_session_id or "")
            rows.append(SessionSnapshot(
                session_id=sess.id,
                provider=sess.provider,
                provider_session_id=provider_sid,
                generation=_generation(sess.id, provider_sid),
                runtime_host="omni-chat",
                alive=bool(sess.provider_impl),
                generation_started_at=0.0,
                identity_known=bool(provider_sid),
                permission_known=True,
                working=bool(sess.in_flight_turn),
                working_known=bool(sess.provider_impl),
                goal_state=dict(sess.goal_state or {}),
                goal_resumable=_goal_resumable(sess.goal_state),
                human_paused=str((sess.goal_state or {}).get("status") or "").lower()
                in {"paused", "blocked", "usage_limited", "budget_limited", "cancelled", "complete", "completed"},
                goal_known=True,
                block_reason=_declared_block_reason(sess.goal_state),
            ))

        try:
            await get_litework_bridge().refresh(force=True)
            for meta in get_litework_bridge().list_meta():
                if not str(meta.get("provider") or "").lower().startswith("codex"):
                    continue
                lite = meta.get("litework") if isinstance(meta.get("litework"), Mapping) else {}
                incarnation = str(lite.get("incarnation_id") or "")
                handle = str(meta.get("provider_session_id") or "")
                rows.append(SessionSnapshot(
                    session_id=str(meta.get("id") or ""),
                    provider=str(meta.get("provider") or ""),
                    provider_session_id=handle,
                    generation=_generation(handle, incarnation),
                    runtime_host="litework",
                    alive=bool(meta.get("alive")),
                    generation_started_at=0.0,
                    identity_known=bool(handle and incarnation),
                    permission_known=bool(lite.get("writable")),
                    working=bool(meta.get("working")),
                    working_known=bool(meta.get("working_known")),
                    has_user_turn=bool(meta.get("has_user_turn")),
                    goal_state=dict(meta.get("goal_state") or meta.get("goal") or {})
                    if isinstance(meta.get("goal_state") or meta.get("goal"), Mapping) else {},
                    goal_resumable=_goal_resumable(meta.get("goal_state") or meta.get("goal")),
                    goal_known=isinstance(meta.get("goal_state") or meta.get("goal"), Mapping),
                    block_reason=_declared_block_reason(meta.get("goal_state") or meta.get("goal")),
                ))
        except Exception:
            # LiTeWork is optional.  Unknown/unavailable external state is not
            # permission to guess or send to a stale handle.
            pass
        return rows

    async def seed_backlog(self, service: CapacityRetryService) -> None:
        """Import only current live PTY failures from the account's exact log DB."""
        rows = await self.snapshots()
        for snapshot in rows:
            if snapshot.runtime_host != "omni-pty" or not snapshot.alive:
                continue
            failure = self._native_failures.get((snapshot.provider, snapshot.provider_session_id))
            if (
                failure is None
                or failure["failed_at"] < snapshot.generation_started_at
                or snapshot.input_at > failure["failed_at"]
                or snapshot.pending_input
            ):
                continue
            await service.observe({
                "kind": "error",
                "provider_owned": True,
                "provider": snapshot.provider,
                "session_id": snapshot.session_id,
                "provider_session_id": snapshot.provider_session_id,
                "generation": snapshot.generation,
                "runtime_host": snapshot.runtime_host,
                "event_id": f"logs_2:{failure['log_id']}",
                "observed_at": failure["failed_at"],
                "first_seen_at": failure["failed_at"],
                "next_due_at": failure["failed_at"] + 900.0,
                "recheck_goal": snapshot.goal_resumable,
                "error": "Selected model is at capacity. Please try a different model.",
            }, now=time.time())

    async def reconcile_attempts(self, service: CapacityRetryService) -> None:
        """Finish an already written prompt without appending duplicate text."""
        current = {row.session_id: row for row in await self.snapshots()}
        records = await _retry_store_call(service.store, "list")
        for record in records:
            if record.runtime_host != "omni-pty" or record.state != "attempting" or record.submission is not None:
                continue
            row = current.get(record.session_id)
            if (row is None or not row.alive or not row.identity_known or not row.working_known
                    or row.generation != record.generation or row.provider_session_id != record.provider_session_id):
                continue
            if row.working:
                # Do not poke a turn that has moved on since the native failure.
                record.submission = "later-activity"
                record.state = "succeeded"
                record.reason = "provider-progress-after-attempt"
                record.recovered_at = time.time()
                await _retry_store_call(service.store, "upsert", record)
                continue
            if not row.capacity_failure:
                continue
            if (row.pending_input or row.input_at > float(record.last_attempt_at or 0) + 10
                    or float(row.capacity_failure["failed_at"]) > float(record.last_attempt_at or 0)):
                await _retry_store_call(
                    service.store,
                    "record_submission",
                    record.key,
                    record.last_attempt_at,
                    "superseded",
                )
                continue
            from .pty import get_manager
            from .pty_routes import _confirm_submitted_prompt
            sess = get_manager().get(row.session_id)
            if sess is None:
                continue
            # Persist before any Enter: a crash must not create repeated confirms.
            await _retry_store_call(
                service.store,
                "record_submission",
                record.key,
                record.last_attempt_at,
                "confirmation-requested",
            )
            result = await _confirm_submitted_prompt(row.session_id, sess)
            await _retry_store_call(
                service.store,
                "record_submission",
                record.key,
                record.last_attempt_at,
                result,
                "submission-pending" if result in {"unconfirmed", "queued_behind_turn"} else None,
            )

    async def migrate(self, record: Any, snapshot: SessionSnapshot) -> dict[str, Any]:
        """One-shot inheritance: sealed handoff, claim handover, new session.

        Only the omni PTY runtime is supported: it is the only host where the
        native identity, goal, and failure provenance are all exactly verified.
        """
        from .codex_capacity_migration_runtime import perform_migration

        if snapshot is None or snapshot.runtime_host != "omni-pty":
            return {"ok": False, "reason": f"unsupported-runtime-host:{getattr(snapshot, 'runtime_host', '?')}"}

        async def resnapshot() -> SessionSnapshot | None:
            return {item.session_id: item for item in await self.snapshots()}.get(record.session_id)

        return await perform_migration(record, snapshot, resnapshot=resnapshot)

    async def send(self, snapshot: SessionSnapshot, prompt: str) -> bool | dict[str, Any]:
        # Revalidate the exact generation immediately before sending.
        current = {item.session_id: item for item in await self.snapshots()}.get(snapshot.session_id)
        if current is None or current != snapshot or not current.working_known or current.working or current.pending_input:
            return False
        if current.runtime_host == "omni-chat":
            from .chat import get_chat_manager

            sess = get_chat_manager().get(current.session_id)
            if sess is None or sess.ended_at is not None:
                return False
            await get_chat_manager().submit_user_prompt(sess, prompt, record_history=False)
            return True
        if current.runtime_host == "litework":
            from .litework_bridge import get_litework_bridge

            await get_litework_bridge().send(current.session_id, prompt, submit=True)
            return True
        from .pty import get_manager

        from .pty_routes import _submit_prompt, _confirm_submitted_prompt, _turn_working

        sess = get_manager().get(current.session_id)
        if sess is None:
            return False
        identity = await asyncio.to_thread(resolve_codex_runtime_identity, sess.child_pid, refresh=True)
        if (not identity.get("known") or identity.get("native_session_id") != current.provider_session_id
                or float(identity.get("process_created_at") or 0) > current.generation_started_at):
            return False
        await _submit_prompt(current.session_id, prompt)
        if _turn_working(sess):
            return {"accepted": True, "submission": "submitted"}
        submission = await _confirm_submitted_prompt(current.session_id, sess)
        return {"accepted": submission in {"submitted", "recovered", "recovered_overlay"},
                "pending": submission in {"unconfirmed", "queued_behind_turn"}, "submission": submission}


_service: CapacityRetryService | None = None


def get_service() -> CapacityRetryService:
    global _service
    if _service is None:
        _service = CapacityRetryService(RetryStore(default_store_path()), CcDaemonRetryAdapter())
    return _service


async def observe_provider_event(event: Mapping[str, Any]) -> bool:
    return await get_service().observe(event)


async def observe_pty_chunk(
    sess: Any,
    chunk: str,
    *,
    was_working: bool,
    observed_at: float | None = None,
    sequence: int | None = None,
) -> bool:
    """PTY text cannot establish provider error provenance; native logs own this signal."""
    return False


async def observe_pty_progress(sess: Any, *, observed_at: float) -> bool:
    """Close an attempt only on a fresh provider busy transition."""
    if not str(sess.provider or "").lower().startswith("codex"):
        return False
    token = hashlib.sha256(str(sess.host_token or "").encode()).hexdigest()[:16]
    provider_sid = str(sess.provider_session_id or sess.claude_session_id or "")
    return await observe_provider_event({
        "kind": "progress",
        "provider_owned": True,
        "provider": sess.provider,
        "session_id": sess.id,
        "provider_session_id": provider_sid,
        "generation": _generation(sess.id, sess.started_at, sess.host_pid, sess.host_port, token, sess.child_pid),
        "runtime_host": "omni-pty",
        "observed_at": observed_at,
    })


async def retry_tick() -> dict[str, Any]:
    return await get_service().tick()


def retry_status() -> list[dict[str, Any]]:
    from dataclasses import asdict

    return [asdict(record) for record in get_service().store.list()]


async def retry_status_async() -> list[dict[str, Any]]:
    from dataclasses import asdict

    records = await _retry_store_call(get_service().store, "list")
    return [asdict(record) for record in records]


async def disable(session_id: str | None = None) -> int:
    service = get_service()
    if session_id:
        return await service.cancel_for_session(session_id, "disabled")
    total = 0
    for record in await _retry_store_call(service.store, "list"):
        total += await service.cancel_for_session(record.session_id, "disabled")
    return total


__all__ = [
    "disable",
    "get_service",
    "observe_provider_event",
    "retry_status",
    "retry_status_async",
    "retry_tick",
]
