# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.material.naming_convention_checker.py"
"""MaterialTagCoverageWorker — ID 命名 + 域标签覆盖 (HARD, Stage 3 Clean Migration 2026-04-22).

Worker 协议:
  FORMAT_IN  = doctor.material.extracted
  FORMAT_OUT = doctor.material.check.tag-coverage

诊断目标: 检查 Format 命名和标签覆盖:
  1. ID 全小写无非法字符 (允许下划线、连字符、点)
  2. tags 含 ID 域前缀匹配的域标签 (如 "guardian.*" 应有 "guardian" tag)
"""
from __future__ import annotations

import re
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


class MaterialTagCoverageWorker(Worker):
    """检查 Format 命名和标签覆盖."""

    DESCRIPTION = "检查 Format ID 命名规范 (小写合法字符) 与 tags 域标签覆盖"
    FORMAT_IN = "doctor.material.extracted"
    FORMAT_OUT = "doctor.material.check.tag-coverage"

    def run(self, input_data: Any) -> Verdict:
        material_id: str = input_data["material_id"]
        extracted = input_data.get("extracted", {})
        format_obj: dict = extracted.get("format_obj", {})
        tags: list = format_obj.get("tags") or []

        sub_checks = []

        # 1. ID 全小写 + 合法字符 (允许下划线)
        legal_chars = bool(re.match(r"^[a-z0-9._\-]+$", material_id))
        sub_checks.append(("ID 全小写无非法字符", legal_chars,
                           f"'{material_id}' 含大写或非法字符" if not legal_chars else "OK"))

        # 2. tags 含域标签 (连字符/下划线等价)
        domain = material_id.split(".")[0] if "." in material_id else ""
        if domain:
            domain_norm = domain.replace("-", "_")
            has_domain_tag = any(
                domain in tag or domain_norm in tag.replace("-", "_")
                for tag in tags
            )
        else:
            has_domain_tag = True
        sub_checks.append(("tags 含域标签", has_domain_tag,
                           f"tags={tags} 中未见 '{domain}' 或 '{domain.replace('-','_')}'" if not has_domain_tag
                           else f"OK (tag 含 '{domain}')"))

        passed_count = sum(1 for _, ok, _ in sub_checks if ok)
        all_pass = passed_count == len(sub_checks)

        check_result = {
            "check": "tag_coverage",
            "passed": all_pass,
            "severity": "MEDIUM" if not all_pass else "INFO",
            "detail": f"{passed_count}/{len(sub_checks)} 命名/标签规范通过",
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
                "check_tag_coverage": check_result,
            },
            diagnosis=f"TagCoverage: {passed_count}/{len(sub_checks)} passed",
        )
