# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/builders ts=2026-05-07T09:30:00Z type=router status=active agent=ai-ide
# [OMNI] summary="HypothesisChallengeQueue — 据假设 V1 metadata 客观排序产'优先怀疑队列'. 修 V2 第二项 — schema §三步骤 1-2 真触发器"
# [OMNI] why="hypothesis_v1_upgrade_report 7.6 V2 待办第一项 '立质疑工作流真触发器'. schema §三步骤 1-2: focus_count + 优先怀疑顺序 (confidence=low+risk=high → untested → depended_by≥3)"
# [OMNI] tags=builder,challenge-queue,hypothesis-priority,structured,no-llm
# [OMNI] material_id="material:diagnosis.doctor.builders.hypothesis_challenge_queue.py"
"""HypothesisChallengeQueue · 假设质疑优先级排序 (V0).

跟 V1Upgrader 同形态 — 不用 LLM, 纯函数, 不直接落盘. 输入假设 list 输出排序后 list.

按 hypothesis_system_schema §三步骤 1-2:
  步骤 1: 立 focus_count = N (调用方决定 N)
  步骤 2: 优先怀疑顺序:
    a. confidence_level=low + risk_if_wrong=high (新生成未验证 + 重要)
    b. verification_status=untested + applies_to 跟当前问题对得上
    c. depended_by ≥ 3 (基础假设, 一崩多崩)

V0 实施步骤 2 排序逻辑:
  对每条假设算 priority_score (越高越优先怀疑) + priority_reason (字符串 list 解释).
  按 score 降序排, 同 score 按 id 升序稳定排. 截前 N 返.

  score 拆分 (按 schema 顺序, a 类比 b 大, b 比 c 大):
  +1000  if a 触发 (confidence=low + risk=high)
  +100   if b 触发 (untested + applies_to 命中 problem_context)
  +10*N  if c 触发 (depended_by N, N 个其他假设依赖)
  +1     for each verification_status==falsified (轻微优先 — 已被证否的应当列出便复审)

V0 不动假设本身 (不写 challenge_log / 不改 status). 那是 challenge agent 真做证否时的事.
本组件只负责排序 + 提供 priority_reason 给调用方决定看哪几条.

调用方:
    queue = HypothesisChallengeQueue()
    ranked = queue.rank(hypotheses, problem_context={"applies_to": "worker"}, focus_count=5)
    for entry in ranked:
        print(f"{entry.hypothesis_id}: score={entry.priority_score}, reasons={entry.priority_reasons}")
"""
from __future__ import annotations

from dataclasses import dataclass, field


# 优先级权重 (按 schema §三步骤 2 顺序: a > b > c)
_WEIGHT_A_LOW_HIGH = 1000   # confidence=low + risk=high
_WEIGHT_B_UNTESTED_MATCH = 100  # untested + applies_to 命中 problem_context
_WEIGHT_C_DEPENDED_BY = 10  # 每条 dependent ×10
_WEIGHT_FALSIFIED = 1       # 轻微优先 (已证否的应被复审 — 当 skip_frozen=False 时)

# frozen status (V7 2026-05-07 加, 跟 ChallengeRecorder/ResolutionRecorder 一致)
# falsified: 已证否封存; real_world_validated: 实战 ≥3 验过的不轻易翻盘
_FROZEN_STATUSES = ("falsified", "real_world_validated")


@dataclass
class ChallengeQueueEntry:
    """一条假设排序后的位置."""
    hypothesis_id: str
    priority_score: int
    priority_reasons: list[str] = field(default_factory=list)
    hypothesis_dict: dict | None = None  # 原 dict 便于调用方拿 statement


@dataclass
class ChallengeQueueResult:
    """排序后的全队列结果."""
    ranked: list[ChallengeQueueEntry] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (id, reason)
    total_input: int = 0

    @property
    def summary(self) -> str:
        if not self.ranked:
            return f"input {self.total_input}, ranked 0"
        top_score = self.ranked[0].priority_score
        return (
            f"input {self.total_input}, ranked {len(self.ranked)}, "
            f"skipped {len(self.skipped)}, top_score={top_score}"
        )


