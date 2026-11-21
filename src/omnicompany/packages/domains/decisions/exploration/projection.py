# [OMNI] origin=ai-ide domain=decisions ts=2026-06-27T00:00:00Z type=module status=active
# [OMNI] summary="投影器:把统一决策库 + 因果边 sidecar(+ 物料注册表)投影成 material-centric 探索 DAG。节点主键=真本体 id,边=真实 links/decision_space/因果边,散落根=入度0,版本链由 supersedes 串。"
# [OMNI] why="图=真本体的投影,不另建手维护数据集。权威=plan B1。"
# [OMNI] tags=decisions,exploration,projection,decision-tree,graph
"""探索路径投影器。

build_graph() 读真本体 → 出 {nodes, edges, roots, kinds, stats, source_token}:
  - 节点 = 决策库每个 id 的最新行(含墓碑,status=deleted 标记不丢);主键直接用真 id。
  - 边(规整成 因→果 方向):
      rests_on   : 信念 → 决策(决策立足于信念)
      supersedes : 旧 → 新(版本链)
      parent     : 父 → 子
      related    : 无向(去重)
      rejected   : 决策 → 否决项(死分支,decision_space 里 chosen=false)
      因果边      : refines/critiques/responds_to_critique(来自 sidecar,带 rationale)
  - 散落根 = 入度 0 的节点(多起点,主干自然涌现,不预设)。
"""

from __future__ import annotations

import json

from .. import library
from .._paths import RECORDS_PATH
from . import backfill, version
from ._paths import BACKFILL_LEDGER_PATH, CAUSAL_EDGES_PATH

# 决策库 kind → 图节点 kind(库只有三类;产物/设施/真源/理念/工作类由 registry/anchor 回填后注入)
_KIND_MAP = {"decision": "决策", "belief": "信念", "comment": "指正"}

# 边关系(规整为 因→果 流向)
REL_RESTS_ON = "rests_on"
REL_SUPERSEDES = "supersedes"
REL_PARENT = "parent"
REL_RELATED = "related"
REL_REJECTED = "rejected"
REL_ENFORCED_BY = "enforced_by"   # 裁决 → 执法器(载体标识节点,非记录)
_CAUSAL_RELS = ("refines", "critiques", "responds_to_critique")


def _short(text: str, n: int = 48) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


def read_causal_edges() -> list[dict]:
    """读因果边 sidecar(refines/critiques/responds_to_critique + rationale)。"""
    if not CAUSAL_EDGES_PATH.is_file():
        return []
    out: list[dict] = []
    for line in CAUSAL_EDGES_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("src") and e.get("dst") and e.get("rel") in _CAUSAL_RELS:
            out.append(e)
    return out


def source_token() -> str:
    """缓存失效 token:决策库 + 因果边 sidecar + 回填台账 的 mtime/size(三个真源都纳入,R1 #3)。"""
    parts = []
    for p in (RECORDS_PATH, CAUSAL_EDGES_PATH, BACKFILL_LEDGER_PATH):
        if p.is_file():
            st = p.stat()
            parts.append(f"{p.name}:{int(st.st_mtime)}:{st.st_size}")
        else:
            parts.append(f"{p.name}:0:0")
    return "|".join(parts)


def _record_node(rec: dict) -> dict:
    rid = rec["id"]
    rkind = rec.get("kind")
    anchor = rec.get("anchor") or {}
    origin = rec.get("origin") or {}
    node = {
        "id": rid,
        "kind": _KIND_MAP.get(rkind, rkind or "未知"),
        "record_kind": rkind,
        "label": _short(rec.get("statement") or rid),
        "statement": rec.get("statement") or "",
        "status": rec.get("status") or "",
        "project": rec.get("project") or "",
        "deleted": rec.get("status") == "deleted",
        "anchor": {k: anchor.get(k) for k in ("kind", "ref", "excerpt") if anchor.get(k)},
        "has_excerpt": bool(anchor.get("excerpt")),
        "session_ref": origin.get("session_ref") or "",
        "is_root": False,
    }
    if rec.get("distilled"):
        node["distilled"] = True
        node["book_ref"] = rec.get("book_ref") or ""
    if rkind == "decision" and rec.get("decision_space"):
        node["decision_space"] = rec["decision_space"]
    if rkind == "belief":
        node["verification_status"] = rec.get("verification_status") or rec.get("status") or ""
    # 不给决策/信念/指正设 version_name —— 版本只属耐用物 material(R1 #1 铁律):
    # 否则 anchor.ref 里 reports/q1/v2/foo.md 的 /v2/ 会被误当版本号,凭空造假版本链。
    return node


