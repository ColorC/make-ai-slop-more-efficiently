# [OMNI] origin=claude-code domain=dashboard/ccdaemon ts=2026-06-28 type=infra
# [OMNI] material_id="material:cli.claude_code.remote_control.supervisor.py"
# [OMNI] summary="omni cc remote 的生命周期 + 自重启 supervisor: 后台无窗口常驻 `claude remote-control`(服务器模式), 让官方 Claude app / claude.ai/code 随时接管本机 omnicompany 会话。supervisor 循环重起(覆盖网络断 ~10min 超时退出), pid/log/state 落 data/。"
# [OMNI] why="官方 Remote Control 是纯出站 HTTPS 中转(无需公网/隧道/鉴权配置), 但本机进程一断会话即停、网络断超 ~10min 进程会退出, 故需一个 detached 无窗口 supervisor 自重启 + 防 spawn 风暴的退避。复用 lifecycle 的 data 根与 cc daemon/chatui 同款 DETACHED/TerminateProcess 范式, 不另造进程托管。"
# [OMNI] tags=claude-code,remote-control,supervisor,daemon,persistent,mobile
"""omni cc remote —— 官方 Claude Remote Control 常驻服务的进程托管 + 自重启 supervisor。

形态: 后台 detached 无窗口跑一个 supervisor(本模块 `_supervise_main`), 它循环拉起
`claude remote-control`(服务器模式, cwd=omnicompany 根, 全本地环境可用), 子进程退出就重起。

- 主力保活 = supervisor 循环(网络抖动 / ~10min 超时退出 → 秒级重起);
- 退避防 spawn 风暴: 子进程秒退(<15s)按 5·2^streak 退避, 封顶 60s; 跑过 60s 视为健康, 退避归零;
- 防重复会话: start 前清理"supervisor 已死但 claude 仍孤儿存活"的残留子树, 避免 claude.ai/code 里冒出重复在线会话。

纯函数 + 一个 `_supervise_main` 入口, 不依赖 click(CLI 在 cli/commands/cc.py 的 `remote` 组)。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root
from omnicompany.dashboard.ccdaemon import lifecycle

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008

_LOG_MAX_BYTES = 5 * 1024 * 1024  # supervisor / debug log 各超 5MB 轮转一份
_HEALTHY_RUN_S = 60              # 子进程活过这么久视为健康, 退避归零
_BACKOFF_BASE_S = 5
_BACKOFF_CAP_S = 60

# 配置默认值(start 无参时 = 上次保存的配置, 没保存才落这里; 让自启/保活的无参 start 不丢用户设置)
DEFAULTS = {"name": "OmniCompany", "spawn": "same-dir", "permission_mode": None, "debug": False}


# ── 路径(复用 ccdaemon lifecycle 的 data 根, 不另立 data 目录)──────────

def _data_dir() -> Path:
    return lifecycle._data_dir()


def pid_file() -> Path:
    return _data_dir() / "cc_remote.pid"          # supervisor 进程 pid


def child_pid_file() -> Path:
    return _data_dir() / "cc_remote_child.pid"     # 当前 claude remote-control 子进程 pid(claude.cmd 树根)


def log_file() -> Path:
    return _data_dir() / "cc_remote.log"           # supervisor 事件日志(启动/退出/重起)


def debug_file() -> Path:
    return _data_dir() / "cc_remote_debug.log"     # claude --debug-file(仅 debug=true 时启用)


def state_file() -> Path:
    return _data_dir() / "cc_remote.json"          # 配置 + 计数


def stop_file() -> Path:
    return _data_dir() / "cc_remote.stop"          # 存在 = 已请求停止(supervisor 见到即跳出循环)


# ── 小工具 ────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if sys.platform == "win32":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pid(p: Path) -> int | None:
    try:
        return int(p.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _read_state() -> dict[str, Any]:
    try:
        return json.loads(state_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_config() -> dict[str, Any]:
    """读上次保存的会话配置(供无参 start / 自启复用); 缺字段落 DEFAULTS。"""
    st = _read_state()
    return {k: st.get(k, DEFAULTS[k]) for k in DEFAULTS}


def _write_state(st: dict[str, Any]) -> None:
    try:
        state_file().write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _rotate(p: Path, max_bytes: int = _LOG_MAX_BYTES) -> None:
    try:
        if p.exists() and p.stat().st_size > max_bytes:
            bak = p.with_suffix(p.suffix + ".1")
            bak.unlink(missing_ok=True)
            p.rename(bak)
    except OSError:
        pass


def _log(msg: str) -> None:
    """往 supervisor 日志追加一行带时间戳的事件(超限自动轮转)。"""
    _rotate(log_file())
    line = f"{_now_iso()}  {msg}\n"
    try:
        with log_file().open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def _kill_tree(pid: int | None) -> bool:
    """杀掉 pid 及其子树(claude.cmd → node → 会话 child)。返回是否尝试过。"""
    if not pid:
        return False
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass
    return True


def _terminate(pid: int | None) -> None:
    if not pid:
        return
    if sys.platform == "win32":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
        if h:
            ctypes.windll.kernel32.TerminateProcess(h, 0)
            ctypes.windll.kernel32.CloseHandle(h)
    else:
        try:
            os.kill(pid, 15)
        except OSError:
            pass


def _resolve_claude() -> str:
    """定位 claude 可执行(Windows 用 claude.cmd, 供 subprocess 直接跑)。"""
    if sys.platform == "win32":
        cand = Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd"
        if cand.exists():
            return str(cand)
        import shutil
        for n in ("claude.cmd", "claude.exe", "claude"):
            f = shutil.which(n)
            if f:
                return f
        raise FileNotFoundError("找不到 claude.cmd(确认 Claude Code 已全局安装在 PATH)")
    import shutil
    f = shutil.which("claude")
    if not f:
        raise FileNotFoundError("找不到 claude(确认 Claude Code 已安装在 PATH)")
    return f


def _child_env() -> dict[str, str]:
    """子进程环境: 去掉会顶掉 claude.ai OAuth 的凭据(Remote Control 不支持 API key /
    inference-only token), 其余原样继承(PATH 让 claude 能找到 node)。"""
    env = os.environ.copy()
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
        env.pop(k, None)
    return env


# ── 状态查询 ──────────────────────────────────────────────────────────

def read_status() -> dict[str, Any]:
    sup = _read_pid(pid_file())
    sup_alive = _pid_alive(sup)
    child = _read_pid(child_pid_file())
    child_alive = _pid_alive(child)
    st = _read_state()
    return {
        "alive": sup_alive,
        "supervisor_pid": sup if sup_alive else None,
        "child_pid": child if child_alive else None,
        "child_alive": child_alive,
        "name": st.get("name"),
        "spawn": st.get("spawn"),
        "permission_mode": st.get("permission_mode"),
        "workdir": st.get("workdir"),
        "started_at": st.get("started_at"),
        "restarts": st.get("restarts"),
        "last_exit": st.get("last_exit"),
        "orphan_child": (child if (child_alive and not sup_alive) else None),
        "log_file": str(log_file()),
        "state_file": str(state_file()),
    }


# ── 启动 / 停止 ───────────────────────────────────────────────────────

def start(name: str = "OmniCompany", spawn: str = "same-dir",
          permission_mode: str | None = None, debug: bool = False,
          workdir: str | None = None) -> dict[str, Any]:
    """启动常驻 supervisor(幂等: 已活则 no-op)。spawn detached 无窗口的 `_supervise_main`。"""
    s = read_status()
    if s["alive"]:
        return {"ok": True, "already": True, "supervisor_pid": s["supervisor_pid"],
                "note": "supervisor 已在跑", "name": s.get("name")}

    # supervisor 已死但 claude 子进程可能成孤儿存活 → 清掉, 否则会冒出重复在线会话
    orphan = _read_pid(child_pid_file())
    if _pid_alive(orphan):
        _kill_tree(orphan)
        _log(f"start: 清理孤儿 claude 子树 pid={orphan}")
    child_pid_file().unlink(missing_ok=True)
    stop_file().unlink(missing_ok=True)

    wd = workdir or str(omni_workspace_root())
    _data_dir().mkdir(parents=True, exist_ok=True)
    _write_state({
        "name": name, "spawn": spawn, "permission_mode": permission_mode,
        "debug": bool(debug), "workdir": wd,
        "started_at": _now_iso(), "restarts": 0,
        "child_pid": None, "last_launch_at": None, "last_exit": None,
    })

    # detached 无窗口 python 跑 supervisor 循环(配置从 state 文件读, 免 -c 参数转义)
    code = ("from omnicompany.dashboard.ccdaemon import remote_control as r; r._supervise_main()")
    creationflags = DETACHED_PROCESS if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        [sys.executable, "-c", code], cwd=wd,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        env=_child_env(), creationflags=creationflags,
    )
    pid_file().write_text(str(proc.pid), encoding="utf-8")
    _log(f"start: supervisor spawned pid={proc.pid} name={name!r} spawn={spawn} "
         f"permission_mode={permission_mode} debug={debug} cwd={wd}")
    return {"ok": True, "supervisor_pid": proc.pid, "name": name, "spawn": spawn,
            "workdir": wd, "log": str(log_file()),
            "note": "supervisor 已起; `omni cc remote status` 看连接状态, "
                    "手机端打开 Claude app → Code 标签按名字找会话"}


def stop() -> dict[str, Any]:
    """停止: 落 stop 标记 → 杀 claude 子树 → 终止 supervisor → 清 pid 文件。"""
    stop_file().write_text(_now_iso(), encoding="utf-8")
    sup = _read_pid(pid_file())
    child = _read_pid(child_pid_file())
    killed = {}
    if _pid_alive(child):
        _kill_tree(child)          # 杀子树 → supervisor 的 child.wait() 返回, 见 stop 标记跳出
        killed["child"] = child
    time.sleep(0.4)
    if _pid_alive(sup):
        _terminate(sup)
        killed["supervisor"] = sup
    pid_file().unlink(missing_ok=True)
    child_pid_file().unlink(missing_ok=True)
    stop_file().unlink(missing_ok=True)
    _log(f"stop: killed={killed}")
    if not killed:
        return {"ok": False, "reason": "not running"}
    return {"ok": True, "killed": killed}


def restart(**kw) -> dict[str, Any]:
    stop()
    time.sleep(0.6)
    return start(**kw)


# ── supervisor 主循环(detached 进程入口)────────────────────────────────

def _supervise_main() -> None:
    """detached 无窗口进程的入口: 循环拉起 `claude remote-control`, 退出即重起(带退避)。"""
    try:
        pid_file().write_text(str(os.getpid()), encoding="utf-8")  # 确认自身 pid(start 已写, 幂等)
        st = _read_state()
        name = st.get("name") or "OmniCompany"
        spawn = st.get("spawn") or "same-dir"
        permission_mode = st.get("permission_mode")
        debug = bool(st.get("debug"))
        wd = st.get("workdir") or str(omni_workspace_root())

        try:
            claude = _resolve_claude()
        except FileNotFoundError as e:
            _log(f"supervise: FATAL {e}")
            pid_file().unlink(missing_ok=True)
            return

        # --no-create-session-in-dir: 长驻服务器不预建空会话, 改连上时按需建,
        # 否则 supervisor 每次重启都在 claude.ai/code 里下崽一个空会话(列表越堆越多)。
        base_cmd = [claude, "remote-control", "--name", name, "--spawn", spawn,
                    "--no-create-session-in-dir"]
        if permission_mode:
            base_cmd += ["--permission-mode", permission_mode]
        if debug:
            base_cmd += ["--verbose", "--debug-file", str(debug_file())]

        _log(f"supervise: loop start name={name!r} spawn={spawn} claude={claude}")
        streak = 0
        while not stop_file().exists():
            if debug:
                _rotate(debug_file())
            launched_at = time.time()
            try:
                child = subprocess.Popen(
                    base_cmd, cwd=wd,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                    env=_child_env(), creationflags=(CREATE_NO_WINDOW if sys.platform == "win32" else 0),
                )
            except OSError as e:
                _log(f"supervise: spawn failed {type(e).__name__}: {e}; 退避 {_BACKOFF_CAP_S}s")
                time.sleep(_BACKOFF_CAP_S)
                continue

            child_pid_file().write_text(str(child.pid), encoding="utf-8")
            st = _read_state()
            st["child_pid"] = child.pid
            st["last_launch_at"] = _now_iso()
            _write_state(st)
            _log(f"supervise: launched claude child pid={child.pid}")

            rc = child.wait()
            ran_s = time.time() - launched_at
            st = _read_state()
            st["last_exit"] = {"code": rc, "at": _now_iso(), "ran_s": round(ran_s, 1)}
            child_pid_file().unlink(missing_ok=True)

            if stop_file().exists():
                _log(f"supervise: child exited rc={rc} ran={ran_s:.0f}s — stop 标记在, 退出循环")
                _write_state(st)
                break

            if ran_s >= _HEALTHY_RUN_S:
                streak = 0
                backoff = _BACKOFF_BASE_S
            else:
                streak += 1
                backoff = min(_BACKOFF_CAP_S, _BACKOFF_BASE_S * (2 ** (streak - 1)))
            st["restarts"] = int(st.get("restarts") or 0) + 1
            _write_state(st)
            _log(f"supervise: child exited rc={rc} ran={ran_s:.0f}s streak={streak} → {backoff}s 后重起")

            # 退避期间分段睡, 好让 stop 及时生效
            slept = 0.0
            while slept < backoff and not stop_file().exists():
                time.sleep(min(1.0, backoff - slept))
                slept += 1.0

        _log("supervise: 循环结束")
    except Exception as e:  # noqa: BLE001
        _log(f"supervise: 异常退出 {type(e).__name__}: {e}")
    finally:
        pid_file().unlink(missing_ok=True)
        child_pid_file().unlink(missing_ok=True)
