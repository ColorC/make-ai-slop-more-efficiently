# [OMNI] origin=claude-code domain=services/_governance/progress_steward ts=2026-06-27T00:00:00Z type=router
# [OMNI] summary="进度型自述语义精判(轨一里程碑二, 性价比模型)。对确定性探针圈出的候选三态分类(进度漂移/决策设计/误报), 只喂 whatnow 当前状态+候选行降误报, findings 进 human-inbox 待审。"
# [OMNI] why="纯词表会高误报(DocPrism 两段式经验);进度真源在 whatnow, 精判需对照它判'这行是否还成立/是否指涉别处进度'。决策叙述不可当垃圾删。"
# [OMNI] tags=governance,progress-ssot,llm-semantic,human-inbox
# [OMNI] material_id="material:governance.progress_steward.review.py"
"""进度型自述语义精判 — 三态分类(性价比模型), 进 human-inbox。

形态(对齐 governance_semantic_first.md + doc_steward):
  - 输入 = 里程碑一确定性探针圈出的候选(progress_scan.json)。
  - 只喂"该计划在 whatnow 的当前状态摘要 + 候选行", 不灌全文(降幻觉/省 token)。
  - 三态: progress_drift(指涉式进度, 应剥离/标'以 whatnow 为准') / decision_design(决策设计叙述, 保留并可导向 decisions 域) / false_positive(放过)。
  - broken_ref 是确定性腐化, 不走 LLM 直接判 progress_drift。
  - 每个 doc 有可处置项就开一条 human-inbox 待审(给证据列表, 不打分)。
"""
from __future__ import annotations

import json
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root

from .probe import report_dir, run_progress_scan

WHATNOW = "http://127.0.0.1:8230"

# 送 LLM 精判的候选类别(relative_time 仅在 has_ref 时送, 否则噪声太大)
_LLM_CATS = {"progress_done", "progress_inprogress", "progress_planned", "metric"}

SYSTEM = """你是 omnicompany 的"进度型自述"判别员。背景铁律: 项目/计划进度的唯一真源是 whatnow 目标系统;\
plan.md / 代码注释 / 文档里**不该**写"指涉别处的、会随真源漂移的进度"(如完成度百分比/做到哪了/下一步), \
但**允许**写"自我陈述"(本模块自己的 TODO/done)与"决策/设计叙述"(为什么这么做, 长期有效)。

给你一篇文档里若干"候选行"(确定性探针圈出的含进度/时态措辞的行), 以及该计划在 whatnow 的当前状态。\
对每个候选行三态分类:
- progress_drift: 在陈述项目/计划进度且会随真源漂移(指涉式进度), 应剥离或改成"以 whatnow 为准"。
- decision_design: 是决策/设计/取舍/为什么这么做的长期叙述, 不是进度 → 保留(可导向 decisions 域记一笔)。
- false_positive: 命中了进度词但其实是稳定事实/定义/示例/本模块自我陈述 → 放过。
每条给一句证据(引该行关键词)。不确定时归 false_positive(宁放过不误伤)。只输出 JSON。"""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["classifications"],
    "properties": {
        "classifications": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["line", "state", "evidence"],
                "properties": {
                    "line": {"type": "integer"},
                    "state": {"type": "string",
                              "enum": ["progress_drift", "decision_design", "false_positive"]},
                    "evidence": {"type": "string"},
                },
            },
        },
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _board() -> dict:
    try:
        with urllib.request.urlopen(WHATNOW + "/api/board?archived=1", timeout=20) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return {}


def _plan_status_index(board: dict) -> dict[str, str]:
    """plan_id → 一行中文状态摘要。"""
    idx: dict[str, str] = {}
    for c in board.get("clusters", []):
        for g in c.get("goals", []):
            for t in g.get("tasks", []):
                pid = (t.get("plan_id") or "").strip()
                if pid:
                    lp = (t.get("latest_progress") or "")[:80]
                    idx[pid] = (f"完成度 {t.get('completion', 0)}% / 状态 {t.get('status', '?')}"
                                + (f" / 最新: {lp}" if lp else ""))
    return idx


def _plan_id_of(doc_rel: str) -> str:
    p = doc_rel.replace("\\", "/")
    if p.startswith("docs/plans/") and p.endswith("/plan.md"):
        return p[len("docs/plans/"):-len("/plan.md")]
    return ""