def build_graph(project: str | None = None, kinds: list[str] | None = None,
                include_deleted: bool = True, statuses: list[str] | None = None) -> dict:
    """投影出探索 DAG。project=按项目过滤;kinds=按 record_kind 过滤;include_deleted=是否保留墓碑;
    statuses=按记录 status 过滤(如 ["adopted"] 只看已拍板裁决;墓碑仍由 include_deleted 管)。"""
    folded = library.fold()
    nodes: list[dict] = []
    node_ids: set[str] = set()

    for rid, rec in folded.items():
        if project is not None:
            rec_proj = rec.get("project") or ""
            if project == "(未归位)":          # 哨兵桶:与 /projects 端点对无 project 记录的桶名一致
                if rec_proj:
                    continue
            elif rec_proj != project:
                continue
        if kinds and rec.get("kind") not in kinds:
            continue
        if statuses and (rec.get("status") or "") not in statuses:
            continue
        if not include_deleted and rec.get("status") == "deleted":
            continue
        nodes.append(_record_node(rec))
        node_ids.add(rid)

    edges: list[dict] = []
    rejected_nodes: list[dict] = []
    enforcer_nodes: dict[str, dict] = {}
    seen_related: set[tuple[str, str]] = set()

    # 用 folded 拿 links/decision_space(只对已纳入节点的记录连边)
    for rid in node_ids:
        rec = folded[rid]
        links = rec.get("links") or {}
        for b in links.get("rests_on") or []:
            if b in node_ids:
                edges.append({"source": b, "target": rid, "rel": REL_RESTS_ON,
                              "note": "决策立足于此信念"})
        for old in links.get("supersedes") or []:
            if old in node_ids:
                edges.append({"source": old, "target": rid, "rel": REL_SUPERSEDES,
                              "note": "被新决策取代(版本链)"})
        parent = links.get("parent")
        if parent and parent in node_ids:
            edges.append({"source": parent, "target": rid, "rel": REL_PARENT, "note": "父决策"})
        for other in links.get("related") or []:
            if other in node_ids:
                key = tuple(sorted((rid, other)))
                if key not in seen_related:
                    seen_related.add(key)
                    edges.append({"source": rid, "target": other, "rel": REL_RELATED, "note": "相关"})
        # 执法边:裁决 → 执法器(载体标识节点;值非记录 id,如 demogame.design_doc_lint.check7_self_reference)
        for enforcer in links.get("enforced_by") or []:
            enforcer = str(enforcer).strip()
            if not enforcer:
                continue
            nid = f"enforcer:{enforcer}"
            enforcer_nodes.setdefault(nid, {
                "id": nid, "kind": "执法器", "record_kind": "enforcer",
                "label": _short(enforcer.rsplit(".", 1)[-1]),
                "statement": enforcer,
                "status": "", "project": rec.get("project") or "", "deleted": False,
                "is_root": False,
            })
            edges.append({"source": rid, "target": nid, "rel": REL_ENFORCED_BY,
                          "note": "裁决编译进此执法器"})
        # 否决项:decision_space 里 chosen=false 的 option → 死分支节点
        if rec.get("kind") == "decision":
            for i, opt in enumerate(rec.get("decision_space") or []):
                if isinstance(opt, dict) and opt.get("chosen") is False:
                    nid = f"{rid}#reject:{i}"
                    why = (opt.get("why") or "").strip()
                    rejected_nodes.append({
                        "id": nid, "kind": "否决", "record_kind": "rejected",
                        "label": _short(opt.get("option") or "被否决项"),
                        "statement": (opt.get("option") or "") + (f" —— {why}" if why else ""),
                        "status": "", "project": rec.get("project") or "", "deleted": False,
                        "of_decision": rid, "is_root": False,
                    })
                    edges.append({"source": rid, "target": nid, "rel": REL_REJECTED,
                                  "note": "决策里被否决的备选(死分支)"})

    nodes.extend(rejected_nodes)
    node_ids |= {n["id"] for n in rejected_nodes}
    nodes.extend(enforcer_nodes.values())
    node_ids |= set(enforcer_nodes)

    # 回填台账:产物/设施/真源 物料节点(注入图,连回所属决策,版本链 supersedes)
    # 必须在因果边之前——因果边可能指向物料节点(如 决策 --refines--> 产物)。
    mat_nodes, mat_edges = _ledger_nodes_edges(node_ids, project)
    nodes.extend(mat_nodes)
    node_ids |= {n["id"] for n in mat_nodes}
    edges.extend(mat_edges)

    # 因果边 sidecar(只连两端都在的;此时决策+否决+物料节点都已就位)
    for e in read_causal_edges():
        if e["src"] in node_ids and e["dst"] in node_ids:
            edges.append({"source": e["src"], "target": e["dst"], "rel": e["rel"],
                          "rationale": e.get("rationale") or "",
                          "rationale_verified": e.get("rationale_verified"),
                          "note": "因果边(抽取)"})

    # 版本号/族 + 版本链
    version.annotate_versions(nodes)
    chains = version.build_version_chains(nodes, edges)

    # 散落根 = 入度 0
    indeg: dict[str, int] = {n["id"]: 0 for n in nodes}
    for e in edges:
        if e["target"] in indeg:
            indeg[e["target"]] += 1
    roots = [nid for nid, d in indeg.items() if d == 0]
    root_set = set(roots)
    for n in nodes:
        n["is_root"] = n["id"] in root_set

    stats = {
        "n_nodes": len(nodes),
        "n_edges": len(edges),
        "n_roots": len(roots),
        "n_deleted": sum(1 for n in nodes if n.get("deleted")),
        "n_rejected": len(rejected_nodes),
        "n_version_chains": len(chains),
        "by_kind": _count(nodes, "kind"),
        "by_status": _count(nodes, "status"),
        "by_rel": _count(edges, "rel"),
    }
    return {
        "nodes": nodes,
        "edges": edges,
        "roots": roots,
        "version_chains": chains,
        "kinds": sorted({n["kind"] for n in nodes}),
        "project": project,
        "stats": stats,
        "source_token": source_token(),
    }


