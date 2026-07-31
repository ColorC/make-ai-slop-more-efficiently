# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-25T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.material.health_record_aggregator.py"
"""MaterialHealthWriterWorker — Format 健康档案聚合 (HARD).

**契约变更 #02 (2026-04-25)**: 去 health_score/health_grade · severity 归一.
- 不打分
- 用 health_record_v2.build_health_record
- 保留 format_def / extracted / sig_diff_ok 域字段

Worker 协议:
  FORMAT_IN  = doctor.material.checks
  FORMAT_OUT = doctor.material.health-record
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._diagnosis.doctor.health_record_v2 import build_health_record
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import (
    ARCHIVE_AVAILABLE,
    HealthArchive,
    REGISTRY_ARCHIVE_DIR,
    logger,
    make_format_snapshot,
    write_proximity_snapshot,
)


_KNOWN_CHECK_KEYS = [
    "check_five_element",
    "check_tag_coverage",
    "check_parent_chain",
    "check_composite_format",
    "check_example_presence",
    "check_llm_audit",
]


class MaterialHealthWriterWorker(Worker):
    """汇总所有检查结果, 生成 v2 Format 健康档案 (不打分)."""

    DESCRIPTION = "汇总 Format 检查结果, severity 归一 critical/major/minor, 生成 v2 健康档案 (无分数/等级)"
    FORMAT_IN = "doctor.material.checks"
    FORMAT_OUT = "doctor.material.health-record"

    def run(self, input_data: Any) -> Verdict:
        material_id: str = input_data["material_id"]
        extracted: dict = input_data.get("extracted", {})
        checks = [input_data[k] for k in _KNOWN_CHECK_KEYS if k in input_data]
        format_obj: dict = extracted.get("format_obj", {})

        # sig_diff 失败 → 在 checks 前插 critical 占位
        sig_diff_ok = input_data.get("sig_diff_ok", True)
        enriched_checks = list(checks)
        if not sig_diff_ok:
            enriched_checks.insert(0, {
                "check": "sig_diff",
                "passed": False,
                "severity": "CRITICAL",
                "observation": f"Format '{material_id}' 未找到定义, 无法完整诊断",
            })

        format_def = {
            k: format_obj[k]
            for k in ("id", "name", "description", "parent", "tags", "examples", "json_schema")
            if format_obj.get(k) is not None
        }

        health_record = build_health_record(
            enriched_checks,
            summary_base=f"Format '{material_id}' ",
            failure_repr="observation",
            material_id=material_id,
            source_root=input_data.get("source_root", ""),
            format_def=format_def,
            sig_diff_ok=sig_diff_ok,
            extracted=extracted,
        )

        self._save_format_health(material_id, health_record, extracted, input_data)

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output=health_record,
            diagnosis=(
                f"HealthWriter: {material_id} verdict={health_record['verdict']} "
                f"counts={health_record['counts']}"
            ),
        )

    def _save_format_health(
        self, material_id: str, health_record: dict, extracted: dict, input_data: dict
    ) -> None:
        """中央 + 就近双写格式健康档案 (静默失败)."""
        if not ARCHIVE_AVAILABLE:
            return
        try:
            source_root = input_data.get("source_root", "")
            defined_in = extracted.get("defined_in", "")
            fmt_source_file = (
                str(Path(source_root).parent / defined_in)
                if (source_root and defined_in) else source_root
            )
            archive = HealthArchive(REGISTRY_ARCHIVE_DIR)
            snapshot = make_format_snapshot(f"format:{material_id}", health_record, fmt_source_file, archive)
            write_proximity_snapshot(fmt_source_file, "formats", material_id, snapshot)
        except Exception as e:
            logger.debug("HealthArchive write skipped for %s: %s", material_id, e)
