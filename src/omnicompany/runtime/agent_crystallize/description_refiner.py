# [OMNI] origin=claude-code domain=runtime/agent_crystallize/description_refiner ts=2026-04-15T00:00:00Z
# [OMNI] material_id="material:runtime.agent_crystallize.description_refiner.llm_proposer.py"
"""DescriptionRefiner — LLM 生成 DESCRIPTION 改进候选.

输入: agent 的 trace (工具使用 / 外部访问 / 最终答案预览) + 当前 DESCRIPTION
输出: 一条候选 SpecPatch, 含改进后的 DESCRIPTION 文本

LLM 调用 1 次, caller=info_audit.description_refiner (不触发 piggyback).
"""
from __future__ import annotations

import json
import re
from typing import Any

from .protocol import (
    AgentLoopTrace,
    CrystallizerObservation,
    ExperienceCrystallizer,
    SpecPatch,
)


_MODEL = "qwen3.6-plus"

_SYSTEM = """你是 OmniCompany Router 规范优化师.

你会看到一个 Router (LAP 节点) 的:
  1. 当前 DESCRIPTION / FORMAT_IN / FORMAT_OUT
  2. 该节点以 AgentNodeLoop 模式运行时的工具使用模式 + 最终答案预览

你的任务: 判断当前 DESCRIPTION 是否足够准确地描述了该节点**实际**需要做什么、
依赖什么. 如不够, 提出一个改进后的 DESCRIPTION.

改进原则:
  - 不要美化 / 扩词, 只补"当前 DESCRIPTION 漏说的实际依赖".
  - 不要超过 300 字.
  - 若当前 DESCRIPTION 已经足够, 返回 needs_update=false, proposed_description=空.

严格 JSON 输出 (无 markdown 围栏):

{
  "needs_update": true | false,
  "reasoning": "1-2 句话说为什么需要/不需要改",
  "proposed_description": "改进后的 DESCRIPTION, 空串表示不改",
  "key_additions": ["列出几个新增强调的点 (bullet)"]
}
"""


class DescriptionRefiner:
    """基于 agent trace, LLM 生成 DESCRIPTION 改进候选."""

    name = "description"

    def observe(self, trace: AgentLoopTrace) -> CrystallizerObservation:
        from collections import Counter
        tool_counts = Counter(tc.name for tc in trace.tool_calls)
        facts: dict[str, Any] = {
            "router_class": trace.router_class,
            "node_id": trace.node_id,
            "current_description": trace.description,
            "format_in": trace.format_in,
            "format_out": trace.format_out,
            "total_turns": trace.total_turns,
            "tool_usage": dict(tool_counts),
            "external_node_accesses": trace.external_node_accesses,
            "final_answer_preview": trace.final_answer_preview[:800],
        }
        return CrystallizerObservation(
            crystallizer=self.name,
            facts=facts,
            narrative=f"采集 {trace.router_class} 的 agent trace 用于 DESCRIPTION 精化.",
        )

    def propose(
        self,
        observation: CrystallizerObservation,
        downstream_eval: dict[str, Any],
    ) -> list[SpecPatch]:
        f = observation.facts
        if not f.get("current_description"):
            return []

        user_msg = f"""## Router 信息

- Router 类: {f.get('router_class')}
- node_id: {f.get('node_id')}
- FORMAT_IN: {f.get('format_in')}
- FORMAT_OUT: {f.get('format_out')}
- 当前 DESCRIPTION: {f.get('current_description')}

## Agent 运行观察

- 总轮数: {f.get('total_turns')}
- 工具使用: {f.get('tool_usage')}
- 访问的外部节点输出: {f.get('external_node_accesses')}

## 下游评价

{json.dumps(downstream_eval, ensure_ascii=False)}

## 最终答案预览 (前 800 字)

{f.get('final_answer_preview', '')}

请判断当前 DESCRIPTION 是否需要改进, 按 system 指令返回 JSON."""

        try:
            from omnicompany.runtime.llm.llm import LLMClient
            client = LLMClient(model=_MODEL, role="runtime_main", max_tokens=1024)
            resp = client.call(
                messages=[{"role": "user", "content": user_msg}],
                system=_SYSTEM,
                info_audit=False,  # 不叠加 piggyback
                caller="info_audit.description_refiner",
            )
            raw = ""
            for b in getattr(resp, "content", []) or []:
                if getattr(b, "type", "") == "text":
                    raw = getattr(b, "text", "") or ""
                    break
            raw = raw.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-z]*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw.strip())
            data = json.loads(raw)
        except Exception:
            return []

        if not data.get("needs_update"):
            return []

        proposed = (data.get("proposed_description") or "").strip()
        if not proposed:
            return []

        patch = SpecPatch(
            crystallizer=self.name,
            target_router=f.get("router_class", "?") or "?",
            patch_type="description_refine",
            title="DESCRIPTION 改进建议",
            rationale=data.get("reasoning", ""),
            current_value=f.get("current_description"),
            proposed_value=proposed,
            evidence=data.get("key_additions", []),
            confidence=0.55,
        )
        # Self-judge 准入门槛: 用工具白名单 + 阈值双重过滤
        actual_tools = list(f.get("tool_usage", {}).keys())
        try:
            from .patch_self_judge import run_patch_self_judge
            judgment = run_patch_self_judge(
                patch,
                format_in=f.get("format_in", ""),
                format_out=f.get("format_out", ""),
                current_description=f.get("current_description", ""),
                actual_tools=actual_tools,
            )
            if not judgment["pass"]:
                import logging
                logging.getLogger(__name__).info(
                    "[DescriptionRefiner] patch rejected by self-judge: %s",
                    judgment.get("reject_reason", "?"),
                )
                return []
            # 用 self-judge score 更新 confidence
            patch.confidence = round(judgment["score"] * 0.8, 2)  # 稍微折扣
        except Exception:
            pass  # self-judge 失败不阻断主路径
        return [patch]


_: ExperienceCrystallizer = DescriptionRefiner()  # type check
