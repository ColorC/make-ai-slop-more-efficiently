# [OMNI] origin=claude-code domain=services/agent ts=2026-10-26 type=infrastructure
"""Observable, recoverable lifecycle records for `omni worker run`.

Each worker run directory carries three small JSON files:

- ``worker.pid.json``: controller PID plus its process start time. PID alone is
  not an identity on Windows (PIDs are reused quickly); pid+create_time is.
- ``heartbeat.json``: rewritten every 15 s by a controller thread. Provider
  output is diagnostic; optional idle expiry uses subtree CPU/IO and controller
  heartbeats, so a healthy silent orchestrator remains alive.
- ``exit.json``: written by the controller for succeeded/failed/timed_out/
  permission_violation/error/cancelled. A killed controller cannot write it, so
  :func:`reconcile_worker_run` writes ``status=killed`` on its behalf, and only
  after the recorded process identity is gone.

On Windows the controller assigns itself to an unnamed KILL_ON_JOB_CLOSE Job
Object before any provider work starts, so every descendant is born inside the
job (no spawn-before-assignment window, including SDK-spawned processes).
The only job handle is held, non-inheritable, by the controller; when the
controller dies for any reason the kernel closes it and ends the subtree. The
caller/parent is never placed in the job.

This module only reads and writes run records. It never terminates processes.
"""

from __future__ import annotations

import atexit
import asyncio
import json
import os
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

LIFECYCLE_SCHEMA = "omni.worker.lifecycle/v1"
PID_FILENAME = "worker.pid.json"
HEARTBEAT_FILENAME = "heartbeat.json"
EXIT_FILENAME = "exit.json"
LINK_FILENAME = "link.json"
LEGACY_REQUEST_FILENAME = "request.json"
LEGACY_RESULT_FILENAME = "result.json"

HEARTBEAT_INTERVAL_S = 15.0
HEARTBEAT_STALE_AFTER_S = 90.0
DEFAULT_TIMEOUT_S = None
DEFAULT_IDLE_TIMEOUT_S = None

RUN_ROOT_ENV = "OMNI_WORKER_RUN_ROOT"
CONTAINMENT_ENV = "OMNI_WORKER_JOB_CONTAINMENT"

STATE_RUNNING = "running"
STATE_STALE = "stale"
STATE_LOST = "lost"
STATE_EXITED = "exited"
STATE_KILLED = "killed"
STATE_LEGACY_FINISHED = "legacy-finished"
STATE_LEGACY_UNKNOWN = "legacy-unknown"
ACTIVE_STATES = frozenset({STATE_RUNNING, STATE_STALE, STATE_LOST})

_CREATE_TIME_TOLERANCE_S = 1.0
_REASON_LIMIT = 2000


# ─── small IO helpers ──────────────────────────────────────────


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            # A concurrent reader on Windows can briefly block the rename.
            time.sleep(0.02 * (attempt + 1))
    try:
        path.write_text(text, encoding="utf-8")
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def default_worker_run_root() -> Path:
    """Default discovery root for worker lifecycle records."""

    raw = os.environ.get(RUN_ROOT_ENV, "").strip()
    if raw:
        root = Path(raw).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root
    from omnicompany.core.config import resolve_runtime_data_dir

    return resolve_runtime_data_dir("worker_runs")


# ─── process identity ──────────────────────────────────────────


from omnicompany.packages.services._core.guardian.sentinel import (
    process_create_time, process_identity_alive,
)


# ─── Windows Job Object containment ────────────────────────────

_JOB_LOCK = threading.Lock()
_JOB_STATE: dict[str, Any] | None = None
_JOB_HANDLE: Any = None  # held until process exit; closing it kills the subtree


def containment_requested(explicit: bool | None = None) -> bool:
    if explicit is not None:
        return explicit
    value = os.environ.get(CONTAINMENT_ENV, "").strip().lower()
    if value in {"0", "off", "false", "no"}:
        return False
    if value == "force":
        return True
    # In-process CLI tests must not put the pytest runner itself in a job.
    return "PYTEST_CURRENT_TEST" not in os.environ


