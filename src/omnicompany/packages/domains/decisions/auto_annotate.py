# [OMNI] origin=claude-code domain=decisions ts=2026-07-04T00:00:00Z type=module status=active
# [OMNI] summary="AI 半程标注器:从决策图(exploration.projection.build_graph)采样边,调结构化模型选标准化动词,写入 verb_annotations.jsonl。支持 two-pass 边界冲突采集与断点续跑。"
# [OMNI] why="统一设计工作室第三期(plan.md §10 第三期):首棵可见决策树+walker 画布上积累 100 条实战标注,供词表坍缩/裁剪定稿。"
# [OMNI] tags=decisions,verbs,auto-annotate,llm,exploration
"""omni decisions verb auto-annotate 的实现。

采样策略:
  1. exploration.projection.build_graph(project) 拿全部边;
  2. 优先取非 rejected 的真实边(rests_on/supersedes/parent/related/enforced_by/产出/依据/refines/...);
  3. 不足 cap 再从 rejected 边采样补足。

每条边给模型两端节点的 kind + statement(各截≤150字)+ rel,模型从六词表里选一个,
或输出"其他:<词>"(表外自定义),并给一句 rationale。

--two-pass:同一批边分别用两个不同 system 视角标注两遍,annotator 分别带 -p1/-p2 后缀,
供 verbs.stats() 的边界冲突统计使用(同边不同人标了不同词)。

断点续跑:跳过"同 annotator 同边"已存在的标注(verbs.fold() 幂等键命中即跳)。
"""

from __future__ import annotations

import json
import os
from typing import Any

from . import verbs
from ._paths import _OMNI_ROOT
from .exploration import projection

# 直跑/CLI 调用前手动加载仓根 .env(THE_COMPANY_API_KEY 等),照 scripts/backfill_decision_projects.py。
_ENV = _OMNI_ROOT / ".env"


def _load_env_once() -> None:
    if not _ENV.is_file():
        return
    for line in _ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# rel 优先级:非 rejected 的"真实边"优先采样,rejected(死分支)不足才补。
_PRIORITY_RELS = (
    "rests_on", "supersedes", "parent", "related", "enforced_by",
    "产出", "依据", "约束", "refines", "critiques", "responds_to_critique",
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "verb": {"type": "string", "description": f"六词表之一({'/'.join(verbs.VERBS)})或'其他:<词>'"},
        "rationale": {"type": "string", "description": "一句话:为什么是这个动作"},
    },
    "required": ["verb", "rationale"],
}

# 批式 schema: 一次调用标一批边(index 对齐输入序),吞吐比逐边快一个量级
_BATCH = 20
_BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "annotations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "对应输入 edges 数组下标"},
                    "verb": {"type": "string",
                             "description": f"六词表之一({'/'.join(verbs.VERBS)})或'其他:<词>'"},
                    "rationale": {"type": "string", "description": "一句话:为什么是这个动作"},
                },
                "required": ["index", "verb", "rationale"],
            },
        },
    },
    "required": ["annotations"],
}


def _system_prompt(perspective: str | None = None) -> str:
    base = (
        "你在给决策图里的一条边标注『这一步在做什么认知动作』。"
        f"默认六词表:{'/'.join(verbs.VERBS)}——"
        "拆分=把问题/产物拆成更小的子问题或子部件;"
        "推导=从已确立的前提/知识推出新结论;"
        "联想=借结构相似的已有案例/概念做迁移;"
        "生成=产出一个新的候选方案/新版本;"
        "反证=提出反对、指出前提/推理/结论的失效;"
        "延伸=由当前议题/产物衍生出新的相邻议题。"
        "优先从六词表里选一个;实在不贴切才输出'其他:<你的词>',不要滥用表外词。"
        "rationale 只写一句话,依据给定的两端节点内容和关系类型判断,不要编造未给出的信息。"
    )
    if perspective == "p2":
        base += (
            "\n\n【第二视角】你是独立复核者,不要参考任何先前标注,只依据边本身的信息独立判断——"
            "这是为了后续做双标注者一致性分析,你的判断必须是你自己的、不受他人影响的第一反应。"
        )
    return base


