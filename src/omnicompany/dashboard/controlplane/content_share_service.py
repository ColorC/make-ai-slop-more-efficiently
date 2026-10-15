"""Dashboard-side supervisor for the Slip service on loopback port 8214.

用户 2026-09-18 裁定：Slip 与 omnicompany 分开。Slip 现在是独立项目（独立仓、独立
包、独立数据），本模块只是它的**消费方**——把它拉起来并看着它别死掉，代码不在这里。
接法只有一条：装好 `slip` 包后跑 `python -m slip.service_main`。没装就起不来，
日志会直说，而不是悄悄退回某个仓内副本。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.request

logger = logging.getLogger("omnicompany.content_share_service")

_PORT = int(os.environ.get("OMNI_CONTENT_SHARE_PORT", "8214"))
_MAX_RESTARTS = 8
_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_monitor: threading.Thread | None = None
_stopping = False
# ``urllib.request.build_opener`` constructs an HTTPS context and reloads the
# Windows certificate store.  The monitor probes once per second while Slip is
# owned by another process (the normal Dashboard restart case); rebuilding that
# opener on every probe needlessly burns CPU and holds the GIL.
_HEALTH_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def service_url() -> str:
    return os.environ.get("OMNI_CONTENT_SHARE_URL", f"http://127.0.0.1:{_PORT}")


def _health_ok(timeout: float = 1.0) -> bool:
    try:
        with _HEALTH_OPENER.open(f"{service_url()}/api/content-share/v1/health", timeout=timeout) as response:
            return response.status == 200 and bool(json.loads(response.read()).get("ok"))
    except Exception:
        return False


def _spawn() -> subprocess.Popen | None:
    command = [sys.executable, "-m", "slip.service_main", "--host", "127.0.0.1", "--port", str(_PORT)]
    try:
        return subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as error:
        logger.error("slip service start failed (是否已安装 slip 包?): %s", error)
        return None


def _monitor_loop() -> None:
    global _proc
    restarts = 0
    while not _stopping:
        proc = _proc
        if proc is None:
            if _health_ok(timeout=0.5):
                time.sleep(1.0)
                continue
            restarts += 1
            if restarts > _MAX_RESTARTS:
                logger.error("content-share service exceeded restart limit")
                return
            with _lock:
                if not _stopping and _proc is None:
                    _proc = _spawn()
            time.sleep(1.0)
            continue
        if proc.poll() is None:
            time.sleep(1.0)
            continue
        if _stopping:
            return
        restarts += 1
        if restarts > _MAX_RESTARTS:
            logger.error("content-share service exceeded restart limit")
            return
        time.sleep(1.0)
        _proc = _spawn()


def start() -> None:
    global _proc, _monitor, _stopping
    already_healthy = _health_ok(timeout=0.4)
    with _lock:
        _stopping = False
        if not already_healthy:
            _proc = _spawn()
    if _monitor is None or not _monitor.is_alive():
        _monitor = threading.Thread(target=_monitor_loop, name="content-share-monitor", daemon=True)
        _monitor.start()
    if already_healthy:
        return
    deadline = time.time() + 8.0
    while time.time() < deadline:
        if _health_ok(timeout=0.5):
            logger.info("content-share service ready: %s", service_url())
            return
        time.sleep(0.2)
    logger.error("content-share service not ready after 8s: %s", service_url())


def stop() -> None:
    global _proc, _stopping
    with _lock:
        _stopping = True
        proc = _proc
        _proc = None
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=3.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
