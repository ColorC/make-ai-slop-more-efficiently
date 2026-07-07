# [OMNI] origin=claude-code domain=services/_governance/progress_steward ts=2026-06-27T00:00:00Z type=router
# [OMNI] summary="plan.md 新鲜度元数据 + 人一键处置(续期/标注/剥离)。给 plan.md OMNI 头加 last_verified/authority=whatnow, 让'以 whatnow 为准'机器可读;剥离/标注是人审后的显式动作, 不自动改文件。"
# [OMNI] why="接 Confluence FreshPage/Hugo expiryDate 的轻量 freshness 机制(只取字段约定不引整套站);进度漂移由人一键处置, 形成闭环。"
# [OMNI] tags=governance,progress-ssot,freshness,human-disposition
# [OMNI] material_id="material:governance.progress_steward.freshness.py"
"""plan.md 新鲜度字段 + 人一键处置(轨一里程碑三)。

机制:
  - set_freshness / mark_fresh: 给 plan.md 的 OMNI 头加/更新一行
    `<!-- [OMNI] last_verified=YYYY-MM-DD authority=whatnow -->`,
    让"进度以 whatnow 为准"从一句话变成机器可读元数据(Confluence FreshPage / Hugo expiryDate 思路)。
  - annotate_lines: 给指定进度行**行尾**追加 `<!-- 进度以 whatnow 为准 -->` 标注(保留原文)。
  - strip_lines: 把指定进度行整行注释成 `<!-- [stripped 进度→whatnow] 原文 -->`(剥离)。
全部是人审后的显式动作(CLI / 审阅台一键), 不在巡检里自动改文件。
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root

_FRESH_RE = re.compile(r"<!--\s*\[OMNI\]\s*last_verified=([^\s]+)\s+authority=(\w+)\s*-->")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _plan_path(plan_id: str, root: Path | None = None) -> Path:
    base = root or omni_workspace_root()
    return base / "docs" / "plans" / plan_id / "plan.md"


def read_freshness(plan_id: str, root: Path | None = None) -> dict[str, Any]:
    """读 plan.md 的 last_verified / authority(没有则空)。"""
    p = _plan_path(plan_id, root)
    if not p.is_file():
        return {"exists": False}
    head = p.read_text(encoding="utf-8", errors="replace")[:2000]
    m = _FRESH_RE.search(head)
    return {"exists": True, "last_verified": m.group(1) if m else None,
            "authority": m.group(2) if m else None, "path": str(p)}


def set_freshness(plan_id: str, *, authority: str = "whatnow",
                  last_verified: str | None = None, root: Path | None = None) -> dict[str, Any]:
    """在 plan.md OMNI 头里加/更新 last_verified + authority 行。返回结果。"""
    p = _plan_path(plan_id, root)
    if not p.is_file():
        return {"ok": False, "error": f"plan.md 不存在: {p}"}
    lv = last_verified or _today()
    text = p.read_text(encoding="utf-8")
    lines = text.split("\n")
    new_line = f"<!-- [OMNI] last_verified={lv} authority={authority} -->"
    # 已有 freshness 行 → 原地更新
    for i, ln in enumerate(lines[:40]):
        if _FRESH_RE.search(ln):
            lines[i] = new_line
            p.write_text("\n".join(lines), encoding="utf-8")
            return {"ok": True, "action": "updated", "last_verified": lv, "authority": authority}
    # 无 → 插在第一行 OMNI 头之后(找首个 [OMNI] 行)
    insert_at = 0
    for i, ln in enumerate(lines[:40]):
        if "[OMNI]" in ln:
            insert_at = i + 1
            break
    lines.insert(insert_at, new_line)
    p.write_text("\n".join(lines), encoding="utf-8")
    return {"ok": True, "action": "inserted", "last_verified": lv, "authority": authority}


def mark_fresh(plan_id: str, root: Path | None = None) -> dict[str, Any]:
    """人一键"续期"(Mark as Fresh): 把 last_verified 刷成今天。"""
    return set_freshness(plan_id, last_verified=_today(), root=root)


def _edit_lines(plan_id: str, line_nos: list[int], mode: str, root: Path | None = None) -> dict[str, Any]:
    p = _plan_path(plan_id, root)
    if not p.is_file():
        return {"ok": False, "error": f"plan.md 不存在: {p}"}
    lines = p.read_text(encoding="utf-8").split("\n")
    changed = []
    for n in sorted(set(line_nos)):
        if n < 1 or n > len(lines):
            continue
        orig = lines[n - 1]
        if mode == "annotate":
            if "进度以 whatnow 为准" in orig:
                continue
            lines[n - 1] = orig.rstrip() + "  <!-- 进度以 whatnow 为准 -->"
        elif mode == "strip":
            if orig.lstrip().startswith("<!-- [stripped"):
                continue
            lines[n - 1] = f"<!-- [stripped 进度→whatnow] {orig.strip()} -->"
        changed.append(n)
    if changed:
        p.write_text("\n".join(lines), encoding="utf-8")
        set_freshness(plan_id, root=root)  # 处置完顺手续期
    return {"ok": True, "mode": mode, "changed_lines": changed}


def annotate_lines(plan_id: str, line_nos: list[int], root: Path | None = None) -> dict[str, Any]:
    """标注: 给进度行行尾加 '进度以 whatnow 为准'(保留原文)。"""
    return _edit_lines(plan_id, line_nos, "annotate", root=root)


def strip_lines(plan_id: str, line_nos: list[int], root: Path | None = None) -> dict[str, Any]:
    """剥离: 把进度行整行注释掉(原文保留在注释里, 可回溯)。"""
    return _edit_lines(plan_id, line_nos, "strip", root=root)
