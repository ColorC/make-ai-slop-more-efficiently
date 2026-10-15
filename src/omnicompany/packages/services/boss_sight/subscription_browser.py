"""Open OmniCompany's shared persistent Chrome profile so the user can log in once.

This is the existing agent-login channel:
``~/.cloudcli/browser-use/profiles/agent-login``

It is not the daily Chrome/Edge profile. Cookies stay in this directory after
the visible window is closed. Capture attaches over CDP.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_CHROME = os.environ.get("CHROME_BIN") or r"C:\Program Files\Google\Chrome\Application\chrome.exe"
_PROFILE = Path.home() / ".cloudcli" / "browser-use" / "profiles" / "agent-login"
_STATE = Path.home() / ".cloudcli" / "browser-use" / "agent-login-state.json"
_SHOW_WINDOW = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "domains"
    / "bilibili_publish"
    / "episodes"
    / "ep02-publishing-pipelines"
    / "video"
    / "pipeline"
    / "show_window.ps1"
)


def profile_dir() -> Path:
    return _PROFILE


def _read_state() -> dict[str, Any]:
    try:
        data = json.loads(_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(patch: dict[str, Any]) -> dict[str, Any]:
    current = _read_state()
    current.update(patch)
    _STATE.parent.mkdir(parents=True, exist_ok=True)
    _STATE.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def _cdp_browser(port: int) -> str | None:
    if not port:
        return None
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}/json/version", timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError):
        return None
    if isinstance(payload, dict) and payload.get("Browser"):
        return str(payload["Browser"])
    return None


def _recorded_port() -> int:
    return int(_read_state().get("cdpPort") or 0)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _agent_login_pid() -> int | None:
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Where-Object { $_.CommandLine -match 'agent-login' -and $_.CommandLine -notmatch '--type=' } | "
        "Select-Object -First 1 -ExpandProperty ProcessId"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    raw = (completed.stdout or "").strip()
    if not raw.isdigit():
        return None
    pid = int(raw)
    return pid if pid > 0 else None


def _chrome_is_headless() -> bool:
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Where-Object { $_.CommandLine -match 'agent-login' -and $_.CommandLine -notmatch '--type=' } | "
        "Select-Object -First 1 -ExpandProperty CommandLine"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    line = completed.stdout or ""
    return "--headless" in line


def _close_agent_login_chrome() -> None:
    script = (
        "$ps = Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Where-Object { $_.CommandLine -match 'agent-login' -and $_.CommandLine -notmatch '--type=' }; "
        "foreach ($p in $ps) { $h = Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue; "
        "if ($h) { [void]$h.CloseMainWindow() } }; "
        "Start-Sleep -Seconds 2; "
        "$left = @(Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Where-Object { $_.CommandLine -match 'agent-login' }); "
        "foreach ($p in $left) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _show_window(pid: int | None) -> None:
    if not pid or not _SHOW_WINDOW.is_file():
        return
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(_SHOW_WINDOW),
            "-TargetPid",
            str(pid),
            "-IncludeChildren",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def _launch_chrome(url: str, port: int, *, headless: bool) -> None:
    _PROFILE.mkdir(parents=True, exist_ok=True)
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    args = [
        _CHROME,
        f"--user-data-dir={_PROFILE}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=TranslateUI",
        "--disable-dev-shm-usage",
    ]
    if headless:
        args.append("--headless=new")
    else:
        args.extend(["--window-size=1280,900", "--new-window"])
    args.append(url or "about:blank")
    subprocess.Popen(
        args,
        close_fds=True,
        creationflags=creationflags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _launch_visible(url: str, port: int) -> None:
    _launch_chrome(url, port, headless=False)


def ensure_headless() -> None:
    port = _recorded_port()
    if _cdp_browser(port):
        return
    port = _free_port()
    _launch_chrome("about:blank", port, headless=True)
    if not _wait_cdp(port):
        raise RuntimeError("自动读取用的无头浏览器没有起来")
    _write_state({"cdpPort": port, "lastSubscriptionError": None})


def _wait_cdp(port: int, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _cdp_browser(port):
            return True
        time.sleep(0.4)
    return False


def browser_status() -> dict[str, Any]:
    port = _recorded_port()
    running = bool(_cdp_browser(port))
    return {
        "running": running,
        "profile": str(_PROFILE),
        "profile_exists": _PROFILE.is_dir(),
        "channel": "agent-login",
        "cdp_port": port if running else None,
        "visible": bool(running and not _chrome_is_headless()),
        "last_url": _read_state().get("lastSubscriptionUrl"),
        "last_title": _read_state().get("lastSubscriptionTitle"),
        "last_error": _read_state().get("lastSubscriptionError"),
        "note": (
            "这是 OmniCompany 通用持久登录档（~/.cloudcli/browser-use/profiles/agent-login），"
            "不是日常 Chrome/Edge。第一次在弹出窗口里登录，cookie 会留在这个档里。"
        ),
    }


def _cdp_work(url: str | None, *, read_text: bool) -> dict[str, Any]:
    port = _recorded_port()
    if not _cdp_browser(port):
        raise RuntimeError("持久登录档的 Chrome 还没起来")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        try:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[-1] if context.pages else context.new_page()
            if url:
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(1800)
            result: dict[str, Any] = {
                "ok": True,
                "url": page.url,
                "title": page.title(),
            }
            if read_text:
                result["text"] = (page.inner_text("body") or "")[:8000]
            _write_state(
                {
                    "lastSubscriptionUrl": result["url"],
                    "lastSubscriptionTitle": result["title"],
                    "lastSubscriptionError": None,
                }
            )
            return result
        finally:
            browser.close()


def open_login(url: str) -> dict[str, Any]:
    port = _recorded_port()
    live = bool(_cdp_browser(port))
    visible = live and not _chrome_is_headless()
    try:
        if live and visible:
            result = _cdp_work(url, read_text=False)
            result.update(browser_status())
            return result
        if live:
            _close_agent_login_chrome()
            time.sleep(1.2)
        port = _free_port()
        _launch_visible(url, port)
        if not _wait_cdp(port):
            raise RuntimeError("可见登录窗没有开出调试端口")
        _write_state(
            {
                "cdpPort": port,
                "lastSubscriptionUrl": url,
                "lastSubscriptionError": None,
            }
        )
        _show_window(_agent_login_pid())
        result = {"ok": True, "url": url, "title": None}
        result.update(browser_status())
        return result
    except Exception as exc:  # noqa: BLE001
        _write_state({"lastSubscriptionError": str(exc)})
        raise


def capture_page(url: str | None = None) -> dict[str, Any]:
    port = _recorded_port()
    if not _cdp_browser(port):
        if not url:
            raise RuntimeError("没有正在运行的登录窗，先打开登录窗或给一个网址")
        ensure_headless()
    result = _cdp_work(url, read_text=True)
    result.update(browser_status())
    return result
