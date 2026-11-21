# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-05-04T00:00:00Z type=infrastructure
"""GrepRouter · ripgrep 包装 SingleTool, 对齐 claude-code GrepTool.

参考: 参考项目/claude-code-analysis/src/tools/GrepTool/prompt.ts

核心行为:
  - 优先调系统 ripgrep (`rg`) 命令; 退化到 Python re
  - 三种输出模式: content / files_with_matches (默认) / count
  - 支持 -i / -A / -B / -C / -n / multiline 选项
  - glob 过滤 + type 过滤
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)


_HEAD_LIMIT_DEFAULT = 250


class GrepRouter(SingleToolRouter):
    """Powerful search built on ripgrep (with Python regex fallback)."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.read_file",)
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    TOOL_NAME: ClassVar[str] = "Grep"
    DESCRIPTION: ClassVar[str] = (
        "Powerful search tool built on ripgrep.\n"
        "\n"
        "Usage:\n"
        "- ALWAYS use Grep for search tasks. NEVER invoke `grep` or `rg` as a Bash command.\n"
        "- Supports full regex syntax (e.g. `log.*Error`, `function\\s+\\w+`).\n"
        "- Filter files with `glob` (e.g. `*.py`) or `type` (e.g. `py`, `rust`).\n"
        "- Output modes:\n"
        "    `content` shows matching lines\n"
        "    `files_with_matches` shows only file paths (default)\n"
        "    `count` shows match counts per file\n"
        "- Multiline matching: pass `multiline: true` for cross-line patterns.\n"
        "- Pattern syntax is ripgrep (not POSIX grep) — literal braces need `\\{\\}`."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regex pattern to search for",
            },
            "path": {
                "type": "string",
                "description": "File or directory to search in. Default: tool context cwd",
            },
            "glob": {
                "type": "string",
                "description": "Glob filter (e.g. `*.{ts,tsx}`)",
            },
            "type": {
                "type": "string",
                "description": "ripgrep file type filter (e.g. `py`, `rust`)",
            },
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_with_matches", "count"],
                "description": "Output mode (default `files_with_matches`)",
            },
            "-i": {"type": "boolean", "description": "Case-insensitive"},
            "-n": {"type": "boolean", "description": "Show line numbers (content mode)"},
            "-A": {"type": "integer", "description": "Lines after match"},
            "-B": {"type": "integer", "description": "Lines before match"},
            "-C": {"type": "integer", "description": "Lines around match"},
            "head_limit": {
                "type": "integer",
                "minimum": 0,
                "description": f"Limit output (default {_HEAD_LIMIT_DEFAULT}, 0 = unlimited)",
            },
            "multiline": {
                "type": "boolean",
                "description": "Allow patterns to span lines",
            },
        },
        "required": ["pattern"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        pattern = args.get("pattern", "")
        if not isinstance(pattern, str) or not pattern:
            raise ToolExecutionError("pattern is required (non-empty regex)")

        raw_path = (args.get("path") or "").strip()
        if raw_path:
            search_path = Path(raw_path)
            if not search_path.is_absolute() and ctx.cwd:
                search_path = (Path(ctx.cwd) / search_path).resolve()
        else:
            search_path = Path(ctx.cwd) if ctx.cwd else Path.cwd()

        if not search_path.exists():
            raise ToolExecutionError(f"search path does not exist: {search_path}")

        output_mode = args.get("output_mode", "files_with_matches")
        if output_mode not in ("content", "files_with_matches", "count"):
            raise ToolExecutionError(f"invalid output_mode: {output_mode}")

        head_limit = int(args.get("head_limit", _HEAD_LIMIT_DEFAULT))

        # 优先 ripgrep
        rg_path = shutil.which("rg")
        if rg_path:
            return self._run_ripgrep(
                rg_path, pattern, search_path, args, output_mode, head_limit
            )
        # Python fallback
        return self._run_python_fallback(
            pattern, search_path, args, output_mode, head_limit
        )

    def _run_ripgrep(
        self,
        rg_path: str,
        pattern: str,
        search_path: Path,
        args: dict,
        output_mode: str,
        head_limit: int,
    ) -> str:
        cmd = [rg_path, pattern]

        if args.get("-i"):
            cmd.append("-i")
        if args.get("multiline"):
            cmd.extend(["-U", "--multiline-dotall"])

        # output mode
        if output_mode == "files_with_matches":
            cmd.append("-l")
        elif output_mode == "count":
            cmd.append("-c")
        else:  # content
            if args.get("-n"):
                cmd.append("-n")
            if args.get("-A"):
                cmd.extend(["-A", str(int(args["-A"]))])
            if args.get("-B"):
                cmd.extend(["-B", str(int(args["-B"]))])
            if args.get("-C"):
                cmd.extend(["-C", str(int(args["-C"]))])

        if args.get("glob"):
            cmd.extend(["--glob", str(args["glob"])])
        if args.get("type"):
            cmd.extend(["--type", str(args["type"])])

        cmd.append(str(search_path))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            raise ToolExecutionError("grep timed out (60s)")
        except OSError as e:
            raise ToolExecutionError(f"ripgrep invocation failed: {e}")

        # ripgrep: rc=0 命中, rc=1 未命中, rc>=2 错误
        if result.returncode >= 2:
            raise ToolExecutionError(
                f"ripgrep error (rc={result.returncode}): {result.stderr.strip()[:500]}"
            )
        out = result.stdout.rstrip("\n")
        if not out:
            return f"No matches for pattern {pattern!r} in {search_path}"
        if head_limit > 0:
            lines = out.split("\n")
            if len(lines) > head_limit:
                truncated = lines[:head_limit]
                truncated.append(f"... (truncated to {head_limit} of {len(lines)} lines)")
                out = "\n".join(truncated)
        return out

    def _run_python_fallback(
        self,
        pattern: str,
        search_path: Path,
        args: dict,
        output_mode: str,
        head_limit: int,
    ) -> str:
        """无 rg 时的 Python 退化实现. 仅基础功能, 不支持 type 过滤."""
        flags = re.IGNORECASE if args.get("-i") else 0
        if args.get("multiline"):
            flags |= re.MULTILINE | re.DOTALL
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            raise ToolExecutionError(f"invalid regex {pattern!r}: {e}")

        glob_filter = args.get("glob")
        files: list[Path] = []
        if search_path.is_file():
            files = [search_path]
        else:
            it = search_path.rglob(glob_filter or "*")
            files = [p for p in it if p.is_file()]

        results: list[str] = []
        for f in files:
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            matches = list(regex.finditer(content)) if args.get("multiline") else None
            line_matches = []
            for lineno, line in enumerate(content.splitlines(), 1):
                if regex.search(line):
                    line_matches.append((lineno, line))

            if output_mode == "files_with_matches":
                if line_matches or matches:
                    results.append(str(f))
            elif output_mode == "count":
                cnt = len(line_matches) if not args.get("multiline") else len(matches or [])
                if cnt > 0:
                    results.append(f"{f}:{cnt}")
            else:  # content
                for lineno, line in line_matches:
                    prefix = f"{f}:{lineno}:" if args.get("-n") else f"{f}:"
                    results.append(prefix + line)

        if not results:
            return f"No matches for pattern {pattern!r} in {search_path}"
        if head_limit > 0 and len(results) > head_limit:
            results = results[:head_limit] + [
                f"... (truncated to {head_limit} of {len(results)} results)"
            ]
        return "\n".join(results)
