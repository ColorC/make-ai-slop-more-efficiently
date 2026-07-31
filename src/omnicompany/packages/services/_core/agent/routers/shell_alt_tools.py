# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-05-04T00:00:00Z type=infrastructure
"""PowerShellRouter / REPLRouter · 第九波 (2026-05-04).

PowerShell: Windows PowerShell 命令执行 (走 BashBus 但 shell=powershell.exe)
REPL: Python REPL 状态保留执行 (跨多次调用复用解释器状态)
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from io import StringIO
from typing import ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)


# ─── PowerShellRouter ────────────────────────────────────────────


class PowerShellRouter(SingleToolRouter):
    """Execute a PowerShell command (Windows PowerShell or pwsh)."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ("*",)
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ("*",)

    TOOL_NAME: ClassVar[str] = "PowerShell"
    # DESCRIPTION 1:1 复刻 cc PowerShellTool/prompt.ts::getPrompt 静态部分 (Wave 5 续, 2026-05-05)
    # 适配:
    #   - timeout 用 omnicompany 默认 60s / 600s 上限 (跟 INPUT_SCHEMA 对齐)
    #   - edition 检测当前 omnicompany 没做 — 用最稳的 5.1 兼容版指引 (cc 在 edition 未知时也是这策略)
    #   - 跳过 background task (omnicompany 没该设施)
    #   - 工具替代项用 omnicompany 命名 (Glob/Grep/Read/Edit/write_file)
    #   - 跳过 cc 的 max output truncate (omnicompany 不静默截断, 跟 BashRouter 一致)
    DESCRIPTION: ClassVar[str] = (
        "Executes a given PowerShell command with optional timeout. Working directory persists between commands; shell state (variables, functions) does not.\n"
        "\n"
        "IMPORTANT: This tool is for terminal operations via PowerShell: git, npm, docker, and PS cmdlets. DO NOT use it for file operations (reading, writing, editing, searching, finding files) - use the specialized tools for this instead.\n"
        "\n"
        "PowerShell edition: unknown — assume Windows PowerShell 5.1 for compatibility\n"
        "   - Do NOT use `&&`, `||`, ternary `?:`, null-coalescing `??`, or null-conditional `?.`. These are PowerShell 7+ only and parser-error on 5.1.\n"
        "   - To chain commands conditionally: `A; if ($?) { B }`. Unconditionally: `A; B`.\n"
        "\n"
        "Before executing the command, please follow these steps:\n"
        "\n"
        "1. Directory Verification:\n"
        "   - If the command will create new directories or files, first use `Get-ChildItem` (or `ls`) to verify the parent directory exists and is the correct location\n"
        "\n"
        "2. Command Execution:\n"
        "   - Always quote file paths that contain spaces with double quotes\n"
        "   - Capture the output of the command.\n"
        "\n"
        "PowerShell Syntax Notes:\n"
        "   - Variables use $ prefix: $myVar = \"value\"\n"
        "   - Escape character is backtick (`), not backslash\n"
        "   - Use Verb-Noun cmdlet naming: Get-ChildItem, Set-Location, New-Item, Remove-Item\n"
        "   - Common aliases: ls (Get-ChildItem), cd (Set-Location), cat (Get-Content), rm (Remove-Item)\n"
        "   - Pipe operator | works similarly to bash but passes objects, not text\n"
        "   - Use Select-Object, Where-Object, ForEach-Object for filtering and transformation\n"
        "   - String interpolation: \"Hello $name\" or \"Hello $($obj.Property)\"\n"
        "   - Registry access uses PSDrive prefixes: `HKLM:\\SOFTWARE\\...`, `HKCU:\\...` — NOT raw `HKEY_LOCAL_MACHINE\\...`\n"
        "   - Environment variables: read with `$env:NAME`, set with `$env:NAME = \"value\"` (NOT `Set-Variable` or bash `export`)\n"
        "   - Call native exe with spaces in path via call operator: `& \"C:\\Program Files\\App\\app.exe\" arg1 arg2`\n"
        "\n"
        "Interactive and blocking commands (will hang — this tool runs with -NonInteractive):\n"
        "   - NEVER use `Read-Host`, `Get-Credential`, `Out-GridView`, `$Host.UI.PromptForChoice`, or `pause`\n"
        "   - Destructive cmdlets (`Remove-Item`, `Stop-Process`, `Clear-Content`, etc.) may prompt for confirmation. Add `-Confirm:$false` when you intend the action to proceed. Use `-Force` for read-only/hidden items.\n"
        "   - Never use `git rebase -i`, `git add -i`, or other commands that open an interactive editor\n"
        "\n"
        "Passing multiline strings (commit messages, file content) to native executables:\n"
        "   - Use a single-quoted here-string so PowerShell does not expand `$` or backticks inside. The closing `'@` MUST be at column 0 (no leading whitespace) on its own line — indenting it is a parse error:\n"
        "<example>\n"
        "git commit -m @'\n"
        "Commit message here.\n"
        "Second line with $literal dollar signs.\n"
        "'@\n"
        "</example>\n"
        "   - Use `@'...'@` (single-quoted, literal) not `@\"...\"@` (double-quoted, interpolated) unless you need variable expansion\n"
        "   - For arguments containing `-`, `@`, or other characters PowerShell parses as operators, use the stop-parsing token: `git log --% --format=%H`\n"
        "\n"
        "Usage notes:\n"
        "  - The command argument is required.\n"
        "  - You can specify an optional timeout in seconds (up to 600s / 10 minutes). If not specified, commands will timeout after 60s.\n"
        "  - It is very helpful if you write a clear, concise description of what this command does.\n"
        "  - Output is NOT silently truncated — if the output is large, narrow with `Select-Object -First N`, `| Out-String -Stream | Select-Object -First N`, or `| Where-Object { ... }`.\n"
        "  - Avoid using PowerShell to run commands that have dedicated tools, unless explicitly instructed:\n"
        "    - File search: Use Glob (NOT Get-ChildItem -Recurse)\n"
        "    - Content search: Use Grep (NOT Select-String)\n"
        "    - Read files: Use Read (NOT Get-Content)\n"
        "    - Edit files: Use Edit\n"
        "    - Write files: Use Write (NOT Set-Content/Out-File)\n"
        "    - Communication: Output text directly (NOT Write-Output/Write-Host)\n"
        "  - When issuing multiple commands:\n"
        "    - If the commands are independent and can run in parallel, make multiple PowerShell tool calls in a single message.\n"
        "    - If the commands depend on each other and must run sequentially, chain them in a single PowerShell call (see edition-specific chaining syntax above).\n"
        "    - Use `;` only when you need to run commands sequentially but don't care if earlier commands fail.\n"
        "    - DO NOT use newlines to separate commands (newlines are ok in quoted strings and here-strings)\n"
        "  - Do NOT prefix commands with `cd` or `Set-Location` -- the working directory is already set to the correct project directory automatically.\n"
        "  - For git commands:\n"
        "    - Prefer to create a new commit rather than amending an existing commit.\n"
        "    - Before running destructive operations (e.g., git reset --hard, git push --force, git checkout --), consider whether there is a safer alternative that achieves the same goal. Only use destructive operations when they are truly the best approach.\n"
        "    - Never skip hooks (--no-verify) or bypass signing (--no-gpg-sign, -c commit.gpgsign=false) unless the user has explicitly asked for it. If a hook fails, investigate and fix the underlying issue."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout_sec": {"type": "integer", "minimum": 1, "maximum": 600},
        },
        "required": ["command"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        command = (args.get("command") or "").strip()
        if not command:
            raise ToolExecutionError("command is required")
        timeout_sec = int(args.get("timeout_sec", 60))

        # 选择 shell
        ps = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        if not ps:
            if os.environ.get("OMNI_POWERSHELL_DRY_RUN") == "1":
                return f"(dry-run, no pwsh installed) command was: {command}"
            raise ToolExecutionError(
                "pwsh / powershell.exe not on PATH. "
                "Install pwsh (https://aka.ms/pscore) or use Bash tool."
            )

        # 通过 -NonInteractive -Command "..." 执行
        full_cmd = [ps, "-NonInteractive", "-NoProfile", "-Command", command]
        try:
            result = subprocess.run(
                full_cmd,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout_sec,
                cwd=ctx.cwd or None,
            )
        except subprocess.TimeoutExpired:
            raise ToolExecutionError(f"PowerShell timed out ({timeout_sec}s)")
        except OSError as e:
            raise ToolExecutionError(f"PowerShell invocation failed: {e}")

        parts = []
        if result.stdout:
            parts.append(result.stdout.rstrip("\n"))
        if result.stderr:
            parts.append(f"[stderr]\n{result.stderr.rstrip()}")
        if result.returncode != 0:
            parts.append(f"[exit={result.returncode}]")
        return "\n".join(parts) if parts else "(no output)"


# ─── REPLRouter ──────────────────────────────────────────────────


class REPLRouter(SingleToolRouter):
    """Execute Python code in a persistent REPL (state preserved across calls within a session)."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    TOOL_NAME: ClassVar[str] = "REPL"
    DESCRIPTION: ClassVar[str] = (
        "Run Python code in a persistent REPL — variables defined in earlier calls survive.\n"
        "\n"
        "Use cases:\n"
        "- Iterative data exploration (load DataFrame once, query many times)\n"
        "- Build up state across LLM turns without re-importing\n"
        "\n"
        "State stored in ctx.repl_globals (dict). Captures stdout."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "reset_state": {
                "type": "boolean",
                "description": "Clear REPL globals before running (default false)",
            },
        },
        "required": ["code"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        code = args.get("code", "")
        if not isinstance(code, str) or not code.strip():
            raise ToolExecutionError("code is required (non-empty string)")
        reset = bool(args.get("reset_state", False))

        # ctx.repl_globals 跨调用共享, 没有就建
        if reset or not hasattr(ctx, "repl_globals"):
            ctx.repl_globals = {"__name__": "__omni_repl__", "__builtins__": __builtins__}  # type: ignore[attr-defined]

        # 重定向 stdout 捕获
        from contextlib import redirect_stdout, redirect_stderr
        out_buf = StringIO()
        err_buf = StringIO()
        try:
            with redirect_stdout(out_buf), redirect_stderr(err_buf):
                exec(code, ctx.repl_globals)  # type: ignore[attr-defined]
        except Exception as e:
            err_buf.write(f"\n[exception] {type(e).__name__}: {e}")

        out_text = out_buf.getvalue()
        err_text = err_buf.getvalue()
        parts = []
        if out_text:
            parts.append(out_text.rstrip("\n"))
        if err_text:
            parts.append(f"[stderr]\n{err_text.rstrip()}")
        return "\n".join(parts) if parts else "(no output; state updated)"
