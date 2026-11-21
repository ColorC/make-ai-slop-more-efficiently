# [OMNI] origin=claude-code domain=runtime/agent_crystallize/trace ts=2026-04-15T00:00:00Z
# [OMNI] material_id="material:runtime.agent_crystallize.trace_snapshot.builder.py"
"""AgentNodeLoop 内部状态 → AgentLoopTrace 结构化快照.

agent loop 完成后调用, 从 self._messages + self._total_*_tokens 等提炼.
不依赖 EventBus (避免强耦合), 直接读 loop 的属性.
"""
from __future__ import annotations

from typing import Any

from .protocol import AgentLoopTrace, ToolCallRecord


def build_agent_loop_trace(
    loop: Any,
    *,
    node_id: str,
    format_in: str = "",
    format_out: str = "",
    description: str = "",
    input_data: dict[str, Any] | None = None,
    finished_reason: str = "unknown",
) -> AgentLoopTrace:
    """从一个已完成运行的 AgentNodeLoop 实例提炼 trace.

    Args:
        loop: AgentNodeLoop 实例, 已调完 run(input_data).
        node_id: 节点 id (runner 知道).
        format_in/out/description: 节点规范信息 (runner 从 node / router 取).
        input_data: 节点起手时的 input_data, 用于记录上游 keys.
        finished_reason: loop 终止原因 (从 finish 事件或 stop_reason 推断).
    """
    messages = getattr(loop, "_messages", []) or []
    # M3 v2: 优先用 outer router 类名 (set by router.run before delegating)
    router_class = getattr(loop, "_outer_router_class", None) or type(loop).__name__

    tool_calls: list[ToolCallRecord] = []
    external_accesses: set[str] = set()
    turn = 0
    final_text_parts: list[str] = []

    # messages 列表结构: [{"role": "user"|"assistant"|"tool", "content": str | list[block]}]
    # 遍历, 抽工具调用和结果
    pending_calls: dict[str, dict[str, Any]] = {}  # tool_use_id -> {name, args, turn}
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        # assistant: 文本 + tool_use block
        if role == "assistant":
            turn += 1
            if isinstance(content, str):
                final_text_parts.append(content)
            elif isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    btype = b.get("type")
                    if btype == "text":
                        final_text_parts.append(b.get("text", ""))
                    elif btype == "tool_use":
                        tc_id = b.get("id") or f"t{turn}_{len(pending_calls)}"
                        pending_calls[tc_id] = {
                            "name": b.get("name", "?"),
                            "args": b.get("input", {}) or {},
                            "turn": turn,
                        }
                        # 推断是否访问了 "其他节点输出": args 里含 node_id / from_node 等提示
                        args = b.get("input") or {}
                        for hint_key in ("node_id", "from_node", "source_node", "ref_node"):
                            if hint_key in args and isinstance(args[hint_key], str):
                                external_accesses.add(args[hint_key])
        # tool / user (with tool_result): 结果回传
        elif role in ("tool", "user"):
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        tc_id = b.get("tool_use_id")
                        if tc_id and tc_id in pending_calls:
                            p = pending_calls.pop(tc_id)
                            result_str = b.get("content")
                            if isinstance(result_str, list):
                                result_str = "\n".join(
                                    x.get("text", "") for x in result_str
                                    if isinstance(x, dict)
                                )
                            elif not isinstance(result_str, str):
                                result_str = str(result_str)
                            is_err = b.get("is_error", False)
                            tool_calls.append(ToolCallRecord(
                                turn=p["turn"],
                                name=p["name"],
                                args=p["args"],
                                result_preview=(result_str or "")[:500],
                                error=(result_str[:500] if is_err else None),
                            ))

    # 未配结果的 pending call (loop 提前终止) 也记一下
    for tc_id, p in pending_calls.items():
        tool_calls.append(ToolCallRecord(
            turn=p["turn"],
            name=p["name"],
            args=p["args"],
            result_preview="",
            error="no_result (loop ended)",
        ))

    final_answer = "\n".join(final_text_parts)[:2000]
    upstream_keys = sorted((input_data or {}).keys()) if isinstance(input_data, dict) else []

    return AgentLoopTrace(
        node_id=node_id,
        router_class=router_class,
        format_in=format_in,
        format_out=format_out,
        description=description,
        total_turns=turn,
        finished_reason=finished_reason,
        tool_calls=tool_calls,
        external_node_accesses=sorted(external_accesses),
        upstream_input_keys=upstream_keys,
        final_answer_preview=final_answer,
        total_input_tokens=int(getattr(loop, "_total_input_tokens", 0) or 0),
        total_output_tokens=int(getattr(loop, "_total_output_tokens", 0) or 0),
    )
