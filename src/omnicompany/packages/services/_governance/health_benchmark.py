# [OMNI] origin=claude-code domain=services/_governance ts=2026-06-27T00:00:00Z type=router
# [OMNI] summary="语义空间健康治理金标 benchmark。人/主力模型亲读后手标 gold, 跑性价比模型对照一致率(证据列表不打分)。便宜模型产出不免检。"
# [OMNI] why="MEMORY 铁律:便宜模型产出建金标 benchmark, 人亲读才作数。给进度三态精判一个可信度量。"
# [OMNI] tags=governance,benchmark,gold-standard,semantic-space-health
# [OMNI] material_id="material:governance.health_benchmark.py"
"""进度三态精判金标 benchmark(证据列表, 不打分)。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root

# 人(主力模型)亲读手标的金标种子: snippet + whatnow 状态 → 期望三态
_PROGRESS_GOLD_SEED = [
    {"snippet": "状态: **已实现并验证**, 完成度 100%", "whatnow": "完成度 30% in_progress",
     "gold": "progress_drift", "why": "指涉项目整体进度且与 whatnow 冲突"},
    {"snippet": "决策: 选 A 不选 B, 因为 B 会造出第二个真源", "whatnow": "完成度 50%",
     "gold": "decision_design", "why": "为什么这么做的长期叙述, 非进度"},
    {"snippet": "本模块定义 track 的 business 与 plan 概念: 改软件 ≠ 用软件", "whatnow": "未纳管",
     "gold": "decision_design", "why": "概念定义, 长期有效"},
    {"snippet": "- [x] Step 3 阶段 A 纸面 UX 设计 (本计划自己的勾)", "whatnow": "完成度 60%",
     "gold": "false_positive", "why": "本模块自我陈述自己的 TODO/done, SRP 合规"},
    {"snippet": "下一步: 把 Phase 2-4 的实读逻辑做实并对 gemini-cli 跑通", "whatnow": "完成度 60% in_progress",
     "gold": "progress_drift", "why": "陈述下一步进度计划"},
    {"snippet": "Format 是数据契约, 是节点间通信的类型系统", "whatnow": "未纳管",
     "gold": "false_positive", "why": "稳定事实/定义陈述"},
]


def gold_path(facility: str = "progress_steward", root: Path | None = None) -> Path:
    d = (root or omni_workspace_root()) / "data" / "governance" / facility
    d.mkdir(parents=True, exist_ok=True)
    return d / "benchmark_gold.json"


def ensure_progress_gold(root: Path | None = None) -> Path:
    p = gold_path("progress_steward", root)
    if not p.is_file():
        p.write_text(json.dumps({"kind": "progress_three_state", "samples": _PROGRESS_GOLD_SEED},
                                ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def run_progress_benchmark(*, model: str | None = None, root: Path | None = None,
                           echo: Any = None) -> dict[str, Any]:
    """跑性价比模型对金标, 报一致率 + 每条证据(不打分)。"""
    from omnicompany.runtime.llm.structured import call_json
    from .progress_steward.review import SYSTEM, SCHEMA
    base = root or omni_workspace_root()
    gold = json.loads(ensure_progress_gold(base).read_text(encoding="utf-8"))["samples"]
    evidence = []
    agree = 0
    for s in gold:
        user = (f"文档: benchmark\nwhatnow 当前状态: {s['whatnow']}\n\n候选行:\n- L1 [test]: {s['snippet']}")
        try:
            res = call_json(system=SYSTEM, user=user, schema=SCHEMA, model=model,
                            caller="health_benchmark.progress", max_tokens=600, max_corrections=2)
            got = (res.get("classifications") or [{}])[0].get("state", "?")
        except Exception as e:  # noqa: BLE001
            got = f"error:{str(e)[:40]}"
        ok = got == s["gold"]
        agree += int(ok)
        evidence.append({"snippet": s["snippet"][:50], "gold": s["gold"], "model": got,
                         "agree": ok, "why_gold": s["why"]})
        if echo:
            echo(f"  {'✓' if ok else '✗'} gold={s['gold']:16} model={got:16} | {s['snippet'][:36]}")
    payload = {"kind": "progress_benchmark", "model": model or "default",
               "agreement": f"{agree}/{len(gold)}", "evidence": evidence}
    out = (base / "data" / "governance" / "progress_steward" / "benchmark_result.json")
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
