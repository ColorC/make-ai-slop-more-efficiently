# [OMNI] origin=claude-code domain=omnifactory/cleanup_bot ts=2026-04-21T00:00:00Z type=router
# [OMNI] material_id="material:diagnosis.cleanup_bot.rollback_planner_report_printer_worker.py"
"""RollbackPlannerWorker — cleanup_bot 回滚计划打印 (Stage 3 独立文件).

Worker 协议:
  FORMAT_IN  = cleanup.plan
  FORMAT_OUT = cleanup.done

职责: 打印 PowerShell 清理脚本到控制台 (只打印, 不自动执行; 安全降级)。
"""
from __future__ import annotations

from typing import Any

from omnifactory.packages.services._core.omnicompany import Worker
from omnifactory.protocol.anchor import Verdict, VerdictKind


class RollbackPlannerWorker(Worker):
    """打印回滚计划（不自动执行删除）。"""

    DESCRIPTION = (
        "将 AnomalyDetectorWorker 输出的 Markdown 报告格式化打印到控制台，"
        "提醒用户手动执行清理脚本。不自动删除任何文件（安全降级）。"
    )
    FORMAT_IN = "cleanup.plan"
    FORMAT_OUT = "cleanup.done"

    def run(self, input_data: Any) -> Verdict:
        report = input_data.get("anomaly_report", "")

        separator = "=" * 46
        print(f"\n\n{separator}")
        print("  CLEANUP BOT AUDIT REPORT")
        print(f"{separator}\n")
        print(report)
        print(f"\n{separator}\n")

        return Verdict(
            kind=VerdictKind.PASS,
            output={"summary": "Cleanup plan generated", "report": report},
        )
