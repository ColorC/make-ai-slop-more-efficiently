# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/tools ts=2026-05-07T11:00:00Z type=router status=active agent=ai-ide
# [OMNI] summary="RecordHypothesisChallengeTool — 包 HypothesisChallengeRecorder 作 agent 可调的 SingleToolRouter. ChallengeDiagnosticAgent 调它记 challenge_log + status='challenged'"
# [OMNI] why="V3 ChallengeAgent 实施 — schema §三步骤 3 工具部分. ChallengeRecorder 是纯函数, 包 SingleToolRouter 才能挂 agent. 工具读写 yaml IO (跟 ChallengeRecorder 不读不写不同), agent 用工具落档"
# [OMNI] tags=tool,doctor,challenge-record,structured,V3
# [OMNI] material_id="material:diagnosis.doctor.tools.record_hypothesis_challenge.py"
"""RecordHypothesisChallengeTool · 假设质疑落档工具.

ChallengeDiagnosticAgent 调本工具:
- 读目标假设 yaml
- 调 HypothesisChallengeRecorder.record (含 frozen status 拒 等所有边界)
- 写回 yaml (含 challenge_log 累加 + status='challenged')

跟 ChallengeRecorder 区别: ChallengeRecorder 是纯函数 (在 builders/ 子包), 不读写盘.
本工具是 SingleToolRouter 包装 (在 tools/ 子包), 接 agent 真做 IO.
"""
from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import yaml

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)
from omnicompany.packages.services._diagnosis.doctor.builders.hypothesis_challenge_recorder import (
    HypothesisChallengeRecorder,
)


def _project_root() -> Path:
    here = Path(__file__).resolve()
    for p in (here, *here.parents):
        if (p / "src" / "omnicompany").is_dir() and (p / "docs").is_dir():
            return p
    return here.parents[6] if len(here.parents) > 6 else here.parent


_PROJECT_ROOT = _project_root()


class RecordHypothesisChallengeTool(SingleToolRouter):
    """落档假设质疑 — 改 yaml status='challenged' + 追加 challenge_log."""

    TOOL_NAME: ClassVar[str] = "record_hypothesis_challenge"
    DESCRIPTION: ClassVar[str] = (
        "Record a challenge against a hypothesis. "
        "Reads the hypothesis yaml, appends challenge_log entry, sets status='challenged', writes back. "
        "Rejects challenge if verification_status is 'falsified' or 'real_world_validated' (frozen, schema §三步骤 4). "
        "Use this when challenge is reasonable but not yet falsified — for falsified verdict use record_hypothesis_resolution. "
        "Returns: {recorded, hypothesis_id, status_before, status_after, log_count}."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "hypothesis_yaml_path": {
                "type": "string",
                "description": "Path to the hypothesis yaml (relative to project root, e.g. 'data/services/doctor/hypotheses/H-2026-05-06-001.yaml')",
            },
            "challenge_reason": {
                "type": "string",
                "description": "One sentence explaining why this hypothesis is being challenged. Required.",
            },
            "source": {
                "type": "string",
                "description": "Source of challenge: red_green_test / historical_instance / standards_authority / manual",
                "default": "manual",
            },
            "challenger_id": {
                "type": "string",
                "description": "Who is challenging. Default 'ai-ide'.",
                "default": "ai-ide",
            },
        },
        "required": ["hypothesis_yaml_path", "challenge_reason"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False  # 写 yaml, 非并发安全
    IS_READONLY: ClassVar[bool] = False

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        hyp_path = (args.get("hypothesis_yaml_path") or "").strip()
        reason = (args.get("challenge_reason") or "").strip()
        if not hyp_path:
            raise ToolExecutionError("hypothesis_yaml_path 必填")
        if not reason:
            raise ToolExecutionError("challenge_reason 必填 (一句话)")

        # 解析路径 (相对项目根)
        full_path = (_PROJECT_ROOT / hyp_path).resolve()
        try:
            full_path.relative_to(_PROJECT_ROOT)
        except ValueError:
            raise ToolExecutionError(f"hypothesis_yaml_path 必须在项目根内: {hyp_path}")

        if not full_path.exists():
            raise ToolExecutionError(f"假设 yaml 不存在: {hyp_path}")

        try:
            with full_path.open(encoding="utf-8") as f:
                hyp_dict = yaml.safe_load(f)
        except Exception as e:
            raise ToolExecutionError(f"读 yaml 失败: {e}")

        if not isinstance(hyp_dict, dict):
            raise ToolExecutionError(f"yaml 顶层非 dict: {type(hyp_dict).__name__}")

        recorder = HypothesisChallengeRecorder()
        result = recorder.record(
            hyp_dict, reason,
            source=args.get("source") or "manual",
            challenger_id=args.get("challenger_id") or "ai-ide",
        )

        if not result.recorded:
            return (
                f"NOT_RECORDED hypothesis_id={result.hypothesis_id} | "
                f"reason: {result.skip_reason}"
            )

        # 写回 yaml
        try:
            with full_path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(result.upgraded_dict, f, allow_unicode=True, sort_keys=False)
        except Exception as e:
            raise ToolExecutionError(f"写 yaml 失败: {e}")

        log_count = len(result.upgraded_dict.get("challenge_log") or [])
        return (
            f"RECORDED hypothesis_id={result.hypothesis_id} | "
            f"status: {result.new_log_entry.get('status_before')} → 'challenged' | "
            f"log_count={log_count} | "
            f"yaml: {hyp_path}"
        )
