# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=helper
# [OMNI] material_id="material:diagnosis.doctor.worker.material.shared_ast_tools.py"
"""Material 诊断子域共享常量 + AST 工具 (Stage 3 Clean Migration 2026-04-22).

供 material_extractor / signature_diff / five_element_check / tag_coverage /
parent_chain / composite_check / example_presence_check /
material_contextual_audit / health_writer 复用.
"""
from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

logger = logging.getLogger("omnicompany.doctor.format")


# 默认 source root (omnicompany 项目的 src/omnicompany/)
DEFAULT_SOURCE_ROOT = Path("e:/WindowsWorkspace/omnicompany/src/omnicompany")

# ── HealthArchive 可选集成 ─────────────────────────────────────────
try:
    from omnicompany.packages.services._core.registry.archive import (
        HealthArchive,
        make_router_snapshot,
        make_format_snapshot,
        write_proximity_snapshot,
    )
    from omnicompany.packages.services._core.registry.scanner import _infer_package
    ARCHIVE_AVAILABLE = True
    REGISTRY_ARCHIVE_DIR = Path(__file__).parents[6] / "data" / "registry" / "health"
except ImportError:
    ARCHIVE_AVAILABLE = False
    HealthArchive = None  # type: ignore
    make_router_snapshot = None  # type: ignore
    make_format_snapshot = None  # type: ignore
    write_proximity_snapshot = None  # type: ignore
    _infer_package = None  # type: ignore
    REGISTRY_ARCHIVE_DIR = Path(".")

# Material ID 应含域前缀: domain.something
DOMAIN_PATTERN = re.compile(r"^[a-z][a-z0-9_-]+\.[a-z]")

# 语义类型后缀白名单 (用于 Material TagCoverage ID 命名检查)
SEMANTIC_SUFFIXES = (
    "-request", "-report", "-record", "-result", "-response",
    "-state", "-action", "-observation", "-context",
    ".fmt.", "fmt.",
)

# 用途关键词: FORMAT_IN / FORMAT_OUT 角色标识
INPUT_ROLES = ("FORMAT_IN", "format_in", "from_format")
OUTPUT_ROLES = ("FORMAT_OUT", "format_out", "to_format")


# ════════════════════════════════════════════════════════════════
# AST 工具函数 (供 FormatExtractorWorker 使用)
# ════════════════════════════════════════════════════════════════

def is_format_call(node: ast.Call) -> bool:
    func = node.func
    return (isinstance(func, ast.Name) and func.id == "Format") or (
        isinstance(func, ast.Attribute) and func.attr == "Format"
    )


def extract_kwargs(call_node: ast.Call) -> dict:
    """从 Format() 调用提取关键字参数 (literal_eval; 失败记 None)."""
    kw_dict: dict = {}
    for kw in call_node.keywords:
        if kw.arg is None:
            continue
        try:
            kw_dict[kw.arg] = ast.literal_eval(kw.value)
        except (ValueError, TypeError):
            kw_dict[kw.arg] = None
    return kw_dict


def iter_format_calls(tree: ast.Module, material_id: str):
    """遍历 AST, yield 所有 id==material_id 的 Format() 调用节点.

    顶层赋值优先 (先 tree.body, 再其余节点).
    """
    seen: set[int] = set()
    # Pass 1: 顶层 Assign — 可以提取常量名
    for stmt in tree.body:
        if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            continue
        value = getattr(stmt, "value", None)
        if not isinstance(value, ast.Call) or not is_format_call(value):
            continue
        kw = extract_kwargs(value)
        if kw.get("id") == material_id:
            seen.add(id(value))
            yield value
    # Pass 2: 全文 walk — 处理 list/函数内部定义
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not is_format_call(node):
            continue
        if id(node) in seen:
            continue
        kw = extract_kwargs(node)
        if kw.get("id") == material_id:
            yield node


def find_constant_name(tree: ast.Module, target_call: ast.Call) -> str | None:
    """如果 target_call 是顶层 `CONST = Format(...)` 的值, 返回常量名, 否则 None."""
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            if stmt.value is target_call and stmt.targets:
                t = stmt.targets[0]
                if isinstance(t, ast.Name):
                    return t.id
        elif isinstance(stmt, ast.AnnAssign):
            if stmt.value is target_call and isinstance(stmt.target, ast.Name):
                return stmt.target.id
    return None
