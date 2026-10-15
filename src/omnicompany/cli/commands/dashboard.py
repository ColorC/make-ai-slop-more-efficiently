# [OMNI] origin=ai-ide ts=2026-06-11 type=cli
# [OMNI] material_id="material:cli.dashboard.hot_update_verbs.py"
"""omni dashboard — 驾驶舱免重启更新的触发入口 (CLI 黄金范式).

痛点 (用户 2026-06-11): 改完 dashboard 要重载整个 VSCode, 所有 claude/codex 会话陪葬.
方案: 三层各自免重启更新, 本命令组是统一触发面:

  omni dashboard status      # 8210/8201 健康 + ui/ext 版本 token
  omni dashboard ui-update   # 前端 npm build → 产物哈希变 → 页面 3s 内自刷新
  omni dashboard ui-reload   # 不重新构建, 强制所有打开的页面刷新
  omni dashboard ext-update  # 扩展 impl 重编译 → loader 5s 内热换, 不重启扩展宿主
  omni dashboard ext-reload  # 不重新编译, 强制 loader 热换一次 impl
  omni dashboard restart     # 重启 dashboard 进程 (8210); 绝不碰 ccdaemon (8201, 会话所在)

版本信号总线: dashboard/controlplane/dev_reload.py; 网页轮询: frontend/src/lib/devReload.ts;
扩展热换: extensions/vscode-chat-sidebar/src/loader.ts.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import click

from .._access import any_caller

_DASHBOARD_PORT = int(os.environ.get("OMNI_DASHBOARD_PORT", "8210"))
_DAEMON_PORT = int(os.environ.get("OMNI_CC_DAEMON_PORT", "8201"))
_DASHBOARD_MUTEX_NAME = r"Local\OmnicompanyDashboardLifecycle"
_DASHBOARD_MUTEX_TIMEOUT_SECONDS = 30.0
_dashboard_lifecycle_thread_lock = threading.Lock()


def _root() -> Path:
    from omnicompany.core.config import omni_workspace_root
    return omni_workspace_root()


def _dashboard_dir() -> Path:
    return _root() / "src" / "omnicompany" / "dashboard"


@contextmanager
def _dashboard_lifecycle_lock(timeout: float = _DASHBOARD_MUTEX_TIMEOUT_SECONDS):
    """Serialize every dashboard stop/start operation across CLI processes."""

    if os.name != "nt":
        if not _dashboard_lifecycle_thread_lock.acquire(timeout=timeout):
            raise click.ClickException("dashboard lifecycle lock timed out after 30 seconds")
        try:
            yield
        finally:
            _dashboard_lifecycle_thread_lock.release()
        return

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateMutexW(None, False, _DASHBOARD_MUTEX_NAME)
    if not handle:
        raise click.ClickException("could not create dashboard lifecycle mutex")
    wait_result = kernel32.WaitForSingleObject(handle, int(timeout * 1000))
    if wait_result not in (0x00000000, 0x00000080):  # WAIT_OBJECT_0 / WAIT_ABANDONED_0
        kernel32.CloseHandle(handle)
        raise click.ClickException("dashboard lifecycle lock timed out after 30 seconds")
    try:
        yield
    finally:
        kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)


def _notes_web_dir() -> Path:
    # 唯一画布前端真源: 2026-09-14 起 dashboard 与 Spark Workshop 共用 webworks/apps/material-canvas
    # (dashboard/notes-web 是 08-17 迁入后分叉出去的旧副本, 已不再构建或服务)。
    return _dashboard_dir().parents[3] / "webworks" / "apps" / "material-canvas"


def _overlay_web_stale() -> bool:
    """札记网页产物(dist-web)是否落后于输入。

    dist-web 由 webworks/apps/material-canvas 的 vite.web.config.ts 出包:
    源码新、产物旧时 /lofa/overlay/app/ 静默服务旧画布, 是一整批"改了没生效"
    bug 的来源。这里以 dist-web/notes-web.html 的 mtime 为基准, 监视源码、
    入口、构建配置、package.json 与 webworks 内核依赖(file: 链接)。
    """
    overlay = _notes_web_dir()
    marker = overlay / "dist-web" / "notes-web.html"
    if not marker.is_file():
        return True
    try:
        marker_mtime = marker.stat().st_mtime
    except OSError:
        return True
    watch: list[Path] = [
        overlay / "src",
        overlay / "notes-web.html",
        overlay / "vite.web.config.ts",
        overlay / "package.json",
        _notes_web_dir().parents[1] / "packages" / "material-note-core",
        _notes_web_dir().parents[1] / "packages" / "material-core",
    ]
    skip_dirs = {"node_modules", "dist", "dist-web", "target", "test", "tests"}
    for root in watch:
        if not root.exists():
            continue
        if root.is_file():
            if root.stat().st_mtime > marker_mtime:
                return True
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for name in filenames:
                try:
                    if (Path(dirpath) / name).stat().st_mtime > marker_mtime:
                        return True
                except OSError:
                    continue
    return False


def _http_json(method: str, url: str, body: dict | None = None, timeout: float = 3.0) -> dict | None:
    """无三方依赖的本地 HTTP 小工具; 不可达返回 None."""
    import urllib.request

    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _versions() -> dict | None:
    return _http_json("GET", f"http://127.0.0.1:{_DASHBOARD_PORT}/api/dev/versions")


def _bump(target: str) -> dict | None:
    return _http_json("POST", f"http://127.0.0.1:{_DASHBOARD_PORT}/api/dev/bump", {"target": target})


def _run_build(
    cwd: Path,
    args: list[str],
    label: str,
    *,
    env: dict[str, str] | None = None,
) -> None:
    click.echo(f"[{label}] {' '.join(args)}  (cwd={cwd})")
    # CREATE_NO_WINDOW: npm/cmd 子进程别弹前台窗(用户硬规则)。Dashboard 构建是后台维护，
    # Windows 上降一级优先级，避免 tsc/terser 与正在交互的 PTY/agent 抢时间片。
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if os.name == "nt":
        creationflags |= getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    proc = subprocess.run(args, cwd=str(cwd), shell=(os.name == "nt"), env=env,
                          creationflags=creationflags)
    if proc.returncode != 0:
        click.echo(json.dumps({"ok": False, "step": label, "returncode": proc.returncode}, ensure_ascii=False))
        raise SystemExit(proc.returncode)


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _deploy_ui_build(staging: Path, live: Path) -> None:
    """Overlay a complete staged build and switch ``index.html`` last.

    Vite's default ``emptyOutDir`` created a long interval where the running
    Dashboard had no bundle at all. Content-hashed assets can coexist safely,
    so publish them first and atomically replace the entry only after every
    referenced file is ready. Old assets get a one-day grace period for pages
    that were already open when a newer build landed.
    """
    index_source = staging / "index.html"
    if not index_source.is_file():
        raise click.ClickException("ui build produced no index.html")
    live.mkdir(parents=True, exist_ok=True)
    current_files: set[Path] = set()
    for source in staging.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(staging)
        current_files.add(relative)
        if relative.as_posix() == "index.html":
            continue
        _atomic_copy(source, live / relative)
    _atomic_copy(index_source, live / "index.html")

    cutoff = time.time() - 24 * 60 * 60
    assets = live / "assets"
    if assets.is_dir():
        for old_file in assets.rglob("*"):
            if not old_file.is_file():
                continue
            relative = old_file.relative_to(live)
            try:
                stale = old_file.stat().st_mtime < cutoff
            except OSError:
                continue
            if relative not in current_files and stale:
                old_file.unlink(missing_ok=True)


@click.group("dashboard")
def cmd_dashboard() -> None:
    """驾驶舱免重启更新 (status / ui-update / ui-reload / ext-update / ext-reload / restart / inspect)。"""


@cmd_dashboard.command("status")
@any_caller
def cmd_dashboard_status() -> None:
    """dashboard/ccdaemon 健康 + 当前 ui/ext 版本 token。"""
    dash = _http_json("GET", f"http://127.0.0.1:{_DASHBOARD_PORT}/api/cc/chat/health")
    daemon = _http_json("GET", f"http://127.0.0.1:{_DAEMON_PORT}/cc/chat/health")
    click.echo(json.dumps({
        "dashboard": {"port": _DASHBOARD_PORT, "ready": dash is not None},
        "ccdaemon": {"port": _DAEMON_PORT, "ready": daemon is not None},
        "versions": _versions(),
    }, ensure_ascii=False, indent=2))


@cmd_dashboard.command("observe")
@click.option("--json-out", "as_json", is_flag=True, help="输出原始 JSON(给 Agent 消费)。")
@click.option("--errors", "errors_only", is_flag=True, help="只看错误/警告事件。")
@click.option("--limit", type=click.IntRange(1, 500), default=40, show_default=True,
              help="返回最近多少条事件。")
@any_caller
def cmd_dashboard_observe(as_json: bool, errors_only: bool, limit: int) -> None:
    """观测在线前端实例与最近事件 —— 远端用户「画布打不开」类问题的第一入口。

    数据源是 dashboard 内存 ring(POST /api/diagnostics/frontend/events 上报,
    落盘 logs/frontend-telemetry.jsonl)。看谁在线、从哪个入口(origin)、
    浏览器里正在报什么错 —— 不用再从网关日志反着猜。
    """
    level = "error,warn" if errors_only else ""
    query = f"?limit={limit}" + (f"&level={level}" if level else "")
    payload = _http_json("GET", f"http://127.0.0.1:{_DASHBOARD_PORT}/api/diagnostics/frontend{query}", timeout=5.0)
    if payload is None:
        click.echo(json.dumps({"ok": False, "reason": "dashboard 不可达或观测端点未部署(需 restart)"},
                              ensure_ascii=False))
        raise SystemExit(1)
    backend = _http_json(
        "GET",
        f"http://127.0.0.1:{_DASHBOARD_PORT}/api/diagnostics/backend",
        timeout=5.0,
    )
    payload["backend"] = backend
    if as_json:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    import datetime as _dt

    click.echo("── 在线前端实例(近 5 分钟有心跳) ──")
    online = [i for i in payload.get("instances", []) if i.get("online")]
    if not online:
        click.echo("  (无 —— 用户可能还没刷新到带观测的版本, 或入口不可达)")
    for inst in online:
        last = _dt.datetime.fromtimestamp(float(inst.get("last_seen", 0))).strftime("%H:%M:%S")
        click.echo(
            f"  [{str(inst.get('surface', '?')):>13}] {str(inst.get('origin', '?')):40} "
            f"错误 {inst.get('errors', 0):>3} 事件 {inst.get('events', 0):>4}  最后 {last}\n"
            f"                最近: {inst.get('last_event', '')}"
        )
    click.echo("── 最近事件 ──")
    events = payload.get("events", [])
    if not events:
        click.echo("  (无)")
    for ev in events:
        ts = _dt.datetime.fromtimestamp(float(ev.get("t", 0))).strftime("%H:%M:%S")
        click.echo(f"  {ts} {str(ev.get('level', '?')):<5} {str(ev.get('kind', '?')):<22} {str(ev.get('message', ''))[:150]}")
    click.echo("── 后端排队与代码归因 ──")
    if not backend:
        click.echo("  (后端诊断端点尚未部署；重启 Dashboard 后可用)")
    else:
        current = backend.get("current") or {}
        aggregate = backend.get("aggregate") or {}
        click.echo(
            f"  inflight={current.get('inflight', 0)} "
            f"executor_queue={current.get('executor_queue_depth', 0)} "
            f"loop_lag={current.get('event_loop_lag_ms', 0)}ms "
            f"loop_lag_p95={aggregate.get('event_loop_lag_p95_ms')}ms"
        )
        for rec in list(backend.get("recent_slow") or [])[-5:]:
            click.echo(
                f"  {rec.get('duration_ms')}ms {rec.get('path')} → {rec.get('handler')} "
                f"session={rec.get('session') or '-'} queue={rec.get('executor_queue_start', 0)}"
            )
    click.echo("── 材料入口 ──\n  前端观测落盘: logs/frontend-telemetry.jsonl | 后端慢/错: logs/dashboard-slow-errors.jsonl")


@cmd_dashboard.command("uploads")
@click.option("--limit", type=click.IntRange(1, 200), default=20, show_default=True,
              help="返回最近多少个上传批次。")
@click.option("--paths-only", is_flag=True,
              help="只输出仍可直接交给 Agent 使用的绝对文件路径。")
@any_caller
def cmd_dashboard_uploads(limit: int, paths_only: bool) -> None:
    """查询浏览器/手机投递到 Agent 暂存区的上传记录。"""
    from omnicompany.dashboard.controlplane.file_bridge import (
        read_upload_history,
        upload_history_path,
    )

    batches = read_upload_history(limit)
    if paths_only:
        for batch in batches:
            for item in batch.get("items") or []:
                path = item.get("path")
                if path:
                    click.echo(path)
        return
    click.echo(json.dumps({
        "ok": True,
        "history_path": str(upload_history_path()),
        "batches": batches,
        "hint": "只取路径: omni dashboard uploads --paths-only",
    }, ensure_ascii=False, indent=2))


@cmd_dashboard.command("ui-update")
@any_caller
def cmd_dashboard_ui_update() -> None:
    """前端 npm build; 产物哈希变化后所有打开的页面 3s 内自刷新。"""
    before = (_versions() or {}).get("ui")
    dashboard_dir = _dashboard_dir()
    frontend = dashboard_dir / "frontend"
    staging = _root() / ".omni" / "tmp" / "dashboard-ui-build" / f"{os.getpid()}-{time.time_ns()}"
    build_env = dict(os.environ)
    build_env["OMNI_DASHBOARD_BUILD_OUT_DIR"] = str(staging)
    try:
        _run_build(frontend, ["npm", "run", "build"], "ui-build", env=build_env)
        _deploy_ui_build(staging, dashboard_dir / "static")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    # 札记网页产物与 dashboard UI 同链发布: dist-web 落后于源码(或 webworks 内核)时重建,
    # 并 bump ui epoch — dashboard index.html 没变也要让页面重拿新画布产物。
    overlay_built = False
    overlay = _notes_web_dir()
    if (overlay / "vite.web.config.ts").is_file() and _overlay_web_stale():
        _run_build(overlay, ["npm", "run", "build"], "notes-web-build")
        overlay_built = True
        _bump("ui")
    after = (_versions() or {}).get("ui")
    changed = before is not None and after is not None and before != after
    if after is not None and not changed:
        # 构建产物没变 (无实质改动) 时页面不会自刷; 如确需强刷用 ui-reload
        click.echo(json.dumps({"ok": True, "built": True, "changed": False,
                               "hint": "产物未变化, 页面不刷新; 强刷用 omni dashboard ui-reload"}, ensure_ascii=False))
        return
    click.echo(json.dumps({"ok": True, "built": True, "changed": changed, "overlay_built": overlay_built,
                           "ui_before": before, "ui_after": after}, ensure_ascii=False))


@cmd_dashboard.command("ui-reload")
@any_caller
def cmd_dashboard_ui_reload() -> None:
    """不重新构建, 强制所有打开的 dashboard 页面刷新 (含 VSCode webview 里的 iframe)。"""
    res = _bump("ui")
    if res is None:
        click.echo(json.dumps({"ok": False, "error": f"dashboard ({_DASHBOARD_PORT}) 不可达"}, ensure_ascii=False))
        raise SystemExit(1)
    click.echo(json.dumps(res, ensure_ascii=False))


@cmd_dashboard.command("ext-update")
@any_caller
def cmd_dashboard_ext_update() -> None:
    """重编译 VSCode 扩展 impl 层; loader 5s 内热换, 不重启扩展宿主。"""
    ext_dir = _dashboard_dir() / "extensions" / "vscode-chat-sidebar"
    before = (_versions() or {}).get("ext")
    _run_build(ext_dir, ["npm", "run", "compile"], "ext-compile")
    after = (_versions() or {}).get("ext")
    changed = before is not None and after is not None and before != after
    if after is not None and not changed:
        click.echo(json.dumps({"ok": True, "built": True, "changed": False,
                               "hint": "impl.js 未变化, loader 不热换; 强制热换用 omni dashboard ext-reload"}, ensure_ascii=False))
        return
    click.echo(json.dumps({"ok": True, "built": True, "changed": changed,
                           "ext_before": before, "ext_after": after}, ensure_ascii=False))


@cmd_dashboard.command("ext-reload")
@any_caller
def cmd_dashboard_ext_reload() -> None:
    """不重新编译, 强制扩展 loader 热换一次 impl (调试用)。"""
    res = _bump("ext")
    if res is None:
        click.echo(json.dumps({"ok": False, "error": f"dashboard ({_DASHBOARD_PORT}) 不可达"}, ensure_ascii=False))
        raise SystemExit(1)
    click.echo(json.dumps(res, ensure_ascii=False))


def _kill_port_win(port: int) -> None:
    script = (
        f"$conns = Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue; "
        "$pids = @($conns | Select-Object -ExpandProperty OwningProcess -Unique); "
        "foreach ($pidToCheck in $pids) { "
        "$proc = Get-CimInstance Win32_Process -Filter \"ProcessId=$pidToCheck\" "
        "-ErrorAction SilentlyContinue; "
        "if ($proc -and $proc.CommandLine -like '*omnicompany.dashboard.app:app*') { "
        "Stop-Process -Id $pidToCheck -Force -ErrorAction Stop "
        "} "
        "}"
    )
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit={proc.returncode}"
        raise click.ClickException(f"dashboard restart preflight failed: {detail}")


def _is_dashboard_command(command: str, port: int) -> bool:
    """Only match this dashboard's uvicorn entry and its exact listen port."""
    if not re.search(
        r"(?:^|\s)-m\s+uvicorn\s+omnicompany\.dashboard\.app:app(?:\s|$)",
        command,
        flags=re.IGNORECASE,
    ):
        return False
    return re.search(
        rf"(?:^|\s)--port(?:\s+|=){port}(?:\s|$)",
        command,
        flags=re.IGNORECASE,
    ) is not None


