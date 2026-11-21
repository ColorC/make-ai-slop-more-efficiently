# [OMNI] origin=claude-code domain=runtime/agent_crystallize/format_edge_inferrer ts=2026-04-15T00:00:00Z
# [OMNI] material_id="material:runtime.agent_crystallize.format_edge_inferrer.heuristic_engine.py"
"""FormatEdgeInferrer — 推断缺失的 Format 组件 (composite edge).

启发式:
  - Agent 反复访问了某个其他节点的输出 (external_node_accesses 非空)
    → 目标节点的 FORMAT_IN 可能应该是 composite Format, 显式包含那个源
  - Agent 调了同名工具 >= 3 次去捞外部数据 → 信号

不做 LLM 调用, 纯规则. 产出候选 patch, 交人审.
"""
from __future__ import annotations

from typing import Any

from .protocol import (
    AgentLoopTrace,
    CrystallizerObservation,
    ExperienceCrystallizer,
    SpecPatch,
)


_REPEATED_ACCESS_THRESHOLD = 3


class FormatEdgeInferrer:
    """若 agent 反复访问特定外部信息源, 建议把该信息源声明为 FORMAT_IN 的 component."""

    name = "format"

    def observe(self, trace: AgentLoopTrace) -> CrystallizerObservation:
        # 重复访问信号:
        #  (a) external_node_accesses 明示的其他节点
        #  (b) 同一 tool 被调多次 (如 local_read 多次)
        #  (c) 反复访问同一目录前缀 (agent 在单一子系统里深挖)
        from collections import Counter
        tool_counts = Counter(tc.name for tc in trace.tool_calls)
        high_repeat_tools = [n for n, c in tool_counts.items() if c >= _REPEATED_ACCESS_THRESHOLD]

        # 路径前缀分析: 扫描 tool args 里的 path/file 字段, 抽公共前缀
        path_prefixes: Counter[str] = Counter()
        for tc in trace.tool_calls:
            for k in ("path", "file", "file_path", "target", "filename"):
                v = tc.args.get(k)
                if isinstance(v, str) and v:
                    # 取前两级目录作前缀
                    parts = v.replace("\\", "/").split("/")
                    if len(parts) >= 2:
                        prefix = "/".join(parts[:2])
                        path_prefixes[prefix] += 1
                    break
        hot_prefixes = [(p, c) for p, c in path_prefixes.most_common(5) if c >= _REPEATED_ACCESS_THRESHOLD]

        facts: dict[str, Any] = {
            "router_class": trace.router_class,
            "external_node_accesses": trace.external_node_accesses,
            "high_repeat_tools": high_repeat_tools,
            "high_repeat_tool_counts": {n: tool_counts[n] for n in high_repeat_tools},
            "hot_path_prefixes": hot_prefixes,
            "format_in": trace.format_in,
        }
        narrative_parts = []
        if trace.external_node_accesses:
            narrative_parts.append(
                f"Agent 明示访问了其他节点: {trace.external_node_accesses}."
            )
        if high_repeat_tools:
            narrative_parts.append(
                f"高频重复工具: {high_repeat_tools} (≥{_REPEATED_ACCESS_THRESHOLD} 次)."
            )
        if hot_prefixes:
            narrative_parts.append(
                f"热点路径前缀: {hot_prefixes} — 提示该节点深挖特定子系统."
            )
        return CrystallizerObservation(
            crystallizer=self.name,
            facts=facts,
            narrative=" ".join(narrative_parts) or "未观察到显著外部依赖模式.",
        )

    def propose(
        self,
        observation: CrystallizerObservation,
        downstream_eval: dict[str, Any],
    ) -> list[SpecPatch]:
        facts = observation.facts
        patches: list[SpecPatch] = []

        external_nodes: list[str] = facts.get("external_node_accesses") or []
        fmt_in = facts.get("format_in") or ""
        high_repeat = facts.get("high_repeat_tools") or []

        if external_nodes:
            # 建议: format_in 升级为 composite, 显式 components 包含 external_nodes 的输出 Format
            evidence = [f"Agent 访问的外部节点: {n}" for n in external_nodes]
            evidence.append(f"当前 FORMAT_IN: {fmt_in}")
            patches.append(SpecPatch(
                crystallizer=self.name,
                target_router=facts.get("router_class", "?") or "?",
                patch_type="format_components_add",
                title="建议 FORMAT_IN 升级为 composite",
                rationale=(
                    f"Agent 在运行中反复访问了 {external_nodes} 的输出, "
                    f"说明当前 FORMAT_IN ({fmt_in or '空'}) 结构性缺失这些依赖. "
                    f"将其显式声明为 Format.components, 让 pipeline 在编译期保证供给."
                ),
                current_value=fmt_in,
                proposed_value={
                    "format_id_new": f"{fmt_in}.composite",
                    "components": [fmt_in] + external_nodes if fmt_in else external_nodes,
                },
                evidence=evidence,
                confidence=min(0.5 + 0.15 * len(external_nodes), 0.9),
            ))

        if high_repeat:
            counts = facts.get("high_repeat_tool_counts") or {}
            patches.append(SpecPatch(
                crystallizer=self.name,
                target_router=facts.get("router_class", "?") or "?",
                patch_type="tool_manual",
                title=f"{high_repeat[0]} 等工具高频使用",
                rationale=(
                    f"这些工具被 agent 调用 {counts}, "
                    f"提示该节点实际依赖的信息密度高于 FORMAT_IN 传递. "
                    f"考虑: (1) 前置节点预加载; (2) DESCRIPTION 注明典型工具模式."
                ),
                current_value=None,
                proposed_value={"high_repeat_tools": counts},
                evidence=[f"{n}: {counts[n]} 次" for n in high_repeat],
                confidence=0.45,
            ))

        hot_prefixes = facts.get("hot_path_prefixes") or []
        if hot_prefixes:
            patches.append(SpecPatch(
                crystallizer=self.name,
                target_router=facts.get("router_class", "?") or "?",
                patch_type="format_components_add",
                title=f"热点目录 {hot_prefixes[0][0]} 应显式入参",
                rationale=(
                    f"Agent 在路径前缀 {[p for p,_ in hot_prefixes]} 下反复访问, "
                    f"提示这些是节点实际工作的聚焦子系统, 但当前 FORMAT_IN "
                    f"({fmt_in or '空'}) 未携带指引. "
                    f"考虑: (1) 上游 RepoMapper 预计算该子系统摘要; "
                    f"(2) FORMAT_IN 增 `focus_prefixes` 字段显式声明探索边界."
                ),
                current_value=fmt_in,
                proposed_value={
                    "suggested_focus_prefixes": [p for p, _ in hot_prefixes],
                    "access_counts": dict(hot_prefixes),
                },
                evidence=[f"{p}: {c} 次访问" for p, c in hot_prefixes],
                confidence=0.55,
            ))

        return patches


_: ExperienceCrystallizer = FormatEdgeInferrer()  # type check
