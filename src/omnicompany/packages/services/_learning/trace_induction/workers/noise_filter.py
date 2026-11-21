# [OMNI] origin=claude-code domain=services/trace_induction ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:learning.trace_induction.llm_step_annotation_filter.worker.py"
"""NoiseFilterWorker — LLM 标注噪音步骤 (SOFT, Stage 3 Clean Migration 2026-04-22)."""
from __future__ import annotations

import logging
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import format_steps, parse_json_loose

logger = logging.getLogger(__name__)


_NOISE_FILTER_PROMPT = """\
以下是 agent 执行"{purpose}"时的完整操作记录。
请标注每一步是：
- ESSENTIAL（核心步骤，必须保留）
- EXPLORATION（探索/尝试，可能有价值但非必要）
- MISTAKE（走弯路/错误尝试，应排除）
- RETRY（重试之前的失败，保留最终成功版本）

注意：
- 如果某一步失败（tool_exit_ok=0）但之后有成功的重试，标记失败步为 RETRY
- agent 的 think 步骤如果包含有价值的推理，标记为 ESSENTIAL
- grep/ls 等探索性操作，如果其结果被后续步骤使用，标记为 ESSENTIAL

操作记录：
{steps_text}

输出严格 JSON（不要 markdown 代码块）：
{{"annotations": [{{"step_num": 1, "label": "ESSENTIAL", "reason": "..."}}]}}
"""


class NoiseFilterWorker(Worker):
    """LLM 标注每步为 ESSENTIAL/EXPLORATION/MISTAKE/RETRY，过滤保留核心步骤。

    输入 ti.trace-data: {traces, purpose}
    输出 ti.essential: {essential_steps, purpose, trace_count}
    """

    FORMAT_IN = "ti.trace-data"
    FORMAT_OUT = "ti.essential"
    DESCRIPTION = (
        "对每个 trace 的步骤调用 LLM 标注重要性等级。"
        "保留 ESSENTIAL 步骤，过滤 EXPLORATION/MISTAKE/RETRY。"
        "多 trace 时对每个 trace 单独过滤后合并。"
    )

    def __init__(self, *, client=None):
        self._client = client

    def run(self, input_data: Any) -> Verdict:
        traces = input_data.get("traces", {})
        purpose = input_data.get("purpose", "")

        if not traces:
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis="无 trace 数据可过滤",
            )

        all_essential = []
        for tid, steps in traces.items():
            steps_text = format_steps(steps)
            prompt = _NOISE_FILTER_PROMPT.format(purpose=purpose, steps_text=steps_text)

            try:
                resp = self._client.call(
                    messages=[{"role": "user", "content": prompt}],
                    system="你是一个操作流程分析专家。",
                )
                raw = resp.content[0].text
                data = parse_json_loose(raw)
                if not data:
                    continue

                essential_nums = {
                    a["step_num"] for a in data.get("annotations", [])
                    if a.get("label") == "ESSENTIAL"
                }
                for s in steps:
                    if s["step_num"] in essential_nums:
                        all_essential.append({**s, "_trace_id": tid})
            except Exception as e:
                logger.warning("[noise_filter] trace %s failed: %s", tid, e)
                continue

        if not all_essential:
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis="过滤后无 ESSENTIAL 步骤",
            )

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "essential_steps": all_essential,
                "purpose": purpose,
                "trace_count": input_data.get("trace_count", 1),
                "domain": input_data.get("domain", ""),
                "db_path": input_data.get("db_path", ""),
            },
            diagnosis=f"保留 {len(all_essential)} 个 ESSENTIAL 步骤",
        )
