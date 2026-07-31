# [OMNI] origin=claude-code domain=runtime/buses ts=2026-04-23T00:00:00Z type=infrastructure
# [OMNI] material_id="material:runtime.buses.bash_bus.command_executor.py"
"""BashBus · subprocess 命令执行统一入口.

覆盖 agent "执行命令"能力. 收归散落的 `subprocess.run/Popen/check_output` / `os.system` 调用.

与 Disk / Web Bus 同层次: 虽然 bash 本质为 disk+web 的载体, 但因其作为独立能力出现频率高
(git / p4 / npm / dotnet / Unity / PowerShell 调用), 单独一条 bus 便于审计与审核.

**基本审核** (拦明显危险, 运行时硬拦截):
  - 危险指令黑名单 (rm -rf / / format C: / mkfs / dd if=/dev/zero / fork bomb)
  - cwd 路径必须在项目根 / 已知工作区 / 临时目录内
  - shell=True 时 regex 扫整条命令, list 形式时扫每个 arg

**Windows 防御层** (2026-05-04 加, 对齐参考项目 + omnicompany 独有):
  - 自动重写 `>nul` / `2>nul` / `>>NUL` → `/dev/null` (照搬参考项目 rewriteWindowsNullRedirect)
  - 拒绝 mkdir/cp/mv/touch 等命令首参数含未引号反斜杠 (会被 bash 当转义符, 整串变单一目录名)
  - 拒绝 `mkdir "-p"` 这种把选项引号化当目录名的写法
  - 拒绝双层盘符混拼 (POSIX `/x/...` + Windows `X:/...` 同时出现)

**不管** (归 Guardian 合规规则):
  - 具体业务命令的语义合法性 (归各 Worker 自己判)
  - 长期进程管理 (本 bus 只管 run/check_output; Popen 场景 Phase 2 加)
"""
from __future__ import annotations

import atexit
import os
import re
import subprocess
import threading
import time
import weakref
from pathlib import Path
from typing import Union

from omnicompany.core.config import omni_workspace_root
from omnicompany.runtime.buses.base import ServiceBus
from omnicompany.runtime.buses.workspace import Workspace

# 危险指令 regex 清单 · 运行时硬拦截最小集.
# 原则: 宁漏拦也别误拦无辜命令; 合规层走 Guardian 再加.
_DANGEROUS_PATTERNS = (
    # Unix: 递归删根或关键系统路径
    re.compile(r"\brm\s+(-[rRfF]+\s+)+(/|/\*|~|/etc|/usr|/bin|/boot|/home|/root|/var)(\s|$|;|&|\|)"),
    # Windows: 递归删系统盘根
    re.compile(r"\b(del|erase)\s+/[fFsSqQ]+\s+(c:|C:)\\*(\s|$|;|&|\|)"),
    re.compile(r"\brmdir\s+/[sSqQ]+\s+(c:|C:)\\*(\s|$|;|&|\|)", re.IGNORECASE),
    # Windows: 格式化系统盘
    re.compile(r"\bformat\s+[cC]:", re.IGNORECASE),
    # Unix: 磁盘破坏
    re.compile(r"\bmkfs\.[a-zA-Z0-9]+\b"),
    re.compile(r"\bdd\s+if=/dev/(zero|urandom|random)\s+of=/dev/[sh]d[a-z]"),
    # fork bomb
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
    # Unix: 重定向输出到关键设备
    re.compile(r">\s*/dev/[sh]d[a-z]\d*(\s|$)"),
)

