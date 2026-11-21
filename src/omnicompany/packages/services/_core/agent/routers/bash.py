# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-04-23T00:00:00Z type=infrastructure
# [OMNI] material_id="material:core.agent.routers.bash.executor.py"
"""BashRouter · 通用 Bash SingleTool, 底层走 BashBus.

与 `packages/domains/demogame/ux/routers/_safe_bash.py::SafeBashRouter` 的关系:
  - SafeBashRouter 是成熟的 demogame/ux 业务特化版 (白名单命令 / 路径白名单 / 50KB 截断).
  - 本 Router 是 `services/agent` 层的**通用基类** — 底层复用 BashBus 获得:
    * 工作区安全网 (workspace.bash_cwd_prefixes 硬限 cwd)
    * 危险命令 regex 黑名单 (rm -rf / / format C: / mkfs / dd / fork bomb)
    * 审计回流 EventBus
  - 子类 override `_validate_command` 加业务白名单 (例: config_service 只允 p4/python/git).
  - 不强制统一 SafeBashRouter — demogame/ux 迁移可选, 不在本 Phase 做.

**输出策略** (对齐 `feedback_no_defensive_truncation` 铁律):
  - 默认**不截断** stdout/stderr. 大输出由调用者 (LLM) 自己 pipe `head`/`tail`/`grep` 精确过滤.
  - 命令超时 raise `TIMEOUT after N s` (诚实报错, 不静默).

**示例子类** (config_service 用):

```python
class ConfigServiceBashRouter(BashRouter):
    TOOL_NAME = "bash"
    _WHITELIST_HEADS = ("p4", "python", "git", "ls", "cat", "head", "tail", "grep", "find")

    def _validate_command(self, command: str) -> tuple[bool, str]:
        head = command.strip().split(None, 1)[0] if command.strip() else ""
        if head not in self._WHITELIST_HEADS:
            return False, f"command '{head}' not in whitelist"
        return True, ""
```
"""
from __future__ import annotations

import logging
import subprocess
from typing import Any, ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolExecutionError,
)
from omnicompany.runtime.agent.agent_loop_tools import ToolContext
from omnicompany.runtime.buses import BashBus, BusRejection
from omnicompany.runtime.buses.persistent_shell import PersistentShellSession

logger = logging.getLogger(__name__)