class HypothesisChallengeQueue:
    """按 V1 metadata 给假设排"优先怀疑顺序" (schema §三步骤 1-2)."""

    def rank(
        self,
        hypotheses: list[dict],
        problem_context: dict | None = None,
        focus_count: int | None = None,
        depended_by_threshold: int = 3,
        skip_frozen: bool = True,
    ) -> ChallengeQueueResult:
        """据 V1 metadata 排序 + 截前 N.

        Args:
            hypotheses: 假设 dict list (V1 schema, 含 confidence_level/risk_if_wrong/
                verification_status/applies_to/dependent_hypotheses).
            problem_context: 当前问题上下文, 例 {"applies_to": "worker"}. 命中假设 applies_to
                时触发 b 类优先 (untested + 命中). None 时 b 不触发.
            focus_count: 截前 N. None 返全部.
            depended_by_threshold: c 类触发阈值. 默认 3 (按 schema §三步骤 2.c).
            skip_frozen: 默认 True (V7 加) — 跳过 verification_status='falsified' /
                'real_world_validated' 假设. 跟 ChallengeRecorder/ResolutionRecorder 一致 —
                这些已封存假设不该再质疑. False 时把 falsified 假设也排进 (轻微优先复审).

        Returns:
            ChallengeQueueResult, ranked 按 score 降序排.
        """
        result = ChallengeQueueResult(total_input=len(hypotheses))

        # 先建 depended_by 反向图: 谁依赖谁
        depended_by_count: dict[str, int] = {}
        for hyp in hypotheses:
            if not isinstance(hyp, dict):
                continue
            deps = hyp.get("dependent_hypotheses") or []
            for dep_id in deps:
                if isinstance(dep_id, str):
                    depended_by_count[dep_id] = depended_by_count.get(dep_id, 0) + 1

        ctx_applies_to = (problem_context or {}).get("applies_to") or ""

        entries: list[ChallengeQueueEntry] = []
        for hyp in hypotheses:
            if not isinstance(hyp, dict):
                result.skipped.append(("<non-dict>", f"非 dict: {type(hyp).__name__}"))
                continue
            hid = hyp.get("id")
            if not hid:
                result.skipped.append(("<no-id>", "缺 id"))
                continue

            # V7: skip_frozen 跳过已 frozen 状态假设
            verification_status = hyp.get("verification_status", "untested")
            if skip_frozen and verification_status in _FROZEN_STATUSES:
                result.skipped.append((
                    hid,
                    f"verification_status='{verification_status}' 已封存 (跳过质疑队列)"
                ))
                continue

            score = 0
            reasons: list[str] = []

            # a 类: confidence=low + risk=high
            if hyp.get("confidence_level") == "low" and hyp.get("risk_if_wrong") == "high":
                score += _WEIGHT_A_LOW_HIGH
                reasons.append(
                    f"a: 新生成未验证 + 影响重大 (confidence=low + risk=high) +{_WEIGHT_A_LOW_HIGH}"
                )

            # b 类: untested + applies_to 命中
            applies_to = hyp.get("applies_to") or ""
            if (hyp.get("verification_status") == "untested"
                    and ctx_applies_to
                    and applies_to == ctx_applies_to):
                score += _WEIGHT_B_UNTESTED_MATCH
                reasons.append(
                    f"b: untested 且 applies_to='{applies_to}' 跟问题上下文匹 +{_WEIGHT_B_UNTESTED_MATCH}"
                )

            # c 类: depended_by ≥ 阈值
            dep_count = depended_by_count.get(hid, 0)
            if dep_count >= depended_by_threshold:
                bonus = _WEIGHT_C_DEPENDED_BY * dep_count
                score += bonus
                reasons.append(
                    f"c: 被 {dep_count} 条假设依赖 (基础假设, 一崩多崩) +{bonus}"
                )

            # falsified 轻微优先
            if hyp.get("verification_status") == "falsified":
                score += _WEIGHT_FALSIFIED
                reasons.append(f"falsified — 已证否复审 +{_WEIGHT_FALSIFIED}")

            entries.append(ChallengeQueueEntry(
                hypothesis_id=hid,
                priority_score=score,
                priority_reasons=reasons,
                hypothesis_dict=hyp,
            ))

        # 按 score 降序, 同 score 按 id 升序 (稳定)
        entries.sort(key=lambda e: (-e.priority_score, e.hypothesis_id))

        if focus_count is not None and focus_count >= 0:
            entries = entries[:focus_count]
        result.ranked = entries
        return result


def rank_hypothesis_challenge_queue(
    hypotheses: list[dict],
    problem_context: dict | None = None,
    focus_count: int | None = None,
) -> ChallengeQueueResult:
    """便捷入口."""
    return HypothesisChallengeQueue().rank(
        hypotheses, problem_context=problem_context, focus_count=focus_count
    )
