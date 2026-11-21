# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.material.existence_anchor.py"
"""MaterialSignatureDiffWorker — Format 存在性 Anchor (HARD, Stage 3 Clean Migration 2026-04-22).

Worker 协议:
  FORMAT_IN  = doctor.material.extracted
  FORMAT_OUT = doctor.material.extracted

诊断目标: 判定指定 material_id 是否以 Format() 对象形式真正存在. 不存在则短路 EMIT
最小健康档案, 阻止下游检查链白跑.
"""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


class MaterialSignatureDiffWorker(Worker):
    """校验 Format ID 是否在某个 formats.py 中有 Format() 对象定义.

    PASS: 找到 Format() 对象 → 进入完整检查链
    FAIL: 未找到 → 直接写入最小健康档案 (short-circuit to HealthWriter)
    """

    DESCRIPTION = "校验 Format ID 是否以 Format() 对象形式定义于 formats.py; PASS 透传 extracted, FAIL 短路 EMIT 最小健康档案"
    FORMAT_IN = "doctor.material.extracted"
    FORMAT_OUT = "doctor.material.extracted"
    INPUT_KEYS = ["material_id", "found"]

    def run(self, input_data: Any) -> Verdict:
        material_id: str = input_data["material_id"]
        found: bool = input_data.get("found", False)
        format_obj: dict = input_data.get("format_obj", {})

        if not found or not format_obj:
            detail = (
                "Format ID 在所有 formats.py 中均未找到 Format() 对象定义"
                if not found else
                "找到文件但未能提取 Material (Format 对象)字段 (AST 解析失败)"
            )
            # 契约变更 #02 (2026-04-25): 不塞 health_score=0.0 / health_grade='F' 占位
            # 直接产 v2 schema 的最小 health_record · 含 1 条 critical check
            from omnicompany.packages.services._diagnosis.doctor.health_record_v2 import build_health_record
            record = build_health_record(
                [{"check": "sig_diff", "passed": False, "severity": "CRITICAL", "observation": detail}],
                summary_base=f"Format '{material_id}' ",
                failure_repr="observation",
                material_id=material_id,
                source_root=input_data.get("source_root", ""),
                sig_diff_ok=False,
            )
            return Verdict(
                kind=VerdictKind.FAIL,
                confidence=1.0,
                output=record,
                diagnosis=f"SignatureDiff FAIL: {material_id} — {detail}",
            )

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output={
                "material_id": material_id,
                "source_root": input_data.get("source_root", ""),
                "extracted": input_data,
                "sig_diff_ok": True,
            },
            diagnosis=f"SignatureDiff PASS: {material_id}",
        )