# DESCRIPTION 1:1 复刻 cc BashTool/prompt.ts::getSimplePrompt 静态部分 (Wave 5 续, 2026-05-05)
# 来源: 参考项目/claude-code-analysis/src/tools/BashTool/prompt.ts L275-369
# 适配:
#   - 工具名引用改 omnicompany 命名 (Glob 大写 / glob 小写 / Grep 大写 / grep 小写 / Read / Edit / write_file)
#   - timeout 数值用 omnicompany BashRouter 实际默认 (60s / 600s)
#   - 跳过 sandbox 段 (claude.ai 沙盒特有, omnicompany 走 BashBus + workspace.bash_cwd_prefixes)
#   - 跳过 undercover / claude.ai 商品特有段
#   - 跳过 background task / run_in_background 段 (omnicompany 没该设施)
#   - 保留 git commit / PR 安全规则 (omnicompany dev 工作流通用)
#   - 加 omnicompany 独有铁律 (find 禁令 / 反斜杠路径拒)
_DEFAULT_DESCRIPTION = (
    "Executes a given bash command and returns its output.\n"
    "\n"
    "The working directory persists between commands, but shell state does not. The shell environment is initialized from the user's profile (bash or zsh).\n"
    "\n"
    "IMPORTANT: Avoid using this tool to run `find`, `grep`, `cat`, `head`, `tail`, `sed`, `awk`, or `echo` commands, unless explicitly instructed or after you have verified that a dedicated tool cannot accomplish your task. Instead, use the appropriate dedicated tool as this will provide a much better experience for the user:\n"
    "\n"
    " - File search: Use Glob (NOT find or ls)\n"
    " - Content search: Use Grep (NOT grep or rg)\n"
    " - Read files: Use Read (NOT cat/head/tail)\n"
    " - Edit files: Use Edit (NOT sed/awk)\n"
    " - Write files: Use Write (NOT echo >/cat <<EOF)\n"
    " - Communication: Output text directly (NOT echo/printf)\n"
    "While the Bash tool can do similar things, it's better to use the built-in tools as they provide a better user experience and make it easier to review tool calls and give permission.\n"
    "\n"
    "# Instructions\n"
    " - If your command will create new directories or files, first use this tool to run `ls` to verify the parent directory exists and is the correct location.\n"
    " - Always quote file paths that contain spaces with double quotes in your command (e.g., cd \"path with spaces/file.txt\")\n"
    " - Try to maintain your current working directory throughout the session by using absolute paths and avoiding usage of `cd`. You may use `cd` if the User explicitly requests it.\n"
    " - You may specify an optional timeout in seconds (up to 600s / 10 minutes). By default, your command will timeout after 60s.\n"
    " - When issuing multiple commands:\n"
    "  - If the commands are independent and can run in parallel, make multiple Bash tool calls in a single message. Example: if you need to run \"git status\" and \"git diff\", send a single message with two Bash tool calls in parallel.\n"
    "  - If the commands depend on each other and must run sequentially, use a single Bash call with '&&' to chain them together.\n"
    "  - Use ';' only when you need to run commands sequentially but don't care if earlier commands fail.\n"
    "  - DO NOT use newlines to separate commands (newlines are ok in quoted strings).\n"
    " - For git commands:\n"
    "  - Prefer to create a new commit rather than amending an existing commit.\n"
    "  - Before running destructive operations (e.g., git reset --hard, git push --force, git checkout --), consider whether there is a safer alternative that achieves the same goal. Only use destructive operations when they are truly the best approach.\n"
    "  - Never skip hooks (--no-verify) or bypass signing (--no-gpg-sign, -c commit.gpgsign=false) unless the user has explicitly asked for it. If a hook fails, investigate and fix the underlying issue.\n"
    " - Avoid unnecessary `sleep` commands:\n"
    "  - Do not sleep between commands that can run immediately — just run them.\n"
    "  - If your command is long running, the timeout should be increased instead of polling with sleep.\n"
    "  - Do not retry failing commands in a sleep loop — diagnose the root cause.\n"
    "  - If you must poll an external process, use a check command (e.g. `gh run view`) rather than sleeping first.\n"
    "  - If you must sleep, keep the duration short (1-5 seconds) to avoid blocking the pipeline.\n"
    "\n"
    "# omnicompany-specific constraints\n"
    " - The `find` command is REJECTED at the BashBus layer (357 zombie process incident 2026-05-04). Use Glob (file names) or Grep (file contents) instead.\n"
    " - Path arguments containing unquoted backslashes (e.g. `mkdir data\\X\\Y`) are REJECTED — bash treats `\\X` as escape. Use forward slashes (`data/X/Y`) or single-quote the whole argument.\n"
    " - `mkdir \"-p\"` (option as quoted directory name) is REJECTED. Use `mkdir -p <dir>` without quoting the option.\n"
    " - Mixed POSIX/Windows path drive (e.g. `cd /e/X && mkdir e:/X`) is REJECTED. Pick one path style per command.\n"
    " - cwd must be within the project workspace (`workspace.bash_cwd_prefixes`); commands outside the workspace will be rejected by BashBus.\n"
    "\n"
    "# Committing changes with git\n"
    "\n"
    "Only create commits when requested by the user. If unclear, ask first. When the user asks you to create a new git commit, follow these steps carefully:\n"
    "\n"
    "Git Safety Protocol:\n"
    "- NEVER update the git config\n"
    "- NEVER run destructive git commands (push --force, reset --hard, checkout ., restore ., clean -f, branch -D) unless the user explicitly requests these actions. Taking unauthorized destructive actions is unhelpful and can result in lost work, so it's best to ONLY run these commands when given direct instructions\n"
    "- NEVER skip hooks (--no-verify, --no-gpg-sign, etc) unless the user explicitly requests it\n"
    "- NEVER run force push to main/master, warn the user if they request it\n"
    "- CRITICAL: Always create NEW commits rather than amending, unless the user explicitly requests a git amend. When a pre-commit hook fails, the commit did NOT happen — so --amend would modify the PREVIOUS commit, which may result in destroying work or losing previous changes. Instead, after hook failure, fix the issue, re-stage, and create a NEW commit\n"
    "- When staging files, prefer adding specific files by name rather than using \"git add -A\" or \"git add .\", which can accidentally include sensitive files (.env, credentials) or large binaries\n"
    "- NEVER commit changes unless the user explicitly asks you to. It is VERY IMPORTANT to only commit when explicitly asked, otherwise the user will feel that you are being too proactive\n"
    "\n"
    "1. Run the following bash commands in parallel to understand the current state:\n"
    "  - git status (see all untracked files; never use -uall flag)\n"
    "  - git diff (see staged and unstaged changes)\n"
    "  - git log (see recent commit message style)\n"
    "2. Analyze all staged changes and draft a concise (1-2 sentences) commit message that focuses on the \"why\" rather than the \"what\".\n"
    "3. Add specific files by name + create the commit with the message + run git status to verify success.\n"
    "4. If the commit fails due to pre-commit hook: fix the issue and create a NEW commit (never --amend).\n"
    "\n"
    "Important notes:\n"
    "- Never use git commands with the -i flag (like git rebase -i or git add -i) since they require interactive input which is not supported.\n"
    "- Do not use --no-edit with git rebase commands.\n"
    "- If there are no changes to commit, do not create an empty commit.\n"
    "- ALWAYS pass multiline commit messages via a HEREDOC: `git commit -m \"$(cat <<'EOF'\\nmessage\\nEOF\\n)\"`\n"
    "\n"
    "# Output\n"
    "\n"
    "On success, returns the command's stdout (and stderr if any), with the exit code prefixed when non-zero. Output is NOT silently truncated — if the output is large, narrow with `head -n N`, `tail -n N`, or `grep PATTERN` to keep the agent context small."
)


