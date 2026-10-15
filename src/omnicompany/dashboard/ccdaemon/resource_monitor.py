"""Read-only resource attribution and anomaly monitoring for ccdaemon.

The monitor deliberately has no termination/suspend API.  It observes Windows
processes, attributes descendants to durable PTY sessions and local review/dev
facilities, and writes evidence that a human can use before cleanup.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import multiprocessing
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from omnicompany.core.config import omni_workspace_root

logger = logging.getLogger(__name__)

SCHEMA = "omni.resource-monitor.v1"
# A full Windows process attribution pass is deliberately comprehensive and can
# take tens of seconds on machines with many CLI trees.  Running that every
# minute turns observation into a recurring source of scheduler pressure.  The
# monitor is diagnostic, not a realtime control loop, so ten minutes is the
# safer default; operators can still request a shorter interval explicitly.
DEFAULT_INTERVAL_S = float(os.environ.get("OMNI_RESOURCE_MONITOR_INTERVAL_S") or 600)
DEPRIORITIZE_PROCESS_CPU_PCT = float(
    os.environ.get("OMNI_RESOURCE_DEPRIORITIZE_PROCESS_CPU_PCT") or 10.0
)
DEPRIORITIZE_PROCESS_PRIVATE_BYTES = int(
    os.environ.get("OMNI_RESOURCE_DEPRIORITIZE_PROCESS_PRIVATE_BYTES") or 512 * 1024**2
)
HISTORY_MAX_BYTES = int(
    os.environ.get("OMNI_RESOURCE_MONITOR_HISTORY_MAX_BYTES") or 20 * 1024 * 1024
)
ALERTS_MAX_BYTES = int(
    os.environ.get("OMNI_RESOURCE_MONITOR_ALERTS_MAX_BYTES") or 10 * 1024 * 1024
)
_PTY_SESSION_RE = re.compile(r"pty_hosts[\\/]\.([0-9a-f]{16})\.launch", re.IGNORECASE)
_SECRET_FLAGS = {
    "--api-key",
    "--apikey",
    "--authorization",
    "--password",
    "--secret",
    "--token",
}


@dataclass(frozen=True)
class ProcessFact:
    pid: int
    ppid: int
    name: str
    command: str
    create_time: float
    rss_bytes: int
    private_bytes: int
    handles: int
    threads: int
    cpu_seconds: float = 0.0
    cpu_percent: float = 0.0
    cwd: str = ""

    @property
    def age_s(self) -> float:
        return max(0.0, time.time() - self.create_time)


@dataclass(frozen=True)
class Thresholds:
    system_processes_warning: int = 600
    system_processes_critical: int = 750
    system_handles_warning: int = 300_000
    system_handles_critical: int = 400_000
    system_memory_warning_pct: float = 85.0
    system_memory_critical_pct: float = 95.0
    system_cpu_warning_pct: float = 90.0
    group_processes_warning: int = 30
    group_handles_warning: int = 15_000
    group_handles_critical: int = 30_000
    group_private_warning_bytes: int = 4 * 1024**3
    group_private_critical_bytes: int = 8 * 1024**3
    group_cpu_warning_pct: float = 400.0
    process_handles_warning: int = 3_000
    process_handles_critical: int = 6_000
    orphan_min_age_s: float = 30 * 60

    @classmethod
    def from_env(cls) -> "Thresholds":
        defaults = cls()

        def integer(name: str, default: int) -> int:
            return int(os.environ.get(name) or default)

        def decimal(name: str, default: float) -> float:
            return float(os.environ.get(name) or default)

        return cls(
            system_processes_warning=integer(
                "OMNI_RESOURCE_WARN_PROCESSES", defaults.system_processes_warning
            ),
            system_processes_critical=integer(
                "OMNI_RESOURCE_CRITICAL_PROCESSES", defaults.system_processes_critical
            ),
            system_handles_warning=integer(
                "OMNI_RESOURCE_WARN_HANDLES", defaults.system_handles_warning
            ),
            system_handles_critical=integer(
                "OMNI_RESOURCE_CRITICAL_HANDLES", defaults.system_handles_critical
            ),
            system_memory_warning_pct=decimal(
                "OMNI_RESOURCE_WARN_MEMORY_PCT", defaults.system_memory_warning_pct
            ),
            system_memory_critical_pct=decimal(
                "OMNI_RESOURCE_CRITICAL_MEMORY_PCT", defaults.system_memory_critical_pct
            ),
            system_cpu_warning_pct=decimal(
                "OMNI_RESOURCE_WARN_CPU_PCT", defaults.system_cpu_warning_pct
            ),
            group_processes_warning=integer(
                "OMNI_RESOURCE_WARN_GROUP_PROCESSES", defaults.group_processes_warning
            ),
            group_handles_warning=integer(
                "OMNI_RESOURCE_WARN_GROUP_HANDLES", defaults.group_handles_warning
            ),
            group_handles_critical=integer(
                "OMNI_RESOURCE_CRITICAL_GROUP_HANDLES", defaults.group_handles_critical
            ),
            group_private_warning_bytes=integer(
                "OMNI_RESOURCE_WARN_GROUP_PRIVATE_BYTES",
                defaults.group_private_warning_bytes,
            ),
            group_private_critical_bytes=integer(
                "OMNI_RESOURCE_CRITICAL_GROUP_PRIVATE_BYTES",
                defaults.group_private_critical_bytes,
            ),
            group_cpu_warning_pct=decimal(
                "OMNI_RESOURCE_WARN_GROUP_CPU_PCT", defaults.group_cpu_warning_pct
            ),
            process_handles_warning=integer(
                "OMNI_RESOURCE_WARN_PROCESS_HANDLES", defaults.process_handles_warning
            ),
            process_handles_critical=integer(
                "OMNI_RESOURCE_CRITICAL_PROCESS_HANDLES",
                defaults.process_handles_critical,
            ),
            orphan_min_age_s=decimal(
                "OMNI_RESOURCE_ORPHAN_MIN_AGE_S", defaults.orphan_min_age_s
            ),
        )


DISK_DRIVES = ("C:\\", "D:\\", "E:\\")
GIB = 1024**3


def _query_windows_volume(root: str) -> dict[str, Any]:
    """Read one mounted volume using the Windows native disk-space APIs."""
    if os.name != "nt":
        raise OSError("Windows volume query is unavailable on this platform")
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    free_space = kernel32.GetDiskFreeSpaceExW
    free_space.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_ulonglong),
        ctypes.POINTER(ctypes.c_ulonglong),
        ctypes.POINTER(ctypes.c_ulonglong),
    ]
    free_space.restype = wintypes.BOOL
    available = ctypes.c_ulonglong()
    total = ctypes.c_ulonglong()
    free = ctypes.c_ulonglong()
    if not free_space(root, ctypes.byref(available), ctypes.byref(total), ctypes.byref(free)):
        raise ctypes.WinError(ctypes.get_last_error())

    serial = wintypes.DWORD()
    get_volume = kernel32.GetVolumeInformationW
    get_volume.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    get_volume.restype = wintypes.BOOL
    if not get_volume(root, None, 0, ctypes.byref(serial), None, None, None, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return {
        "volume_identity": f"{root.upper()}:{int(serial.value):08x}",
        "available_to_caller_bytes": int(available.value),
        "total_bytes": int(total.value),
        "free_bytes": int(free.value),
        "queried_at": time.time(),
        "coverage": "complete",
    }


def _collect_disk_usage(
    drives: Iterable[str] = DISK_DRIVES,
    *,
    query=_query_windows_volume,
) -> dict[str, Any]:
    """Sample explicit drive roots; query failures remain unknown, never zero."""
    volumes: list[dict[str, Any]] = []
    for root in drives:
        try:
            item = query(root)
            volumes.append({"root": root, **item})
        except Exception as exc:  # a missing/unavailable drive is not zero free space
            volumes.append(
                {
                    "root": root,
                    "volume_identity": None,
                    "available_to_caller_bytes": None,
                    "total_bytes": None,
                    "free_bytes": None,
                    "queried_at": time.time(),
                    "coverage": "query_error",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:240],
                }
            )
    return {
        "disks": volumes,
        "resource_coverage_disks": sum(
            volume["coverage"] == "complete" for volume in volumes
        ),
    }


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _nonnegative_bytes(value: Any) -> float | None:
    number = _finite_number(value)
    return number if number is not None and number >= 0 else None


def _add_disk_growth(current: dict[str, Any], previous: dict[str, Any]) -> None:
    old = {
        item.get("volume_identity"): item
        for item in (previous.get("disks") or [])
        if item.get("volume_identity") and item.get("coverage") == "complete"
    }
    for item in current.get("disks") or []:
        item["growth_bytes_per_sec"] = None
        item["growth_interval_seconds"] = None
        prior = old.get(item.get("volume_identity"))
        if item.get("coverage") != "complete" or prior is None:
            continue
        current_time = _finite_number(item.get("queried_at"))
        previous_time = _finite_number(prior.get("queried_at"))
        current_free = _nonnegative_bytes(item.get("free_bytes"))
        previous_free = _nonnegative_bytes(prior.get("free_bytes"))
        if (
            current_time is None
            or previous_time is None
            or current_time <= 0
            or previous_time <= 0
            or current_free is None
            or previous_free is None
        ):
            continue
        elapsed = current_time - previous_time
        if elapsed <= 0:
            continue
        item["growth_bytes_per_sec"] = (previous_free - current_free) / elapsed
        item["growth_interval_seconds"] = elapsed


def _disk_observations(system: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    cfg = (policy or {}).get("disk_supervision") or {}
    if not cfg.get("enabled", False):
        return []
    warn_free = int(cfg.get("warning_free_bytes", 30 * GIB))
    critical_free = int(cfg.get("critical_free_bytes", 10 * GIB))
    warn_growth = float(cfg.get("warning_growth_bytes_per_hour", 5 * GIB)) / 3600.0
    out: list[dict[str, Any]] = []
    for volume in system.get("disks") or []:
        free = _nonnegative_bytes(volume.get("free_bytes"))
        if volume.get("coverage") != "complete" or free is None:
            continue
        evidence = {k: volume.get(k) for k in ("root", "volume_identity", "free_bytes", "total_bytes", "available_to_caller_bytes", "growth_bytes_per_sec", "growth_interval_seconds", "queried_at", "coverage")}
        if free < warn_free:
            critical = free < critical_free
            out.append(_observation(
                "disk-free-space", str(volume["volume_identity"]), "critical" if critical else "warning",
                f"{volume['root']} has {free / GIB:.1f} GiB free", evidence,
                confirm_after=int(cfg.get("confirmation_samples", 2)), confidence="high",
            ))
        growth = volume.get("growth_bytes_per_sec")
        growth = _finite_number(growth)
        if growth is not None and float(growth) > warn_growth:
            out.append(_observation(
                "disk-consumption-rate", str(volume["volume_identity"]), "warning",
                f"{volume['root']} is consuming {float(growth) * 3600 / GIB:.1f} GiB/hour", evidence,
                confirm_after=int(cfg.get("confirmation_samples", 2)), confidence="high",
            ))
    return out


def _preserve_unmeasured_disk_alerts(
    state: dict[str, Any],
    events: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
    previous: dict[str, Any],
    system: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Do not resolve an active disk alert when that volume could not be measured."""
    preserve: set[tuple[Any, str]] = set()
    for volume in system.get("disks") or []:
        root = volume.get("root")
        free = _nonnegative_bytes(volume.get("free_bytes"))
        if volume.get("coverage") != "complete" or free is None:
            preserve.update(
                (root, kind)
                for kind in ("disk-free-space", "disk-consumption-rate")
            )
            continue
        growth = _finite_number(volume.get("growth_bytes_per_sec"))
        elapsed = _finite_number(volume.get("growth_interval_seconds"))
        if growth is None or elapsed is None or elapsed <= 0:
            preserve.add((root, "disk-consumption-rate"))
    old_active = previous.get("active") or {}
    if not isinstance(old_active, dict):
        old_active = {}
    retained = [
        alert
        for alert in old_active.values()
        if isinstance(alert, dict)
        and (alert.get("kind"), (alert.get("evidence") or {}).get("root"))
        in {(kind, root) for root, kind in preserve}
    ]
    if not retained:
        return state, events, alerts
    active = state.setdefault("active", {})
    for alert in retained:
        active[alert["fingerprint"]] = alert
    retained_fingerprints = {alert["fingerprint"] for alert in retained}
    events = [
        event for event in events
        if not (
            event.get("event") == "resolved"
            and (event.get("alert") or {}).get("fingerprint") in retained_fingerprints
        )
    ]
    alerts = [
        alert for alert in alerts
        if alert.get("fingerprint") not in retained_fingerprints
    ] + retained
    return state, events, alerts


