# [OMNI] origin=claude-code domain=decisions ts=2026-07-10T00:00:00Z type=module status=active
# [OMNI] summary="开工切片装配(确定性零LLM):按 项目/管线/关键词 拉出该情境的手册条目+判例+活跃猜想+近期偏离+检查单,脚注带延续判据三档与独立三出口提醒。"
# [OMNI] why="多域并存时各管线要快速找到自己那一片(分册×book_refs×关键词三轴);自动开工注入归框架线,本件是它的手动前身,也是接口提醒的落点(提醒指向真实可跑命令)。"
# [OMNI] tags=decisions,brief,slice,onboarding,decision-ontology
"""开工切片装配(omni decisions brief)。

主轴=**当前会话绑定**:零参数时读 data/cc_session_active.json 的 active_plan/project 自动出切片
——正在推进哪个 plan/project,切片就跟着哪个(框架线"开工自动注入"的手动前身,同一份绑定真源)。

四个过滤轴(全确定性,可叠加):
  - --plan: 该计划名下的既有拍板与猜想(记录 track.id 匹配,裸名归一化);
  - -p/--project: 该项目归属分册(book.PART_META)条目 + 项目活跃猜想 + 近期偏离;
  - --pipeline: 注册表该条的 book_refs 条目 + when/scale/confirm 声明;
  - --keys: 关键词对条目名/结论的子串匹配(硬筛,不做语义)。
脚注固定输出延续判据三档与独立三出口——提醒长在切片里,用的人一定看得见。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import library
from .book import BOOK_DIR, all_book_entries, part_meta

_OMNI_ROOT = Path(__file__).resolve().parents[5]
_SESSION_BINDING = _OMNI_ROOT / "data" / "cc_session_active.json"


def _norm_plan(plan_id: str) -> str:
    """plan id 归一成 track.id 惯用裸名:去目录前缀、去 [日期] 前缀、小写。"""
    p = (plan_id or "").strip().split("/")[-1]
    if p.startswith("[") and "]" in p:
        p = p.split("]", 1)[1]
    return p.strip().lower()


def _plan_match(track_id: str, plan_norm: str) -> bool:
    t = (track_id or "").strip().lower()
    return bool(t and plan_norm) and (t == plan_norm or t in plan_norm or plan_norm in t)


def read_session_binding() -> dict[str, str]:
    """当前会话绑定(omni plan use / session bind 写的同一份台账);读不到返空。"""
    try:
        d = json.loads(_SESSION_BINDING.read_text(encoding="utf-8"))
        return {"plan": d.get("active_plan") or "", "project": d.get("project") or ""}
    except Exception:
        return {"plan": "", "project": ""}

CONTINUITY_FOOTER = (
    "延续判据(手册 00 治理规则6): 与判例一致→自动走,消费留痕即延续证据;两可→自由裁量,record 一笔链回先例;"
    "真冲突→惊动用户。\n"
    "显式独立三出口(不许静默另起炉灶): ①项目特化条目(特别优于一般,声明成立情境) "
    "②主动变更偏离 `omni ledger deviate --mode active_change` ③条目 deviation_waiver 豁免。\n"
    "改陈述库唯一通道=候选流水线(omni decisions candidate → 内部治理件 → candidate-apply);"
    "用户裁决以对话答复为主,裸问题不得混入普通审阅队列。"
)


def _match(text: str, keys: list[str]) -> bool:
    t = (text or "").lower()
    return any(k.lower() in t for k in keys)


def build_brief(
    *,
    plan: str = "",
    project: str = "",
    pipeline: str = "",
    keys: list[str] | None = None,
    use_session_binding: bool = True,
) -> dict[str, Any]:
    """装配切片。四轴可叠加;plan/project 全空且允许时,取当前会话绑定为缺省。"""
    keys = [k for k in (keys or []) if (k or "").strip()]
    bound_from_session = False
    if use_session_binding and not plan and not project and not pipeline and not keys:
        b = read_session_binding()
        plan, project = b["plan"], b["project"]
        bound_from_session = bool(plan or project)
    out: dict[str, Any] = {"plan": plan, "project": project, "pipeline": pipeline,
                           "keys": keys, "from_session_binding": bound_from_session}

    # ── 主轴: 该计划名下的既有拍板与猜想(继续一个 plan 时先看它自己已经定过什么) ──
    if plan:
        pn = _norm_plan(plan)
        plan_recs = [r for r in library.active_records()
                     if _plan_match(((r.get("track") or {}).get("id") or ""), pn)]
        out["plan_records"] = {
            "total": len(plan_recs),
            "decisions": [
                {"id": r["id"], "status": r.get("status"),
                 "statement": (r.get("statement") or "")[:80]}
                for r in plan_recs if r.get("kind") == "decision"
            ][-20:],
            "beliefs": [
                {"id": r["id"], "status": r.get("status"),
                 "statement": (r.get("statement") or "")[:80]}
                for r in plan_recs if r.get("kind") == "belief"
            ][-10:],
        }

    # ── 轴1: 管线声明(注册表=机检真源) ──
    pipe_refs: set[str] = set()
    if pipeline:
        from omnicompany.core import registry as _registry

        if not _registry.names():
            _registry.discover()
        entry = _registry.get(pipeline)
        if entry is None:
            out["pipeline_error"] = f"管线未注册: {pipeline}"
        else:
            pipe_refs = set(getattr(entry, "book_refs", ()) or ())
            out["pipeline_decl"] = {
                "when": getattr(entry, "when", None),
                "scale": getattr(entry, "scale", None),
                "confirm": getattr(entry, "confirm", False),
                "book_refs": sorted(pipe_refs),
            }
            if not project:
                # 管线域名可回推项目(轻推断,显式给 -p 优先)
                project = entry.domain if entry.domain in ("vilo",) else project

    # ── 轴2+3: 手册条目(分册归属 × 关键词) ──
    entries = []
    for path, e in all_book_entries():
        meta = part_meta(path.stem)
        hit_pipe = e["anchor"] in pipe_refs
        hit_proj = bool(project) and meta["project"] == project
        hit_keys = bool(keys) and _match(e["name"] + " " + e["statement"], keys)
        if pipe_refs or project or keys:
            if not (hit_pipe or hit_proj or hit_keys):
                continue
        entries.append({
            "anchor": e["anchor"],
            "name": e["name"],
            "statement": e["statement"],
            "case_ids": e["case_ids"],
            "part": path.stem,
            "authority": meta["authority"],
            "via": ("pipeline" if hit_pipe else "project" if hit_proj else
                    "keys" if hit_keys else "all"),
        })
    out["entries"] = entries

    # 检查单指针(该切片涉及的分册)
    seen_parts = sorted({e["part"] for e in entries})
    out["checklists"] = [
        str(BOOK_DIR / "checklists" / f"{s}-检查单.md")
        for s in seen_parts
        if (BOOK_DIR / "checklists" / f"{s}-检查单.md").is_file()
    ]

    # ── 项目活跃猜想(untested/challenged 别当定论用) ──
    if project:
        beliefs = [r for r in library.active_records()
                   if r.get("kind") == "belief" and (
                       r.get("project") == project
                       or f"domain:{project}" in (r.get("tags") or []))]
        by_status: dict[str, int] = {}
        for b in beliefs:
            s = b.get("status") or "untested"
            by_status[s] = by_status.get(s, 0) + 1
        risky = [b for b in beliefs if b.get("risk_if_wrong") == "high"
                 and (b.get("status") or "untested") in ("untested", "challenged", "searching")]
        out["beliefs"] = {
            "total": len(beliefs),
            "by_status": by_status,
            "high_risk_unsettled": [
                {"id": b["id"], "statement": (b.get("statement") or "")[:80]}
                for b in sorted(risky, key=lambda r: r.get("created_at") or "")[-8:]
            ],
        }

        # 近期偏离(引用了该项目记录的)
        try:
            from omnicompany.packages.services._core.ledger import list_deviations

            proj_ids = {r["id"] for r in library.active_records()
                        if r.get("project") == project and r.get("id")}
            devs = []
            for ev in list_deviations(200):
                refs = (ev.get("deviation") or {}).get("refs") or []
                if any(r in proj_ids for r in refs):
                    devs.append({"time": ev.get("time"), "kind": (ev.get("deviation") or {}).get("kind"),
                                 "note": (ev.get("deviation") or {}).get("note", "")[:70], "refs": refs})
            out["recent_deviations"] = devs[-5:]
        except Exception:
            out["recent_deviations"] = []

    out["footer"] = CONTINUITY_FOOTER
    return out


def render_brief_text(b: dict[str, Any]) -> str:
    lines: list[str] = []
    head = " · ".join(x for x in (
        f"计划 {b['plan']}" if b.get("plan") else "",
        f"项目 {b['project']}" if b.get("project") else "",
        f"管线 {b['pipeline']}" if b.get("pipeline") else "",
        f"关键词 {','.join(b['keys'])}" if b.get("keys") else "") if x)
    lines.append(f"══ 开工切片 {head or '(全书索引)'} ══"
                 + ("(自动取自当前会话绑定)" if b.get("from_session_binding") else ""))

    pr = b.get("plan_records")
    if pr:
        lines.append(f"\n本计划已有记录 {pr['total']} 条——先延续再新拍:")
        for d in pr["decisions"]:
            lines.append(f"  [{d['status']}] {d['id']} {d['statement']}")
        for bl in pr["beliefs"]:
            lines.append(f"  [猜 {bl['status']}] {bl['id']} {bl['statement']}")

    if b.get("pipeline_error"):
        lines.append(f"⚠ {b['pipeline_error']}")
    decl = b.get("pipeline_decl")
    if decl:
        w = decl.get("when") or {}
        s = decl.get("scale") or {}
        lines.append(f"管线声明: when={w.get('semantic', '(未声明)')}")
        lines.append(f"  scale={s or '(未声明)'}"
                     + (" · 确认门 confirm=True(--yes)" if decl.get("confirm") else ""))

    entries = b.get("entries") or []
    lines.append(f"\n手册条目 {len(entries)} 条:")
    if not entries:
        lines.append("  (无命中——该域还没开分册?看 docs/ontology/接入指南.md 场景三)")
    for e in entries:
        cases = f"  判例:{','.join(e['case_ids'])}" if e["case_ids"] else ""
        lines.append(f"  [{e['via']}] {e['name']} — {e['statement'][:64]}")
        lines.append(f"        {e['anchor']}{cases}")

    for c in b.get("checklists") or []:
        lines.append(f"检查单: {c}")

    bel = b.get("beliefs")
    if bel:
        lines.append(f"\n活跃猜想 {bel['total']} 条 {bel['by_status']}")
        for hb in bel.get("high_risk_unsettled") or []:
            lines.append(f"  ⚠ 高风险未定论 {hb['id']}: {hb['statement']}(别在其上叠推导)")
    for d in b.get("recent_deviations") or []:
        lines.append(f"近期偏离: [{d['kind']}] {d['note']} → {','.join(d['refs'])}")

    lines.append("\n" + b["footer"])
    return "\n".join(lines)
