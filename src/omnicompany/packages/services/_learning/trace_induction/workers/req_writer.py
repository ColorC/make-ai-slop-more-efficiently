# [OMNI] origin=claude-code domain=services/trace_induction ts=2026-04-22T00:00:00Z type=worker
# [OMNI] OMNI-004 NOTE: run() 为 async 不可避免 — 依赖 requirement_writer.generate_requirement_with_llm
# [OMNI] material_id="material:learning.trace_induction.sop_to_requirement_converter.worker.py"
#   该函数本身是 async (需要 await llm_call), Worker 必须跟随. 合法场景, 非 AgentNodeLoop-style 违反.
"""ReqWriterWorker — LLM 生成需求文档 (SOFT, Stage 3 Clean Migration 2026-04-22)."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


def _derive_pipeline_name(purpose: str, domain: str) -> str:
    """从 purpose 推导英文 pipeline name (kebab-case ASCII)."""
    words = re.findall(r'[A-Za-z][a-z]*[A-Za-z]*', purpose)
    if not words:
        h = hashlib.md5(purpose.encode()).hexdigest()[:6]
        return f"{domain or 'auto'}-{h}"
    name = "-".join(w.lower() for w in words[:4])
    if domain:
        name = f"{domain}-{name}"
    return name


class ReqWriterWorker(Worker):
    """LLM 将 SOP 转化为 Workflow Factory 可消费的需求文档。

    输入 ti.sop: {sop, derived_from}
    输出 ti.requirement: {requirement_doc, purpose, domain}
    """

    FORMAT_IN = "ti.sop"
    FORMAT_OUT = "ti.requirement"
    DESCRIPTION = (
        "将结构化 SOP 转化为 Workflow Factory 可消费的 Markdown 需求文档。"
        "包含目标、触发场景、操作流程、数据流、错误处理、验证标准、约束。"
        "如果 LLM 不可用则降级为确定性模板填充。"
    )

    def __init__(self, *, client=None):
        self._client = client

    async def run(self, input_data: Any) -> Verdict:
        sop_dict = input_data.get("sop", {})
        derived_from = input_data.get("derived_from", [])
        domain = input_data.get("domain", "")

        if not sop_dict or not sop_dict.get("steps"):
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis="SOP 数据为空，无法生成需求文档",
            )

        from omnicompany.packages.services._learning.trace_induction.sop_extractor import _build_sop_from_json
        sop_obj = _build_sop_from_json(sop_dict, derived_from, sop_dict.get("extraction_method", ""))

        llm_call = None
        if self._client:
            async def llm_call(messages, system):
                resp = self._client.call(messages=messages, system=system)
                return resp.content[0].text

        from omnicompany.packages.services._learning.trace_induction.requirement_writer import generate_requirement_with_llm
        req_doc = await generate_requirement_with_llm(
            sop_obj, llm_call=llm_call, domain_knowledge=domain,
        )

        if not req_doc or len(req_doc) < 50:
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis="需求文档生成失败或内容过短",
            )

        purpose = sop_dict.get("purpose", "")
        pipeline_name = _derive_pipeline_name(purpose, domain)

        meta_header = (
            f"**Pipeline Name**: {pipeline_name}\n"
            f"**Domain**: {domain or 'custom'}\n\n"
        )
        req_doc = meta_header + req_doc

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "requirement_doc": req_doc,
                "purpose": purpose,
                "domain": domain,
                "trace_ids": derived_from,
                "db_path": input_data.get("db_path", ""),
            },
            diagnosis=f"需求文档 {len(req_doc)} 字符, pipeline_name={pipeline_name}",
        )
