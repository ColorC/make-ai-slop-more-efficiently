# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-05-04T00:00:00Z type=infrastructure
"""SleepRouter · 等待固定时长 SingleTool, 对齐 claude-code SleepTool.

参考: 参考项目/claude-code-analysis/src/tools/SleepTool/prompt.ts

核心行为:
  - 等待 `seconds` 秒, 返回实际等待时长
  - 上限保护 (max 600s = 10 分钟, 防 LLM 误传巨大数值锁死循环)
  - 不持有 shell 进程 (优于 Bash(sleep ...))
  - 单元测试时支持 SLEEP_TOOL_INSTANT=1 立即返回不真等
"""
from __future__ import annotations

import logging
import os
import time
from typing import ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)


_MAX_SECONDS = 600  # 10 分钟硬上限


class SleepRouter(SingleToolRouter):
    """Wait for a specified duration without holding a shell process."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    TOOL_NAME: ClassVar[str] = "Sleep"
    DESCRIPTION: ClassVar[str] = (
        "Wait for a specified duration. Use this when:\n"
        "- The user tells you to wait or rest\n"
        "- You have nothing to do and need to defer (e.g. polling)\n"
        "- Waiting for an external event (e.g. soak time on a deploy)\n"
        "\n"
        "Prefer this over `Bash(sleep ...)` — doesn't hold a shell process.\n"
        "Each wake costs an API call; the prompt cache expires after 5 min — balance accordingly.\n"
        f"Max sleep is {_MAX_SECONDS} seconds (10 min)."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "seconds": {
                "type": "number",
                "minimum": 0,
                "maximum": _MAX_SECONDS,
                "description": f"Duration in seconds (max {_MAX_SECONDS})",
            },
            "reason": {
                "type": "string",
                "description": "Why you're sleeping (telemetry, shown to user)",
            },
        },
        "required": ["seconds"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        try:
            seconds = float(args.get("seconds", 0))
        except (TypeError, ValueError):
            raise ToolExecutionError("seconds must be a number")
        if seconds < 0:
            raise ToolExecutionError("seconds must be >= 0")
        if seconds > _MAX_SECONDS:
            raise ToolExecutionError(
                f"seconds must be <= {_MAX_SECONDS} (got {seconds}). "
                f"For longer waits, use ScheduleCron / Monitor or break into multiple sleeps."
            )

        reason = (args.get("reason") or "").strip()

        # 单测捷径: 立即返回 (不真等)
        if os.environ.get("SLEEP_TOOL_INSTANT") == "1":
            return f"Slept for {seconds}s (instant mode, dry run). Reason: {reason or '(none)'}"

        t0 = time.time()
        time.sleep(seconds)
        elapsed = time.time() - t0
        return f"Slept for {elapsed:.2f}s. Reason: {reason or '(none)'}"
