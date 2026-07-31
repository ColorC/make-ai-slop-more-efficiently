# [OMNI] origin=claude-code domain=omnicompany/repair ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:core.repair.format_patcher.ast_engine.py"
"""FormatPatcherWorker — Repair Team Worker (Format 修复分组 · #2).

Worker 协议:
  FORMAT_IN  = repair.fmt.attempt
  FORMAT_OUT = repair.fmt.attempt

职责: 将 LLM 给出的 delta 精准写入 Format() 源码定义, 使用 guarded_write。
"""
from __future__ import annotations

import ast
import logging
import re
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

logger = logging.getLogger(__name__)

_DEFAULT_SOURCE_ROOT = Path("e:/WindowsWorkspace/omnicompany/src/omnicompany")


# ════════════════════════════════════════════════════════════════
# AST 精准 patch Format() 块工具函数
# ════════════════════════════════════════════════════════════════

def _to_repr(value: Any) -> str:
    """将 Python 值转为代码字面量字符串。"""
    if isinstance(value, str):
        # 对长字符串使用括号换行格式，必须转义所有 \ 和 "
        if "\n" in value or len(value) > 60:
            escaped = value.replace('\\', '\\\\').replace('"', '\\"')
            return f'(\n    "{escaped}"\n)'
        return repr(value)
    if isinstance(value, list):
        items = ", ".join(repr(x) for x in value)
        return f"[{items}]"
    if isinstance(value, dict):
        return repr(value)
    return repr(value)


def _char_offset(lines: list[str], line_idx: int, col_byte: int) -> int:
    """将 AST (line_idx, col_byte) 转换为 Python 字符串的字符偏移量。

    Python 3.8+ 的 AST col_offset / end_col_offset 是 UTF-8 字节偏移，
    而 Python 字符串切片按 Unicode 字符（code point）计。
    对含 CJK 字符的代码，两者不同，必须先把字节偏移转成字符偏移。
    """
    char_base = sum(len(l) for l in lines[:line_idx])
    line = lines[line_idx] if line_idx < len(lines) else ""
    char_col = len(line.encode("utf-8")[:col_byte].decode("utf-8", errors="replace"))
    return char_base + char_col


def patch_format_source(source_text: str, format_id: str, delta: dict) -> tuple[str, list[str]]:
    """
    在 source_text 中找到 Format(id=format_id, ...) 块，
    对 delta 中的每个字段做精准替换（AST 字符级定位）。

    返回 (patched_source, list_of_applied_fields)。
    未应用的字段（找不到 Format 块）返回原文 + 空列表。
    """
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return source_text, []

    # 找到 Format(id=format_id) 调用节点
    format_call: ast.Call | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_format = (isinstance(func, ast.Name) and func.id == "Format") or (
            isinstance(func, ast.Attribute) and func.attr == "Format"
        )
        if not is_format:
            continue
        for kw in node.keywords:
            if kw.arg == "id":
                try:
                    if ast.literal_eval(kw.value) == format_id:
                        format_call = node
                        break
                except Exception:
                    pass
        if format_call:
            break

    if not format_call:
        return source_text, []

    applied: list[str] = []
    current = source_text
    for field, new_value in delta.items():
        current, ok = _apply_single_field(current, format_id, field, new_value)
        if ok:
            applied.append(field)

    return current, applied


def _apply_single_field(source_text: str, format_id: str, field: str, new_value: Any) -> tuple[str, bool]:
    """
    对单个字段做 patch。
    - 字段存在：用 AST 精准定位值的字符范围，替换为 new_repr
    - 字段不存在：在 Format() 最后一个 kwarg 后插入新行
    """
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return source_text, False

    lines = source_text.splitlines(keepends=True)

    # 找 Format(id=format_id)
    format_call: ast.Call | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_format = (isinstance(func, ast.Name) and func.id == "Format") or (
            isinstance(func, ast.Attribute) and func.attr == "Format"
        )
        if not is_format:
            continue
        for kw in node.keywords:
            if kw.arg == "id":
                try:
                    if ast.literal_eval(kw.value) == format_id:
                        format_call = node
                        break
                except Exception:
                    pass
        if format_call:
            break

    if not format_call:
        return source_text, False

    new_repr = _to_repr(new_value)

    # 查找该 field 是否已存在于 kwargs
    for kw in format_call.keywords:
        if kw.arg != field:
            continue
        val = kw.value
        start = _char_offset(lines, val.lineno - 1, val.col_offset)
        end = _char_offset(lines, val.end_lineno - 1, val.end_col_offset)
        patched = source_text[:start] + new_repr + source_text[end:]
        return patched, True

    # 字段不存在 — 在最后一个 kwarg 值结束位置后插入
    if not format_call.keywords:
        return source_text, False

    last_kw = format_call.keywords[-1]
    last_val = last_kw.value
    end = _char_offset(lines, last_val.end_lineno - 1, last_val.end_col_offset)

    last_kw_line = lines[last_kw.value.lineno - 1] if lines else ""
    indent = re.match(r"(\s*)", last_kw_line).group(1)
    kw_indent = " " * last_kw.col_offset

    insert = f",\n{kw_indent}{field}={new_repr}"
    patched = source_text[:end] + insert + source_text[end:]
    return patched, True


