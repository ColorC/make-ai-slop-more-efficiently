# [OMNI] origin=claude-code domain=omnicompany/workflow_factory ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:team_builder.workers.shared_util_reexport.layer.py"
"""workflow_factory/workers/_shared.py — 共享基类 / 辅助函数 re-export.

Clean Migration 2026-04-20 · Diamond shortcut shared layer:
  业务代码继续驻留在 `_archive/routers_legacy.py`, 本模块 re-export:
    - `_CodeGenBaseRouter`   (code_generator 子节点共享基类)
    - `_GLOBAL_FIX_LIMIT` / `_check_global_fix_iter` (全局修复迭代上限)
    - `_wf_no_trunc` / `_extract_json_obj` / `_wf_extract_python_code`
    - `check_format_in_consumption` re-export (向后兼容 import 路径)

Worker 子类 (`workers/<name>.py`) 通过 Diamond 继承挂 omnicompany.Worker 基类.
完整业务逻辑仍在 _archive, Stage 3 清洁工作可把代码真正搬到 workers/*.py.
"""
from __future__ import annotations

from .._archive.routers_legacy import (
    _CodeGenBaseRouter,
    _GLOBAL_FIX_LIMIT,
    _check_global_fix_iter,
    _wf_no_trunc,
    _extract_json_obj,
    _wf_extract_python_code,
    check_format_in_consumption,
)


__all__ = [
    "_CodeGenBaseRouter",
    "_GLOBAL_FIX_LIMIT",
    "_check_global_fix_iter",
    "_wf_no_trunc",
    "_extract_json_obj",
    "_wf_extract_python_code",
    "check_format_in_consumption",
]
