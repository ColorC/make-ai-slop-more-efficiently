# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-05-04T00:00:00Z type=infrastructure
"""EnterPlanModeRouter / ExitPlanModeRouter · 计划模式状态切换 SingleTool.

参考: 参考项目/claude-code-analysis/src/tools/EnterPlanModeTool/prompt.ts + ExitPlanModeTool/prompt.ts

核心:
  - EnterPlanMode: 进入"先想清楚再写代码"的探索阶段, 标记 ToolContext (or 落盘 .omni/plan_mode.json)
  - ExitPlanMode: 退出, 表示已写完 plan, 等用户审核 (omnicompany 这里 plan 已落到 docs/plans/)
  - 状态转换在 .omni/plan_mode_state.json 持久 (跨工具调用复用)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)


_STATE_FILE = ".omni/plan_mode_state.json"


def _state_path(ctx: ToolContext) -> Path:
    base = Path(ctx.cwd) if ctx.cwd else Path.cwd()
    return base / _STATE_FILE


def _read_state(ctx: ToolContext) -> dict:
    p = _state_path(ctx)
    if not p.exists():
        return {"in_plan_mode": False}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"in_plan_mode": False}


def _write_state(ctx: ToolContext, state: dict) -> None:
    p = _state_path(ctx)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


class EnterPlanModeRouter(SingleToolRouter):
    """Enter plan mode: explore the codebase before writing code, present plan for approval."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.modify_file",)

    TOOL_NAME: ClassVar[str] = "EnterPlanMode"
    DESCRIPTION: ClassVar[str] = (
        "Enter plan mode for non-trivial implementation tasks.\n"
        "\n"
        "What happens:\n"
        "1. You explore the codebase using Glob, Grep, Read tools\n"
        "2. Understand existing patterns and architecture\n"
        "3. Design an implementation approach\n"
        "4. Write the plan to docs/plans/<topic>/plan.md\n"
        "5. Use ExitPlanMode when ready for user approval\n"
        "\n"
        "Use AskUserQuestion if you need to clarify approaches BEFORE finalizing the plan."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "Plan topic (used to name docs/plans/<topic>/)",
            },
            "rationale": {
                "type": "string",
                "description": "Why we're entering plan mode (1-2 sentences)",
            },
        },
        "required": ["topic"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        topic = (args.get("topic") or "").strip()
        if not topic:
            raise ToolExecutionError("topic is required")
        rationale = (args.get("rationale") or "").strip()

        state = _read_state(ctx)
        if state.get("in_plan_mode"):
            existing_topic = state.get("topic", "?")
            return (
                f"Already in plan mode (topic: {existing_topic}). "
                f"Use ExitPlanMode first if switching topics."
            )

        new_state = {
            "in_plan_mode": True,
            "topic": topic,
            "rationale": rationale,
            "entered_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_state(ctx, new_state)
        return (
            f"Entered plan mode (topic: {topic}). "
            f"Now: explore → design → write plan to docs/plans/[YYYY-MM-DD]{topic.upper()}/plan.md → "
            f"call ExitPlanMode for approval."
        )


class ExitPlanModeRouter(SingleToolRouter):
    """Exit plan mode after writing plan to disk; signals ready for user approval."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.modify_file",)

    TOOL_NAME: ClassVar[str] = "ExitPlanMode"
    DESCRIPTION: ClassVar[str] = (
        "Exit plan mode after writing your plan. Signals to the user that the plan is ready for review.\n"
        "\n"
        "Before calling:\n"
        "- Plan must be written to docs/plans/<topic>/plan.md\n"
        "- Plan should be unambiguous (use AskUserQuestion earlier if needed)\n"
        "\n"
        "Do NOT use AskUserQuestion to ask 'is plan okay?' — that's exactly what THIS tool does."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "plan_path": {
                "type": "string",
                "description": "Absolute path to the plan.md (for sanity check it exists)",
            },
        },
        "required": [],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        state = _read_state(ctx)
        if not state.get("in_plan_mode"):
            return "Not in plan mode. ExitPlanMode is a no-op."

        plan_path_arg = (args.get("plan_path") or "").strip()
        topic = state.get("topic", "")
        plan_exists_msg = ""
        if plan_path_arg:
            p = Path(plan_path_arg)
            if not p.is_absolute():
                raise ToolExecutionError(f"plan_path must be absolute: {plan_path_arg}")
            if not p.exists():
                raise ToolExecutionError(
                    f"plan file does not exist: {plan_path_arg}. Write the plan first."
                )
            plan_exists_msg = f" (plan at {plan_path_arg})"

        new_state = {
            "in_plan_mode": False,
            "last_topic": topic,
            "exited_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_state(ctx, new_state)
        return (
            f"Exited plan mode (topic was: {topic}){plan_exists_msg}. "
            f"User now reviews the plan; await approval before implementing."
        )
