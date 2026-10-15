# [OMNI] origin=ai-ide ts=2026-05-24 type=infra
# [OMNI] material_id="material:dashboard.boss_sight.controller.extract_result.py"
"""ControllerExtractResult — 从 messages 提总控 submit_response tool_use 组 Verdict.

跟 _HealthCriteriaExtractResult (team_supervisor) 同套范式: 反向扫 messages 找
最后一个 submit_response tool_use 的 input, 装 Verdict.
"""

from __future__ import annotations

import logging

from omnicompany.packages.services._core.agent.routers.extract_result import ExtractResultRouter
from omnicompany.protocol.anchor import Verdict, VerdictKind

_log = logging.getLogger(__name__)


SUBMIT_RESPONSE_TOOL_NAME = "submit_response"


class ControllerExtractResult(ExtractResultRouter):
    """从 messages 抽 submit_response tool_use 的 input 作为本轮产物."""

    def extract(
        self,
        *,
        final_text: str,
        messages: list[dict],
        turn_count: int,
        stop_reason: str,
    ) -> Verdict:
        submit_input: dict | None = None
        side_tool_calls: list[dict] = []

        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_use":
                    continue
                name = block.get("name", "")
                inp = block.get("input") or {}
                if name == SUBMIT_RESPONSE_TOOL_NAME and isinstance(inp, dict):
                    submit_input = dict(inp)  # 最后一次出现的为准
                else:
                    side_tool_calls.append({"name": name, "input": inp})

        if submit_input is None:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={
                    "reply_to_user": final_text[:500],
                    "turn_summary": "(未调 submit_response)",
                    "side_actions_taken": [c["name"] for c in side_tool_calls[:10]],
                    "turn_count": turn_count,
                    "stop_reason": stop_reason,
                },
                diagnosis=(
                    f"controller 未在末步调 submit_response "
                    f"(turns={turn_count}, stop={stop_reason}). final_text fallback"
                ),
            )

        # 把工具调用记录附上, 让 provider hook 能转发
        submit_input["_side_tool_calls"] = side_tool_calls
        submit_input["_turn_count"] = turn_count
        submit_input["_stop_reason"] = stop_reason
        # OmniAgentProvider._run() 兜底推 text NormalizedMessage 时读 final_text 字段
        # (omni_agent.py L210-217). 把 reply_to_user 复制进去, 让前端能收到总控回复.
        submit_input["final_text"] = submit_input.get("reply_to_user", "")

        if stop_reason == "max_turns":
            return Verdict(
                kind=VerdictKind.PARTIAL,
                output=submit_input,
                diagnosis=f"controller 末步 OK 但预算耗尽: {turn_count} turns",
            )
        return Verdict(
            kind=VerdictKind.PASS,
            output=submit_input,
            diagnosis=f"controller turn 完成: side_actions={submit_input.get('side_actions_taken')}",
        )


__all__ = ["ControllerExtractResult", "SUBMIT_RESPONSE_TOOL_NAME"]
