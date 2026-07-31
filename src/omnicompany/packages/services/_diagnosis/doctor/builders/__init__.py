# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/builders ts=2026-05-07T01:55:00Z type=config status=active agent=ai-ide
# [OMNI] summary="doctor 诊断器构建器集合 — 据假设跟 facility 缺口自动产新诊断设施 (pytest skeleton / 假设型 agent prompt 等)"
# [OMNI] why="meta_diagnosis_pipeline_plan §阶段 8 + plan §一第 7 条原话埋的路: '根据健康性假设再去创建用于诊断的 agent 或者 worker'. 这是真'诊断器构建器'"
# [OMNI] tags=builders,objective,doctor
# [OMNI] material_id="material:diagnosis.doctor.builders.aggregate.exports.py"
"""doctor 诊断器构建器.

不用 LLM. 据假设跟 facility 缺口产新诊断设施.

4 类 builder (2026-05-07 加 V1Upgrader):
- PytestSkeletonBuilder: 据 team formats.py 的 Material 输出 + facility_scanner 缺失项, 产 pytest test_*.py skeleton
- LintRuleBuilder: 据 anti_pattern AP-XXX detection_strategy, 产 lint 规则 Python 函数 skeleton (V2 待)
- HypothesisAgentPromptBuilder: 据假设 yaml + 现有 agent prompt 模板, 产新假设型诊断 agent prompt
- HypothesisV1Upgrader: V0 假设 dict → V1 metadata 升级 (含 source_kind=code → 'code-derived' 类别)
"""
from __future__ import annotations

from .pytest_skeleton_builder import (
    PytestSkeletonBuilder,
    build_pytest_skeleton_for_team,
)
from .hypothesis_agent_prompt_builder import (
    HypothesisAgentPromptBuilder,
    HypothesisAgentPromptSkeleton,
)
from .hypothesis_v1_upgrader import (
    HypothesisV1Upgrader,
    HypothesisV1UpgradeResult,
    UpgradedHypothesis,
)
from .hypothesis_challenge_queue import (
    HypothesisChallengeQueue,
    ChallengeQueueResult,
    ChallengeQueueEntry,
    rank_hypothesis_challenge_queue,
)
from .hypothesis_challenge_recorder import (
    HypothesisChallengeRecorder,
    ChallengeRecordResult,
    record_hypothesis_challenge,
)
from .hypothesis_resolution_recorder import (
    HypothesisResolutionRecorder,
    ResolutionRecordResult,
    record_hypothesis_resolution,
)
from .hypothesis_confidence_auditor import (
    HypothesisConfidenceAuditor,
    ConfidenceAuditEntry,
    ConfidenceAuditResult,
    audit_hypothesis_confidence,
)

__all__ = [
    "PytestSkeletonBuilder",
    "build_pytest_skeleton_for_team",
    "HypothesisAgentPromptBuilder",
    "HypothesisAgentPromptSkeleton",
    "HypothesisV1Upgrader",
    "HypothesisV1UpgradeResult",
    "UpgradedHypothesis",
    "HypothesisChallengeQueue",
    "ChallengeQueueResult",
    "ChallengeQueueEntry",
    "rank_hypothesis_challenge_queue",
    "HypothesisChallengeRecorder",
    "ChallengeRecordResult",
    "record_hypothesis_challenge",
    "HypothesisResolutionRecorder",
    "ResolutionRecordResult",
    "record_hypothesis_resolution",
    "HypothesisConfidenceAuditor",
    "ConfidenceAuditEntry",
    "ConfidenceAuditResult",
    "audit_hypothesis_confidence",
]
