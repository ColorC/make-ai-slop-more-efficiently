# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:core.guardian.rules.material_kind_validator.py"
"""Guardian 规则 · OMNI-037 · Material kind 必填 (F-19 硬规则).

检查: `formats.py` / `materials.py` 文件若含 `Format(` 或 `Material(` 构造, 必须至少
出现一次 `"kind.source"` / `"kind.internal"` / `"kind.sink"` 字符串 (tags 里的 kind.* 标签).

实现: 文件级粗粒度检查 (不区分单条 Material 逐个核对 · 精确检测由 doctor
MaterialKindLegalityWorker 覆盖). 若文件有 Material 定义但整体**完全缺** kind.* 字符串
→ 违规 · HIGH.

豁免:
- _archive / _graveyard 归档
- 外部 / vendors
- 非 formats.py / materials.py 文件
"""
from __future__ import annotations

import re
from pathlib import Path

from ._base import FileContext, GuardianRule, _has_content, _is_external


_MATERIAL_CALL_RE = re.compile(r"\b(Format|Material)\s*\(")
_KIND_TAG_RE = re.compile(r"[\"']kind\.(source|internal|sink)[\"']")


def _is_formats_py(ctx: FileContext) -> bool:
    """限定检查范围: formats.py / materials.py 文件."""
    name = Path(ctx.path).name
    return name in ("formats.py", "materials.py")


def _is_archived(ctx: FileContext) -> bool:
    p = ctx.path.replace("\\", "/")
    return "_archive" in p or "_graveyard" in p


def _check_material_kind_missing(ctx: FileContext) -> bool:
    """OMNI-037: formats.py / materials.py 中有 Material 定义但缺 kind.* tag.

    文件级检查: 若文件含 Format(/Material( 但整体无 "kind.source/internal/sink" → 违规.
    不对单条 Material 精确核对 (那由 doctor blackboard 子域做).
    """
    if _is_external(ctx) or _is_archived(ctx):
        return False
    if not _has_content(ctx):
        return False
    if not _is_formats_py(ctx):
        return False

    content = ctx.content or ""
    if not _MATERIAL_CALL_RE.search(content):
        return False  # 无 Material 定义 · 不适用
    if _KIND_TAG_RE.search(content):
        return False  # 有 kind.* · 合规 (粗粒度)
    return True


RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-037",
        name="material-kind-required",
        severity="HIGH",
        description=(
            "formats.py / materials.py 文件有 Material 定义但整体缺 kind.* tag (F-19). "
            "每条 Material 的 tags 必须含 kind.source / kind.internal / kind.sink 之一. "
            "文件级粗检 · 精确到单条由 doctor blackboard MaterialKindLegalityWorker 覆盖."
        ),
        check=_check_material_kind_missing,
        disposition=["warn"],
        certainty="absolute",
        message_template=(
            "{path} 含 Material/Format 定义但整体无 kind.source/internal/sink 标注.\n"
            "  F-19 硬规则: 每条 Material 的 tags 必须含 kind.* 三分之一.\n"
            "  修法: 在 tags=[...] 里加 'kind.source' (外部输入) / 'kind.internal' "
            "(Worker 间流转) / 'kind.sink' (终态) 之一.\n"
            "  精确到哪条缺 · 跑 doctor.blackboard.MaterialKindLegalityWorker."
        ),
    ),
]
