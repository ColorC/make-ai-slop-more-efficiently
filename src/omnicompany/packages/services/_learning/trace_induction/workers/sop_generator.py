# [OMNI] origin=claude-code domain=services/trace_induction ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:learning.trace_induction.structured_sop_generator.worker.py"
"""SOPGeneratorWorker — LLM 生成结构化 SOP (SOFT, Stage 3 Clean Migration 2026-04-22)."""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import format_steps, parse_json_loose


_SOP_GEN_PROMPT = """\
以下是执行"{purpose}"时的核心操作步骤（已过滤噪音）。
请将其转化为结构化 SOP。

核心步骤：
{steps_text}

输出严格 JSON（不要 markdown 代码块）：
{{
  "purpose": "一句话描述",
  "preconditions": ["前置条件1"],
  "steps": [
    {{"id": 1, "action": "做什么", "tool": "工具名",
      "tool_args_pattern": "参数模板", "input": "输入",
      "output": "输出", "notes": "注意事项", "rules": ["规则"]}}
  ],
  "error_handling": [
    {{"condition": "错误场景", "action": "处理方式"}}
  ]
}}
"""


class SOPGeneratorWorker(Worker):
    """LLM 将核心步骤合并为结构化 SOP。

    输入 ti.essential: {essential_steps, purpose}
    输出 ti.sop: {sop, derived_from, extraction_method}
    """

    FORMAT_IN = "ti.essential"
    FORMAT_OUT = "ti.sop"
    DESCRIPTION = (
        "将过滤后的核心步骤合并为结构化 SOP。单 trace 直接转换，"
        "多 trace 提取共同模式。输出完整的 SOP dict 包含 "
        "purpose/preconditions/steps/error_handling。"
    )

    def __init__(self, *, client=None):
        self._client = client

    def run(self, input_data: Any) -> Verdict:
        essential_steps = input_data.get("essential_steps", [])
        purpose = input_data.get("purpose", "")

        if not essential_steps:
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis="无核心步骤可转化为 SOP",
            )

        steps_text = format_steps(essential_steps)
        prompt = _SOP_GEN_PROMPT.format(purpose=purpose, steps_text=steps_text)

        try:
            resp = self._client.call(
                messages=[{"role": "user", "content": prompt}],
                system="你是一位技术文档工程师。",
            )
            sop_data = parse_json_loose(resp.content[0].text)
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis=f"SOP 生成 LLM 调用失败: {e}",
            )

        if not sop_data or not sop_data.get("steps"):
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis="SOP 生成结果为空或缺少 steps",
            )

        trace_ids = list({s.get("_trace_id", "") for s in essential_steps if s.get("_trace_id")})

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "sop": sop_data,
                "derived_from": trace_ids,
                "extraction_method": "single-trace" if len(trace_ids) <= 1 else "multi-trace-merge",
                "domain": input_data.get("domain", ""),
                "db_path": input_data.get("db_path", ""),
            },
            diagnosis=f"生成 SOP: {len(sop_data.get('steps', []))} 步",
        )
