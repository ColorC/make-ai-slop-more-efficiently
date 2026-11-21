# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-05-04T18:00:00Z type=infrastructure
"""FileEditRouter · exact 字符串替换 SingleTool, 对齐 claude-code FileEditTool.

参考: 参考项目/claude-code-analysis/src/tools/FileEditTool/prompt.ts

核心行为:
  - exact string replacement: old_string → new_string
  - old_string 必须在文件中**唯一**出现 (除非 replace_all=True)
  - 同 = 拒绝 (no-op edit 无意义)
  - 文件不存在 → 错误

L5 协议 (Wave 5+7 部分, 2026-05-04):
  - **强制要求** abs_path 在 ctx.read_files (FileReadRouter / WriteFileRouter 成功后注入)
  - 没 read 过 → 报清晰错误指引 "先 Read"
  - 这跟 cc 行为一致: cc 在 LLM 协议层 + 工具层都强制 (我们工具层强制)

边界:
  - 仅文本文件 (utf-8). 二进制 → 用专门工具
  - 不做语法校验 (替换后是否仍然 valid Python/YAML 等不在本工具范围)
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


class FileEditRouter(SingleToolRouter):
    """Performs exact string replacements in files."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.read_file",)
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.modify_file",)

    TOOL_NAME: ClassVar[str] = "Edit"
    # DESCRIPTION 1:1 复刻 cc FileEditTool/prompt.ts (含 Read 前置强调)
    DESCRIPTION: ClassVar[str] = (
        "Performs exact string replacements in files.\n"
        "\n"
        "Usage:\n"
        "- You must use your `Read` tool at least once in the conversation before editing. This tool will error if you attempt an edit without reading the file.\n"
        "- When editing text from Read tool output, ensure you preserve the exact indentation (tabs/spaces) as it appears AFTER the line number prefix. The line number prefix format is: line number + tab. Everything after that is the actual file content to match. Never include any part of the line number prefix in the old_string or new_string.\n"
        "- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.\n"
        "- Only use emojis if the user explicitly requests it. Avoid adding emojis to files unless asked.\n"
        "- The edit will FAIL if `old_string` is not unique in the file. Either provide a larger string with more surrounding context to make it unique or use `replace_all` to change every instance of `old_string`.\n"
        "- Use `replace_all` for replacing and renaming strings across the file. This parameter is useful if you want to rename a variable for instance."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to edit",
            },
            "old_string": {
                "type": "string",
                "description": "The text to replace (must match exactly, including whitespace)",
            },
            "new_string": {
                "type": "string",
                "description": "The text to replace it with (must be different from old_string)",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences (default false). When false, old_string must be unique.",
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        raw_path = (args.get("file_path") or "").strip()
        old_string = args.get("old_string", "")
        new_string = args.get("new_string", "")
        replace_all = bool(args.get("replace_all", False))

        if not raw_path:
            raise ToolExecutionError("file_path is required")
        if not isinstance(old_string, str) or not isinstance(new_string, str):
            raise ToolExecutionError("old_string and new_string must be strings")
        if old_string == new_string:
            raise ToolExecutionError(
                "old_string and new_string are identical — no-op edit not allowed"
            )
        if not old_string:
            raise ToolExecutionError("old_string cannot be empty")

        path = Path(raw_path)
        if not path.is_absolute():
            raise ToolExecutionError(
                f"file_path must be absolute, got relative: {raw_path!r}"
            )
        if not path.exists():
            raise ToolExecutionError(f"file does not exist: {raw_path}")
        if path.is_dir():
            raise ToolExecutionError(f"path is a directory, not a file: {raw_path}")

        # L5 协议: Read→Edit 状态机. abs_path 必须在 ctx.read_files (Read / Write 成功后注入).
        # 跟 cc 工具层强制一致 — 防 LLM 凭幻觉编辑没读过的文件.
        read_files = getattr(ctx, "read_files", None)
        if read_files is not None:  # set 注入有的话才检查 (向下兼容老 ctx)
            abs_str = str(path.resolve())
            if abs_str not in read_files:
                raise ToolExecutionError(
                    f"file {raw_path} has not been read in this conversation. "
                    "You must use the Read tool first to see its contents before editing. "
                    "This prevents accidental edits based on stale or assumed file state."
                )

        try:
            content = path.read_text(encoding="utf-8")
        except OSError as e:
            raise ToolExecutionError(f"failed to read {raw_path}: {e}")
        except UnicodeDecodeError as e:
            raise ToolExecutionError(
                f"file is not valid UTF-8 ({e.reason}); cannot edit binary or non-utf8 files"
            )

        # 计数 old_string 出现次数
        count = content.count(old_string)
        if count == 0:
            raise ToolExecutionError(
                f"old_string not found in {raw_path}. "
                "Make sure indentation/whitespace matches the file exactly. "
                "If reading from `Read` output, exclude the line number prefix."
            )
        if count > 1 and not replace_all:
            raise ToolExecutionError(
                f"old_string occurs {count} times in {raw_path}. "
                "Either provide more surrounding context to make it unique, "
                "or set replace_all=true to change every instance."
            )

        if replace_all:
            new_content = content.replace(old_string, new_string)
            replaced = count
        else:
            new_content = content.replace(old_string, new_string, 1)
            replaced = 1

        try:
            path.write_text(new_content, encoding="utf-8")
        except OSError as e:
            raise ToolExecutionError(f"failed to write {raw_path}: {e}")

        return f"Edited {raw_path}: replaced {replaced} occurrence(s)"
