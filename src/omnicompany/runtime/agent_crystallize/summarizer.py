# [OMNI] origin=claude-code domain=runtime/agent_crystallize/summarizer ts=2026-04-15T00:00:00Z
# [OMNI] material_id="material:runtime.agent_crystallize.trace_summarizer.recorder.py"
"""TraceSummarizer — 最简 crystallizer, 只记录工具使用模式.

不产生 patch (只记录), 是后续 crystallizer 的原料, 也供人类复盘.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .protocol import (
    AgentLoopTrace,
    CrystallizerObservation,
    ExperienceCrystallizer,
    SpecPatch,
)


class TraceSummarizer:
    """记录 agent 调了哪些工具 / 用了多少轮 / 访问了哪些外部节点输出.

    产出 1 条 observation, **不生成 patch** (返回空列表).
    作用: 给其他 crystallizer 供原料, 同时落盘供人类复盘.
    """

    name = "trace"

    def observe(self, trace: AgentLoopTrace) -> CrystallizerObservation:
        tool_counter = Counter(tc.name for tc in trace.tool_calls)
        err_calls = [tc for tc in trace.tool_calls if tc.error]

        # 同 tool 被以相同 args 调多次 → 可能提示"需要反复查同类信息"
        arg_signatures: Counter[str] = Counter()
        for tc in trace.tool_calls:
            # args 签名: name + 前两个 key 的 值 repr (截断)
            sorted_keys = sorted(tc.args.keys())[:2]
            sig_bits = [tc.name] + [f"{k}={str(tc.args.get(k))[:40]}" for k in sorted_keys]
            arg_signatures["||".join(sig_bits)] += 1
        repeated_args = {sig: cnt for sig, cnt in arg_signatures.items() if cnt >= 2}

        facts: dict[str, Any] = {
            "total_turns": trace.total_turns,
            "finished_reason": trace.finished_reason,
            "total_tool_calls": len(trace.tool_calls),
            "tool_usage_counts": dict(tool_counter),
            "error_tool_count": len(err_calls),
            "external_node_accesses": trace.external_node_accesses,
            "upstream_input_keys": trace.upstream_input_keys,
            "repeated_arg_signatures": repeated_args,
            "tokens": {
                "input": trace.total_input_tokens,
                "output": trace.total_output_tokens,
            },
        }

        narrative_parts = [
            f"Agent {trace.router_class} (node={trace.node_id}) 跑了 {trace.total_turns} 轮, "
            f"终止于 {trace.finished_reason}.",
        ]
        if tool_counter:
            top3 = tool_counter.most_common(3)
            narrative_parts.append(
                "Top tool 使用: " + ", ".join(f"{n}×{c}" for n, c in top3) + "."
            )
        if repeated_args:
            narrative_parts.append(
                f"重复参数签名 {len(repeated_args)} 个 (>=2 次), 可能提示反复查同类信息."
            )
        if trace.external_node_accesses:
            narrative_parts.append(
                f"访问了其他节点输出: {trace.external_node_accesses}."
            )
        if err_calls:
            narrative_parts.append(f"{len(err_calls)} 次工具调用出错.")

        return CrystallizerObservation(
            crystallizer=self.name,
            facts=facts,
            narrative=" ".join(narrative_parts),
        )

    def propose(
        self,
        observation: CrystallizerObservation,
        downstream_eval: dict[str, Any],
    ) -> list[SpecPatch]:
        # 纯记录型, 不产出 patch
        return []


_: ExperienceCrystallizer = TraceSummarizer()  # type check
