# [OMNI] origin=claude-code domain=omnicompany/repair ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:core.repair.patch_merger.combiner.py"
"""PatchMergerWorker — Repair Team Worker (Router 修复分组 · #6).

Worker 协议:
  FORMAT_IN  = diag.repair.tags-patch
  FORMAT_OUT = diag.repair.patch-plan

职责: 合并 desc_diff / fail_diff / tags_diff 为单一 diff 字符串。
"""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


class PatchMergerWorker(Worker):
    """合并 desc_diff / fail_diff / tags_diff 为单一 diff 字符串。

    策略: 顺序拼接, 每段之间加空行分隔。
    空 diff (对应无该类型问题) 直接跳过。
    """

    DESCRIPTION = (
        "合并 R-01/R-05/R-07 三个专属规划器各自生成的 diff 为单一提案，"
        "无问题的类型对应 diff 为 None，直接跳过"
    )
    FORMAT_IN = "diag.repair.tags-patch"
    FORMAT_OUT = "diag.repair.patch-plan"

    def run(self, input_data: Any) -> Verdict:
        router_class: str = input_data.get("router_class", "")
        parts: list[str] = []
        for key in ("desc_diff", "fail_diff", "tags_diff"):
            d = input_data.get(key)
            if d and d.strip():
                parts.append(d.strip())

        if not parts:
            return Verdict(kind=VerdictKind.PASS, confidence=1.0,
                           output={**input_data, "diff": None},
                           diagnosis=f"PatchMerger: {router_class} 无有效 diff")

        merged = "\n\n".join(parts)
        return Verdict(kind=VerdictKind.PASS, confidence=1.0,
                       output={**input_data, "diff": merged},
                       diagnosis=f"PatchMerger: {router_class} 合并 {len(parts)} 段 diff")
