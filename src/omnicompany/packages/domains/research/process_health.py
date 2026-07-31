# [OMNI] origin=codex domain=research/health ts=2026-07-18T12:25:00+08:00 type=health status=active
# [OMNI] summary="research.run 进程级健康审计：发现仍绑定 runs/run_* 的残留 worker，补足只看 native_status.json 的盲区。"
# [OMNI] why="2026-07-18 发现两个 2026-07-12 Codex node worker 仍存活，但旧 run 无 native_status.json，omni research doctor 假绿。"
# [OMNI] tags=research,doctor,orphan-process,observability
"""Process-level health checks for optional unattended research workers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class ResearchRunProcess:
    """A live process whose command/cwd is bound to a research ``runs/run_*`` dir."""

    pid: int
    parent_pid: int
    name: str
    created_at: str
    run_name: str
    command_line: str


@dataclass(frozen=True)
class ProcessAudit:
    processes: tuple[ResearchRunProcess, ...]
    error: str = ""


def _normalized_path_text(value: object) -> str:
    return str(value or "").replace("\\", "/").rstrip("/").casefold()


def _run_name_bound_to_row(row: dict[str, Any], runs_root: Path) -> str:
    root = _normalized_path_text(runs_root.resolve())
    haystacks = [
        _normalized_path_text(row.get("CommandLine")),
        _normalized_path_text(row.get("Cwd")),
    ]
    marker = root + "/"
    for haystack in haystacks:
        start = haystack.find(marker)
        if start < 0:
            continue
        tail = haystack[start + len(marker):]
        run_name = tail.split("/", 1)[0].strip("\"'")
        if run_name.startswith("run_"):
            if runs_root.is_dir():
                for candidate in runs_root.iterdir():
                    if candidate.name.casefold() == run_name:
                        return candidate.name
            return run_name
    return ""


def _match_research_processes(
    rows: Iterable[dict[str, Any]],
    runs_root: Path,
) -> tuple[ResearchRunProcess, ...]:
    matches: list[ResearchRunProcess] = []
    for row in rows:
        run_name = _run_name_bound_to_row(row, runs_root)
        if not run_name:
            continue
        try:
            pid = int(row.get("ProcessId") or 0)
            parent_pid = int(row.get("ParentProcessId") or 0)
        except (TypeError, ValueError):
            continue
        if pid <= 0 or pid == os.getpid():
            continue
        matches.append(
            ResearchRunProcess(
                pid=pid,
                parent_pid=parent_pid,
                name=str(row.get("Name") or ""),
                created_at=str(row.get("CreationDate") or ""),
                run_name=run_name,
                command_line=str(row.get("CommandLine") or ""),
            )
        )
    return tuple(sorted(matches, key=lambda item: item.pid))


def _windows_process_rows() -> list[dict[str, Any]]:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        raise RuntimeError("PATH 中找不到 PowerShell，无法枚举 research worker")
    script = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "@(Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,Name,CreationDate,CommandLine) | "
        "ConvertTo-Json -Compress"
    )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=8,
        check=False,
        creationflags=creationflags,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown error").strip()
        raise RuntimeError(f"Win32_Process 枚举失败: {detail[:240]}")
    raw = (completed.stdout or "").lstrip("\ufeff").strip()
    if not raw:
        return []
    payload = json.loads(raw)
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    raise RuntimeError("Win32_Process 返回了非对象 JSON")


def _procfs_process_rows() -> list[dict[str, Any]]:
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        raise RuntimeError("当前平台既非 Windows，也没有 /proc，无法枚举 research worker")
    rows: list[dict[str, Any]] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command_line = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode(
                "utf-8", errors="replace"
            )
            cwd = os.readlink(entry / "cwd")
            stat_parts = (entry / "stat").read_text(encoding="utf-8", errors="replace").split()
            parent_pid = int(stat_parts[3]) if len(stat_parts) > 3 else 0
            name = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip()
        except (OSError, ValueError):
            continue
        rows.append(
            {
                "ProcessId": int(entry.name),
                "ParentProcessId": parent_pid,
                "Name": name,
                "CreationDate": "",
                "CommandLine": command_line,
                "Cwd": cwd,
            }
        )
    return rows


def inspect_research_processes(runs_root: Path) -> ProcessAudit:
    """Return live processes bound to a research run; never raise into the CLI."""

    try:
        rows = _windows_process_rows() if os.name == "nt" else _procfs_process_rows()
        return ProcessAudit(processes=_match_research_processes(rows, runs_root))
    except (OSError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        return ProcessAudit(processes=(), error=str(exc))
