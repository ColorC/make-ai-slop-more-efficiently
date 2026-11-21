# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/scanners ts=2026-05-07T01:25:00Z type=config status=active agent=ai-ide
# [OMNI] summary="doctor 客观诊断设施扫描器集合 — 不基于 LLM 的硬扫描器"
# [OMNI] why="meta_diagnosis_pipeline_plan §阶段 6. 用户 5/6 立: 诊断设施=客观代码不基于 LLM"
# [OMNI] tags=scanners,objective,doctor
# [OMNI] material_id="material:diagnosis.doctor.scanners.aggregate.exports.py"
"""doctor 客观诊断设施扫描器.

跟现 5 agent 区别:
- 5 agent: LLM 驱动, 软语义诊断
- 这层 scanners: 纯 Python 代码扫规则, 客观判定, 不调 LLM

提供:
- FacilityScanner: 扫一个 team 的 tests/ dogfood/ .omni/ 看现有验证设施
- WorkPatternAnomalyScanner: 拿 git log 检测 5 类工作模式异常 (阶段 7)
- PromptPatchPileScanner: 扫 prompt md 数 AP-024 patch-pile 3 类信号 (V1 2026-05-07)
"""
from __future__ import annotations

from .facility_scanner import FacilityScanner, scan_team_test_facilities
from .work_pattern_scanner import WorkPatternAnomalyScanner, scan_work_pattern_anomalies
from .prompt_patch_pile_scanner import (
    PromptPatchPileScanner,
    scan_prompt_patch_pile,
    PromptPatchPileSignal,
    PromptPatchPileScanResult,
)

__all__ = [
    "FacilityScanner",
    "scan_team_test_facilities",
    "WorkPatternAnomalyScanner",
    "scan_work_pattern_anomalies",
    "PromptPatchPileScanner",
    "scan_prompt_patch_pile",
    "PromptPatchPileSignal",
    "PromptPatchPileScanResult",
]
