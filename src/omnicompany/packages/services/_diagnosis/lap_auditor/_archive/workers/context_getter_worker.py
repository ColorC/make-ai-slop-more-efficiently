# [OMNI] origin=claude-code domain=omnifactory/lap_auditor ts=2026-04-21T00:00:00Z type=router
# [OMNI] material_id="material:diagnosis.lap_auditor.code_context_collector.worker.python"
"""ContextGetterWorker — lap_auditor 代码上下文采集 (Stage 3 独立文件).

Worker 协议:
  FORMAT_IN  = lap_auditor.input
  FORMAT_OUT = lap_auditor.context

职责: 读取 target_path 下所有 .py 文件, 拼装为带文件名标题的代码上下文字符串。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from omnifactory.packages.services._core.omnicompany import Worker
from omnifactory.protocol.anchor import Verdict, VerdictKind


class ContextGetterWorker(Worker):
    """读取指定路径下的所有 .py 源码，拼装为上下文。"""

    DESCRIPTION = (
        "读取 target_path 下所有 .py 文件（或单个 .py 文件），"
        "拼装为带文件名标题的代码上下文字符串，供 LLM 审计节点使用。"
    )
    FORMAT_IN = "lap_auditor.input"
    FORMAT_OUT = "lap_auditor.context"

    def run(self, input_data: Any) -> Verdict:
        target_path = input_data.get("target_path")
        if not target_path:
            return Verdict(kind=VerdictKind.FAIL, diagnosis="输入不满足要求: 缺少 target_path")

        tgt = Path(target_path)
        if not tgt.exists():
            return Verdict(kind=VerdictKind.FAIL, diagnosis=f"路径不存在: {tgt}")

        code_files = []
        if tgt.is_file():
            if tgt.suffix == ".py":
                code_files.append((tgt.name, tgt.read_text(encoding="utf-8", errors="replace")))
        else:
            for p in tgt.rglob("*.py"):
                if p.is_file():
                    code_files.append((
                        str(p.relative_to(tgt)),
                        p.read_text(encoding="utf-8", errors="replace"),
                    ))

        if not code_files:
            return Verdict(kind=VerdictKind.FAIL, diagnosis=f"未找到 Python 文件: {tgt}")

        context_str = ""
        for name, content in code_files:
            context_str += f"\n--- File: {name} ---\n```python\n{content}\n```\n"

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "target_path": str(tgt),
                "code_context": context_str,
            },
        )