def _dashboard_pids_win(port: int) -> list[int]:
    """Find every live dashboard instance, including ones that already closed the port.

    Uvicorn closes its listening socket before application shutdown finishes. Looking only
    at Get-NetTCPConnection therefore misses shutdown-hung instances and every restart adds
    another CPU-consuming process.
    """
    script = (
        "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
        "Where-Object { $_.CommandLine -like '*omnicompany.dashboard.app:app*' } | "
        "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"
    )
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        return []
    found: list[int] = []
    for line in proc.stdout.splitlines():
        raw_pid, separator, command = line.partition("\t")
        if not separator or not raw_pid.strip().isdigit():
            continue
        if _is_dashboard_command(command.strip(), port):
            found.append(int(raw_pid.strip()))
    return sorted(set(found))


def _alive_pids_win(pids: list[int]) -> list[int]:
    if not pids:
        return []
    joined = ",".join(str(pid) for pid in sorted(set(pids)))
    script = (
        f"Get-Process -Id @({joined}) -ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty Id"
    )
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        return []
    return sorted({int(line.strip()) for line in proc.stdout.splitlines() if line.strip().isdigit()})


def _kill_pids_win(pids: list[int]) -> None:
    if not pids:
        return
    joined = ",".join(str(pid) for pid in sorted(set(pids)))
    script = f"Stop-Process -Id @({joined}) -Force -ErrorAction SilentlyContinue"
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )


