# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:core.guardian.format_in_mode.validator.py"
"""Guardian 规则 · OMNI-038 · FORMAT_IN_MODE 必填 (R-24 硬规则).

检查: Python 文件若含类属性 `FORMAT_IN = [...]` (list literal · 多入 Worker), 必须同
类体内显式声明 `FORMAT_IN_MODE = "and"` 或 `"or"`.

实现: 文件级正则扫 · 对每个类块匹配 FORMAT_IN list literal 和 FORMAT_IN_MODE 声明.
同一类里有 list FORMAT_IN 但无 MODE → 违规 · HIGH.

豁免:
- _archive / _graveyard 归档
- 外部 / vendors
- 单 str FORMAT_IN (FORMAT_IN = "..." 而非 [...]) 不适用
"""
from __future__ import annotations

import re

from ._base import FileContext, GuardianRule, _has_content, _is_external, _is_python


# 匹配 class 头: `class Foo(Bar):` (提取类名 + 位置)
_CLASS_RE = re.compile(r"^class\s+(\w+)\s*\([^)]*\)\s*:", re.MULTILINE)

# 匹配类属性 FORMAT_IN = [...]  (list literal 多入)
# 允许 list 跨多行
_FORMAT_IN_LIST_RE = re.compile(
    r"FORMAT_IN\s*=\s*\[[^\]]*\]",
    re.DOTALL,
)

# 匹配 FORMAT_IN = "..." 或 '...' (单 str 不适用本规则)
_FORMAT_IN_STR_RE = re.compile(r"FORMAT_IN\s*=\s*[\"']")

# 匹配 FORMAT_IN_MODE = "and" / "or"
_FORMAT_IN_MODE_RE = re.compile(
    r"FORMAT_IN_MODE\s*=\s*[\"'](and|or)[\"']",
)


def _is_archived(ctx: FileContext) -> bool:
    p = ctx.path.replace("\\", "/")
    return "_archive" in p or "_graveyard" in p


def _split_class_blocks(content: str) -> list[tuple[str, str]]:
    """把 content 按 class 切成 (class_name, class_body) 列表.

    简化实现: 按 `^class ...:` 行切分 · body 包含从 class 行到下一个 top-level
    构造 (另一个 class 或模块级 def 或文件结尾) 之前.
    """
    lines = content.split("\n")
    starts: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = _CLASS_RE.match(line)
        if m:
            starts.append((i, m.group(1)))

    blocks: list[tuple[str, str]] = []
    for idx, (line_idx, name) in enumerate(starts):
        # body 终点: 下一个 class / 文件结尾
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        # 只到下一个"模块级" 行 (不缩进) — 简化为到下一个 class 即可
        body = "\n".join(lines[line_idx:end])
        blocks.append((name, body))
    return blocks


def _check_format_in_mode_missing(ctx: FileContext) -> bool:
    """OMNI-038: 类有 FORMAT_IN = list 但无 FORMAT_IN_MODE 声明."""
    if _is_external(ctx) or _is_archived(ctx):
        return False
    if not _has_content(ctx):
        return False
    if not _is_python(ctx):
        return False

    content = ctx.content or ""
    # 快速过滤: 整个文件无 FORMAT_IN 就跳过
    if "FORMAT_IN" not in content:
        return False

    for class_name, body in _split_class_blocks(content):
        # 找 FORMAT_IN = list literal
        fi_list_match = _FORMAT_IN_LIST_RE.search(body)
        if fi_list_match is None:
            continue  # 单 str 或不含, 不适用
        # 确认是 list 而非 str (排除 FORMAT_IN = "...[..." 这种字符串噪声)
        # _FORMAT_IN_LIST_RE 已限定 `= [` 开头, 所以匹配到就是 list
        # 检查同类体内是否有 FORMAT_IN_MODE 声明
        if _FORMAT_IN_MODE_RE.search(body):
            continue
        # 此类有 list FORMAT_IN 但无 MODE → 违规
        return True
    return False


RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-038",
        name="format-in-mode-required",
        severity="HIGH",
        description=(
            "类有 `FORMAT_IN = [...]` (list literal · 多入 Worker) 但无显式 "
            "`FORMAT_IN_MODE = 'and'|'or'` 声明 (R-24). "
            "Worker 多入必须明示 and (composite fan-in · 所有到齐) 或 or (alternative · 任一激活)."
        ),
        check=_check_format_in_mode_missing,
        disposition=["warn"],
        certainty="absolute",
        message_template=(
            "{path} 的类有 FORMAT_IN = list[str] 多入声明, 但缺 FORMAT_IN_MODE 显式声明.\n"
            "  R-24 硬规则: list FORMAT_IN 必须同类体内声明 FORMAT_IN_MODE = 'and' 或 'or'.\n"
            "  'and' = composite fan-in (所有 Material 到齐才激活, Worker 基类默认但需显式).\n"
            "  'or' = alternative (任一 Material 到达即激活, Agent ContextScript 典型).\n"
            "  精确定位 · 跑 doctor.blackboard.FormatInModeCheckerWorker."
        ),
    ),
]
