# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-06-27T00:00:00Z type=config
# [OMNI] summary="Guardian 规则 OMNI-098 · 语言治理确定性半(commit-time)。从单一真源 docs/standards/prose_terms.yaml 读: 中文文档里命中禁用英文代称/术语变体/已知压缩缩写 → 每次 patrol 即报。LLM 语义半(非中文泄漏判定/惜字如金)留 weekly cron。"
# [OMNI] why="语言病不能只靠 weekly cron 周才发现一次;确定性可逮的(forbidden_alias/术语变体/已知缩写)进 Guardian, 提交即逮, 及时排除。单一真源, 不另写词表。"
# [OMNI] tags=guardian,language,terminology,OMNI-098,commit-time
# [OMNI] material_id="material:core.guardian.rules.prose_health_deterministic.py"
"""Guardian 规则 · OMNI-098 · 语言治理确定性半(needs_judgment, commit-time patrol)。

只在中文文档(docs/**/*.md, 跳归档/代码)上, 从 prose_terms.yaml 单一真源命中:
  - forbidden_aliases: 本该中文却写英文的代称(done/TODO/WIP/OK…)
  - term_consistency.variants: 术语变体(便宜模型/审查台…)→ 应统一到 canonical
  - abbrev_expansions.short: 已知过度压缩缩写(好恶词…)
缺失不报。LLM 语义判定(非中文泄漏的 keep/change、惜字如金)由 weekly cron 的 omni governance prose-* 做。
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from ._base import FileContext, GuardianRule, _has_content

_CJK_RE = re.compile(r"[一-鿿]")


@lru_cache(maxsize=2)
def _load_terms(path_str: str) -> tuple:
    """读 prose_terms.yaml → (forbidden set, variants set, abbrev set)。"""
    p = Path(path_str)
    if not p.is_file():
        return (frozenset(), frozenset(), frozenset())
    try:
        import yaml
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return (frozenset(), frozenset(), frozenset())
    forbidden = {str(x.get("en", "")).lower() for x in (d.get("forbidden_aliases") or []) if x.get("en")}
    variants = set()
    for g in d.get("term_consistency") or []:
        variants.update(str(v) for v in (g.get("variants") or []))
    abbr = {str(a.get("short", "")) for a in (d.get("abbrev_expansions") or []) if a.get("short")}
    return (frozenset(forbidden), frozenset(variants), frozenset(abbr))


def _terms_for(ctx: FileContext) -> tuple:
    # 从 abs_path 往上找仓根(含 docs/standards/prose_terms.yaml)
    p = Path(ctx.abs_path)
    for parent in [p, *p.parents]:
        cand = parent / "docs" / "standards" / "prose_terms.yaml"
        if cand.is_file():
            return _load_terms(str(cand))
    return (frozenset(), frozenset(), frozenset())


def _is_zh_doc(ctx: FileContext) -> bool:
    p = ctx.path.replace("\\", "/")
    if not p.endswith(".md") or "_archive" in p or "_graveyard" in p:
        return False
    return p.startswith("docs/") or "/docs/" in p


_EN_WORD = re.compile(r"(?<![A-Za-z])([A-Za-z]{2,})(?![A-Za-z])")


def _check_prose_health(ctx: FileContext) -> bool:
    if not _has_content(ctx) or not _is_zh_doc(ctx):
        return False
    forbidden, variants, abbr = _terms_for(ctx)
    if not (forbidden or variants or abbr):
        return False
    in_fence = False
    for line in (ctx.content or "").split("\n"):
        s = line.lstrip()
        if s.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence or s.startswith(("<!-- [OMNI]", "# [OMNI]")):
            continue
        is_zh = len(_CJK_RE.findall(line)) >= 2
        if not is_zh:
            continue
        if any(v and v in line for v in variants):
            return True
        if any(a and a in line for a in abbr):
            return True
        for w in _EN_WORD.findall(line.split("`")[0] if "`" in line else line):
            if w.lower() in forbidden:
                return True
    return False


RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-098",
        name="prose-health-deterministic",
        severity="LOW",
        description=(
            "中文文档命中语言治理单一真源(prose_terms.yaml)里的: 禁用英文代称 / 术语变体 / 已知压缩缩写。"
            "确定性半 commit-time 即报;LLM 语义半(非中文泄漏判定/惜字如金)走 weekly omni governance prose-*。"
        ),
        check=_check_prose_health,
        disposition=["warn"],
        certainty="needs_judgment",
        message_template=(
            "{path} 命中语言治理项(禁用英文代称 / 术语变体 / 已知缩写, 见 docs/standards/prose_terms.yaml).\n"
            "  全量治理 + 改法走 omni governance prose-lang / prose-term / prose-compress."
        ),
    ),
]
