# [OMNI] origin=claude-code domain=omnicompany/core/remote ts=2026-10-17T00:00:00+08:00 type=lib status=active
# [OMNI] summary="omni remote 后端: desktop-node 执行(只发一次, 断连只查不重发)、本机替身后端、http-pull/scp/本机复制三种文件通道。"
"""Execution backends and file transports for ``omni remote``.

* ``DesktopNodeBackend`` — signed tasks via the local control plane
  (``POST /api/desktop-nodes/{node}/tasks``). The task id is chosen here, so a dropped
  connection is resolved by looking the task up, never by sending it again.
* ``FakeBackend`` — same interface, runs the very same PowerShell locally with every
  remote path mapped under a local directory (``<fake_root>\\<drive>\\...``). Used by the
  tests and for template checks before touching a real machine.

File transports: ``http-pull`` (remote fetches from a one-shot token HTTP server on this
machine; plain ``Invoke-WebRequest``, nothing encoded), ``scp`` (pre/post copy through the
profile's ssh alias) and ``local-copy`` (fake backend).
"""
from __future__ import annotations

import http.server
import json
import ntpath
import os
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from .profiles import NodeProfile
from .pstext import RemoteRefused
from .store import assert_local_plain, home

MODE_READONLY = "readonly-l1"
MODE_TRUSTED_EXEC = "trusted-exec-l2"
#: control plane + node ceilings (desktop_nodes_core.MODE_MAX_TIMEOUT_MS, remote_transport.rs TTL check)
MODE_MAX_TIMEOUT_S = {MODE_READONLY: 120, MODE_TRUSTED_EXEC: 1800}
MODE_MAX_TTL_S = {MODE_READONLY: 600, MODE_TRUSTED_EXEC: 32 * 60}
TERMINAL = {"completed", "failed", "rejected", "revoked", "expired", "invalid"}
ONLINE_WINDOW_MS = 90_000
PS_ARGV = ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"]


class TransportError(Exception):
    pass


# ── control plane HTTP ─────────────────────────────────────────────────────────

def _base() -> str:
    return os.environ.get("OMNI_DASHBOARD_URL", "http://127.0.0.1:8210").rstrip("/")


def _http(method: str, path: str, payload: dict[str, Any] | None = None, timeout: float = 20.0) -> tuple[int, dict[str, Any]]:
    url = f"{_base()}/api/desktop-nodes{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json", "X-Omnicompany-Actor": "omni remote"}
    token = os.environ.get("OMNI_CONTROLLER_TOKEN", "").strip()
    if token:
        headers["X-Omnicompany-Controller-Auth"] = token
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback control plane
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        try:
            return error.code, json.loads(body)
        except ValueError:
            return error.code, {"ok": False, "error": body[:300] or f"HTTP {error.code}"}
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise TransportError(f"连不上控制端 {_base()}: {error}") from error


def fetch_nodes() -> list[dict[str, Any]]:
    status, body = _http("GET", "")
    if status != 200 or not body.get("ok"):
        raise TransportError(f"列节点失败 (HTTP {status}): {body.get('error') or body}")
    return [node for node in body.get("nodes") or [] if isinstance(node, dict)]


def node_summary(node: dict[str, Any] | None) -> dict[str, Any]:
    if not node:
        return {"paired": False, "online": False}
    last_seen = int(node.get("last_seen_ms") or 0)
    online = bool(last_seen) and int(time.time() * 1000) - last_seen <= ONLINE_WINDOW_MS
    permissions = node.get("permissions") if isinstance(node.get("permissions"), dict) else {}
    approved: dict[str, list[str]] = {}
    status = node.get("status") if isinstance(node.get("status"), dict) else {}
    for grant in status.get("grants") or []:
        if isinstance(grant, dict) and grant.get("enabled") is not False and not grant.get("paused"):
            approved[str(grant.get("mode"))] = list(grant.get("approved_program_ids") or [])
    return {
        "paired": True,
        "label": node.get("label"),
        "online": online,
        "last_seen_s_ago": (int(time.time() * 1000) - last_seen) // 1000 if last_seen else None,
        "granted_modes": list(permissions.get("granted_modes") or []),
        "approved_programs": approved,
        "active_tasks": status.get("active_tasks"),
        "revoked": bool(node.get("revoked")),
    }