class BashRouter(SingleToolRouter):
    # bash 是通用工具, 任何外部 IO 都可能 ('*' 弱声明)
    CONSUMED_META_IO = ("*",)
    PRODUCED_META_IO = ("*",)

    """Generic Bash tool backed by BashBus.

    Subclasses SHOULD override TOOL_NAME / DESCRIPTION / `_validate_command`
    to narrow the allowed command set to the package's needs.

    Do NOT override `_execute` unless you need behavior that bypasses BashBus
    (which would defeat the audit + safety net).
    """

    TOOL_NAME: ClassVar[str] = "bash"
    DESCRIPTION: ClassVar[str] = _DEFAULT_DESCRIPTION
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to execute (shell syntax supported).",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory. Must be within workspace.bash_cwd_prefixes. Ignored when `persistent=true` (session manages cwd).",
            },
            "timeout_sec": {
                "type": "integer",
                "minimum": 1,
                "maximum": 600,
                "description": "Timeout in seconds. Default 60.",
            },
            "persistent": {
                "type": "boolean",
                "description": "If true, run in a persistent shell session (cd / export persist across calls). Default false (each call is a fresh subprocess via BashBus).",
            },
        },
        "required": ["command"],
    }
    IS_READONLY: ClassVar[bool] = False
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    # 持久 session 实例 — Wave 4 + Wave 8 (2026-05-05): persistent=true 时启
    # 跨 _execute 调用复用 (cwd / env 持久). lazy 构建, 第一次 persistent 调用才起.

    def __init__(self, *, bash_bus: BashBus, bus: Any | None = None, **kw: Any):
        super().__init__(bus=bus, **kw)
        self._bash_bus = bash_bus
        # Wave 4 续 (2026-05-05): persistent shell session lazy 实例.
        # 同 BashRouter 实例 (跨 agent turn) 共享, cwd / export 跨调用持久.
        self._persistent_session: PersistentShellSession | None = None

    def _validate_command(self, command: str) -> tuple[bool, str]:
        """Subclass hook: return (ok, reason). Default accepts anything BashBus accepts.

        Override to narrow with a package whitelist. The BashBus dangerous-pattern
        blacklist always runs on top of whatever this method returns.
        """
        return True, ""

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        command = args.get("command", "")
        if not command or not isinstance(command, str):
            raise ToolExecutionError("command is required (non-empty string)")

        cwd = args.get("cwd")
        timeout = int(args.get("timeout_sec", 60))
        persistent = bool(args.get("persistent", False))

        ok, reason = self._validate_command(command)
        if not ok:
            raise ToolExecutionError(f"BLOCKED: {reason}")

        # Wave 4 续 (2026-05-05) + Wave 8 集成: persistent=true 走 PersistentShellSession
        # cwd / export 跨调用持久; 默认 false 走 BashBus 每次 fresh subprocess.
        if persistent:
            return self._run_persistent(command, cwd=cwd, timeout=timeout, ctx=ctx)

        try:
            result = self._bash_bus.run(
                command,
                cwd=cwd,
                timeout=timeout,
                shell=True,
                capture_output=True,
                check=False,
            )
        except BusRejection as exc:
            raise ToolExecutionError(f"BLOCKED by BashBus: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ToolExecutionError(f"TIMEOUT after {timeout}s") from exc

        stdout = result.stdout or ""
        stderr = result.stderr or ""
        parts: list[str] = []
        if stdout:
            parts.append(stdout.rstrip("\n"))
        if stderr:
            parts.append(f"[stderr]\n{stderr.rstrip(chr(10))}")
        if result.returncode != 0:
            parts.append(f"[returncode={result.returncode}]")
        return "\n".join(parts) if parts else "(no output)"

    def _run_persistent(
        self, command: str, *, cwd: str | None, timeout: int, ctx: ToolContext,
    ) -> str:
        """走 PersistentShellSession 跑命令, cwd / env 跨调用持久.

        - 第一次调用时 lazy 起 session (取 ctx.cwd 或参数 cwd 作初始 cwd)
        - 后续调用 session 自管 cwd (cd 持久), 显式参数 cwd 会被忽略 (跟 cc shell 一致)
        - 透传 ctx.abort_event 给 session (Wave 8 集成: 命中 → 杀进程树)
        - 危险命令黑名单复用 BashBus (BashBus 不在路径上, 但黑名单是 module-level regex)
        """
        # 首次启 session: 用 ctx.cwd 作初始 cwd, 若没有则用入参 cwd, 否则系统 cwd
        if self._persistent_session is None:
            initial_cwd = (
                cwd
                or getattr(ctx, "cwd", None)
                or None  # PersistentShellSession 自己 fallback os.getcwd()
            )
            self._persistent_session = PersistentShellSession(cwd=initial_cwd)

        # 危险命令黑名单 — 复用 BashBus 的 module-level 检测 (即使不走 BashBus.run)
        # 安全网必须保留, 不让 persistent 模式绕过
        from omnicompany.runtime.buses.bash_bus import _match_dangerous
        danger = _match_dangerous(command)
        if danger:
            raise ToolExecutionError(
                f"BLOCKED: command matches dangerous pattern `{danger}`. "
                f"persistent=true does not bypass safety checks."
            )

        abort_event = getattr(ctx, "abort_event", None)
        try:
            stdout, stderr, rc = self._persistent_session.run(
                command,
                timeout=float(timeout),
                abort_event=abort_event,
            )
        except RuntimeError as exc:
            # PersistentShellSession 在 closed / aborted 时 raise RuntimeError
            if "aborted" in str(exc):
                raise ToolExecutionError(f"ABORTED by external signal: {exc}")
            raise ToolExecutionError(f"persistent shell error: {exc}")
        except subprocess.TimeoutExpired:
            raise ToolExecutionError(f"TIMEOUT after {timeout}s (persistent session)")

        parts: list[str] = []
        if stdout:
            parts.append(stdout.rstrip("\n"))
        if stderr:
            parts.append(f"[stderr]\n{stderr.rstrip(chr(10))}")
        if rc != 0:
            parts.append(f"[returncode={rc}]")
        # Hint: 当前 session cwd (LLM 可见, 知道 cd 已生效)
        parts.append(f"[session_cwd={self._persistent_session.cwd}]")
        return "\n".join(parts) if parts else "(no output)"
