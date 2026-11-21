# [OMNI] origin=claude-code domain=omnifactory/guardian ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:guardian.llm_violation_judge.worker.py"
"""LLMJudgeWorker — Guardian Team Worker #3.

Worker 协议:
  FORMAT_IN  = guardian.violation_set
  FORMAT_OUT = guardian.violation_set.judged

职责: 订阅 violation_set.needs_judgment → LLM/Agent 复核 → 产出 judged 集合。
未启用 LLM/Agent 时等同直通 (needs_judgment 丢弃, 只留 confirmed)。
内部 delegate 到现有 `LLMJudge.review()` / `GuardianAgent.review()`。

Q4 dogfood 要点: 此 Worker 是 "订阅部分 material 字段" 案例
— 它只消费 needs_judgment 子集, 不触及 confirmed/duplicates, 体现了
material 内部字段选择消费的合法性。
"""
from __future__ import annotations

from typing import Any

from omnifactory.protocol.anchor import Verdict, VerdictKind
from omnifactory.packages.services._core.omnicompany import Worker


class LLMJudgeWorker(Worker):
    """对 needs_judgment 违规做 LLM/Agent 复核 → 合并产出 violation_set.judged。"""

    DESCRIPTION = (
        "Guardian Team Worker #3: 订阅 guardian.violation_set, 对 needs_judgment "
        "部分调用 LLMJudge / GuardianAgent 复核, 合并 confirmed + 通过复核的"
        "judged, 产出 guardian.violation_set.judged。未启用 LLM 时直通 confirmed。"
    )
    FORMAT_IN = "guardian.violation_set"
    FORMAT_OUT = "guardian.violation_set.judged"

    def run(self, input_data: dict[str, Any]) -> Verdict:
        payload = input_data.get("guardian.violation_set") or input_data
        scan_ts = payload.get("scan_ts", "")
        scan_mode = payload.get("scan_mode", "diff")

        confirmed = payload.get("confirmed", [])
        needs_judgment = payload.get("needs_judgment", [])

        # Q0 阶段直通模式: 不启用 LLM/Agent, 仅透传 confirmed
        # Phase 1 后接 LLMJudge.review() / GuardianAgent.review()
        judged = []
        for v in needs_judgment:
            # placeholder: 未启用复核时全部不通过 (保守)
            # 真实调用: `reviewed = LLMJudge().review(violation_from_dict(v))`
            pass

        merged_violations = [{**v, "reviewed_by": None, "review_confidence": 1.0} for v in confirmed] + judged

        # Protocol 约定: verdict.output 是 FORMAT_OUT 对应 Format 的 payload 本体 (平铺)
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "scan_ts": scan_ts,
                "scan_mode": scan_mode,
                "violations": merged_violations,
                "agent_reviewed": len(judged),
            },
        )
