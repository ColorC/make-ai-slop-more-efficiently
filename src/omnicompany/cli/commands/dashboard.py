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
import subprocess
import sys
import time
from pathlib import Path

import click

from .._access import any_caller

_DASHBOARD_PORT = int(os.environ.get("OMNI_DASHBOARD_PORT", "8210"))
_DAEMON_PORT = int(os.environ.get("OMNI_CC_DAEMON_PORT", "8201"))


def _root() -> Path:
    from omnicompany.core.config import omni_workspace_root
    return omni_workspace_root()


def _dashboard_dir() -> Path:
    return _root() / "src" / "omnicompany" / "dashboard"


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


def _run_build(cwd: Path, args: list[str], label: str) -> None:
    click.echo(f"[{label}] {' '.join(args)}  (cwd={cwd})")
    # CREATE_NO_WINDOW: npm/cmd 子进程别弹前台窗(用户硬规则)。输出仍走继承的 stdout, 不影响看构建日志。
    proc = subprocess.run(args, cwd=str(cwd), shell=(os.name == "nt"),
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if proc.returncode != 0:
        click.echo(json.dumps({"ok": False, "step": label, "returncode": proc.returncode}, ensure_ascii=False))
        raise SystemExit(proc.returncode)


@click.group("dashboard")
def cmd_dashboard() -> None:
    """驾驶舱免重启更新 (status / ui-update / ui-reload / ext-update / ext-reload / restart)。"""


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
    _run_build(_dashboard_dir() / "frontend", ["npm", "run", "build"], "ui-build")
    after = (_versions() or {}).get("ui")
    changed = before is not None and after is not None and before != after
    if after is not None and not changed:
        # 构建产物没变 (无实质改动) 时页面不会自刷; 如确需强刷用 ui-reload
        click.echo(json.dumps({"ok": True, "built": True, "changed": False,
                               "hint": "产物未变化, 页面不刷新; 强刷用 omni dashboard ui-reload"}, ensure_ascii=False))
        return
    click.echo(json.dumps({"ok": True, "built": True, "changed": changed,
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


@cmd_dashboard.command("restart")
@any_caller
def cmd_dashboard_restart() -> None:
    """重启 dashboard 进程 (8210)。绝不碰 ccdaemon (8201) — claude/codex 会话全程存活。

    VSCode 扩展每 5s 健康监测, 进程回来后会自动把 iframe 重挂上, 无需任何手动操作。
    """
    root = _root()
    if os.name == "nt":
        _kill_port_win(_DASHBOARD_PORT)
    else:
        subprocess.run(["bash", "-c", f"lsof -ti:{_DASHBOARD_PORT} | xargs -r kill -9"], capture_output=True)
    time.sleep(0.8)

    log_dir = root / "data"
    out_log = open(log_dir / f"live_dashboard_{_DASHBOARD_PORT}.out.log", "ab")
    err_log = open(log_dir / f"live_dashboard_{_DASHBOARD_PORT}.err.log", "ab")
    env = {**os.environ, "PYTHONPATH": str(root / "src"), "OMNI_WORKSPACE_ROOT": str(root)}
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0) if os.name == "nt" else 0
    # LOFA: 默认仍 127.0.0.1(不破坏现状); 设 OMNI_DASHBOARD_HOST=0.0.0.0 开放局域网给安卓端。
    _host = os.environ.get("OMNI_DASHBOARD_HOST", "127.0.0.1")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "omnicompany.dashboard.app:app",
         "--host", _host, "--port", str(_DASHBOARD_PORT), "--log-level", "info"],
        cwd=str(root), env=env, stdout=out_log, stderr=err_log,
        creationflags=creationflags, close_fds=True,
        start_new_session=(os.name != "nt"),
    )

    deadline = time.time() + 30
    ready = False
    while time.time() < deadline:
        if _http_json("GET", f"http://127.0.0.1:{_DASHBOARD_PORT}/api/cc/chat/health", timeout=1.5) is not None:
            ready = True
            break
        time.sleep(0.5)
    click.echo(json.dumps({"ok": ready, "pid": proc.pid, "port": _DASHBOARD_PORT,
                           "versions": _versions(),
                           "note": "ccdaemon (8201) 未受影响, 会话存活"}, ensure_ascii=False, indent=2))
    if not ready:
        raise SystemExit(1)
