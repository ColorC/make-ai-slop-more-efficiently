# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-05-04T00:00:00Z type=infrastructure
"""ScheduleCronRouter · 创建/更新/列出/删除/触发定时任务 SingleTool.

参考: 参考项目/claude-code-analysis/src/tools/ScheduleCronTool/

实现:
  - 落盘 .omni/cron/<name>.json (一个 cron 任务一个 json)
  - 支持 create / update / list / delete / run_now 子命令
  - 实际触发由外部 cron 守护 (omnicompany 已有 sentinel, 或借 OS cron) 消费 .omni/cron/
  - 不依赖 claude.ai 的远程 cron, omnicompany 自管
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)


_VALID_ACTIONS = ("create", "update", "list", "delete", "run_now")
# 简化 cron: 5 字段 (分 时 日 月 周), 或保留字 @daily / @hourly / @weekly
_CRON_FIELD_RE = re.compile(r"^(\S+\s+){4}\S+$")
_PRESETS = {"@hourly", "@daily", "@weekly", "@monthly", "@yearly"}


def _cron_dir(ctx: ToolContext) -> Path:
    base = Path(ctx.cwd) if ctx.cwd else Path.cwd()
    d = base / ".omni" / "cron"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _validate_cron(expr: str) -> None:
    if expr in _PRESETS:
        return
    if _CRON_FIELD_RE.match(expr.strip()):
        return
    raise ToolExecutionError(
        f"invalid cron expression: {expr!r}. "
        f"Use 5-field standard (e.g. '0 * * * *') or preset {sorted(_PRESETS)}"
    )


class ScheduleCronRouter(SingleToolRouter):
    """Create / update / list / delete / run scheduled tasks (omnicompany-local cron)."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.modify_file",)

    TOOL_NAME: ClassVar[str] = "ScheduleCron"
    DESCRIPTION: ClassVar[str] = (
        "Schedule recurring tasks (cron expressions) consumed by omnicompany's sentinel.\n"
        "\n"
        "Actions:\n"
        "- `create`: new cron task (params: name, schedule, command/prompt)\n"
        "- `update`: modify existing (params: name + fields to update)\n"
        "- `list`: show all cron tasks\n"
        "- `delete`: remove a task (params: name)\n"
        "- `run_now`: manually trigger immediately (params: name)\n"
        "\n"
        "Schedule formats: standard cron (`m h dom mon dow`) or @daily / @hourly / @weekly / @monthly / @yearly."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(_VALID_ACTIONS)},
            "name": {"type": "string", "description": "Task name (filesystem-safe)"},
            "schedule": {"type": "string", "description": "Cron expression"},
            "command": {"type": "string", "description": "Bash command to run"},
            "prompt": {"type": "string", "description": "Or: agent prompt to run"},
            "description": {"type": "string", "description": "Human-readable description"},
        },
        "required": ["action"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        action = args.get("action", "")
        if action not in _VALID_ACTIONS:
            raise ToolExecutionError(f"action must be one of {_VALID_ACTIONS}")

        cron_dir = _cron_dir(ctx)

        if action == "list":
            tasks = []
            for f in sorted(cron_dir.glob("*.json")):
                if f.name.startswith("."):
                    continue  # 跳过内部状态/锁文件
                try:
                    tasks.append(json.loads(f.read_text(encoding="utf-8")))
                except Exception:
                    continue
            if not tasks:
                return "No cron tasks scheduled."
            lines = []
            for t in tasks:
                lines.append(f"- {t.get('name', '?')}: {t.get('schedule', '?')} → {t.get('description', t.get('command', t.get('prompt', '?')))[:80]}")
            return "\n".join(lines)

        name = (args.get("name") or "").strip()
        if not name:
            raise ToolExecutionError(f"{action} requires `name`")
        if any(c in name for c in r' /\:*?"<>|'):
            raise ToolExecutionError(f"name must be filesystem-safe (got {name!r})")

        task_path = cron_dir / f"{name}.json"

        if action == "delete":
            if not task_path.exists():
                raise ToolExecutionError(f"task {name!r} not found")
            task_path.unlink()
            return f"Deleted cron task: {name}"

        if action == "run_now":
            if not task_path.exists():
                raise ToolExecutionError(f"task {name!r} not found")
            # 真触发由外部 sentinel 消费 trigger 标记
            trigger_file = cron_dir / f"{name}.trigger"
            trigger_file.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
            return f"Triggered cron task {name!r} (sentinel will pick it up next pass)."

        # create / update 都需要 schedule + (command 或 prompt)
        schedule = (args.get("schedule") or "").strip()
        if action == "create":
            if not schedule:
                raise ToolExecutionError("create requires `schedule`")
            _validate_cron(schedule)
            if task_path.exists():
                raise ToolExecutionError(
                    f"task {name!r} already exists; use action='update' to modify"
                )
        else:  # update
            if schedule:
                _validate_cron(schedule)
            if not task_path.exists():
                raise ToolExecutionError(f"task {name!r} not found")

        command = args.get("command", "").strip()
        prompt = args.get("prompt", "").strip()
        description = args.get("description", "").strip()

        if action == "create":
            if not command and not prompt:
                raise ToolExecutionError("create requires `command` or `prompt`")
            data = {
                "name": name,
                "schedule": schedule,
                "command": command,
                "prompt": prompt,
                "description": description,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "last_run_at": None,
            }
        else:
            data = json.loads(task_path.read_text(encoding="utf-8"))
            if schedule:
                data["schedule"] = schedule
            if command:
                data["command"] = command
            if prompt:
                data["prompt"] = prompt
            if description:
                data["description"] = description
            data["updated_at"] = datetime.now(timezone.utc).isoformat()

        task_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        verb = "Created" if action == "create" else "Updated"
        return f"{verb} cron task: {name} ({data.get('schedule', '?')})"