# cwd 允许的工作区前缀 (大小写不敏感, 前缀匹配).
#
# 工作区顶层 = omni 仓根的父目录, 由权威解析器派生而非写死. 额外机器级工作区
# (P4 / Users / Unix tmp) 通过环境变量 OMNI_ALLOWED_WORKSPACE_PREFIXES
# (os.pathsep 分隔) 外置覆盖; 未配置时沿用开发机默认, 保证本机行为不变.
def _default_allowed_cwd_prefixes() -> tuple[str, ...]:
    prefixes: list[str] = [str(omni_workspace_root().parent)]
    env = os.environ.get("OMNI_ALLOWED_WORKSPACE_PREFIXES", "")
    if env:
        prefixes.extend(p for p in env.split(os.pathsep) if p)
    else:
        # 开发机兜底 (可被上面的 env 覆盖)
        prefixes.extend(["d:\\p4", "c:\\users"])
    prefixes.extend(["/tmp", "/var/tmp", "/home"])
    return tuple(prefixes)


_DEFAULT_ALLOWED_CWD_PREFIXES = _default_allowed_cwd_prefixes()


def _match_dangerous(text: str) -> str | None:
    """返回命中 pattern 的 regex source, 或 None."""
    for pat in _DANGEROUS_PATTERNS:
        if pat.search(text):
            return pat.pattern
    return None


def _normalize(path: Path) -> str:
    return str(path.resolve()).lower()


# ─── Windows 防御层 (2026-05-04 加) ───────────────────────────────────
#
# 对齐参考项目 e:/WindowsWorkspace/参考项目/claude-code-analysis/src/utils/bash/shellQuoting.ts
# 和 .../utils/windowsPaths.ts. 解决用户 2026-05-03 反馈的四类 bash 错误产物:
#   1. nul 文件 (>nul / 2>nul 在 git bash 创建字面量文件)
#   2. 反斜杠路径破坏 (mkdir "data\X\Y" 整串变单一目录名)
#   3. -p 文件夹 (mkdir "-p" 把选项引号化当目录)
#   4. 双层盘符 (cd /e/X 后 mkdir e:/X)


# 4.1 nul 重写 (照搬参考项目 rewriteWindowsNullRedirect, anthropics/claude-code#4928)
#
# 匹配: >nul, > NUL, 2>nul, &>nul, >>nul (大小写不敏感)
# 不误伤: >null, >nullable, >nul.txt, cat nul.txt
_NUL_REDIRECT_RE = re.compile(r"(\d?&?>+\s*)[Nn][Uu][Ll](?=\s|$|[|&;)\n])")


def rewrite_windows_null_redirect(cmd: str) -> str:
    """`>nul` / `2>nul` / `>>NUL` → `/dev/null`. 用户透明, 仅审计记录原始命令."""
    return _NUL_REDIRECT_RE.sub(r"\1/dev/null", cmd)


# 4.2 路径互转 (照搬参考项目 windowsPathToPosixPath / posixPathToWindowsPath)
def windows_to_posix_path(p: str) -> str:
    r"""Windows 路径 → POSIX. C:\Users\foo → /c/Users/foo, \\server\share → //server/share."""
    if p.startswith("\\\\"):
        return p.replace("\\", "/")
    m = re.match(r"^([A-Za-z]):[/\\]", p)
    if m:
        return "/" + m.group(1).lower() + p[2:].replace("\\", "/")
    return p.replace("\\", "/")


def posix_to_windows_path(p: str) -> str:
    r"""POSIX 路径 → Windows. /c/X, /mnt/c/X, /cygdrive/c/X, //server/share."""
    if p.startswith("//"):
        return p.replace("/", "\\")
    m = re.match(r"^/mnt/([A-Za-z])(/|$)", p)
    if m:
        drive = m.group(1).upper()
        rest = p[len(f"/mnt/{m.group(1)}"):]
        return drive + ":" + (rest or "\\").replace("/", "\\")
    m = re.match(r"^/cygdrive/([A-Za-z])(/|$)", p)
    if m:
        drive = m.group(1).upper()
        rest = p[len(f"/cygdrive/{m.group(1)}"):]
        return drive + ":" + (rest or "\\").replace("/", "\\")
    m = re.match(r"^/([A-Za-z])(/|$)", p)
    if m:
        drive = m.group(1).upper()
        rest = p[2:]
        return drive + ":" + (rest or "\\").replace("/", "\\")
    return p.replace("/", "\\")


