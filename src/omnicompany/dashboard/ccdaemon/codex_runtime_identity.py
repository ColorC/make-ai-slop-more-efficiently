"""Bind a PTY to its live Codex main thread, never inherited tool environment.

Only OS ancestry, the process's own HOME/open rollout handles and native SQLite
metadata are evidence. Titles, prompts, newest global threads and registry hints
are not identity. Unknown/ambiguous evidence is deliberately not writable.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import psutil

_cache: dict[tuple[int, float], tuple[float, dict[str, Any]]] = {}

# Read-only watchers (one native-link loop per Codex PTY, the capacity sweep)
# each run an open_files probe that takes seconds under load. On the daemon's
# shared 8-worker default executor they held every worker and starved all /cc
# HTTP routes into 120s proxy timeouts (py-spy, 2026-09-27: 69% of worker
# samples here). Watchers submit to this pool; input callers (refresh=True)
# keep the default executor so they never queue behind watchers.
CODEX_IDENTITY_WATCH_EXECUTOR = ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="codex-identity-watch",
)

# psutil's Windows open_files() walks every system handle while holding the GIL
# and TerminateThread()s NtQueryObject workers that hang. Run in the ccdaemon it
# stalled the event loop on every poll and finally froze it outright (py-spy,
# 2026-09-13: all threads parked behind open_files). A throwaway interpreter keeps
# the stall and any lock left behind by a killed worker out of the daemon.
_OPEN_FILES_TIMEOUT_S = 15.0
_OPEN_FILES_SCRIPT = (
    "import json, sys, psutil; "
    "print(json.dumps([f.path for f in psutil.Process(int(sys.argv[1])).open_files()]))"
)


def _open_file_paths(pid: int) -> list[str]:
    """Open file paths of ``pid``; a hung or failed probe raises and fails closed."""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _OPEN_FILES_SCRIPT, str(pid)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_OPEN_FILES_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OSError(f"open_files probe timed out for pid {pid}") from exc
    if proc.returncode != 0:
        raise OSError(f"open_files probe failed for pid {pid}: {proc.stderr.strip()[-300:]}")
    paths = json.loads(proc.stdout)
    if not isinstance(paths, list):
        raise ValueError("open_files probe returned a non-list payload")
    return [str(path) for path in paths]


def _direct_codex_in_probe(child_pid: int) -> psutil.Process | None:
    pending = [psutil.Process(child_pid)]
    found = []
    for _depth in range(5):
        next_level = []
        for process in pending:
            try:
                if process.name().lower() in {"codex", "codex.exe"}:
                    # Native subagents and exec/fork tools below it cannot own
                    # the enclosing PTY, even if they inherit OMNI_CC_PTY_ID.
                    found.append(process)
                else:
                    next_level.extend(process.children())
            except psutil.Error:
                continue
        pending = next_level
        if not pending:
            break
    return found[0] if len(found) == 1 else None


_DIRECT_CODEX_SCRIPT = (
    "import json, sys; "
    "from omnicompany.dashboard.ccdaemon.codex_runtime_identity import _direct_codex_in_probe; "
    "p = _direct_codex_in_probe(int(sys.argv[1])); "
    "print(json.dumps({'pid': p.pid, 'created_at': p.create_time()} if p else None))"
)


def _direct_codex(child_pid: int) -> psutil.Process | None:
    """Keep Windows process-tree enumeration and its GIL stalls off the daemon.

    An executor thread still shares the event loop's GIL. The same bounded,
    main-only ancestry walk therefore runs in a disposable interpreter, like
    the open-handle probe. Validate its process generation before consuming it.
    """
    try:
        probe = subprocess.run(
            [sys.executable, "-c", _DIRECT_CODEX_SCRIPT, str(child_pid)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_OPEN_FILES_TIMEOUT_S, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OSError(f"process-tree probe timed out for pid {child_pid}") from exc
    if probe.returncode != 0:
        raise OSError(f"process-tree probe failed for pid {child_pid}")
    selected = json.loads(probe.stdout)
    if selected is None:
        return None
    if not isinstance(selected, dict) or not {"pid", "created_at"}.issubset(selected):
        raise ValueError("process-tree probe returned an invalid process generation")
    process = psutil.Process(int(selected["pid"]))
    if (process.create_time() != float(selected["created_at"])
            or not process.is_running()
            or process.name().lower() not in {"codex", "codex.exe"}):
        return None
    return process


def _read_identity(home: Path, files: list[str], pid: int, created_at: float) -> dict[str, Any]:
    unknown = {"known": False, "reason": "no-unambiguous-main-rollout", "provider_home": str(home), "pid": pid}
    sessions = (home / "sessions").resolve()
    opened = []
    for file in files:
        path = Path(file).resolve()
        if path.name.startswith("rollout-") and path.suffix == ".jsonl" and path.is_relative_to(sessions):
            opened.append(path)
    if not opened:
        return unknown
    with sqlite3.connect((home / "state_5.sqlite").as_uri() + "?mode=ro", uri=True, timeout=.5) as db:
        candidates = []
        for path in opened:
            # UUID is a filename suffix generated by Codex, not user content.
            native = path.stem[-36:]
            row = db.execute("SELECT id,source,rollout_path,archived,title FROM threads WHERE id=?", (native,)).fetchone()
            if not row or row[3] or Path(row[2]).resolve() != path:
                continue
            # Codex stores subagent/fork source as structured JSON. Only the
            # direct CLI main thread may receive input through this PTY.
            source = row[1]
            try:
                source = json.loads(source)
            except (ValueError, TypeError):
                pass
            if source != "cli":
                continue
            candidates.append((row[0], str(path), row[4]))
    if len(candidates) != 1:
        return unknown
    native, rollout, title = candidates[0]
    with sqlite3.connect((home / "logs_2.sqlite").as_uri() + "?mode=ro", uri=True, timeout=.5) as db:
        row = db.execute(
            "SELECT process_uuid FROM logs WHERE thread_id=? AND process_uuid LIKE ? AND ts>=? ORDER BY id DESC LIMIT 1",
            (native, f"pid:{pid}:%", int(created_at) - 1),
        ).fetchone()
    if not row:
        return unknown | {"reason": "main-rollout-process-log-unconfirmed"}
    return {"known": True, "reason": "live-process-open-main-rollout", "native_session_id": native,
            "provider_home": str(home), "pid": pid, "process_created_at": created_at,
            "process_uuid": row[0], "rollout_path": rollout, "title": title}


def resolve_codex_runtime_identity(child_pid: int | None, *, refresh: bool = False) -> dict[str, Any]:
    """Resolve current main identity; a cached value is never authority to send.

    Read-only watchers share a short cache; input callers use refresh=True.
    Process creation time prevents PID reuse. Missing handles fail closed.
    """
    try:
        if not child_pid:
            return {"known": False, "reason": "missing-child-process"}
        process = _direct_codex(int(child_pid))
        if process is None:
            return {"known": False, "reason": "no-unique-direct-codex-process"}
        created = process.create_time()
        key = (process.pid, created)
        cached = _cache.get(key)
        if not refresh and cached and time.monotonic() - cached[0] < 30:
            return dict(cached[1])
        # Read only the required environment value; never return credentials.
        explicit_home = process.environ().get("CODEX_HOME")
        files = _open_file_paths(process.pid)
        homes = {str(Path(file).parent.resolve()) for file in files
                 if Path(file).name in {"logs_2.sqlite", "logs_2.sqlite-wal", "logs_2.sqlite-shm"}}
        # SQLite connections can close while an idle TUI keeps its rollout
        # handle open. Its sessions ancestor is equally process-owned evidence.
        for file in files:
            path = Path(file)
            if path.name.startswith("rollout-") and path.suffix == ".jsonl":
                sessions = next((parent for parent in path.parents if parent.name == "sessions"), None)
                if sessions is not None:
                    homes.add(str(sessions.parent.resolve()))
        if explicit_home:
            home = Path(explicit_home).resolve()
            if homes and str(home) not in homes:
                return {"known": False, "reason": "process-home-handle-mismatch"}
        elif len(homes) == 1:
            home = Path(next(iter(homes)))
        else:
            return {"known": False, "reason": "process-home-unknown"}
        result = _read_identity(home, files, process.pid, created)
        if process.create_time() != created or not process.is_running():
            return {"known": False, "reason": "process-generation-changed"}
        _cache[key] = (time.monotonic(), result)
        if len(_cache) > 128:
            oldest = min(_cache, key=lambda item: _cache[item][0])
            _cache.pop(oldest, None)
        return dict(result)
    except (psutil.Error, OSError, sqlite3.Error, ValueError, TypeError):
        return {"known": False, "reason": "runtime-identity-unreadable"}


def hook_owns_pty(pty_id: str, native_session_id: str) -> bool:
    """An inherited PTY environment variable alone never authorizes binding."""
    from .pty import _read_meta_store

    meta = _read_meta_store().get(pty_id) or {}
    identity = resolve_codex_runtime_identity(meta.get("child_pid"), refresh=True)
    try:
        caller = next((process for process in psutil.Process().parents()
                       if process.name().lower() in {"codex", "codex.exe"}), None)
    except psutil.Error:
        return False
    return bool(identity.get("known") and identity.get("native_session_id") == native_session_id
                and caller is not None and caller.pid == identity.get("pid"))
