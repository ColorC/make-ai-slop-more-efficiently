"""Passive Windows foreground-change attribution for focus-stealing incidents."""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from pathlib import Path
from typing import Any

import psutil

from omnicompany.core.config import omni_workspace_root

_CONSOLE_NAMES = {
    "cmd.exe", "powershell.exe", "pwsh.exe", "conhost.exe",
    "openconsole.exe", "windowsterminal.exe",
}
_BROWSER_NAMES = {"chrome.exe", "msedge.exe", "firefox.exe", "chromium.exe"}
_FOCUS_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cc-focus")


def _command_signature(process: psutil.Process) -> list[str]:
    """Keep code-origin hints while dropping command bodies and user data."""

    try:
        argv = process.cmdline()
    except (psutil.Error, OSError):
        return []
    out: list[str] = []
    skip_next = False
    for index, raw in enumerate(argv[:16]):
        if skip_next:
            skip_next = False
            continue
        value = str(raw)
        lowered = value.lower()
        if lowered in {"-encodedcommand", "-command", "-c"}:
            out.append(value)
            skip_next = True
            continue
        if lowered == "-file" and index + 1 < len(argv):
            out.extend([value, Path(str(argv[index + 1])).name[:160]])
            skip_next = True
            continue
        if lowered == "-m" and index + 1 < len(argv):
            out.extend([value, str(argv[index + 1])[:160]])
            skip_next = True
            continue
        if index == 0 or any(lowered.endswith(suffix) for suffix in (
            ".py", ".ps1", ".psm1", ".js", ".mjs", ".cjs", ".ts", ".tsx",
            ".cmd", ".bat", ".exe",
        )):
            out.append(Path(value).name[:160])
        elif value.startswith("-"):
            out.append(value[:80])
        if len(out) >= 8:
            break
    return out


def _owner_from_resource_snapshot(pid: int, path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for group in payload.get("groups") or []:
        if pid in (group.get("pids") or []):
            return {
                "group_id": group.get("id"),
                "role": group.get("role"),
                "owner": group.get("owner") or {},
            }
    return None


class FocusMonitor:
    """Sample one Win32 handle twice a second; write only relevant changes."""

    def __init__(self, *, interval_s: float = 0.5, root: Path | None = None) -> None:
        self.interval_s = max(0.25, float(interval_s))
        self.root = root or omni_workspace_root()
        self.log_path = self.root / "data" / "services" / "focus_monitor" / "events.jsonl"
        self.resource_snapshot_path = (
            self.root / "data" / "services" / "resource_monitor" / "latest.json"
        )
        self.events: deque[dict[str, Any]] = deque(maxlen=100)
        self._last_hwnd = 0
        self._task: asyncio.Task[None] | None = None
        self._owner_index: dict[int, dict[str, Any]] = {}
        self._owner_index_refresh_at = 0.0
        self.started_at = time.time()

    def _owner_for_pid(self, pid: int) -> dict[str, Any] | None:
        """Cache the resource join so ordinary focus switching stays cheap."""

        now = time.monotonic()
        if now >= self._owner_index_refresh_at:
            try:
                payload = json.loads(self.resource_snapshot_path.read_text(encoding="utf-8"))
                owner_index: dict[int, dict[str, Any]] = {}
                for group in payload.get("groups") or []:
                    attribution = {
                        "group_id": group.get("id"),
                        "role": group.get("role"),
                        "owner": group.get("owner") or {},
                    }
                    for group_pid in group.get("pids") or []:
                        owner_index[int(group_pid)] = attribution
                self._owner_index = owner_index
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            self._owner_index_refresh_at = now + 10.0
        return self._owner_index.get(pid)

    @staticmethod
    def _foreground() -> tuple[int, int, str]:
        if os.name != "nt":
            return 0, 0, ""
        user32 = ctypes.windll.user32
        hwnd = int(user32.GetForegroundWindow() or 0)
        if not hwnd:
            return 0, 0, ""
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        length = int(user32.GetWindowTextLengthW(hwnd) or 0)
        buffer = ctypes.create_unicode_buffer(min(max(length + 1, 1), 512))
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return hwnd, int(pid.value), buffer.value[:500]

    def _record(self, hwnd: int, pid: int, title: str) -> None:
        try:
            process = psutil.Process(pid)
            name = process.name().lower()
        except (psutil.Error, OSError):
            return
        owner = self._owner_for_pid(pid)
        if name not in _CONSOLE_NAMES and name not in _BROWSER_NAMES and owner is None:
            return
        ancestry: list[dict[str, Any]] = []
        current = process
        for _ in range(6):
            try:
                ancestry.append({
                    "pid": current.pid,
                    "name": current.name(),
                    "command_signature": _command_signature(current),
                })
                current = current.parent()
                if current is None:
                    break
            except (psutil.Error, OSError):
                break
        event = {
            "schema": "omni.focus-event.v1",
            "ts": time.time(),
            "category": (
                "console" if name in _CONSOLE_NAMES
                else "browser" if name in _BROWSER_NAMES
                else "managed-app"
            ),
            "hwnd": hwnd,
            "pid": pid,
            "process_name": name,
            "title": title,
            "owner": owner,
            "ancestry": ancestry,
        }
        self.events.append(event)
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            if self.log_path.exists() and self.log_path.stat().st_size >= 5 * 1024 * 1024:
                os.replace(self.log_path, self.log_path.with_suffix(".jsonl.1"))
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError:
            pass

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            hwnd, pid, title = self._foreground()
            if hwnd and hwnd != self._last_hwnd:
                self._last_hwnd = hwnd
                # psutil ancestry/cmdline and the append-only attribution log
                # may touch protected processes or a contended file. Never
                # let that observational path pause PTY/WebSocket scheduling.
                await loop.run_in_executor(
                    _FOCUS_EXECUTOR,
                    self._record,
                    hwnd,
                    pid,
                    title,
                )
            await asyncio.sleep(self.interval_s)

    def start(self) -> asyncio.Task[None] | None:
        if os.name != "nt":
            return None
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="cc-focus-monitor")
        return self._task

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema": "omni.focus-monitor.v1",
            "sampled_at": time.time(),
            "started_at": self.started_at,
            "running": bool(self._task and not self._task.done()),
            "policy": {
                "mode": "passive",
                "interval_s": self.interval_s,
                "process_scans": False,
                "writes_only_on_relevant_focus_change": True,
                "managed_application_focus": True,
                "resource_join_cache_s": 10.0,
                "memory_event_limit": self.events.maxlen,
                "disk_limit_bytes": 5 * 1024 * 1024,
            },
            "events": list(self.events),
        }


_MONITOR: FocusMonitor | None = None


def get_focus_monitor() -> FocusMonitor:
    global _MONITOR
    if _MONITOR is None:
        _MONITOR = FocusMonitor()
    return _MONITOR


__all__ = ["FocusMonitor", "get_focus_monitor"]
