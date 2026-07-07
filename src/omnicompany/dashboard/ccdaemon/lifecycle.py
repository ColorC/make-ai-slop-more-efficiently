# [OMNI] origin=ai-ide ts=2026-05-09 type=infra
# [OMNI] material_id="material:dashboard.ccdaemon.lifecycle.pid_port_bookkeeping.py"
"""ccdaemon 生命周期管理 — pid / port 文件 + 启动健康自检.

dashboard 控制面 (controlplane/cc_proxy.py) 通过读 data/cc_daemon.port 知道 daemon
监听哪个端口; CLI (omni cc daemon status / restart) 通过读 data/cc_daemon.pid 知
道当前 daemon 进程; guardian zombie-scan 通过同样的文件认 daemon 不是 zombie.

文件协议
--------
data/cc_daemon.pid   单行 ASCII 整数 = 当前 daemon 进程 pid; 空文件或不存在 = 没在跑
data/cc_daemon.port  单行 ASCII 整数 = 当前监听端口 (默认 8201, 启动时可覆盖)
data/cc_daemon.log   stdout/stderr 落盘, 按需轮转 (rotation 阶段六补)

进程死亡时 atexit hook 清 pid 文件 (port 保留作下次启动 hint).
异常崩溃 (kill -9) 留陈旧 pid; CLI status 跟启动逻辑都先 psutil 校验 pid 还活, 不活
就当无效清理.
"""

from __future__ import annotations

import logging
import os
import socket
import sys
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8201


def _data_dir() -> Path:
    """data/ 根目录 — 跟 cc_sessions.json / events.db 同位置."""
    state_dir = os.environ.get("OMNI_CC_DAEMON_STATE_DIR")
    if state_dir:
        return Path(state_dir)
    from omnicompany.core.config import omni_workspace_root
    return omni_workspace_root() / "data"


def pid_file() -> Path:
    return _data_dir() / "cc_daemon.pid"


def port_file() -> Path:
    return _data_dir() / "cc_daemon.port"


def log_file() -> Path:
    return _data_dir() / "cc_daemon.log"


class DaemonStatus(NamedTuple):
    pid: int | None
    port: int | None
    alive: bool      # 进程层面活着: pid 存在(或端口被本 daemon 占)
    serving: bool    # 应用层面在服务: /health 真应答 ccdaemon 签名

    @property
    def zombie(self) -> bool:
        """进程在但 /health 不应答 = 僵尸(挂死/启动卡住) → 该重启, 不能当"已在跑"放过.

        历史坑: read_status 只看 pid 活不活时, 挂死进程(pid 在、socket 不服务)被
        误报 alive=True → `omni cc daemon start` 说 already running 拒绝拉新, 扩展
        readiness gate (daemon /health) 又永远过不了 → 持续转圈 'not fully ready'。
        """
        return self.alive and not self.serving


def read_status(probe: bool = True) -> DaemonStatus:
    """读 pid/port 文件 + 校验 pid 真活 + (probe) 打 /health 判 serving.

    probe=True (默认, CLI status/start/restart 用): 总是打 /health → serving 真值,
      可识别僵尸 (alive 但 not serving)。
    probe=False (cc_proxy 热路径, 每次代理请求都调): 不主动打 /health, 仅在 pid
      丢/陈旧时探一次端口 (沿用旧兜底行为, 避免每请求一次 HTTP round-trip)。
    """
    pid: int | None = None
    port: int | None = None
    pf = pid_file()
    if pf.is_file():
        try:
            pid = int(pf.read_text(encoding="utf-8").strip() or "0") or None
        except ValueError:
            pid = None
    porf = port_file()
    if porf.is_file():
        try:
            port = int(porf.read_text(encoding="utf-8").strip() or "0") or None
        except ValueError:
            port = None

    alive = False
    if pid is not None:
        alive = _pid_alive(pid)
        if not alive:
            # 陈旧 pid, 清掉避免误导 cc_proxy
            try:
                pf.unlink()
            except OSError:
                pass
            pid = None

    serving = False
    # probe=True: 总是探 /health(僵尸检测 + serving 真值)。
    # probe=False: 仅在 pid 丢/陈旧时探(旧 cc_proxy 兜底行为, 不在热路径加 round-trip)。
    if port is not None and (probe or not alive):
        # 端口有人听 → 必须确认占用者是 ccdaemon 本身 (查 /health 签名), 否则被别的
        # 进程抢端口时会误判 daemon 活着 → 代理静默 404。命中则顺手把真 pid 从
        # /health 取回, 让 status/stop/restart 仍能正常工作。
        health_pid = _daemon_health_pid(port)
        if health_pid is not None:
            serving = True
            if not alive:
                alive = True
                if pid is None:
                    pid = health_pid
    return DaemonStatus(pid=pid, port=port, alive=alive, serving=serving)


