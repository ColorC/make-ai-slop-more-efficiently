# OMNI-PERSISTENT-SCRIPT owner=maintainers purpose=wait for exact native turn completion before account-aware Codex CLI reload
"""Reload one Codex PTY only after its exact native turn is complete."""
from __future__ import annotations

import contextlib
import ctypes
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import click


class NativeTurn:
    def __init__(self, session_id: str, turn_id: str):
        self.session_id = session_id
        self.turn_id = turn_id
        self.offset = 0
        self.pending = b""
        self.native_id = None
        self.latest_turn = None
        self.completed = False

    def consume(self, path: Path) -> bool:
        with path.open("rb") as stream:
            stream.seek(self.offset)
            chunk = stream.read()
            self.offset += len(chunk)
        lines = (self.pending + chunk).split(b"\n")
        self.pending = lines.pop()
        for line in lines:
            if not line.strip():
                continue
            row = json.loads(line)
            payload = row.get("payload", {})
            if row.get("type") == "session_meta":
                self.native_id = payload.get("id")
            elif row.get("type") == "event_msg":
                if payload.get("type") == "task_started":
                    self.latest_turn = payload.get("turn_id")
                elif payload.get("type") == "task_complete" and payload.get("turn_id") == self.turn_id:
                    self.completed = True
        if self.native_id != self.session_id:
            raise RuntimeError("Native history session id does not match the requested session")
        if self.latest_turn != self.turn_id:
            raise RuntimeError("A different native turn is active; preserve the session and do not reload it")
        return self.completed


@contextlib.contextmanager
def directory_change(directory: Path):
    """Bounded Windows change event, without a model or status polling loop."""
    if os.name != "nt":
        raise RuntimeError("Native Codex turn reload currently requires Windows change events")
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.FindFirstChangeNotificationW.argtypes = [ctypes.c_wchar_p, ctypes.c_int, ctypes.c_uint32]
    k.FindFirstChangeNotificationW.restype = ctypes.c_void_p
    k.FindNextChangeNotification.argtypes = [ctypes.c_void_p]
    k.FindNextChangeNotification.restype = ctypes.c_int
    k.FindCloseChangeNotification.argtypes = [ctypes.c_void_p]
    k.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    k.WaitForSingleObject.restype = ctypes.c_uint32
    handle = k.FindFirstChangeNotificationW(str(directory), False, 0x08 | 0x10)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())

    def wait(seconds: float):
        result = k.WaitForSingleObject(handle, max(1, min(int(seconds * 1000), 0xFFFFFFFE)))
        if result == 0x102:
            raise TimeoutError("Exact native turn did not complete within the reload deadline")
        if result != 0 or not k.FindNextChangeNotification(handle):
            raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield wait
    finally:
        k.FindCloseChangeNotification(handle)


def wait_for_turn(path: Path, state: NativeTurn, timeout: float, watcher=directory_change):
    deadline = time.monotonic() + timeout
    with watcher(path.parent) as wait:
        while not state.consume(path):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Exact native turn did not complete within the reload deadline")
            wait(remaining)
        # Re-read once before lifecycle actions; a new task must never be killed.
        state.consume(path)


def lifecycle_cli(pty_id: str, receipt: dict, save, run=subprocess.run, before_kill=lambda: None) -> dict:
    """Use the existing account-aware kill/restore/resume CLI operations."""
    entry = str(Path(sys.executable).with_name("omni.exe"))
    for action, timeout, key in [("kill", 45, "killed"), ("restore", 45, "restored"), ("resume", 145, "resumed")]:
        argv = [entry, "cc", "session", action, pty_id]
        if action == 'kill':
            before_kill()
        result = run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        step = {"command": argv, "exit_code": result.returncode, "stderr": result.stderr}
        receipt["actions"].append(step)
        save()
        if result.returncode:
            raise RuntimeError(f"Native session {action} failed: {result.stderr or result.stdout}")
        body = json.loads(result.stdout)
        rows = body.get(key, [])
        step["result"] = rows
        save()
        if len(rows) != 1 or not rows[0].get("ok"):
            raise RuntimeError(f"Native session {action} did not confirm one exact successful result")
    return rows[0]