def contain_current_process() -> dict[str, Any]:
    """Assign this process to a KILL_ON_JOB_CLOSE job (once per process)."""

    global _JOB_STATE, _JOB_HANDLE
    with _JOB_LOCK:
        if _JOB_STATE is not None:
            return dict(_JOB_STATE)
        if sys.platform != "win32":
            _JOB_STATE = {"kind": "job_object", "status": "unsupported", "platform": sys.platform}
            return dict(_JOB_STATE)
        try:
            _JOB_HANDLE = _create_kill_on_close_job_and_assign_self()
            _JOB_STATE = {"kind": "job_object", "status": "assigned"}
        except OSError as exc:
            _JOB_STATE = {"kind": "job_object", "status": "unavailable", "error": str(exc)}
        return dict(_JOB_STATE)


def _create_kill_on_close_job_and_assign_self() -> Any:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    job_object_extended_limit_information = 9
    job_object_limit_kill_on_job_close = 0x00002000

    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    # NULL security attributes => the handle is not inheritable, and the job
    # is unnamed, so no other process can hold it open past our death.
    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    # No BREAKAWAY_OK / SILENT_BREAKAWAY_OK: descendants cannot leave the job.
    info.BasicLimitInformation.LimitFlags = job_object_limit_kill_on_job_close
    if not kernel32.SetInformationJobObject(
        handle,
        job_object_extended_limit_information,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        err = ctypes.get_last_error()
        kernel32.CloseHandle(handle)
        raise ctypes.WinError(err)
    if not kernel32.AssignProcessToJobObject(handle, kernel32.GetCurrentProcess()):
        err = ctypes.get_last_error()
        kernel32.CloseHandle(handle)
        raise ctypes.WinError(err)
    return handle


# ─── controller-side lifecycle ─────────────────────────────────

_ACTIVE_LOCK = threading.Lock()
_ACTIVE: "WorkerLifecycle | None" = None


def record_worker_activity(source: str) -> None:
    """Mark provider output/event activity for the active worker run, if any."""

    lifecycle = _ACTIVE
    if lifecycle is not None:
        lifecycle.record_activity(source)


def active_worker_lifecycle() -> "WorkerLifecycle | None":
    return _ACTIVE


class TreeActivityClock:
    """Unknown samples and live controller heartbeats prevent idle expiry."""

    def __init__(self) -> None:
        self.last_active = time.monotonic()
        self.previous: dict[tuple[int, float], tuple[float, int, int]] | None = None

    def remaining(self, timeout_s: float) -> float:
        import psutil
        now = time.monotonic()
        try:
            root = psutil.Process()
            processes = [root, *root.children(recursive=True)]
            current = {}
            for p in processes:
                cpu = p.cpu_times()
                io = p.io_counters()
                current[(p.pid, p.create_time())] = (
                    cpu.user + cpu.system,
                    io.read_bytes + io.write_bytes + getattr(io, "other_bytes", 0),
                    io.read_count + io.write_count + getattr(io, "other_count", 0),
                )
        except (psutil.Error, OSError):
            current = None
        life = active_worker_lifecycle()
        fresh_heartbeat = bool(life and life._last_heartbeat_mono is not None
                               and now - life._last_heartbeat_mono <= HEARTBEAT_STALE_AFTER_S)
        if current is None or self.previous is None or current != self.previous or fresh_heartbeat:
            self.last_active = now
        self.previous = current
        return timeout_s - (now - self.last_active)


async def run_with_tree_idle(awaitable: Any, idle_timeout_s: float | None) -> Any:
    """Provider output silence is never an idle signal. Explicit opt-in only."""
    if idle_timeout_s is None or float(idle_timeout_s) <= 0:
        return await awaitable
    timeout = float(idle_timeout_s)
    clock = TreeActivityClock()
    task = asyncio.ensure_future(awaitable)
    try:
        while not task.done():
            remaining = clock.remaining(timeout)
            if remaining <= 0:
                raise asyncio.TimeoutError(f"worker subtree CPU/IO inactive and no fresh heartbeat for {timeout:g}s")
            done, _ = await asyncio.wait({task}, timeout=min(1.0, remaining))
            if done:
                return task.result()
        return task.result()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


class WorkerLifecycle:
    """Controller-owned pid/heartbeat/exit records for one worker run."""

    def __init__(
        self,
        run_dir: Path | str,
        *,
        run_id: str,
        provider: str,
        cwd: Path | str | None = None,
        timeout_s: float | None = None,
        idle_timeout_s: float | None = None,
        heartbeat_interval_s: float = HEARTBEAT_INTERVAL_S,
        contain: bool | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.run_id = run_id
        self.provider = provider
        self.cwd = str(cwd) if cwd is not None else None
        self.timeout_s = timeout_s
        self.idle_timeout_s = idle_timeout_s
        self.heartbeat_interval_s = float(heartbeat_interval_s)
        self._contain = contain
        self._extra = dict(extra or {})
        self.pid = os.getpid()
        self.create_time = process_create_time(self.pid)
        self.started_ts = time.time()
        self.containment: dict[str, Any] = {"kind": "job_object", "status": "disabled"}
        self._lock = threading.Lock()
        self._seq = 0
        self._last_activity_ts: float | None = None
        self._last_activity_source = ""
        self._activity_count = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._finished: dict[str, Any] | None = None
        self.cancel_reason = ""
        self._last_heartbeat_mono: float | None = None

    @property
    def pid_path(self) -> Path:
        return self.run_dir / PID_FILENAME

    @property
    def heartbeat_path(self) -> Path:
        return self.run_dir / HEARTBEAT_FILENAME

    @property
    def exit_path(self) -> Path:
        return self.run_dir / EXIT_FILENAME

    def start(self) -> "WorkerLifecycle":
        global _ACTIVE
        # Contain first: everything spawned after this line is born in the job.
        requested = containment_requested(self._contain)
        if requested:
            self.containment = contain_current_process()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": LIFECYCLE_SCHEMA,
            "run_id": self.run_id,
            "provider": self.provider,
            "pid": self.pid,
            "create_time": self.create_time,
            "parent_pid": os.getppid(),
            "host": socket.gethostname(),
            "started_ts": self.started_ts,
            "started_at": _iso(self.started_ts),
            "cwd": self.cwd,
            "run_dir": str(self.run_dir),
            "timeout_s": self.timeout_s,
            "idle_timeout_s": self.idle_timeout_s,
            "heartbeat_interval_s": self.heartbeat_interval_s,
            "heartbeat_stale_after_s": HEARTBEAT_STALE_AFTER_S,
            "containment": self.containment,
        }
        payload.update(self._extra)
        _write_json_atomic(self.pid_path, payload)
        self._write_heartbeat(phase="running")
        if requested and self.containment.get("status") == "unavailable":
            reason = "Windows Job Object unavailable; refusing to spawn an uncontained worker"
            self.finish(status="error", exit_code=1, reason=reason)
            raise RuntimeError(reason)
        with _ACTIVE_LOCK:
            _ACTIVE = self
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"omni-worker-heartbeat-{self.run_id}",
            daemon=True,
        )
        self._thread.start()
        atexit.register(self._atexit_fallback)
        return self

    def record_activity(self, source: str) -> None:
        with self._lock:
            self._last_activity_ts = time.time()
            self._last_activity_source = str(source)[:200]
            self._activity_count += 1

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.heartbeat_interval_s):
            try:
                self._write_heartbeat(phase="running")
            except Exception:
                # A failed heartbeat write must not kill the worker; a stale
                # heartbeat is exactly what the monitor should see then.
                pass

    def _write_heartbeat(self, *, phase: str) -> None:
        with self._lock:
            self._seq += 1
            now = time.time()
            payload = {
                "schema": LIFECYCLE_SCHEMA,
                "run_id": self.run_id,
                "pid": self.pid,
                "seq": self._seq,
                "ts": now,
                "ts_iso": _iso(now),
                "phase": phase,
                "uptime_s": round(now - self.started_ts, 3),
                "last_activity_ts": self._last_activity_ts,
                "last_activity_at": _iso(self._last_activity_ts) if self._last_activity_ts else None,
                "last_activity_source": self._last_activity_source,
                "activity_count": self._activity_count,
            }
        _write_json_atomic(self.heartbeat_path, payload)
        self._last_heartbeat_mono = time.monotonic()

    def finish(self, *, status: str, exit_code: int | None, reason: str = "") -> dict[str, Any]:
        """Write exit.json once; later calls return the first record."""

        global _ACTIVE
        with self._lock:
            if self._finished is not None:
                return dict(self._finished)
            now = time.time()
            payload = {
                "schema": LIFECYCLE_SCHEMA,
                "run_id": self.run_id,
                "pid": self.pid,
                "create_time": self.create_time,
                "status": status,
                "exit_code": exit_code,
                "reason": str(reason or "")[:_REASON_LIMIT],
                "writer": "controller",
                "ended_ts": now,
                "ended_at": _iso(now),
                "duration_s": round(now - self.started_ts, 3),
                "last_activity_ts": self._last_activity_ts,
            }
            self._finished = payload
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        try:
            self._write_heartbeat(phase="exited")
        except Exception:
            pass
        _write_json_atomic(self.exit_path, payload)
        with _ACTIVE_LOCK:
            if _ACTIVE is self:
                _ACTIVE = None
        try:
            atexit.unregister(self._atexit_fallback)
        except Exception:
            pass
        return dict(payload)

    def _atexit_fallback(self) -> None:
        if self._finished is None:
            try:
                self.finish(
                    status="error",
                    exit_code=None,
                    reason="controller interpreter exited before writing an exit marker",
                )
            except Exception:
                pass