def _reap_dashboard_pids_win(pids: list[int], timeout: float = 2.0) -> list[int]:
    """Give graceful shutdown a moment, then terminate only the captured old instances."""
    deadline = time.time() + timeout
    remaining = _alive_pids_win(pids)
    while remaining and time.time() < deadline:
        time.sleep(0.2)
        remaining = _alive_pids_win(remaining)
    if remaining:
        _kill_pids_win(remaining)
    return remaining


def _graceful_stop(port: int, timeout: float = 6.0) -> bool:
    """先请后端自己走 shutdown 流程; 端口在 timeout 内关掉就算成功。

    硬杀的代价是 WebSocket 不发关闭帧(前端要等 TCP 超时)、写状态文件的 os.replace
    可能停在半路、以及冷启比优雅重启慢导致反代 502 的窗口更长。所以默认先礼后兵。
    后端不支持该端点(旧进程)或没在听, 直接返回 False 交给强杀, 不阻塞重启。
    """
    if _http_json("POST", f"http://127.0.0.1:{port}/api/dev/shutdown", body={}, timeout=2.0) is None:
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _port_listening(port):
            return True
        time.sleep(0.2)
    return False


def _port_listening(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _rotate_log(path: Path, max_bytes: int = 32 * 1024 * 1024, keep: int = 2) -> None:
    """超过 max_bytes 就转存, 只留 keep 份历史。

    这两个日志是以 append 模式跨重启共用同一文件的, 从来没有人轮转 ——
    实测 out.log 长到 290MB(err 3.2MB)。日志本身不会拖垮服务, 但它会让
    "翻日志定位问题" 这件事变得没法做, 也在慢慢吃盘。
    """
    try:
        if not path.exists() or path.stat().st_size < max_bytes:
            return
        for idx in range(keep, 0, -1):
            older = path.with_suffix(path.suffix + f".{idx}")
            newer = path if idx == 1 else path.with_suffix(path.suffix + f".{idx - 1}")
            if newer.exists():
                if older.exists():
                    older.unlink()
                newer.rename(older)
    except OSError:
        # 轮转失败绝不能挡住重启 —— 日志是辅助, 服务可用性优先。
        pass


def _dashboard_log_paths(root: Path) -> tuple[Path, Path]:
    log_dir = root / "data"
    return (
        (log_dir / f"live_dashboard_{_DASHBOARD_PORT}.out.log").absolute(),
        (log_dir / f"live_dashboard_{_DASHBOARD_PORT}.err.log").absolute(),
    )


def _ipv4_listener_pids(port: int) -> list[int]:
    """Return IPv4 listener owners once, without stopping any process."""

    if os.name == "nt":
        script = (
            f"Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | "
            "Where-Object { $_.LocalAddress -in @('127.0.0.1', '0.0.0.0') } | "
            "Select-Object -ExpandProperty OwningProcess -Unique"
        )
        try:
            proc = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                capture_output=True,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
                timeout=8,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if proc.returncode != 0:
            return []
        return sorted({int(line.strip()) for line in proc.stdout.splitlines() if line.strip().isdigit()})

    # psutil is already an optional runtime dependency in the dashboard stack;
    # keep this read-only fallback small and do not introduce a new dependency.
    try:
        import psutil
    except ImportError:
        return []
    pids: set[int] = set()
    try:
        for conn in psutil.net_connections(kind="tcp4"):
            if (
                conn.status == psutil.CONN_LISTEN
                and conn.laddr
                and conn.laddr.port == port
                and conn.laddr.ip in {"127.0.0.1", "0.0.0.0"}
                and conn.pid is not None
            ):
                pids.add(int(conn.pid))
    except (OSError, psutil.Error):
        return []
    return sorted(pids)


def _start_dashboard(root: Path, *, stop: str, reaped_pids: list[int] | None = None) -> dict:
    """Start one hidden dashboard and return a receipt with absolute log paths."""

    out_path, err_path = _dashboard_log_paths(root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _rotate_log(out_path)
    _rotate_log(err_path)
    out_log = open(out_path, "ab")
    err_log = open(err_path, "ab")
    env = {**os.environ, "PYTHONPATH": str(root / "src"), "OMNI_WORKSPACE_ROOT": str(root)}
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0) if os.name == "nt" else 0
    # LOFA: 默认仍 127.0.0.1(不破坏现状); 设 OMNI_DASHBOARD_HOST=0.0.0.0 开放局域网给安卓端。
    host = os.environ.get("OMNI_DASHBOARD_HOST", "127.0.0.1")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "omnicompany.dashboard.app:app",
         "--host", host, "--port", str(_DASHBOARD_PORT), "--log-level", "info",
         "--no-access-log"],
        cwd=str(root), env=env, stdout=out_log, stderr=err_log,
        creationflags=creationflags, close_fds=True,
        start_new_session=(os.name != "nt"),
    )
    # Popen has duplicated the descriptors; close the CLI copies immediately.
    out_log.close()
    err_log.close()

    deadline = time.time() + 30
    ready = False
    actual_pid: int | None = None
    readiness_error: str | None = None
    while time.time() < deadline:
        if _http_json("GET", f"http://127.0.0.1:{_DASHBOARD_PORT}/api/cc/chat/health", timeout=1.5) is not None:
            listener_pids = _ipv4_listener_pids(_DASHBOARD_PORT)
            actual_pid = listener_pids[0] if len(listener_pids) == 1 else None
            if proc.poll() is not None:
                readiness_error = "dashboard health responded after the newly started process had already exited"
            elif listener_pids == [proc.pid]:
                ready = True
            else:
                readiness_error = (
                    "dashboard health responded, but the new process does not own the IPv4 listener"
                )
            break
        time.sleep(0.5)
    if not ready and readiness_error is None:
        readiness_error = "dashboard health did not become ready within 30 seconds"
    result = {
        "ok": ready,
        "ready": ready,
        "pid": proc.pid,
        "requested_pid": proc.pid,
        "actual_pid": actual_pid,
        "port": _DASHBOARD_PORT,
        "started": True,
        "stop": stop,
        "reaped_stale_pids": reaped_pids or [],
        "out_log_path": str(out_path),
        "err_log_path": str(err_path),
        "versions": _versions() if ready else None,
        "note": "ccdaemon (8201) 未受影响, 会话存活" if ready else readiness_error,
    }
    if not ready:
        result["error"] = readiness_error
    return result


