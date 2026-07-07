# [OMNI] origin=ai-ide domain=decisions ts=2026-06-27T00:00:00Z type=runner status=active
# [OMNI] summary="因果边抽取:独立 agent 从对话散文抽 refines/critiques/responds_to_critique + rationale,连真实节点 id,落 causal_edges sidecar。让散落的决策节点连成探索因果链。"
# [OMNI] why="决策库 links 几乎不存在(3514行仅0.5%),决策树几乎全是孤点。带理由的因果边是 prior art 无人闭合的点(IBIS×ToT)。权威=plan B6。独立抽取不自产自验。"
# [OMNI] tags=decisions,exploration,causal,extraction,rationale
"""因果边抽取器 —— 从真实对话抽 decision↔material 的带理由因果边。

流程:projection 拿候选节点(id+statement)→ 按 session 分组 → 读该 session 对话 →
便宜模型抽『哪个节点 refines/critiques/responds_to_critique 哪个 + rationale(原文)』→
校验 src/dst 是真实节点 id、rel 合法 → 去重 → append 进 causal_edges.jsonl。
投影器把这些边并进图,决策节点不再是孤点。

LLM 走 omni 网关(runtime.llm.structured.call_json,HTTP,EDR 安全)。_llm_call 可被测试替换。
"""

from __future__ import annotations

import json
import re

from . import projection
from ._paths import CAUSAL_EDGES_PATH, ensure_dirs

DEFAULT_MODEL = "gpt-5.5"   # 与 extract_run 一致;--model 可覆盖(qwen3.6-plus 更省)

_RELS = ("refines", "critiques", "responds_to_critique")

_SYS = (
    "你从一段真实人机对话里,找出给定『决策/产物/指正节点』之间的因果边。三种关系(方向 src→dst):"
    "critiques = src 是对 dst(某材料)的审阅/指正;"
    "refines = src 据某指正改进/产出了 dst 的新版本;"
    "responds_to_critique = src(一段工作/决策)回应了 dst 这条指正。"
    "铁律:(1) src/dst 只能从给定 id 列表里选,绝不编造 id;(2) 每条边 rationale 必填,引对话原文(导致这条边的那句指正/理由),不许改写;"
    "(3) 只连对话里真实发生的因果,宁缺毋滥,没有就返回空。"
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "src": {"type": "string"},
                    "dst": {"type": "string"},
                    "rel": {"type": "string", "enum": list(_RELS)},
                    "rationale": {"type": "string"},
                },
                "required": ["src", "dst", "rel", "rationale"],
            },
        }
    },
    "required": ["edges"],
}


def _llm_call(system: str, user: str, model: str | None) -> dict:
    """走 omni LLM 网关抽结构化结果。测试里可 monkeypatch 本函数。"""
    from omnicompany.runtime.llm.structured import call_json

    return call_json(system=system, user=user, schema=_SCHEMA,
                     model=model or DEFAULT_MODEL, caller="decisions.causal_extract",
                     max_tokens=3000) or {"edges": []}


def _session_path_map() -> dict[str, str]:
    from ..sources import conversation as cv

    return {s["session_id"]: s["path"] for s in cv.scan_claude_sessions()}


def _resolve_path(session_ref: str, path_map: dict[str, str]) -> tuple[str | None, str | None]:
    """session_ref(截断 8 位或全 UUID)解析成 (真实文件路径, 真实完整 sid)。

    收紧匹配(R1 #9):只认 sid.startswith(session_ref),恰好命中 1 个才返回;0 个或多个(歧义)
    都返回 (None, None) 宁缺毋滥,避免把对话/边的来源出处记成错误 session。
    """
    if not session_ref:
        return None, None
    if session_ref in path_map:
        return path_map[session_ref], session_ref
    hits = [(sid, p) for sid, p in path_map.items() if sid.startswith(session_ref)]
    if len(hits) == 1:
        return hits[0][1], hits[0][0]
    return None, None


def _norm(text: str) -> str:
    """归一化:去掉 condense 注入的【用户】/【助手】标记 + 所有空白,供 rationale 子串校验。"""
    text = re.sub(r"【[^】]*】", "", text or "")
    return re.sub(r"\s+", "", text)


