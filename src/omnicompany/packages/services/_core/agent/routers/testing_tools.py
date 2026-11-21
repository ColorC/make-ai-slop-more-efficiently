# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-05-04T00:00:00Z type=infrastructure
"""OverflowTestRouter / SyntheticOutputRouter · 第九波 (2026-05-04).

合规测试工具:
  - OverflowTest: 故意产出大量输出 (测下游截断/分页/buffer 处理)
  - SyntheticOutput: 产出指定模板的合成输出 (测错误处理 / format 解析)
"""
from __future__ import annotations

import json
import logging
from typing import ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)


# ─── OverflowTestRouter ──────────────────────────────────────────


class OverflowTestRouter(SingleToolRouter):
    """Generate intentionally-large output to test downstream truncation/pagination."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    TOOL_NAME: ClassVar[str] = "OverflowTest"
    DESCRIPTION: ClassVar[str] = (
        "Generate large/long output for stress-testing downstream consumers.\n"
        "\n"
        "- mode='lines': return N lines of dummy text\n"
        "- mode='chars': return N characters of single string\n"
        "- mode='json': return a JSON object with N entries"
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["lines", "chars", "json"]},
            "size": {"type": "integer", "minimum": 1, "maximum": 1_000_000},
        },
        "required": ["mode", "size"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        mode = args.get("mode", "")
        if mode not in ("lines", "chars", "json"):
            raise ToolExecutionError(f"mode must be lines/chars/json, got {mode!r}")
        size = int(args.get("size", 100))
        if not (1 <= size <= 1_000_000):
            raise ToolExecutionError("size must be 1..1000000")

        if mode == "lines":
            return "\n".join(f"line {i+1}" for i in range(size))
        if mode == "chars":
            return "x" * size
        # json
        obj = {f"key_{i}": f"value_{i}" for i in range(size)}
        return json.dumps(obj, ensure_ascii=False)


# ─── SyntheticOutputRouter ──────────────────────────────────────


class SyntheticOutputRouter(SingleToolRouter):
    """Emit a synthetic output following a template (for testing parsers / error handlers)."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    TOOL_NAME: ClassVar[str] = "SyntheticOutput"
    DESCRIPTION: ClassVar[str] = (
        "Emit a synthetic output following a chosen pattern.\n"
        "\n"
        "Patterns:\n"
        "- 'json_valid' / 'json_malformed' / 'markdown_table' / 'error_payload' / 'long_unicode'\n"
        "- 'echo': just return the `body` argument unchanged\n"
        "\n"
        "Useful for testing how downstream tools handle different output shapes."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "enum": [
                    "json_valid", "json_malformed", "markdown_table",
                    "error_payload", "long_unicode", "echo",
                ],
            },
            "body": {"type": "string", "description": "Used by 'echo'"},
            "n_rows": {"type": "integer", "description": "Used by 'markdown_table'"},
        },
        "required": ["pattern"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        pattern = args.get("pattern", "")
        if pattern == "json_valid":
            return json.dumps({"ok": True, "value": 42, "list": [1, 2, 3]}, ensure_ascii=False)
        if pattern == "json_malformed":
            return '{"ok": true, "value": 42, "list": [1, 2, 3'  # 故意缺右括号
        if pattern == "markdown_table":
            n_rows = int(args.get("n_rows", 3))
            lines = ["| col1 | col2 |", "|---|---|"]
            for i in range(n_rows):
                lines.append(f"| a{i} | b{i} |")
            return "\n".join(lines)
        if pattern == "error_payload":
            return json.dumps({
                "is_error": True,
                "error": "Simulated failure",
                "code": "SYNTHETIC_E001",
            }, ensure_ascii=False)
        if pattern == "long_unicode":
            return "你好世界 " * 100 + "🎉" * 50
        if pattern == "echo":
            return args.get("body", "")
        raise ToolExecutionError(f"unknown pattern: {pattern!r}")