def _restart_dashboard_locked(root: Path) -> dict:
    """Restart while the caller holds the lifecycle lock."""

    prior_pids = _dashboard_pids_win(_DASHBOARD_PORT) if os.name == "nt" else []
    if _port_listening(_DASHBOARD_PORT) and os.name == "nt" and not prior_pids:
        raise click.ClickException(
            f"dashboard port {_DASHBOARD_PORT} is occupied by an unknown process; refusing to kill caddy"
        )
    # 先礼: 让后端自己收尾(关 WebSocket、跑 lifespan shutdown), 空窗更短、状态文件不会写一半。
    graceful = _graceful_stop(_DASHBOARD_PORT)
    reaped_pids: list[int] = []
    if os.name == "nt" and prior_pids:
        # 端口关闭不等于进程退出。精确回收重启前捕获的实例，包含历次遗留的
        # shutdown-hung Uvicorn；绝不匹配 ccdaemon 或其他端口的服务。
        reaped_pids = _reap_dashboard_pids_win(prior_pids)
    elif not graceful:
        # 后兵: 旧进程不认这个端点、或收尾超时, 照旧强杀 —— 重启绝不能因为收尾失败而卡住。
        if os.name == "nt":
            _kill_port_win(_DASHBOARD_PORT)
        else:
            subprocess.run(["bash", "-c", f"lsof -ti:{_DASHBOARD_PORT} | xargs -r kill -9"], capture_output=True)
        time.sleep(0.8)
    return _start_dashboard(
        root,
        stop="graceful" if graceful else "forced",
        reaped_pids=reaped_pids,
    )


