"""External LiTeWork development-runtime supervisor.

This module intentionally lives in OmniCompany.  It treats LiTeWork as an
external product and uses only LiTeWork's public CLI; it never owns or mutates
the terminal-daemon socket endpoint.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from omnicompany.core.config import resolve_service_data_dir


FULL_RESTART_CONFIRMATION = "FULL-RESTART"
DEFAULT_POLL_SECONDS = 10.0

# Environment left behind by a runtime that owns terminal sessions, tagged with
# the runtime that owns it.  The tag matters: only a session hosted by the very
# runtime being restarted would lose its own caller, so the refusal is scoped to
# that runtime.  See `detect_internal_session`.
INTERNAL_SESSION_ENV_MARKERS: tuple[tuple[str, str, str], ...] = (
    ("ORCA_TERMINAL_HANDLE", "litework-terminal", "litework"),
    ("ORCA_PANE_KEY", "litework-terminal", "litework"),
    ("ORCA_TAB_ID", "litework-terminal", "litework"),
    ("ORCA_WORKTREE_ID", "litework-terminal", "litework"),
    ("OMNI_CC_PTY_ID", "omnicompany-pty", "omnicompany"),
)

# A status RPC that never answers is not evidence that the host is gone; probe
# again before the guard is allowed to conclude anything.
STATUS_PROBE_ATTEMPTS = 3
STATUS_PROBE_DELAY_SECONDS = 2.0
# Spawn backoff.  Without it a mis-diagnosed host produced 17 launches in one
# hour (2026-09-03), each one dying on LiTeWork's single-instance profile lock.
MIN_SPAWN_INTERVAL_SECONDS = 120.0
MAX_SPAWN_INTERVAL_SECONDS = 900.0

_WINDOWS_DETACHED_FLAGS = (
    getattr(subprocess, "DETACHED_PROCESS", 0)
    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    | getattr(subprocess, "CREATE_NO_WINDOW", 0)
)


@dataclass(frozen=True)
class LiteWorkConfig:
    root: str
    mode: str = "serve"
    port: int = 6770
    pairing_address: str = "litework.example.com"
    profile_dir: str = ""
    poll_seconds: float = DEFAULT_POLL_SECONDS
    # Empty = inherit the LiTeWork build's own default (test for unpackaged dev).
    # Set to a LiWork cloud base (e.g. https://liwork-server.the_company.com) to pin
    # the host — and everything it launches — to that environment.
    cloud_api_url: str = ""

    @property
    def root_path(self) -> Path:
        return Path(self.root).expanduser().resolve()

    @property
    def cli_script(self) -> Path:
        return self.root_path / "config" / "scripts" / "orca-dev.mjs"


def data_dir() -> Path:
    return resolve_service_data_dir("litework_host")


def config_file() -> Path:
    return data_dir() / "config.json"


def pid_file() -> Path:
    return data_dir() / "guard.pid"


def stop_file() -> Path:
    return data_dir() / "guard.stop"


def guard_lock_file() -> Path:
    return data_dir() / "guard.lock"


def state_file() -> Path:
    return data_dir() / "state.json"


def log_file() -> Path:
    return data_dir() / "guard.log"


def runtime_log_file() -> Path:
    return data_dir() / "runtime.log"


def spawn_state_file() -> Path:
    return data_dir() / "spawn.json"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _log(message: str) -> None:
    path = log_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{_now_iso()} {message}\n")


def configure(
    root: str | Path,
    *,
    mode: str = "serve",
    port: int = 6770,
    pairing_address: str = "litework.example.com",
    profile_dir: str | Path | None = None,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    cloud_api_url: str | None = None,
) -> LiteWorkConfig:
    root_path = Path(root).expanduser().resolve()
    if mode not in {"open", "serve"}:
        raise ValueError("mode must be 'open' or 'serve'")
    if not (root_path / "config" / "scripts" / "orca-dev.mjs").is_file():
        raise FileNotFoundError(f"LiTeWork dev CLI not found under {root_path}")
    if not 1 <= int(port) <= 65535:
        raise ValueError("port must be between 1 and 65535")
    config = LiteWorkConfig(
        root=str(root_path),
        mode=mode,
        port=int(port),
        pairing_address=str(pairing_address or "").strip(),
        profile_dir=str(Path(profile_dir).expanduser().resolve()) if profile_dir else "",
        poll_seconds=max(2.0, float(poll_seconds)),
        cloud_api_url=str(cloud_api_url or "").strip(),
    )
    _write_json(config_file(), asdict(config))
    return config


def load_config() -> LiteWorkConfig:
    payload = _read_json(config_file())
    env_root = os.environ.get("OMNI_LITEWORK_ROOT", "").strip()
    if env_root:
        payload["root"] = env_root
    if not payload.get("root"):
        raise RuntimeError(
            "LiTeWork host is not configured; run `omni litework configure --root <checkout>`"
        )
    config = LiteWorkConfig(
        root=str(payload["root"]),
        mode=str(payload.get("mode") or "serve"),
        port=int(payload.get("port") or 6770),
        pairing_address=str(payload.get("pairing_address") or ""),
        profile_dir=str(payload.get("profile_dir") or ""),
        poll_seconds=max(2.0, float(payload.get("poll_seconds") or DEFAULT_POLL_SECONDS)),
        # Explicit env wins over persisted config so ops can override per-launch,
        # mirroring OMNI_LITEWORK_ROOT above.
        cloud_api_url=os.environ.get("LIWORK_CLOUD_API_URL", "").strip()
        or str(payload.get("cloud_api_url") or ""),
    )
    if not config.cli_script.is_file():
        raise FileNotFoundError(f"LiTeWork dev CLI not found: {config.cli_script}")
    return config


def _cli_env(config: LiteWorkConfig) -> dict[str, str]:
    env = os.environ.copy()
    if config.profile_dir:
        env["ORCA_DEV_USER_DATA_PATH"] = config.profile_dir
    if config.cloud_api_url:
        env["LIWORK_CLOUD_API_URL"] = config.cloud_api_url
    return env


def cli_command(config: LiteWorkConfig, args: Sequence[str]) -> list[str]:
    node = shutil.which("node")
    if not node:
        raise FileNotFoundError("node is not available on PATH")
    return [node, str(config.cli_script), *args]


def run_cli(
    args: Sequence[str],
    *,
    config: LiteWorkConfig | None = None,
    timeout: float = 12.0,
    require_ok: bool = True,
) -> dict[str, Any]:
    cfg = config or load_config()
    proc = subprocess.run(
        cli_command(cfg, args),
        cwd=cfg.root_path,
        env=_cli_env(cfg),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        detail = (proc.stderr or proc.stdout or "no output").strip()[-1000:]
        raise RuntimeError(f"LiTeWork CLI returned invalid JSON: {detail}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("LiTeWork CLI returned a non-object payload")
    if require_ok and (proc.returncode != 0 or payload.get("ok") is not True):
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        message = error.get("message") or proc.stderr.strip() or f"exit {proc.returncode}"
        raise RuntimeError(f"LiTeWork CLI failed: {message}")
    return payload


def runtime_status(config: LiteWorkConfig | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    try:
        payload = run_cli(["status", "--json"], config=cfg, timeout=8.0, require_ok=False)
    except Exception as exc:  # noqa: BLE001 - status is an observability boundary
        return {
            "healthy": False,
            # Why: the probe itself failed, so "not running" is unknown, not observed.
            "probe_ok": False,
            "app_running": False,
            "runtime_reachable": False,
            "error": str(exc),
        }
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    app = result.get("app") if isinstance(result.get("app"), dict) else {}
    runtime = result.get("runtime") if isinstance(result.get("runtime"), dict) else {}
    app_running = app.get("running") is True
    runtime_reachable = runtime.get("reachable") is True and runtime.get("state") == "ready"
    return {
        "healthy": bool(payload.get("ok") is True and app_running and runtime_reachable),
        "probe_ok": True,
        "app_running": app_running,
        "app_pid": app.get("pid") if isinstance(app.get("pid"), int) else None,
        "runtime_reachable": runtime_reachable,
        "runtime_state": runtime.get("state"),
        "runtime_id": runtime.get("runtimeId"),
        "app_version": runtime.get("appVersion"),
        "error": None if payload.get("ok") is True else payload.get("error"),
    }


def list_terminals(config: LiteWorkConfig | None = None) -> list[dict[str, Any]]:
    payload = run_cli(["terminal", "list", "--json"], config=config, timeout=12.0)
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    rows = result.get("terminals")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def detect_internal_session(
    env: Mapping[str, str] | None = None,
    *,
    runtime: str | None = None,
) -> dict[str, str] | None:
    """Report the managed session the caller is running inside, if any.

    Pass ``runtime`` to ask the only question that gates a restart: is the caller
    hosted by *this* runtime?  A restart issued from inside the runtime it is
    restarting takes down its own caller mid-flight, so nobody is left to observe
    whether recovery finished.  A caller hosted by a different runtime is an
    ordinary operator and must not be refused.  Omit ``runtime`` to report
    provenance for diagnostics.
    """
    source = os.environ if env is None else env
    for key, kind, owner in INTERNAL_SESSION_ENV_MARKERS:
        if runtime is not None and owner != runtime:
            continue
        value = str(source.get(key) or "").strip()
        if value:
            return {"kind": kind, "marker": key, "value": value, "runtime": owner}
    return None


def live_terminals(config: LiteWorkConfig | None = None) -> list[dict[str, Any]]:
    """Terminals a destructive action would destroy, newest metadata first."""
    return [row for row in list_terminals(config) if row.get("orphaned") is not True]


def deployment_script(config: LiteWorkConfig) -> Path | None:
    """The checkout's own deployment tool, when it ships one."""
    script = config.root_path / "tools" / "litework-6769.ps1"
    return script if script.is_file() else None


