# [OMNI] origin=ai-ide domain=decisions ts=2026-06-27T00:00:00Z type=runner status=active
# [OMNI] summary="探索历程提炼:独立 agent(gpt-5.5)读真对话提炼一条连续、有意义的操作流——产品在各节点如何变、用户如何检查出偏移、越来越细的决策、走向目标;按主题聚成泳道。缓存,A 单领域/B 时期。"
# [OMNI] why="决策树不是决策库散点投影,而是 agent 从对话提炼的连续探索叙事(对齐手写 v2)。泳道=某类产物的某条思路。权威=plan B5/B6 放大版。"
# [OMNI] tags=decisions,exploration,narrative,swimlane,gpt-5.5
"""探索历程提炼器 —— 从真对话提炼连续操作流 + 主题泳道。

A 单领域:给 project,只提炼该领域相关的探索演化链(忽略其他主题)。
B 时期全景:给一段时期,提炼跨所有主题的泳道全景。
LLM 走 omni 网关 gpt-5.5(可换)。结果按 scope 缓存,不每次重跑。
"""

from __future__ import annotations

import json

from .. import library
from .._paths import _OMNI_ROOT
from ..sources import conversation as cv
from . import causal_extract
from . import projection
from ._paths import EXPLORATION_ROOT, ensure_dirs

DEFAULT_MODEL = "gpt-5.5"
NARRATIVE_DIR = EXPLORATION_ROOT / "narratives"
SCOPE_CONFIG = _OMNI_ROOT / "config" / "decisions" / "narrative_scopes.yaml"

# 项目注册表 id → 决策库 project 字段(单一真源映射;前端只传注册表 id,后端归一)。
# 两套分类对不齐:aigc-image≠aigc、业务域一对多、omni-* 归 omnicompany。
REGISTRY_TO_DECISIONS = {
    "aigc-image": "aigc", "aigc-music": "aigc",
    "example-config": "example_domain", "example-unity": "example_domain", "example-prefab": "example_domain", "example-demo": "example_domain",
    "omnidashboard": "omnicompany", "omni-guard": "omnicompany", "omni-productization": "omnicompany",
    "omni-sw-engineering": "omnicompany", "omni-learning": "omnicompany",
    "omni-teambuilder-doctor": "omnicompany", "omni-remote": "omnicompany", "decisions": "omnicompany",
    "personal-site": "web-company", "publish-backup": "web-company",
}


def canon_project(project: str | None) -> str | None:
    """注册表 id → 决策库 project;同名/未知原样返回(vilo/walker 等)。"""
    return REGISTRY_TO_DECISIONS.get(project, project) if project else project


def to_registry_id(decisions_project: str | None) -> str | None:
    """决策库 project → 一个注册表 id(反查,一对多取第一个;供前端 deep-link 打开项目)。"""
    if not decisions_project:
        return None
    for reg, dec in REGISTRY_TO_DECISIONS.items():
        if dec == decisions_project:
            return reg
    return decisions_project   # 同名(vilo/walker)

_SYS = (
    "你是探索历程提炼师。读真实的人机协作对话,提炼出一条【连续、有意义的探索历程】——"
    "产品/材料在各个节点如何变化、用户如何检查出偏移、如何越来越细地决策、最终走向要去的方向。"
    "不是罗列孤立决策,而是一条连贯的操作流叙事(像复盘『我们怎么一步步把这东西做对的』)。"
    "把事件按【主题】聚成泳道:某一类产物的某一条思路/某个方面各成一条道(如『审阅台』『候选图风格』『目录归位』)。"
    "每个事件标:属哪条泳道、产品在此如何变(product_change)、用户做了什么(user_action:抛理念/检查发现偏移/指正/拍决策)、"
    "是否纠偏(is_correction)、原文证据(evidence,引对话原文不改写)。宁缺毋滥,只记真实发生且有意义的。"
)

