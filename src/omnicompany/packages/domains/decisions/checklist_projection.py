# [OMNI] origin=claude-code domain=decisions ts=2026-07-10T00:00:00Z type=module status=active
# [OMNI] summary="检查单投影:语义手册分册→交稿/开工检查单(确定性派生,每节≤9条超限拆分,带Source注释可重建)。"
# [OMNI] why="plan §五(检查单纪律:派生检查单每节5-9条;不承诺照做全对,需临场判断处显式标注)+§七.3(交稿检查单=任务时刻短视图必须保留)。"
# [OMNI] tags=decisions,checklist,projection,decision-ontology
"""检查单投影(手册的任务时刻短视图)。

派生规则(确定性,零 LLM):
  - 每个手册条目 → 一条检查项:结论压缩为一行 + 触发情境(when.trigger 语义);
  - 每节最多 9 条,超限按顺序拆「之二/之三」节(治理规则4);
  - 头部声明:扫描单性质(防漏看),不承诺照做全对,临场判断处以手册正文为准。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .book import BOOK_DIR, book_files, parse_book_entries

CHECKLIST_DIR = BOOK_DIR / "checklists"
_MAX_PER_SECTION = 9


def render_checklist(part_stem: str, out_dir: Path | None = None) -> Path:
    """把某分册(如 10-vilo叙事)渲染成检查单投影,返回落盘路径。"""
    src = next((p for p in book_files() if p.stem == part_stem), None)
    if src is None:
        raise ValueError(f"分册不存在: {part_stem}(现有: {[p.stem for p in book_files()]})")
    entries = parse_book_entries(src)
    if not entries:
        raise ValueError(f"分册 {part_stem} 无条目(含「结论:」的小节)")

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "<!-- [OMNI] origin=ai-ide domain=decisions type=ontology-checklist status=active -->",
        f"<!-- Source: docs/ontology/{src.name} -->",
        f"<!-- 生成投影,勿手改;重渲: omni decisions checklist {part_stem} (生成于 {ts}) -->",
        "",
        f"# {part_stem} · 检查单",
        "",
        "扫描单(防漏看),不承诺照做全对——判断细节以手册条目正文为准,临场拿不准就回读原条目。",
        "",
    ]
    chunks = [entries[i:i + _MAX_PER_SECTION] for i in range(0, len(entries), _MAX_PER_SECTION)]
    for ci, chunk in enumerate(chunks):
        suffix = "" if len(chunks) == 1 else f" · 之{ci + 1}"
        lines.append(f"## 逐条过{suffix}")
        lines.append("")
        for e in chunk:
            first = e["statement"].split(";")[0].split("——")[0][:80]
            lines.append(f"- [ ] **{e['name']}** — {first}(细则见 [{e['name']}]"
                         f"({src.name}#{e['name']}))")
        lines.append("")

    out_root = out_dir or CHECKLIST_DIR
    out_root.mkdir(parents=True, exist_ok=True)
    out = out_root / f"{part_stem}-检查单.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