def register_run_link(run_dir: Path, run_id: str, *, default_root: Path | None = None) -> Path | None:
    """Make an explicit --run-root run discoverable from the default root."""

    root = (default_root or default_worker_run_root()).resolve()
    run_dir = Path(run_dir).resolve()
    if run_dir.parent == root:
        return None
    link_path = root / run_id / LINK_FILENAME
    _write_json_atomic(
        link_path,
        {"schema": LIFECYCLE_SCHEMA, "run_id": run_id, "run_dir": str(run_dir)},
    )
    return link_path


# ─── monitor/CLI-side inspection ───────────────────────────────


def _resolve_link(entry: Path) -> Path | None:
    link = _read_json(entry / LINK_FILENAME)
    if not link or not link.get("run_dir"):
        return None
    target = Path(str(link["run_dir"]))
    return target if target.is_dir() else None


def _is_run_dir(path: Path) -> bool:
    return any(
        (path / name).is_file()
        for name in (PID_FILENAME, EXIT_FILENAME, LEGACY_REQUEST_FILENAME, LEGACY_RESULT_FILENAME)
    )


def discover_worker_runs(
    run_roots: Iterable[Path | str] | None = None,
    *,
    include_default: bool = True,
) -> list[Path]:
    """List run directories under explicit roots and/or the default root."""

    roots: list[Path] = [Path(root).expanduser().resolve() for root in (run_roots or ())]
    if include_default:
        roots.append(default_worker_run_root().resolve())
    seen: set[str] = set()
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            target = entry if _is_run_dir(entry) else _resolve_link(entry)
            if target is None:
                continue
            key = os.path.normcase(str(target.resolve()))
            if key in seen:
                continue
            seen.add(key)
            found.append(target)
    return found


