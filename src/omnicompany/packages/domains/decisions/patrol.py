# [OMNI] origin=claude-code domain=decisions ts=2026-07-10T00:00:00Z type=module status=active
# [OMNI] summary="决策本体巡检:双向引用完整性(手册↔库↔管线注册表指针悬空/野投影/悬空边)+清场废墟检测(应拆除路径复活/被引用)+when覆盖缺口。确定性零LLM,供 omni decisions patrol 与 guardian 消费。"
# [OMNI] why="plan §五(投影带Source可重建+巡检双向引用完整性)+§十二·五(清场纪律:废墟检测常项);机制卡进巡检不靠自觉。"
# [OMNI] tags=decisions,patrol,integrity,ruins,decision-ontology
"""决策本体巡检(阶段五巡检件,确定性)。

检查项:
  A book_entry_missing_record   手册条目在库里没有蒸馏态指针记录(先跑 scripts/sync_ontology_book.py)
  B dangling_book_ref           库内 distilled 记录的 book_ref 指向不存在的手册锚点
  C pipeline_dangling_book_ref  管线注册表 book_refs 指向不存在的手册锚点
  D projection_to_ghost_pipeline 记录 projections(type=pipeline) 指向未注册管线
  E missing_projection_backfill 管线声明 book_refs 但对应记录 projections 未回填(提示重跑 sync)
  F dangling_links              库内 links(rests_on/supersedes/parent/related) 指向不存在记录
  G ruins                       清场纪律:应拆除路径仍存在(合并清单各项的墓碑清单)
  H when_coverage               管线 when/scale 元数据缺口(advisory 计数,不算违规)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import library
from .book import all_book_anchors, all_book_entries

_OMNI_ROOT = Path(__file__).resolve().parents[5]

# 清场纪律墓碑清单:这些路径已按合并清单拆除,若复活(重新出现)即"清场未完成"。
# 每项带出处(合并清单编号),新增拆除时在此登记。
RUIN_PATHS: tuple[tuple[str, str], ...] = (
    # 合并清单#1: 假设收编停机迁移(2026-07-10)
    ("src/omnicompany/packages/services/_learning/hypothesis/store.py", "#1 V1 HypothesisStore 死代码"),
    ("src/omnicompany/packages/services/_learning/hypothesis/validator.py", "#1 khyp validator"),
    ("src/omnicompany/packages/services/_learning/hypothesis/reflector_daemon.py", "#1 lockstep 双脑"),
    ("src/omnicompany/packages/services/_learning/hypothesis/pipeline.py", "#1 弃用垫片"),
    ("src/omnicompany/packages/services/_learning/knowledge/graph.py", "#1 khyp 孤儿图遍历"),
    ("scripts/smoke_lockstep.py", "#1 lockstep 冒烟"),
    ("data/services/hypothesis", "#1 V1 遗留数据(已归档 data/_archive/hypothesis_v1_sessions_20260710)"),
    ("data/knowledge/hypotheses", "#1 khyp 主题文档目录(收编为 30-知识 投影)"),
    # 前端清点: 一套图壳=review-canvas,第二套图删除(2026-07-10)
    ("src/omnicompany/dashboard/frontend/src/entities/material-graph",
     "前端清点 裸DAG图组件(DEC-2026-07-04-240 已裁死;浏览姿态并入 review-canvas)"),
    # 合并清单#5: distilled 现状长文(无生成器孤儿快照,已归档 data/_archive/decisions_distilled_20260619;
    # 工作规范与流程 的收编走候选流水线 mat_ca0d74e9c5f94148)
    ("data/domains/decisions/distilled",
     "#5 现状长文快照(集中浏览需求已由 30-知识/浏览姿态/阅读视图承接)"),
)


def _registry_entries() -> list:
    """加载管线注册表一次(discover 幂等但重复调会刷 overwrite 警告)。"""
    from omnicompany.core import registry as _registry

    if not _registry.names():
        _registry.discover()
    return _registry.list_all()


def check_bidirectional_refs() -> dict[str, list[dict[str, Any]]]:
    """A-F:手册↔库↔管线注册表 双向引用完整性。"""
    issues: dict[str, list[dict[str, Any]]] = {
        "book_entry_missing_record": [],
        "dangling_book_ref": [],
        "pipeline_dangling_book_ref": [],
        "projection_to_ghost_pipeline": [],
        "missing_projection_backfill": [],
        "dangling_links": [],
    }

    entries = all_book_entries()
    anchors = {e["anchor"] for _, e in entries}
    recs = library.active_records()
    rec_ids = {r["id"] for r in recs if r.get("id")}
    by_alias: dict[str, dict] = {}
    for r in recs:
        for a in r.get("aliases") or []:
            by_alias.setdefault(a, r)

    # A: 手册条目 → 库指针
    for _, e in entries:
        if e["alias"] not in by_alias:
            issues["book_entry_missing_record"].append(
                {"anchor": e["anchor"], "fix": "python scripts/sync_ontology_book.py"})

    # B: 库 book_ref → 手册锚点
    for r in recs:
        if r.get("distilled") and (r.get("book_ref") or "").strip():
            if r["book_ref"] not in anchors:
                issues["dangling_book_ref"].append(
                    {"id": r["id"], "book_ref": r["book_ref"]})

    # C/D/E: 管线注册表两向
    pipeline_names: set[str] = set()
    try:
        entries_reg = _registry_entries()
        pipeline_names = {en.name for en in entries_reg}
        for en in entries_reg:
            for ref in getattr(en, "book_refs", ()) or ():
                if ref not in anchors:
                    issues["pipeline_dangling_book_ref"].append(
                        {"pipeline": en.name, "book_ref": ref})
                else:
                    alias = next((e["alias"] for _, e in entries if e["anchor"] == ref), None)
                    rec = by_alias.get(alias) if alias else None
                    if rec is not None:
                        projs = rec.get("projections") or []
                        if not any(p.get("type") == "pipeline" and p.get("ref") == en.name
                                   for p in projs):
                            issues["missing_projection_backfill"].append(
                                {"pipeline": en.name, "record": rec.get("id"),
                                 "fix": "python scripts/sync_ontology_book.py"})
    except Exception as exc:  # 注册表加载失败:如实报,不吞
        issues["pipeline_dangling_book_ref"].append({"error": f"注册表加载失败: {exc}"})

    # D: 记录 projections → 管线存在
    if pipeline_names:
        for r in recs:
            for p in r.get("projections") or []:
                if p.get("type") == "pipeline" and p.get("ref") not in pipeline_names:
                    issues["projection_to_ghost_pipeline"].append(
                        {"id": r.get("id"), "pipeline_ref": p.get("ref")})

    # F: 悬空边(enforced_by 除外——其值是执法载体标识非记录 id)
    for r in recs:
        links = r.get("links") or {}
        targets: list[str] = []
        for rel in ("rests_on", "supersedes", "related"):
            targets += list(links.get(rel) or [])
        if links.get("parent"):
            targets.append(links["parent"])
        for t in targets:
            if t not in rec_ids:
                issues["dangling_links"].append({"id": r.get("id"), "target": t})

    return issues


def check_ruins(root: Path | None = None) -> list[dict[str, str]]:
    """G:清场废墟检测——应拆除路径若仍存在,报"清场未完成"。"""
    base = root or _OMNI_ROOT
    out: list[dict[str, str]] = []
    for rel, why in RUIN_PATHS:
        p = base / rel
        if p.exists():
            out.append({"path": rel, "why": why})
    return out


def check_interface_sources(extra_roots: list[Path] | None = None) -> list[dict[str, str]]:
    """I:接口文件 Source 注释悬空扫描。

    扫 .claude/ 与用户 skills 根下的 `Source: docs/ontology/...#锚` 注释,
    锚点必须真实存在于手册(接口=投影,指针悬空即漂移)。
    """
    import re as _re

    anchors = all_book_anchors()
    roots = [
        _OMNI_ROOT / ".claude",
        Path.home() / ".claude" / "skills",
    ] + list(extra_roots or [])
    pat = _re.compile(r"Source:\s*(docs/ontology/[^\s>]+#[^\s>]+?)\s*(?:-->|$)", _re.M)
    out: list[dict[str, str]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for p in root.rglob("*.md"):
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
                continue
            for m in pat.finditer(text):
                ref = m.group(1).strip()
                if ref not in anchors:
                    out.append({"file": str(p), "source_ref": ref})
    return out


def check_stale_projection() -> list[dict[str, str]]:
    """J:生成投影重放比对(漂移检查第一版=重放生成+文件比对)。

    30-知识 重渲到内存与盘上现文件比对(剔除生成时间戳行);不一致=投影过期。
    """
    import tempfile

    from .knowledge_projection import KNOWLEDGE_PROJECTION_PATH, render_knowledge_projection

    if not KNOWLEDGE_PROJECTION_PATH.is_file():
        return [{"projection": str(KNOWLEDGE_PROJECTION_PATH),
                 "problem": "投影文件不存在", "fix": "omni decisions knowledge"}]

    def _normalize(text: str) -> str:
        return "\n".join(l for l in text.splitlines() if "生成于" not in l)

    with tempfile.TemporaryDirectory() as td:
        fresh = render_knowledge_projection(Path(td) / "fresh.md").read_text(encoding="utf-8")
    on_disk = KNOWLEDGE_PROJECTION_PATH.read_text(encoding="utf-8")
    if _normalize(fresh) != _normalize(on_disk):
        return [{"projection": str(KNOWLEDGE_PROJECTION_PATH),
                 "problem": "投影与库当前态不一致(过期)", "fix": "omni decisions knowledge"}]
    return []


def check_when_coverage() -> dict[str, Any]:
    """H:管线 when/scale 元数据覆盖(advisory)。"""
    try:
        entries = _registry_entries()
    except Exception as exc:
        return {"error": str(exc)}
    missing_when = [e.name for e in entries if not getattr(e, "when", None)]
    missing_scale = [e.name for e in entries if not getattr(e, "scale", None)]
    return {
        "total": len(entries),
        "with_when": len(entries) - len(missing_when),
        "with_scale": len(entries) - len(missing_scale),
        "missing_when": sorted(missing_when),
    }


def run_patrol() -> dict[str, Any]:
    """全量巡检。返回 {ok, issues, ruins, when_coverage};ok=False 当且仅当 A-G/I/J 有违规。"""
    issues = check_bidirectional_refs()
    issues["dangling_interface_source"] = check_interface_sources()
    issues["stale_projection"] = check_stale_projection()
    ruins = check_ruins()
    coverage = check_when_coverage()
    n_bad = sum(len(v) for v in issues.values()) + len(ruins)
    return {
        "ok": n_bad == 0,
        "violation_count": n_bad,
        "issues": issues,
        "ruins": ruins,
        "when_coverage": coverage,
    }
