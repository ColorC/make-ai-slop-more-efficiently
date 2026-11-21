# [OMNI] origin=claude-code domain=project_atlas ts=2026-06-21 type=helper status=active
# [OMNI] summary="writer:把 author 的 object-SKILL 草稿写进 staging/<space>/<name>/SKILL.md(UTF-8 无 BOM),并 append 项目速览名录草稿。"
# [OMNI] why="AI 产出不直进 canonical:先落 staging 待人审(评审 2);SKILL.md 必须 UTF-8 无 BOM(check-skill-metadata 要求,实测 Codex 据此触发)。"
# [OMNI] tags=project_atlas,writer
"""project_atlas writer —— 落 staging(草稿) + 名录草稿。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from ._paths import ATLAS_PATH, STAGING_ROOT, ensure_dirs

_SLUG = re.compile(r"[^a-z0-9-]+")


def slug(s: str) -> str:
    out = _SLUG.sub("-", (s or "").strip().lower()).strip("-")
    return out or "unnamed"


def write_skill(space: str, skill: dict) -> str:
    """写一条 object-SKILL 到 staging/<space>/<name>/SKILL.md(UTF-8 无 BOM)。返回路径。"""
    ensure_dirs()
    name = slug(skill.get("name", ""))
    desc = (skill.get("description") or "").replace("\n", " ").strip()
    body = (skill.get("body") or "").rstrip()
    d = STAGING_ROOT / space / name
    d.mkdir(parents=True, exist_ok=True)
    content = f"---\nname: {name}\ndescription: >-\n  {desc}\n---\n\n{body}\n"
    p = d / "SKILL.md"
    p.write_text(content, encoding="utf-8")  # Python utf-8 = 无 BOM
    return str(p)


def write_atlas_entry(entry: dict) -> None:
    """把一条项目速览名录草稿 append 进 atlas.jsonl(待并入 omni project)。"""
    ensure_dirs()
    rec = {**entry, "staged_at": datetime.now().isoformat(timespec="seconds")}
    with ATLAS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