def find_worker_run(
    run_ref: str,
    run_roots: Iterable[Path | str] | None = None,
    *,
    include_default: bool = True,
) -> Path | None:
    """Resolve a run id or run directory path."""

    direct = Path(run_ref).expanduser()
    if direct.is_dir():
        resolved = direct.resolve()
        if _is_run_dir(resolved):
            return resolved
        return _resolve_link(resolved)
    roots: list[Path] = [Path(root).expanduser().resolve() for root in (run_roots or ())]
    if include_default:
        roots.append(default_worker_run_root().resolve())
    for root in roots:
        entry = root / run_ref
        if entry.is_dir():
            if _is_run_dir(entry):
                return entry
            linked = _resolve_link(entry)
            if linked is not None:
                return linked
    return None


def read_worker_run(run_dir: Path | str, *, now: float | None = None) -> dict[str, Any]:
    """Classify one run directory without writing anything."""

    run_dir = Path(run_dir)
    now = time.time() if now is None else now
    pid_info = _read_json(run_dir / PID_FILENAME)
    heartbeat = _read_json(run_dir / HEARTBEAT_FILENAME)
    exit_info = _read_json(run_dir / EXIT_FILENAME)
    result = _read_json(run_dir / LEGACY_RESULT_FILENAME)
    request = _read_json(run_dir / LEGACY_REQUEST_FILENAME)

    heartbeat_ts = _float_or_none((heartbeat or {}).get("ts"))
    activity_ts = _float_or_none((heartbeat or {}).get("last_activity_ts"))
    record: dict[str, Any] = {
        "run_id": (pid_info or {}).get("run_id") or (result or {}).get("run_id") or run_dir.name,
        "run_dir": str(run_dir),
        "provider": (pid_info or {}).get("provider")
        or (result or {}).get("provider")
        or (request or {}).get("provider"),
        "pid": (pid_info or {}).get("pid"),
        "create_time": (pid_info or {}).get("create_time"),
        "started_at": (pid_info or {}).get("started_at") or (request or {}).get("created_at"),
        "containment": (pid_info or {}).get("containment"),
        "timeout_s": (pid_info or {}).get("timeout_s", (request or {}).get("timeout_s")),
        "idle_timeout_s": (pid_info or {}).get("idle_timeout_s"),
        "heartbeat_age_s": round(now - heartbeat_ts, 3) if heartbeat_ts is not None else None,
        "last_activity_age_s": round(now - activity_ts, 3) if activity_ts is not None else None,
        "has_result": result is not None,
        "result_status": (result or {}).get("status"),
        "exit": exit_info,
        "identity_alive": None,
        "legacy": pid_info is None,
    }

    if exit_info is not None:
        state = STATE_KILLED if exit_info.get("status") == "killed" else STATE_EXITED
    elif pid_info is None:
        state = STATE_LEGACY_FINISHED if result is not None else STATE_LEGACY_UNKNOWN
    else:
        alive = process_identity_alive(pid_info.get("pid"), pid_info.get("create_time"))
        record["identity_alive"] = alive
        if alive is False:
            state = STATE_LOST
        else:
            fresh = (
                record["heartbeat_age_s"] is not None
                and record["heartbeat_age_s"] <= HEARTBEAT_STALE_AFTER_S
            )
            state = STATE_RUNNING if fresh else STATE_STALE
    record["state"] = state
    record["protected"] = state == STATE_RUNNING
    return record