# 4.4 反斜杠路径参数检测 (omnicompany 独有, 参考项目无)
#
# 触发命令: mkdir / cp / mv / touch / rm / cd / ls
# 反斜杠场景: mkdir "data\X\Y" - bash 把 \X 当转义符, 整串变单一目录名 "data\X\Y"
# 例外: 反斜杠在单引号内 (如 `mkdir 'data\X\Y'`) 保留字面量, 不视为问题
_PATH_CMD_HEADS = ("mkdir", "cp", "mv", "touch", "rm", "cd", "ls", "rmdir")

# 匹配: 命令头 + 可选 flags + 含反斜杠的参数 (非纯引号)
# 注: 这是粗略检测 — 真严谨需要 shell 词法分析 (留给 AST 阶段)
_BACKSLASH_PATH_RE = re.compile(
    r"\b(?:" + "|".join(_PATH_CMD_HEADS) + r")\b\s+(?:-[a-zA-Z]+\s+)*"
    r"(?P<arg>[^\s'\"\-][^\s'\"]*\\[^\s'\"]+)"
)


def _check_backslash_path(cmd: str) -> str | None:
    """命令含 mkdir|cp|mv|... + 未引号反斜杠路径 → 返回拒绝原因."""
    m = _BACKSLASH_PATH_RE.search(cmd)
    if m:
        return (
            f"路径参数 '{m.group('arg')}' 含未引号反斜杠. "
            f"bash 把 \\X 当转义符, 整串会变单一目录名. "
            f"改用正斜杠 '/' 或单引号包裹整串 (如 'data\\X\\Y')."
        )
    return None


# 4.5 -p 当目录检测 (omnicompany 独有)
#
# 错误形式 1: mkdir "-p"           → -p 引号化, 当字面量目录名
# 错误形式 2: mkdir '-p'            → 同上
# 错误形式 3: mkdir -p              → -p 是合法选项, 但后无目录 → bash 会报错
_DASH_AS_DIR_RE = re.compile(r"\bmkdir\s+(?:[^|&;\n]*?\s+)?(?P<quote>[\"'])(?P<arg>-[a-zA-Z]+)(?P=quote)")
_MKDIR_DASH_P_NO_TARGET = re.compile(r"^\s*mkdir\s+-[a-zA-Z]+\s*(?:[|&;\n]|$)")


def _check_dash_as_dir(cmd: str) -> str | None:
    """`mkdir "-p"` / `mkdir '-p'` 把选项引号化当目录."""
    m = _DASH_AS_DIR_RE.search(cmd)
    if m:
        return (
            f"`{m.group('arg')}` 被引号化, 会被当目录名而非选项. "
            f"移除引号或换 `mkdir -p -- <真目录>` 形式."
        )
    if _MKDIR_DASH_P_NO_TARGET.search(cmd.strip()):
        return "`mkdir -p` 后无目录参数. 必须 `mkdir -p <dir>`."
    return None


# 4.6 双层盘符检测 (omnicompany 独有)
#
# 错误形式: 命令含 POSIX 风格 /x/Y 后又紧接 Windows 风格 X:/Y
# 例: cd /e/WindowsWorkspace && mkdir e:/X (在 cd 后已经在 e: 盘, 又拼绝对盘符)
# 例: 拼接路径产生 /e/WindowsWorkspace/e:/X 这种字面量
_DOUBLE_DRIVE_RE = re.compile(r"/[a-zA-Z]/[^/\s]+(?:/[^/\s]+)*/[a-zA-Z]:/")


def _check_double_drive(cmd: str) -> str | None:
    """检测 POSIX `/x/...` + Windows `X:/...` 混拼."""
    if _DOUBLE_DRIVE_RE.search(cmd):
        return (
            "命令含双层盘符 (POSIX `/x/...` + Windows `X:/...` 混拼). "
            "选其中一种风格, 不要拼接."
        )
    return None


