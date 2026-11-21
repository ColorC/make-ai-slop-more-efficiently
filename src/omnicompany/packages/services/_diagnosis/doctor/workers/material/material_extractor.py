# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.material.ast_definition_scanner.py"
"""MaterialExtractorWorker — AST 扫描 Material 对象 (HARD, Stage 3 Clean Migration 2026-04-22).

Worker 协议:
  FORMAT_IN  = doctor.material.request
  FORMAT_OUT = doctor.material.extracted

诊断目标: 定位指定 material_id 的 Material 对象定义 (protocol 层仍叫 Format 类),
并收集其 FORMAT_IN/OUT 引用清单 (usages), 供下游检查链判断连通性与命名规范.

PASS: 正常扫描完成 (found 字段指示是否真找到 Material; SignatureDiff 负责短路)
FAIL: material_id 为空 / source_root 不存在 (入口参数违约)

术语对应:
  - material_id (变量/字段名, 新命名, 替代原 format_id)
  - Material (业务对象) / Format (protocol 类 + FormatRegistry, 保留)
  - FORMAT_IN / FORMAT_OUT (类属性) / format_in / format_out (字段) 均为 protocol 硬契约, 保留
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import (
    DEFAULT_SOURCE_ROOT,
    INPUT_ROLES,
    OUTPUT_ROLES,
    extract_kwargs,
    find_constant_name,
    iter_format_calls,
)


def _should_skip(py_file: Path) -> bool:
    s = str(py_file)
    return "__pycache__" in s or "_graveyard" in s


def _scan_definition(source_root: Path, material_id: str) -> tuple[dict, str | None, str | None]:
    """Step 1: 扫 formats.py 查找 Material(id=material_id) 的 Format() 定义.

    Returns: (format_obj, constant_name, defined_in_relpath) 若未找到则 format_obj 为空 dict.
    """
    for py_file in source_root.rglob("formats.py"):
        if _should_skip(py_file):
            continue
        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if material_id not in content:
            continue
        try:
            tree = ast.parse(content, filename=str(py_file))
        except SyntaxError:
            continue

        try:
            rel = str(py_file.relative_to(source_root.parent))
        except ValueError:
            rel = str(py_file)

        for call_node in iter_format_calls(tree, material_id):
            kw_dict = extract_kwargs(call_node)
            if kw_dict.get("id") != material_id:
                continue
            const = find_constant_name(tree, call_node) or "(list/func)"
            return kw_dict, const, rel
    return {}, None, None


def _scan_usages(source_root: Path, material_id: str) -> list[dict]:
    """Step 2: 扫全库 .py 文件, 收集 FORMAT_IN/FORMAT_OUT 引用清单."""
    usages: list[dict] = []
    for py_file in source_root.rglob("*.py"):
        if _should_skip(py_file):
            continue
        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if material_id not in content:
            continue
        try:
            rel = str(py_file.relative_to(source_root.parent))
        except ValueError:
            rel = str(py_file)

        for lineno, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if material_id not in stripped:
                continue
            if not any(k in stripped for k in (*INPUT_ROLES, *OUTPUT_ROLES)):
                continue
            role_tokens = []
            if any(k in stripped for k in INPUT_ROLES):
                role_tokens.append("INPUT")
            if any(k in stripped for k in OUTPUT_ROLES):
                role_tokens.append("OUTPUT")
            usages.append({
                "file": rel, "lineno": lineno,
                "role": "+".join(role_tokens) or "UNKNOWN",
                "line": stripped[:120],
            })
    return usages


class MaterialExtractorWorker(Worker):
    """扫描 source_root 下所有 formats.py, 用 AST 找到 Format(id="...", ...) Material 实例,
    提取 id/name/description/examples/tags/parent 等关键字参数 (format_obj).
    同时扫描全部 .py 文件, 收集 FORMAT_IN/FORMAT_OUT 引用 (usages).

    PASS 时 found 字段指示是否实际找到 Material; FAIL 仅在入口参数违约时触发.
    """

    DESCRIPTION = "用 AST 从 formats.py 提取 Material (Format 对象) 字段; 扫描全部源码收集 FORMAT_IN/OUT 引用"
    FORMAT_IN = "doctor.material.request"
    FORMAT_OUT = "doctor.material.extracted"
    INPUT_KEYS = ["material_id"]

    def __init__(self, source_root: str | None = None):
        self._source_root = Path(source_root) if source_root else DEFAULT_SOURCE_ROOT

    def run(self, input_data: Any) -> Verdict:
        material_id = input_data.get("material_id") or ""
        if not material_id:
            return Verdict(
                kind=VerdictKind.FAIL, confidence=1.0,
                output={"material_id": "", "error": "material_id 必填"},
                diagnosis="MaterialExtractor FAIL: material_id 为空",
            )

        source_root = Path(input_data.get("source_root") or self._source_root)
        if not source_root.exists():
            return Verdict(
                kind=VerdictKind.FAIL, confidence=1.0,
                output={"material_id": material_id, "error": f"source_root 不存在: {source_root}"},
                diagnosis=f"MaterialExtractor FAIL: source_root {source_root} 不存在",
            )

        format_obj, constant_name, defined_in = _scan_definition(source_root, material_id)
        usages = _scan_usages(source_root, material_id)
        found = bool(format_obj)

        return Verdict(
            kind=VerdictKind.PASS, confidence=1.0,
            output={
                "material_id": material_id,
                "source_root": str(source_root),
                "found": found,
                "defined_in": defined_in,
                "constant_name": constant_name,
                "format_obj": format_obj,
                "usages": usages,
            },
            diagnosis=f"MaterialExtractor: {material_id} found={found} usages={len(usages)}",
        )
