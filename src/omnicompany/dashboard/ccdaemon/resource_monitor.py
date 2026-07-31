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
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from omnicompany.core.config import omni_workspace_root

logger = logging.getLogger(__name__)

SCHEMA = "omni.resource-monitor.v1"
DEFAULT_INTERVAL_S = float(os.environ.get("OMNI_RESOURCE_MONITOR_INTERVAL_S") or 60)
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
    if "codex" in command:
        return "codex"
    if "claude" in command:
        return "claude_code"
    if "opencode" in command:
        return "opencode"
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
                    confirm_after=3 if kind == "group-cpu-percent" else 2,
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
        self.last_error: str | None = None
        self.last_sample_at: float | None = None

    @property
    def latest_path(self) -> Path:
        return self.data_dir / "latest.json"

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
        return facts, system

    def sample(self, *, persist: bool = True) -> dict[str, Any]:
        started = time.monotonic()
        now = time.time()
        facts, system = self._collect()
        groups, assigned = attribute_processes(facts, workspace_root=str(self.root.parent))
        observations = detect_observations(facts, groups, system, self.thresholds)

        previous_state: dict[str, Any] = {}
        state_path = self.data_dir / "state.json"
        if persist and state_path.is_file():
            try:
                previous_state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                previous_state = {}
        state, events, alerts = _advance_alert_state(observations, previous_state, now=now)

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

    def status(self) -> dict[str, Any]:
        latest = self.read_latest()
        return {
            "running": bool(self._task and not self._task.done()),
            "interval_s": self.interval_s,
            "last_sample_at": latest.get("sampled_at") or self.last_sample_at,
            "last_error": self.last_error or latest.get("last_error"),
            "alert_counts": latest.get("alert_counts") or {},
            "automatic_cleanup_allowed": False,
        }

    async def run(self) -> None:
        self._stop.clear()
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self.sample)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("resource monitor sample failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_s)
            except asyncio.TimeoutError:
                pass

    def start(self) -> asyncio.Task[None]:
        if self._task and not self._task.done():
            return self._task
        self._task = asyncio.create_task(self.run(), name="cc-resource-monitor")
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._task = None


_MONITOR: ResourceMonitor | None = None


def get_resource_monitor() -> ResourceMonitor:
    global _MONITOR
    if _MONITOR is None:
        _MONITOR = ResourceMonitor()
    return _MONITOR


def read_jsonl_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    values: list[dict[str, Any]] = []
    for line in lines[-max(1, min(limit, 500)) :]:
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
    "attribute_processes",
    "detect_observations",
    "get_resource_monitor",
    "read_jsonl_tail",
]
