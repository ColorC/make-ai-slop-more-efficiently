# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-05-04T18:00:00Z type=infrastructure
"""FileReadRouter · 读文件 SingleTool, 对齐 claude-code FileReadTool.

参考: 参考项目/claude-code-analysis/src/tools/FileReadTool/prompt.ts (renderPromptTemplate)

核心行为:
  - file_path 必须绝对路径
  - 默认读最多 2000 行 (claude-code MAX_LINES_TO_READ)
  - 支持 offset/limit 参数读特定段
  - cat -n 格式 (行号 + tab + 内容)
  - 不存在文件抛清晰错误

L1+L2 对齐 (Wave 5 部分, 2026-05-04):
  - DESCRIPTION 1:1 复刻 cc renderPromptTemplate 默认输出 (含 image/PDF/Jupyter/screenshot 行)
  - INPUT_SCHEMA 加 pages 字段 (PDF 用)
  - L2 行为: image / PDF / Jupyter 当前不真支持, 报清晰错误指引 (诚实)
  - L5 协议: 成功读后注入 ctx.read_files set, FileEdit 检查依赖 (Read→Edit 状态机)
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


_MAX_LINES_DEFAULT = 2000
_MAX_LINE_LENGTH = 2000  # 单行截断阈值, 防止读到二进制时爆栈

# 二进制 / 多模态文件后缀 — omnicompany 当前不支持真解析
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".ico")
_PDF_EXTS = (".pdf",)
_NOTEBOOK_EXTS = (".ipynb",)


class FileReadRouter(SingleToolRouter):
    """Read text file from local filesystem (cat -n formatted)."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.read_file",)
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    TOOL_NAME: ClassVar[str] = "Read"
    # DESCRIPTION 1:1 复刻 cc renderPromptTemplate 默认输出
    # 来源: 参考项目/claude-code-analysis/src/tools/FileReadTool/prompt.ts
    # 注: cc 真启时 isPDFSupported() 跟其他 runtime 检测会插条件分支 — omnicompany
    # 静态版用最常见配置 (offset_default + line_format + 含 PDF 行)
    DESCRIPTION: ClassVar[str] = (
        "Reads a file from the local filesystem. You can access any file directly by using this tool.\n"
        "Assume this tool is able to read all files on the machine. If the User provides a path to a file assume that path is valid. It is okay to read a file that does not exist; an error will be returned.\n"
        "\n"
        "Usage:\n"
        "- The file_path parameter must be an absolute path, not a relative path\n"
        f"- By default, it reads up to {_MAX_LINES_DEFAULT} lines starting from the beginning of the file\n"
        "- You can optionally specify a line offset and limit (especially handy for long files), but it's recommended to read the whole file by not providing these parameters\n"
        "- Results are returned using cat -n format, with line numbers starting at 1\n"
        "- This tool allows Claude Code to read images (eg PNG, JPG, etc). When reading an image file the contents are presented visually as Claude Code is a multimodal LLM.\n"
        "- This tool can read PDF files (.pdf). For large PDFs (more than 10 pages), you MUST provide the pages parameter to read specific page ranges (e.g., pages: \"1-5\"). Reading a large PDF without the pages parameter will fail. Maximum 20 pages per request.\n"
        "- This tool can read Jupyter notebooks (.ipynb files) and returns all cells with their outputs, combining code, text, and visualizations.\n"
        "- This tool can only read files, not directories. To read a directory, use an ls command via the Bash tool.\n"
        "- You will regularly be asked to read screenshots. If the user provides a path to a screenshot, ALWAYS use this tool to view the file at the path. This tool will work with all temporary file paths.\n"
        "- If you read a file that exists but has empty contents you will receive a system reminder warning in place of file contents."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to read",
            },
            "offset": {
                "type": "integer",
                "minimum": 1,
                "description": "The line number to start reading from. Only provide if the file is too large to read at once.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "description": "The number of lines to read. Only provide if the file is too large to read at once.",
            },
            "pages": {
                "type": "string",
                "description": "Page range for PDF files (e.g., \"1-5\", \"3\", \"10-20\"). Only applicable to PDF files. Maximum 20 pages per request.",
            },
        },
        "required": ["file_path"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        raw_path = (args.get("file_path") or "").strip()
        if not raw_path:
            raise ToolExecutionError("file_path is required")

        offset = int(args.get("offset") or 1)
        if offset < 1:
            raise ToolExecutionError("offset must be >= 1 (1-indexed)")
        limit = int(args.get("limit") or _MAX_LINES_DEFAULT)
        if limit < 1:
            raise ToolExecutionError("limit must be >= 1")

        path = Path(raw_path)
        if not path.is_absolute():
            raise ToolExecutionError(
                f"file_path must be absolute, got relative: {raw_path!r}. "
                "Use the full path starting from drive letter (Windows) or root (Unix)."
            )
        if not path.exists():
            raise ToolExecutionError(f"file does not exist: {raw_path}")
        if path.is_dir():
            raise ToolExecutionError(
                f"path is a directory, not a file: {raw_path}. "
                "Use Bash `ls` to list directories."
            )

        # L2 边界: image / PDF / Jupyter 当前不真支持 (Wave 5 留 Wave 5b 实现)
        # 报清晰错误指引, 比直接当文本读出乱码好 (诚实)
        suffix = path.suffix.lower()
        if suffix in _IMAGE_EXTS:
            raise ToolExecutionError(
                f"image file detected ({suffix}); reading binary as text would yield garbage. "
                "omnicompany IDEAgentLoop has multimodal image attach via "
                "tool_ctx.pending_image_attachments — but FileReadRouter doesn't auto-attach yet. "
                "Use ReadImage / multimodal-aware tool, or open the file outside this conversation."
            )
        if suffix in _PDF_EXTS:
            pages = (args.get("pages") or "").strip()
            raise ToolExecutionError(
                f"PDF file detected ({suffix}); pages parameter {pages or '(empty)'!r} ignored — "
                "PDF parsing not implemented in this version. Convert to text first via "
                "`pdftotext` or similar, then read the txt result."
            )
        if suffix in _NOTEBOOK_EXTS:
            raise ToolExecutionError(
                f"Jupyter notebook detected ({suffix}); cell-aware parsing not implemented. "
                "Use NotebookEdit tool (deferred) for cell-level operations, or read raw JSON."
            )

        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                # 读到 offset 起始行, 然后取 limit 行
                lines: list[str] = []
                current_line = 0
                for line in f:
                    current_line += 1
                    if current_line < offset:
                        continue
                    if len(lines) >= limit:
                        break
                    # 单行截断防爆
                    if len(line) > _MAX_LINE_LENGTH:
                        line = line[:_MAX_LINE_LENGTH] + "...[truncated]\n"
                    lines.append(line)
        except OSError as e:
            raise ToolExecutionError(f"failed to read {raw_path}: {e}")

        # L5 协议: 成功读后 (即使空文件) 把 abs_path 注入 ctx.read_files set,
        # FileEditRouter 会检查 (Read→Edit 状态机). ctx.read_files 由
        # AgentNodeLoop.build_tool_context 默认注入跨工具共享的 set 实例.
        read_files = getattr(ctx, "read_files", None)
        if read_files is not None:
            try:
                read_files.add(str(path.resolve()))
            except Exception:
                pass  # set 注入有问题不影响读结果

        if not lines:
            return "(file is empty or offset past end)"

        # cat -n 格式: 行号右对齐 6 位 + tab + 内容
        # claude-code 用 "line number + tab" (compact) 或 "spaces + line number + arrow"
        formatted = "".join(
            f"{offset + i:6d}\t{line}" for i, line in enumerate(lines)
        )
        # 不强制换行: 文件内容自带 \n
        return formatted.rstrip("\n") if formatted else formatted
