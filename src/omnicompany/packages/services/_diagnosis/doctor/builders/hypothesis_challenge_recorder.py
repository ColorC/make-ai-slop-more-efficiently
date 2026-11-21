# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/builders ts=2026-05-07T10:00:00Z type=router status=active agent=ai-ide
# [OMNI] summary="HypothesisChallengeRecorder — 给一条假设记录质疑 (写 challenge_log + status=challenged). 修 V2 步骤 3 'challenge agent 实施' 结构化部分"
# [OMNI] why="schema §三步骤 3 '提其他猜想 → 立 challenge_log 记 challenge_reason + status 改 challenged'. 步骤 3 是结构化操作不需 LLM, 拆成工具. 步骤 4 (跑反例 / 找历史 / 对照) 才需 LLM agent (留 V3)"
# [OMNI] tags=builder,challenge-recorder,hypothesis-state,structured,no-llm
# [OMNI] material_id="material:diagnosis.doctor.builders.hypothesis_challenge_recorder.py"
"""HypothesisChallengeRecorder · 假设质疑记录器 (V0).

跟 V1Upgrader / ChallengeQueue 同形态 — 不用 LLM, 纯函数, 不直接落盘.

按 schema §三步骤 3:
  对每条焦点假设, 立 challenge_log 记 challenge_reason + status 改 challenged

V0 行为:
- 输入: 假设 dict + challenge_reason + challenge_source (例 'red_green_test'/'historical_instance'/'standards_authority') + challenger_id
- 输出: 新 dict (含 challenge_log 新条目 + status='challenged')
- 不写盘 (调用方决定写回哪个 yaml)
- 不修原 dict (返新 dict)

V0 限制 (按 schema §三步骤 4 边界):
- status='falsified' 的假设不允许再 challenge — 已被证否的应当封存 (返 RecordResult skipped)
- status='real_world_validated' 的假设也不允许再 challenge — 实战 ≥3 次验过的不该轻易翻盘
  (调用方真要质疑得手工先 reset status, 或走 V3 完整证否流程)

V0 不接通:
- 不直接产 challenge agent SPEC (是另一份大工作 V3)
- 不读 yaml / 不写 yaml. 调用方处理 IO

调用方:
    rec = HypothesisChallengeRecorder()
    result = rec.record(hyp_dict, "反例 fixture 显示假设不成立",
                        source="red_green_test", challenger_id="ai-ide")
    if result.recorded:
        write_yaml(out_path, result.upgraded_dict)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# 不允许再 challenge 的状态 (按 schema §三步骤 4 边界)
_FROZEN_STATUSES = ("falsified", "real_world_validated")

# challenge_source 字面合法值 (按 schema §三步骤 4 列的 3 类)
_VALID_SOURCES = ("red_green_test", "historical_instance", "standards_authority", "manual")


@dataclass
class ChallengeRecordResult:
    """一次 record 调用的结果."""
    recorded: bool                       # 真记下来了吗 (False 表 frozen 跳过)
    hypothesis_id: str
    upgraded_dict: dict | None = None    # 修过的 dict (recorded=True 时才有)
    skip_reason: str = ""                # recorded=False 时的原因
    new_log_entry: dict | None = None    # 本次新加的 challenge_log 条目


class HypothesisChallengeRecorder:
    """记录假设质疑 — 写 challenge_log + status=challenged.

    schema §三步骤 3 工具部分实施.
    """

    def record(
        self,
        hypothesis: dict,
        challenge_reason: str,
        source: str = "manual",
        challenger_id: str = "ai-ide",
    ) -> ChallengeRecordResult:
        """记一次质疑.

        Args:
            hypothesis: 假设 dict (V1 schema). 必含 id.
            challenge_reason: 质疑理由 (一句话, 必填).
            source: 质疑来源, 默认 'manual'. 合法值见 _VALID_SOURCES.
            challenger_id: 质疑发起者 ID (例 'ai-ide' / 'agent:spec_diagnostic').

        Returns:
            ChallengeRecordResult. recorded=False 时 skip_reason 解释.
        """
        if not isinstance(hypothesis, dict):
            return ChallengeRecordResult(
                recorded=False, hypothesis_id="<non-dict>",
                skip_reason=f"输入非 dict: {type(hypothesis).__name__}",
            )
        hid = hypothesis.get("id")
        if not hid:
            return ChallengeRecordResult(
                recorded=False, hypothesis_id="<no-id>",
                skip_reason="假设缺 id",
            )

        reason = (challenge_reason or "").strip()
        if not reason:
            return ChallengeRecordResult(
                recorded=False, hypothesis_id=hid,
                skip_reason="challenge_reason 必填 (一句话)",
            )

        # frozen status 不允许再 challenge
        verification_status = hypothesis.get("verification_status", "untested")
        if verification_status in _FROZEN_STATUSES:
            return ChallengeRecordResult(
                recorded=False, hypothesis_id=hid,
                skip_reason=(
                    f"verification_status='{verification_status}' 已封存 (按 schema §三步骤 4) "
                    f"— {('falsified 已证否的应当封存' if verification_status == 'falsified' else 'real_world_validated 实战验过的不该轻易翻盘')}. "
                    f"调用方真要质疑得先 reset status 或走 V3 完整证否流程."
                ),
            )

        # 校 source 合法性 (软警告: 不在 _VALID_SOURCES 列表里允许但记 note)
        source_clean = (source or "manual").strip()
        if source_clean not in _VALID_SOURCES:
            # 不阻止, 但留 note 在 log entry
            source_warning = f"非标 source '{source_clean}', 合法值: {_VALID_SOURCES}"
        else:
            source_warning = ""

        # 构造新 log entry
        log_entry = {
            "ts": _now_iso(),
            "challenge_reason": reason,
            "source": source_clean,
            "challenger_id": (challenger_id or "ai-ide").strip(),
            "status_before": (hypothesis.get("status") or "active"),
            "verification_status_before": verification_status,
        }
        if source_warning:
            log_entry["source_warning"] = source_warning

        # 拷贝 + 修
        upgraded = dict(hypothesis)
        existing_log = list(upgraded.get("challenge_log") or [])
        existing_log.append(log_entry)
        upgraded["challenge_log"] = existing_log
        upgraded["status"] = "challenged"

        return ChallengeRecordResult(
            recorded=True,
            hypothesis_id=hid,
            upgraded_dict=upgraded,
            new_log_entry=log_entry,
        )


def record_hypothesis_challenge(
    hypothesis: dict,
    challenge_reason: str,
    source: str = "manual",
    challenger_id: str = "ai-ide",
) -> ChallengeRecordResult:
    """便捷入口."""
    return HypothesisChallengeRecorder().record(
        hypothesis, challenge_reason, source=source, challenger_id=challenger_id,
    )