def _ledger_nodes_edges(decision_ids: set[str], project: str | None) -> tuple[list[dict], list[dict]]:
    """从回填台账注入 产物/设施/真源 物料节点 + 边(产出/依据 方向感知,supersedes 版本链)。

    只注入那些『所属决策已在图里 或 项目匹配过滤』的物料,保证连得上、不悬空。
    """
    # 过滤掉缺 key/material_id 的残缺台账行,避免硬下标 KeyError → 端点 500(R1 #13a)
    ledger = [r for r in backfill.read_ledger() if r.get("key") and r.get("material_id")]
    if not ledger:
        return [], []
    key_to_mid = {row["key"]: row["material_id"] for row in ledger}
    nodes: list[dict] = []
    edges: list[dict] = []
    seen: set[str] = set()
    for row in ledger:
        if project is not None and row.get("project") != project:
            continue
        link_to = row.get("link_to")
        # 连得上(决策在图里)才注入,避免孤立物料;无项目过滤时只要决策在库即可
        if link_to not in decision_ids:
            continue
        mid = row["material_id"]
        if mid in seen:
            continue
        seen.add(mid)
        is_gap = row.get("status") in ("lost", "unlocated")
        node = {
            "id": mid, "kind": row.get("kind") or "产物", "record_kind": "material",
            "label": _short(row.get("label") or mid), "statement": row.get("summary") or "",
            "status": row.get("status") or "", "project": row.get("project") or "",
            "deleted": is_gap, "is_gap": is_gap, "is_root": False,
            "material_id": mid, "source_file": row.get("path") or "",
            # 受控版本名 = 文件名(供 annotate_versions 从命名约定推断,不依赖人写 label;R2)
            "version_name": (row.get("path") or "").replace("\\", "/").rstrip("/").split("/")[-1],
        }
        if row.get("version") is not None:
            node["version"] = row["version"]
            node["version_family"] = row.get("version_family") or ""
        nodes.append(node)
        rel = row.get("rel") or "产出"
        # 方向感知:产出=决策→物料;依据/约束=物料→决策(物料是输入/基础)
        if rel in ("依据", "约束"):
            edges.append({"source": mid, "target": link_to, "rel": rel, "note": "物料作为决策的输入/基础"})
        else:
            edges.append({"source": link_to, "target": mid, "rel": rel, "note": "决策产出此物料"})
    # supersedes 版本链(老 material → 新 material)
    for row in ledger:
        if project is not None and row.get("project") != project:
            continue
        sup = row.get("supersedes")
        if sup and row["material_id"] in seen:
            old_mid = key_to_mid.get(sup)
            if old_mid in seen:
                edges.append({"source": old_mid, "target": row["material_id"], "rel": "supersedes",
                              "note": "物料版本链(回填声明)"})
    return nodes, edges


def _count(items: list[dict], field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for it in items:
        k = it.get(field) or "(none)"
        out[k] = out.get(k, 0) + 1
    return out


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    try:  # Windows 控制台默认 GBK,中文/符号输出走 utf-8
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="投影探索 DAG(决策库→material-centric 图)")
    ap.add_argument("--project", default=None, help="按项目过滤(如 aigc)")
    ap.add_argument("--kind", action="append", dest="kinds", default=None,
                    help="按 record_kind 过滤(decision/belief/comment),可多次")
    ap.add_argument("--no-deleted", action="store_true", help="不含墓碑节点")
    ap.add_argument("--full", action="store_true", help="打印完整 JSON(默认只打 stats)")
    args = ap.parse_args(argv)
    g = build_graph(project=args.project, kinds=args.kinds, include_deleted=not args.no_deleted)
    if args.full:
        print(json.dumps(g, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(g["stats"], ensure_ascii=False, indent=2))
        print(f"roots={len(g['roots'])} version_chains={len(g['version_chains'])} "
              f"source_token={g['source_token']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
