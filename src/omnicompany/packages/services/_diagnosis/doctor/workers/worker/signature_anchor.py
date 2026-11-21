# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.worker.signature_existence_anchor.py"
"""WorkerSignatureAnchor — Router 存在性 + 元数据 Anchor (HARD, Stage 3 2026-04-22).

Worker 协议:
  FORMAT_IN  = diag.worker.extracted
  FORMAT_OUT = diag.worker.sig-checked

诊断目标: 校验 Router 类是否存在且有基础元数据 (DESCRIPTION / FORMAT_IN / FORMAT_OUT).

PASS: 全部存在 → 创建 diag.worker.acc 累加器, 进入完整诊断链
FAIL: 任一缺失 → EMIT 最小健康档案 (短路, 跳过后续节点)

规则对照:
  - R-01 DESCRIPTION 非空
  - R-02 FORMAT_IN / FORMAT_OUT 是字符串字面量 (f-string 不可静态分析, 附 HIGH warning)
"""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


class WorkerSignatureAnchor(Worker):
    """Anchor: 校验 Router 元数据 (DESCRIPTION / FORMAT_IN / FORMAT_OUT) 存在."""

    DESCRIPTION = "校验 Router 类存在且有 DESCRIPTION/FORMAT_IN/FORMAT_OUT; 任一缺失则 EMIT 最小健康档案"
    FORMAT_IN = "diag.worker.extracted"
    FORMAT_OUT = "diag.worker.sig-checked"
    INPUT_KEYS = ["worker_class", "found", "description", "format_in", "format_out"]

    def run(self, input_data: Any) -> Verdict:
        worker_class: str = input_data["worker_class"]
        found: bool = input_data.get("found", False)
        description: str | None = input_data.get("description")
        format_in: str | None = input_data.get("format_in")
        format_out: str | None = input_data.get("format_out")
        format_in_kind: str = input_data.get("format_in_kind", "literal")
        format_out_kind: str = input_data.get("format_out_kind", "literal")

        truly_missing: list[str] = []
        fstring_fields: list[str] = []

        if not found:
            truly_missing.append("class_not_found")
        if not description:
            truly_missing.append("DESCRIPTION_empty")
        if not format_in:
            if format_in_kind == "fstring":
                fstring_fields.append("FORMAT_IN")
            else:
                truly_missing.append("FORMAT_IN_empty")
        if not format_out:
            if format_out_kind == "fstring":
                fstring_fields.append("FORMAT_OUT")
            else:
                truly_missing.append("FORMAT_OUT_empty")

        if truly_missing:
            detail_msg = "; ".join(truly_missing)
            obs_parts = []
            if "class_not_found" in truly_missing:
                obs_parts.append(f"Router 类 '{worker_class}' 在目标文件中不存在")
            else:
                if "DESCRIPTION_empty" in truly_missing:
                    obs_parts.append("DESCRIPTION 为空")
                if "FORMAT_IN_empty" in truly_missing:
                    obs_parts.append("FORMAT_IN 为空")
                if "FORMAT_OUT_empty" in truly_missing:
                    obs_parts.append("FORMAT_OUT 为空")
            observation = "; ".join(obs_parts)

            return Verdict(
                kind=VerdictKind.FAIL,
                confidence=1.0,
                output={
                    "worker_class": worker_class,
                    "source_file": input_data.get("source_file", ""),
                    "source_root": input_data.get("source_root", ""),
                    "extracted": input_data,
                    "sig_ok": False,
                    "checks": [{
                        "check": "signature",
                        "standard": "R-01/R-02 基础元数据存在性",
                        "severity": "CRITICAL",
                        "passed": False,
                        "observation": observation,
                        "detail": {"missing": truly_missing},
                    }],
                },
                diagnosis=f"RouterSignature FAIL: {worker_class} — {detail_msg}",
            )

        desc_len = len(description or "")
        fin_display = "f-string" if format_in_kind == "fstring" else f"'{format_in}'"
        fout_display = "f-string" if format_out_kind == "fstring" else f"'{format_out}'"
        observation = (
            f"DESCRIPTION {desc_len} chars ✓; "
            f"FORMAT_IN={fin_display} ✓; "
            f"FORMAT_OUT={fout_display} ✓"
        )

        sig_checks: list[dict] = [{
            "check": "signature",
            "standard": "R-01/R-02 基础元数据存在性",
            "severity": "CRITICAL",
            "passed": True,
            "observation": observation,
            "detail": None,
        }]
        if fstring_fields:
            sig_checks.append({
                "check": "R-02-fstring",
                "standard": "FORMAT_IN/OUT 必须是字符串字面量, f-string 不可静态分析",
                "severity": "HIGH",
                "passed": False,
                "observation": (
                    f"{' / '.join(fstring_fields)} 使用 f-string, "
                    "Doctor 无法做上下游搜索和契约验证"
                ),
                "detail": {"fstring_fields": fstring_fields},
            })

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output={
                "worker_class": worker_class,
                "source_file": input_data.get("source_file", ""),
                "source_root": input_data.get("source_root", ""),
                "extracted": input_data,
                "sig_ok": True,
                "checks": sig_checks,
            },
            diagnosis=f"RouterSignature PASS: {worker_class}",
        )
