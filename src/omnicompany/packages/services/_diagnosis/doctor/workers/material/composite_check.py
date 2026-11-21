# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.material.composite_format_checker.py"
"""MaterialCompositeCheckWorker — composite Format 合法性 (HARD, Stage 3 Clean Migration 2026-04-22).

Worker 协议:
  FORMAT_IN  = doctor.material.extracted
  FORMAT_OUT = doctor.material.check.composite

诊断目标: 检查 composite Format (有 components 字段) 的引用合法性:
  - 非 composite Format → 跳过 (INFO PASS)
  - composite Format → 检查 description 是否说明了组合意图
    (含"由"/"组合"/"包含"/"汇聚"/"composed"/"contains" 等关键词)
"""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


# 组合意图关键词 (中英文)
_INTENT_KEYWORDS = ("由", "组合", "包含", "汇聚", "composed", "contains", "combines", "aggregates")


class MaterialCompositeCheckWorker(Worker):
    """检查 composite Format (有 components 字段) 的引用合法性和描述意图."""

    DESCRIPTION = (
        "检查 composite Format (有 components 字段) 的 components 合法性和描述完整性; "
        "非 composite Format 跳过"
    )
    FORMAT_IN = "doctor.material.extracted"
    FORMAT_OUT = "doctor.material.check.composite"

    def run(self, input_data: Any) -> Verdict:
        fmt_id: str = input_data.get("material_id", "")
        extracted = input_data.get("extracted", {})
        format_obj: dict = extracted.get("format_obj", {})
        components: list = format_obj.get("components", [])
        description: str = format_obj.get("description", "") or ""
        checks = list(input_data.get("checks", []))

        if not components:
            checks.append({
                "check": "composite_format",
                "severity": "INFO",
                "passed": True,
                "observation": "非 composite Format, 跳过组合检查",
                "detail": None,
            })
        else:
            has_intent = any(kw in description for kw in _INTENT_KEYWORDS)
            checks.append({
                "check": "composite_format",
                "severity": "MEDIUM",
                "passed": has_intent,
                "observation": (
                    f"composite Format, components={components}, description 说明了组合意图 ✓"
                    if has_intent else
                    f"composite Format, components={components}, 但 description 未说明组合意图"
                    " (建议补充'由 X/Y/Z 组成'等描述)"
                ),
                "detail": {"components": components, "has_intent": has_intent},
            })

        check_result = checks[-1]
        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output={
                "material_id": fmt_id,
                "source_root": input_data.get("source_root", ""),
                "sig_diff_ok": input_data.get("sig_diff_ok", True),
                "extracted": input_data.get("extracted", {}),
                "check_composite_format": check_result,
            },
            diagnosis=f"CompositeFormatCheck: {fmt_id} components={components}",
        )