@cmd_dashboard.command("restart")
@any_caller
def cmd_dashboard_restart() -> None:
    """串行重启 dashboard 进程 (8210)，绝不碰 ccdaemon (8201)。"""

    with _dashboard_lifecycle_lock():
        result = _restart_dashboard_locked(_root())
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ready"]:
        raise SystemExit(1)


def _ensure_dashboard_locked(root: Path) -> dict:
    """Ensure a healthy dashboard without stopping a healthy instance."""

    health = _http_json("GET", f"http://127.0.0.1:{_DASHBOARD_PORT}/api/cc/chat/health", timeout=1.5)
    if health is not None:
        pids = _dashboard_pids_win(_DASHBOARD_PORT) if os.name == "nt" else []
        if os.name == "nt" and not pids:
            raise click.ClickException(
                f"dashboard port {_DASHBOARD_PORT} returned health data but its process identity was not proven; refusing to kill caddy"
            )
        out_path, err_path = _dashboard_log_paths(root)
        return {
            "ok": True,
            "ready": True,
            "pid": pids[0] if pids else None,
            "port": _DASHBOARD_PORT,
            "started": False,
            "restarted": False,
            "out_log_path": str(out_path),
            "err_log_path": str(err_path),
            "note": "dashboard already healthy; no process was stopped",
        }

    listening = _port_listening(_DASHBOARD_PORT)
    identified = _dashboard_pids_win(_DASHBOARD_PORT) if os.name == "nt" else []
    if listening and not identified:
        raise click.ClickException(
            f"dashboard port {_DASHBOARD_PORT} is occupied by an unknown process; refusing to kill caddy"
        )

    if listening and identified:
        deadline = time.time() + 30.0
        while time.time() < deadline:
            if _http_json("GET", f"http://127.0.0.1:{_DASHBOARD_PORT}/api/cc/chat/health", timeout=1.5) is not None:
                out_path, err_path = _dashboard_log_paths(root)
                return {
                    "ok": True,
                    "ready": True,
                    "pid": identified[0],
                    "port": _DASHBOARD_PORT,
                    "started": False,
                    "restarted": False,
                    "out_log_path": str(out_path),
                    "err_log_path": str(err_path),
                    "note": "dashboard became healthy during the bounded readiness window; no process was stopped",
                }
            time.sleep(0.5)
        # Re-prove identity after the wait before allowing restart.
        if not (_dashboard_pids_win(_DASHBOARD_PORT) if os.name == "nt" else []):
            raise click.ClickException("dashboard readiness expired and the exact dashboard process identity disappeared")
        result = _restart_dashboard_locked(root)
        result["restarted"] = True
        return result

    return _start_dashboard(root, stop="none")


