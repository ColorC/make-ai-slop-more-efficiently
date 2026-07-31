"""Detached supervision for long-running AI-player explore drives.

The dispatch layer deliberately does not know anything about game semantics.  A
worker invokes the existing Click command in-process, preserves its complete
stdout/stderr, and exposes a small filesystem lease that a low-frequency watcher
can inspect without touching the model or device.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .runtime_snapshot import (
    RuntimeSnapshot,
    RuntimeSnapshotError,
    find_runtime_repository_root,
    prepare_runtime_snapshot,
    verify_runtime_snapshot,
)
from .session_game_player_runtime import (
    SESSION_GAME_PLAYER_RUNTIME_DESCRIPTOR_ENV,
    SessionGamePlayerRuntime,
)
from .runtime_version_fence import (
    EXPECTED_CONTRACT_ENV,
    FENCE_SIGNAL_ENV,
    LIVE_REPOSITORY_ENV,
    OBSERVATORY_ROOT_ENV,
    SNAPSHOT_REPOSITORY_ENV,
    RUNTIME_FENCE_SIGNAL_SCHEMA,
    PlayerRuntimeContract,
    RuntimeContractProbeError,
    probe_runtime_contract,
    read_player_database_schema,
    read_runtime_contract,
    write_runtime_contract,
)


RUN_SCHEMA = "game-observatory.ai-player.explore-dispatch-run.v1"
EVENT_SCHEMA = "game-observatory.ai-player.explore-dispatch-event.v1"
HEARTBEAT_SCHEMA = "game-observatory.ai-player.explore-dispatch-heartbeat.v1"
TERMINAL_STATES = frozenset({"completed", "recoverable", "failed"})
ACTIVE_STATES = frozenset({"starting", "running"})
HEARTBEAT_INTERVAL_SECONDS = 2.0
HEARTBEAT_STALE_SECONDS = 30.0
STARTUP_GRACE_SECONDS = 30.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(_json_bytes(payload))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _append_event(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, raw)
    finally:
        os.close(descriptor)


class ExploreDispatchError(RuntimeError):
    """Raised when a detached drive cannot be dispatched safely."""


class ExploreDispatchLedger:
    """Filesystem ledger and single-active-run guard for detached drives."""

    def __init__(self, observatory_root: Path) -> None:
        self.root = observatory_root.resolve() / "explore_dispatch_runs"
        self.runs_root = self.root / "runs"
        self.active_root = self.root / "active"
        self.runtime_snapshots_root = self.root / "runtime_snapshots"
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.active_root.mkdir(parents=True, exist_ok=True)
        self.runtime_snapshots_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _active_key(environment_id: str, session_id: str) -> str:
        raw = f"{environment_id}\0{session_id}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def run_dir(self, run_id: str) -> Path:
        if not run_id or any(character in run_id for character in "/\\"):
            raise ExploreDispatchError(f"invalid dispatch run id: {run_id!r}")
        return self.runs_root / run_id

    def state_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "state.json"

    def spec_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "spec.json"

    def heartbeat_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "heartbeat.json"

    def events_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "events.jsonl"

    def active_path(self, environment_id: str, session_id: str) -> Path:
        return self.active_root / f"{self._active_key(environment_id, session_id)}.json"

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        return value if isinstance(value, dict) else None

    def get_state(self, run_id: str) -> dict[str, Any] | None:
        return self._read_json(self.state_path(run_id))

    def _write_state(self, state: dict[str, Any]) -> dict[str, Any]:
        _atomic_write(self.state_path(str(state["run_id"])), state)
        return state

    def append_event(
        self,
        run_id: str,
        phase: str,
        *,
        state: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        _append_event(
            self.events_path(run_id),
            {
                "schema": EVENT_SCHEMA,
                "run_id": run_id,
                "phase": phase,
                "state": state,
                "at": _utc_now(),
                "detail": detail or {},
            },
        )

    def _claim_active(
        self,
        *,
        environment_id: str,
        session_id: str,
        run_id: str,
        process_nonce: str,
    ) -> None:
        active_path = self.active_path(environment_id, session_id)
        payload = _json_bytes(
            {
                "run_id": run_id,
                "process_nonce": process_nonce,
                "environment_id": environment_id,
                "session_id": session_id,
                "claimed_at": _utc_now(),
            }
        )
        for _attempt in range(3):
            try:
                descriptor = os.open(
                    active_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                owner = self._read_json(active_path)
                owner_run_id = str((owner or {}).get("run_id") or "")
                owner_state = self.status(owner_run_id) if owner_run_id else None
                if owner_state and owner_state.get("state") in ACTIVE_STATES:
                    raise ExploreDispatchError(
                        "canonical session already has an active detached drive: "
                        f"{owner_run_id}"
                    )
                claimed_at = _parse_timestamp(str((owner or {}).get("claimed_at") or ""))
                if (
                    owner_state is None
                    and claimed_at is not None
                    and time.time() - claimed_at <= STARTUP_GRACE_SECONDS
                ):
                    raise ExploreDispatchError(
                        "canonical session has a detached drive being initialized: "
                        f"{owner_run_id}"
                    )
                current = self._read_json(active_path)
                if current == owner:
                    active_path.unlink(missing_ok=True)
                continue
            else:
                try:
                    os.write(descriptor, payload)
                finally:
                    os.close(descriptor)
                return
        raise ExploreDispatchError("could not acquire canonical-session dispatch guard")

    def _release_active_claim(
        self,
        *,
        environment_id: str,
        session_id: str,
        run_id: str,
        process_nonce: str,
    ) -> None:
        active_path = self.active_path(environment_id, session_id)
        owner = self._read_json(active_path)
        if (
            owner
            and owner.get("run_id") == run_id
            and owner.get("process_nonce") == process_nonce
        ):
            active_path.unlink(missing_ok=True)

    def create_run(self, spec: dict[str, Any]) -> dict[str, Any]:
        run_id = str(spec["run_id"])
        process_nonce = str(spec["process_nonce"])
        environment_id = str(spec["environment_id"])
        session_id = str(spec["session_id"])
        self._claim_active(
            environment_id=environment_id,
            session_id=session_id,
            run_id=run_id,
            process_nonce=process_nonce,
        )
        run_dir = self.run_dir(run_id)
        try:
            run_dir.mkdir(parents=False, exist_ok=False)
            _atomic_write(self.spec_path(run_id), spec)
            now = _utc_now()
            state = {
                "schema": RUN_SCHEMA,
                "run_id": run_id,
                "process_nonce": process_nonce,
                "state": "starting",
                "environment_id": environment_id,
                "session_id": session_id,
                "task_id": str(spec["task_id"]),
                "pid": None,
                "created_at": now,
                "updated_at": now,
                "started_at": None,
                "finished_at": None,
                "exit_code": None,
                "failure_reason": None,
                "stop_reason": None,
                "recoverable": False,
                "recovery_detail": None,
                "runtime_snapshot_sha256": spec.get("runtime_snapshot_sha256"),
                "runtime_snapshot_repository_root": spec.get(
                    "runtime_snapshot_repository_root"
                ),
                "stdout_path": str(run_dir / "stdout.log"),
                "stderr_path": str(run_dir / "stderr.log"),
                "events_path": str(self.events_path(run_id)),
            }
            self._write_state(state)
            self.append_event(run_id, "dispatch_created", state="starting")
            return state
        except Exception:
            self._release_active_claim(
                environment_id=environment_id,
                session_id=session_id,
                run_id=run_id,
                process_nonce=process_nonce,
            )
            raise

    def note_spawned(self, run_id: str, pid: int) -> dict[str, Any]:
        state = self.get_state(run_id)
        if state is None:
            raise ExploreDispatchError(f"dispatch run is missing: {run_id}")
        if state["state"] != "starting":
            raise ExploreDispatchError(f"dispatch run is no longer starting: {run_id}")
        state = {**state, "pid": pid, "updated_at": _utc_now()}
        return self._write_state(state)

    def transition(
        self,
        run_id: str,
        new_state: str,
        *,
        expected_states: set[str],
        exit_code: int | None = None,
        failure_reason: str | None = None,
        stop_reason: str | None = None,
        recoverable: bool = False,
        recovery_detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = self.get_state(run_id)
        if state is None:
            raise ExploreDispatchError(f"dispatch run is missing: {run_id}")
        if state["state"] not in expected_states:
            return state
        now = _utc_now()
        state = {
            **state,
            "state": new_state,
            "updated_at": now,
            "started_at": state.get("started_at") or (
                now if new_state == "running" else None
            ),
            "finished_at": now if new_state in TERMINAL_STATES else None,
            "exit_code": exit_code,
            "failure_reason": failure_reason,
            "stop_reason": stop_reason,
            "recoverable": recoverable,
            "recovery_detail": recovery_detail,
        }
        self._write_state(state)
        if new_state in TERMINAL_STATES:
            self._release_active_claim(
                environment_id=str(state["environment_id"]),
                session_id=str(state["session_id"]),
                run_id=str(state["run_id"]),
                process_nonce=str(state["process_nonce"]),
            )
        return state

    def write_heartbeat(self, run_id: str, *, sequence: int) -> None:
        state = self.get_state(run_id)
        if state is None:
            return
        _atomic_write(
            self.heartbeat_path(run_id),
            {
                "schema": HEARTBEAT_SCHEMA,
                "run_id": run_id,
                "process_nonce": state["process_nonce"],
                "pid": os.getpid(),
                "sequence": sequence,
                "emitted_at": _utc_now(),
            },
        )

    def _heartbeat_health(
        self,
        state: dict[str, Any],
        *,
        now: float,
    ) -> tuple[bool, str, float | None]:
        heartbeat = self._read_json(self.heartbeat_path(str(state["run_id"])))
        if (
            heartbeat
            and heartbeat.get("run_id") == state.get("run_id")
            and heartbeat.get("process_nonce") == state.get("process_nonce")
            and heartbeat.get("pid") == state.get("pid")
        ):
            emitted_at = _parse_timestamp(str(heartbeat.get("emitted_at") or ""))
            age = None if emitted_at is None else max(0.0, now - emitted_at)
            if age is not None and age <= HEARTBEAT_STALE_SECONDS:
                return True, "heartbeat_fresh", age
            return False, "heartbeat_stale", age
        grace_origin = _parse_timestamp(
            str(state.get("started_at") or state.get("created_at") or "")
        )
        grace_age = None if grace_origin is None else max(0.0, now - grace_origin)
        if grace_age is not None and grace_age <= STARTUP_GRACE_SECONDS:
            return True, "heartbeat_startup_grace", grace_age
        return False, "heartbeat_missing", grace_age

    def status(
        self,
        run_id: str,
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        state = self.get_state(run_id)
        if state is None:
            return None
        if state.get("state") in TERMINAL_STATES:
            # Heal claims left by older workers that reached a terminal state
            # before terminal-transition cleanup existed.
            self._release_active_claim(
                environment_id=str(state["environment_id"]),
                session_id=str(state["session_id"]),
                run_id=str(state["run_id"]),
                process_nonce=str(state["process_nonce"]),
            )
            return {**state, "worker_health": "terminal", "heartbeat_age_seconds": None}
        healthy, reason, heartbeat_age = self._heartbeat_health(
            state,
            now=time.time() if now is None else now,
        )
        if not healthy:
            state = self.transition(
                run_id,
                "failed",
                expected_states=set(ACTIVE_STATES),
                exit_code=None,
                failure_reason="worker_unexpected_exit",
            )
            self.append_event(
                run_id,
                "worker_lost",
                state="failed",
                detail={"health_reason": reason, "heartbeat_age_seconds": heartbeat_age},
            )
            return {
                **state,
                "worker_health": reason,
                "heartbeat_age_seconds": heartbeat_age,
            }
        return {
            **state,
            "worker_health": reason,
            "heartbeat_age_seconds": heartbeat_age,
        }

    def latest_for_session(
        self,
        *,
        environment_id: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        active = self._read_json(self.active_path(environment_id, session_id))
        if active and active.get("run_id"):
            active_state = self.status(str(active["run_id"]))
            if active_state is not None:
                return active_state
        matching: list[dict[str, Any]] = []
        for path in self.runs_root.glob("*/state.json"):
            state = self._read_json(path)
            if (
                state
                and state.get("environment_id") == environment_id
                and state.get("session_id") == session_id
            ):
                matching.append(state)
        if not matching:
            return None
        latest = max(matching, key=lambda item: str(item.get("created_at") or ""))
        return self.status(str(latest["run_id"]))


def _worker_command(spec: dict[str, Any]) -> list[str]:
    command = [
        "game",
        "player",
        "--root",
        str(spec["observatory_root"]),
        "--json",
        "explore",
        "drive",
        str(spec["session_id"]),
        "--environment",
        str(spec["environment_id"]),
        "--task",
        str(spec["task_id"]),
        "--max-turns",
        str(spec["max_turns"]),
        "--overall-timeout",
        str(spec["overall_timeout"]),
        "--turn-timeout",
        str(spec["turn_timeout"]),
        "--cwd",
        str(spec.get("provider_cwd") or spec["cwd"]),
    ]
    instruction = spec.get("instruction")
    if instruction:
        command.extend(["--instruction", str(instruction)])
    return command


def _heartbeat_loop(
    ledger: ExploreDispatchLedger,
    run_id: str,
    stop: threading.Event,
) -> None:
    sequence = 0
    while not stop.is_set():
        sequence += 1
        ledger.write_heartbeat(run_id, sequence=sequence)
        stop.wait(HEARTBEAT_INTERVAL_SECONDS)


def _runtime_fence_detail(
    spec: dict[str, Any],
) -> dict[str, Any] | None:
    expected_path = Path(str(spec["expected_runtime_contract_path"]))
    snapshot_root = Path(str(spec["runtime_snapshot_repository_root"]))
    live_root = Path(str(spec["live_repository_root"]))
    try:
        verify_runtime_snapshot(snapshot_root)
        expected = read_runtime_contract(expected_path)
        frozen = probe_runtime_contract(snapshot_root)
        live = probe_runtime_contract(live_root)
        database = read_player_database_schema(Path(str(spec["observatory_root"])))
    except RuntimeSnapshotError as exc:
        return {
            "change_kind": "frozen_runtime_integrity_change",
            "error": str(exc),
        }
    except RuntimeContractProbeError as exc:
        return {
            "change_kind": "runtime_contract_probe_failed",
            "error": str(exc),
        }
    if not expected.semantically_matches(frozen):
        return {
            "change_kind": "frozen_runtime_contract_mismatch",
            "expected": expected.to_dict(),
            "frozen": frozen.to_dict(),
        }
    if database.ai_player_schema_version > expected.ai_player_schema_version:
        return {
            "change_kind": "database_schema_newer_than_frozen_runtime",
            "expected": expected.to_dict(),
            "database": {
                "ai_player_schema_version": database.ai_player_schema_version,
                "sqlite_user_version": database.sqlite_user_version,
            },
        }
    if not expected.semantically_matches(live):
        return {
            "change_kind": "facility_contract_or_schema_changed",
            "expected": expected.to_dict(),
            "current": live.to_dict(),
        }
    return None


def _persist_runtime_fence_signal(spec: dict[str, Any], detail: dict[str, Any]) -> None:
    _atomic_write(
        Path(str(spec["runtime_fence_signal_path"])),
        {
            "schema": RUNTIME_FENCE_SIGNAL_SCHEMA,
            "stop_reason": "facility_contract_change",
            "recoverable": True,
            "detected_at": _utc_now(),
            **detail,
        },
    )


def _settle_canonical_session_after_worker(
    spec: dict[str, Any],
) -> dict[str, Any] | None:
    """Leave no running canonical session after its detached worker exits."""

    from ..store import ObservatoryStore
    from .session_control import (
        AIPlayerSessionCommand,
        AIPlayerSessionControl,
        AIPlayerSessionError,
        AIPlayerSessionReconcileCommand,
    )
    from .store import AIPlayerStore

    environment_id = str(spec["environment_id"])
    session_id = str(spec["session_id"])
    run_id = str(spec["run_id"])
    control = AIPlayerSessionControl(
        AIPlayerStore(ObservatoryStore(Path(str(spec["observatory_root"]))))
    )
    current = control.get_session(environment_id, session_id)
    if current is None:
        return None
    if current.state != "running":
        return {
            "outcome": "already_settled",
            "session_state": current.state,
            "session_version": current.version,
        }
    try:
        control.assert_session_lease_active(environment_id, session_id)
    except AIPlayerSessionError:
        control.reconcile_stale_sessions(
            AIPlayerSessionReconcileCommand(
                command_id=f"{session_id}.dispatch-terminal-reconcile.{run_id}",
                environment_id=environment_id,
                actor="explore-dispatch-worker",
                reason="Reconcile a stale canonical lease after detached drive termination.",
            )
        )
        current = control.get_session(environment_id, session_id)
        if current is None or current.state != "running":
            return {
                "outcome": "stale_lease_reconciled",
                "session_state": getattr(current, "state", "missing"),
                "session_version": getattr(current, "version", None),
            }
    try:
        paused = control.pause(
            session_id,
            AIPlayerSessionCommand(
                command_id=f"{session_id}.dispatch-terminal-pause.{run_id}.{current.version}",
                environment_id=environment_id,
                expected_version=current.version,
                actor="explore-dispatch-worker",
                reason=(
                    "Detached drive terminated; preserve an immediately resumable "
                    "checkpoint without a zombie worker lease."
                ),
            ),
        )
    except AIPlayerSessionError as exc:
        return {
            "outcome": "error",
            "session_state": current.state,
            "session_version": current.version,
            "error_code": exc.code,
            "error": exc.message,
        }
    return {
        "outcome": "paused",
        "session_state": paused.state,
        "session_version": paused.version,
    }


def run_worker(
    run_dir: Path,
    *,
    invoke: Callable[..., Any] | None = None,
    runtime_fence: Callable[[dict[str, Any]], dict[str, Any] | None] = (
        _runtime_fence_detail
    ),
) -> int:
    """Run one drive inside the detached worker process."""

    run_dir = run_dir.resolve()
    spec_raw = json.loads((run_dir / "spec.json").read_text(encoding="utf-8"))
    if not isinstance(spec_raw, dict):
        raise ExploreDispatchError("dispatch spec must be a JSON object")
    spec = spec_raw
    ledger = ExploreDispatchLedger(Path(str(spec["observatory_root"])))
    run_id = str(spec["run_id"])
    launch_gate = run_dir / "launch.ready"
    deadline = time.monotonic() + STARTUP_GRACE_SECONDS
    while not launch_gate.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    if not launch_gate.exists():
        current = ledger.get_state(run_id)
        if current is not None and current.get("state") == "starting":
            ledger.transition(
                run_id,
                "failed",
                expected_states={"starting"},
                exit_code=2,
                failure_reason="worker_launch_gate_timeout",
            )
            ledger.append_event(
                run_id,
                "worker_launch_gate_timeout",
                state="failed",
            )
        return 2
    current = ledger.get_state(run_id)
    if current is None or current.get("state") != "starting":
        return 2
    fence_detail = runtime_fence(spec)
    if fence_detail is not None:
        _persist_runtime_fence_signal(spec, fence_detail)
        ledger.transition(
            run_id,
            "recoverable",
            expected_states={"starting"},
            exit_code=0,
            stop_reason="facility_contract_change",
            recoverable=True,
            recovery_detail=fence_detail,
        )
        ledger.append_event(
            run_id,
            "facility_contract_change",
            state="recoverable",
            detail=fence_detail,
        )
        return 0
    running = ledger.transition(
        run_id,
        "running",
        expected_states={"starting"},
    )
    if running.get("state") != "running":
        return 2
    ledger.append_event(
        run_id,
        "worker_started",
        state="running",
        detail={"pid": os.getpid()},
    )
    stop_heartbeat = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat_loop,
        args=(ledger, run_id, stop_heartbeat),
        name=f"explore-dispatch-heartbeat-{run_id}",
        daemon=True,
    )
    heartbeat.start()
    command = _worker_command(spec)
    ledger.append_event(
        run_id,
        "drive_started",
        state="running",
        detail={"command": command},
    )
    stdout_path = Path(str(running["stdout_path"]))
    stderr_path = Path(str(running["stderr_path"]))
    exit_code = 0
    failure_reason: str | None = None
    runtime_descriptor_path = run_dir / "session-game-player-runtime.json"
    session_runtime = SessionGamePlayerRuntime(
        descriptor_path=runtime_descriptor_path,
        observatory_root=Path(str(spec["observatory_root"])),
        environment_id=str(spec["environment_id"]),
        session_id=str(spec["session_id"]),
        run_id=run_id,
        process_nonce=str(spec["process_nonce"]),
        runtime_snapshot_sha256=str(spec["runtime_snapshot_sha256"]),
        runtime_fence_signal_path=Path(str(spec["runtime_fence_signal_path"])),
    )
    previous_runtime_descriptor = os.environ.get(
        SESSION_GAME_PLAYER_RUNTIME_DESCRIPTOR_ENV
    )
    try:
        if invoke is None:
            from omnicompany.cli.main import cli

            invoke = cli.main
        with (
            session_runtime,
            stdout_path.open("a", encoding="utf-8", newline="") as stdout_file,
            stderr_path.open("a", encoding="utf-8", newline="") as stderr_file,
            contextlib.redirect_stdout(stdout_file),
            contextlib.redirect_stderr(stderr_file),
        ):
            os.environ[SESSION_GAME_PLAYER_RUNTIME_DESCRIPTOR_ENV] = str(
                runtime_descriptor_path
            )
            invoke(
                args=command,
                prog_name="omni",
                standalone_mode=False,
            )
    except BaseException as exc:  # noqa: BLE001 - worker must persist every failure
        exit_code = int(getattr(exc, "exit_code", 1) or 1)
        failure_reason = f"{type(exc).__name__}: {exc}"
        with stderr_path.open("a", encoding="utf-8", newline="") as stderr_file:
            traceback.print_exc(file=stderr_file)
    finally:
        if previous_runtime_descriptor is None:
            os.environ.pop(SESSION_GAME_PLAYER_RUNTIME_DESCRIPTOR_ENV, None)
        else:
            os.environ[
                SESSION_GAME_PLAYER_RUNTIME_DESCRIPTOR_ENV
            ] = previous_runtime_descriptor
        stop_heartbeat.set()
        heartbeat.join(timeout=HEARTBEAT_INTERVAL_SECONDS + 1)
    try:
        settlement = _settle_canonical_session_after_worker(spec)
    except Exception as exc:  # noqa: BLE001 - terminal state must remain observable
        settlement = {
            "outcome": "error",
            "error_code": type(exc).__name__,
            "error": str(exc),
        }
    if settlement is not None:
        ledger.append_event(
            run_id,
            "canonical_session_settled",
            state="running",
            detail=settlement,
        )
        if settlement.get("outcome") == "error":
            exit_code = exit_code or 3
            failure_reason = (
                "canonical_session_settlement_failed: "
                f"{settlement.get('error_code')}: {settlement.get('error')}"
            )
    fence_signal = ledger._read_json(Path(str(spec["runtime_fence_signal_path"])))
    if (
        exit_code == 0
        and fence_signal is not None
        and fence_signal.get("schema") == RUNTIME_FENCE_SIGNAL_SCHEMA
        and fence_signal.get("stop_reason") == "facility_contract_change"
    ):
        recovery_detail = {
            key: value
            for key, value in fence_signal.items()
            if key not in {"schema", "stop_reason", "recoverable", "detected_at"}
        }
        final = ledger.transition(
            run_id,
            "recoverable",
            expected_states={"running"},
            exit_code=0,
            stop_reason="facility_contract_change",
            recoverable=True,
            recovery_detail=recovery_detail,
        )
        if final.get("state") == "recoverable":
            ledger.append_event(
                run_id,
                "facility_contract_change",
                state="recoverable",
                detail=recovery_detail,
            )
    elif exit_code == 0:
        final = ledger.transition(
            run_id,
            "completed",
            expected_states={"running"},
            exit_code=0,
        )
        if final.get("state") == "completed":
            ledger.append_event(run_id, "drive_completed", state="completed")
        else:
            ledger.append_event(
                run_id,
                "worker_finished_after_terminal_state",
                state=str(final.get("state")),
                detail={"drive_exit_code": 0},
            )
    else:
        ledger.transition(
            run_id,
            "failed",
            expected_states={"running"},
            exit_code=exit_code,
            failure_reason=failure_reason or "drive_failed",
        )
        ledger.append_event(
            run_id,
            "drive_failed",
            state="failed",
            detail={"exit_code": exit_code, "failure_reason": failure_reason},
        )
    return exit_code


def launch_detached_worker(
    run_dir: Path,
    *,
    popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
) -> int:
    """Launch a hidden, independent worker and return its OS pid."""

    spec_raw = json.loads((run_dir / "spec.json").read_text(encoding="utf-8"))
    if not isinstance(spec_raw, dict):
        raise ExploreDispatchError("dispatch spec must be a JSON object")
    snapshot_root = Path(str(spec_raw["runtime_snapshot_repository_root"]))
    snapshot_source = snapshot_root / "src"
    if not snapshot_source.is_dir():
        raise ExploreDispatchError(f"runtime snapshot source is missing: {snapshot_source}")
    command = [sys.executable, "-m", __name__, "worker", "--run-dir", str(run_dir)]
    stdout_path = run_dir / "stdout.log"
    stderr_path = run_dir / "stderr.log"
    environment = os.environ.copy()
    # PYTHONPATH is replaced, not appended.  The worker, provider wrapper and every
    # nested `omni` console entry must resolve omnicompany from the same snapshot.
    environment["PYTHONPATH"] = str(snapshot_source)
    environment[EXPECTED_CONTRACT_ENV] = str(spec_raw["expected_runtime_contract_path"])
    environment[LIVE_REPOSITORY_ENV] = str(spec_raw["live_repository_root"])
    # Runtime code is frozen, while external assets and deployment-relative
    # facilities continue to resolve from the real repository/workspace.
    environment["OMNI_WORKSPACE_ROOT"] = str(spec_raw["live_repository_root"])
    environment[FENCE_SIGNAL_ENV] = str(spec_raw["runtime_fence_signal_path"])
    environment[OBSERVATORY_ROOT_ENV] = str(spec_raw["observatory_root"])
    # Commands issued by the provider intentionally omit the repetitive --root
    # option.  The canonical store resolver uses this deployment override, while
    # OBSERVATORY_ROOT_ENV is reserved for the runtime fence itself.
    environment["GAME_OBSERVATORY_ROOT"] = str(spec_raw["observatory_root"])
    environment[SNAPSHOT_REPOSITORY_ENV] = str(snapshot_root)
    kwargs: dict[str, Any] = {
        "cwd": str(run_dir),
        "env": environment,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        kwargs.update({"creationflags": creationflags, "startupinfo": startupinfo})
    else:
        kwargs["start_new_session"] = True
    with (
        stdout_path.open("ab", buffering=0) as stdout_file,
        stderr_path.open("ab", buffering=0) as stderr_file,
    ):
        process = popen(
            command,
            stdout=stdout_file,
            stderr=stderr_file,
            **kwargs,
        )
    return int(process.pid)


def dispatch_drive(
    observatory_root: Path,
    *,
    session_id: str,
    environment_id: str,
    task_id: str,
    instruction: str | None,
    max_turns: int,
    overall_timeout: float,
    turn_timeout: float,
    cwd: Path,
    launcher: Callable[[Path], int] = launch_detached_worker,
    repository_root: Path | None = None,
    contract_probe: Callable[[Path], PlayerRuntimeContract] = probe_runtime_contract,
    snapshot_preparer: Callable[..., RuntimeSnapshot] = prepare_runtime_snapshot,
) -> dict[str, Any]:
    ledger = ExploreDispatchLedger(observatory_root)
    run_id = f"explore-dispatch.{uuid.uuid4().hex}"
    process_nonce = uuid.uuid4().hex
    live_repository_root = (
        repository_root.resolve()
        if repository_root is not None
        else find_runtime_repository_root(Path(__file__))
    )
    try:
        expected_contract = contract_probe(live_repository_root)
        snapshot = snapshot_preparer(
            live_repository_root,
            ledger.runtime_snapshots_root,
            expected_contract=expected_contract,
        )
    except (RuntimeContractProbeError, RuntimeSnapshotError) as exc:
        raise ExploreDispatchError(f"could not freeze explore runtime: {exc}") from exc
    run_dir = ledger.run_dir(run_id)
    spec = {
        "schema": "game-observatory.ai-player.explore-dispatch-spec.v1",
        "run_id": run_id,
        "process_nonce": process_nonce,
        "observatory_root": str(observatory_root.resolve()),
        "session_id": session_id,
        "environment_id": environment_id,
        "task_id": task_id,
        "instruction": instruction,
        "max_turns": max_turns,
        "overall_timeout": overall_timeout,
        "turn_timeout": turn_timeout,
        "cwd": str(cwd.resolve()),
        # Keep the model's writable workspace on the canonical mutable ledger.
        # The worker and every nested omni command still import code only from
        # the frozen snapshot through the replaced PYTHONPATH.
        "provider_cwd": str(observatory_root),
        "live_repository_root": str(live_repository_root),
        "runtime_snapshot_repository_root": str(snapshot.repository_root),
        "runtime_snapshot_sha256": snapshot.source_manifest_sha256,
        "runtime_snapshot_reused": snapshot.reused,
        "expected_runtime_contract_path": str(run_dir / "runtime-contract.json"),
        "runtime_fence_signal_path": str(run_dir / "runtime-fence-signal.json"),
        "created_at": _utc_now(),
    }
    state = ledger.create_run(spec)
    try:
        write_runtime_contract(
            Path(str(spec["expected_runtime_contract_path"])),
            expected_contract,
        )
        pid = launcher(ledger.run_dir(run_id))
        state = ledger.note_spawned(run_id, pid)
        ledger.append_event(
            run_id,
            "worker_dispatched",
            state="starting",
            detail={"pid": pid},
        )
        (ledger.run_dir(run_id) / "launch.ready").write_bytes(b"ready\n")
        return state
    except Exception as exc:
        ledger.transition(
            run_id,
            "failed",
            expected_states={"starting"},
            failure_reason=f"worker_launch_failed: {type(exc).__name__}: {exc}",
        )
        ledger.append_event(
            run_id,
            "worker_launch_failed",
            state="failed",
            detail={"error": f"{type(exc).__name__}: {exc}"},
        )
        raise ExploreDispatchError(
            f"could not launch detached explore worker: {type(exc).__name__}: {exc}"
        ) from exc


def _main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    worker = subparsers.add_parser("worker")
    worker.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "worker":
        return run_worker(args.run_dir)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())
