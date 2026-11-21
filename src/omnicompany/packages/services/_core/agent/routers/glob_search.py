# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-05-04T00:00:00Z type=infrastructure
"""GlobRouter · 文件名 glob 匹配 SingleTool, 对齐 claude-code GlobTool.

参考: 参考项目/claude-code-analysis/src/tools/GlobTool/prompt.ts

核心行为:
  - 支持 **/*.ext 类 glob 模式
  - 默认在 ctx.cwd 或显式 path 下搜
  - 结果按 mtime 倒序 (最新先), 对齐 claude-code "sorted by modification time"
  - 默认上限 100 条 (head_limit), 0 = 无上限
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


_HEAD_LIMIT_DEFAULT = 100


class GlobRouter(SingleToolRouter):
    """Fast file pattern matching tool, returns paths sorted by mtime (newest first)."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.list_dir",)
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    TOOL_NAME: ClassVar[str] = "Glob"
    DESCRIPTION: ClassVar[str] = (
        "Fast file pattern matching that works with any codebase size.\n"
        "\n"
        "- Supports glob patterns like `**/*.js` or `src/**/*.ts`\n"
        "- Returns matching file paths sorted by modification time (newest first)\n"
        "- Use this tool when you need to find files by name patterns\n"
        "- For open-ended search needing multiple rounds of glob+grep, prefer the Agent tool"
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern (e.g. `**/*.py`, `src/**/*.ts`)",
            },
            "path": {
                "type": "string",
                "description": "Directory to search in. Default: tool context cwd.",
            },
            "head_limit": {
                "type": "integer",
                "minimum": 0,
                "description": f"Limit results (default {_HEAD_LIMIT_DEFAULT}, 0 = unlimited)",
            },
        },
        "required": ["pattern"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        pattern = (args.get("pattern") or "").strip()
        if not pattern:
            raise ToolExecutionError("pattern is required")

        raw_path = (args.get("path") or "").strip()
        head_limit = int(args.get("head_limit", _HEAD_LIMIT_DEFAULT))
        if head_limit < 0:
            raise ToolExecutionError("head_limit must be >= 0 (0 = unlimited)")

        if raw_path:
            base = Path(raw_path)
            if not base.is_absolute():
                base = (Path(ctx.cwd) / base).resolve() if ctx.cwd else base.resolve()
        else:
            base = Path(ctx.cwd) if ctx.cwd else Path.cwd()

        if not base.exists():
            raise ToolExecutionError(f"search path does not exist: {base}")
        if not base.is_dir():
            raise ToolExecutionError(f"search path is not a directory: {base}")

        # pathlib glob: 区分 */** 行为
        # `**/*.py` → 用 rglob("*.py")
        # `*.py` → 用 glob("*.py")
        # 通用做法: 直接 glob(pattern), 但 `**/...` 需要 pathlib >= 3.13 才走 ** 递归
        # 兼容做法: 检测 `**` 用 rglob 后过滤
        try:
            if "**" in pattern:
                # 移除 ** 前缀, 用 rglob
                tail = pattern.replace("**/", "").replace("**", "*")
                matches = list(base.rglob(tail))
            else:
                matches = list(base.glob(pattern))
        except Exception as e:
            raise ToolExecutionError(f"glob pattern error: {e}")

        # 只要文件 (跳目录), 按 mtime 倒序
        files = [p for p in matches if p.is_file()]
        try:
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            # 个别文件 stat 失败时退化为字典序
            files.sort(key=lambda p: str(p))

        # head_limit
        total = len(files)
        if head_limit > 0:
            files = files[:head_limit]

        if not files:
            return f"No matches for pattern '{pattern}' in {base}"

        out_lines = [str(f) for f in files]
        if total > len(files):
            out_lines.append(f"... (truncated to {len(files)} of {total} matches; raise head_limit or refine pattern)")
        return "\n".join(out_lines)
