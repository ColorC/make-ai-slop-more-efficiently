# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.material.five_element_checker.py"
"""MaterialFiveElementCheckWorker — Format 五要素 (HARD, Stage 3 Clean Migration 2026-04-22).

Worker 协议:
  FORMAT_IN  = doctor.material.extracted
  FORMAT_OUT = doctor.material.check.five-element

诊断目标: 检查 Material (Format 对象)五要素是否完整:
  1. id 含域前缀 (domain.something)
  2. name 字段非空
  3. description 字段非空
  4. examples 或 json_schema 非空 ([PLANNED] 格式豁免)
  5. tags 非空列表
"""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import DOMAIN_PATTERN


class MaterialFiveElementCheckWorker(Worker):
    """检查 Material (Format 对象)五要素完整性 (id 含域前缀 / name / description / examples|json_schema / tags)."""

    DESCRIPTION = "检查 Material (Format 对象)五要素: id 域前缀 / name / description / examples / tags 均非空"
    FORMAT_IN = "doctor.material.extracted"
    FORMAT_OUT = "doctor.material.check.five-element"

    def run(self, input_data: Any) -> Verdict:
        material_id: str = input_data["material_id"]
        extracted = input_data.get("extracted", {})
        format_obj: dict = extracted.get("format_obj", {})

        desc = format_obj.get("description") or ""
        tags = format_obj.get("tags") or []
        is_planned = "[PLANNED" in desc.upper() or "planned" in [t.lower() for t in tags]

        sub_checks = []

        # 1. id 含域前缀
        has_domain = bool(DOMAIN_PATTERN.match(material_id))
        sub_checks.append(("id 含域前缀", has_domain,
                           f"'{material_id}' 应以 'domain.something' 形式"))

        # 2. name 非空
        name = format_obj.get("name") or ""
        has_name = bool(name.strip())
        sub_checks.append(("name 非空", has_name,
                           f"name='{name}'" if has_name else "name 字段缺失或为空"))

        # 3. description 非空
        has_desc = bool(desc.strip())
        sub_checks.append(("description 非空", has_desc,
                           f"description 存在 ({len(desc)} 字符)" if has_desc else "description 字段缺失或为空"))

        # 4. examples 非空列表 OR json_schema 非空 (两者均可满足类型说明要求)
        examples = format_obj.get("examples")
        if examples is None:
            examples = []
        json_schema = format_obj.get("json_schema")
        has_examples = isinstance(examples, list) and len(examples) > 0
        has_schema = isinstance(json_schema, dict) and len(json_schema) > 0
        has_type_info = has_examples or has_schema
        if is_planned and not has_type_info:
            sub_checks.append(("examples/json_schema 非空", True, "[PLANNED] 格式豁免示例要求"))
        else:
            detail_ok = (
                f"共 {len(examples)} 个示例" if has_examples
                else f"json_schema 存在 ({len(json_schema)} 个顶层字段)" if has_schema
                else "examples 和 json_schema 均为空 (需至少提供一种类型说明)"
            )
            sub_checks.append(("examples/json_schema 非空", has_type_info, detail_ok))

        # 5. tags 非空列表
        has_tags = isinstance(tags, list) and len(tags) > 0
        sub_checks.append(("tags 非空列表", has_tags,
                           f"tags={tags}" if has_tags else "tags 为空列表或缺失"))

        passed_count = sum(1 for _, ok, _ in sub_checks if ok)
        all_pass = passed_count == len(sub_checks)

        check_result = {
            "check": "five_element",
            "passed": all_pass,
            "severity": "HIGH" if not all_pass else "INFO",
            "detail": f"{passed_count}/{len(sub_checks)} 要素通过",
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
                "extracted": input_data.get("extracted", {}),
                "check_five_element": check_result,
            },
            diagnosis=f"FiveElementCheck: {passed_count}/{len(sub_checks)} passed",
        )