# 4.7 find 禁令 (2026-05-04 紧急加, 用户明确叫停):
#
# 背景:
#   find 在 Windows + git bash 下 + subprocess.run(timeout=) 触发 TimeoutExpired
#   不会真杀子进程 (CreateProcess 后子进程独立). 23 个僵尸 find 跑了最久 30 小时,
#   累计 CPU 100k 秒, 严重拖累机器. 加上很多 find 命令是巨慢扫描 (find / / find /d/P4/ 等),
#   即使能正常退出也是性能黑洞.
# 替代:
#   - 文件名匹配 → GlobRouter (pathlib.rglob 安全有界)
#   - 内容匹配 → GrepRouter (ripgrep, 默认带 .gitignore + 类型过滤)
#   - 真 dir 列举 → ls / scandir
# 例外:
#   `findstr` (Windows 自带, 不是 *nix find), `git find-objects` 等子命令保留.
#   完整词 find 起头的 shell 命令一律拒绝.
_FIND_COMMAND_RE = re.compile(r"(?:^|[\s|&;])find(?:\s|$)(?!str)")


def _check_find_forbidden(cmd: str) -> str | None:
    """禁止 find 命令 (用户 2026-05-04 紧急叫停, 23 僵尸进程事故).

    用 GlobRouter (文件名) 或 GrepRouter (内容) 替代.
    findstr (Windows 自带不同物) 不算 find.
    """
    if _FIND_COMMAND_RE.search(cmd):
        return (
            "find 命令已被禁止 (2026-05-04 用户紧急叫停, "
            "Windows + git bash 下 subprocess timeout 不杀子进程, "
            "曾产生 23 个僵尸进程跑了 30 小时). "
            "替代方案: 文件名匹配用 Glob 工具, 内容匹配用 Grep 工具, "
            "目录列举用 `ls -la`."
        )
    return None


# 4.3 stdin 重定向 (照搬参考项目, 防 spawn 卡 stdin)
_STDIN_REDIRECT_RE = re.compile(r"<\s*[^&|]")
# 注: cat 不是交互式工具 (常用于读文件 / heredoc), 不进列表
_INTERACTIVE_HEADS = ("vi", "vim", "nano", "emacs", "less", "more", "ipython", "ssh")


def _has_stdin_redirect(cmd: str) -> bool:
    """命令已有 < 重定向 (但不是 << / <&)"""
    # 匹配 `<` 但排除 `<<` (heredoc) 和 `<&` (fd 复制)
    return bool(re.search(r"(?<![<&])<(?![<&])", cmd))


def _is_interactive(cmd: str) -> bool:
    """命令头是已知交互式工具 → 不该自动加 stdin redirect."""
    head = cmd.strip().split(None, 1)[0] if cmd.strip() else ""
    head_base = head.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]  # 去路径
    for pat in _INTERACTIVE_HEADS:
        if re.match(rf"^{pat}", head_base):
            return True
    return False


def should_add_stdin_redirect(cmd: str) -> bool:
    """非交互命令 + 无 stdin 重定向 → 应加 `< /dev/null` 防 spawn 卡死."""
    if _is_interactive(cmd):
        return False
    if _has_stdin_redirect(cmd):
        return False
    return True


# ─── 全局进程注册表 + atexit 清理 (2026-05-04 加, 357 僵尸事故修复) ───────
#
# 问题: subprocess.run(timeout=) 在 Windows + git bash 下 timeout 不杀子进程,
# Python 父退出后 grep / find / tail 子进程被 reparent 到 init, 跑几周累积成僵尸群.
# 解法: 用 Popen + 注册表跟踪所有活跃进程, atexit / 异常退出时强杀进程树.
#
# weakref.WeakSet 让进程对象正常 GC 时自动从注册表移除, 避免内存泄漏.

