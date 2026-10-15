"""controlplane/canvas_store_service.py — dashboard 自持的画布存储服务（@omni/canvas-store 实例）。

2026-08-16 零依赖裁决修正: omnidashboard 的 builtin 画布**不经 workshop**
(workshop 必须可单独部署、对 dashboard 零依赖)。dashboard 自己起同一 TS 权威包
(@omni/canvas-store)的一个独立实例服务 builtin 存储根(overlay-note-store);
Python 桥的 notes_canvas_*/notes_text_* 透明转发到该服务 —— 两个软件各跑各的
实例, 唯一共享物是 webworks 里的包代码。

生命周期由 dashboard 托管: startup 拉起(健康探测就绪), 进程意外退出自动重拉,
shutdown 回收。令牌首次随机生成落盘, 桥与服务共用同一 token 文件。
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger("omnicompany.canvas_store_service")

_PORT = int(os.environ.get("OMNI_CANVAS_STORE_PORT", "8212"))
_ROOT = Path(os.environ.get("OMNI_CANVAS_STORE_ROOT", r"C:/workspace/overlay-note-store"))
_SERVER_SCRIPT = Path(os.environ.get(
    "OMNI_CANVAS_STORE_SERVER",
    r"C:/workspace/webworks\packages\canvas-store\server.ts",
))
_TOKEN_FILE = Path(os.environ.get(
    "OMNI_CANVAS_STORE_TOKEN_FILE",
    r"C:/workspace/omnicompany\data\canvas_store_token.txt",
))
_MAX_RESTARTS = 8

_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_monitor: threading.Thread | None = None
_stopping = False


def store_url() -> str:
    return os.environ.get("OMNI_CANVAS_STORE_URL", f"http://127.0.0.1:{_PORT}")


def token_value() -> str:
    try:
        return _TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _ensure_token() -> None:
    if _TOKEN_FILE.is_file():
        return
    _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_FILE.write_text(secrets.token_hex(24), encoding="utf-8")


def _health_ok(timeout: float = 1.5) -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"{store_url()}/api/health", timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return bool(payload.get("ok"))
    except Exception:
        return False


def _spawn() -> subprocess.Popen | None:
    if not _SERVER_SCRIPT.is_file():
        logger.error("canvas-store 服务脚本不存在: %s", _SERVER_SCRIPT)
        return None
    command = [
        "node", "--experimental-strip-types", str(_SERVER_SCRIPT),
        "--root", str(_ROOT),
        "--product", "omnidashboard",
        "--text-dir", "materials",
        "--workspace-check", "off",
        "--port", str(_PORT),
        "--token-file", str(_TOKEN_FILE),
    ]
    try:
        return subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as error:
        logger.error("canvas-store 服务启动失败: %s", error)
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
            logger.error("canvas-store 服务连续崩溃 %s 次, 停止重拉", restarts)
            return
        logger.warning("canvas-store 服务退出(code=%s), 1s 后重拉(%s/%s)", code, restarts, _MAX_RESTARTS)
        time.sleep(1.0)
        _proc = _spawn()


def start() -> None:
    """dashboard startup 钩子: 拉起服务并等健康(最多 ~8s), 已就绪则秒回。"""
    global _proc, _monitor, _stopping
    _ensure_token()
    with _lock:
        _stopping = False
        if _health_ok(timeout=0.5):
            return
        _proc = _spawn()
    if _monitor is None or not _monitor.is_alive():
        _monitor = threading.Thread(target=_monitor_loop, name="canvas-store-monitor", daemon=True)
        _monitor.start()
    deadline = time.time() + 8.0
    while time.time() < deadline:
        if _health_ok(timeout=0.5):
            logger.info("canvas-store 服务就绪: %s (root=%s)", store_url(), _ROOT)
            return
        time.sleep(0.3)
    logger.error("canvas-store 服务未在 8s 内就绪(画布读写将报错): %s", store_url())


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
