# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/tools ts=2026-05-05T21:30:00Z type=config status=active agent=ai-ide-current
# [OMNI] summary="doctor 业务工具集合 — 注册到 TOOL_REGISTRY 让 doctor agent SPEC.tools 字符串名引用"
# [OMNI] tags=tools,doctor,registry
# [OMNI] material_id="material:diagnosis.doctor.tools.aggregate.exports.py"
"""doctor 业务工具集合.

import 本包即触发所有 doctor 业务 SingleToolRouter 子类 register_tool 到全局
TOOL_REGISTRY. doctor agent (spec_diagnostic 等) 在 SPEC.tools 用字符串名引用.
"""
from __future__ import annotations

from omnicompany.packages.services._core.agent.configurable import register_tool

from .write_finding import WriteFindingRouter
from .submit_verdict import SubmitVerdictRouter
from .write_hypothesis import WriteHypothesisRouter
from .submit_derivation_report import SubmitDerivationReportRouter
from .git_log_tool import GitLogTool
from .record_hypothesis_challenge import RecordHypothesisChallengeTool
from .record_hypothesis_resolution import RecordHypothesisResolutionTool
from .rank_hypothesis_challenge_queue import RankHypothesisChallengeQueueTool

# 注册 doctor 业务工具到 TOOL_REGISTRY
register_tool("write_finding", WriteFindingRouter)
register_tool("submit_verdict", SubmitVerdictRouter)
register_tool("write_hypothesis", WriteHypothesisRouter)
register_tool("submit_derivation_report", SubmitDerivationReportRouter)
register_tool("git_log", GitLogTool)
register_tool("record_hypothesis_challenge", RecordHypothesisChallengeTool)
register_tool("record_hypothesis_resolution", RecordHypothesisResolutionTool)
register_tool("rank_hypothesis_challenge_queue", RankHypothesisChallengeQueueTool)


__all__ = [
    "WriteFindingRouter",
    "SubmitVerdictRouter",
    "WriteHypothesisRouter",
    "SubmitDerivationReportRouter",
    "GitLogTool",
    "RecordHypothesisChallengeTool",
    "RecordHypothesisResolutionTool",
    "RankHypothesisChallengeQueueTool",
]