def verify_pty_binding(pty_id, native_session_id, fetch=None):
    if fetch is None:
        import urllib.request
        from omnicompany.dashboard.ccdaemon.api import read_status
        status = read_status()
        if not status.alive or not status.port:
            raise RuntimeError('ccdaemon is not alive; preserve the original session')
        with urllib.request.urlopen(f'http://127.0.0.1:{status.port}/cc/sessions?include_recoverable=false', timeout=30) as response:
            payload = json.loads(response.read())
    else:
        payload = fetch()
    rows = [r for r in payload.get('items', []) if r.get('id') == pty_id]
    if len(rows) != 1 or rows[0].get('provider_session_id') != native_session_id:
        raise RuntimeError('Exact PTY is not bound to the requested native session; preserve all sessions')
    return {k:rows[0].get(k) for k in ['id','provider','provider_session_id','cwd']}


def find_reloaded_native(session_id: str, expected: Path, old_pid: int, timeout=45):
    import psutil
    deadline = time.monotonic() + timeout
    while True:
        matches = []
        for process in psutil.process_iter(["pid", "name"]):
            if (process.info["name"] or "").lower() != "codex.exe" or process.pid == old_pid:
                continue
            try:
                if session_id in process.cmdline():
                    exe = process.exe()
                    if os.path.normcase(exe) != os.path.normcase(str(expected)):
                        raise RuntimeError("Resumed native process loaded an unexpected executable: " + exe)
                    matches.append({"pid": process.pid, "executable": exe, "native_session_id": session_id})
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RuntimeError("More than one resumed native process matches this exact session")
        if time.monotonic() >= deadline:
            raise TimeoutError("Resumed native executable could not be verified within 45 seconds")
        time.sleep(0.5)  # deterministic bounded process-start verification; never model polling


@click.command("reload-after-turn")
@click.argument("pty_id")
@click.option("--history-file", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--native-session-id", required=True)
@click.option("--turn-id", required=True)
@click.option("--old-native-pid", type=int, required=True)
@click.option("--expected-executable", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--timeout", type=click.IntRange(1, 7200), default=3600, show_default=True)
@click.option("--receipt", type=click.Path(dir_okay=False, path_type=Path), required=True)
def cmd_reload_after_turn(pty_id, history_file, native_session_id, turn_id, old_native_pid, expected_executable, timeout, receipt):
    """Wait for an exact Codex turn, then restore the same history with the installed CLI."""
    if not re.fullmatch(r"[0-9a-f]{16}", pty_id):
        raise click.UsageError("A precise 16-character PTY id is required")
    if not receipt.is_absolute() or not receipt.parent.is_dir():
        raise click.UsageError("Receipt must be an absolute file in an existing project runtime directory")
    if receipt.exists():
        raise click.UsageError("Existing reload receipt must be consumed, not overwritten")
    report = {"status": "waiting_for_exact_turn", "pty_id": pty_id, "native_session_id": native_session_id,
              "turn_id": turn_id, "history_file": str(history_file), "old_native_pid": old_native_pid,
              "expected_executable": str(expected_executable), "timeout_seconds": timeout, "actions": [],
              "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "artifacts": [{"path": str(receipt), "role": "native turn wait and account-aware reload terminal receipt"}]}
    def save():
        receipt.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    save()
    try:
        state = NativeTurn(native_session_id, turn_id)
        wait_for_turn(history_file, state, timeout)
        report['original_binding'] = verify_pty_binding(pty_id, native_session_id)
        report["status"] = "native_turn_complete"
        save()
        resumed = lifecycle_cli(pty_id, report, save, before_kill=lambda: state.consume(history_file))
        if resumed.get("provider_session_id") != native_session_id:
            raise RuntimeError("Native resume did not confirm the original native session id")
        report["new_pty_id"] = resumed["new_id"]
        report["native_process"] = find_reloaded_native(native_session_id, expected_executable, old_native_pid)
        report.update(status="complete", history_preserved=history_file.is_file())
    except Exception as exc:
        report.update(status="failed_with_reason", reason=str(exc), error_type=type(exc).__name__)
        save()
        raise click.ClickException(f"{exc}; receipt: {receipt}") from exc
    finally:
        report["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        save()
    click.echo(json.dumps(report, ensure_ascii=False))