def deployment_active_port(config: LiteWorkConfig) -> int | None:
    """Serve port the deployment's gateway currently routes to.

    The deployment hands the Host between two ports (observed live: it moved to
    6771 on 2026-09-03), so the port this service was configured with goes stale.
    """
    payload = _read_json(config.root_path / ".runtime" / "litework-6769" / "gateway-route.json")
    port = payload.get("activePort")
    if isinstance(port, (int, float)) and 0 < int(port) < 65536:
        return int(port)
    return None


def _recover_via_deployment(config: LiteWorkConfig) -> dict[str, Any] | None:
    """Let the checkout's deployment tool start the Host, when it owns one.

    That tool knows the blue-green port and the versioned build root it deployed.
    Launching `orca-dev serve` from here instead would quietly start the repo's
    own build on the port the deployment had already left.  Returns None when the
    tool is absent or refuses, so the caller can fall back rather than leave the
    host down.
    """
    script = deployment_script(config)
    if script is None:
        return None
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        return None
    try:
        proc = subprocess.run(
            [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
             "-Action", "recover"],
            cwd=config.root_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log(f"deployment recover unavailable: {type(exc).__name__}: {exc}")
        return None
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-400:]
        _log(f"deployment recover refused rc={proc.returncode}: {detail}")
        return None
    _log("deployment recover completed")
    return {"via": "deployment", "script": str(script)}


