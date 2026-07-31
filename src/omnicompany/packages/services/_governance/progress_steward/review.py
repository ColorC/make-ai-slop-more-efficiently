# [OMNI] origin=claude-code domain=services/_governance/progress_steward ts=2026-06-27T00:00:00Z type=router
# [OMNI] summary="进度型自述语义精判。对确定性候选三态分类, 只喂 WhatNow 当前状态+候选行降误报, findings 合并送审。"
# [OMNI] why="纯词表会高误报(DocPrism 两段式经验);进度真源在 whatnow, 精判需对照它判'这行是否还成立/是否指涉别处进度'。决策叙述不可当垃圾删。"
# [OMNI] tags=governance,progress-ssot,llm-semantic,reviewstage
# [OMNI] material_id="material:governance.progress_steward.review.py"
"""进度型自述语义精判 — 三态分类(性价比模型), 合并进 Reviewstage。

形态(对齐 governance_semantic_first.md + doc_steward):
  - 输入 = 里程碑一确定性探针圈出的候选(progress_scan.json)。
  - 只喂"该计划在 whatnow 的当前状态摘要 + 候选行", 不灌全文(降幻觉/省 token)。
  - 三态: progress_drift(指涉式进度, 应剥离/标'以 whatnow 为准') / decision_design(决策设计叙述, 保留并可导向 decisions 域) / false_positive(放过)。
  - broken_ref 是确定性腐化, 不走 LLM 直接判 progress_drift。
  - 每轮把可处置项合并成一份审阅材料(给证据列表, 不打分, 内容不变不重复)。
  - 断点续跑(2026-07-26): 每篇判完立即落盘 progress_review-checkpoint.json,
    重跑时签名(候选行集合)一致的篇目直接复用 —— 被心跳超时杀也保住已判部分, 下周只补缺口。
"""
from __future__ import annotations

import hashlib
import json
import threading
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


# ── 断点续跑 checkpoint(2026-07-26: 跑到 90/92 被心跳超时杀, 整轮白烧) ──────
# 每篇判完立即落盘; 重跑时签名(候选行集合)一致的篇目直接复用, 超时被杀也保住已判部分。

def _checkpoint_path() -> Path:
    return report_dir() / "progress_review-checkpoint.json"


def _load_checkpoint() -> dict[str, Any]:
    try:
        raw = json.loads(_checkpoint_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _doc_signature(cands: list[dict], brokens: list[dict]) -> str:
    """候选行集合签名 — 文档内容/探针结果变了就重判, 没变就复用上轮结论。"""
    rows = sorted(
        [("c", c.get("line"), c.get("category"), c.get("snippet")) for c in cands]
        + [("b", b.get("line"), b.get("category"), b.get("snippet")) for b in brokens]
    )
    return hashlib.sha1(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


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
                        only_ref: bool = False, submit_review: bool = True,
                        workers: int = 4, root: Path | None = None,
                        echo: Any = None, review_store=None) -> dict[str, Any]:
    """对探针候选做三态精判, 可处置项合并成一份审阅材料。"""
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

    # 断点续跑: 签名一致的篇目复用 checkpoint, 只精判新增/变了的; 每篇判完立即落盘。
    checkpoint = _load_checkpoint()
    signatures = {d: _doc_signature(by_doc.get(d, []), broken.get(d, [])) for d in docs}
    reused: dict[str, dict[str, Any]] = {}
    todo: list[str] = []
    for d in docs:
        prev = checkpoint.get(d)
        prev_cls = (prev or {}).get("result", {}).get("classifications", []) if isinstance(prev, dict) else []
        if isinstance(prev, dict) and prev.get("signature") == signatures[d] \
                and isinstance(prev.get("result"), dict) \
                and not any(c.get("state") == "error" for c in prev_cls):
            # 签名一致且无 LLM 错误块 → 复用; 有错误块的不进复用, 下轮重判(瞬时失败可自愈)
            reused[d] = prev["result"]
        else:
            todo.append(d)
    _ck_lock = threading.Lock()

    def _persist(doc: str, result: dict[str, Any]) -> None:
        with _ck_lock:
            checkpoint[doc] = {"signature": signatures[doc], "result": result, "at": _now()}
            try:
                _checkpoint_path().write_text(
                    json.dumps(checkpoint, ensure_ascii=False, indent=1), encoding="utf-8")
            except OSError:
                pass

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
        result = {"doc": doc, "classifications": result_cls}
        _persist(doc, result)
        return result

    batch = run_parallel_items(todo, _review_doc, workers=workers,
                               progress_label="progress_steward.review",
                               item_label=lambda i, d: Path(d).parent.name, echo=echo)
    judged = {r["doc"]: r for r in batch.results if r}
    # 按 docs 顺序合并: 本轮新判 + checkpoint 复用(被杀掉的篇目下周只补判缺口)
    doc_results = [judged.get(d) or reused[d] for d in docs if d in judged or d in reused]

    # 抑制名单过滤(人已判可接受的点不再报, 防定时跑刷旧噪声)
    from omnicompany.packages.services._governance.health_suppress import is_suppressed
    for r in doc_results:
        r["classifications"] = [
            c for c in r["classifications"]
            if not is_suppressed("progress_steward", f"{r['doc']}:{c.get('line')}:{c.get('state')}", base)
        ]

    # 汇总: 不再为每份文档造一个阻塞待办。
    drift_total = decision_total = 0
    for r in doc_results:
        drifts = [c for c in r["classifications"] if c["state"] == "progress_drift"]
        decisions = [c for c in r["classifications"] if c["state"] == "decision_design"]
        drift_total += len(drifts)
        decision_total += len(decisions)

    payload = {
        "kind": "progress_review", "generated_at": _now(), "model": model or "default",
        "reviewed_docs": len(doc_results), "failed_docs": len(batch.failures),
        "reused_docs": len(reused), "judged_docs": len(judged),
        "drift_total": drift_total, "decision_total": decision_total,
        "results": doc_results,
    }
    stamp = _now().replace(":", "").replace("-", "")[:15]
    (report_dir() / f"progress_review-{stamp}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (report_dir() / "progress_review-latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if submit_review and (drift_total or decision_total):
        if review_store is None:
            from omnicompany.dashboard.boss_sight.reviewstage.routes import get_store
            review_store = get_store()
        from omnicompany.dashboard.boss_sight.reviewstage.report_submission import submit_markdown_report
        lines = [
            "# 进度语义复判", "",
            f"- 复判文档: {len(doc_results)}",
            f"- 进度漂移: {drift_total}",
            f"- 决策/设计叙述: {decision_total}", "",
        ]
        for result in doc_results:
            actionable = [c for c in result["classifications"] if c.get("state") in {"progress_drift", "decision_design"}]
            if not actionable:
                continue
            lines.append(f"## {result['doc']}")
            for item in actionable[:20]:
                lines.append(f"- L{item.get('line')} · {item.get('state')} · {item.get('evidence')}")
            lines.append("")
        payload["review_material"] = submit_markdown_report(
            review_store,
            title="进度语义复判合并报告",
            content="\n".join(lines),
            source_plan_id="omnicompany-governance/[2026-06-27]SEMANTIC-SPACE-HEALTH",
            reason="语义精判发现进度漂移或需沉淀的决策叙述; 请按证据合并核对。",
            dedupe_key="progress-semantic-review",
            stable_payload=json.dumps(doc_results, ensure_ascii=False, sort_keys=True),
            version_family="progress-semantic-review",
            extra={"report_path": str(report_dir() / "progress_review-latest.json")},
        )
    return payload
