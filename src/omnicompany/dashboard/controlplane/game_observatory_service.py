# [OMNI] origin=ai-ide ts=2026-08-18 type=infra
"""controlplane/game_observatory_service.py — dashboard 托管的 game_observatory 域服务。

DOMAIN-AUTONOMY-P1 A2：复刻 canvas_store_service.py 模式——startup 拉起
（健康探测就绪）、进程意外退出自动重拉、shutdown 回收。服务本体是
packages/domains/game_observatory/service_main.py（uvicorn 小壳，路由 100%
复用 api.py）。

单口铁律：服务只绑 127.0.0.1:8213（config/backplane/domains.yaml 记账），
外部流量仍走 8210 的 game_observatory_proxy 反代，对外面貌不变。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
import urllib.request

logger = logging.getLogger("omnicompany.game_observatory_service")

_PORT = int(os.environ.get("OMNI_GAME_OBSERVATORY_PORT", "8213"))
_MAX_RESTARTS = 8

_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_monitor: threading.Thread | None = None
_stopping = False


def service_url() -> str:
    return os.environ.get("OMNI_GAME_OBSERVATORY_URL", f"http://127.0.0.1:{_PORT}")


def _health_ok(timeout: float = 1.5) -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"{service_url()}/api/game-observatory/health", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _spawn() -> subprocess.Popen | None:
    command = [
        sys.executable, "-m",
        "omnicompany.packages.domains.game_observatory.service_main",
        "--host", "127.0.0.1", "--port", str(_PORT),
    ]
    try:
        return subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as error:
        logger.error("game-observatory 服务启动失败: %s", error)
        return None


def _monitor_loop() -> None:
    global _proc
    restarts = 0
    while not _stopping:
        proc = _proc
        if proc is None:
            time.sleep(1.0)
            continue
        code = proc.poll()
        if code is None:
            time.sleep(1.0)
            continue
        if _stopping:
            return
        restarts += 1
        if restarts > _MAX_RESTARTS:
            logger.error("game-observatory 服务连续崩溃 %s 次, 停止重拉", restarts)
            return
        logger.warning("game-observatory 服务退出(code=%s), 1s 后重拉(%s/%s)",
                       code, restarts, _MAX_RESTARTS)
        time.sleep(1.0)
        _proc = _spawn()


def start() -> None:
    """dashboard startup 钩子: 拉起服务并等健康(最多 ~15s), 已就绪则秒回。

    health 路由会扫报告库, 首次冷启动比 canvas-store 慢, 预算放宽到 15s;
    没等到只记错误不阻断 dashboard 启动(反代会对未就绪上游回 502)。
    """
    global _proc, _monitor, _stopping
    with _lock:
        _stopping = False
        if _health_ok(timeout=0.5):
            return
        _proc = _spawn()
    if _monitor is None or not _monitor.is_alive():
        _monitor = threading.Thread(
            target=_monitor_loop, name="game-observatory-monitor", daemon=True)
        _monitor.start()
    deadline = time.time() + 15.0
    while time.time() < deadline:
        if _health_ok(timeout=1.0):
            logger.info("game-observatory 服务就绪: %s", service_url())
            return
        time.sleep(0.3)
    logger.error("game-observatory 服务未在 15s 内就绪(反代将回 502): %s", service_url())


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
