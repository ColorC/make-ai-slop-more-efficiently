# [OMNI] origin=claude-code domain=omnifactory/lap_auditor ts=2026-04-21T00:00:00Z type=router
# [OMNI] material_id="material:diagnosis.lap_auditor.report_printer.worker.python"
"""ReportFormatterWorker — lap_auditor 报告格式化 (Stage 3 独立文件).

Worker 协议:
  FORMAT_IN  = lap_auditor.report
  FORMAT_OUT = lap_auditor.done

职责: 打印 LLM 审计报告到控制台, 保留 report 字段供调用方读取。
"""
from __future__ import annotations

from typing import Any

from omnifactory.packages.services._core.omnicompany import Worker
from omnifactory.protocol.anchor import Verdict, VerdictKind


class ReportFormatterWorker(Worker):
    """打印并保存审计报告。"""

    DESCRIPTION = (
        "将 SpecAuditorWorker 输出的 Markdown 报告格式化打印到控制台，"
        "并在 output 中保留报告内容供调用方读取。"
    )
    FORMAT_IN = "lap_auditor.report"
    FORMAT_OUT = "lap_auditor.done"

    def run(self, input_data: Any) -> Verdict:
        report = input_data.get("report", "")
        target = input_data.get("target_path", "unknown")

        separator = "=" * 46
        print(f"\n\n{separator}")
        print(f"  LAP AUDIT REPORT FOR: {target}")
        print(f"{separator}\n")
        print(report)
        print(f"\n{separator}\n")

        return Verdict(
            kind=VerdictKind.PASS,
            output={"summary": "Audit complete", "report": report},
        )