class DesktopNodeBackend:
    name = "desktop-node"

    def __init__(self, profile: NodeProfile) -> None:
        self.profile = profile

    def map_path(self, remote: str) -> str:
        return remote

    def status(self) -> dict[str, Any]:
        nodes = {str(n.get("node_id")): n for n in fetch_nodes()}
        return node_summary(nodes.get(self.profile.node_id))

    def exec_ps(self, command: str, *, timeout_s: int, mode: str | None = None) -> dict[str, Any]:
        mode = mode or self.profile.exec_mode
        timeout_s = max(5, min(int(timeout_s), MODE_MAX_TIMEOUT_S[mode]))
        ttl_s = min(timeout_s + 90, MODE_MAX_TTL_S[mode])
        task_id = str(uuid.uuid4())
        payload = {
            "task_id": task_id,
            "mode": mode,
            "program_id": self.profile.shell_program,
            "argv": [*PS_ARGV, command],
            "cwd_relative": ".",
            "timeout_ms": timeout_s * 1000,
            "ttl_ms": ttl_s * 1000,
            "requested_capabilities": ["process.exec"],
        }
        started = time.monotonic()
        errors: list[str] = []
        queued = False
        for _attempt in range(2):
            try:
                status, body = _http("POST", f"/{self.profile.node_id}/tasks", payload)
            except TransportError as error:
                errors.append(str(error))
                try:
                    landed = self._lookup(task_id) is not None
                except TransportError as lookup_error:
                    errors.append(str(lookup_error))
                    landed = False
                if landed:  # the POST landed; never send it again
                    queued = True
                    break
                continue
            if status == 202 and body.get("ok"):
                queued = True
                break
            if status == 409 and body.get("error") == "duplicate_task_id":  # an earlier POST of this same id landed
                queued = True
                break
            return self._receipt(task_id, status="dispatch-failed", started=started, errors=errors,
                                 stderr=f"控制端拒绝下发 (HTTP {status}): {body.get('error') or body}")
        if not queued:
            return self._receipt(task_id, status="dispatch-failed", started=started, errors=errors,
                                 stderr="控制端两次都没接住任务: " + "; ".join(errors))
        deadline = started + ttl_s + timeout_s + 30
        interval = 1.0
        last_status = "queued"
        while True:
            try:
                task = self._lookup(task_id)
            except TransportError as error:
                errors.append(str(error))
                task = None
            if task is not None:
                last_status = str(task.get("status") or last_status)
                if last_status in TERMINAL:
                    receipt = task.get("receipt") if isinstance(task.get("receipt"), dict) else {}
                    return self._receipt(task_id, status=last_status, started=started, errors=errors, native=receipt)
            if time.monotonic() >= deadline:
                return self._receipt(task_id, status="wait-timeout", started=started, errors=errors,
                                     stderr=f"本机等到上限仍未终态, 最后状态 {last_status}; 任务 {task_id} 可能仍在节点上运行")
            time.sleep(interval)
            interval = min(interval + 0.5, 3.0)

    def _lookup(self, task_id: str) -> dict[str, Any] | None:
        status, body = _http("GET", f"/tasks/{task_id}")
        if status == 404:
            return None
        if status != 200 or not body.get("ok"):
            raise TransportError(f"取任务失败 (HTTP {status}): {body.get('error') or body}")
        task = body.get("task")
        return task if isinstance(task, dict) else None

    @staticmethod
    def _receipt(task_id: str, *, status: str, started: float, errors: list[str], native: dict[str, Any] | None = None,
                 stderr: str = "") -> dict[str, Any]:
        native = native or {}
        return {
            "task_id": task_id,
            "status": status,
            "exit_code": native.get("exit_code"),
            "termination": native.get("termination"),
            "stdout": native.get("stdout") or "",
            "stderr": (native.get("stderr") or "") + stderr,
            "duration_ms": native.get("duration_ms"),
            "queue_latency_ms": native.get("queue_latency_ms"),
            "leftover_processes": native.get("leftover_processes"),
            "output_compaction": native.get("output_compaction"),
            "transport_errors": errors,
            "wall_s": round(time.monotonic() - started, 2),
        }

    def transport(self) -> "Transport":
        if self.profile.file_channel == "http-pull":
            if not self.profile.http_pull_host:
                raise RemoteRefused("profile", f"节点画像 {self.profile.name} 用 http-pull 但没有 http_pull_host")
            return HttpPullTransport(self.profile.http_pull_host, self.profile.http_pull_ports)
        if self.profile.file_channel == "scp":
            return ScpTransport(self.profile.ssh_alias)
        raise RemoteRefused("profile", f"desktop-node 后端不支持文件通道 {self.profile.file_channel}")


# ── fake backend ───────────────────────────────────────────────────────────────