@cmd_dashboard.command("ensure")
@click.option("--hidden", is_flag=True, hidden=True)
@any_caller
def cmd_dashboard_ensure(hidden: bool) -> None:
    """确保 dashboard 健康运行；健康实例不停止。"""

    del hidden  # The option documents the hidden-script caller without changing semantics.
    with _dashboard_lifecycle_lock():
        result = _ensure_dashboard_locked(_root())
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ready"]:
        raise SystemExit(1)


def _desktop_inspect_endpoint() -> tuple[str, Path] | None:
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return None
    path = Path(appdata) / "cc.colorc.omnicompany.desktop" / "inspect.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    port = int(data.get("port") or 0)
    if port <= 0:
        return None
    return f"http://127.0.0.1:{port}", path


def _wait_inspect_command(command_id: str, timeout: float = 30.0) -> dict | None:
    deadline = time.time() + timeout
    latest: dict | None = None
    while time.time() < deadline:
        latest = _http_json(
            "GET",
            f"http://127.0.0.1:{_DASHBOARD_PORT}/api/clients/desktop/inspect/command/{command_id}",
            timeout=2.0,
        )
        if latest and latest.get("command", {}).get("status") in {"ok", "error"}:
            return latest
        time.sleep(0.25)
    return latest


def _queue_page_inspect_command(kind: str, payload: dict, client_id: str | None = None) -> dict | None:
    body: dict = {"kind": kind, "payload": payload}
    if client_id:
        body["clientId"] = client_id
    return _http_json(
        "POST",
        f"http://127.0.0.1:{_DASHBOARD_PORT}/api/clients/desktop/inspect/command",
        body,
        timeout=5.0,
    )