_ACTIVE_PROCESSES: "weakref.WeakSet[subprocess.Popen]" = weakref.WeakSet()
_REGISTRY_LOCK = threading.Lock()


def _register_process(proc: subprocess.Popen) -> None:
    with _REGISTRY_LOCK:
        _ACTIVE_PROCESSES.add(proc)


def _unregister_process(proc: subprocess.Popen) -> None:
    with _REGISTRY_LOCK:
        _ACTIVE_PROCESSES.discard(proc)


def _kill_process_tree(proc: subprocess.Popen, timeout: float = 5.0) -> bool:
    """强杀进程树. Windows 用 taskkill /F /T, POSIX 用 killpg.

    Returns:
        True 表示进程已死, False 表示尝试后仍未死.
    """
    if proc.poll() is not None:
        return True
    try:
        if os.name == "nt":
            # taskkill /F /T 杀整树
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=timeout,
            )
        else:
            # POSIX: killpg 杀整 process group (要求 Popen 时 start_new_session=True)
            try:
                os.killpg(os.getpgid(proc.pid), 9)
            except (ProcessLookupError, OSError):
                pass
    except Exception:
        pass
    # 验证: wait 一下看是否真死
    try:
        proc.wait(timeout=2)
        return True
    except subprocess.TimeoutExpired:
        # 兜底 proc.kill (本进程 SIGKILL)
        try:
            proc.kill()
            proc.wait(timeout=1)
        except Exception:
            pass
        return proc.poll() is not None


def _cleanup_active_processes() -> None:
    """atexit hook: 清空所有还活着的 BashBus 子进程.

    Python 解释器退出时调用. 防止子进程被 reparent 到 init 后变僵尸.
    """
    with _REGISTRY_LOCK:
        active = list(_ACTIVE_PROCESSES)
    for proc in active:
        try:
            if proc.poll() is None:
                _kill_process_tree(proc, timeout=2.0)
        except Exception:
            pass


atexit.register(_cleanup_active_processes)