def fake_root() -> Path:
    raw = os.environ.get("OMNI_REMOTE_FAKE_ROOT", "").strip()
    return Path(raw) if raw else home() / "fake"


class FakeBackend:
    """Runs the generated PowerShell on this machine, remote paths mapped under ``fake_root()``."""

    name = "fake"

    def __init__(self, profile: NodeProfile, root: Path | None = None) -> None:
        self.profile = profile
        self.root = Path(os.path.abspath(root or fake_root())) / profile.name

    def map_path(self, remote: str) -> str:
        if not remote:
            return remote
        normalized = ntpath.normpath(remote)
        drive, rest = ntpath.splitdrive(normalized)
        if not drive or not drive.endswith(":"):
            raise RemoteRefused("path", f"远端路径必须带盘符: {remote}")
        return str(self.root / drive[0].upper() / rest.lstrip("\\"))

    def status(self) -> dict[str, Any]:
        return {"paired": True, "label": f"fake:{self.profile.name}", "online": True, "granted_modes": [MODE_TRUSTED_EXEC],
                "approved_programs": {MODE_TRUSTED_EXEC: ["powershell"]}, "fake_root": str(self.root)}

    def exec_ps(self, command: str, *, timeout_s: int, mode: str | None = None) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        argv = ["powershell.exe", *PS_ARGV, command]
        env = dict(os.environ, OMNI_REMOTE_FAKE="1")
        # a PSModulePath inherited from pwsh 7 makes Windows PowerShell 5.1 autoload the wrong module copies;
        # without it 5.1 rebuilds its own default, like a fresh desktop-node task
        env.pop("PSModulePath", None)
        task_id = f"fake-{uuid.uuid4()}"
        try:
            proc = subprocess.run(argv, cwd=self.root, capture_output=True, timeout=max(5, int(timeout_s)), env=env)
            termination, code = "completed", proc.returncode
            stdout, stderr = proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as error:
            termination, code = "timeout", None
            stdout, stderr = error.stdout or b"", error.stderr or b""
        if code is not None and code >= 2**31:
            code -= 2**32
        return {
            "task_id": task_id,
            "status": "completed",
            "exit_code": code,
            "termination": termination,
            "stdout": stdout.decode("utf-8", "replace"),
            "stderr": stderr.decode("utf-8", "replace"),
            "duration_ms": int((time.monotonic() - started) * 1000),
            "queue_latency_ms": 0,
            "transport_errors": [],
            "wall_s": round(time.monotonic() - started, 2),
        }

    def transport(self) -> "Transport":
        if self.profile.file_channel == "http-pull":
            return HttpPullTransport("127.0.0.1", self.profile.http_pull_ports)
        return LocalCopyTransport(self)


# ── file transports ────────────────────────────────────────────────────────────

class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "omni-remote/1"

    def log_message(self, *_args: Any) -> None:
        pass

    def _route(self) -> tuple[str | None, str | None]:
        parts = self.path.split("?")[0].strip("/").split("/")
        if len(parts) != 3 or parts[0] != self.server.token:  # type: ignore[attr-defined]
            return None, None
        return parts[1], parts[2]

    def do_GET(self) -> None:  # noqa: N802
        kind, key = self._route()
        path = self.server.files.get(key) if kind == "f" else None  # type: ignore[attr-defined]
        if not path:
            self.send_error(404)
            return
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.end_headers()
        with open(path, "rb") as handle:
            shutil.copyfileobj(handle, self.wfile, 1 << 20)
        self.server.served.add(key)  # type: ignore[attr-defined]

    def do_PUT(self) -> None:  # noqa: N802
        kind, key = self._route()
        dest = self.server.uploads.get(key) if kind == "u" else None  # type: ignore[attr-defined]
        if not dest:
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        part = Path(dest + ".part")
        assert_local_plain(part)
        remaining = length
        with part.open("wb") as handle:
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 1 << 20))
                if not chunk:
                    break
                handle.write(chunk)
                remaining -= len(chunk)
        if remaining:
            part.unlink()
            self.send_error(400, "short body")
            return
        os.replace(part, dest)
        self.server.received.add(key)  # type: ignore[attr-defined]
        self.send_response(204)
        self.end_headers()