@cmd_dashboard.group("inspect")
def cmd_dashboard_inspect() -> None:
    """看桌面客户端/网页驾驶舱的原本视角，并做远程网页操作。"""


@cmd_dashboard_inspect.command("status")
@any_caller
def cmd_dashboard_inspect_status() -> None:
    """列出已连接的 inspect 页面，以及桌面 loopback 是否在线。"""
    desktop = _desktop_inspect_endpoint()
    desktop_health = None
    if desktop is not None:
        desktop_health = _http_json("GET", f"{desktop[0]}/health")
    pages = _http_json("GET", f"http://127.0.0.1:{_DASHBOARD_PORT}/api/clients/desktop/inspect/clients")
    click.echo(json.dumps({
        "ok": True,
        "desktop": {
            "endpoint": desktop[0] if desktop else None,
            "health": desktop_health,
        },
        "pages": pages,
    }, ensure_ascii=False, indent=2))


@cmd_dashboard_inspect.command("screenshot")
@click.option("--out", "out_path", type=click.Path(path_type=Path), default=None,
              help="把 PNG 写到这个路径；省略则写到 data/runtime/inspect-latest.png")
@click.option("--client-id", default=None, help="精确指定 status 列出的页面实例；指定后不走桌面原生兜底")
@any_caller
def cmd_dashboard_inspect_screenshot(out_path: Path | None, client_id: str | None) -> None:
    """抓当前桌面 WebView 或已连接页面的画面。优先原生桌面截图。"""
    target = out_path or (_root() / "data" / "runtime" / "inspect-latest.png")
    target.parent.mkdir(parents=True, exist_ok=True)
    desktop = _desktop_inspect_endpoint()
    if desktop is not None and not client_id:
        import urllib.request
        try:
            with urllib.request.urlopen(f"{desktop[0]}/screenshot", timeout=10) as response:
                raw = response.read()
            if raw[:8] == b"\x89PNG\r\n\x1a\n":
                target.write_bytes(raw)
                click.echo(json.dumps({
                    "ok": True,
                    "source": "desktop",
                    "path": str(target),
                    "bytes": len(raw),
                }, ensure_ascii=False))
                return
        except Exception as error:
            click.echo(json.dumps({
                "ok": False,
                "source": "desktop",
                "error": str(error),
                "hint": "桌面原生通道未就绪时会回退到页面 inspect",
            }, ensure_ascii=False))

    queued = _queue_page_inspect_command("screenshot", {}, client_id)
    if not queued or not queued.get("ok"):
        click.echo(json.dumps({
            "ok": False,
            "source": "page",
            "clientId": client_id,
            "error": (queued or {}).get("error") or "inspect hub unreachable",
            "hint": "先确认 dashboard 在跑，并且桌面/网页已打开驾驶舱",
        }, ensure_ascii=False))
        raise SystemExit(1)
    done = _wait_inspect_command(queued["command"]["id"])
    command = ((done or {}).get("command") or {}) if isinstance(done, dict) else {}
    result = command.get("result") or {}
    canvases = result.get("canvases") if isinstance(result, dict) else None
    saved = []
    if isinstance(result, dict):
        native = result.get("screenshot")
        data_url = native.get("dataUrl") if isinstance(native, dict) else result.get("screenshotDataUrl")
        if isinstance(data_url, str) and "base64," in data_url:
            import base64
            blob = base64.b64decode(data_url.split("base64,", 1)[1])
            if blob[:8] == b"\x89PNG\r\n\x1a\n":
                target.write_bytes(blob)
                saved.append(str(target))
    if isinstance(canvases, list):
        import base64
        for index, canvas in enumerate(canvases[:4]):
            if saved:
                break
            data_url = str((canvas or {}).get("dataUrl") or "")
            marker = "base64,"
            if marker not in data_url:
                continue
            blob = base64.b64decode(data_url.split(marker, 1)[1])
            piece = target if index == 0 else target.with_name(f"{target.stem}-{index}{target.suffix}")
            piece.write_bytes(blob)
            saved.append(str(piece))
    command_status = str(command.get("status") or "") or None
    command_error = str(command.get("error") or "").strip() or None
    native_capture_error = (
        str(result.get("nativeCaptureError") or "").strip()
        if isinstance(result, dict)
        else ""
    ) or None
    if not command:
        hub_error = str((done or {}).get("error") or "").strip() if isinstance(done, dict) else ""
        failure_error = f"inspect_hub_error:{hub_error}" if hub_error else "inspect_command_unavailable"
    elif command_status in {"pending", "in_flight"}:
        failure_error = f"inspect_command_timeout:{command_status}"
    elif command_status == "error":
        failure_error = command_error or "inspect_command_failed"
    elif command_status != "ok":
        failure_error = f"inspect_command_status:{command_status or 'unknown'}"
    else:
        failure_error = None if saved else "unsupported"
    command_ok = command_status == "ok"
    unsupported = failure_error == "unsupported"
    summary = {
        # A successful queue round-trip is not a screenshot.  The page
        # adapter must return at least one decodable image, otherwise expose
        # an explicit unsupported result instead of a false positive.
        "ok": command_ok and bool(saved),
        "source": "page",
        "clientId": client_id or command.get("clientId"),
        "commandId": command.get("id") or queued.get("command", {}).get("id"),
        "commandStatus": command_status,
        "commandError": command_error,
        "nativeCaptureError": native_capture_error,
        "path": saved[0] if saved else None,
        "saved": saved,
        "unsupported": unsupported,
        "error": failure_error,
        "title": result.get("title") if isinstance(result, dict) else None,
        "activeTabId": result.get("activeTabId") if isinstance(result, dict) else None,
        "tabs": result.get("tabs") if isinstance(result, dict) else None,
        "terminals": [
            {
                "sessionId": item.get("sessionId"),
                "provider": item.get("provider"),
                "visible": item.get("visible"),
                "cols": item.get("cols"),
                "rows": item.get("rows"),
                "preview": (item.get("lines") or [])[-16:],
            }
            for item in (result.get("terminals") or [])
        ] if isinstance(result, dict) else [],
    }
    click.echo(json.dumps(summary, ensure_ascii=False, indent=2))
    if not summary["ok"]:
        raise SystemExit(1)


