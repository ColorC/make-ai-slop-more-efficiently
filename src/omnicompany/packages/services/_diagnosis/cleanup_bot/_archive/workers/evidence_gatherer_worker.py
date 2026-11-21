# [OMNI] origin=claude-code domain=omnifactory/cleanup_bot ts=2026-04-21T00:00:00Z type=router
# [OMNI] material_id="material:diagnosis.cleanup_bot.evidence_gatherer_disk_scanner_worker.py"
"""EvidenceGathererWorker — cleanup_bot 路径扫描 (Stage 3 独立文件).

Worker 协议:
  FORMAT_IN  = cleanup.input
  FORMAT_OUT = cleanup.evidence

职责: 按 keyword 扫描 root_dir (max_depth=5), 收集可疑路径供 LLM 分析。
"""
from __future__ import annotations

import os
from typing import Any

from omnifactory.packages.services._core.omnicompany import Worker
from omnifactory.protocol.anchor import Verdict, VerdictKind


class EvidenceGathererWorker(Worker):
    """扫描指定根目录下包含指定关键词的所有物理路径。"""

    DESCRIPTION = (
        "接收 root_dir 和 keyword，使用 os.walk（限制深度5层）扫描磁盘，"
        "收集路径名或文件名包含关键词的所有条目，供 LLM 分析合法性。"
    )
    FORMAT_IN = "cleanup.input"
    FORMAT_OUT = "cleanup.evidence"

    def run(self, input_data: Any) -> Verdict:
        root_dir = input_data.get("root_dir", "E:\\")
        keyword = input_data.get("keyword", "")
        if not keyword:
            return Verdict(kind=VerdictKind.FAIL, diagnosis="输入不满足要求: keyword 不能为空")

        found_paths: list[str] = []
        max_depth = 5
        base_depth = root_dir.rstrip(os.sep).count(os.sep)

        try:
            for root, dirs, files in os.walk(root_dir):
                current_depth = root.count(os.sep)
                if current_depth - base_depth > max_depth:
                    dirs.clear()
                    continue
                for d in dirs:
                    if keyword.lower() in d.lower():
                        found_paths.append(os.path.join(root, d))
                for f in files:
                    if keyword.lower() in f.lower():
                        found_paths.append(os.path.join(root, f))
        except PermissionError:
            pass

        if not found_paths:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"未找到包含关键词 '{keyword}' 的路径",
            )

        return Verdict(
            kind=VerdictKind.PASS,
            diagnosis=f"找到 {len(found_paths)} 个可疑路径",
            output={
                "keyword": keyword,
                "evidence_str": "\n".join(found_paths),
                "raw_paths": found_paths,
            },
        )
