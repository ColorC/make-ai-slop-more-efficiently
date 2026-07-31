# [OMNI] origin=claude-code domain=decisions ts=2026-07-10T00:00:00Z type=module status=active
# [OMNI] summary="语义手册(docs/ontology/)结构解析单点:条目=「## 名」+「结论:」小节;供同步脚本与巡检共用,避免两套解析漂移。"
# [OMNI] why="决策本体合并清单#4(手册↔库指针互通)与阶段五巡检(双向引用完整性)都要读手册条目;解析规则只许一份。"
# [OMNI] tags=decisions,ontology,book,parser
"""语义手册结构解析(确定性,零 LLM)。

条目判定:手册 md 文件里含「结论:」行的 "## " 小节(代码块内的模板示例被剔除)。
锚点约定:docs/ontology/<文件名>#<条目名>;库内幂等 alias=ontology:<文件 stem>#<条目名>。
"""

from __future__ import annotations

import re
from pathlib import Path

_OMNI_ROOT = Path(__file__).resolve().parents[5]
BOOK_DIR = _OMNI_ROOT / "docs" / "ontology"

# 各分册的领域归属与权威等级(单点真源;sync 落库与 brief 切片共用)。
# authority: 条目蒸馏自用户拍板=user_explicit;AI 归纳用户未逐条确认=high。
PART_META: dict[str, dict[str, str]] = {
    "10-vilo叙事": {"project": "vilo", "authority": "user_explicit"},
    "20-探索通则": {"project": "omnicompany", "authority": "high"},
}
DEFAULT_PART_META = {"project": "omnicompany", "authority": "high"}


def part_meta(stem: str) -> dict[str, str]:
    return PART_META.get(stem, DEFAULT_PART_META)


_ID_RE = re.compile(r"\b(?:DEC|BLF|CMT)-\d{4}-\d{2}-\d{2}-\d{3}\b")


def strip_code_fences(text: str) -> str:
    """剔除 ``` 代码块(条目模板等示例里的「结论:」不算真条目)。"""
    return re.sub(r"```.*?```", "", text, flags=re.S)


def parse_book_entries(path: Path) -> list[dict]:
    """→ [{name, statement, case_ids, anchor, alias}];确定性结构解析。"""
    text = strip_code_fences(path.read_text(encoding="utf-8"))
    entries: list[dict] = []
    sections = re.split(r"^## +", text, flags=re.M)[1:]
    for sec in sections:
        lines = sec.splitlines()
        name = (lines[0] or "").strip()
        body = "\n".join(lines[1:])
        m = re.search(r"^结论[::](.+)$", body, re.M)
        if not name or not m:
            continue
        statement = re.sub(r"\s+", " ", m.group(1)).strip()
        entries.append({
            "name": name,
            "statement": statement[:200],
            "case_ids": sorted(set(_ID_RE.findall(body))),
            "anchor": f"docs/ontology/{path.name}#{name}",
            "alias": f"ontology:{path.stem}#{name}"[:80],
        })
    return entries


def book_files(book_dir: Path | None = None) -> list[Path]:
    """手册分册文件(排除 INDEX、生成投影 30-知识、说明书性质的接入指南)。"""
    d = book_dir or BOOK_DIR
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.md") if p.stem not in ("INDEX", "30-知识", "接入指南"))


def all_book_entries(book_dir: Path | None = None) -> list[tuple[Path, dict]]:
    out: list[tuple[Path, dict]] = []
    for p in book_files(book_dir):
        for e in parse_book_entries(p):
            out.append((p, e))
    return out


def all_book_anchors(book_dir: Path | None = None) -> set[str]:
    return {e["anchor"] for _, e in all_book_entries(book_dir)}