def _port_is_listening(port: int, *, host: str = "127.0.0.1", timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _probe_status(config: LiteWorkConfig) -> dict[str, Any]:
    """Poll `status` until it reports healthy or the retry budget is spent.

    One slow answer under load used to read as "the host is gone" and triggered a
    launch; only a repeated verdict is allowed to mean that.
    """
    last = runtime_status(config)
    for _ in range(max(0, STATUS_PROBE_ATTEMPTS - 1)):
        if last.get("healthy"):
            return last
        time.sleep(STATUS_PROBE_DELAY_SECONDS)
        last = runtime_status(config)
    return last


def _spawn_cooldown() -> dict[str, Any]:
    """Remaining backoff before another launch attempt is allowed."""
    state = _read_json(spawn_state_file())
    last_at = state.get("last_spawn_at")
    failures = state.get("consecutive_failures")
    failures = int(failures) if isinstance(failures, (int, float)) else 0
    interval = min(
        MAX_SPAWN_INTERVAL_SECONDS,
        MIN_SPAWN_INTERVAL_SECONDS * (2 ** max(0, failures - 1)),
    )
    if not isinstance(last_at, (int, float)):
        return {"remaining": 0.0, "interval": interval, "consecutive_failures": failures}
    remaining = max(0.0, float(last_at) + interval - time.time())
    return {"remaining": remaining, "interval": interval, "consecutive_failures": failures}


def _record_spawn(*, healthy: bool) -> None:
    state = _read_json(spawn_state_file())
    failures = state.get("consecutive_failures")
    failures = int(failures) if isinstance(failures, (int, float)) else 0
    _write_json(spawn_state_file(), {
        "last_spawn_at": time.time(),
        "last_spawn_iso": _now_iso(),
        "consecutive_failures": 0 if healthy else failures + 1,
    })


def _clear_spawn_backoff() -> None:
    if _read_json(spawn_state_file()).get("consecutive_failures"):
        _write_json(spawn_state_file(), {"consecutive_failures": 0})


def _spawn_runtime(config: LiteWorkConfig) -> int:
    args: list[str]
    if config.mode == "serve":
        args = ["serve", "--port", str(config.port)]
        if config.pairing_address:
            args += ["--pairing-address", config.pairing_address]
    else:
        args = ["open", "--json"]
    runtime_log_file().parent.mkdir(parents=True, exist_ok=True)
    log_handle = runtime_log_file().open("a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            cli_command(config, args),
            cwd=config.root_path,
            env=_cli_env(config),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=log_handle,
            creationflags=_WINDOWS_DETACHED_FLAGS if sys.platform == "win32" else 0,
            start_new_session=sys.platform != "win32",
            close_fds=True,
        )
    finally:
        log_handle.close()
    _log(f"runtime spawn pid={proc.pid} mode={config.mode} root={config.root}")
    return proc.pid


def _wait_healthy(config: LiteWorkConfig, timeout: float = 45.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last = runtime_status(config)
    while time.monotonic() < deadline:
        if last.get("healthy"):
            return last
        time.sleep(1.0)
        last = runtime_status(config)
    return last


def ensure_runtime(config: LiteWorkConfig | None = None) -> dict[str, Any]:
    """Ensure a missing host is started; never kill or fully restart it.

    Every branch that stops short of launching exists because a wrong launch is
    not free: LiTeWork holds one single-instance lock per profile, so a duplicate
    dies on startup, and a launch during a blue-green deployment can fight the
    deployment for that lock.
    """
    cfg = config or load_config()
    before = _probe_status(cfg)
    if before.get("healthy"):
        _clear_spawn_backoff()
        return {"ok": True, "action": "none", "status": before}
    if before.get("probe_ok") is False:
        return {"ok": False, "action": "probe_failed", "status": before}
    if before.get("app_running"):
        # An alive-but-unreachable host might still own renderer state.  The
        # guard reports it and waits for an explicit restart instead of killing.
        return {"ok": False, "action": "degraded", "status": before}
    busy = next(
        (port for port in {cfg.port, deployment_active_port(cfg)} if port and _port_is_listening(port)),
        None,
    )
    if busy:
        # Someone answers on a serve port even though the status RPC says no
        # app: a deployment or a foreign host owns it.  Launching would only
        # lose the single-instance race.
        return {"ok": False, "action": "port_busy", "port": busy, "status": before}
    cooldown = _spawn_cooldown()
    if cooldown["remaining"] > 0:
        return {
            "ok": False,
            "action": "cooldown",
            "cooldown_seconds": round(cooldown["remaining"], 1),
            "consecutive_failures": cooldown["consecutive_failures"],
            "status": before,
        }
    recovered = _recover_via_deployment(cfg)
    if recovered is not None:
        # The deployment tool already blocked until the Host answered, so this is
        # a confirmation, not a second startup wait.
        after = _wait_healthy(cfg, timeout=15.0)
        _record_spawn(healthy=bool(after.get("healthy")))
        return {"ok": bool(after.get("healthy")), "action": "recovered", **recovered, "status": after}
    launcher_pid = _spawn_runtime(cfg)
    after = _wait_healthy(cfg)
    _record_spawn(healthy=bool(after.get("healthy")))
    return {
        "ok": bool(after.get("healthy")),
        "action": "started",
        "launcher_pid": launcher_pid,
        "status": after,
    }


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            process_query_limited_information = 0x1000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == 259
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pid(path: Path) -> int | None:
    try:
        value = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return value if value > 0 else None


@contextmanager
def _exclusive_guard_lock():
    """Hold one machine-local lock for the lifetime of the guard process."""
    path = guard_lock_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    acquired = False
    try:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except (OSError, BlockingIOError):
            acquired = False
        yield acquired
    finally:
        if acquired:
            try:
                handle.seek(0)
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def _terminate_exact_pid(pid: int, *, timeout: float = 8.0) -> None:
    if not _pid_alive(pid):
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    else:
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if _pid_alive(pid):
        if sys.platform == "win32":
            raise RuntimeError(f"process {pid} did not exit")
        os.kill(pid, signal.SIGKILL)


def reload_renderer(config: LiteWorkConfig | None = None) -> dict[str, Any]:
    """Reload the desktop renderer through LiTeWork's public inspect CLI."""
    cfg = config or load_config()
    payload = run_cli(["desktop", "reload", "--json"], config=cfg, timeout=20.0)
    return {"ok": True, "level": "renderer", "result": payload.get("result")}


def restart_preserving_sessions(config: LiteWorkConfig | None = None) -> dict[str, Any]:
    """Restart only the LiTeWork app/serve host and verify daemon sessions survive.

    Terminal state lives in the separate daemon, so PTYs survive — but every web
    and desktop client attached to the host is dropped.  The caller-side refusal
    (internal session, authorization) is enforced by the CLI layer so this stays
    testable, matching `full_restart` below.
    """
    cfg = config or load_config()
    _log(f"app restart requested pid={os.getpid()}")
    before_status = runtime_status(cfg)
    before_handles = {str(row.get("handle")) for row in list_terminals(cfg) if row.get("handle")}
    app_pid = before_status.get("app_pid")
    if isinstance(app_pid, int):
        _terminate_exact_pid(app_pid)
    after_start = ensure_runtime(cfg)
    if not after_start.get("ok"):
        raise RuntimeError(f"LiTeWork did not recover after app restart: {after_start}")
    after_handles = {str(row.get("handle")) for row in list_terminals(cfg) if row.get("handle")}
    missing = sorted(before_handles - after_handles)
    if missing:
        raise RuntimeError(f"LiTeWork restart lost terminal sessions: {missing}")
    return {
        "ok": True,
        "level": "app",
        "preserved_terminal_count": len(before_handles),
        "status": after_start.get("status"),
    }


def _default_daemon_pid_file(config: LiteWorkConfig) -> Path:
    if config.profile_dir:
        profile = Path(config.profile_dir)
    elif sys.platform == "win32" and os.environ.get("APPDATA"):
        profile = Path(os.environ["APPDATA"]) / "liwork-dev"
    else:
        profile = Path.home() / ".liwork-dev"
    return profile / "daemon" / "daemon-v34.pid"


def _daemon_pid(config: LiteWorkConfig) -> int | None:
    payload = _read_json(_default_daemon_pid_file(config))
    pid = payload.get("pid")
    return pid if isinstance(pid, int) and pid > 0 else None


def full_restart(config: LiteWorkConfig | None = None) -> dict[str, Any]:
    """Destroy all LiTeWork terminal sessions, daemon and host, then start clean.

    Confirmation, the internal-session refusal and the live-session
    acknowledgement are deliberately enforced by the CLI layer so this function
    can remain testable.  Callers must never expose it as an automatic guard
    action.
    """
    cfg = config or load_config()
    terminals = list_terminals(cfg)
    _log(f"FULL RESTART requested pid={os.getpid()} terminals={len(terminals)}")
    for row in terminals:
        handle = row.get("handle")
        if handle:
            run_cli(["terminal", "close", "--terminal", str(handle), "--json"], config=cfg)
    status = runtime_status(cfg)
    app_pid = status.get("app_pid")
    if isinstance(app_pid, int):
        _terminate_exact_pid(app_pid)
    daemon_pid = _daemon_pid(cfg)
    if daemon_pid:
        _terminate_exact_pid(daemon_pid)
    after = ensure_runtime(cfg)
    if not after.get("ok"):
        raise RuntimeError(f"LiTeWork did not recover after full restart: {after}")
    return {
        "ok": True,
        "level": "full",
        "destroyed_terminal_count": len(terminals),
        "status": after.get("status"),
    }


def guard_status(config: LiteWorkConfig | None = None) -> dict[str, Any]:
    pid = _read_pid(pid_file())
    alive = _pid_alive(pid)
    state = _read_json(state_file())
    result: dict[str, Any] = {
        "alive": alive,
        "pid": pid if alive else None,
        "config_file": str(config_file()),
        "state_file": str(state_file()),
        "log_file": str(log_file()),
        "last_check": state,
    }
    if config is not None:
        result["runtime"] = runtime_status(config)
    return result


def start_guard(config: LiteWorkConfig | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    current = guard_status()
    if current["alive"]:
        return {"ok": True, "already": True, **current}
    stop_file().unlink(missing_ok=True)
    code = (
        "from omnicompany.packages.services._core import litework_host as h; "
        "h.guard_main()"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=cfg.root_path,
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_WINDOWS_DETACHED_FLAGS if sys.platform == "win32" else 0,
        start_new_session=sys.platform != "win32",
        close_fds=True,
    )
    pid_file().write_text(str(proc.pid), encoding="utf-8")
    _log(f"guard started pid={proc.pid}")
    return {"ok": True, "already": False, "alive": True, "pid": proc.pid}


def stop_guard() -> dict[str, Any]:
    stop_file().write_text(_now_iso(), encoding="utf-8")
    pid = _read_pid(pid_file())
    if not _pid_alive(pid):
        pid_file().unlink(missing_ok=True)
        return {"ok": True, "already": True, "alive": False}
    assert pid is not None
    deadline = time.monotonic() + 5.0
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if _pid_alive(pid):
        _terminate_exact_pid(pid)
    pid_file().unlink(missing_ok=True)
    _log(f"guard stopped pid={pid}")
    return {"ok": True, "alive": False, "stopped_pid": pid}


def guard_main() -> None:
    """Detached guard loop.  It only starts missing hosts; it never kills one."""
    with _exclusive_guard_lock() as acquired:
        if not acquired:
            _log(f"guard duplicate refused pid={os.getpid()}")
            return
        try:
            pid_file().write_text(str(os.getpid()), encoding="utf-8")
            cfg = load_config()
            _log(f"guard loop entered pid={os.getpid()} root={cfg.root}")
            while not stop_file().exists():
                try:
                    result = ensure_runtime(cfg)
                    _write_json(state_file(), {"checked_at": _now_iso(), **result})
                    if result.get("action") != "none":
                        _log(f"guard ensure action={result.get('action')} ok={result.get('ok')}")
                except Exception as exc:  # noqa: BLE001 - the loop must survive probe failures
                    _write_json(
                        state_file(),
                        {"checked_at": _now_iso(), "ok": False, "action": "error", "error": str(exc)},
                    )
                    _log(f"guard error {type(exc).__name__}: {exc}")
                deadline = time.monotonic() + cfg.poll_seconds
                while not stop_file().exists() and time.monotonic() < deadline:
                    time.sleep(min(0.5, deadline - time.monotonic()))
        finally:
            if _read_pid(pid_file()) == os.getpid():
                pid_file().unlink(missing_ok=True)
            stop_file().unlink(missing_ok=True)
            _log("guard loop exited")
