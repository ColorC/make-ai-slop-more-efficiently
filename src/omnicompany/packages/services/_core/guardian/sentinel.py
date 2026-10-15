# [OMNI] origin=omnicompany domain=omnicompany/guardian ts=2026-04-10T00:00:00Z
# [OMNI] material_id="material:core.guardian.sentinel.daemon_engine.py"
"""OmniSentinel — 独立长驻巡逻守护进程 (2026-04-10 重构)

设计模型 (per user directive 2026-04-10):

  sentinel 是**独立长驻进程**, 不随 TeamRunner / agent_node_loop /
  其他主程序退出而退出. 它是**触发 + 冷却**式: 每次核心组件启动时更新
  .omni/core_activity_ts.json, sentinel 在醒来时读这个文件, 如果有新活动
  且冷却期已过, 就跑一次 *增量* patrol (只扫 mtime > last_patrol_ts
  的文件). LLM-heavy 规则有更长的独立冷却窗口, 默认 30 分钟.

启动路径:
  - `omni guardian daemon` (主路径, 用户可手动或由 TeamRunner 自动调用)
  - `sentinel.ensure_daemon_running()` (runtime 层的自动 spawn, 检测到
     daemon 不在就 fork 一个 detached child 进程)

单例机制:
  - .omni/sentinel.pid 保存当前 daemon 的 PID
  - 启动前检查 PID 存活性, 避免重复 spawn
  - 主循环每轮检查 PID file 是否仍指向自己, 若被新 daemon 覆盖则主动退出

状态文件 (全部在 .omni/):
  .omni/sentinel.pid              - 当前 daemon 的 PID
  .omni/core_activity_ts.json     - 核心组件最后活跃时间 (由 runner 等写)
  .omni/sentinel_state.json       - sentinel 自己的状态: last_patrol_ts 等

向后兼容:
  原 OmniSentinel 线程类作为 shim 保留, 所有方法转发到模块级函数.
  runner.py::_ensure_guardian_running() 不再启动线程, 改为 ping activity +
  ensure_daemon_running().

参考:
  docs/plans/[2026-04-10]GUARDIAN-SENTINEL-ACTIVITY-GATED/plan.md §1
  原历史: LLM quota burst 是真实问题 (见旧版 first_delay 处理), 本版通过
  cooldown_s / llm_cooldown_s 从协议层解决.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from omnicompany.core.config import omni_workspace_root

logger = logging.getLogger(__name__)

__version__ = "1.3.0"   # 2026-04-10: in-process thread → detached daemon process

_DEFAULT_ROOT = omni_workspace_root()

# ─── 常量: 状态文件路径 ─────────────────────────────────────

_PID_FILE_REL = ".omni/sentinel.pid"
_ACTIVITY_TS_FILE_REL = ".omni/core_activity_ts.json"
_STATE_FILE_REL = ".omni/sentinel_state.json"

# 默认参数 (可被 daemon_loop 参数覆盖)
_DEFAULT_WAKE_INTERVAL_S = 10      # sentinel 唤醒检查间隔
_DEFAULT_COOLDOWN_S = 300          # 两次 patrol 最小间隔 = 5 分钟
_DEFAULT_LLM_COOLDOWN_S = 1800     # 两次 LLM patrol 最小间隔 = 30 分钟


# ─── 时间工具 ──────────────────────────────────────────────

def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


# ─── JSON 读写工具 ─────────────────────────────────────────

def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, data: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except OSError as e:
        logger.warning("sentinel: write %s failed: %s", path, e)
        return False


# ─── PID 文件 & 进程活性 ─────────────────────────────────────

def read_pid_file(root: Path = _DEFAULT_ROOT) -> Optional[int]:
    pid_file = Path(root) / _PID_FILE_REL
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def write_pid_file(root: Path = _DEFAULT_ROOT, pid: Optional[int] = None) -> None:
    if pid is None:
        pid = os.getpid()
    pid_file = Path(root) / _PID_FILE_REL
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(pid), encoding="utf-8")


def clear_pid_file(root: Path = _DEFAULT_ROOT) -> None:
    pid_file = Path(root) / _PID_FILE_REL
    try:
        if pid_file.exists():
            pid_file.unlink()
    except OSError:
        pass


def _is_pid_alive(pid: Optional[int]) -> bool:
    """Cross-platform PID alive check."""
    if pid is None or pid <= 0:
        return False
    try:
        if platform.system() == "Windows":
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                ctypes.windll.kernel32.GetExitCodeProcess(
                    handle, ctypes.byref(exit_code)
                )
                return exit_code.value == STILL_ACTIVE
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        else:
            os.kill(pid, 0)   # signal 0 = no-op, raises if dead
            return True
    except Exception:
        return False


def is_daemon_alive(root: Path = _DEFAULT_ROOT) -> bool:
    """Check if sentinel daemon is running (PID file + process alive)."""
    return _is_pid_alive(read_pid_file(root))


# ─── 活跃时间戳 (由核心组件调用) ────────────────────────────

def write_activity_ts(
    root: Path = _DEFAULT_ROOT,
    source: str = "unknown",
) -> None:
    """Record a core-component activity. Sentinel reads this on next wake.

    Core components (TeamRunner, agent_node_loop, dispatch, etc.) should
    call this on start. The cost is ~1 small JSON write; called once per
    invocation. Cheap enough to be unconditional.
    """
    data = {
        "last_activity_ts": _iso_now(),
        "source": source,
    }
    _write_json(Path(root) / _ACTIVITY_TS_FILE_REL, data)


def read_activity_ts(root: Path = _DEFAULT_ROOT) -> Optional[datetime]:
    data = _read_json(Path(root) / _ACTIVITY_TS_FILE_REL)
    return _parse_iso(data.get("last_activity_ts", ""))


# ─── Sentinel 自身状态 ─────────────────────────────────────

def read_sentinel_state(root: Path = _DEFAULT_ROOT) -> dict:
    data = _read_json(Path(root) / _STATE_FILE_REL)
    # 字段兜底
    data.setdefault("last_patrol_ts", "")
    data.setdefault("last_llm_patrol_ts", "")
    data.setdefault("last_processed_activity_ts", "")
    data.setdefault("patrol_count", 0)
    data.setdefault("llm_patrol_count", 0)
    return data


def write_sentinel_state(root: Path = _DEFAULT_ROOT, state: Optional[dict] = None) -> None:
    if state is None:
        state = {}
    _write_json(Path(root) / _STATE_FILE_REL, state)


# ─── 守护进程主循环 ────────────────────────────────────────

def daemon_loop(
    root: Path = _DEFAULT_ROOT,
    wake_interval_s: int = _DEFAULT_WAKE_INTERVAL_S,
    cooldown_s: int = _DEFAULT_COOLDOWN_S,
    llm_cooldown_s: int = _DEFAULT_LLM_COOLDOWN_S,
    once: bool = False,
    verbose: bool = True,
) -> None:
    """Sentinel daemon 主循环. 被 `omni guardian daemon` CLI 调用.

    - wake_interval_s : sentinel 每隔多久醒来检查一次 (默认 10s, 低开销)
    - cooldown_s      : 两次 patrol 之间的最小间隔 (默认 300s = 5min)
    - llm_cooldown_s  : LLM-involving patrol 的最小间隔 (默认 1800s = 30min)
    - once            : 只处理一轮就退出 (测试/诊断用)

    工作流 (伪码):

      write PID file
      while 不退出:
          sleep(wake_interval_s)
          if PID file 指向的 PID 不是自己: return (新 daemon 上线, 我让位)
          activity = read_activity_ts()
          state = read_sentinel_state()
          if 无新活动 since last_processed: continue
          if 冷却未过 (now - last_patrol < cooldown): continue
          run_llm = (距上次 LLM patrol > llm_cooldown)
          run_patrol(full=True, since_ts=last_patrol_ts, use_llm=run_llm)
          更新 state (last_patrol_ts / last_processed / last_llm_patrol_ts)
    """
    root = Path(root)
    if verbose:
        logger.info("[sentinel daemon] 启动 v%s  root=%s", __version__, root)
        logger.info(
            "[sentinel daemon] wake=%ds cooldown=%ds llm_cooldown=%ds once=%s",
            wake_interval_s, cooldown_s, llm_cooldown_s, once,
        )

    # 设置递归守卫环境变量: daemon 内部任何嵌套的 ensure_daemon_running 都会 no-op.
    # 这是防 spawn 风暴的关键 (2026-04-10 修 44 daemon 风暴事件).
    os.environ[_RECURSION_GUARD_ENV] = "1"

    write_pid_file(root)
    my_pid = os.getpid()

    try:
        while True:
            # 单例守护: PID file 被改就让位
            current_pid = read_pid_file(root)
            if current_pid != my_pid:
                if verbose:
                    logger.info(
                        "[sentinel daemon] PID file 指向 %s 不是自己 %s, 退出",
                        current_pid, my_pid,
                    )
                return

            # 一次条件评估 + 可能的 patrol
            did_patrol = _run_once(root, cooldown_s, llm_cooldown_s, verbose)

            if once:
                if verbose:
                    logger.info(
                        "[sentinel daemon] --once: did_patrol=%s, 退出",
                        did_patrol,
                    )
                return

            time.sleep(wake_interval_s)
    except KeyboardInterrupt:
        if verbose:
            logger.info("[sentinel daemon] Ctrl-C, 停止")
    finally:
        # 只有 PID file 仍指向自己时才清除 (避免误删新 daemon 的 PID file)
        if read_pid_file(root) == my_pid:
            clear_pid_file(root)


def _run_once(
    root: Path,
    cooldown_s: int,
    llm_cooldown_s: int,
    verbose: bool,
) -> bool:
    """Evaluate state; run one patrol if conditions met. Returns True if patrolled."""
    activity_ts = read_activity_ts(root)
    if activity_ts is None:
        if verbose:
            logger.debug("[sentinel] 无 activity_ts, 跳过")
        return False

    state = read_sentinel_state(root)
    last_patrol = _parse_iso(state.get("last_patrol_ts", ""))
    last_llm_patrol = _parse_iso(state.get("last_llm_patrol_ts", ""))
    last_processed = _parse_iso(state.get("last_processed_activity_ts", ""))

    now = datetime.now(timezone.utc)

    # 无新活动 → 跳过
    if last_processed is not None and activity_ts <= last_processed:
        if verbose:
            logger.debug("[sentinel] activity 未更新, 跳过")
        return False

    # 仍在冷却期 → 跳过
    if last_patrol is not None:
        elapsed = (now - last_patrol).total_seconds()
        if elapsed < cooldown_s:
            if verbose:
                logger.debug(
                    "[sentinel] 冷却中 (%.0fs < %ds), 跳过", elapsed, cooldown_s,
                )
            return False

    # 决定是否跑 LLM (首次 last_llm_patrol 为空 → 允许)
    run_llm = True
    if last_llm_patrol is not None:
        llm_elapsed = (now - last_llm_patrol).total_seconds()
        if llm_elapsed < llm_cooldown_s:
            run_llm = False

    # 增量 patrol: since_ts = 上次 patrol 的时间, 首次为空则全量
    since_ts_str = state.get("last_patrol_ts", "") or None

    if verbose:
        logger.info(
            "[sentinel] 触发 patrol  since_ts=%s  run_llm=%s",
            since_ts_str or "(首次,全量)", run_llm,
        )

    try:
        from omnicompany.packages.services._core.guardian import run_patrol
        result = run_patrol(
            project_root=root,
            full_scan=True,
            use_llm=run_llm,
            use_agent=run_llm,  # 2026-04-24: GuardianAgent 复核同 LLM 冷却节奏 (run_llm=True 时开)
            since_ts=since_ts_str,
        )
        if verbose:
            logger.info(
                "[sentinel] patrol 完成  files=%d  violations=%d  mode=%s",
                result.get("files_scanned", 0),
                result.get("violations_found", 0),
                result.get("scan_mode", "?"),
            )
    except Exception as e:
        logger.warning("[sentinel] patrol 异常: %s", e, exc_info=True)
        return False

    # 运行空间 hygiene 扫描 (2026-04-23 I-18 扩展: 持续监督 OMNI-047~051)
    # hygiene 失败不算 patrol 失败, 但记 warning
    try:
        from omnicompany.packages.services._core.guardian.workers import HygieneScanWorker
        hw = HygieneScanWorker()
        hv = hw.run({"project_root": str(root)})
        if verbose:
            logger.info(
                "[sentinel] hygiene 完成  violations=%d  candidates=%d  by_rule=%s",
                hv.output.get("violation_count", 0),
                hv.output.get("candidate_count", 0),
                hv.output.get("by_rule", {}),
            )
    except Exception as e:
        logger.warning("[sentinel] hygiene 异常 (非致命): %s", e, exc_info=True)

    # 罚单逾期升级 (Phase 4 第四C 步, 2026-04-28): 每次唤醒检查一次, 不消耗 LLM
    try:
        from .tow_truck import OmniTow
        tow = OmniTow(project_root=root)
        result = tow.escalate_overdue_tickets(threshold_days=7)
        if result["escalated_count"] > 0:
            logger.warning(
                "[sentinel] 逾期升级 %d 条罚单到 evolve-signal: %s",
                result["escalated_count"], result["escalated_ticket_ids"][:5],
            )
    except Exception as e:
        logger.debug("[sentinel] 逾期升级异常 (非致命): %s", e)

    # 工作区污染候选 (2026-09-07 收紧): 只读扫描工作区根顶层非登记项。
    # 未知不等于可删；实际回收须由带 manifest/hash/回退的已验收 mover 执行。
    try:
        from .workspace_pollution import run_workspace_pollution_scan
        wsp = run_workspace_pollution_scan(omni_root=root, dry_run=True)
        if wsp["total_tickets"] > 0:
            logger.warning(
                "[sentinel] 工作区污染候选 %d 项: %s",
                wsp["total_tickets"], wsp["by_root"],
            )
    except Exception as e:
        logger.debug("[sentinel] 污染扫描异常 (非致命): %s", e)

    # 更新 state
    state["last_patrol_ts"] = now.isoformat()
    state["last_processed_activity_ts"] = activity_ts.isoformat()
    state["patrol_count"] = int(state.get("patrol_count", 0)) + 1
    if run_llm:
        state["last_llm_patrol_ts"] = now.isoformat()
        state["llm_patrol_count"] = int(state.get("llm_patrol_count", 0)) + 1
    write_sentinel_state(root, state)
    return True


# ─── Spawn detached daemon ─────────────────────────────────

# 环境变量递归守卫: 设置后表示"当前进程树已经在 sentinel daemon 里",
# 任何嵌套的 ensure_daemon_running 调用都会 no-op. 这是防 spawn 风暴的
# 防御深度措施, 2026-04-10 引入 (修 44 daemon 风暴事件).
_RECURSION_GUARD_ENV = "OMNI_SENTINEL_DAEMON_RUNNING"


def ensure_daemon_running(root: Path = _DEFAULT_ROOT) -> bool:
    """Guarantee a sentinel daemon is running. Spawn detached child if not.

    **重要**: 当前版本不再从 runner._ensure_guardian_running 自动调用.
    此函数保留供用户/运维显式启动, 或将来 L4 设计确定后再打开自动 spawn.

    递归守卫: 如果环境变量 OMNI_SENTINEL_DAEMON_RUNNING=1 (当前进程已在
    sentinel 进程树里), 直接返回 True 不做任何事. 防止 TeamRunner
    在 patrol 内部被创建时触发 spawn 递归风暴.

    Idempotent: if daemon already alive, returns True without spawning.

    Returns:
        True 如果 daemon 在运行 (已有的或本次 spawn 的, 或递归守卫命中)
        False 如果 spawn 失败
    """
    # 递归守卫: 已经在 daemon 进程树里, no-op
    if os.environ.get(_RECURSION_GUARD_ENV) == "1":
        logger.debug("[sentinel] ensure_daemon_running: 递归守卫命中, skip")
        return True

    root = Path(root)
    if is_daemon_alive(root):
        return True

    python_exe = sys.executable
    cmd = [
        python_exe, "-m", "omnicompany",
        "guardian", "daemon",
        "--root", str(root),
    ]
    # 给子进程设置递归守卫环境变量
    child_env = os.environ.copy()
    child_env[_RECURSION_GUARD_ENV] = "1"
    try:
        if platform.system() == "Windows":
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            subprocess.Popen(
                cmd,
                cwd=str(root),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=flags,
                close_fds=True,
                env=child_env,
            )
        else:
            subprocess.Popen(
                cmd,
                cwd=str(root),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
                env=child_env,
            )
        logger.info("[sentinel] 已 spawn detached daemon")
        return True
    except Exception as e:
        logger.warning("[sentinel] spawn daemon failed: %s", e)
        return False


def stop_daemon(root: Path = _DEFAULT_ROOT, timeout_s: int = 20) -> bool:
    """Signal the sentinel daemon to stop by clearing its PID file.

    The daemon's main loop checks PID file every wake interval and exits if
    it no longer points to its own PID. If daemon doesn't die within timeout_s,
    falls back to platform-specific force kill.

    Returns True if daemon is confirmed dead after call.
    """
    root = Path(root)
    pid = read_pid_file(root)
    if pid is None or not _is_pid_alive(pid):
        clear_pid_file(root)
        return True

    clear_pid_file(root)
    # 等 daemon 自退
    for _ in range(timeout_s):
        if not _is_pid_alive(pid):
            return True
        time.sleep(1)
    # 超时, 强杀
    try:
        if platform.system() == "Windows":
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                check=False, capture_output=True,
            )
        else:
            import signal
            os.kill(pid, signal.SIGTERM)
    except Exception as e:
        logger.warning("[sentinel] force kill failed: %s", e)
    return not _is_pid_alive(pid)


def daemon_status(root: Path = _DEFAULT_ROOT) -> dict:
    """Return a status dict for CLI 'omni guardian daemon-status' or similar."""
    root = Path(root)
    pid = read_pid_file(root)
    alive = _is_pid_alive(pid)
    state = read_sentinel_state(root)
    activity = _read_json(Path(root) / _ACTIVITY_TS_FILE_REL)
    return {
        "alive": alive,
        "pid": pid,
        "version": __version__,
        "last_patrol_ts": state.get("last_patrol_ts") or None,
        "last_llm_patrol_ts": state.get("last_llm_patrol_ts") or None,
        "patrol_count": state.get("patrol_count", 0),
        "llm_patrol_count": state.get("llm_patrol_count", 0),
        "last_core_activity_ts": activity.get("last_activity_ts") or None,
        "last_core_activity_source": activity.get("source") or None,
    }


# ─── 向后兼容: OmniSentinel 类 shim ─────────────────────────
#
# 新代码应直接调用模块级函数 (write_activity_ts / ensure_daemon_running /
# daemon_loop). 此 class 仅为兼容 runner.py 等遗留代码路径.

class OmniSentinel:
    """Shim. Real sentinel 是 detached daemon, 见模块级函数."""

    __version__ = __version__
    _instance: Optional["OmniSentinel"] = None

    def __init__(self, project_root: Path = _DEFAULT_ROOT) -> None:
        self._root = Path(project_root)

    @classmethod
    def get_instance(cls, project_root: Path = _DEFAULT_ROOT) -> "OmniSentinel":
        if cls._instance is None:
            cls._instance = cls(project_root)
        return cls._instance

    def is_alive(self) -> bool:
        return is_daemon_alive(self._root)

    def needs_refresh(self) -> bool:
        # 版本刷新现由 PID-file 单例机制自然处理
        return False

    def refresh(self) -> None:
        stop_daemon(self._root)

    def start(self, daemon: bool = True, interval_seconds: int = 300) -> None:
        # 保留旧 signature, 忽略 interval_seconds (daemon 用自己的参数)
        ensure_daemon_running(self._root)

    def stop(self) -> None:
        stop_daemon(self._root)

    def status(self) -> dict:
        return daemon_status(self._root)
