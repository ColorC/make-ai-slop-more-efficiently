# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.material.example_presence_checker.py"
"""MaterialExamplePresenceWorker — examples 质量 (HARD, Stage 3 Clean Migration 2026-04-22).

Worker 协议:
  FORMAT_IN  = doctor.material.extracted
  FORMAT_OUT = doctor.material.check.example-presence

诊断目标: 检查 Format.examples 列表质量:
  1. examples 列表非空 OR json_schema 非空 (两者之一即可)
  2. 至少一个示例是含字段的非空 dict

[PLANNED] 格式豁免 (description 含 "[PLANNED" 或 tags 含 "planned").
"""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


class MaterialExamplePresenceWorker(Worker):
    """检查 Format.examples 列表质量 ([PLANNED] 格式豁免)."""

    DESCRIPTION = "检查 Format.examples 列表非空且含有意义的示例 dict ([PLANNED] 格式豁免)"
    FORMAT_IN = "doctor.material.extracted"
    FORMAT_OUT = "doctor.material.check.example-presence"

    def run(self, input_data: Any) -> Verdict:
        material_id: str = input_data["material_id"]
        extracted = input_data.get("extracted", {})
        format_obj: dict = extracted.get("format_obj", {})
        examples: list = format_obj.get("examples") or []
        json_schema: dict = format_obj.get("json_schema") or {}
        desc: str = format_obj.get("description") or ""
        tags: list = format_obj.get("tags") or []

        # [PLANNED] 豁免
        is_planned = "[PLANNED" in desc.upper() or "planned" in [t.lower() for t in tags]
        if is_planned:
            check_result = {
                "check": "example_presence",
                "passed": True,
                "severity": "INFO",
                "detail": "[PLANNED] Format 豁免示例要求",
                "sub_checks": [],
            }
            return Verdict(
                kind=VerdictKind.PASS,
                confidence=1.0,
                output={
                    "material_id": material_id,
                    "source_root": input_data.get("source_root", ""),
                    "sig_diff_ok": input_data.get("sig_diff_ok", True),
                    "extracted": extracted,
                    "check_example_presence": check_result,
                },
                diagnosis=f"ExamplePresence: PLANNED exemption for {material_id}",
            )

        sub_checks = []
        has_schema = isinstance(json_schema, dict) and len(json_schema) > 0

        # 1. examples 非空 OR json_schema 非空
        has_examples = isinstance(examples, list) and len(examples) > 0
        has_type_info = has_examples or has_schema
        sub_checks.append(("examples 或 json_schema 非空", has_type_info,
                           f"共 {len(examples)} 个示例" if has_examples
                           else "json_schema 存在" if has_schema
                           else "examples 和 json_schema 均为空"))

        # 2. 若有 examples, 至少一个示例是含字段的 dict
        if has_examples:
            has_meaningful = any(isinstance(e, dict) and len(e) >= 1 for e in examples)
            sub_checks.append(("至少一个示例含字段", has_meaningful,
                               "示例包含有意义的字段" if has_meaningful
                               else "所有示例均为空 dict {}"))

        passed_count = sum(1 for _, ok, _ in sub_checks if ok)
        all_pass = passed_count == len(sub_checks)
        max_fields = max((len(e) for e in examples if isinstance(e, dict)), default=0)

        detail_str = (
            f"示例存在 ({len(examples)} 个, 最大字段数={max_fields})"
            if has_examples else
            "json_schema 定义存在 (替代示例)"
            if has_schema else
            "示例质量不足 (examples 和 json_schema 均为空)"
        )
        check_result = {
            "check": "example_presence",
            "passed": all_pass,
            "severity": "MEDIUM" if not all_pass else "INFO",
            "detail": detail_str,
            "sub_checks": [
                {"name": n, "passed": ok, "detail": d}
                for n, ok, d in sub_checks
            ],
        }

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output={
                "material_id": material_id,
                "source_root": input_data.get("source_root", ""),
                "sig_diff_ok": input_data.get("sig_diff_ok", True),
                "extracted": extracted,
                "check_example_presence": check_result,
            },
            diagnosis=f"ExamplePresence: {'OK' if all_pass else 'FAIL'} for {material_id}",
        )
