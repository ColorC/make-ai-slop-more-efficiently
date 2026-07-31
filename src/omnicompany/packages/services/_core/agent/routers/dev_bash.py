# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-04-24T00:00:00Z type=infrastructure
# [OMNI] material_id="material:core.agent.routers.dev_bash.restricted_executor.py"
"""DevBashRouter · 开发专用受限 Bash SingleTool.

与 `routers/bash.py::BashRouter` 的区别:
  - BashRouter 走 BashBus + workspace 完整基础设施, 为核心管线设计
  - 本 Router 是 Stage D DevAgent 专用: **独立 subprocess** + 双层守卫
    * cwd 必须在 ToolContext.allowed_bash_roots 某根下 (骨架级 assert)
    * 命令黑名单扫描 (dangerous patterns)
    * 超时硬上限 300s

CWD 追踪 (2026-05-04 加): user 命令尾部追加 pwd 标记, 跑完从 stderr 抽真实 cwd.
  - LLM 跑 `cd /tmp; ls` 后, 返回结果含 `[cwd_after=/tmp]`
  - LLM 下一轮看到, 可在 cwd 参数里传新路径
  - 不强制 (LLM 看不看由它自己), 但比 "状态默默丢失" 安全
  - 对齐 CC `Shell.ts` 的 `pwd -P >| <tempfile>` 模式 (我们用 stderr marker 简化)

设计哲学对齐 memory `feedback_100pct_required_goes_to_skeleton.md`:
  "100% 必做的事必须写进骨架固定环节 · 禁依赖 LLM 主观抉择".
  安全约束在 `_execute` 代码层 assert, 不靠 prompt 劝 "请不要 rm -rf".
  "请"换成 `if matches_dangerous: raise`.
"""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)