def _node_ctx(nodes_by_id: dict[str, dict], node_id: str) -> dict[str, str]:
    n = nodes_by_id.get(node_id) or {}
    return {
        "id": node_id,
        "kind": n.get("kind") or n.get("record_kind") or "",
        "statement": (n.get("statement") or n.get("label") or "")[:150],
    }


def _sample_edges(project: str | None, cap: int) -> tuple[list[dict[str, Any]], dict[str, dict]]:
    """从决策图采样最多 cap 条边:优先非 rejected 真实边,不足再用 rejected 补足。返回 (边列表, 节点id→节点)。"""
    g = projection.build_graph(project=project)
    edges = g.get("edges") or []
    priority = [e for e in edges if e.get("rel") in _PRIORITY_RELS]
    rejected = [e for e in edges if e.get("rel") == "rejected"]
    sample = priority[:cap]
    if len(sample) < cap:
        sample += rejected[: cap - len(sample)]
    return sample[:cap], {n["id"]: n for n in g.get("nodes") or []}


def run(*, project: str | None = None, cap: int = 100, model: str = "qwen3.6-plus",
        two_pass: bool = False) -> dict[str, Any]:
    """采样边 + 调模型标注 + 断点续跑写入。返回 {sampled, written, skipped, note}。"""
    _load_env_once()
    from omnicompany.runtime.llm.structured import call_json

    edges, nodes_by_id = _sample_edges(project, cap)
    if not edges:
        return {"sampled": 0, "written": 0, "skipped": 0, "note": "决策图无可标注的边(project 过滤后为空?)"}

    existing = {(*verbs.edge_key(r["edge"]), r["annotator"]) for r in verbs.fold()}

    passes = ["p1", "p2"] if two_pass else [None]
    written = skipped = 0
    errors: list[str] = []

    for pass_name in passes:
        annotator_suffix = f"-{pass_name}" if pass_name else ""
        annotator = f"{model}{annotator_suffix}"
        system = _system_prompt(pass_name)
        todo = []
        for e in edges:
            src, dst, rel = e.get("source", ""), e.get("target", ""), e.get("rel", "")
            if (src, dst, rel, annotator) in existing:
                skipped += 1
            else:
                todo.append((src, dst, rel))
        for i in range(0, len(todo), _BATCH):
            batch = todo[i:i + _BATCH]
            user = json.dumps({"edges": [
                {"index": j, "rel": rel,
                 "source_node": _node_ctx(nodes_by_id, src),
                 "target_node": _node_ctx(nodes_by_id, dst)}
                for j, (src, dst, rel) in enumerate(batch)
            ]}, ensure_ascii=False)
            try:
                res = call_json(system=system + "\n对输入 edges 数组逐条标注,index 对齐下标,一条不漏。",
                                user=user, schema=_BATCH_SCHEMA, model=model,
                                caller="decisions.verbs.auto_annotate", max_tokens=4000)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"批 {i // _BATCH + 1}: {str(exc)[:80]}")
                continue
            got = {a.get("index"): a for a in (res.get("annotations") or []) if isinstance(a, dict)}
            for j, (src, dst, rel) in enumerate(batch):
                a = got.get(j)
                raw_verb = str((a or {}).get("verb") or "").strip()
                verb = raw_verb.split(":", 1)[1].strip() if raw_verb.startswith("其他:") else raw_verb
                if not verb:
                    errors.append(f"{src}->{dst}({rel}): 批内漏标/未给 verb")
                    continue
                verbs.append_annotation(
                    source=src, target=dst, rel=rel, verb=verb,
                    rationale=str((a or {}).get("rationale") or ""),
                    annotator=annotator, source_kind="ai",
                )
                existing.add((src, dst, rel, annotator))
                written += 1

    note = ""
    if errors:
        note = f"{len(errors)} 条调用失败/无效(可重跑续标,幂等跳过已写):" + "; ".join(errors[:3])
    return {"sampled": len(edges) * len(passes), "written": written, "skipped": skipped, "note": note}