def _redact_command(parts: Iterable[str] | str) -> str:
    tokens = list(parts) if not isinstance(parts, str) else parts.split()
    redacted: list[str] = []
    redact_next = False
    for token in tokens:
        if redact_next:
            redacted.append("<redacted>")
            redact_next = False
            continue
        lowered = token.lower()
        if lowered in _SECRET_FLAGS:
            redacted.append(token)
            redact_next = True
            continue
        if "=" in token and token.split("=", 1)[0].lower() in _SECRET_FLAGS:
            redacted.append(f"{token.split('=', 1)[0]}=<redacted>")
            continue
        redacted.append(token)
    return " ".join(redacted)[:600]


def _windows_process_skeletons() -> list[dict[str, Any]]:
    """Enumerate PID/PPID/name/thread count with one Toolhelp snapshot.

    ``psutil.process_iter`` is unexpectedly expensive on this workstation
    (10-20s for ~550 processes). Toolhelp is a single kernel snapshot and lets
    us reserve per-process queries for workspace candidates only.
    """
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(PROCESSENTRY32W),
    ]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(PROCESSENTRY32W),
    ]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    invalid = ctypes.c_void_p(-1).value
    if snapshot == invalid:
        raise ctypes.WinError(ctypes.get_last_error())
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    values: list[dict[str, Any]] = []
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            values.append(
                {
                    "pid": int(entry.th32ProcessID),
                    "ppid": int(entry.th32ParentProcessID),
                    "name": str(entry.szExeFile),
                    "threads": int(entry.cntThreads),
                }
            )
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return values


def _windows_performance_counts() -> dict[str, int]:
    if os.name != "nt":
        return {}
    import ctypes
    from ctypes import wintypes

    class PERFORMANCE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("CommitTotal", ctypes.c_size_t),
            ("CommitLimit", ctypes.c_size_t),
            ("CommitPeak", ctypes.c_size_t),
            ("PhysicalTotal", ctypes.c_size_t),
            ("PhysicalAvailable", ctypes.c_size_t),
            ("SystemCache", ctypes.c_size_t),
            ("KernelTotal", ctypes.c_size_t),
            ("KernelPaged", ctypes.c_size_t),
            ("KernelNonpaged", ctypes.c_size_t),
            ("PageSize", ctypes.c_size_t),
            ("HandleCount", wintypes.DWORD),
            ("ProcessCount", wintypes.DWORD),
            ("ThreadCount", wintypes.DWORD),
        ]

    value = PERFORMANCE_INFORMATION()
    value.cb = ctypes.sizeof(value)
    if not ctypes.WinDLL("psapi").GetPerformanceInfo(
        ctypes.byref(value), ctypes.sizeof(value)
    ):
        return {}
    return {
        "handles": int(value.HandleCount),
        "process_count": int(value.ProcessCount),
        "threads": int(value.ThreadCount),
    }


def _is_dev_service(fact: ProcessFact) -> bool:
    command = fact.command.lower().replace("/", "\\")
    name = fact.name.lower()
    return (
        name == "node.exe"
        and any(
            marker in command
            for marker in ("\\vite\\bin\\vite", "\\astro\\astro", " server.mjs", " serve.mjs")
        )
    ) or (
        name in {"python.exe", "pythonw.exe"}
        and "-m http.server" in command
    )


