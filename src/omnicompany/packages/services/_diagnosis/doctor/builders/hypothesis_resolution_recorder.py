# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/builders ts=2026-05-07T13:00:00Z type=router status=active agent=ai-ide
# [OMNI] summary="HypothesisResolutionRecorder — 假设证否落档纯函数 (跟 ChallengeRecorder 同形态). 修 V3 留下的债 — V3 时 resolution 逻辑混在 SingleToolRouter._execute"
# [OMNI] why="V4-1 还债. ChallengeRecorder 是纯函数+SingleToolRouter 两层, ResolutionRecorder V3 立时偷懒只写一层 (混 IO 跟逻辑). 提到 builders/ 跟 ChallengeRecorder 一致"
# [OMNI] tags=builder,resolution-recorder,hypothesis-state,structured,no-llm,V4
# [OMNI] material_id="material:diagnosis.doctor.builders.hypothesis_resolution_recorder.py"
"""HypothesisResolutionRecorder · 假设证否记录器 (V0).

跟 ChallengeRecorder 同形态 — 不用 LLM, 纯函数, 不直接落盘.

按 schema §三步骤 4: 证否成立 → status=falsified, 落 resolution.

跟 ChallengeRecorder 区别:
- ChallengeRecorder: 提质疑, status='active'/'red_green_pass' → 'challenged' (可逆)
- ResolutionRecorder: 终态, status → 'falsified' (不可逆 — 已证否的封存)

V0 行为:
- 输入: 假设 dict + falsifying_evidence + method + falsifier_id
- 输出: 新 dict (含 resolution 字段 + status='falsified' + verification_status='falsified')
- 不写盘 (调用方决定 IO)
- 不修原 dict (返新 dict)

V0 边界 (按 schema §三步骤 3-4):
- **必先 challenged**: status 必须是 'challenged' 才允许 resolution
  (按顺序: untested → challenge → challenged → resolution → falsified)
- **frozen 拒**: verification_status='falsified' / 'real_world_validated' 拒
  (已证否的不该再证一次; 实战验过的不该轻易翻盘)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# 证否方法字面合法值 (按 schema §三步骤 4 列的 3 类 + manual)
_VALID_METHODS = ("red_green_test", "historical_instance", "standards_authority", "manual")

# 不允许再 resolution 的状态
_FROZEN_STATUSES = ("falsified", "real_world_validated")


@dataclass
class ResolutionRecordResult:
    """一次 record_resolution 调用的结果."""
    falsified: bool                    # 真落档 falsified 了吗
    hypothesis_id: str
    upgraded_dict: dict | None = None  # 修过的 dict (falsified=True 时才有)
    skip_reason: str = ""              # falsified=False 时的原因
    resolution: dict | None = None     # 本次落档的 resolution dict


class HypothesisResolutionRecorder:
    """落档假设证否 — 修 dict 加 resolution + status='falsified'.

    schema §三步骤 4 工具部分纯函数实施.
    """

    def record(
        self,
        hypothesis: dict,
        falsifying_evidence: str,
        method: str = "manual",
        falsifier_id: str = "ai-ide",
    ) -> ResolutionRecordResult:
        """记一次证否落档.

        Args:
            hypothesis: 假设 dict (V1 schema). 必含 id. 必须已 status='challenged'.
            falsifying_evidence: 1-3 句具体证据 (cite file:line / 标准 / fixture). 必填.
            method: 证否方法, 默认 'manual'. 合法值见 _VALID_METHODS.
            falsifier_id: 证否者 ID (例 'ai-ide' / 'agent:challenge_diagnostic').

        Returns:
            ResolutionRecordResult. falsified=False 时 skip_reason 解释.
        """
        if not isinstance(hypothesis, dict):
            return ResolutionRecordResult(
                falsified=False, hypothesis_id="<non-dict>",
                skip_reason=f"输入非 dict: {type(hypothesis).__name__}",
            )
        hid = hypothesis.get("id")
        if not hid:
            return ResolutionRecordResult(
                falsified=False, hypothesis_id="<no-id>",
                skip_reason="假设缺 id",
            )

        evidence = (falsifying_evidence or "").strip()
        if not evidence:
            return ResolutionRecordResult(
                falsified=False, hypothesis_id=hid,
                skip_reason="falsifying_evidence 必填 (1-3 句具体证据)",
            )

        # frozen 拒
        verification_status = hypothesis.get("verification_status", "untested")
        if verification_status in _FROZEN_STATUSES:
            return ResolutionRecordResult(
                falsified=False, hypothesis_id=hid,
                skip_reason=(
                    f"verification_status='{verification_status}' 已封存 — "
                    f"{('已证否过' if verification_status == 'falsified' else '实战验过的不该轻易翻盘')}"
                ),
            )

        # 必先 challenged (按 schema §三步骤 3-4 顺序)
        current_status = hypothesis.get("status", "active")
        if current_status != "challenged":
            return ResolutionRecordResult(
                falsified=False, hypothesis_id=hid,
                skip_reason=(
                    f"status='{current_status}' (必须先 'challenged' 才能 resolution). "
                    f"先调 ChallengeRecorder 提质疑收证据, 再调本工具证否. "
                    f"按 schema §三步骤 3-4 顺序."
                ),
            )

        method_clean = (method or "manual").strip()
        # 软警告: 非标 method 允许但留 note
        method_warning = ""
        if method_clean not in _VALID_METHODS:
            method_warning = f"非标 method '{method_clean}', 合法值: {_VALID_METHODS}"

        resolution = {
            "ts": _now_iso(),
            "outcome": "falsified",
            "falsifying_evidence": evidence,
            "method": method_clean,
            "falsifier_id": (falsifier_id or "ai-ide").strip(),
            "status_before": current_status,
            "verification_status_before": verification_status,
        }
        if method_warning:
            resolution["method_warning"] = method_warning

        # 拷贝 + 修
        upgraded = dict(hypothesis)
        upgraded["resolution"] = resolution
        upgraded["status"] = "falsified"
        upgraded["verification_status"] = "falsified"

        return ResolutionRecordResult(
            falsified=True,
            hypothesis_id=hid,
            upgraded_dict=upgraded,
            resolution=resolution,
        )


def record_hypothesis_resolution(
    hypothesis: dict,
    falsifying_evidence: str,
    method: str = "manual",
    falsifier_id: str = "ai-ide",
) -> ResolutionRecordResult:
    """便捷入口."""
    return HypothesisResolutionRecorder().record(
        hypothesis, falsifying_evidence, method=method, falsifier_id=falsifier_id,
    )
