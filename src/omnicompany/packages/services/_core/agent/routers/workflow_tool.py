# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-05-04T00:00:00Z type=infrastructure
"""WorkflowRouter · 调用预定义 workflow SingleTool, 对齐 claude-code WorkflowTool.

参考: 参考项目/claude-code-analysis/src/tools/WorkflowTool/

omnicompany 实现:
  - workflow 是 .claude/workflows/<name>.{md,yaml} 描述的多步流程
  - 不强制运行 workflow (omnicompany 自有 Team / Worker 体系作为更强工作流)
  - 此工具主要"加载 workflow 描述供 LLM 跟随" — 类似 SkillRouter 但语义是流程
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)


def _workflow_search_paths(ctx: ToolContext) -> list[Path]:
    paths: list[Path] = []
    base = Path(ctx.project_root or ctx.cwd or Path.cwd())
    paths.append(base / ".claude" / "workflows")
    home = Path.home() / ".claude" / "workflows"
    paths.append(home)
    return [p for p in paths if p.exists() and p.is_dir()]


class WorkflowRouter(SingleToolRouter):
    """List/load a predefined workflow description (.claude/workflows/<name>)."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.read_file",)
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    TOOL_NAME: ClassVar[str] = "Workflow"
    DESCRIPTION: ClassVar[str] = (
        "Load a predefined workflow description.\n"
        "\n"
        "- action='list': enumerate available workflows\n"
        "- action='load': read a workflow's full content (markdown / yaml)\n"
        "\n"
        "Workflows live in .claude/workflows/<name>.{md,yaml}.\n"
        "For complex multi-step omnicompany tasks, prefer the Team + Worker system."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "load"]},
            "name": {"type": "string", "description": "Workflow name (filename without ext)"},
        },
        "required": ["action"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        action = args.get("action", "")
        if action not in ("list", "load"):
            raise ToolExecutionError("action must be 'list' or 'load'")

        if action == "list":
            workflows: list[str] = []
            for sp in _workflow_search_paths(ctx):
                for f in sorted(sp.iterdir()):
                    if f.is_file() and f.suffix in (".md", ".yaml", ".yml"):
                        workflows.append(f"{f.stem} ({f})")
            if not workflows:
                return "No workflows found in .claude/workflows/"
            return "\n".join(f"- {w}" for w in workflows)

        # load
        name = (args.get("name") or "").strip()
        if not name:
            raise ToolExecutionError("load requires `name`")
        if any(c in name for c in r' /\:*?"<>|'):
            raise ToolExecutionError(f"name must be filesystem-safe: {name!r}")

        for sp in _workflow_search_paths(ctx):
            for ext in (".md", ".yaml", ".yml"):
                f = sp / f"{name}{ext}"
                if f.exists():
                    try:
                        content = f.read_text(encoding="utf-8")
                    except Exception as e:
                        raise ToolExecutionError(f"failed to read {f}: {e}")
                    return f"=== Workflow: {name} (from {f}) ===\n\n{content}"

        searched = [str(p) for p in _workflow_search_paths(ctx)]
        raise ToolExecutionError(f"workflow {name!r} not found in {searched}")
