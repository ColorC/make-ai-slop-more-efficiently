# [OMNI] origin=claude-code domain=services/skill_importer ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:utility.skill_importer.structure_analyzer.llm.py"
"""StructureAnalysisWorker — LLM 归纳 skill 结构 (SOFT, Stage 3 Clean Migration 2026-04-22)."""
from __future__ import annotations

import json
import re
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.llm.llm import LLMClient


_ANALYSIS_PROMPT = """You are acting as an OmniCompany LAP Compiler.
Your job is to translate a Claude Code Skill into a structured pipeline blueprint.

Input sections (SKILL.md):
{sections_json}

Reference files (excerpts):
{reference_contents}

Scripts files (excerpts):
{scripts_contents}

Extract the following **strictly** as JSON (no markdown fencing):

{{
  "skill_purpose": "一句话描述此 skill 的核心用途",
  "skill_domain": "suggested pipeline domain name, snake_case",
  "skill_pipeline_name": "suggested pipeline name, hyphen-case",
  "nodes": [
    {{
      "id": "snake_case_id",
      "title": "Human readable title",
      "kind": "ANCHOR" | "TRANSFORMER" | "SCATTER",
      "is_llm": true | false,
      "uses_user_interaction": true | false,
      "uses_subagent_parallelism": true | false,
      "input_description": "1 sentence on what data comes in",
      "output_description": "1 sentence on what data goes out",
      "knowledge_points": ["exact rules / URLs / schema snippets from source, no paraphrasing"],
      "tools_required": ["tool names or file paths"]
    }}
  ],
  "dag_edges": [
    {{"source": "id_1", "target": "id_2", "condition": "PASS"}},
    {{"source": "id_2", "target": "id_1", "condition": "FAIL"}}
  ],
  "special_constraints": [
    "strict rules the skill enforces, e.g. '主 agent 在 subagent 运行期间不读子 agent 负责的文件'"
  ],
  "coverage_expectations": "如何衡量 '这个 skill 跑完算不算合格' 的标准"
}}

Rules:
- Order nodes by logical execution sequence
- Do NOT drop technical details; knowledge_points 必须包含原文的精确规则 / 阈值 / URL
- If skill has "subagent parallel analysis", set uses_subagent_parallelism=true and mark that node kind=SCATTER
- dag_edges use PASS for nominal flow, FAIL for retry loops
- Output ONLY the JSON object, no markdown code fence, no explanatory text"""


def _parse_json_loose(text: str) -> Any:
    """容忍 ``` fence 和裸换行的宽松 JSON 解析."""
    stripped = text.strip()

    try:
        return json.loads(stripped, strict=False)
    except Exception:
        pass

    fence_match = re.match(r"```(?:\w+)?\s*\n", stripped)
    if fence_match:
        body = stripped[fence_match.end():]
        if body.endswith("```"):
            body = body[:-3].rstrip()
        try:
            return json.loads(body, strict=False)
        except Exception:
            pass

    first = stripped.find("{")
    last = stripped.rfind("}")
    if first >= 0 and last > first:
        try:
            return json.loads(stripped[first:last + 1], strict=False)
        except Exception:
            pass

    return None


class StructureAnalysisWorker(Worker):
    DESCRIPTION = (
        "基于 parsed sections 让 LLM 归纳 skill 的核心结构: 目的 / 节点列表 / "
        "依赖边 / 特殊约束 / 覆盖预期。输出是结构化 JSON, 供下游 FormatInference 和 "
        "RequirementDraft 消费。"
    )
    FORMAT_IN = "skill_importer.parsed_sections"
    FORMAT_OUT = "skill_importer.skill_structure"

    def run(self, data: dict) -> Verdict:
        sections_for_llm = [
            {"title": s["title"], "level": s["level"], "body_preview": s["body"]}
            for s in data.get("sections", [])
        ]
        prompt = _ANALYSIS_PROMPT.format(
            sections_json=json.dumps(sections_for_llm, ensure_ascii=False),
            reference_contents=json.dumps(
                data.get("reference_contents", {}),
                ensure_ascii=False,
            ),
            scripts_contents=json.dumps(data.get("scripts_contents", {}), ensure_ascii=False),
        )

        try:
            client = LLMClient(role="ide_agent", max_tokens=8192, tools=[])
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL, output=data,
                diagnosis=f"LLMClient init failed: {e}",
            )

        try:
            response = client.call(
                messages=[{"role": "user", "content": prompt}],
                system="Output ONLY JSON, no markdown fence, no prose.",
            )
            text = "".join(
                b.text for b in response.content if hasattr(b, "text")
            ).strip()
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL, output=data,
                diagnosis=f"LLM call failed: {e}",
            )

        parsed = _parse_json_loose(text)
        if parsed is None or not isinstance(parsed, dict):
            return Verdict(
                kind=VerdictKind.FAIL, output=data,
                diagnosis=f"LLM JSON parse failed; first 300 chars: {text[:300]}",
            )

        if not parsed.get("nodes"):
            return Verdict(
                kind=VerdictKind.FAIL, output=data,
                diagnosis="LLM 未返回任何节点, 分析失败",
            )

        out = dict(data)
        out.update({
            "skill_purpose": parsed.get("skill_purpose", ""),
            "skill_domain": parsed.get("skill_domain", "imported"),
            "skill_pipeline_name": parsed.get(
                "skill_pipeline_name", data["skill_name"].replace("_", "-")
            ),
            "nodes": parsed["nodes"],
            "dag_edges": parsed.get("dag_edges", []),
            "special_constraints": parsed.get("special_constraints", []),
            "coverage_expectations": parsed.get("coverage_expectations", ""),
        })
        return Verdict(
            kind=VerdictKind.PASS,
            output=out,
            confidence=0.85,
            diagnosis=(
                f"analyzed: {len(out['nodes'])} nodes, "
                f"{len(out['dag_edges'])} edges, "
                f"{len(out['special_constraints'])} constraints"
            ),
            granted_tags=["domain.skill_importer", "stage.analyzed"],
        )