def _rationale_in_text(rationale: str, norm_full: str) -> bool:
    """rationale(自称对话原文)是否真出现在归一化后的完整 session 文本里(R1 #6,软校验)。"""
    nr = _norm(rationale)
    if not nr:
        return False
    if nr in norm_full:
        return True
    probe = nr[:40] if len(nr) >= 40 else nr   # 取足够长子片段,容 LLM 轻微增删
    return probe in norm_full


def candidate_nodes(project: str) -> list[dict]:
    """该项目可连边的节点:决策/信念/指正(带 session_ref)+ 物料(可作 critique 对象)。"""
    g = projection.build_graph(project=project)
    out = []
    for n in g["nodes"]:
        if n.get("record_kind") in ("decision", "belief", "comment", "material"):
            out.append({"id": n["id"], "kind": n["kind"], "record_kind": n.get("record_kind"),
                        "statement": n.get("statement") or n.get("label") or "",
                        "session_ref": n.get("session_ref") or ""})
    return out


def read_existing() -> set[tuple[str, str, str]]:
    out: set[tuple[str, str, str]] = set()
    for e in projection.read_causal_edges():
        out.add((e["src"], e["dst"], e["rel"]))
    return out


def extract_for_project(project: str, *, model: str | None = None, max_chars: int = 24000,
                        dry_run: bool = False, _path_map: dict | None = None) -> dict:
    """对一个项目跑因果抽取:按 session 分组喂模型,校验后写 sidecar。"""
    from ..sources import conversation as cv

    nodes = candidate_nodes(project)
    if not nodes:
        return {"project": project, "edges_found": 0, "edges_written": 0, "sessions": 0, "note": "无候选节点"}
    valid_ids = {n["id"] for n in nodes}
    materials = [n for n in nodes if n.get("record_kind") == "material"]
    by_session: dict[str, list[dict]] = {}
    for n in nodes:
        if n["session_ref"]:
            by_session.setdefault(n["session_ref"], []).append(n)

    path_map = _path_map if _path_map is not None else _session_path_map()
    existing = read_existing()
    found: list[dict] = []
    sessions_done = 0
    for sref, snodes in by_session.items():
        path, real_sid = _resolve_path(sref, path_map)
        if not path:
            continue
        full_text = cv.condense_text(path)             # 完整文本:rationale 校验基于它(不基于截断版)
        if not full_text.strip():
            continue
        text = full_text
        if len(text) > max_chars:                      # 喂模型取首尾(关键决策常在两端)
            text = text[: max_chars // 2] + "\n…(略)…\n" + text[-max_chars // 2:]
        listing = [{"id": n["id"], "kind": n["kind"], "statement": n["statement"][:80]}
                   for n in snodes + materials]
        user = json.dumps({"可连节点": listing, "对话": text}, ensure_ascii=False)
        res = _llm_call(_SYS, user, model)
        norm_full = _norm(full_text)
        for e in (res or {}).get("edges", []) or []:
            src, dst, rel = e.get("src"), e.get("dst"), e.get("rel")
            if (src in valid_ids and dst in valid_ids and src != dst and rel in _RELS
                    and (src, dst, rel) not in existing):
                existing.add((src, dst, rel))
                rationale = (e.get("rationale") or "").strip()[:600]
                found.append({"src": src, "dst": dst, "rel": rel, "rationale": rationale,
                              "rationale_verified": _rationale_in_text(rationale, norm_full),
                              "project": project, "session_ref": real_sid})
        sessions_done += 1

    written = 0
    if found and not dry_run:
        ensure_dirs()
        with CAUSAL_EDGES_PATH.open("a", encoding="utf-8") as f:
            for e in found:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        written = len(found)
    return {"project": project, "edges_found": len(found), "edges_written": written,
            "sessions": sessions_done, "dry_run": dry_run,
            "samples": [{"src": e["src"], "rel": e["rel"], "dst": e["dst"]} for e in found[:5]]}


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    # 独立跑时显式加载仓根 .env(THE_COMPANY_API_KEY 等),与 cli/main.py、dashboard/app.py 一致
    try:
        from dotenv import load_dotenv

        from .._paths import DATA_ROOT
        load_dotenv(DATA_ROOT.parents[2] / ".env")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="因果边抽取(decision↔material 带理由)")
    ap.add_argument("--project", default="aigc")
    ap.add_argument("--model", default=None)
    ap.add_argument("--write", action="store_true", help="真写 sidecar(默认 dry-run)")
    args = ap.parse_args(argv)
    s = extract_for_project(args.project, model=args.model, dry_run=not args.write)
    print(json.dumps(s, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