@cmd_dashboard_inspect.command("info")
@click.option("--client-id", default=None, help="精确指定 status 列出的页面实例")
@any_caller
def cmd_dashboard_inspect_info(client_id: str | None) -> None:
    """读当前页签、终端尺寸和可见会话，不抓图。"""
    queued = _queue_page_inspect_command("info", {}, client_id)
    if not queued or not queued.get("ok"):
        click.echo(json.dumps({"ok": False, "error": (queued or {}).get("error") or "unreachable"}, ensure_ascii=False))
        raise SystemExit(1)
    done = _wait_inspect_command(queued["command"]["id"])
    click.echo(json.dumps(done, ensure_ascii=False, indent=2))


@cmd_dashboard_inspect.command("eval")
@click.option("--client-id", default=None, help="精确指定 status 列出的页面实例；指定后不走桌面原生兜底")
@click.argument("expression")
@any_caller
def cmd_dashboard_inspect_eval(client_id: str | None, expression: str) -> None:
    """在用户当前驾驶舱页面执行 JS 表达式并返回结果。"""
    desktop = _desktop_inspect_endpoint()
    if desktop is not None and not client_id:
        payload = _http_json("POST", f"{desktop[0]}/eval", {"expression": expression}, timeout=8.0)
        if payload is not None:
            click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
            return
    queued = _queue_page_inspect_command("eval", {"expression": expression}, client_id)
    if not queued or not queued.get("ok"):
        click.echo(json.dumps({"ok": False, "error": (queued or {}).get("error") or "unreachable"}, ensure_ascii=False))
        raise SystemExit(1)
    click.echo(json.dumps(_wait_inspect_command(queued["command"]["id"]), ensure_ascii=False, indent=2))


@cmd_dashboard_inspect.command("click")
@click.option("--client-id", default=None, help="精确指定 status 列出的页面实例；指定后不走桌面原生兜底")
@click.argument("x", type=int)
@click.argument("y", type=int)
@any_caller
def cmd_dashboard_inspect_click(client_id: str | None, x: int, y: int) -> None:
    """在用户当前驾驶舱页面点击 CSS 像素坐标。"""
    desktop = _desktop_inspect_endpoint()
    if desktop is not None and not client_id:
        payload = _http_json("POST", f"{desktop[0]}/input", {"kind": "click", "x": x, "y": y}, timeout=8.0)
        if payload is not None:
            click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
            return
    queued = _queue_page_inspect_command("click", {"x": x, "y": y}, client_id)
    if not queued or not queued.get("ok"):
        click.echo(json.dumps({"ok": False, "error": (queued or {}).get("error") or "unreachable"}, ensure_ascii=False))
        raise SystemExit(1)
    click.echo(json.dumps(_wait_inspect_command(queued["command"]["id"]), ensure_ascii=False, indent=2))


@cmd_dashboard_inspect.command("type")
@click.option("--client-id", default=None, help="精确指定 status 列出的页面实例")
@click.argument("text")
@any_caller
def cmd_dashboard_inspect_type(client_id: str | None, text: str) -> None:
    """向当前焦点控件输入文本。"""
    queued = _queue_page_inspect_command("type", {"text": text}, client_id)
    if not queued or not queued.get("ok"):
        click.echo(json.dumps({"ok": False, "error": (queued or {}).get("error") or "unreachable"}, ensure_ascii=False))
        raise SystemExit(1)
    click.echo(json.dumps(_wait_inspect_command(queued["command"]["id"]), ensure_ascii=False, indent=2))


@cmd_dashboard_inspect.command("key")
@click.option("--client-id", default=None, help="精确指定 status 列出的页面实例")
@click.argument("key")
@any_caller
def cmd_dashboard_inspect_key(client_id: str | None, key: str) -> None:
    """向当前焦点控件发送一个按键，例如 Enter / Escape / Tab。"""
    queued = _queue_page_inspect_command("key", {"key": key}, client_id)
    if not queued or not queued.get("ok"):
        click.echo(json.dumps({"ok": False, "error": (queued or {}).get("error") or "unreachable"}, ensure_ascii=False))
        raise SystemExit(1)
    click.echo(json.dumps(_wait_inspect_command(queued["command"]["id"]), ensure_ascii=False, indent=2))
