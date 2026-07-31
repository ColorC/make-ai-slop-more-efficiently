# [OMNI] origin=claude-code domain=omnicompany/workflow_factory ts=2026-04-20T00:00:00Z type=shim
# [OMNI] material_id="material:core.team_builder.codegen_loop_compatibility.shim.py"
"""workflow_factory/routers_codegen.py — 向后兼容 shim (Clean Migration 2026-04-20).

CodeGenLoop 是 AgentNodeLoop (非 Worker, 本次迁移不动 Agent Loop 继承, 仅做 re-export).
真实实现在 `_archive/routers_codegen_legacy.py`.

保留文件存在以兼容现有 import 路径:
  from omnicompany.packages.services._core.team_builder.routers_codegen import CodeGenLoop
"""
from __future__ import annotations

from ._archive.routers_codegen_legacy import (
    CodeGenLoop,
    WriteFileRouter,
    PyCompileRouter,
    ListFilesRouter,
    ReadWrittenFileRouter,
)


__all__ = [
    "CodeGenLoop",
    "WriteFileRouter",
    "PyCompileRouter",
    "ListFilesRouter",
    "ReadWrittenFileRouter",
]