# 黑名单: 无论 cwd 在哪里, 永远禁执行 (与 BashBus 默认保持一致 + 额外 P4/git 破坏)
_DANGEROUS_PATTERNS = [
    re.compile(r"\brm\s+-rf\s+/"),
    re.compile(r"\brm\s+-rf\s+\.($|\s)"),      # rm -rf . 在任何 cwd 都是灾难
    re.compile(r"\bdel\s+/s\b"),
    re.compile(r"\bformat\s+[a-z]:"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s+if="),
    re.compile(r":\s*\(\s*\)\s*\{"),            # fork bomb
    # Git 破坏操作
    re.compile(r"\bgit\s+commit\b"),
    re.compile(r"\bgit\s+push\b"),
    re.compile(r"\bgit\s+reset\s+--hard\b"),
    re.compile(r"\bgit\s+checkout\s+--\b"),
    re.compile(r"\bgit\s+restore\b"),
    re.compile(r"\bgit\s+clean\s+-f"),
    # P4 破坏操作
    re.compile(r"\bp4\s+submit\b"),
    re.compile(r"\bp4\s+revert\b"),
    re.compile(r"\bp4\s+delete\b"),
    # 系统级
    re.compile(r"\bshutdown\b"),
    re.compile(r"\breboot\b"),
]


def _matches_danger(cmd: str) -> str | None:
    for pat in _DANGEROUS_PATTERNS:
        if pat.search(cmd):
            return pat.pattern
    return None


class DevBashRouter(SingleToolRouter):
    CONSUMED_META_IO = ("*",)
    PRODUCED_META_IO = ("*",)

    """Dev-focused Bash tool with cwd whitelist + command blacklist.

    Requires ToolContext.allowed_bash_roots (tuple[str]) — cwd must be within
    one of those roots (recursive). Empty/missing → ALL commands refused.

    Allowed roots typically: autochess-ui/, battle-sim-web/frontend/, /tmp/.
    """

    TOOL_NAME: ClassVar[str] = "bash"
    DESCRIPTION: ClassVar[str] = (
        "Run a bash/shell command in a restricted working directory.\n"
        "- `cwd` MUST be within the Worker's declared allowed_bash_roots "
        "(e.g. `demoworkspace/autochess-ui/` for Stage D dev agent). Anywhere else → REFUSED.\n"
        "- Dangerous patterns (rm -rf /, format, git commit/push/reset, p4 submit/revert, "
        "shutdown, fork bombs) are BLOCKED regardless of cwd. Use for BUILD/RUN/DIAGNOSE only.\n"
        "- stdout + stderr + exit code returned. Each stream truncated at 5MB to prevent OOM "
        "(LLM 看到 [TRUNCATED ... bytes omitted] 标注; 用 head/tail/grep 自己缩窄).\n"
        "- Use pipes inside the command (head/tail/grep) to narrow large output yourself.\n"
        "- For long-running processes (dev server / build / test), run in background "
        "(`nohup ... > log 2>&1 &; disown`) and poll log file or HTTP via web_fetch.\n"
        "\nArguments:\n"
        "  command: str       — shell command\n"
        "  cwd: str           — absolute path in allowed_bash_roots\n"
        "  timeout_sec: int   — default 120, max 1200 (=20min). 长流程改 nohup 后台跑."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to execute."},
            "cwd": {"type": "string", "description": "Absolute working directory (must be within allowed_bash_roots)."},
            "timeout_sec": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1200,
                "description": "Timeout seconds. Default 120, max 1200 (20 min). 超过这个就 nohup 后台.",
            },
        },
        "required": ["command", "cwd"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        cmd = (args.get("command") or "").strip()
        raw_cwd = (args.get("cwd") or "").strip()
        timeout = int(args.get("timeout_sec", 120))
        timeout = max(1, min(timeout, 1200))

        if not cmd:
            raise ToolExecutionError("command is required")
        # cwd 缺省策略: agent 没传 cwd → 用 ctx.cwd (build_tool_context 设的默认 cwd) fallback
        # 完全没 ctx.cwd 才 raise (没默认可用)
        if not raw_cwd:
            ctx_cwd = (getattr(ctx, "cwd", "") or "").strip()
            if ctx_cwd:
                raw_cwd = ctx_cwd
                logger.info(f"bash: agent 没传 cwd, 用 ctx.cwd 默认值 = {raw_cwd}")
            else:
                allowed_roots = getattr(ctx, "allowed_bash_roots", None) or ()
                roots_hint = ", ".join(allowed_roots[:3]) if allowed_roots else "(no allowlist)"
                raise ToolExecutionError(
                    f"cwd 参数必填 — bash 没默认 cwd 也没 ctx.cwd. 你必须传 cwd 字段 (绝对路径, 在 allowed_bash_roots 内).\n"
                    f"allowed_bash_roots 示例: {roots_hint}\n"
                    f"示例: bash(command='ls', cwd='e:/WindowsWorkspace/demogame-knowledge-base/'). 重试, 带 cwd."
                )

        # 黑名单 (骨架级 assert)
        danger = _matches_danger(cmd)
        if danger:
            raise ToolExecutionError(
                f"bash REFUSED: command matches dangerous pattern `{danger}`.\n"
                f"Destructive operations (rm -rf, git commit/push, p4 submit, format, shutdown, ...) "
                f"are never allowed in DevBashRouter. Use only build/run/diagnose commands."
            )

        # cwd 白名单 (骨架级 assert)
        allowed_roots = getattr(ctx, "allowed_bash_roots", None) or ()
        if not allowed_roots:
            raise ToolExecutionError(
                "bash REFUSED: no allowed_bash_roots declared in tool context. "
                "This Worker has no permission to run shell commands."
            )
        # cwd sanity check: 防 LLM 输出 bug 拼出无 separator 的怪串
        # (例 "eWindowsWorkspace_scratchfigma_pull_abyssgJUhPyBeWrC6486sojoD6d" — 应是 "e:/WindowsWorkspace/_scratch/figma_pull_abyss/gJUhPyBeWrC..." 但分隔符被吞)
        # 检测: 长字符串 (>20 char) 且无 / 也无 \ → 不是合法路径
        if len(raw_cwd) > 20 and "/" not in raw_cwd and "\\" not in raw_cwd:
            raise ToolExecutionError(
                f"cwd 格式可疑 — `{raw_cwd}` 长度 {len(raw_cwd)} 但完全没路径分隔符 ('/' 或 '\\\\').\n"
                f"看起来像 LLM 输出 bug 把分隔符吞了. 检查你拼接 cwd 时是否丢了 '/' 或 ':'.\n"
                f"正确格式示例: 'e:/WindowsWorkspace/_scratch/figma_pull_abyss/<file_key>/'"
            )
        try:
            cwd_abs = Path(raw_cwd).resolve()
        except Exception as e:
            raise ToolExecutionError(f"cannot resolve cwd {raw_cwd!r}: {e}")
        if not cwd_abs.exists() or not cwd_abs.is_dir():
            raise ToolExecutionError(f"cwd does not exist or is not a dir: {cwd_abs}")

        roots_resolved = []
        root_ok = False
        for r in allowed_roots:
            try:
                rr = Path(r).resolve()
            except Exception:
                continue
            roots_resolved.append(str(rr))
            try:
                cwd_abs.relative_to(rr)
                root_ok = True
                break
            except ValueError:
                continue

        if not root_ok:
            listing = "\n  - ".join(sorted(roots_resolved))
            raise ToolExecutionError(
                f"bash REFUSED: cwd `{cwd_abs}` is outside allowed_bash_roots.\n"
                f"Allowed roots:\n  - {listing}\n"
                f"Set cwd to an absolute path inside one of those roots."
            )

        # 执行 — 显式用 bash 不走 cmd.exe (跟 Claude Code 同 pattern, 见
        # 参考项目/claude-code-analysis/src/utils/Shell.ts which('bash') + spawn).
        #
        # 历史 bug (2026-05-02 修): 之前用 subprocess.Popen(cmd, shell=True), Windows 默认
        # 走 cmd.exe. cmd.exe 不识 unix 工具 (cat / heredoc / mkdir -p), 引发:
        #   - mkdir -p "<path>" → cmd.exe 当成创 2 dir, 在 cwd 创空 -p 目录 (项目根污染)
        #   - cat > xxx << EOF → cmd.exe 报"此时不应有 <<", agent fallback 反复挣扎
        #   - 跨平台不一致 (Linux/Mac shell=True 走 /bin/sh OK, Windows shell=True 走 cmd 坏)
        #
        # 修法: shutil.which('bash') 找 git bash (Windows) / 系统 bash (Linux/Mac), 显式
        # 调 ['bash', '-c', cmd] 不走 shell=True. 跟 Claude Code 一致.
        import os as _os
        import shutil as _shutil
        bash_path = _shutil.which("bash") or "/usr/bin/bash"
        # CWD 追踪 (CC Shell.ts pwd 模式): 把 user cmd 包成
        #   { newline cmd newline }
        #   _omni_rc=$?            # 抓 user 命令真 exit code
        #   printf "...marker..." "$(pwd)" >&2  # 写 cwd marker 到 stderr
        #   exit $_omni_rc          # 用 user exit 覆盖 printf exit (返回真退出码)
        # 即使 user_cmd 失败, pwd marker 仍然写; exit code 反映 user_cmd 不是 printf.
        # 用 newline 分隔 (不用 ;), 防 user_cmd 含 heredoc / 注释行 / 未闭合等破坏 ;.
        wrapped_cmd = (
            "{\n" + cmd + "\n}\n"
            "_omni_rc=$?\n"
            "printf '\\n__OMNI_CWD_AFTER__%s__OMNI_END__\\n' "
            "\"$(pwd -P 2>/dev/null)\" >&2\n"
            "exit $_omni_rc"
        )
        try:
            popen_kwargs = dict(
                cwd=str(cwd_abs),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if _os.name == "nt":
                # Windows: 新进程组以便能 kill group. CREATE_NEW_PROCESS_GROUP = 0x00000200
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                popen_kwargs["start_new_session"] = True
            # explicit args list, not shell=True. argv = [bash, -c, wrapped_cmd]
            proc = subprocess.Popen([bash_path, "-c", wrapped_cmd], **popen_kwargs)
        except Exception as e:
            raise ToolExecutionError(f"Popen failed: {e}")

        # 输出读取 — 线程边读边截, 真 OOM 安全 (2026-05-04 改).
        # 旧 communicate() 是 post-hoc 截断: 全 read 到 Python str 再 slice, 5GB
        # 输出会先 OOM 再截. 新法用 reader thread, 命中 cap 后继续 read 但 drop
        # (drain pipe 不让 proc 阻塞写), 真正限 5MB 内存.
        _MAX_STREAM = 5 * 1024 * 1024  # 5 MiB / 流
        import threading as _threading

        def _capped_read(stream: Any, dest: list[Any]) -> None:
            chunks: list[str] = []
            total = 0
            truncated = False
            try:
                while True:
                    try:
                        chunk = stream.read(65536)
                    except (OSError, ValueError):
                        break
                    if not chunk:
                        break
                    if total < _MAX_STREAM:
                        allowed = _MAX_STREAM - total
                        if len(chunk) <= allowed:
                            chunks.append(chunk)
                            total += len(chunk)
                        else:
                            chunks.append(chunk[:allowed])
                            total += allowed
                            truncated = True
                    else:
                        truncated = True
            finally:
                dest.append(("".join(chunks), total, truncated))

        stdout_buf: list[Any] = []
        stderr_buf: list[Any] = []
        t_out = _threading.Thread(target=_capped_read, args=(proc.stdout, stdout_buf), daemon=True)
        t_err = _threading.Thread(target=_capped_read, args=(proc.stderr, stderr_buf), daemon=True)
        t_out.start(); t_err.start()
        timed_out = False
        aborted = False

        # L7 abort 协议 (Wave 8 P3, 2026-05-05): polling wait, 每 0.5s 检查 ctx.abort_event
        # 命中 → 杀整棵进程树 + raise. 跟 timeout 路径共用 kill 逻辑.
        abort_event = getattr(ctx, "abort_event", None)

        def _kill_tree() -> None:
            try:
                if _os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        capture_output=True, timeout=10,
                    )
                else:
                    _os.killpg(_os.getpgid(proc.pid), 9)
            except Exception:
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try: proc.kill()
                except Exception: pass

        import time as _time
        _poll_interval = 0.5  # 0.5s polling, 平衡响应性 + CPU
        _deadline = _time.time() + timeout
        try:
            while True:
                _remaining = _deadline - _time.time()
                if _remaining <= 0:
                    # 真 timeout
                    raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)
                # poll 一次, 等长 = min(poll_interval, remaining)
                _wait_chunk = min(_poll_interval, _remaining)
                try:
                    proc.wait(timeout=_wait_chunk)
                    returncode = proc.returncode
                    break  # 进程退出
                except subprocess.TimeoutExpired:
                    # 没退, 检查 abort
                    if abort_event is not None and abort_event.is_set():
                        _kill_tree()
                        returncode = -15  # SIGTERM 风格 ("aborted")
                        aborted = True
                        break
                    # else 继续 polling
        except subprocess.TimeoutExpired:
            _kill_tree()
            returncode = -9
            timed_out = True
        # reader thread 抓剩余输出
        t_out.join(timeout=2); t_err.join(timeout=2)
        stdout, stdout_total, stdout_truncated = stdout_buf[0] if stdout_buf else ("", 0, False)
        stderr, stderr_total, stderr_truncated = stderr_buf[0] if stderr_buf else ("", 0, False)
        stdout = (stdout or "").rstrip("\n")
        stderr = (stderr or "").rstrip("\n")

        # CWD 追踪解析 — 从 stderr 抽 __OMNI_CWD_AFTER__<path>__OMNI_END__ 标记
        cwd_after: str | None = None
        _cwd_marker_pat = re.compile(r"__OMNI_CWD_AFTER__(.*?)__OMNI_END__", re.DOTALL)
        m = _cwd_marker_pat.search(stderr)
        if m:
            cwd_after = m.group(1).strip() or None
            # 从 stderr 删 marker 行 (不让 LLM 看到内部协议)
            stderr = _cwd_marker_pat.sub("", stderr).rstrip("\n").strip()
            # 处理 marker 前的多余空行
            stderr = re.sub(r"\n{3,}", "\n\n", stderr)

        # 截断标记 — reader thread 已限 5MB / 流, 这里只补 [TRUNCATED] hint 给 LLM
        if stdout_truncated:
            stdout += (
                f"\n\n[TRUNCATED · stdout 截断 (上限 5 MiB · 已读 {stdout_total} bytes) · "
                f"用 head/tail/grep 在命令内缩窄, 或写文件后 read_file 分段读]"
            )
        if stderr_truncated:
            stderr += (
                f"\n\n[TRUNCATED · stderr 截断 (上限 5 MiB · 已读 {stderr_total} bytes) · "
                f"用 head/tail/grep 在命令内缩窄, 或写文件后 read_file 分段读]"
            )

        if aborted:
            raise ToolExecutionError(
                f"bash ABORTED by external signal (process tree killed). "
                f"Command: `{cmd[:120]}...`\n"
                f"Captured stdout: {stdout[:500]}\n"
                f"Captured stderr: {stderr[:500]}"
            )

        if timed_out:
            raise ToolExecutionError(
                f"bash TIMEOUT after {timeout}s (killed). Command: `{cmd[:120]}...`\n"
                f"Captured stdout: {stdout[:500]}\n"
                f"Captured stderr: {stderr[:500]}\n"
                f"Note (Windows): `npx`/`npm` background `&` can appear to hang subprocess even with timeout. "
                f"Use `powershell -Command 'Start-Process ... -NoNewWindow -RedirectStandardOutput log'` "
                f"for a truly detached dev server, then poll via web_fetch / playwright_probe."
            )

        parts = []
        if stdout:
            parts.append(stdout)
        if stderr:
            parts.append(f"[stderr]\n{stderr}")
        parts.append(f"[exit={returncode}]")
        # CWD 追踪 hint — 给 LLM 看 (它可以在下次 cwd 参数用新路径)
        # 一直返 cwd_after (即使等于入参), 30 字节/call 不值得跨 cygwin/Windows 比较取舍.
        # LLM 看到就知道 "上一轮 bash 留我在哪儿".
        if cwd_after:
            parts.append(f"[cwd_after={cwd_after}]")
        return "\n".join(parts)