def reconcile_worker_run(run_dir: Path | str, *, now: float | None = None) -> dict[str, Any]:
    """Write a killed exit marker when the controller vanished without one.

    Only acts when worker.pid.json exists, exit.json is missing and the
    recorded identity (pid + create_time) is proven gone. Never touches
    processes; legacy run directories are left alone.
    """

    run_dir = Path(run_dir)
    now = time.time() if now is None else now
    record = read_worker_run(run_dir, now=now)
    record["reconciled"] = False
    if record["state"] != STATE_LOST:
        return record
    # Re-check right before writing so a racing controller exit wins.
    if (run_dir / EXIT_FILENAME).exists():
        return read_worker_run(run_dir, now=now) | {"reconciled": False}
    result_status = record.get("result_status")
    if result_status:
        status = str(result_status)
        reason = "controller gone after result.json; exit marker was missing"
    else:
        status = "killed"
        reason = "controller process identity gone without exit marker"
    heartbeat_age = record.get("heartbeat_age_s")
    payload = {
        "schema": LIFECYCLE_SCHEMA,
        "run_id": record["run_id"],
        "pid": record["pid"],
        "create_time": record["create_time"],
        "status": status,
        "exit_code": None,
        "reason": reason,
        "writer": "reconciler",
        "detected_ts": now,
        "detected_at": _iso(now),
        "last_heartbeat_ts": (now - heartbeat_age) if heartbeat_age is not None else None,
    }
    _write_json_atomic(run_dir / EXIT_FILENAME, payload)
    updated = read_worker_run(run_dir, now=now)
    updated["reconciled"] = True
    return updated


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


__all__ = [
    "ACTIVE_STATES",
    "CONTAINMENT_ENV",
    "DEFAULT_IDLE_TIMEOUT_S",
    "DEFAULT_TIMEOUT_S",
    "EXIT_FILENAME",
    "HEARTBEAT_FILENAME",
    "HEARTBEAT_INTERVAL_S",
    "HEARTBEAT_STALE_AFTER_S",
    "LIFECYCLE_SCHEMA",
    "LINK_FILENAME",
    "PID_FILENAME",
    "RUN_ROOT_ENV",
    "WorkerLifecycle",
    "active_worker_lifecycle",
    "contain_current_process",
    "containment_requested",
    "default_worker_run_root",
    "discover_worker_runs",
    "find_worker_run",
    "process_create_time",
    "process_identity_alive",
    "read_worker_run",
    "reconcile_worker_run",
    "record_worker_activity",
    "register_run_link",
]
