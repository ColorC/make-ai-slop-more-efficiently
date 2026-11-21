# [OMNI] origin=claude-code domain=services/trace_induction ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:learning.trace_induction.pipeline_index_registrar.worker.py"
"""RegistrarWorker — 确定性注册到 pipeline_index (HARD, Stage 3 Clean Migration 2026-04-22)."""
from __future__ import annotations

import re
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


def _extract_tags(purpose: str, domain: str) -> list[str]:
    """从 purpose 和 domain 提取简单 tags."""
    tags = []
    if domain:
        tags.append(domain)
    words = re.split(r'[\s,;，；。\-_/]+', purpose)
    for w in words:
        if len(w) >= 2 and w.isascii():
            tags.append(w.lower())
    return tags[:10]


class RegistrarWorker(Worker):
    """将 WF 产出注册到 pipeline_index 语义索引。

    输入 ti.wf-result: {pipeline_name, package_path, files, purpose, domain}
    输出 ti.done: {pipeline_name, package_path, registered, status, summary}
    """

    FORMAT_IN = "ti.wf-result"
    FORMAT_OUT = "ti.done"
    DESCRIPTION = (
        "将 Workflow Factory 产出的 pipeline 元信息注册到 pipeline_index 表。"
        "注册后可被 Pre-execution Search 检索，供后续任务复用。确定性操作。"
    )

    def run(self, input_data: Any) -> Verdict:
        pipeline_name = input_data.get("pipeline_name", "")
        package_path = input_data.get("package_path", "")
        purpose = input_data.get("purpose", "")
        domain = input_data.get("domain", "")
        db_path = input_data.get("db_path", "data/intent_traces.db")

        if not pipeline_name:
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis="pipeline_name 为空，无法注册",
            )

        from omnicompany.runtime.storage.experience_search import register_pipeline_to_index
        try:
            register_pipeline_to_index(
                db_path,
                pipeline_name=pipeline_name,
                purpose=purpose,
                domain=domain or None,
                tags=_extract_tags(purpose, domain),
                source="trace_induction",
                test_status="untested",
            )
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis=f"注册失败: {e}",
            )

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "pipeline_name": pipeline_name,
                "package_path": package_path,
                "registered": True,
                "status": "registered",
                "summary": f"Pipeline '{pipeline_name}' 已注册 (domain={domain})",
            },
            diagnosis=f"已注册: {pipeline_name}",
            confidence=1.0,
        )