def _service_key(fact: ProcessFact) -> str:
    command = fact.command.replace("/", "\\")
    lowered = command.lower()
    directory = re.search(r"--directory\s+(.+?)(?=\s+--|$)", command, re.IGNORECASE)
    if directory:
        value = directory.group(1).strip(' "\'')
        path = Path(value)
        if not path.is_absolute() and fact.cwd:
            value = str(Path(fact.cwd) / path)
        return value.lower()
    node_modules = lowered.find("\\node_modules\\")
    if node_modules >= 0:
        prefix = command[:node_modules].strip(' "\'')
        drive = re.search(r"([A-Za-z]:\\.*)$", prefix)
        if drive:
            return drive.group(1).lower()
    if fact.cwd:
        return fact.cwd.lower()
    if "omnicompany.packages." in lowered:
        module = re.search(r"-m\s+(omnicompany\.packages\.[^\s]+)", lowered)
        if module:
            return module.group(1)
    digest = hashlib.sha1(lowered.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"command:{digest}"


def _session_id(fact: ProcessFact) -> str | None:
    match = _PTY_SESSION_RE.search(fact.command)
    return match.group(1).lower() if match else None


def _provider_for(facts: Iterable[ProcessFact]) -> str:
    command = " ".join(item.command.lower() for item in facts)
    if "kimi" in command:
        return "kimi"
    # zcode 必须排在 codex 之前: 子串包含 codex
    if "zcode" in command:
        return "zcode"
    if "grok" in command:
        return "grok"
    # codex_glm / codex_the_company 与官方 codex 同一个可执行文件, 只有进程级
    # provider 覆盖能区分, 所以先看覆盖标记再落回 codex。
    if 'model_provider="zai"' in command or "model_provider=zai" in command:
        return "codex_glm"
    if 'model_provider="the_company"' in command or "model_provider=the_company" in command:
        return "codex_the_company"
    if "codex" in command:
        return "codex"
    if "gemini" in command:
        return "gemini"
    if "claude" in command:
        return "claude_code"
    if "opencode" in command:
        return "opencode"
    if "dsh-pty" in command or "omni-dsh-pty" in command:
        return "dsh"
    return "shell"


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


def _process_summary(fact: ProcessFact) -> dict[str, Any]:
    return {
        "pid": fact.pid,
        "ppid": fact.ppid,
        "name": fact.name,
        "command": fact.command,
        "age_s": round(fact.age_s, 1),
        "cpu_percent": round(fact.cpu_percent, 1),
        "rss_bytes": fact.rss_bytes,
        "private_bytes": fact.private_bytes,
        "handles": fact.handles,
        "threads": fact.threads,
    }


def _group_summary(
    group_id: str,
    role: str,
    facts: list[ProcessFact],
    **owner: Any,
) -> dict[str, Any]:
    ordered = sorted(
        facts,
        key=lambda item: (item.cpu_percent, item.handles, item.private_bytes),
        reverse=True,
    )
    root_pid = int(owner.get("root_pid") or 0)
    # Never lower the detached PTY host itself.  It is the interactive transport
    # for the whole session, while expensive test/build children are the actual
    # background workload.  Lowering the whole tree made keystrokes and daemon
    # calls starve behind a worker pool even though Windows remained responsive.
    deprioritization_pids = [
        item.pid
        for item in ordered
        if item.pid != root_pid
        and (
            item.cpu_percent >= DEPRIORITIZE_PROCESS_CPU_PCT
            or item.private_bytes >= DEPRIORITIZE_PROCESS_PRIVATE_BYTES
        )
    ]
    return {
        "id": group_id,
        "role": role,
        "owner": owner,
        "pids": sorted(item.pid for item in facts),
        "process_count": len(facts),
        "cpu_percent": round(sum(item.cpu_percent for item in facts), 1),
        "rss_bytes": sum(item.rss_bytes for item in facts),
        "private_bytes": sum(item.private_bytes for item in facts),
        "handles": sum(item.handles for item in facts),
        "threads": sum(item.threads for item in facts),
        "deprioritization_pids": deprioritization_pids,
        "top_processes": [_process_summary(item) for item in ordered[:8]],
    }


def attribute_processes(
    facts: list[ProcessFact],
    *,
    workspace_root: str,
) -> tuple[list[dict[str, Any]], dict[int, str]]:
    """Attribute process trees without making any cleanup decision."""
    by_pid = {item.pid: item for item in facts}
    children: dict[int, list[int]] = {}
    for item in facts:
        children.setdefault(item.ppid, []).append(item.pid)

    assigned: dict[int, str] = {}
    groups: list[dict[str, Any]] = []

    def add_tree(root: ProcessFact, group_id: str, role: str, **owner: Any) -> None:
        pids = _descendants(root.pid, children)
        members = [by_pid[pid] for pid in pids if pid in by_pid and pid not in assigned]
        if not members:
            return
        for member in members:
            assigned[member.pid] = group_id
        groups.append(_group_summary(group_id, role, members, root_pid=root.pid, **owner))

    # Detached PTY roots are the strongest ownership evidence. They take
    # priority over the ccdaemon ancestor that originally launched them.
    for item in facts:
        sid = _session_id(item)
        if sid:
            descendants = [by_pid[pid] for pid in _descendants(item.pid, children) if pid in by_pid]
            add_tree(
                item,
                f"agent:{sid}",
                "agent-session",
                session_id=sid,
                provider=_provider_for(descendants),
            )

    for item in facts:
        command = item.command.lower()
        if "omnicompany.dashboard.ccdaemon.main:app" in command:
            add_tree(item, "ccdaemon:8201", "ccdaemon", port=8201)
        elif "omnicompany.dashboard.app:app" in command:
            add_tree(item, "dashboard:8210", "dashboard", port=8210)

    services: dict[str, list[ProcessFact]] = {}
    for item in facts:
        if item.pid not in assigned and _is_dev_service(item):
            services.setdefault(_service_key(item), []).append(item)
    for key, roots in services.items():
        service_facts: list[ProcessFact] = []
        service_id = f"review-service:{hashlib.sha1(key.encode()).hexdigest()[:12]}"
        for root in roots:
            for pid in _descendants(root.pid, children):
                if pid in by_pid and pid not in assigned:
                    assigned[pid] = service_id
                    service_facts.append(by_pid[pid])
        if service_facts:
            groups.append(
                _group_summary(
                    service_id,
                    "review-dev-service",
                    service_facts,
                    service_key=key,
                    root_pids=sorted(root.pid for root in roots),
                    instances=len(roots),
                )
            )

    workspace_marker = workspace_root.lower().replace("/", "\\")
    remaining = [
        item
        for item in facts
        if item.pid not in assigned
        and (
            workspace_marker in item.command.lower().replace("/", "\\")
            or "omnicompany" in item.command.lower()
        )
    ]
    remaining_pids = {item.pid for item in remaining}
    remaining_by_pid = {item.pid: item for item in remaining}
    for item in remaining:
        if item.pid in assigned or item.ppid in remaining_pids:
            continue
        members = [
            by_pid[pid]
            for pid in _descendants(item.pid, children)
            if pid in remaining_by_pid and pid not in assigned
        ]
        if not members:
            continue
        group_id = f"workspace-task:{item.pid}"
        for member in members:
            assigned[member.pid] = group_id
        groups.append(
            _group_summary(
                group_id,
                "workspace-task",
                members,
                workspace_root=workspace_root,
                command=item.command,
            )
        )

    groups.sort(key=lambda item: (item["private_bytes"], item["handles"]), reverse=True)
    return groups, assigned


def _observation(
    kind: str,
    subject: str,
    severity: str,
    message: str,
    evidence: dict[str, Any],
    *,
    confirm_after: int = 2,
    confidence: str = "high",
) -> dict[str, Any]:
    return {
        "fingerprint": f"{kind}:{subject}",
        "kind": kind,
        "subject": subject,
        "severity": severity,
        "confidence": confidence,
        "message": message,
        "evidence": evidence,
        "confirm_after": confirm_after,
        "automatic_cleanup_allowed": False,
        "recommended_action": "inspect_owner_then_confirm_cleanup",
    }


def detect_observations(
    facts: list[ProcessFact],
    groups: list[dict[str, Any]],
    system: dict[str, Any],
    thresholds: Thresholds,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []

    def system_limit(
        kind: str,
        value: float,
        warning: float,
        critical: float,
        unit: str,
        *,
        confirm_after: int = 2,
    ) -> None:
        if value < warning:
            return
        severity = "critical" if value >= critical else "warning"
        observations.append(
            _observation(
                kind,
                "machine",
                severity,
                f"machine {kind} is {value:g}{unit}",
                {"value": value, "warning": warning, "critical": critical, "unit": unit},
                confirm_after=confirm_after,
            )
        )

    system_limit(
        "process-count",
        system["process_count"],
        thresholds.system_processes_warning,
        thresholds.system_processes_critical,
        "",
    )
    system_limit(
        "handle-count",
        system["handles"],
        thresholds.system_handles_warning,
        thresholds.system_handles_critical,
        "",
    )
    system_limit(
        "memory-percent",
        system["memory_percent"],
        thresholds.system_memory_warning_pct,
        thresholds.system_memory_critical_pct,
        "%",
    )
    if system["cpu_percent"] >= thresholds.system_cpu_warning_pct:
        observations.append(
            _observation(
                "cpu-percent",
                "machine",
                "warning",
                f"machine CPU is {system['cpu_percent']:g}%",
                {
                    "value": system["cpu_percent"],
                    "warning": thresholds.system_cpu_warning_pct,
                },
                confirm_after=3,
                confidence="medium",
            )
        )

    for group in groups:
        gid = str(group["id"])
        limits = (
            ("group-process-count", group["process_count"], thresholds.group_processes_warning),
            ("group-handles", group["handles"], thresholds.group_handles_warning),
            (
                "group-private-bytes",
                group["private_bytes"],
                thresholds.group_private_warning_bytes,
            ),
            ("group-cpu-percent", group["cpu_percent"], thresholds.group_cpu_warning_pct),
        )
        for kind, value, warning in limits:
            if value < warning:
                continue
            critical = (
                kind == "group-handles"
                and value >= thresholds.group_handles_critical
            ) or (
                kind == "group-private-bytes"
                and value >= thresholds.group_private_critical_bytes
            )
            observations.append(
                _observation(
                    kind,
                    gid,
                    "critical" if critical else "warning",
                    f"{gid} exceeds {kind} threshold",
                    {
                        "group_id": gid,
                        "role": group["role"],
                        "value": value,
                        "warning": warning,
                        "pids": group["pids"],
                    },
                    # 8 GiB 已是本模块定义的 critical 私有内存异常。它不需要再等
                    # 第二个一分钟采样才变成可消费告警；其它组指标保持原确认窗口。
                    confirm_after=(
                        1
                        if kind == "group-private-bytes" and critical
                        else 3
                        if kind == "group-cpu-percent"
                        else 2
                    ),
                    confidence="medium" if kind == "group-cpu-percent" else "high",
                )
            )
        instances = int(group.get("owner", {}).get("instances") or 0)
        if group["role"] == "review-dev-service" and instances > 1:
            observations.append(
                _observation(
                    "duplicate-dev-service",
                    str(group["owner"].get("service_key") or gid),
                    "warning",
                    f"{instances} development servers share one project/service key",
                    {
                        "group_id": gid,
                        "instances": instances,
                        "root_pids": group["owner"].get("root_pids", []),
                        "service_key": group["owner"].get("service_key"),
                    },
                )
            )

    pids = {item.pid for item in facts}
    monitored = {pid for group in groups for pid in group["pids"]}
    managed = {pid for group in groups if group["role"] == "agent-session" for pid in group["pids"]}
    for item in facts:
        if item.pid in monitored and item.handles >= thresholds.process_handles_warning:
            observations.append(
                _observation(
                    "high-process-handles",
                    f"{item.pid}:{int(item.create_time)}",
                    (
                        "critical"
                        if item.handles >= thresholds.process_handles_critical
                        else "warning"
                    ),
                    f"PID {item.pid} holds {item.handles} handles",
                    _process_summary(item),
                )
            )
        if (
            item.pid not in managed
            and _is_dev_service(item)
            and item.ppid not in pids
            and item.age_s >= thresholds.orphan_min_age_s
        ):
            observations.append(
                _observation(
                    "orphan-dev-service",
                    f"{item.pid}:{int(item.create_time)}",
                    "warning",
                    f"development/review service PID {item.pid} has no live parent",
                    _process_summary(item),
                    confirm_after=3,
                    confidence="medium",
                )
            )
    return observations


def detect_stale_compute_observations(
    facts: list[ProcessFact],
    policy: dict[str, Any],
    *,
    now: float,
) -> list[dict[str, Any]]:
    """Detect old, still-expensive test/debug commands outside agent sessions.

    Age is part of the proof, so a normal foreground run is not flagged merely for
    starting.  The detector only emits evidence and never terminates a process.
    """
    cfg = (policy or {}).get("stale_compute_detection") or {}
    if not cfg.get("enabled", False):
        return []

    patterns = tuple(str(item).casefold() for item in (cfg.get("command_patterns") or ()))
    if not patterns:
        return []
    min_age_s = float(cfg.get("min_age_minutes", float("inf"))) * 60.0
    min_cpu = float(cfg.get("min_cpu_percent", float("inf")))
    min_private_bytes = float(cfg.get("min_private_mb", float("inf"))) * 1024 * 1024
    severity = str(cfg.get("severity") or "critical")
    alert_kind = str(cfg.get("alert_kind") or "stale-workspace-compute")

    observations: list[dict[str, Any]] = []
    for fact in facts:
        command = fact.command.casefold()
        matched_pattern = next((item for item in patterns if item in command), None)
        age_s = max(0.0, now - fact.create_time)
        if (
            matched_pattern is None
            or age_s < min_age_s
            or (fact.cpu_percent < min_cpu and fact.private_bytes < min_private_bytes)
        ):
            continue
        evidence = {
            **_process_summary(fact),
            "matched_pattern": matched_pattern,
            "age_minutes": round(age_s / 60.0, 1),
            "min_age_minutes": min_age_s / 60.0,
            "min_cpu_percent": min_cpu,
            "min_private_mb": min_private_bytes / (1024 * 1024),
        }
        observations.append(
            _observation(
                alert_kind,
                f"{fact.pid}:{int(fact.create_time)}",
                severity,
                f"PID {fact.pid} has run {matched_pattern!r} for {age_s / 60.0:.1f}m "
                f"while using {fact.cpu_percent:.1f}% CPU / "
                f"{fact.private_bytes / (1024**3):.1f} GiB private memory",
                evidence,
                # The minimum age is already a durable confirmation window.
                confirm_after=1,
                confidence="high",
            )
        )
    return observations


def _notify_confirmed_resource_alerts(
    events: list[dict[str, Any]], policy: dict[str, Any]
) -> None:
    """Push one dashboard notice for newly-confirmed critical resource alerts."""
    cfg = (policy or {}).get("alert_consumption") or {}
    if not cfg.get("notify_on_critical", False):
        return
    confirmed = [
        event.get("alert") or {}
        for event in events
        if event.get("event") == "confirmed"
        and (event.get("alert") or {}).get("severity") == "critical"
    ]
    if not confirmed:
        return
    try:
        from omnicompany.packages.services.boss_sight.services import agent_notify

        # Keep the dashboard bell payload to stable alert metadata. Alert messages
        # can contain process-derived text, so never forward them or argv here.
        summaries = []
        for item in confirmed[:3]:
            kind = str(item.get("kind") or "resource-alert")
            evidence = item.get("evidence") or {}
            if kind in {"disk-free-space", "disk-consumption-rate"}:
                root = evidence.get("root")
                free = evidence.get("free_bytes")
                growth = evidence.get("growth_bytes_per_sec")
                space = f" free={int(free)}B" if isinstance(free, (int, float)) else ""
                rate = f" growth={float(growth) * 3600:.0f}B/hour" if isinstance(growth, (int, float)) else ""
                summaries.append(f"{kind} {root or ''}{space}{rate}".strip())
            else:
                summaries.append(kind)
        summary = "; ".join(summaries)
        agent_notify.push_notice(
            f"资源占用异常: {len(confirmed)} 项 critical",
            open_ref={"type": "project", "id": "perf-observatory"},
            body=summary,
        )
    except Exception:  # noqa: BLE001 - notification failure must not stop sampling
        logger.warning("critical resource alert notification failed", exc_info=True)


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _append_jsonl(path: Path, payload: Any, max_bytes: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size >= max_bytes:
        archive = path.with_suffix(path.suffix + ".1")
        os.replace(path, archive)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def _advance_alert_state(
    observations: list[dict[str, Any]],
    previous: dict[str, Any],
    *,
    now: float,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    old_active = previous.get("active") if isinstance(previous, dict) else {}
    if not isinstance(old_active, dict):
        old_active = {}
    active: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    visible: list[dict[str, Any]] = []

    for observation in observations:
        fingerprint = observation["fingerprint"]
        prior = old_active.get(fingerprint) if isinstance(old_active.get(fingerprint), dict) else {}
        consecutive = int(prior.get("consecutive") or 0) + 1
        confirm_after = int(observation.get("confirm_after") or 2)
        status = "confirmed" if consecutive >= confirm_after else "observed"
        record = {
            **observation,
            "status": status,
            "consecutive": consecutive,
            "first_seen_at": float(prior.get("first_seen_at") or now),
            "last_seen_at": now,
        }
        active[fingerprint] = record
        visible.append(record)
        if not prior or prior.get("status") != status:
            events.append({"event": status, "ts": now, "alert": record})

    for fingerprint, prior in old_active.items():
        if fingerprint in active or not isinstance(prior, dict):
            continue
        events.append(
            {
                "event": "resolved",
                "ts": now,
                "alert": {**prior, "status": "resolved", "resolved_at": now},
            }
        )

    visible.sort(
        key=lambda item: (
            item["status"] == "confirmed",
            item["severity"] == "critical",
            item["severity"] == "warning",
            item["consecutive"],
        ),
        reverse=True,
    )
    return {"schema": SCHEMA, "updated_at": now, "active": active}, events, visible


# ── PERF-OBSERVATORY 策略执行层 (priority_enforcer / runaway_idle_detection) ──
#
# 策略真源: config/perf/resource-policy.yaml (schema omni.resource-policy.v1)。
# priority_enforcer 唯一允许的自动动作是把已连续确认高 CPU 的组降为 BelowNormal
# 优先级(可逆, 绝不 kill/挂起); runaway_idle_detection 只产出证据型 alert, 走现有
# alerts 机制落盘, 清理仍需人工确认。两者都必须 fail-open: 任何异常只 warning,
# 不得影响采样主循环。

_RESOURCE_POLICY_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _load_resource_policy(path: Path) -> dict[str, Any]:
    """惰性加载 + mtime 缓存;解析失败时保留上次已知配置(格式错误不清空策略)。"""
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    cached = _RESOURCE_POLICY_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        logger.warning(
            "resource policy failed to parse from %s; keeping last-known config", path, exc_info=True
        )
        data = cached[1] if cached is not None else {}
    _RESOURCE_POLICY_CACHE[key] = (mtime, data)
    return data


def _deprioritize_pid(pid: int) -> None:
    """把单个 pid 的调度优先级降为 BelowNormal —— 唯一允许的自动动作,可逆,非 kill。"""
    if os.name != "nt":
        return
    import psutil

    psutil.Process(pid).nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)


def _normalize_interactive_pid(pid: int) -> None:
    """Keep the daemon/PTY control plane at normal scheduling priority."""
    if os.name != "nt":
        return
    import psutil

    psutil.Process(pid).nice(psutil.NORMAL_PRIORITY_CLASS)


def _ledger_append_remediation(payload: dict[str, Any]) -> None:
    from omnicompany.packages.services._core.ledger import LedgerEvent, append as _ledger_append

    event = LedgerEvent(
        type="perf.remediation",
        agent="resource_monitor.priority_enforcer",
        activity=(
            f"deprioritized {payload.get('group_id')} after sustained CPU "
            f"({payload.get('cpu_percent')}% over {payload.get('confirmation_samples')} samples)"
        ),
        outputs=[str(payload.get("group_id"))],
        meta=payload,
    )
    _ledger_append(event)


def apply_priority_enforcement(
    groups: list[dict[str, Any]],
    policy: dict[str, Any],
    state: dict[str, Any],
    *,
    now: float,
    set_priority: Any = _deprioritize_pid,
    ledger_append: Any = _ledger_append_remediation,
) -> dict[str, Any]:
    """按策略把持续高 CPU 的组降优先级;返回下一轮要持久化的 state。

    Fail-open: 单个 pid 降级失败或 ledger 留痕失败都只 warning,不中断其它组的处理,
    也不向上抛出(调用方的采样主循环不受影响)。
    """
    new_state: dict[str, Any] = {"schema": "omni.priority-enforcement.v1", "updated_at": now}
    cfg = (policy or {}).get("priority_enforcer") or {}
    if not cfg.get("enabled", False) or cfg.get("mode") != "deprioritize_only":
        new_state["streaks"] = {}
        new_state["deprioritized"] = {}
        return new_state

    protected_prefixes = tuple(str(p) for p in (cfg.get("protected_group_prefixes") or ()))
    sustained_cpu = float(cfg.get("sustained_cpu_percent", float("inf")))
    confirmation_samples = max(1, int(cfg.get("confirmation_samples", 3)))

    streaks = dict((state or {}).get("streaks") or {})
    deprioritized = dict((state or {}).get("deprioritized") or {})
    seen_ids: set[str] = set()

    for group in groups:
        gid = str(group.get("id") or "")
        if not gid:
            continue
        seen_ids.add(gid)
        if gid.startswith(protected_prefixes):
            streaks.pop(gid, None)
            continue
        cpu = float(group.get("cpu_percent") or 0.0)
        if cpu >= sustained_cpu:
            streaks[gid] = int(streaks.get(gid, 0)) + 1
        else:
            streaks[gid] = 0
            continue
        if streaks[gid] < confirmation_samples:
            continue

        candidates = group.get("deprioritization_pids")
        if candidates is None:
            # Compatibility for callers/tests supplying the older group schema.
            candidates = group.get("pids") or []
        previous = deprioritized.get(gid) or {}
        previous_pids = {int(pid) for pid in previous.get("pids") or []}
        target_pids = [int(pid) for pid in candidates if int(pid) not in previous_pids]
        if not target_pids:
            continue
        applied_pids: list[int] = []
        for pid in target_pids:
            try:
                set_priority(pid)
                applied_pids.append(pid)
            except Exception:
                logger.warning(
                    "priority_enforcer failed to deprioritize pid %s in group %s", pid, gid, exc_info=True
                )
        combined_pids = sorted(previous_pids | set(applied_pids))
        deprioritized[gid] = {"pids": combined_pids, "at": now, "cpu_percent": cpu}
        if not applied_pids:
            continue
        try:
            ledger_append(
                {
                    "group_id": gid,
                    "action": "deprioritize",
                    "role": group.get("role"),
                    "pids": applied_pids,
                    "cpu_percent": cpu,
                    "sustained_cpu_percent": sustained_cpu,
                    "confirmation_samples": confirmation_samples,
                }
            )
        except Exception:
            logger.warning("priority_enforcer ledger append failed for group %s", gid, exc_info=True)

    # 组从这轮采样彻底消失(进程退出)才清理记忆,避免状态无限增长;attribute_processes
    # 的分组以存活进程树为准,不会出现"短暂缺席又回来"的抖动。
    for gid in list(streaks):
        if gid not in seen_ids:
            streaks.pop(gid, None)
    for gid in list(deprioritized):
        if gid not in seen_ids:
            deprioritized.pop(gid, None)

    new_state["streaks"] = streaks
    new_state["deprioritized"] = deprioritized
    return new_state


def _read_session_record(sessions_path: Path, session_id: str) -> dict[str, Any] | None:
    """从 cc_sessions.json 里只取一个 session 的记录,不解析整份文件。

    data/cc_sessions.json 是按 session id 做键的单个 JSON 对象,可能积到数百条记录、
    个别记录还带很深的 history_summary/event_history;这里按文本定位到目标键,再做一次
    括号配平截取,只对命中的子串 json.loads,避免为了一个字段构建全量嵌套对象图。
    """
    try:
        text = sessions_path.read_text(encoding="utf-8")
    except OSError:
        return None
    marker = f'"{session_id}"'
    idx = text.find(marker)
    if idx < 0:
        return None
    brace_start = text.find("{", idx)
    if brace_start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    end = None
    for i in range(brace_start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        return None
    try:
        value = json.loads(text[brace_start:end])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _group_facts(group: dict[str, Any], pid_to_fact: dict[int, ProcessFact]) -> list[ProcessFact]:
    return [pid_to_fact[pid] for pid in group.get("pids") or [] if pid in pid_to_fact]


def _find_heavy_child(facts: list[ProcessFact], cfg: dict[str, Any]) -> ProcessFact | None:
    names = {str(n).lower() for n in (cfg.get("heavy_child_names") or [])}
    cpu_threshold = float(cfg.get("heavy_child_cpu_percent", float("inf")))
    rss_threshold_mb = float(cfg.get("heavy_child_rss_mb", float("inf")))
    for fact in facts:
        if fact.name.lower() in names:
            return fact
        if fact.cpu_percent > cpu_threshold:
            return fact
        if (fact.rss_bytes / (1024 * 1024)) > rss_threshold_mb:
            return fact
    return None


def _session_idle_hours(
    record: dict[str, Any] | None, facts: list[ProcessFact], now: float
) -> tuple[float, str]:
    """判定依据优先级: cc_sessions.json 记录里的 last_submit_at(最贴合"最后活动"字面
    含义的字段:最近一次用户提交,404 条 pty 记录里 246 条有值、最新鲜的样本仅几分钟前)
    → ended_at(会话已结束的确定性信号) → last_activity_at(覆盖率很低,491 条里仅 7 条
    非空且多为测试夹具值,但存在则最直接) → started_at(仅有创建时间时的次优信号)。
    四者都拿不到时兜底为组内进程里最年轻的 create_time(最近一次有进程活动的近似信号),
    并在返回的判定依据字符串里如实标注,供 evidence 说明这是兜底路径。
    """
    if isinstance(record, dict):
        for field_name in ("last_submit_at", "ended_at", "last_activity_at", "started_at"):
            value = record.get(field_name)
            if isinstance(value, (int, float)) and value > 0:
                return max(0.0, (now - float(value)) / 3600.0), field_name
    if facts:
        youngest_create_time = max(fact.create_time for fact in facts)
        return max(0.0, (now - youngest_create_time) / 3600.0), "youngest_child_create_time_fallback"
    return 0.0, "unknown"


def detect_runaway_idle_observations(
    groups: list[dict[str, Any]],
    pid_to_fact: dict[int, ProcessFact],
    policy: dict[str, Any],
    *,
    now: float,
    session_lookup: Any = None,
) -> list[dict[str, Any]]:
    """产出证据型 alert(kind=策略里的 alert_kind),不做任何自动处置。"""
    cfg = (policy or {}).get("runaway_idle_detection") or {}
    if not cfg.get("enabled", False):
        return []
    idle_threshold_h = float(cfg.get("idle_hours_threshold", float("inf")))
    alert_kind = str(cfg.get("alert_kind") or "runaway-idle-agent-session")
    severity = str(cfg.get("severity") or "critical")

    observations: list[dict[str, Any]] = []
    for group in groups:
        if group.get("role") != "agent-session":
            continue
        owner = group.get("owner") or {}
        session_id = owner.get("session_id")
        facts = _group_facts(group, pid_to_fact)
        heavy = _find_heavy_child(facts, cfg)
        if heavy is None:
            continue

        record = None
        if session_lookup is not None and session_id:
            try:
                record = session_lookup(session_id)
            except Exception:
                logger.warning(
                    "runaway_idle_detection session lookup failed for %s", session_id, exc_info=True
                )
        idle_hours, idle_basis = _session_idle_hours(record, facts, now)
        if idle_hours < idle_threshold_h:
            continue

        gid = str(group.get("id") or session_id or "unknown")
        started_at = (record or {}).get("started_at")
        if not isinstance(started_at, (int, float)):
            started_at = "unknown"
        evidence = {
            "provider": owner.get("provider") or (record or {}).get("provider") or "unknown",
            "session_id": session_id or "unknown",
            "session_name": (
                (record or {}).get("provider_title")
                or (record or {}).get("claude_session_id")
                or "unknown"
            ),
            "started_at": started_at,
            "active_plan": (record or {}).get("active_plan") or "unknown",
            "root_pid": owner.get("root_pid", "unknown"),
            "idle_hours": round(idle_hours, 2),
            "idle_hours_threshold": idle_threshold_h,
            "idle_basis": idle_basis,
            "heavy_child_pid": heavy.pid,
            "heavy_child_name": heavy.name,
            "heavy_child_cpu_percent": round(heavy.cpu_percent, 1),
            "heavy_child_rss_mb": round(heavy.rss_bytes / (1024 * 1024), 1),
        }
        observations.append(
            _observation(
                alert_kind,
                gid,
                severity,
                f"{gid} idle {idle_hours:.1f}h (threshold {idle_threshold_h}h) "
                f"with heavy child {heavy.name} (pid {heavy.pid})",
                evidence,
                confirm_after=1,
                confidence="medium" if idle_basis == "youngest_child_create_time_fallback" else "high",
            )
        )
    return observations


class ResourceMonitor:
    def __init__(
        self,
        *,
        root: Path | None = None,
        interval_s: float = DEFAULT_INTERVAL_S,
        thresholds: Thresholds | None = None,
    ) -> None:
        self.root = (root or omni_workspace_root()).resolve()
        self.data_dir = self.root / "data" / "services" / "resource_monitor"
        self.interval_s = max(5.0, float(interval_s))
        self.thresholds = thresholds or Thresholds.from_env()
        self._cpu_baseline: dict[int, tuple[float, float]] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._worker_process: Any | None = None
        self._worker_stop: Any | None = None
        self._worker_restart_count = 0
        self.last_error: str | None = None
        self.last_sample_at: float | None = None

    @property
    def latest_path(self) -> Path:
        return self.data_dir / "latest.json"

    @property
    def worker_status_path(self) -> Path:
        return self.data_dir / "worker_status.json"

    def _collect(self) -> tuple[list[ProcessFact], dict[str, Any]]:
        try:
            import psutil
        except ImportError as exc:  # pragma: no cover - deployment guard
            raise RuntimeError(
                "resource monitoring requires the dashboard optional dependency 'psutil'"
            ) from exc

        now_mono = time.monotonic()
        next_baseline: dict[int, tuple[float, float]] = {}
        skeletons = _windows_process_skeletons()
        if not skeletons:  # non-Windows fallback
            skeletons = [
                {
                    "pid": proc.pid,
                    "ppid": proc.ppid(),
                    "name": proc.name(),
                    "threads": 0,
                }
                for proc in psutil.process_iter()
            ]
        facts_by_pid = {
            item["pid"]: ProcessFact(
                pid=item["pid"],
                ppid=item["ppid"],
                name=item["name"],
                command="",
                create_time=time.time(),
                rss_bytes=0,
                private_bytes=0,
                handles=0,
                threads=item["threads"],
            )
            for item in skeletons
            if item["pid"]
        }
        candidate_names = {
            "bash.exe",
            "claude.exe",
            "cmd.exe",
            "codex.exe",
            "esbuild.exe",
            "grep.exe",
            "java.exe",
            "kimi.exe",
            "node.exe",
            "opencode.exe",
            "powershell.exe",
            "pwsh.exe",
            "python.exe",
            "pythonw.exe",
            "tail.exe",
            "uvicorn.exe",
        }
        enriched_pids: set[int] = set()

        def enrich(pid: int, *, include_command: bool = True) -> None:
            base = facts_by_pid.get(pid)
            if base is None:
                return
            try:
                proc = psutil.Process(pid)
                memory = proc.memory_info()
                cpu_times = proc.cpu_times()
                cpu_seconds = float(cpu_times.user + cpu_times.system)
                previous = self._cpu_baseline.get(pid)
                cpu_percent = 0.0
                if previous and now_mono > previous[1]:
                    cpu_percent = max(
                        0.0,
                        (cpu_seconds - previous[0]) / (now_mono - previous[1]) * 100.0,
                    )
                next_baseline[pid] = (cpu_seconds, now_mono)
                command = base.command
                if include_command:
                    command = _redact_command(proc.cmdline())
                cwd = ""
                candidate = command.lower()
                if any(
                    marker in candidate
                    for marker in ("vite", "http.server", "server.mjs", "serve.mjs")
                ):
                    try:
                        cwd = proc.cwd()
                    except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                        pass
                facts_by_pid[pid] = ProcessFact(
                    pid=pid,
                    ppid=base.ppid,
                    name=base.name,
                    command=command,
                    create_time=float(proc.create_time()),
                    rss_bytes=int(getattr(memory, "rss", 0) or 0),
                    private_bytes=int(
                        getattr(memory, "private", getattr(memory, "rss", 0)) or 0
                    ),
                    handles=int(proc.num_handles()) if os.name == "nt" else 0,
                    threads=base.threads,
                    cpu_seconds=cpu_seconds,
                    cpu_percent=cpu_percent,
                    cwd=cwd,
                )
                enriched_pids.add(pid)
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError):
                return

        for fact in list(facts_by_pid.values()):
            if fact.name.lower() in candidate_names:
                enrich(fact.pid)

        # Enrich otherwise-uninteresting executables only when they descend
        # from a positively identified managed/workspace root.
        children: dict[int, list[int]] = {}
        for fact in facts_by_pid.values():
            children.setdefault(fact.ppid, []).append(fact.pid)
        workspace_marker = str(self.root.parent).lower().replace("/", "\\")
        roots = [
            fact.pid
            for fact in facts_by_pid.values()
            if _session_id(fact)
            or "omnicompany.dashboard.ccdaemon.main:app" in fact.command.lower()
            or "omnicompany.dashboard.app:app" in fact.command.lower()
            or _is_dev_service(fact)
            or workspace_marker in fact.command.lower().replace("/", "\\")
            or "omnicompany" in fact.command.lower()
        ]
        managed_pids: set[int] = set()
        for root_pid in roots:
            managed_pids.update(_descendants(root_pid, children))
        for pid in managed_pids:
            if pid not in enriched_pids:
                enrich(pid, include_command=False)

        facts = list(facts_by_pid.values())
        self._cpu_baseline = next_baseline
        virtual_memory = psutil.virtual_memory()
        performance = _windows_performance_counts()
        sampled = [facts_by_pid[pid] for pid in enriched_pids if pid in facts_by_pid]
        system = {
            "process_count": performance.get("process_count", len(facts)),
            "threads": performance.get("threads", sum(item.threads for item in facts)),
            "handles": performance.get("handles", sum(item.handles for item in facts)),
            "rss_bytes": sum(item.rss_bytes for item in sampled),
            "private_bytes": sum(item.private_bytes for item in sampled),
            "resource_coverage_processes": len(sampled),
            "memory_percent": round(float(virtual_memory.percent), 1),
            "cpu_percent": round(float(psutil.cpu_percent(interval=0.05)), 1),
        }
        system.update(_collect_disk_usage())
        return facts, system

    @property
    def resource_policy_path(self) -> Path:
        return self.root / "config" / "perf" / "resource-policy.yaml"

    @property
    def priority_state_path(self) -> Path:
        return self.data_dir / "priority_enforcement_state.json"

    def sample(self, *, persist: bool = True, enforce_policy: bool = False) -> dict[str, Any]:
        started = time.monotonic()
        now = time.time()
        facts, system = self._collect()
        previous_state: dict[str, Any] = {}
        state_path = self.data_dir / "state.json"
        if persist and state_path.is_file():
            try:
                previous_state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                previous_state = {}
        try:
            _add_disk_growth(system, previous_state.get("disk_state") or {})
        except Exception:
            logger.warning("disk growth calculation failed; keeping rates unknown", exc_info=True)
            for volume in system.get("disks") or []:
                volume["growth_bytes_per_sec"] = None
                volume["growth_interval_seconds"] = None
        groups, assigned = attribute_processes(facts, workspace_root=str(self.root.parent))
        observations = detect_observations(facts, groups, system, self.thresholds)

        try:
            policy = _load_resource_policy(self.resource_policy_path)
        except Exception:
            logger.warning(
                "failed to load resource policy; policy-driven features disabled this cycle",
                exc_info=True,
            )
            policy = {}

        try:
            observations.extend(_disk_observations(system, policy))
        except Exception:
            logger.warning("disk alert detection failed; skipping this cycle", exc_info=True)

        try:
            pid_to_fact = {fact.pid: fact for fact in facts}
            runaway_observations = detect_runaway_idle_observations(
                groups,
                pid_to_fact,
                policy,
                now=now,
                session_lookup=lambda sid: _read_session_record(
                    self.root / "data" / "cc_sessions.json", sid
                ),
            )
        except Exception:
            logger.warning("runaway_idle_detection failed; skipping this cycle", exc_info=True)
            runaway_observations = []
        observations.extend(runaway_observations)

        try:
            observations.extend(detect_stale_compute_observations(facts, policy, now=now))
        except Exception:
            logger.warning("stale_compute_detection failed; skipping this cycle", exc_info=True)

        # priority_enforcer 会真的降低进程优先级, 只在专用后台采样循环里生效
        # (enforce_policy=True, 见 _resource_monitor_worker_main); CLI 的
        # `omni guardian resources` 走默认参数, 保持它"只读, 不修改任何进程"的既有承诺。
        if enforce_policy and persist:
            try:
                priority_state: dict[str, Any] = {}
                if self.priority_state_path.is_file():
                    try:
                        priority_state = json.loads(
                            self.priority_state_path.read_text(encoding="utf-8")
                        )
                    except (OSError, json.JSONDecodeError):
                        priority_state = {}
                priority_state = apply_priority_enforcement(groups, policy, priority_state, now=now)
                _atomic_json(self.priority_state_path, priority_state)
            except Exception:
                logger.warning("priority_enforcer failed; skipping this cycle", exc_info=True)

        state, events, alerts = _advance_alert_state(observations, previous_state, now=now)
        state, events, alerts = _preserve_unmeasured_disk_alerts(
            state, events, alerts, previous_state, system
        )
        state["disk_state"] = {"disks": system.get("disks") or []}

        external = [
            item
            for item in facts
            if item.pid not in assigned
            and (
                item.cpu_percent >= 50.0
                or item.private_bytes >= 1024**3
                or item.handles >= self.thresholds.process_handles_warning
            )
        ]
        external.sort(
            key=lambda item: (item.cpu_percent, item.private_bytes, item.handles),
            reverse=True,
        )
        snapshot = {
            "schema": SCHEMA,
            "sampled_at": now,
            "duration_ms": round((time.monotonic() - started) * 1000, 1),
            "read_only": True,
            "automatic_cleanup_allowed": False,
            "system": system,
            "groups": groups,
            "alerts": alerts,
            "alert_counts": {
                "observed": sum(item["status"] == "observed" for item in alerts),
                "confirmed": sum(item["status"] == "confirmed" for item in alerts),
                "critical": sum(
                    item["status"] == "confirmed" and item["severity"] == "critical"
                    for item in alerts
                ),
                "warning": sum(
                    item["status"] == "confirmed" and item["severity"] == "warning"
                    for item in alerts
                ),
            },
            "external_pressure": [_process_summary(item) for item in external[:12]],
            "policy": {
                "cleanup": "human_confirmation_required",
                "first_hit": "observed",
                "default_confirmation_samples": 2,
                "cpu_confirmation_samples": 3,
                "interval_s": self.interval_s,
            },
        }
        if persist:
            _atomic_json(self.latest_path, snapshot)
            _atomic_json(state_path, state)
            _append_jsonl(
                self.data_dir / "history.jsonl",
                {
                    "schema": SCHEMA,
                    "sampled_at": now,
                    "duration_ms": snapshot["duration_ms"],
                    "system": system,
                    "alert_counts": snapshot["alert_counts"],
                    "groups": [
                        {
                            key: group[key]
                            for key in (
                                "id",
                                "role",
                                "owner",
                                "process_count",
                                "cpu_percent",
                                "private_bytes",
                                "handles",
                            )
                        }
                        for group in groups
                    ],
                },
                HISTORY_MAX_BYTES,
            )
            for event in events:
                _append_jsonl(
                    self.data_dir / "alerts.jsonl",
                    event,
                    ALERTS_MAX_BYTES,
                )
            _notify_confirmed_resource_alerts(events, policy)
        self.last_sample_at = now
        self.last_error = None
        return snapshot

    def read_latest(self) -> dict[str, Any]:
        if not self.latest_path.is_file():
            return {
                "schema": SCHEMA,
                "available": False,
                "read_only": True,
                "automatic_cleanup_allowed": False,
                "last_error": self.last_error,
            }
        try:
            value = json.loads(self.latest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "schema": SCHEMA,
                "available": False,
                "read_only": True,
                "automatic_cleanup_allowed": False,
                "last_error": f"{type(exc).__name__}: {exc}",
            }
        value["available"] = True
        return value

    def _read_worker_status(self) -> dict[str, Any]:
        try:
            value = json.loads(self.worker_status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def status(self) -> dict[str, Any]:
        latest = self.read_latest()
        worker = self._worker_process
        worker_running = bool(worker and worker.is_alive())
        worker_status = self._read_worker_status()
        return {
            "running": bool(self._task and not self._task.done() and worker_running),
            "execution_mode": "dedicated_process",
            "worker_pid": worker.pid if worker_running else None,
            "worker_restart_count": self._worker_restart_count,
            "interval_s": self.interval_s,
            "last_sample_at": (
                latest.get("sampled_at")
                or worker_status.get("last_sample_at")
                or self.last_sample_at
            ),
            "last_error": self.last_error or worker_status.get("last_error"),
            "alert_counts": latest.get("alert_counts") or {},
            "automatic_cleanup_allowed": False,
        }

    def _start_worker_process(self) -> None:
        context = multiprocessing.get_context("spawn")
        parent_pid = os.getpid()
        worker_stop = context.Event()
        worker = context.Process(
            target=_resource_monitor_worker_main,
            args=(
                str(self.root),
                self.interval_s,
                asdict(self.thresholds),
                worker_stop,
                parent_pid,
            ),
            name="cc-resource-monitor-worker",
            daemon=True,
        )
        worker.start()
        self._worker_stop = worker_stop
        self._worker_process = worker

    async def _stop_worker_process(self) -> None:
        worker = self._worker_process
        worker_stop = self._worker_stop
        self._worker_process = None
        self._worker_stop = None
        if worker is None:
            return
        if worker_stop is not None:
            worker_stop.set()
        await asyncio.to_thread(worker.join, 3.0)
        if worker.is_alive():
            worker.terminate()
            await asyncio.to_thread(worker.join, 3.0)
        try:
            worker.close()
        except (OSError, ValueError):
            pass

    async def run(self) -> None:
        self._stop.clear()
        try:
            while not self._stop.is_set():
                worker = self._worker_process
                if worker is None:
                    self._start_worker_process()
                    worker = self._worker_process
                elif not worker.is_alive():
                    exitcode = worker.exitcode
                    self.last_error = f"resource monitor worker exited with code {exitcode}"
                    self._worker_restart_count += 1
                    try:
                        worker.close()
                    except (OSError, ValueError):
                        pass
                    self._worker_process = None
                    self._worker_stop = None
                    logger.error("%s; restarting", self.last_error)
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        pass
                    continue
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
        finally:
            await self._stop_worker_process()

    def start(self) -> asyncio.Task[None]:
        if self._task and not self._task.done():
            return self._task
        try:
            # A daemon restarted from an already-deprioritized PTY inherits that
            # priority on Windows.  Explicitly break that inheritance before it
            # becomes the control plane for every terminal.
            _normalize_interactive_pid(os.getpid())
        except Exception:
            logger.warning("failed to normalize ccdaemon priority", exc_info=True)
        self._task = asyncio.create_task(self.run(), name="cc-resource-monitor-supervisor")
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._task = None


def _worker_parent_is_alive(expected_parent_pid: int) -> bool:
    """Return whether the ccdaemon process that spawned this worker still exists."""
    if expected_parent_pid <= 0:
        return False
    if expected_parent_pid == os.getpid():
        return True

    parent = multiprocessing.parent_process()
    if parent is not None and parent.pid == expected_parent_pid:
        try:
            return parent.is_alive()
        except (AssertionError, OSError, ValueError):
            return False

    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(
            process_query_limited_information, False, expected_parent_pid
        )
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and (
                exit_code.value == still_active
            )
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(expected_parent_pid, 0)
    except OSError:
        return False
    return True


def _resource_monitor_worker_main(
    root: str,
    interval_s: float,
    thresholds: dict[str, Any],
    stop_event: Any,
    parent_pid: int,
) -> None:
    """Run expensive psutil attribution outside the ccdaemon interpreter/GIL."""
    if os.name == "nt":
        # A ccdaemon that was already running when the no-window policy was
        # upgraded can still spawn this worker with its old creation flags.
        # Hide that transitional console immediately; after the next natural
        # daemon restart _subprocess_hide prevents it from being created at all.
        try:
            import ctypes

            console = ctypes.windll.kernel32.GetConsoleWindow()
            if console:
                ctypes.windll.user32.ShowWindow(console, 0)
        except Exception:
            logger.debug("failed to hide resource monitor worker console", exc_info=True)
    try:
        # The diagnostic worker must yield to the interactive control plane.
        _deprioritize_pid(os.getpid())
    except Exception:
        logger.warning("failed to deprioritize resource monitor worker", exc_info=True)
    monitor = ResourceMonitor(
        root=Path(root),
        interval_s=interval_s,
        thresholds=Thresholds(**thresholds),
    )
    status_path = monitor.worker_status_path
    last_sample_at: float | None = None
    last_error: str | None = None

    def write_status(*, running: bool) -> None:
        try:
            _atomic_json(
                status_path,
                {
                    "schema": SCHEMA,
                    "running": running,
                    "pid": os.getpid(),
                    "parent_pid": parent_pid,
                    "last_sample_at": last_sample_at,
                    "last_error": last_error,
                    "updated_at": time.time(),
                },
            )
        except OSError:
            pass

    write_status(running=True)
    try:
        while not stop_event.is_set() and _worker_parent_is_alive(parent_pid):
            try:
                snapshot = monitor.sample(enforce_policy=True)
                last_sample_at = float(snapshot.get("sampled_at") or time.time())
                last_error = None
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("resource monitor worker sample failed")
            write_status(running=True)
            wait_until = time.monotonic() + interval_s
            while not stop_event.is_set() and time.monotonic() < wait_until:
                if not _worker_parent_is_alive(parent_pid):
                    return
                stop_event.wait(min(1.0, max(0.0, wait_until - time.monotonic())))
    finally:
        write_status(running=False)


_MONITOR: ResourceMonitor | None = None


def get_resource_monitor() -> ResourceMonitor:
    global _MONITOR
    if _MONITOR is None:
        _MONITOR = ResourceMonitor()
    return _MONITOR


def _read_tail_lines(path: Path, limit: int, *, chunk_bytes: int = 64 * 1024) -> list[str]:
    """Read only enough bytes from the end of a JSONL file for ``limit`` lines."""
    wanted = max(1, min(limit, 500))
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            position = stream.tell()
            chunks: list[bytes] = []
            newline_count = 0
            while position > 0 and newline_count <= wanted:
                size = min(chunk_bytes, position)
                position -= size
                stream.seek(position)
                chunk = stream.read(size)
                chunks.append(chunk)
                newline_count += chunk.count(b"\n")
    except OSError:
        return []
    return b"".join(reversed(chunks)).decode("utf-8", errors="replace").splitlines()[-wanted:]


def read_jsonl_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    values: list[dict[str, Any]] = []
    for line in _read_tail_lines(path, limit):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            values.append(value)
    return values


__all__ = [
    "ProcessFact",
    "ResourceMonitor",
    "Thresholds",
    "apply_priority_enforcement",
    "attribute_processes",
    "detect_observations",
    "detect_runaway_idle_observations",
    "detect_stale_compute_observations",
    "get_resource_monitor",
    "read_jsonl_tail",
]
