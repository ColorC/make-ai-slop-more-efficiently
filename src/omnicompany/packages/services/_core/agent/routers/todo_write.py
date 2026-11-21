# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-05-04T00:00:00Z type=infrastructure
"""TodoWriteRouter · 任务列表 SingleTool, 对齐 claude-code TodoWriteTool.

参考: 参考项目/claude-code-analysis/src/tools/TodoWriteTool/prompt.ts

核心行为:
  - 替换/更新整个待办列表 (LLM 每次重写整个 list)
  - 每条 todo 必含 content (imperative) + activeForm (present continuous) + status
  - status ∈ {pending, in_progress, completed}
  - 强约束: 同一时刻最多 1 个 in_progress
  - 落盘到 ctx.cwd/.omni/agent_todos.json (供查询/恢复)
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


_VALID_STATUS = ("pending", "in_progress", "completed")
_STATUS_ICON = {"pending": "○", "in_progress": "◉", "completed": "✓"}


class TodoWriteRouter(SingleToolRouter):
    """Create and manage a structured task list for the agent's current session."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.modify_file",)

    TOOL_NAME: ClassVar[str] = "TodoWrite"
    DESCRIPTION: ClassVar[str] = (
        "Create and manage a structured task list for the current session.\n"
        "\n"
        "When to use:\n"
        "- Complex multi-step tasks (3+ distinct steps)\n"
        "- User provides multiple tasks (numbered or comma-separated)\n"
        "- After receiving new instructions: capture as todos\n"
        "- Mark in_progress BEFORE starting; only ONE in_progress at a time\n"
        "- Mark completed IMMEDIATELY after finishing\n"
        "\n"
        "When NOT to use:\n"
        "- Single trivial task\n"
        "- Purely conversational/informational requests\n"
        "\n"
        "Each todo MUST have:\n"
        "- content: imperative form (e.g. 'Run tests')\n"
        "- activeForm: present continuous (e.g. 'Running tests')\n"
        "- status: pending / in_progress / completed"
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "The complete updated todo list (replaces previous list).",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "minLength": 1, "description": "Imperative form"},
                        "activeForm": {"type": "string", "minLength": 1, "description": "Present continuous form"},
                        "status": {"type": "string", "enum": list(_VALID_STATUS)},
                    },
                    "required": ["content", "activeForm", "status"],
                },
            },
        },
        "required": ["todos"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        todos = args.get("todos", [])
        if not isinstance(todos, list):
            raise ToolExecutionError("todos must be a list")

        # 校验每条
        in_progress_count = 0
        for i, t in enumerate(todos):
            if not isinstance(t, dict):
                raise ToolExecutionError(f"todos[{i}] must be a dict")
            content = t.get("content", "")
            active = t.get("activeForm", "")
            status = t.get("status", "")
            if not content or not isinstance(content, str):
                raise ToolExecutionError(f"todos[{i}].content is required (non-empty string)")
            if not active or not isinstance(active, str):
                raise ToolExecutionError(f"todos[{i}].activeForm is required (non-empty string)")
            if status not in _VALID_STATUS:
                raise ToolExecutionError(
                    f"todos[{i}].status must be one of {_VALID_STATUS}, got {status!r}"
                )
            if status == "in_progress":
                in_progress_count += 1

        if in_progress_count > 1:
            raise ToolExecutionError(
                f"only ONE todo can be in_progress at a time (got {in_progress_count}). "
                "Mark all but one as pending."
            )

        # 落盘 (best-effort, 失败仅 warn 不抛)
        try:
            cwd = Path(ctx.cwd) if ctx.cwd else Path.cwd()
            omni_dir = cwd / ".omni"
            omni_dir.mkdir(parents=True, exist_ok=True)
            todos_file = omni_dir / "agent_todos.json"
            data = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "turn": ctx.turn_number,
                "todos": todos,
            }
            todos_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("[TodoWrite] persist failed (non-fatal): %s", e)

        # 返回格式化展示
        if not todos:
            return "Todos cleared."
        lines = ["Todos updated:"]
        for t in todos:
            icon = _STATUS_ICON.get(t["status"], "?")
            lines.append(f"  {icon} [{t['status']}] {t['content']}")
        return "\n".join(lines)