def _pid_alive(pid: int) -> bool:
    """跨平台 pid 存活检查. Windows 用 OpenProcess, POSIX 用 kill 0."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False
            try:
                exit_code = ctypes.c_ulong()
                ok = kernel32.GetExitCodeProcess(h, ctypes.byref(exit_code))
                # 259 = STILL_ACTIVE
                return bool(ok) and exit_code.value == 259
            finally:
                kernel32.CloseHandle(h)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


def _port_alive(port: int) -> bool:
    if port <= 0:
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def _daemon_health_pid(port: int) -> int | None:
    """端口被占时确认占用者就是 ccdaemon 本身 — 命中 /health 看签名, 返回其 daemon_pid.

    为什么需要: pid 文件丢/陈旧时 read_status 只靠 `_port_alive` 判活, 但任何进程
    占着这个端口都会让它误判 daemon 活着 (实测被 slidecast 临时 `python -m http.server
    8201` 抢端口, 代理把 /api/boss-sight/* 全转给静态服务器 → 静默 404 而非 503)。
    ccdaemon /health 回 {"daemon_pid": ..., "boss_sight": {...}}; 静态服务器只会回
    404 HTML, 无法伪造这个签名。返回 None = 占端口的不是我们的 daemon。
    """
    if port <= 0:
        return None
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as r:
            if r.status != 200:
                return None
            data = json.loads(r.read(8192).decode("utf-8", "replace"))
    except Exception:
        return None
    if not isinstance(data, dict) or "boss_sight" not in data:
        return None
    dp = data.get("daemon_pid")
    return dp if isinstance(dp, int) and dp > 0 else None


def write_pid(pid: int) -> None:
    pf = pid_file()
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(f"{pid}\n", encoding="utf-8")


def write_port(port: int) -> None:
    porf = port_file()
    porf.parent.mkdir(parents=True, exist_ok=True)
    porf.write_text(f"{port}\n", encoding="utf-8")


def clear_pid() -> None:
    pf = pid_file()
    try:
        pf.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("clear_pid failed: %s", e)


def install_atexit_hook() -> None:
    """Daemon 启动时挂 atexit, 正常退出清 pid 文件."""
    import atexit
    atexit.register(clear_pid)


# ── log rotation ([2026-05-09] 阶段 9 exit_criteria 7) ───────────────────────
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
LOG_BACKUP_COUNT = 5  # 留 .log.1 .. .log.5


def rotate_log_if_oversize(max_bytes: int = LOG_MAX_BYTES,
                           backup_count: int = LOG_BACKUP_COUNT) -> bool:
    """Daemon 启动前调一次. 当前 log > max_bytes 就走 rotation 协议:

    1. 删 .log.<backup_count> (最老)
    2. .log.<i> → .log.<i+1> 倒着挪 (i = backup_count-1 .. 1)
    3. .log → .log.1
    4. 创建空 .log

    返回 True = 真做了滚动 (size 超), False = 不需要.

    设计选择: 滚动在启动前做, 不在进程内做. 因为 daemon 子进程 stdout/stderr 已经
    被 omni cc daemon start spawn 时通过 redirect 绑到 fd 上, 进程内换 RotatingFileHandler
    会跟 uvicorn stdout 双写. 启动前滚 + 用户改 ccdaemon 必 restart → 滚 → 重连一致.
    长跑 (>10MB 不重启) 不滚, 进 §14 debt 留独立 plan 真正进程内滚.
    """
    log = log_file()
    if not log.is_file():
        return False
    try:
        size = log.stat().st_size
    except OSError:
        return False
    if size <= max_bytes:
        return False

    # rotation 协议
    log_dir = log.parent
    base = log.name  # 'cc_daemon.log'

    # 1. 删最老
    oldest = log_dir / f"{base}.{backup_count}"
    if oldest.exists():
        try: oldest.unlink()
        except OSError: pass

    # 2. .log.<i> → .log.<i+1> (倒着挪, 防覆盖)
    for i in range(backup_count - 1, 0, -1):
        src = log_dir / f"{base}.{i}"
        dst = log_dir / f"{base}.{i + 1}"
        if src.exists():
            try:
                if dst.exists():
                    dst.unlink()
                src.rename(dst)
            except OSError:
                pass

    # 3. .log → .log.1
    rotated = log_dir / f"{base}.1"
    try:
        if rotated.exists():
            rotated.unlink()
        log.rename(rotated)
    except OSError:
        # rename 失败时尝试 fallback: 简单 truncate
        try: log.write_bytes(b"")
        except OSError: pass
        return False

    # 4. 新建空 .log
    try:
        log.touch()
    except OSError:
        pass
    return True