SCHEMA = {
    "type": "object",
    "properties": {
        "lanes": {"type": "array", "items": {"type": "object", "additionalProperties": True, "properties": {
            "id": {"type": "string"}, "theme": {"type": "string"}, "summary": {"type": "string"}},
            "required": ["id", "theme"]}},
        "events": {"type": "array", "items": {"type": "object", "additionalProperties": True, "properties": {
            "seq": {"type": "integer"}, "lane": {"type": "string"},
            "kind": {"type": "string", "enum": ["理念", "调研", "产物", "检查", "指正", "决策", "转向"]},
            "title": {"type": "string"}, "product_change": {"type": "string"},
            "user_action": {"type": "string"}, "is_correction": {"type": "boolean"},
            "evidence": {"type": "string"}},
            "required": ["seq", "lane", "kind", "title"]}},
        "narrative": {"type": "string"},
    },
    "required": ["lanes", "events"],
}

# ── library-first(mode=project):以决策库投影骨架为锚,LLM 只排序+补叙事,不抽事实 ──
# 事件 kind 直接用决策库语义(不让 LLM 猜),前端按这些着色。
_SKELETON_KINDS = ("理念", "决策", "信念", "指正", "调研", "产物", "设施", "真源")

_SYS_ORDER = (
    "你是探索历程编排师。下面给你【已确认的真实节点】(来自决策库,每个带 id、类型、陈述、否决项、版本、原文引文)"
    "和【定位到的对话原文窗口】。这些节点就是骨架,事实已定 —— 你做两件事:\n"
    "1. 把节点排成一条【连续的探索历程】先后顺序(早→晚),并按【主题】分到泳道"
    "(某一类产物的某一条思路各成一道,如『审阅台』『候选图风格』『目录归位』),给每道 id 和 theme;\n"
    "2. 给每个节点一句 summary,说清【这一步到底发生了什么】:在什么情境下、谁(用户/AI)做了什么、"
    "针对哪个决策或产物、为什么会走到这一步。要一眼能看懂、相对完整,用顺畅的一句话讲清(约 25-45 字);"
    "不要压字数压成干巴主旨(那样更看不懂),也不要照抄冗长揉杂的原句。\n"
    "铁律:① 不得新增骨架之外的 node_id;② 不得漏掉任何骨架节点;"
    "③ 否决项/版本/陈述是事实,summary 据原文如实讲清,不要编造或张冠李戴。"
)

ORDER_SCHEMA = {
    "type": "object",
    "properties": {
        "lanes": {"type": "array", "items": {"type": "object", "additionalProperties": True, "properties": {
            "id": {"type": "string"}, "theme": {"type": "string"}, "summary": {"type": "string"}},
            "required": ["id", "theme"]}},
        "ordering": {"type": "array", "items": {"type": "object", "additionalProperties": True, "properties": {
            "node_id": {"type": "string"}, "lane": {"type": "string"}, "summary": {"type": "string"}},
            "required": ["node_id", "lane"]}},
    },
    "required": ["ordering"],
}


def _session_refs_for_project(project: str) -> list[str]:
    refs: list[str] = []
    for r in library.fold().values():
        if r.get("project") == project and r.get("status") != "deleted":
            sr = (r.get("origin") or {}).get("session_ref")
            if sr and sr not in refs:
                refs.append(sr)
    return refs


def _recent_session_ids(max_sessions: int) -> list[str]:
    """B 时期:取最近改动的 N 个 claude 会话(按文件 mtime)。"""
    sess = cv.scan_claude_sessions()
    def mtime(s: dict) -> float:
        try:
            from pathlib import Path
            return Path(s["path"]).stat().st_mtime
        except Exception:
            return float(s.get("mtime", 0) or 0)
    sess = sorted(sess, key=mtime, reverse=True)
    return [s["session_id"] for s in sess[:max_sessions]]


def _build_corpus(refs: list[str], per_session: int, max_sessions: int) -> tuple[str, list[str]]:
    pm = causal_extract._session_path_map()
    texts: list[str] = []
    used: list[str] = []
    for sr in refs[:max_sessions]:
        path, sid = causal_extract._resolve_path(sr, pm)
        if not path:
            continue
        t = cv.condense_text(path)
        if t.strip():
            texts.append(f"### session {sid or sr}\n" + t[:per_session])
            used.append(sid or sr)
    return "\n\n".join(texts)[:62000], used


