# [OMNI] origin=claude-code domain=services/trace_induction ts=2026-04-22T00:00:00Z type=helper
# [OMNI] material_id="material:learning.trace_induction.worker_helpers.step_formatter_and_json_parser.py"
"""trace_induction workers 共享辅助函数 (Stage 3 Clean Migration 2026-04-22)."""
from __future__ import annotations

import json


def format_steps(steps: list[dict]) -> str:
    """将步骤列表格式化为可读文本 (供 noise_filter / sop_generator 复用)."""
    lines = []
    for s in steps:
        exit_str = {-1: "pending", 0: "FAIL", 1: "ok"}.get(s.get("tool_exit_ok", -1), "?")
        lines.append(
            f"Step {s.get('step_num', '?')}: [{s.get('action_class', '')}] "
            f"{s.get('tool_name', '?')} — {s.get('desc', '')}\n"
            f"  rationale: {s.get('rationale', '')}\n"
            f"  args: {str(s.get('tool_args_summary', ''))[:200]}\n"
            f"  result: {str(s.get('tool_result', ''))[:200]}\n"
            f"  exit: {exit_str}"
        )
    return "\n\n".join(lines)


def parse_json_loose(raw: str) -> dict | None:
    """解析 LLM 返回的 JSON (处理 markdown 包裹)."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
