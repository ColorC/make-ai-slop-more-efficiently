# [OMNI] origin=claude-code domain=domains/decisions ts=2026-07-04T00:00:00Z type=script status=active
"""M2 迁移: data/boss_sight/authored_decisions.json(第二本账) → 统一决策库 + 旧账归档。

统一设计工作室计划(UNIFIED-DESIGN-STUDIO §5 M2 / §6 D3)。
- 逐条转成统一库记录(kind=decision, status=proposed, authority=derived,
  alias=authored-note-<note_id> 与 authored.extract 新产线同键 → 天然幂等衔接);
- error 项跳过(它们本来就不进消费面);
- 迁移后旧文件移到 data/boss_sight/_archive/authored_decisions.json(不删,留档);
- 输出前后计数对账。可重复跑(已归档则跳过迁移一步)。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
_SRC = ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import os

os.environ["OMNI_DECISIONS_STRICT_WRITE"] = "1"

OLD = ROOT / "data" / "boss_sight" / "authored_decisions.json"
ARCHIVE = ROOT / "data" / "boss_sight" / "_archive" / "authored_decisions.json"


def main() -> int:
    from omnicompany.packages.domains.decisions import catalog, library

    src_path = OLD if OLD.is_file() else ARCHIVE
    if not src_path.is_file():
        print("✗ 新旧位置都找不到 authored_decisions.json")
        return 1
    data = json.loads(src_path.read_text(encoding="utf-8"))
    entries = [(nid, v) for nid, v in data.items() if isinstance(v, dict)]
    ok_entries = [(nid, v) for nid, v in entries if not v.get("error")]
    err_entries = len(entries) - len(ok_entries)

    existing = set()
    for rec in library.active_records():
        for a in rec.get("aliases") or []:
            if a.startswith("authored-note-"):
                existing.add(a)

    n_new = n_skip = 0
    for nid, v in ok_entries:
        alias = f"authored-note-{nid}"
        if alias in existing:
            n_skip += 1
            continue
        gist = (v.get("decision_gist") or "").strip()
        constraint = (v.get("constraint") or "").strip()
        statement = (gist + (f"(硬约束: {constraint})" if constraint else ""))[:200]
        applies = (v.get("applies_to") or "").strip() or (v.get("scope") or "").strip()
        project = (v.get("project") or "").strip()
        library.upsert({
            "kind": "decision",
            "statement": statement,
            "scope": "personal",
            "status": "proposed",
            "authority": "derived",
            "project": "" if project == "unfiled" else project,
            "applies_to": applies,
            "aliases": [alias],
            "tags": ["authored-note", "札记炼化", "M2迁移"],
            "anchor": {"kind": "note", "ref": f"authored-note:{nid}",
                       "excerpt": gist[:200]},
            "origin": {"channel": "note", "session_ref": nid, "author": "user",
                       "observed_at": v.get("extracted_at") or ""},
            "created_by": "migrate_authored_decisions",
        })
        n_new += 1
    catalog.rebuild_index()

    # 归档旧文件(留档不删)
    archived = False
    if OLD.is_file():
        ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
        if ARCHIVE.exists():
            ARCHIVE.unlink()
        OLD.rename(ARCHIVE)
        archived = True

    # 对账
    from omnicompany.dashboard.boss_sight.authored.extract import load_decisions
    now_visible = len(load_decisions())
    print("✓ M2 迁移对账:")
    print(f"  旧账条目: {len(entries)}(可用 {len(ok_entries)} / error {err_entries})")
    print(f"  本次迁入: {n_new} / 已存在跳过: {n_skip}")
    print(f"  迁移后统一库投影可见(load_decisions): {now_visible} 条(应≥可用条数)")
    print(f"  旧账归档: {'已移至 ' + str(ARCHIVE.relative_to(ROOT)) if archived else '早已归档'}")
    if now_visible < len(ok_entries):
        print("✗ 对账不过: 投影少于旧账可用条数")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
