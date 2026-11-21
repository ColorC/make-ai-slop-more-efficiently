# [OMNI] origin=ai-ide ts=2026-05-25 type=infra
# [OMNI] material_id="material:dashboard.boss_sight.controller.omni_cli_router.py"
"""OmniCliRouter — 总控专用 cli 执行工具.

2026-05-25 范式校正: 把原 17 个总控特有 function call tool 迁移到 omni cli 子命令.
总控通过本 Router 调 omni cli (不是用 BashRouter, 因为 BashRouter 需要 BashBus 装配,
且总控不需要任意 shell 访问).

特性:
- 只允许跑 `omni ...` 子命令 (不允许 `rm -rf /` 这种)
- 自动注入 OMNI_CLI_CALLER=controller (cli access 装饰器认这个)
- 60s 超时
- stdout + stderr 合并返回 (cli 输出本身就是 LLM 要看的)
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, ClassVar

from omnicompany.core.caller_identity import CALLER_CONTROLLER, CALLER_ENV
from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolExecutionError,
)
from omnicompany.runtime.agent.agent_loop_tools import ToolContext

_log = logging.getLogger(__name__)


class OmniCliRouter(SingleToolRouter):
    """跑 omni cli 子命令. 总控通过本工具调度 / 审阅 / 提议."""

    TOOL_NAME: ClassVar[str] = "omni"
    DESCRIPTION: ClassVar[str] = (
        "Execute an `omni` CLI subcommand. Use for all dispatching / review / proposal / "
        "binding / audit operations. Examples:\n"
        "  omni worker spawn <plan_id> <prompt> --provider claude_code --model-hint auto\n"
        "  omni worker fork <subagent_id> <report_prompt>\n"
        "  omni worker bindings\n"
        "  omni worker audit-traces --lookback-hours 24\n"
        "  omni plan complete <plan_id> --status partial --assessment '...'\n"
        "  omni plan audit --missing-todo\n"
        "  omni review submit --kind markdown --tier important --title X --plan-id Y --content '...'\n"
        "  omni review list --status pending\n"
        "  omni review annotate <material_id> 'AI 批注内容'\n"
        "  omni review push <material_id> 'reason'\n"
        "  omni prompt list\n"
        "  omni propose change prompt_modification --rationale '...' --content-draft '...'\n"
        "Pass the FULL command including 'omni' prefix. Output is combined stdout+stderr. "
        "Cwd defaults to workspace root. OMNI_CLI_CALLER=controller is auto-injected."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "minLength": 5,
                "description": (
                    "Full omni cli command. Must start with 'omni '. "
                    "Use shell-like quoting for args with spaces."
                ),
            },
            "cwd": {
                "type": "string",
                "description": "Working directory. Default: workspace root.",
            },
            "timeout_sec": {
                "type": "integer",
                "minimum": 1,
                "maximum": 600,
                "default": 60,
            },
        },
        "required": ["command"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict[str, Any], ctx: ToolContext) -> str:
        cmd = (args.get("command") or "").strip()
        if not cmd:
            raise ToolExecutionError("command is required")
        # 严格白名单: 只允许 omni 子命令 (防误用全 shell)
        if not (cmd == "omni" or cmd.startswith("omni ")):
            raise ToolExecutionError(
                f"only 'omni ...' subcommands allowed via this tool; got: {cmd[:60]!r}. "
                f"For arbitrary shell, request bash tool via external session."
            )

        cwd = args.get("cwd") or self._workspace_root()
        if not Path(cwd).is_dir():
            raise ToolExecutionError(f"cwd not a directory: {cwd}")

        timeout = int(args.get("timeout_sec") or 60)

        # 解析 args — 用 shlex 处理引号 (Windows 上 posix=False 兼容 win path)
        try:
            argv_parts = shlex.split(cmd, posix=(os.name != "nt"))
        except ValueError as e:
            raise ToolExecutionError(f"command parse failed: {e}")
        if argv_parts[0] != "omni":
            raise ToolExecutionError(f"first token must be 'omni'; got {argv_parts[0]!r}")
        # 替换为真 entrypoint: `python -m omnicompany.cli.main <args>`
        # 这样不依赖 PATH 上的 omni 脚本是否安装
        argv = [sys.executable, "-m", "omnicompany.cli.main"] + argv_parts[1:]

        env = os.environ.copy()
        env[CALLER_ENV] = CALLER_CONTROLLER  # 自动注入身份 (规范要求)
        env["PYTHONIOENCODING"] = "utf-8"

        try:
            proc = subprocess.run(
                argv, cwd=cwd, env=env, capture_output=True,
                text=True, timeout=timeout, encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            return f"omni cli timeout after {timeout}s: {cmd[:120]}"
        except Exception as e:  # noqa: BLE001
            raise ToolExecutionError(f"omni cli failed to spawn: {type(e).__name__}: {e}")

        stdout = (proc.stdout or "").rstrip()
        stderr = (proc.stderr or "").rstrip()
        out_parts: list[str] = []
        if stdout:
            out_parts.append(stdout)
        if stderr:
            out_parts.append(f"[stderr]\n{stderr}")
        if proc.returncode != 0:
            out_parts.append(f"[exit code {proc.returncode}]")
        result = "\n\n".join(out_parts) if out_parts else f"(no output, exit {proc.returncode})"
        # cap output 8K — LLM ctx 友好
        if len(result) > 8000:
            result = result[:8000] + f"\n... [truncated; original length {len(result)}]"
        _log.info("omni cli exit=%d cmd=%s", proc.returncode, cmd[:120])
        return result

    @staticmethod
    def _workspace_root() -> str:
        # 委托到唯一权威 core.config.omni_workspace_root(), 不再硬编码 parents[N]
        from omnicompany.core.config import omni_workspace_root
        return str(omni_workspace_root())


__all__ = ["OmniCliRouter"]
