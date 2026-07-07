# [OMNI] origin=claude-code domain=decisions ts=2026-07-04T00:00:00Z type=module status=active
# [OMNI] summary="标准化动词层(统一设计工作室第三期):决策边/画布连线上的动词标注。schema=调研草案C(verb/from_state/to_state/rationale/challenges),默认词表=草案A六词(拆分/推导/联想/生成/反证/延伸)。"
# [OMNI] why="计划权威 docs/plans/[2026-07-04]UNIFIED-DESIGN-STUDIO/plan.md §3.4+§10 第三期;词表依据 docs/research/2026-07-04-decision-verbs-survey.md(草案C地基+草案A默认表,核心≤10+可插拔,预期坍缩按频率裁剪)。"
# [OMNI] tags=decisions,verbs,annotation,schema-c,exploration
"""标准化动词层 —— 决策边 / 画布连线上的动词标注(BLF-2026-07-04-001 实战半程)。

设计:
  - 边 = {source, target, rel}(与 exploration.projection 的边三元组同构,不另造 id 体系)。
  - annotation 契约(schema C):verb / from_state / to_state / rationale / challenges[] /
    annotator / ts / source(human|ai)。
  - 幂等键 = (source, target, rel, annotator):同一人对同一条边重标 = 覆盖语义
    (append-only 文件里追加新行,fold 时按幂等键取最新)。
  - 默认词表 VERBS 六词,允许表外自定义词——表外词原样记录,统计时单列"表外"。

存储:data/domains/decisions/verb_annotations.jsonl(见 config/ledgers.yaml: verb-annotations)。
路径写死,不接受路径参数——想写别处只能绕过本模块,绕过即违规(照账本铁律)。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ._paths import DATA_ROOT, VERB_ANNOTATIONS_PATH

# 草案 A 六词默认词表(调研报告第三节,认知动作/最贴用户直觉)。
VERBS: tuple[str, ...] = ("拆分", "推导", "联想", "生成", "反证", "延伸")

_SOURCE_KINDS = ("human", "ai")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dir() -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)


def is_out_of_table(verb: str) -> bool:
    """verb 是否在默认六词表之外(允许,统计时单列"表外")。"""
    return (verb or "").strip() not in VERBS


def edge_key(edge: dict[str, Any]) -> tuple[str, str, str]:
    """(source, target, rel) 三元组,公共辅助——供采样器/统计复用同一套边身份定义。"""
    return (str(edge.get("source", "")), str(edge.get("target", "")), str(edge.get("rel", "")))


# 向后兼容内部别名
_edge_key = edge_key


def idem_key(rec: dict[str, Any]) -> tuple[str, str, str, str]:
    """幂等键:edge 三元组 + annotator。同人同边重标 = 覆盖(fold 取最新行)。"""
    edge = rec.get("edge") or {}
    return (*edge_key(edge), str(rec.get("annotator", "")))


# 向后兼容内部别名
_idem_key = idem_key


# ── 写 ───────────────────────────────────────────────────────────────────────

def append_annotation(
    *,
    source: str,
    target: str,
    rel: str,
    verb: str,
    from_state: str = "",
    to_state: str = "",
    rationale: str = "",
    challenges: list[str] | None = None,
    annotator: str = "",
    ts: str | None = None,
    source_kind: str = "human",
) -> dict[str, Any]:
    """追加一条 annotation(append-only)。同幂等键(edge+annotator)再写 = 覆盖语义。

    source_kind 对应 schema 的 "source" 字段(human|ai);参数名避开与 edge.source 撞名。
    """
    if source_kind not in _SOURCE_KINDS:
        raise ValueError(f"source 须为 human|ai,收到 {source_kind!r}")
    verb = (verb or "").strip()
    if not verb:
        raise ValueError("verb 不可为空")
    if not annotator:
        raise ValueError("annotator 不可为空(幂等键的一部分)")

    rec: dict[str, Any] = {
        "edge": {"source": source, "target": target, "rel": rel},
        "verb": verb,
        "from_state": from_state or "",
        "to_state": to_state or "",
        "rationale": rationale or "",
        "challenges": list(challenges or []),
        "annotator": annotator,
        "ts": ts or now_iso(),
        "source": source_kind,
    }
    _ensure_dir()
    with VERB_ANNOTATIONS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


# ── 读 / 折叠 ─────────────────────────────────────────────────────────────────

def _read_lines() -> list[dict[str, Any]]:
    if not VERB_ANNOTATIONS_PATH.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in VERB_ANNOTATIONS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def fold() -> list[dict[str, Any]]:
    """折叠成当前态:每个幂等键(edge+annotator)取最新写入的那行。"""
    folded: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for rec in _read_lines():
        folded[_idem_key(rec)] = rec
    return list(folded.values())


def list_annotations(*, verb: str | None = None) -> list[dict[str, Any]]:
    """列当前态标注,可按 verb 过滤。"""
    recs = fold()
    if verb:
        recs = [r for r in recs if r.get("verb") == verb]
    return recs


# ── 统计(证据列表不打分)───────────────────────────────────────────────────────

def stats() -> dict[str, Any]:
    """词频分布 / per-rel 分布 / human-ai 计数 / 表外词清单 / 双标注者边界冲突清单。"""
    recs = fold()

    verb_freq: dict[str, int] = {}
    out_of_table: dict[str, int] = {}
    by_rel: dict[str, dict[str, int]] = {}
    by_source: dict[str, int] = {"human": 0, "ai": 0}

    # edge_key -> {verb -> [annotator,...]},用于查边界冲突(同边不同人标了不同词)
    edge_verb_annotators: dict[tuple[str, str, str], dict[str, list[str]]] = {}

    for rec in recs:
        verb = (rec.get("verb") or "").strip()
        verb_freq[verb] = verb_freq.get(verb, 0) + 1
        if is_out_of_table(verb):
            out_of_table[verb] = out_of_table.get(verb, 0) + 1

        edge = rec.get("edge") or {}
        rel = str(edge.get("rel", ""))
        by_rel.setdefault(rel, {})
        by_rel[rel][verb] = by_rel[rel].get(verb, 0) + 1

        sk = rec.get("source")
        if sk in by_source:
            by_source[sk] += 1

        ek = _edge_key(edge)
        edge_verb_annotators.setdefault(ek, {}).setdefault(verb, []).append(rec.get("annotator", ""))

    conflicts: list[dict[str, Any]] = []
    for ek, verb_map in edge_verb_annotators.items():
        if len(verb_map) > 1:
            conflicts.append({
                "edge": {"source": ek[0], "target": ek[1], "rel": ek[2]},
                "verbs": {v: sorted(set(a for a in annotators if a)) for v, annotators in verb_map.items()},
            })

    return {
        "total": len(recs),
        "verb_freq": verb_freq,
        "out_of_table": out_of_table,
        "by_rel": by_rel,
        "by_source": by_source,
        "conflicts": conflicts,
    }


# ── 报告 ─────────────────────────────────────────────────────────────────────

def export_report(out_path: str) -> str:
    """把 stats() 渲染成 markdown,写到 out_path,返回该路径。证据列表不打分。"""
    from pathlib import Path

    s = stats()
    lines = [
        "# 标准化动词层标注报告",
        "",
        f"- 总标注数: {s['total']}",
        f"- human/ai: {s['by_source'].get('human', 0)} / {s['by_source'].get('ai', 0)}",
        "",
        "## 词频分布",
        "",
    ]
    for verb, n in sorted(s["verb_freq"].items(), key=lambda kv: -kv[1]):
        tag = "(表外)" if is_out_of_table(verb) else ""
        lines.append(f"- {verb}{tag}: {n}")

    lines += ["", "## 表外词清单", ""]
    if s["out_of_table"]:
        for verb, n in sorted(s["out_of_table"].items(), key=lambda kv: -kv[1]):
            lines.append(f"- {verb}: {n}")
    else:
        lines.append("(无)")

    lines += ["", "## per-rel 分布", ""]
    for rel, verb_map in sorted(s["by_rel"].items()):
        lines.append(f"### {rel}")
        for verb, n in sorted(verb_map.items(), key=lambda kv: -kv[1]):
            lines.append(f"- {verb}: {n}")
        lines.append("")

    lines += ["## 边界冲突清单(同边不同标注者给了不同动词;证据列表不打分)", ""]
    if s["conflicts"]:
        for c in s["conflicts"]:
            e = c["edge"]
            lines.append(f"- {e['source']} --{e['rel']}--> {e['target']}:")
            for verb, annotators in c["verbs"].items():
                lines.append(f"    - {verb}: {', '.join(annotators) or '(匿名)'}")
    else:
        lines.append("(无)")

    text = "\n".join(lines) + "\n"
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return str(p)