# ════════════════════════════════════════════════════════════════
# FormatPatcherWorker
# ════════════════════════════════════════════════════════════════


class FormatPatcherWorker(Worker):
    """将 LLM 给出的 delta 精准写入 Format() 源码定义，使用 guarded_write。

    输入：repair.fmt.attempt（含 delta + health_record.extracted.defined_in）
    输出：同 + patch_ok / patch_applied_fields / patch_error
    """

    DESCRIPTION = "将 delta JSON 字段精准写入 Format() 源码定义"
    FORMAT_IN = "repair.fmt.attempt"
    FORMAT_OUT = "repair.fmt.attempt"

    def run(self, input_data: Any) -> Verdict:
        delta: dict = input_data.get("delta", {})
        health_record: dict = input_data.get("health_record", {})
        format_id: str = input_data.get("format_id", "")
        source_root: str = input_data.get("source_root", "")

        extracted = health_record.get("extracted", {})
        defined_in: str = extracted.get("defined_in", "")

        if not delta:
            return Verdict(
                kind=VerdictKind.PASS,
                confidence=1.0,
                output={**input_data, "patch_ok": True, "patch_applied_fields": [], "patch_note": "delta 为空，跳过 patch"},
                diagnosis="FormatPatcher: delta empty, skip",
            )

        if not defined_in:
            return Verdict(
                kind=VerdictKind.FAIL,
                confidence=1.0,
                output={**input_data, "patch_ok": False, "patch_error": "无法确定 defined_in 路径"},
                diagnosis="FormatPatcher: no defined_in",
            )

        # defined_in 是相对于 source_root.parent 的路径（FormatExtractor 的约定）
        source_root_path = Path(source_root) if source_root else _DEFAULT_SOURCE_ROOT
        target_path = source_root_path.parent / defined_in
        if not target_path.exists():
            target_path = source_root_path / defined_in
        if not target_path.exists():
            target_path = Path(defined_in)
        if not target_path.exists():
            return Verdict(
                kind=VerdictKind.FAIL,
                confidence=1.0,
                output={**input_data, "patch_ok": False, "patch_error": f"文件不存在: {target_path}"},
                diagnosis=f"FormatPatcher: file not found {target_path}",
            )

        try:
            original = target_path.read_text(encoding="utf-8")
            patched, applied = patch_format_source(original, format_id, delta)

            if not applied:
                return Verdict(
                    kind=VerdictKind.PASS,
                    confidence=1.0,
                    output={**input_data, "patch_ok": True, "patch_applied_fields": [], "patch_note": "未找到可 patch 字段"},
                    diagnosis="FormatPatcher: no fields applied",
                )

            from omnicompany.core.guarded_write import write_file
            write_file(
                target_path,
                patched,
                origin="omnicompany",
                domain="repair",
                node="format-patcher",
                purpose=f"LLM 修复 {format_id} Format 字段: {applied}",
            )

            return Verdict(
                kind=VerdictKind.PASS,
                confidence=1.0,
                output={**input_data, "patch_ok": True, "patch_applied_fields": applied},
                diagnosis=f"FormatPatcher: applied {applied}",
            )
        except Exception as e:
            logger.error("FormatPatcher failed: %s", e)
            return Verdict(
                kind=VerdictKind.FAIL,
                confidence=1.0,
                output={**input_data, "patch_ok": False, "patch_error": str(e)},
                diagnosis=f"FormatPatcher error: {e}",
            )