class HttpPullTransport:
    """One-shot HTTP server: listens only while the operation runs, paths carry a random token."""

    kind = "in-task"
    name = "http-pull"

    def __init__(self, host: str, ports: tuple[int, int]) -> None:
        self.host = host
        self.ports = ports
        self.token = secrets.token_hex(16)
        self.httpd: http.server.ThreadingHTTPServer | None = None

    def __enter__(self) -> "HttpPullTransport":
        last: Exception | None = None
        for _ in range(30):
            port = secrets.randbelow(self.ports[1] - self.ports[0]) + self.ports[0]
            try:
                httpd = http.server.ThreadingHTTPServer((self.host, port), _Handler)
                break
            except OSError as error:
                last = error
        else:
            raise TransportError(f"http-pull: {self.host} 上 {self.ports} 端口段都占用了: {last}")
        httpd.token = self.token  # type: ignore[attr-defined]
        httpd.files = {}  # type: ignore[attr-defined]
        httpd.uploads = {}  # type: ignore[attr-defined]
        httpd.received = set()  # type: ignore[attr-defined]
        httpd.served = set()  # type: ignore[attr-defined]
        self.httpd = httpd
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()

    @property
    def base(self) -> str:
        assert self.httpd is not None
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}/{self.token}"

    def offer(self, path: Path) -> str:
        assert self.httpd is not None
        key = secrets.token_hex(8)
        self.httpd.files[key] = str(path)  # type: ignore[attr-defined]
        return f"{self.base}/f/{key}"

    def expect(self, dest: Path) -> tuple[str, str]:
        assert self.httpd is not None
        key = secrets.token_hex(8)
        self.httpd.uploads[key] = str(dest)  # type: ignore[attr-defined]
        return key, f"{self.base}/u/{key}"

    def got(self, key: str) -> bool:
        return self.httpd is not None and key in self.httpd.received  # type: ignore[attr-defined]


class _PreCopyTransport:
    kind = "pre-copy"
    name = "pre-copy"

    def __enter__(self) -> "_PreCopyTransport":
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


class ScpTransport(_PreCopyTransport):
    """scp through the profile's ssh alias. Only copies; never runs remote commands."""

    name = "scp"

    def __init__(self, alias: str, timeout_s: int = 600) -> None:
        self.alias = alias
        self.timeout_s = timeout_s

    @staticmethod
    def _remote(path: str) -> str:
        return path.replace("\\", "/")

    def _run(self, argv: list[str]) -> dict[str, Any]:
        started = time.monotonic()
        try:
            proc = subprocess.run(argv, capture_output=True, timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            raise TransportError(f"scp 超时 {self.timeout_s}s: {' '.join(argv)}") from None
        except FileNotFoundError as error:
            raise TransportError(f"本机没有 scp: {error}") from error
        receipt = {"argv": argv, "exit_code": proc.returncode, "stderr": proc.stderr.decode("utf-8", "replace")[-2000:],
                   "wall_s": round(time.monotonic() - started, 2)}
        if proc.returncode != 0:
            raise TransportError(f"scp 失败 exit={proc.returncode}: {receipt['stderr'].strip()}")
        return receipt

    def copy_dir_into(self, local_dir: Path, remote_parent: str) -> dict[str, Any]:
        return self._run(["scp", "-q", "-B", "-r", str(local_dir), f"{self.alias}:{self._remote(remote_parent)}/"])

    def copy_file_out(self, remote_file: str, local_file: Path) -> dict[str, Any]:
        assert_local_plain(local_file)
        return self._run(["scp", "-q", "-B", f"{self.alias}:{self._remote(remote_file)}", str(local_file)])


class LocalCopyTransport(_PreCopyTransport):
    name = "local-copy"

    def __init__(self, backend: FakeBackend) -> None:
        self.backend = backend

    def copy_dir_into(self, local_dir: Path, remote_parent: str) -> dict[str, Any]:
        # remote_parent is already mapped by the caller
        target = Path(remote_parent) / local_dir.name
        if target.exists():
            raise TransportError(f"local-copy: target exists: {target}")
        shutil.copytree(local_dir, target)
        return {"copied": str(target)}

    def copy_file_out(self, remote_file: str, local_file: Path) -> dict[str, Any]:
        assert_local_plain(local_file)
        shutil.copyfile(remote_file, local_file)
        return {"copied": str(local_file)}


Transport = HttpPullTransport | ScpTransport | LocalCopyTransport


def make_backend(profile: NodeProfile, backend: str | None = None) -> DesktopNodeBackend | FakeBackend:
    choice = (backend or os.environ.get("OMNI_REMOTE_BACKEND") or "desktop-node").strip()
    if choice == "fake":
        return FakeBackend(profile)
    if choice == "desktop-node":
        return DesktopNodeBackend(profile)
    raise RemoteRefused("backend", f"不认识的后端: {choice}（desktop-node 或 fake）")
