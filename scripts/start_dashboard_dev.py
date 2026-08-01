#!/usr/bin/env python3
# [OMNI] origin=ai-ide ts=2026-05-09 type=script
# [OMNI] material_id="material:scripts.start_dashboard_dev.dual_process_launcher.py"
"""dashboard dogfood 双进程启动脚本.

起两个 uvicorn 进程并管理它们的生命周期:
1. ccdaemon (8201, 默认不开 reload) — 持有 chat / pty 真业务
2. dashboard (8200, 开 --reload, 排除 ccdaemon/) — 控制面 + 反向代理

按 [2026-05-09]DASHBOARD-DOGFOOD-RESILIENCE 道路, 两进程拆开后:
- AI IDE 改控制面任意文件触发 dashboard reload, ccdaemon 不动, chat 会话不断
- AI IDE 改 ccdaemon 自身代码必须显式 `omni cc daemon restart`, 浏览器走自动重连协议续展

用法
-----
python scripts/start_dashboard_dev.py
    # ctrl+c 一次: 优雅停 dashboard 后停 daemon
    # ctrl+c 两次: 强制全杀

python scripts/start_dashboard_dev.py --no-daemon
    # 只起 dashboard, 假定 daemon 已经手动起 (例如调试 daemon 单独跑)

python scripts/start_dashboard_dev.py --dashboard-port 8200 --daemon-port 8201
    # 改端口
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
DASHBOARD_DIR = SRC_DIR / "omnicompany" / "dashboard"


def _http_ready(host: str, port: int, path: str = "/cc/health", timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}{path}", timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def _wait_ready(host: str, port: int, path: str, deadline_s: float) -> bool:
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        if _http_ready(host, port, path):
            return True
        time.sleep(0.5)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Dashboard dogfood 双进程启动")
    parser.add_argument("--dashboard-host", default="127.0.0.1")
    parser.add_argument("--dashboard-port", type=int, default=8200)
    parser.add_argument("--daemon-host", default="127.0.0.1")
    parser.add_argument("--daemon-port", type=int, default=8201)
    parser.add_argument("--no-daemon", action="store_true", help="不起 daemon, 假定已手动起")
    parser.add_argument("--no-reload", action="store_true",
                        help="dashboard 也不开 reload (例如生产模式或排查 reload 问题)")
    parser.add_argument("--prod", action="store_true",
                        help="生产模式 (推荐, 默认): dashboard 服务 frontend/static/ build 产物. "
                             "前提 frontend/ 跑过 npm run build.")
    parser.add_argument("--dev", action="store_true",
                        help="开发模式 (留作后续做): 同时起 vite dev (5173, HMR), dashboard 仍 8200. "
                             "当前未实装, 用户用 vite 开发请手动起 'cd frontend && npm run dev'.")
    args = parser.parse_args()

    if args.dev:
        print("[start] WARN: --dev 模式当前没实装 vite dev 起步.")
        print("[start]       手动跑 'cd frontend && npm run dev' 起 vite (5173), 然后跑本脚本起 dashboard (8200).")
        print("[start]       继续按 prod 模式启动...")

    # banner — 帮用户知道当前用什么模式
    static_index = REPO_ROOT / "src" / "omnicompany" / "dashboard" / "static" / "index.html"
    if not static_index.is_file():
        print(f"[start] WARN: {static_index} 不存在 — 跑 'cd frontend && npm run build' 先 build 一次.")
    else:
        print(f"[start] mode=prod (serving frontend/static/)")

    procs: list[subprocess.Popen] = []
    daemon_proc: subprocess.Popen | None = None

    # 1) ccdaemon
    if not args.no_daemon:
        # 先看现状, 不重复起
        try:
            from omnicompany.dashboard.ccdaemon import lifecycle
            s = lifecycle.read_status()
            if s.alive:
                print(f"[start] ccdaemon already running (pid={s.pid} port={s.port}); skipping.")
            else:
                env = os.environ.copy()
                env["OMNI_CC_DAEMON_PORT"] = str(args.daemon_port)
                creationflags = 0
                if sys.platform == "win32":
                    creationflags = 0x00000200  # CREATE_NEW_PROCESS_GROUP — 让 ctrl+c 不直接传子进程

                daemon_cmd = [
                    sys.executable, "-m", "uvicorn",
                    "omnicompany.dashboard.ccdaemon.main:app",
                    "--host", args.daemon_host,
                    "--port", str(args.daemon_port),
                    "--log-level", "info",
                ]
                print(f"[start] launching ccdaemon: {' '.join(daemon_cmd)}")
                daemon_proc = subprocess.Popen(
                    daemon_cmd, env=env, cwd=str(REPO_ROOT),
                    creationflags=creationflags,
                )
                procs.append(daemon_proc)
                # 等 daemon ready
                if not _wait_ready(args.daemon_host, args.daemon_port, "/health", 20.0):
                    print("[start] FAIL: ccdaemon did not become ready in 20s; aborting.", file=sys.stderr)
                    daemon_proc.terminate()
                    return 2
                print(f"[start] ccdaemon ready on http://{args.daemon_host}:{args.daemon_port}/cc/health")
        except ImportError as e:
            print(f"[start] FAIL importing lifecycle: {e}", file=sys.stderr)
            return 3

    # 2) dashboard
    dashboard_cmd = [
        sys.executable, "-m", "uvicorn",
        "omnicompany.dashboard.app:app",
        "--host", args.dashboard_host,
        "--port", str(args.dashboard_port),
        "--log-level", "info",
    ]
    if not args.no_reload:
        dashboard_cmd.extend([
            "--reload",
            "--reload-dir", str(DASHBOARD_DIR),
            # ccdaemon/ 由独立进程持有, 不让 dashboard reload 触发
            "--reload-exclude", str(DASHBOARD_DIR / "ccdaemon" / "*"),
        ])
    print(f"[start] launching dashboard: {' '.join(dashboard_cmd)}")
    dashboard_proc = subprocess.Popen(dashboard_cmd, cwd=str(REPO_ROOT))
    procs.append(dashboard_proc)

    if not _wait_ready(args.dashboard_host, args.dashboard_port, "/api/cc/health", 30.0):
        print("[start] WARN: dashboard /api/cc/health did not respond in 30s. "
              "Continuing anyway — check logs if it does not recover.", file=sys.stderr)
    else:
        print(f"[start] dashboard ready on http://{args.dashboard_host}:{args.dashboard_port}/")
        print("[start] open browser at http://{}:{}/".format(args.dashboard_host, args.dashboard_port))

    print("[start] press ctrl+c to stop both (graceful), twice for force kill.")

    # 3) 等待 + ctrl+c handling
    sigint_count = 0

    def on_sigint(signum, frame):
        nonlocal sigint_count
        sigint_count += 1
        if sigint_count == 1:
            print("\n[stop] ctrl+c received; stopping dashboard then daemon...")
            try:
                dashboard_proc.terminate()
            except Exception:
                pass
        else:
            print("\n[stop] ctrl+c x2; force kill all.")
            for p in procs:
                try:
                    p.kill()
                except Exception:
                    pass
            sys.exit(130)

    signal.signal(signal.SIGINT, on_sigint)

    # 等 dashboard 退出
    try:
        dashboard_proc.wait()
    except KeyboardInterrupt:
        pass

    print("[stop] dashboard exited; stopping daemon...")
    if daemon_proc is not None:
        try:
            daemon_proc.terminate()
            daemon_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            daemon_proc.kill()
        except Exception:
            pass

    print("[stop] all stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