def _skeleton(project: str) -> list[dict]:
    """library-first:以 projection 投影(决策/信念/指正/物料 + 否决 + 版本链)为骨架,事实真本体。"""
    g = projection.build_graph(project=project, include_deleted=True)
    records = library.fold()
    rejects: dict[str, list[str]] = {}
    for n in g["nodes"]:
        if n.get("record_kind") == "rejected":
            rejects.setdefault(n.get("of_decision"), []).append(n.get("statement") or "")
    skel: list[dict] = []
    for n in g["nodes"]:
        if n.get("record_kind") == "rejected":
            continue   # 否决项不单列为事件,挂到其决策的 rejected 字段
        if n.get("status") == "superseded":
            continue   # 去重合并掉的旧版(同一决策多次落定),已折叠到保留版,不在历程重复显示
        is_gap = bool(n.get("is_gap"))   # 物料缺口:台账登记但磁盘未定位/已丢失 —— 只有这种才该灰显
        skel.append({
            "id": n["id"], "kind": n.get("kind") or "", "record_kind": n.get("record_kind"),
            "project": n.get("project") or project,
            "statement": n.get("statement") or n.get("label") or "",
            "status": n.get("status") or "",
            "gap": is_gap, "gap_reason": (n.get("status") or "") if is_gap else "",
            "verification_status": n.get("verification_status") or "",
            "session_ref": n.get("session_ref") or "",
            "excerpt": (n.get("anchor") or {}).get("excerpt") or "",
            "rejected": rejects.get(n["id"], []),
            "version": n.get("version"), "version_family": n.get("version_family"),
            "is_root": bool(n.get("is_root")),
            "created_at": (records.get(n["id"]) or {}).get("created_at") or "",
            "updated_at": (records.get(n["id"]) or {}).get("updated_at") or "",
        })
    # 真实结构边(汇入/分出/跨主题交互):产出/依据/立足/版本链/因果精化 —— 去掉否决边(已并入 rejected 字段)
    skel_ids = {s["id"] for s in skel}
    edges = [{"source": e["source"], "target": e["target"], "rel": e["rel"], "note": e.get("note", "")}
             for e in g["edges"]
             if e.get("rel") != "rejected" and e["source"] in skel_ids and e["target"] in skel_ids]
    return skel, edges


def _scope_definition(scope: str) -> dict:
    """读取命名叙事作用域。作用域只选记录,不复制事实。"""
    try:
        import yaml
        raw = yaml.safe_load(SCOPE_CONFIG.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError) as exc:
        raise ValueError(f"无法读取叙事作用域配置: {SCOPE_CONFIG}: {exc}") from exc
    definition = (raw.get("scopes") or {}).get(scope)
    if not isinstance(definition, dict):
        known = ", ".join(sorted((raw.get("scopes") or {}).keys())) or "无"
        raise ValueError(f"未知叙事作用域 {scope!r}; 已注册: {known}")
    return definition


def _scope_record_ids(definition: dict) -> set[str]:
    projects = {str(x) for x in definition.get("projects") or []}
    anchor_terms = [str(x).lower().replace("\\", "/") for x in definition.get("anchor_contains") or []]
    wanted_tags = {str(x).lower() for x in definition.get("tags") or []}
    selected: set[str] = set()
    for rid, record in library.fold().items():
        anchor = str((record.get("anchor") or {}).get("ref") or "").lower().replace("\\", "/")
        tags = {str(x).lower() for x in record.get("tags") or []}
        if (record.get("project") in projects
                or any(term in anchor for term in anchor_terms)
                or bool(tags & wanted_tags)):
            selected.add(rid)
    return selected


