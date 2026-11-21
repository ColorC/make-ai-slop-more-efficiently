# [OMNI] origin=claude-code domain=runtime/buses ts=2026-05-04T17:00:00Z type=infrastructure
"""PersistentShellSession · 跨调用持久 cwd / env 的 bash 会话 (Wave 4 P1, 2026-05-04).

跟参考项目 LocalShellTask + bashProvider.ts (~257 行) 对齐的**等效**实现, 不一致.

参考项目策略:
  - 真持久 subprocess (一个 bash 跑整个 session)
  - 用 sentinel + tee 同步 stdout / stderr
  - bashProvider 维护 snapshot env (PATH / aliases)

omnicompany 这版 (state-only, 简化版):
  - 每次 run() 起 fresh bash subprocess, 但 cwd / env **状态**跨调用持久
  - 用法: 先把 cmd 跑完, 紧跟 pwd + env -0 probe 写到 stdout, parse 更新 state
  - 下次 run() 用新 cwd / env 起新 subprocess

为啥不真持久 subprocess:
  - 跨平台 stdin / stdout 同步 (bash on git-bash + linux + macOS 行为差异大) 复杂度过高
  - sentinel 检测在 binary stdout 容易出错 (LLM 命令可能输出 ANSI / 二进制)
  - 简化版的 cd / export 持久语义已经覆盖 90% 用例

不目标:
  - REPL / interactive 命令 (vi / less / python REPL): 会卡死 stdin
  - bash function / alias 定义跨调用持久: declare -f 定义的函数会丢
  - 真 PTY 体验 (color / progress bar): 用 BashBus 单次跑

七层对齐评估 (诚实):
  - L1 schema: 工具层暂不暴露 (Wave 5 加 Tool wrapper)
  - L2 行为: cwd / env 持久 ✓; 函数 / alias 不持久 ✗ (跟 cc 真 LocalShellTask 不同)
  - L3 进程: 跟 BashBus 共享 _ACTIVE_PROCESSES + atexit, kill tree 一致
  - L7 可观测: cwd / env 状态变化可记审计, 但本版默认无 audit hook (Wave 5 加)
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import uuid
from typing import Any

from omnicompany.runtime.buses.bash_bus import (
    _kill_process_tree,
    _register_process,
    _unregister_process,
    posix_to_windows_path,
)

logger = logging.getLogger(__name__)


class PersistentShellSession:
    """状态持久 shell 会话 — cwd / env 跨 .run() 持久.

    用法:
        sess = PersistentShellSession(cwd="/tmp")
        sess.run("cd /var; export FOO=bar")
        out, err, rc = sess.run("pwd && echo $FOO")
        # out 含 "/var\nbar\n", rc=0

    线程安全: 内部 lock 保证多线程调用串行化.
    资源: 每次 run() 起 fresh subprocess, 共享全局 _ACTIVE_PROCESSES 注册表.
    """

    def __init__(
        self,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        shell: str | None = None,
        timeout_default: float = 60.0,
    ):
        self._cwd: str = cwd or os.getcwd()
        self._env: dict[str, str] = dict(os.environ) if env is None else dict(env)
        self._shell: str | None = shell  # None → 自动探测
        self._timeout_default = timeout_default
        self._closed = False
        self._lock = threading.Lock()
        # 计数器: 区分多次调用的 marker, 避免上轮残余被错认
        self._call_count = 0

    # ── 状态访问 ────────────────────────────────────────────────

    @property
    def cwd(self) -> str:
        return self._cwd

    @property
    def env(self) -> dict[str, str]:
        return dict(self._env)

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        """标记 session 关闭. 后续 run() 会 raise RuntimeError."""
        self._closed = True

    # ── shell 解析 ────────────────────────────────────────────

    def _resolve_shell(self) -> str:
        """探测 bash 路径. Windows 优先 git bash, Unix 用 SHELL env 或 /bin/bash."""
        if self._shell:
            return self._shell
        if os.name == "nt":
            # 优先 git bash
            for candidate in (r"C:\Program Files\Git\bin\bash.exe", "bash"):
                resolved = shutil.which(candidate) if not candidate.startswith("C:") else None
                if resolved:
                    return resolved
                if os.path.exists(candidate):
                    return candidate
            # 兜底: 假设 bash 在 PATH
            return "bash"
        return os.environ.get("SHELL") or "/bin/bash"

    # ── 核心 run ────────────────────────────────────────────────

    def run(
        self,
        cmd: str,
        *,
        timeout: float | None = None,
        abort_event: "threading.Event | None" = None,
    ) -> tuple[str, str, int]:
        """执行 cmd, 返 (stdout, stderr, exit_code). cwd / env 跨调用持久.

        Args:
            cmd: shell 命令字符串
            timeout: 超时秒数, None 用默认 60s
            abort_event: L7 abort 协议 (Wave 8). 命中时杀进程树 + raise. 调用方
                通常传 ctx.abort_event (AgentNodeLoop 主循环管控).

        Returns:
            (stdout, stderr, exit_code) — stdout 已剥离 probe marker

        Raises:
            RuntimeError: session 已关闭 / abort 触发
            subprocess.TimeoutExpired: 命令超时, 子进程已被强杀
        """
        if self._closed:
            raise RuntimeError("PersistentShellSession is closed")
        if not isinstance(cmd, str) or not cmd.strip():
            raise ValueError("cmd must be a non-empty string")

        timeout = timeout if timeout is not None else self._timeout_default

        with self._lock:
            return self._run_locked(cmd, timeout=timeout, abort_event=abort_event)

    def _run_locked(
        self, cmd: str, *, timeout: float,
        abort_event: "threading.Event | None" = None,
    ) -> tuple[str, str, int]:
        # 用 uuid 防 marker 跟用户输出冲突
        marker_id = uuid.uuid4().hex[:16]
        cwd_begin = f"__OMNI_CWD_{marker_id}_B__"
        cwd_end = f"__OMNI_CWD_{marker_id}_E__"
        env_begin = f"__OMNI_ENV_{marker_id}_B__"
        env_end = f"__OMNI_ENV_{marker_id}_E__"
        self._call_count += 1

        # bash script: 跑 cmd → 存 rc → 输出 cwd probe → 输出 env probe → exit rc
        # `env -0` 输出 NUL 分隔 KEY=VAL (Linux/macOS/git-bash 都支持), 兜底 `env`
        # pwd -W: git bash on Windows 输 Windows 真路径 (不是 /tmp 这种虚拟路径).
        # 在 Linux/macOS bash 上 -W 不识别 → 兜底跑 pwd.
        pwd_cmd = "pwd -W 2>/dev/null || pwd" if os.name == "nt" else "pwd"
        full_cmd = (
            f"{cmd}\n"
            f"_omni_rc=$?\n"
            f'printf "%s\\n" "{cwd_begin}"\n'
            f"{pwd_cmd}\n"
            f'printf "%s\\n" "{cwd_end}"\n'
            f'printf "%s\\n" "{env_begin}"\n'
            f"env -0 2>/dev/null || env\n"
            f'printf "%s\\n" "{env_end}"\n'
            f"exit $_omni_rc\n"
        )

        shell = self._resolve_shell()
        popen_kwargs: dict[str, Any] = dict(
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self._cwd,
            env=self._env,
        )
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen([shell], **popen_kwargs)
        except FileNotFoundError as e:
            raise RuntimeError(f"shell not found: {shell!r} ({e})")

        _register_process(proc)
        # L7 abort 协议 watchdog (Wave 8 P3, 2026-05-05): 用单独线程周期检查
        # abort_event, 命中 → 杀进程树, 让 communicate 自然解阻塞.
        # communicate 自身只 honor timeout 不 honor 任意 event, 必须用外部 watchdog.
        aborted_flag = {"value": False}

        def _watchdog() -> None:
            while proc.poll() is None:
                if abort_event is not None and abort_event.is_set():
                    aborted_flag["value"] = True
                    _kill_process_tree(proc, timeout=2.0)
                    return
                # 0.2s polling 间隔, 跟 abort 信号响应延迟可接受
                if abort_event is None:
                    return  # 没 abort_event 不需要 watchdog
                # threading.Event.wait(timeout=) 比 time.sleep 准, 同时 abort 触发即时返
                if abort_event.wait(timeout=0.2):
                    aborted_flag["value"] = True
                    _kill_process_tree(proc, timeout=2.0)
                    return

        watchdog_thread: "threading.Thread | None" = None
        if abort_event is not None:
            watchdog_thread = threading.Thread(target=_watchdog, daemon=True)
            watchdog_thread.start()

        try:
            try:
                stdout_b, stderr_b = proc.communicate(
                    input=full_cmd.encode("utf-8"),
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                _kill_process_tree(proc, timeout=2.0)
                raise
        finally:
            _unregister_process(proc)
            if watchdog_thread is not None:
                watchdog_thread.join(timeout=1.0)
            # abort 命中 → raise RuntimeError 区分于 timeout
            if aborted_flag["value"]:
                raise RuntimeError(
                    "PersistentShellSession aborted by external signal "
                    "(process tree killed)"
                )

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        exit_code = proc.returncode

        # Parse + update state (即使 cmd 失败 rc!=0, probe 仍跑)
        new_cwd = self._extract_between(stdout, cwd_begin, cwd_end)
        if new_cwd:
            # Windows + git bash: pwd 返 /c/Users/... 风格, subprocess.Popen.cwd
            # 需 Windows 风格路径 — 转一下. POSIX 系统 pwd 返绝对路径直接用.
            if os.name == "nt" and new_cwd.startswith("/"):
                new_cwd = posix_to_windows_path(new_cwd)
            self._cwd = new_cwd

        new_env_block = self._extract_between(stdout, env_begin, env_end)
        if new_env_block:
            parsed = self._parse_env(new_env_block)
            if parsed:
                self._env = self._merge_env(parsed)

        # 剥离 probe markers, 只返用户视图的 stdout
        user_stdout = self._strip_probe_section(stdout, cwd_begin)

        return user_stdout, stderr, exit_code

    # ── helpers ────────────────────────────────────────────────

    @staticmethod
    def _extract_between(text: str, begin: str, end: str) -> str:
        """从 text 抽 begin / end 之间的 block (剥前后空白行)."""
        b_idx = text.find(begin)
        e_idx = text.find(end)
        if b_idx < 0 or e_idx < 0 or e_idx <= b_idx:
            return ""
        # 跳到 begin 行末
        b_line_end = text.find("\n", b_idx)
        if b_line_end < 0 or b_line_end >= e_idx:
            return ""
        return text[b_line_end + 1 : e_idx].rstrip("\n").strip()

    @staticmethod
    def _strip_probe_section(stdout: str, first_marker: str) -> str:
        """剥 stdout 里第一个 marker 后的所有内容 (含 marker 行本身)."""
        idx = stdout.find(first_marker)
        if idx < 0:
            return stdout
        # 找 marker 那行的开头
        line_start = stdout.rfind("\n", 0, idx)
        if line_start < 0:
            return ""
        return stdout[: line_start + 1].rstrip("\n") + ("\n" if line_start + 1 > 0 else "")

    @staticmethod
    def _parse_env(block: str) -> dict[str, str]:
        """env -0 输出 NUL 分隔 KEY=VAL; 兜底 env 是行分隔."""
        out: dict[str, str] = {}
        sep = "\0" if "\0" in block else "\n"
        for entry in block.split(sep):
            entry = entry.strip("\r\n ")
            if not entry or "=" not in entry:
                continue
            k, _, v = entry.partition("=")
            if k:
                out[k] = v
        return out

    def _merge_env(self, parsed: dict[str, str]) -> dict[str, str]:
        if os.name != "nt":
            return parsed

        merged = dict(self._env)
        for key, value in parsed.items():
            if "\x00" in key or "\x00" in value:
                continue
            # Git Bash / WSL can report a Unix-shaped environment. Keep the
            # Windows subprocess base stable and only persist explicit session
            # additions plus project-scoped updates.
            if key not in merged or key.startswith("OMNI_"):
                merged[key] = value
        return merged

    # ── repr ────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"PersistentShellSession(cwd={self._cwd!r}, "
            f"env_keys={len(self._env)}, calls={self._call_count}, "
            f"closed={self._closed})"
        )


__all__ = ["PersistentShellSession"]
