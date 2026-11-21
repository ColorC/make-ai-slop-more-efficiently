"""Targeted cleanup for CodeBuddy's completed Windows ConPTY helpers.

CodeBuddy 2.132.0 uses ``@lydell/node-pty`` for terminal tool calls. On Windows,
the command process can exit while its headless ``conhost.exe`` remains a
direct child of the long-lived CodeBuddy Node process. The native PTY object
then retains memory, handles and a worker thread until CodeBuddy itself exits.

This reaper runs only inside the detached PTY host that owns one CodeBuddy
session. It uses one Toolhelp discovery snapshot, then a session-local Windows
Job Object PID list in steady state. It will terminate a process only after all
of these facts hold:

* it is inside the owned CodeBuddy process tree;
* it is a direct child of a Node process;
* its command line identifies it as a headless ConPTY host;
* the command process created beside it is gone, or no command survived long
  enough to be observed; and
* the output-flush grace period has elapsed.

No process is selected by age, global count or text-output activity alone.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import time
from dataclasses import dataclass
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable

from .resource_monitor import _windows_process_skeletons


@dataclass(frozen=True)
class ProcessSkeleton:
    pid: int
    ppid: int
    name: str


@dataclass(frozen=True)
class ProcessDetails:
    pid: int
    ppid: int
    name: str
    create_time: float
    command: str


@dataclass
class _TrackedConhost:
    pid: int
    ppid: int
    create_time: float
    first_seen_at: float
    last_seen_at: float
    observations: int = 1
    shell_pid: int | None = None
    shell_gone_at: float | None = None


SnapshotProcesses = Callable[[], list[ProcessSkeleton]]
ReadDetails = Callable[[int], ProcessDetails | None]
TerminateProcess = Callable[[ProcessDetails], bool]


def _default_snapshot() -> list[ProcessSkeleton]:
    return [
        ProcessSkeleton(
            pid=int(item["pid"]),
            ppid=int(item["ppid"]),
            name=str(item["name"]),
        )
        for item in _windows_process_skeletons()
    ]


def _default_details(pid: int) -> ProcessDetails | None:
    try:
        import psutil

        proc = psutil.Process(pid)
        return ProcessDetails(
            pid=pid,
            ppid=int(proc.ppid()),
            name=str(proc.name()),
            create_time=float(proc.create_time()),
            command=" ".join(str(part) for part in proc.cmdline()),
        )
    except Exception:  # psutil uses several platform-specific process errors
        return None


def _default_terminate(expected: ProcessDetails) -> bool:
    """Terminate only if PID identity and headless ownership still match."""
    current = _default_details(expected.pid)
    if current is None:
        return True
    if (
        current.ppid != expected.ppid
        or current.name.lower() != "conhost.exe"
        or "--headless" not in current.command.lower()
        or abs(current.create_time - expected.create_time) > 0.01
    ):
        return False
    try:
        import psutil

        proc = psutil.Process(expected.pid)
        # On Windows ``terminate`` and ``kill`` both call TerminateProcess.
        # Issuing it is synchronous; waiting for kernel process-object cleanup
        # here would stall later snapshots for up to a second per completed
        # terminal, so disappearance is verified by the next cheap poll.
        proc.kill()
        return True
    except psutil.NoSuchProcess:
        return True
    except Exception:
        return False


class WindowsJobProcessSource:
    """Give the reaper a session-local PID list without steady global scans."""

    _PROCESS_TERMINATE = 0x0001
    _PROCESS_SET_QUOTA = 0x0100
    _JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3
    _ERROR_MORE_DATA = 234

    def __init__(
        self,
        root_pid: int,
        *,
        details: ReadDetails,
        fallback_interval_s: float | None = None,
    ) -> None:
        self.root_pid = int(root_pid)
        self._details = details
        self.fallback_interval_s = max(
            5.0,
            float(
                fallback_interval_s
                if fallback_interval_s is not None
                else os.environ.get("OMNI_CODEBUDDY_CONHOST_FALLBACK_POLL_S") or 15.0
            ),
        )
        self.mode = "initializing"
        self.initial_discovery_ms = 0.0
        self.assigned_processes = 0
        self.attach_error: str | None = None
        self._initialized = False
        self._job_handle: int | None = None
        self._cached_rows: dict[int, ProcessSkeleton] = {}
        self._next_fallback_at = 0.0
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_api()

    def _configure_api(self) -> None:
        self._kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self._kernel32.OpenProcess.restype = wintypes.HANDLE
        self._kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        self._kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        self._kernel32.IsProcessInJob.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.BOOL),
        ]
        self._kernel32.IsProcessInJob.restype = wintypes.BOOL
        self._kernel32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

    @staticmethod
    def _children(rows: list[ProcessSkeleton]) -> dict[int, list[int]]:
        children: dict[int, list[int]] = {}
        for item in rows:
            children.setdefault(item.ppid, []).append(item.pid)
        return children

    @staticmethod
    def _descendants(root_pid: int, children: dict[int, list[int]]) -> set[int]:
        found: set[int] = set()
        pending = [root_pid]
        while pending:
            pid = pending.pop()
            if pid in found:
                continue
            found.add(pid)
            pending.extend(children.get(pid, ()))
        return found

    def _open_process(self, pid: int) -> int | None:
        handle = self._kernel32.OpenProcess(
            self._PROCESS_TERMINATE | self._PROCESS_SET_QUOTA,
            False,
            pid,
        )
        return int(handle) if handle else None

    def _assign(self, pid: int) -> bool:
        if self._job_handle is None:
            return False
        process_handle = self._open_process(pid)
        if process_handle is None:
            return False
        try:
            already_member = wintypes.BOOL()
            if self._kernel32.IsProcessInJob(
                process_handle,
                self._job_handle,
                ctypes.byref(already_member),
            ) and already_member.value:
                return True
            return bool(
                self._kernel32.AssignProcessToJobObject(
                    self._job_handle,
                    process_handle,
                )
            )
        finally:
            self._kernel32.CloseHandle(process_handle)

    def _initialize(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        started = time.perf_counter()
        try:
            handle = self._kernel32.CreateJobObjectW(None, None)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            self._job_handle = int(handle)

            # The WinPTY spawn can return a fraction before codebuddy.cmd has
            # created its Node child. Discovery is startup-only; retry here so
            # the steady watcher never needs a global process snapshot.
            rows: list[ProcessSkeleton] = []
            node_pids: set[int] = set()
            owned: set[int] = set()
            for attempt in range(3):
                rows = _default_snapshot()
                children = self._children(rows)
                owned = self._descendants(self.root_pid, children)
                node_pids = {
                    item.pid
                    for item in rows
                    if item.pid in owned and item.name.lower() == "node.exe"
                }
                if node_pids:
                    break
                if attempt < 2:
                    time.sleep(0.2)
            if not node_pids:
                raise RuntimeError("CodeBuddy Node process was not discovered")

            children = self._children(rows)
            targets: list[int] = []
            for node_pid in node_pids:
                targets.extend(
                    sorted(self._descendants(node_pid, children), key=lambda pid: pid != node_pid)
                )
            assigned_pids: set[int] = set()
            for pid in dict.fromkeys(targets):
                if self._assign(pid):
                    assigned_pids.add(pid)
            if not (node_pids & assigned_pids):
                raise RuntimeError("CodeBuddy Node could not be assigned to a monitor job")
            self.assigned_processes = len(assigned_pids)
            self.mode = "job_object"
        except Exception as exc:
            self.attach_error = f"{type(exc).__name__}: {exc}"
            self.mode = "low_frequency_fallback"
            self.close()
        finally:
            self.initial_discovery_ms = round(
                (time.perf_counter() - started) * 1000,
                3,
            )

    def _query_job_pids(self) -> list[int]:
        if self._job_handle is None:
            return []
        pointer_size = ctypes.sizeof(ctypes.c_size_t)
        size = 8 + pointer_size * 256
        while True:
            buffer = ctypes.create_string_buffer(size)
            returned = wintypes.DWORD()
            ok = self._kernel32.QueryInformationJobObject(
                self._job_handle,
                self._JOB_OBJECT_BASIC_PROCESS_ID_LIST,
                buffer,
                size,
                ctypes.byref(returned),
            )
            assigned = ctypes.c_uint32.from_buffer(buffer, 0).value
            included = ctypes.c_uint32.from_buffer(buffer, 4).value
            if ok:
                array_type = ctypes.c_size_t * included
                values = array_type.from_buffer(buffer, 8)
                return [int(value) for value in values]
            error = ctypes.get_last_error()
            if error != self._ERROR_MORE_DATA:
                raise ctypes.WinError(error)
            size = max(size * 2, 8 + pointer_size * max(assigned, included, 1))

    def _rows_for_pids(self, pids: list[int]) -> list[ProcessSkeleton]:
        live = set(pids)
        for pid in list(self._cached_rows):
            if pid not in live:
                self._cached_rows.pop(pid, None)
        for pid in live:
            if pid in self._cached_rows:
                continue
            detail = self._details(pid)
            if detail is None:
                continue
            self._cached_rows[pid] = ProcessSkeleton(
                pid=pid,
                ppid=detail.ppid,
                name=detail.name,
            )
        return list(self._cached_rows.values())

    def snapshot(self) -> list[ProcessSkeleton]:
        self._initialize()
        if self.mode == "job_object":
            return self._rows_for_pids(self._query_job_pids())
        now = time.monotonic()
        if now < self._next_fallback_at:
            return list(self._cached_rows.values())
        self._next_fallback_at = now + self.fallback_interval_s
        rows = _default_snapshot()
        children = self._children(rows)
        owned = self._descendants(self.root_pid, children)
        self._cached_rows = {item.pid: item for item in rows if item.pid in owned}
        return list(self._cached_rows.values())

    def close(self) -> None:
        if self._job_handle is not None:
            self._kernel32.CloseHandle(self._job_handle)
            self._job_handle = None

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "initial_discovery_ms": self.initial_discovery_ms,
            "assigned_processes": self.assigned_processes,
            "fallback_interval_s": self.fallback_interval_s,
            "attach_error": self.attach_error,
        }


class CodeBuddyConhostReaper:
    """Observe one owned CodeBuddy tree and reap only completed ConPTY hosts."""

    def __init__(
        self,
        root_pid: int,
        *,
        poll_interval_s: float | None = None,
        exit_grace_s: float | None = None,
        snapshot: SnapshotProcesses | None = None,
        details: ReadDetails = _default_details,
        terminate: TerminateProcess = _default_terminate,
    ) -> None:
        self.root_pid = int(root_pid)
        self.poll_interval_s = max(
            0.1,
            float(
                poll_interval_s
                if poll_interval_s is not None
                else os.environ.get("OMNI_CODEBUDDY_CONHOST_POLL_S") or 0.5
            ),
        )
        self.exit_grace_s = max(
            1.1,
            float(
                exit_grace_s
                if exit_grace_s is not None
                else os.environ.get("OMNI_CODEBUDDY_CONHOST_EXIT_GRACE_S") or 2.5
            ),
        )
        self.pair_window_s = max(1.0, self.poll_interval_s * 3.0)
        self._details = details
        self._terminate = terminate
        self._process_source: WindowsJobProcessSource | None = None
        if snapshot is None:
            self._process_source = WindowsJobProcessSource(
                self.root_pid,
                details=details,
            )
            self._snapshot = self._process_source.snapshot
        else:
            self._snapshot = snapshot
        self._tracked: dict[int, _TrackedConhost] = {}
        self.reaped = 0
        self.natural_exits = 0
        self.last_reaped_at: float | None = None
        self.last_poll_ms = 0.0
        self.last_error: str | None = None

    @staticmethod
    def _descendants(
        root_pid: int,
        children: dict[int, list[int]],
    ) -> set[int]:
        found: set[int] = set()
        pending = [root_pid]
        while pending:
            pid = pending.pop()
            if pid in found:
                continue
            found.add(pid)
            pending.extend(children.get(pid, ()))
        return found

    @staticmethod
    def _is_headless_conhost(value: ProcessDetails | None) -> bool:
        return bool(
            value is not None
            and value.name.lower() == "conhost.exe"
            and "--headless" in value.command.lower()
        )

    def _match_shells(
        self,
        tracked: list[_TrackedConhost],
        peers: list[ProcessDetails],
    ) -> None:
        available = {item.pid: item for item in peers}
        for item in tracked:
            if item.shell_pid is not None:
                available.pop(item.shell_pid, None)
        for item in sorted(tracked, key=lambda value: value.create_time):
            if item.shell_pid is not None:
                continue
            candidates = [
                peer
                for peer in available.values()
                if peer.ppid == item.ppid
                and peer.name.lower() != "conhost.exe"
                and abs(peer.create_time - item.create_time) <= self.pair_window_s
            ]
            if not candidates:
                continue
            peer = min(
                candidates,
                key=lambda value: abs(value.create_time - item.create_time),
            )
            item.shell_pid = peer.pid
            available.pop(peer.pid, None)

    def poll(self, *, now: float | None = None) -> list[int]:
        """Take one bounded snapshot and return the PIDs reaped this poll."""
        started = time.perf_counter()
        observed_at = time.monotonic() if now is None else float(now)
        try:
            rows = self._snapshot()
            by_pid = {item.pid: item for item in rows}
            children: dict[int, list[int]] = {}
            for item in rows:
                children.setdefault(item.ppid, []).append(item.pid)
            owned = self._descendants(self.root_pid, children)
            node_pids = {
                pid
                for pid in owned
                if pid in by_pid and by_pid[pid].name.lower() == "node.exe"
            }

            live_tracked = set(self._tracked) & set(by_pid)
            for pid in list(self._tracked):
                if pid not in live_tracked:
                    self._tracked.pop(pid, None)
                    self.natural_exits += 1

            for item in rows:
                if (
                    item.pid in self._tracked
                    or item.pid not in owned
                    or item.ppid not in node_pids
                    or item.name.lower() != "conhost.exe"
                ):
                    continue
                detail = self._details(item.pid)
                if not self._is_headless_conhost(detail):
                    continue
                assert detail is not None
                self._tracked[item.pid] = _TrackedConhost(
                    pid=item.pid,
                    ppid=item.ppid,
                    create_time=detail.create_time,
                    first_seen_at=observed_at,
                    last_seen_at=observed_at,
                )

            tracked = list(self._tracked.values())
            peer_pids = {
                item.pid
                for item in rows
                if item.ppid in {value.ppid for value in tracked}
                and item.name.lower() != "conhost.exe"
            }
            peers = [
                detail
                for pid in peer_pids
                if (detail := self._details(pid)) is not None
            ]
            self._match_shells(tracked, peers)

            reaped: list[int] = []
            live_pids = set(by_pid)
            for item in tracked:
                item.observations += 1
                item.last_seen_at = observed_at
                if item.shell_pid is not None and item.shell_pid in live_pids:
                    item.shell_gone_at = None
                    continue
                if item.shell_pid is not None and item.shell_gone_at is None:
                    item.shell_gone_at = observed_at
                idle_since = item.shell_gone_at or item.first_seen_at
                if (
                    item.observations < 2
                    or observed_at - idle_since < self.exit_grace_s
                ):
                    continue
                current = self._details(item.pid)
                if (
                    not self._is_headless_conhost(current)
                    or current is None
                    or current.ppid != item.ppid
                    or abs(current.create_time - item.create_time) > 0.01
                ):
                    continue
                if self._terminate(current):
                    reaped.append(item.pid)
                    self._tracked.pop(item.pid, None)
                    self.reaped += 1
                    self.last_reaped_at = time.time()

            self.last_error = None
            return reaped
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return []
        finally:
            self.last_poll_ms = round((time.perf_counter() - started) * 1000, 3)

    def status(self) -> dict[str, Any]:
        status = {
            "enabled": True,
            "root_pid": self.root_pid,
            "poll_interval_s": self.poll_interval_s,
            "exit_grace_s": self.exit_grace_s,
            "tracked": len(self._tracked),
            "tracked_pids": sorted(self._tracked),
            "reaped": self.reaped,
            "natural_exits": self.natural_exits,
            "last_reaped_at": self.last_reaped_at,
            "last_poll_ms": self.last_poll_ms,
            "last_error": self.last_error,
        }
        if self._process_source is not None:
            status["process_source"] = self._process_source.status()
        return status

    def close(self) -> None:
        if self._process_source is not None:
            self._process_source.close()


def _write_bridge_status(
    path: Path,
    *,
    started_at: float,
    reapers: dict[int, CodeBuddyConhostReaper],
    completed: dict[int, dict[str, Any]],
    running: bool,
) -> None:
    payload = {
        "schema": "omni.codebuddy-conhost-reaper-bridge.v1",
        "bridge_pid": os.getpid(),
        "started_at": started_at,
        "updated_at": time.time(),
        "running": running,
        "roots": {str(pid): reaper.status() for pid, reaper in reapers.items()},
        "completed_roots": {str(pid): value for pid, value in completed.items()},
        "total_reaped": (
            sum(reaper.reaped for reaper in reapers.values())
            + sum(int(value.get("reaped") or 0) for value in completed.values())
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def run_existing_session_bridge(
    root_pids: list[int],
    *,
    status_path: Path | None = None,
) -> None:
    """Keep pre-deployment CodeBuddy sessions protected until they exit."""
    started_at = time.time()
    reapers = {
        int(pid): CodeBuddyConhostReaper(int(pid))
        for pid in dict.fromkeys(root_pids)
        if int(pid) > 0
    }
    try:
        import psutil

        root_processes = {pid: psutil.Process(pid) for pid in reapers}
    except Exception:
        root_processes = {}
    completed: dict[int, dict[str, Any]] = {}
    next_status_at = 0.0
    try:
        while reapers:
            changed = False
            for root_pid, reaper in list(reapers.items()):
                root_process = root_processes.get(root_pid)
                root_alive = (
                    root_process.is_running()
                    if root_process is not None
                    else _default_details(root_pid) is not None
                )
                if not root_alive:
                    completed[root_pid] = reaper.status()
                    reaper.close()
                    reapers.pop(root_pid, None)
                    root_processes.pop(root_pid, None)
                    changed = True
                    continue
                if reaper.poll():
                    changed = True
            now = time.monotonic()
            if status_path is not None and (changed or now >= next_status_at):
                _write_bridge_status(
                    status_path,
                    started_at=started_at,
                    reapers=reapers,
                    completed=completed,
                    running=True,
                )
                next_status_at = now + 30.0
            if reapers:
                time.sleep(
                    min(reaper.poll_interval_s for reaper in reapers.values())
                )
    finally:
        for reaper in reapers.values():
            reaper.close()
        if status_path is not None:
            _write_bridge_status(
                status_path,
                started_at=started_at,
                reapers=reapers,
                completed=completed,
                running=False,
            )


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Protect existing CodeBuddy sessions from completed ConPTY residue",
    )
    parser.add_argument(
        "--root-pid",
        action="append",
        type=int,
        required=True,
        help="Existing hosted CodeBuddy wrapper PID; may be repeated",
    )
    parser.add_argument("--status-path", type=Path)
    args = parser.parse_args()
    run_existing_session_bridge(args.root_pid, status_path=args.status_path)


__all__ = [
    "CodeBuddyConhostReaper",
    "ProcessDetails",
    "ProcessSkeleton",
    "WindowsJobProcessSource",
    "run_existing_session_bridge",
]


if __name__ == "__main__":
    _main()