def _skeleton_scope(scope: str) -> tuple[list[dict], list[dict], dict]:
    """从统一图投影中筛出命名作用域,保留同一节点/边契约。"""
    definition = _scope_definition(scope)
    selected = _scope_record_ids(definition)
    records = library.fold()
    graph = projection.build_graph(project=None, include_deleted=True)
    rejects: dict[str, list[str]] = {}
    for node in graph["nodes"]:
        if node.get("record_kind") == "rejected" and node.get("of_decision") in selected:
            rejects.setdefault(node.get("of_decision"), []).append(node.get("statement") or "")

    skeleton: list[dict] = []
    for node in graph["nodes"]:
        if node.get("record_kind") == "rejected" or node.get("id") not in selected:
            continue
        if node.get("status") == "superseded":
            continue
        is_gap = bool(node.get("is_gap"))
        skeleton.append({
            "id": node["id"], "kind": node.get("kind") or "", "record_kind": node.get("record_kind"),
            "project": node.get("project") or (records.get(node["id"]) or {}).get("project") or "unassigned",
            "statement": node.get("statement") or node.get("label") or "",
            "status": node.get("status") or "", "gap": is_gap,
            "gap_reason": (node.get("status") or "") if is_gap else "",
            "verification_status": node.get("verification_status") or "",
            "session_ref": node.get("session_ref") or "",
            "excerpt": (node.get("anchor") or {}).get("excerpt") or "",
            "rejected": rejects.get(node["id"], []),
            "version": node.get("version"), "version_family": node.get("version_family"),
            "is_root": bool(node.get("is_root")),
            "created_at": (records.get(node["id"]) or {}).get("created_at") or "",
            "updated_at": (records.get(node["id"]) or {}).get("updated_at") or "",
        })
    ids = {node["id"] for node in skeleton}
    edges = [{"source": edge["source"], "target": edge["target"], "rel": edge["rel"],
              "note": edge.get("note", "")}
             for edge in graph["edges"]
             if edge.get("rel") != "rejected" and edge["source"] in ids and edge["target"] in ids]
    return skeleton, edges, definition


def _anchor_corpus(skel: list[dict], win: int = 1400, max_total: int = 52000) -> str:
    """有向取材:按每个骨架节点的 excerpt+session_ref 定位原文窗口(替代头部定长截断丢核心)。"""
    pm = causal_extract._session_path_map()
    by_sess: dict[str, list[str]] = {}
    for s in skel:
        if s["excerpt"] and s["session_ref"]:
            by_sess.setdefault(s["session_ref"], []).append(s["excerpt"])
    blocks: list[str] = []
    for sref, excerpts in by_sess.items():
        path, sid = causal_extract._resolve_path(sref, pm)
        if not path:
            continue
        norm = causal_extract._norm(cv.condense_text(path))
        if not norm:
            continue
        spans: list[list[int]] = []
        for ex in excerpts:
            ne = causal_extract._norm(ex)
            probe = ne[:50] if len(ne) >= 50 else ne
            if not probe:
                continue
            pos = norm.find(probe)
            if pos < 0 and len(probe) > 20:
                pos = norm.find(probe[:20])
            if pos >= 0:
                spans.append([max(0, pos - win), min(len(norm), pos + win)])
        if not spans:
            continue
        spans.sort()
        merged: list[list[int]] = []
        for a, b in spans:
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        for a, b in merged:
            blocks.append(f"[session {sid or sref}] …{norm[a:b]}…")
    return "\n\n".join(blocks)[:max_total]


def _brief(text: str, n: int = 16) -> str:
    """机械简短(label 缺省兜底):取首句/首分隔前,截 n 字。"""
    t = (text or "").strip().replace("\n", " ")
    for sep in ("。", "；", ";", "（", "(", "，", ",", "：", ":"):
        i = t.find(sep)
        if 0 < i <= n + 8:
            t = t[:i]; break
    return t if len(t) <= n else t[:n] + "…"


def _event(seq: int, s: dict, o: dict) -> dict:
    """骨架节点 + LLM 一句叙事 → 一个事件;事实字段(陈述/否决/版本/缺口)一律从骨架回填。"""
    kind = s["kind"] if s["kind"] in _SKELETON_KINDS else "产物"
    full = s["statement"] or ""
    return {
        "seq": seq, "lane": o.get("lane") or s.get("version_family") or "主线",
        "project": s.get("project") or "",
        "created_at": s.get("created_at") or "",
        "updated_at": s.get("updated_at") or "",
        "kind": kind,
        "summary": (o.get("summary") or "").strip() or _brief(full, 44),   # 卡片正文:说清发生了什么
        "statement": full,                                                 # 完整陈述进下钻
        "is_correction": s["kind"] == "指正",
        "evidence": s.get("excerpt") or "",
        "rejected": s.get("rejected") or [],
        "version": s.get("version"), "version_family": s.get("version_family"),
        "gap": s.get("gap", False), "gap_reason": s.get("gap_reason", ""),
        "is_root": s.get("is_root", False), "node_id": s["id"],
    }