def run_progress_review(*, limit: int | None = None, model: str | None = None,
                        only_ref: bool = False, push_inbox: bool = True,
                        workers: int = 4, root: Path | None = None,
                        echo: Any = None) -> dict[str, Any]:
    """对探针候选做三态精判, 可处置项进 human-inbox。"""
    from omnicompany.runtime.llm.batch import run_parallel_items
    from omnicompany.runtime.llm.structured import call_json

    base = root or omni_workspace_root()
    scan = run_progress_scan(write=True, root=base)  # 先刷新候选
    by_doc: dict[str, list[dict]] = defaultdict(list)
    broken: dict[str, list[dict]] = defaultdict(list)
    for f in scan["findings"]:
        if f["category"] in ("broken_ref", "broken_anchor"):
            broken[f["doc"]].append(f)
            continue
        if only_ref:
            continue
        send = f["category"] in _LLM_CATS or (f["category"] == "relative_time" and f["has_ref"])
        if send:
            by_doc[f["doc"]].append(f)

    docs = sorted(set(by_doc) | set(broken))
    if limit:
        docs = docs[:limit]
    status_idx = _plan_status_index(_board())

    def _review_doc(doc: str) -> dict[str, Any]:
        cands = by_doc.get(doc, [])
        result_cls: list[dict] = []
        # broken_ref: 确定性 → 直接 progress_drift
        for bf in broken.get(doc, []):
            result_cls.append({"line": bf["line"], "state": "progress_drift",
                               "evidence": f"死链/坏行锚: {bf.get('target') or bf['snippet']}",
                               "category": bf["category"]})
        if cands:
            pid = _plan_id_of(doc)
            wn = status_idx.get(pid, "未纳管(whatnow 无对应 task)")
            # 分块(每块 ≤12 行)避免候选过多撑爆输出致 JSON 截断
            for i in range(0, len(cands), 12):
                chunk = cands[i:i + 12]
                lines = "\n".join(f"- L{c['line']} [{c['category']}]: {c['snippet']}" for c in chunk)
                user = (f"文档: {doc}\nwhatnow 当前状态: {wn}\n\n候选行:\n{lines}")
                try:
                    res = call_json(system=SYSTEM, user=user, schema=SCHEMA, model=model,
                                    caller="progress_steward.review", max_tokens=3000, max_corrections=2)
                    for c in (res or {}).get("classifications", []):
                        result_cls.append({"line": c.get("line"), "state": c.get("state"),
                                           "evidence": str(c.get("evidence", ""))[:160], "category": "llm"})
                except Exception as e:  # noqa: BLE001
                    result_cls.append({"line": 0, "state": "error",
                                       "evidence": f"LLM 失败(块 {i//12}): {str(e)[:120]}", "category": "llm"})
        return {"doc": doc, "classifications": result_cls}

    batch = run_parallel_items(docs, _review_doc, workers=workers,
                               progress_label="progress_steward.review",
                               item_label=lambda i, d: Path(d).parent.name, echo=echo)
    doc_results = [r for r in batch.results if r]

    # 抑制名单过滤(人已判可接受的点不再报, 防定时跑刷旧噪声)
    from omnicompany.packages.services._governance.health_suppress import is_suppressed
    for r in doc_results:
        r["classifications"] = [
            c for c in r["classifications"]
            if not is_suppressed("progress_steward", f"{r['doc']}:{c.get('line')}:{c.get('state')}", base)
        ]

    # 汇总 + 推 human-inbox
    inbox_opened = 0
    drift_total = decision_total = 0
    if push_inbox:
        from omnicompany.runtime.buses import HumanBus, HumanKind
        hb = HumanBus()
    for r in doc_results:
        drifts = [c for c in r["classifications"] if c["state"] == "progress_drift"]
        decisions = [c for c in r["classifications"] if c["state"] == "decision_design"]
        drift_total += len(drifts)
        decision_total += len(decisions)
        if push_inbox and (drifts or decisions):
            lines = []
            if drifts:
                lines.append("【进度漂移·建议剥离或标'以 whatnow 为准'】")
                lines += [f"  L{c['line']}: {c['evidence']}" for c in drifts[:12]]
            if decisions:
                lines.append("【决策/设计叙述·建议 omni decisions record 记一笔后保留】")
                lines += [f"  L{c['line']}: {c['evidence']}" for c in decisions[:8]]
            hb.ask(
                question=(f"计划文档 {r['doc']} 含 {len(drifts)} 条进度漂移 / {len(decisions)} 条决策叙述。"
                          f"进度真源是 whatnow, 请处置(剥离/标注/记决策/保留)。\n" + "\n".join(lines)),
                kind=HumanKind.HUMAN_BLOCKING,
                context={"doc": r["doc"], "drifts": drifts, "decisions": decisions,
                         "facility": "progress_steward.review"},
                source="progress_steward.review",
            )
            inbox_opened += 1

    payload = {
        "kind": "progress_review", "generated_at": _now(), "model": model or "default",
        "reviewed_docs": len(doc_results), "failed_docs": len(batch.failures),
        "drift_total": drift_total, "decision_total": decision_total,
        "inbox_opened": inbox_opened, "results": doc_results,
    }
    stamp = _now().replace(":", "").replace("-", "")[:15]
    (report_dir() / f"progress_review-{stamp}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (report_dir() / "progress_review-latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
