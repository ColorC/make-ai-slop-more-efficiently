# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-06-27T00:00:00Z type=config
# [OMNI] summary="Guardian 规则 OMNI-096 · 语义标签受控词表。源文件里硬写的 semantic_tags 值必须是 '<ns>.<value>' 受控形态(ns∈domain/kind/stage/topic/type);缺失不报(渐进采用)。"
# [OMNI] why="AI 自由打标会出孤儿标签(AI/人工智能/ai)。受控词表兜边界;但缺失不罚(反 WinFS 鸡生蛋)。"
# [OMNI] tags=guardian,semantic-os,controlled-vocabulary,OMNI-096
# [OMNI] material_id="material:core.guardian.rules.semantic_tags_controlled.py"
"""Guardian 规则 · OMNI-096 · 语义标签受控词表(needs_judgment)。

检查源文件里硬写的 `semantic_tags`(= [...] / "semantic_tags": [...]) 值是否都是受控形态
`<namespace>.<value>`(namespace ∈ domain/kind/stage/topic/type)。出现不合形态的值 → 违规(疑似孤儿标签)。
缺 semantic_tags **不报**(渐进采用)。豁免归档/外部/本规则自身与 schema 定义文件。
"""
from __future__ import annotations

import re

from ._base import FileContext, GuardianRule, _has_content, _is_external

_TAGS_BLOCK_RE = re.compile(r"semantic_tags[\"']?\s*[:=]\s*\[([^\]]*)\]")
_TOKEN_RE = re.compile(r"[\"']([^\"']+)[\"']")
_NS = ("domain", "kind", "stage", "topic", "type")
# 本规则自身 + schema 定义文件里会出现 'semantic_tags' 字样, 豁免免自噬
_SELF = ("semantic_tags.py", "schema.py")


def _is_archived(ctx: FileContext) -> bool:
    p = ctx.path.replace("\\", "/")
    return "_archive" in p or "_graveyard" in p


def _check_uncontrolled_tags(ctx: FileContext) -> bool:
    if _is_external(ctx) or _is_archived(ctx) or not _has_content(ctx):
        return False
    if any(ctx.path.replace("\\", "/").endswith(s) for s in _SELF):
        return False
    content = ctx.content or ""
    for block in _TAGS_BLOCK_RE.findall(content):
        for val in _TOKEN_RE.findall(block):
            val = val.strip()
            if "." not in val:
                return True
            ns = val.split(".", 1)[0]
            if ns not in _NS:
                return True
    return False


RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-096",
        name="semantic-tags-controlled",
        severity="MEDIUM",
        description=(
            "源文件硬写的 semantic_tags 值必须是受控形态 '<ns>.<value>'(ns ∈ "
            "domain/kind/stage/topic/type, kind.* 闭集 source/internal/sink)。"
            "防 AI 自由打标出孤儿标签。缺 semantic_tags 不报(渐进采用)。"
        ),
        check=_check_uncontrolled_tags,
        disposition=["warn"],
        certainty="needs_judgment",
        message_template=(
            "{path} 的 semantic_tags 含非受控值(不是 '<ns>.<value>' 或 ns 越界).\n"
            "  受控词表: domain.* / kind.{{source,internal,sink}} / stage.* / topic.* / type.*\n"
            "  权威: docs/standards/semantic-filesystem.md;写入走 semantic_fs.set_semantic(自动校验)."
        ),
    ),
]