def _deterministic_order(skeleton: list[dict]) -> dict:
    """零模型投影:真实时间排序 + project 泳道;完整陈述不做摘要压缩。"""
    ordered = sorted(
        skeleton,
        key=lambda node: (node.get("created_at") or node.get("updated_at") or "", node.get("id") or ""),
    )
    projects = []
    for node in ordered:
        project = node.get("project") or "unassigned"
        if project not in projects:
            projects.append(project)
    lanes = [{"id": f"project:{project}", "theme": project,
              "summary": f"{project} 发布决策与纠正"} for project in projects]
    return {
        "lanes": lanes,
        "ordering": [{"node_id": node["id"], "lane": f"project:{node.get('project') or 'unassigned'}",
                      "summary": node.get("statement") or ""} for node in ordered],
    }


def _assemble(skel: list[dict], res: dict) -> list[dict]:
    """全覆盖校验+回填:丢弃幻觉 id、补回漏排节点,事实钉死在骨架上。"""
    by_id = {s["id"]: s for s in skel}
    used: set[str] = set()
    events: list[dict] = []
    seq = 0
    for o in res.get("ordering") or []:
        nid = o.get("node_id")
        s = by_id.get(nid)
        if not s or nid in used:
            continue   # 幻觉 id / 重复 → 丢
        used.add(nid); seq += 1
        events.append(_event(seq, s, o))
    for s in skel:                # 漏排的骨架节点按原序补回(决不丢决策/物料)
        if s["id"] not in used:
            seq += 1
            events.append(_event(seq, s, {}))
    return events


def _scope_key(mode: str, project: str | None, scope: str | None = None) -> str:
    if mode == "project":
        return f"project_{project}"
    if mode == "scope":
        return f"scope_{scope}"
    return "period_recent"


def cache_path(mode: str, project: str | None = None, scope: str | None = None):
    return NARRATIVE_DIR / f"{_scope_key(mode, project, scope)}.json"


def read_narrative(mode: str = "project", project: str | None = None,
                   scope: str | None = None) -> dict | None:
    p = cache_path(mode, canon_project(project), scope)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    return None