class BashBus(ServiceBus):
    """subprocess 执行总线 (2026-05-04 重写: Popen + 真杀进程树).

    用法:
      bus = BashBus()
      result = bus.run(["git", "status"], cwd="/path/to/repo")
      assert result.returncode == 0
      print(result.stdout)

    进程生命周期:
      - 用 subprocess.Popen 启子进程 (不是 subprocess.run)
      - Windows: CREATE_NEW_PROCESS_GROUP, timeout 用 taskkill /F /T 杀整树
      - POSIX: start_new_session=True, timeout 用 os.killpg 杀整 group
      - 进程注册到 _ACTIVE_PROCESSES, atexit hook 兜底清理
      - 357 僵尸事故 (2026-05-04) 后立的硬规则
    """

    bus_name = "bash"

    def __init__(
        self,
        audit_log_path=None,
        extra_allowed_cwd_prefixes: tuple[str, ...] = (),
        *,
        workspace: Workspace | None = None,
    ):
        super().__init__(audit_log_path=audit_log_path, workspace=workspace)
        self._allowed_cwd_prefixes = tuple(
            p.lower() for p in (_DEFAULT_ALLOWED_CWD_PREFIXES + extra_allowed_cwd_prefixes)
        )

    def _precheck_cwd(self, action: str, cwd: Path | None) -> Path | None:
        if cwd is None:
            return None
        cwd_path = Path(cwd).expanduser()
        if not cwd_path.is_absolute():
            cwd_path = cwd_path.resolve()
        # 1. 若声明 workspace, 优先用 workspace.bash_cwd_prefixes (紧)
        if self.workspace is not None:
            if self.workspace.allows_bash_cwd(cwd_path):
                return cwd_path
            raise self._reject(
                action,
                f"cwd outside workspace '{self.workspace.name}' bash_cwd_prefixes",
                {
                    "cwd": str(cwd_path),
                    "workspace": self.workspace.name,
                    "bash_cwd_prefixes": list(self.workspace.bash_cwd_prefixes),
                },
            )
        # 2. Fallback: 走旧的 extra_allowed_cwd_prefixes
        norm = _normalize(cwd_path)
        if not any(norm.startswith(p) for p in self._allowed_cwd_prefixes):
            raise self._reject(
                action,
                "cwd outside allowed workspaces (declare workspace or add via extra_allowed_cwd_prefixes)",
                {"cwd": str(cwd_path), "allowed_prefixes": list(self._allowed_cwd_prefixes)},
            )
        return cwd_path

    def _preprocess_cmd(
        self, action: str, cmd: Union[str, list[str]]
    ) -> Union[str, list[str]]:
        """Windows 防御层 (2026-05-04 加, 在 _precheck_cmd 之前跑).

        步骤:
          1. 自动重写 (用户透明): >nul / 2>nul → /dev/null. 审计记录原始 vs 重写后.
          2. 拒绝级检测: 反斜杠路径 / -p 当目录 / 双层盘符 → raise BusRejection.

        list 形式命令: 把每段 join 后跑检测, 但不修改 list (重写仅作用于 str 形式).
        """
        # 转成字符串做检测
        if isinstance(cmd, str):
            cmd_str = cmd
        elif isinstance(cmd, (list, tuple)):
            cmd_str = " ".join(str(a) for a in cmd)
        else:
            raise self._reject(
                action,
                f"unsupported cmd type: {type(cmd).__name__}",
                {"cmd": repr(cmd)},
            )

        # 步骤 1: nul 重写 (仅对 str 形式生效, list 形式参数已分隔, 不会有 redirect 拼到一起)
        if isinstance(cmd, str):
            rewritten = rewrite_windows_null_redirect(cmd_str)
            if rewritten != cmd_str:
                self._audit(
                    "preprocess.nul_rewrite",
                    {"original": cmd_str, "rewritten": rewritten},
                    ok=True,
                )
                cmd = rewritten
                cmd_str = rewritten

        # 步骤 2: 拒绝级检测
        for checker_name, checker in (
            ("find_forbidden", _check_find_forbidden),
            ("backslash_path", _check_backslash_path),
            ("dash_as_dir", _check_dash_as_dir),
            ("double_drive", _check_double_drive),
        ):
            reason = checker(cmd_str)
            if reason:
                raise self._reject(
                    action,
                    f"command rejected by Windows defense layer ({checker_name}): {reason}",
                    {"cmd": cmd_str, "checker": checker_name},
                )

        return cmd

    def _precheck_cmd(self, action: str, cmd: Union[str, list[str]]) -> str:
        """扫描危险指令. 返回用于审计的命令字符串表示."""
        if isinstance(cmd, str):
            cmd_str = cmd
        elif isinstance(cmd, (list, tuple)):
            cmd_str = " ".join(str(a) for a in cmd)
        else:
            raise self._reject(
                action,
                f"unsupported cmd type: {type(cmd).__name__}",
                {"cmd": repr(cmd)},
            )
        danger = _match_dangerous(cmd_str)
        if danger:
            raise self._reject(
                action,
                "command matches dangerous pattern",
                {"cmd": cmd_str, "pattern": danger},
            )
        return cmd_str

    def run(
        self,
        cmd: Union[str, list[str]],
        *,
        cwd: Union[str, Path, None] = None,
        timeout: float | None = None,
        env: dict | None = None,
        input: str | None = None,
        capture_output: bool = True,
        check: bool = False,
        dry_run: bool = False,
        shell: bool | None = None,
    ) -> subprocess.CompletedProcess:
        """执行命令 · 返回 CompletedProcess.

        Args:
          cmd: str (shell=True) 或 list[str] (shell=False, 默认).
          cwd: 工作目录, 必须在允许的工作区内.
          timeout: 超时秒数.
          env: 环境变量 (None 继承).
          input: stdin 输入.
          capture_output: True 则 capture stdout+stderr.
          check: True 则非零 returncode 抛异常.
          dry_run: True 则仅记审计, 不真执行 (返回 dummy CompletedProcess).
          shell: 默认 True 当 cmd 为 str, False 当 list. 可覆盖.
        """
        # Windows 防御层: nul 重写 + 错误形式拒绝 (在危险指令检查之前)
        cmd = self._preprocess_cmd("exec", cmd)
        cmd_str = self._precheck_cmd("exec", cmd)
        cwd_path = self._precheck_cwd("exec", cwd)

        if shell is None:
            shell = isinstance(cmd, str)

        audit_payload = {
            "cmd": cmd_str,
            "cwd": str(cwd_path) if cwd_path else None,
            "shell": shell,
            "timeout": timeout,
            "dry_run": dry_run,
        }

        if dry_run:
            self._audit("exec", {**audit_payload, "dry_run_only": True})
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr="[dry-run] not executed"
            )

        # ── 用 Popen + 自管 timeout, 真杀进程树 (2026-05-04 357 僵尸事故修复) ──
        start = time.perf_counter()
        popen_kwargs: dict = dict(
            cwd=cwd_path,
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE if capture_output else None,
            stdin=subprocess.PIPE if input is not None else None,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=shell,
        )
        # 进程树 kill 的前置条件: 创建独立进程组 / session
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen(cmd, **popen_kwargs)
        except Exception as e:
            self._audit(
                "exec",
                {**audit_payload, "error": f"Popen failed: {e}"},
                ok=False,
            )
            raise

        _register_process(proc)

        try:
            # communicate 处理 input + 读 stdout/stderr + 等 wait, 一次搞定
            try:
                stdout, stderr = proc.communicate(input=input, timeout=timeout)
                returncode = proc.returncode
                timed_out = False
            except subprocess.TimeoutExpired:
                # 真杀进程树 (子 + 孙都杀)
                _kill_process_tree(proc, timeout=5.0)
                # communicate 一次拿剩余输出 (不再 raise, 因为子已死)
                try:
                    stdout, stderr = proc.communicate(timeout=2.0)
                except subprocess.TimeoutExpired:
                    stdout, stderr = ("", "")
                returncode = -9  # SIGKILL 风格标记
                timed_out = True
                # 抛 TimeoutExpired 给上层 (保持向后兼容). 进程已死, audit 已记
                elapsed = (time.perf_counter() - start) * 1000
                self._audit(
                    "exec",
                    {
                        **audit_payload,
                        "returncode": returncode,
                        "elapsed_ms": elapsed,
                        "error": "TimeoutExpired",
                        "timeout_limit": timeout,
                        "killed_tree": True,
                    },
                    ok=False,
                )
                raise subprocess.TimeoutExpired(cmd, timeout, output=stdout, stderr=stderr)
        finally:
            _unregister_process(proc)

        elapsed_ms = (time.perf_counter() - start) * 1000
        stdout_size = len(stdout) if stdout else 0
        stderr_size = len(stderr) if stderr else 0
        # 构造 CompletedProcess 跟旧 subprocess.run 接口兼容
        result = subprocess.CompletedProcess(
            args=cmd, returncode=returncode, stdout=stdout, stderr=stderr,
        )
        self._audit(
            "exec",
            {
                **audit_payload,
                "returncode": result.returncode,
                "elapsed_ms": elapsed_ms,
                "stdout_bytes": stdout_size,
                "stderr_bytes": stderr_size,
            },
            ok=(result.returncode == 0),
        )

        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, cmd, output=result.stdout, stderr=result.stderr
            )
        return result

    def check_output(
        self,
        cmd: Union[str, list[str]],
        **kw,
    ) -> str:
        """便捷包装: run(check=True, capture_output=True).stdout."""
        kw.setdefault("capture_output", True)
        result = self.run(cmd, check=True, **kw)
        return result.stdout or ""
