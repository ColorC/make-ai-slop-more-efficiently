# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-05-04T00:00:00Z type=infrastructure
"""NotebookEditRouter · Jupyter notebook 单元格编辑 SingleTool, 对齐 claude-code NotebookEditTool.

参考: 参考项目/claude-code-analysis/src/tools/NotebookEditTool/prompt.ts

核心行为:
  - replace: 替换 cell_number 单元格的 source
  - insert: 在 cell_number 索引插入新单元格
  - delete: 删 cell_number 单元格
  - cell_type 可选 'code' / 'markdown' (新增/替换时用)
  - cell_number 是 0-indexed
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)


_VALID_CELL_TYPES = ("code", "markdown")
_VALID_EDIT_MODES = ("replace", "insert", "delete")


class NotebookEditRouter(SingleToolRouter):
    """Edit cells in a Jupyter notebook (.ipynb)."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.read_file",)
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.modify_file",)

    TOOL_NAME: ClassVar[str] = "NotebookEdit"
    DESCRIPTION: ClassVar[str] = (
        "Edit a single cell in a Jupyter notebook (.ipynb). Three modes:\n"
        "- `replace` (default): replace cell at index `cell_number` with new_source\n"
        "- `insert`: insert new cell at index `cell_number` (others shift down)\n"
        "- `delete`: delete cell at index `cell_number` (others shift up)\n"
        "\n"
        "cell_number is 0-indexed. notebook_path must be absolute. "
        "Optional cell_type ('code' / 'markdown') for replace/insert."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "notebook_path": {
                "type": "string",
                "description": "Absolute path to the .ipynb file",
            },
            "cell_number": {
                "type": "integer",
                "minimum": 0,
                "description": "0-indexed cell index",
            },
            "new_source": {
                "type": "string",
                "description": "New cell content (replace/insert)",
            },
            "edit_mode": {
                "type": "string",
                "enum": list(_VALID_EDIT_MODES),
                "description": "replace (default) / insert / delete",
            },
            "cell_type": {
                "type": "string",
                "enum": list(_VALID_CELL_TYPES),
                "description": "Cell type for replace/insert (default 'code')",
            },
        },
        "required": ["notebook_path", "cell_number"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        raw_path = (args.get("notebook_path") or "").strip()
        if not raw_path:
            raise ToolExecutionError("notebook_path is required")
        if not raw_path.endswith(".ipynb"):
            raise ToolExecutionError(f"notebook_path must end with .ipynb, got: {raw_path}")

        path = Path(raw_path)
        if not path.is_absolute():
            raise ToolExecutionError(f"notebook_path must be absolute: {raw_path}")
        if not path.exists():
            raise ToolExecutionError(f"notebook does not exist: {raw_path}")

        try:
            cell_number = int(args.get("cell_number", -1))
        except (TypeError, ValueError):
            raise ToolExecutionError("cell_number must be a non-negative integer")
        if cell_number < 0:
            raise ToolExecutionError("cell_number must be >= 0")

        edit_mode = args.get("edit_mode", "replace")
        if edit_mode not in _VALID_EDIT_MODES:
            raise ToolExecutionError(
                f"edit_mode must be one of {_VALID_EDIT_MODES}, got {edit_mode!r}"
            )

        new_source = args.get("new_source", "")
        if edit_mode != "delete" and not isinstance(new_source, str):
            raise ToolExecutionError(f"new_source must be string for {edit_mode}")

        cell_type = args.get("cell_type", "code")
        if cell_type not in _VALID_CELL_TYPES:
            raise ToolExecutionError(
                f"cell_type must be one of {_VALID_CELL_TYPES}, got {cell_type!r}"
            )

        try:
            with path.open("r", encoding="utf-8") as f:
                nb = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise ToolExecutionError(f"failed to parse notebook {raw_path}: {e}")

        cells = nb.get("cells")
        if not isinstance(cells, list):
            raise ToolExecutionError(f"notebook has no 'cells' list (corrupt? {raw_path})")

        n_cells = len(cells)

        # 边界
        if edit_mode == "replace" and cell_number >= n_cells:
            raise ToolExecutionError(
                f"cell_number {cell_number} out of range (notebook has {n_cells} cells)"
            )
        if edit_mode == "delete" and cell_number >= n_cells:
            raise ToolExecutionError(
                f"cell_number {cell_number} out of range (notebook has {n_cells} cells)"
            )
        if edit_mode == "insert" and cell_number > n_cells:
            raise ToolExecutionError(
                f"cell_number {cell_number} out of range for insert "
                f"(must be 0..{n_cells} inclusive)"
            )

        # 操作
        if edit_mode == "delete":
            removed = cells.pop(cell_number)
            action = f"deleted cell {cell_number} (type={removed.get('cell_type', '?')})"
        elif edit_mode == "insert":
            cells.insert(cell_number, _make_cell(cell_type, new_source))
            action = f"inserted {cell_type} cell at index {cell_number}"
        else:  # replace
            existing = cells[cell_number]
            existing["cell_type"] = cell_type
            existing["source"] = _split_source_lines(new_source)
            # outputs/execution_count 复位 (replace 后通常需要重跑)
            if cell_type == "code":
                existing["outputs"] = []
                existing["execution_count"] = None
            else:
                # markdown 不该有 outputs/execution_count
                existing.pop("outputs", None)
                existing.pop("execution_count", None)
            action = f"replaced cell {cell_number} (set type={cell_type})"

        try:
            with path.open("w", encoding="utf-8") as f:
                json.dump(nb, f, indent=1, ensure_ascii=False)
                f.write("\n")
        except OSError as e:
            raise ToolExecutionError(f"failed to write {raw_path}: {e}")

        return f"Notebook {raw_path}: {action}"


def _make_cell(cell_type: str, source: str) -> dict:
    """构造一个新 cell 字典 (符合 nbformat v4)."""
    cell = {
        "cell_type": cell_type,
        "metadata": {},
        "source": _split_source_lines(source),
    }
    if cell_type == "code":
        cell["outputs"] = []
        cell["execution_count"] = None
    return cell


def _split_source_lines(source: str) -> list[str]:
    """nbformat 把 source 存成 list[str], 每行带 \\n (除最后一行)."""
    if not source:
        return []
    lines = source.splitlines(keepends=True)
    return lines