def list_narratives() -> list[dict]:
    """已提炼缓存清单(供前端列可选历程)。"""
    if not NARRATIVE_DIR.is_dir():
        return []
    out = []
    for f in sorted(NARRATIVE_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        meta = d.get("_meta") or {}
        out.append({"key": f.stem, "mode": meta.get("mode"), "project": meta.get("project"),
                    "scope": meta.get("scope"),
                    "registry_id": to_registry_id(meta.get("project")),
                    "lanes": len(d.get("lanes", [])), "events": len(d.get("events", []))})
    return out


def extract_narrative(mode: str = "project", project: str | None = None, *, scope: str | None = None,
                      model: str | None = None, force: bool = False,
                      max_sessions: int = 4, per_session: int = 22000,
                      deterministic: bool = False) -> dict:
    """提炼一条探索历程(带缓存)。mode=project(A,单领域聚焦)/ period(B,时期全景)。"""
    project = canon_project(project)   # 注册表 id → 决策库 project,前后端统一
    cached = read_narrative(mode, project, scope)
    if cached is not None and not force:
        return cached

    from omnicompany.runtime.llm.structured import call_json

    # mode=project:library-first —— 决策库投影骨架为锚 + 有向取材 + LLM 只排序增益 + 全覆盖回填。
    # 这复用 projection.build_graph 已稳定产出的 44 节点骨架(决策/信念/指正/物料/否决/版本链),
    # 不再从原始对话头部定长截断从零重抽(那会丢掉落在对话尾部 85% 处的核心决策段)。
    if mode in ("project", "scope"):
        definition: dict = {}
        if mode == "project":
            if not project:
                raise ValueError("mode=project 需要 project")
            skel, sk_edges = _skeleton(project)
            focus_name = project
        else:
            if not scope:
                raise ValueError("mode=scope 需要 scope")
            skel, sk_edges, definition = _skeleton_scope(scope)
            focus_name = definition.get("display_name") or scope
        if skel:
            corpus = _anchor_corpus(skel)
            listing = [{
                "id": s["id"], "类型": s["kind"], "陈述": s["statement"],
                **({"否决": s["rejected"]} if s["rejected"] else {}),
                **({"版本": s["version"]} if s.get("version") else {}),
                **({"状态": s["status"]} if s["status"] in ("deleted", "untested") else {}),
                "引文": (s["excerpt"] or "")[:160],
            } for s in skel]
            if deterministic:
                res = _deterministic_order(skel)
            else:
                user = ("【已确认的真实节点(骨架:按此排序+分泳道+补叙事,不增不漏)】\n"
                        + json.dumps(listing, ensure_ascii=False, indent=1)
                        + "\n\n【定位到的对话原文窗口(写 product_change 用)】\n"
                        + (corpus or "(无定位窗口,凭陈述写)"))
                res = call_json(system=_SYS_ORDER, user=user, schema=ORDER_SCHEMA,
                                model=model or DEFAULT_MODEL, caller="decisions.narrative",
                                max_tokens=8000) or {"ordering": []}
            out = {"lanes": res.get("lanes", []), "events": _assemble(skel, res),
                   "edges": sk_edges, "narrative": res.get("narrative", ""),
                   "_meta": {"mode": mode, "project": project, "scope": scope,
                             "display_name": focus_name,
                             "model": "deterministic" if deterministic else (model or DEFAULT_MODEL),
                             "skeleton": len(skel), "edges": len(sk_edges),
                             "from": "library-first-deterministic" if deterministic else "library-first"}}
            ensure_dirs(); NARRATIVE_DIR.mkdir(parents=True, exist_ok=True)
            cache_path(mode, project, scope).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
            return out
        if mode == "scope":
            return {"lanes": [], "events": [], "narrative": "", "note": "作用域内没有决策记录",
                    "_meta": {"mode": mode, "scope": scope, "sessions": []}}
        refs = _session_refs_for_project(project)   # 决策库该领域为空 → 退化到原文重抽
        focus = project
    else:
        refs = _recent_session_ids(max_sessions)
        focus = None

    corpus, used = _build_corpus(refs, per_session, max_sessions)
    if not corpus.strip():
        return {"lanes": [], "events": [], "narrative": "", "note": "无可用语料(该领域无对话或会话已不在磁盘)",
                "_meta": {"mode": mode, "project": project, "sessions": []}}

    sys_p = _SYS + (f"\n\n本次【只】提炼与「{focus}」这个领域/项目直接相关的探索,其他主题的内容忽略不记。" if focus else "")
    res = call_json(system=sys_p, user=corpus, schema=SCHEMA, model=model or DEFAULT_MODEL,
                    caller="decisions.narrative", max_tokens=7000) or {"lanes": [], "events": []}
    res["_meta"] = {"mode": mode, "project": project, "sessions": used, "model": model or DEFAULT_MODEL,
                    "from": "corpus-fallback"}

    ensure_dirs()
    NARRATIVE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path(mode, project, scope).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    return res


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        from dotenv import load_dotenv

        from .._paths import DATA_ROOT
        load_dotenv(DATA_ROOT.parents[2] / ".env")   # 仓根 .env(THE_COMPANY_API_KEY)
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="提炼探索历程(连续操作流+主题泳道)")
    ap.add_argument("--mode", choices=["project", "scope", "period"], default="project")
    ap.add_argument("--project", default=None, help="A 模式:领域/项目(如 aigc)")
    ap.add_argument("--scope", default=None, help="命名跨项目作用域(如 outward-publishing)")
    ap.add_argument("--model", default=None)
    ap.add_argument("--max-sessions", type=int, default=4)
    ap.add_argument("--force", action="store_true", help="忽略缓存重抽")
    ap.add_argument("--deterministic", action="store_true", help="零模型:按真实时间和 project 生成可重建快照")
    args = ap.parse_args(argv)
    res = extract_narrative(mode=args.mode, project=args.project, scope=args.scope, model=args.model,
                            force=args.force, max_sessions=args.max_sessions,
                            deterministic=args.deterministic)
    print(json.dumps({"lanes": len(res.get("lanes", [])), "events": len(res.get("events", [])),
                      "themes": [l.get("theme") for l in res.get("lanes", [])],
                      "cache": str(cache_path(args.mode, args.project, args.scope))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
