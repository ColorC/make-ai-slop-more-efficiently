# [OMNI] origin=ai-ide ts=2026-05-24 type=infra
# [OMNI] material_id="material:dashboard.boss_sight.controller.prompt_builder.py"
"""ControllerPromptBuilder — 装总控首轮会话.

两种调用形态都支持:

1. **OmniAgentProvider 标准路径** (chat.py 创 controller session):
   input_data = {input, prompt, trace_id, origin, agent_name}
   → 解析为 event_kind="user.message", event_payload={message_content: prompt}
   → ctx (plan_index / subagent_status) 懒扫描自动装

2. **块 3+ 事件唤起路径** (subagent.completed / subagent.blocked 通过 EventBus 进):
   input_data = {event_kind, event_payload, plan_index_material, subagent_status_material,
                 workflow_summary_material}
   → 直接用上游传的 ctx 字段

两种路径产同样的 user message 文本.
"""

from __future__ import annotations

import logging
from typing import Any

from omnicompany.packages.services._core.agent.routers.prompt_builder import PromptBuilderRouter

_log = logging.getLogger(__name__)


def _default_workspace_root() -> str:
    """omnicompany 仓库根目录推导."""
    from omnicompany.core.config import omni_workspace_root
    return str(omni_workspace_root())


class ControllerPromptBuilder(PromptBuilderRouter):
    """总控首轮装配. NODE_PROMPT 通过 template= 传入."""

    def __init__(
        self,
        *,
        template: str = "",
        bus: Any | None = None,
        workspace_root: str | None = None,
    ) -> None:
        super().__init__(template=template, bus=bus)
        self._workspace_root = workspace_root or _default_workspace_root()

    def _lazy_scan_plan_index(self) -> dict:
        from ..aggregator.plan_index_scanner import PlanIndexScanner

        try:
            scanner = PlanIndexScanner(self._workspace_root)
            return scanner.to_material_payload(scanner.scan())
        except Exception:  # noqa: BLE001
            _log.exception("lazy scan plan_index 失败")
            return {"plans": [], "total": 0}

    def _lazy_load_subagent_status(self) -> dict:
        from ..aggregator.subagent_status_aggregator import SubagentStatusAggregator

        try:
            agg = SubagentStatusAggregator(self._workspace_root)
            agg.refresh_from_cc_sessions()
            return agg.to_material_payload()
        except Exception:  # noqa: BLE001
            _log.exception("lazy load subagent_status 失败")
            return {"subagents": [], "active_count": 0, "total_count": 0}

    def _lazy_load_workflow_summary(self) -> dict:
        from ..cockpit_workflow import build_workflow_summary

        try:
            return build_workflow_summary(ws=self._workspace_root).get("ctx_summary", {})
        except Exception:  # noqa: BLE001
            _log.exception("lazy load workflow_summary 失败")
            return {"status": "unavailable", "headline": "workflow summary unavailable"}

    def build_initial_messages(self, input_data: dict) -> list[dict]:
        # ── 解析 event 形态 ────────────────────────────────────────────
        event_kind = input_data.get("event_kind")
        if not event_kind:
            # OmniAgentProvider 标准路径: 把 prompt 当 user.message
            event_kind = "user.message"
            user_text = input_data.get("prompt") or input_data.get("input") or ""
            event_payload = {"message_content": user_text}
        else:
            event_payload = input_data.get("event_payload") or {}

        # ── 装 ctx (懒扫描) ────────────────────────────────────────────
        plan_index = input_data.get("plan_index_material")
        if not plan_index or not (plan_index.get("plans") if isinstance(plan_index, dict) else None):
            plan_index = self._lazy_scan_plan_index()
        subagent_status = input_data.get("subagent_status_material")
        if not subagent_status or not isinstance(subagent_status, dict) or "subagents" not in subagent_status:
            subagent_status = self._lazy_load_subagent_status()
        workflow_summary = input_data.get("workflow_summary_material")
        if not workflow_summary or not isinstance(workflow_summary, dict):
            workflow_summary = self._lazy_load_workflow_summary()

        plans = plan_index.get("plans", []) if isinstance(plan_index, dict) else []
        subagents = subagent_status.get("subagents", []) if isinstance(subagent_status, dict) else []
        active_count = subagent_status.get("active_count", 0) if isinstance(subagent_status, dict) else 0
        total_count = subagent_status.get("total_count", len(subagents)) if isinstance(subagent_status, dict) else 0

        # ── 渲 user message ───────────────────────────────────────────
        lines: list[str] = ["# 本轮唤起", ""]
        lines.append(f"**event_kind**: `{event_kind}`")

        if event_kind == "user.message":
            content = event_payload.get("message_content", "")
            lines.append("")
            lines.append("**user_message**:")
            lines.append("")
            lines.append(content)
        elif event_kind == "subagent.completed":
            lines.append(
                f"**subagent**: `{event_payload.get('subagent_id', '?')}` "
                f"**plan**: `{event_payload.get('plan_id', '?')}` "
                f"**verdict**: `{event_payload.get('verdict', '?')}`"
            )
            output_summary = event_payload.get("output_summary", "")
            if output_summary:
                lines.append("")
                lines.append("**output_summary**:")
                lines.append(output_summary[:1500])
            produced = event_payload.get("produced_materials") or []
            if produced:
                lines.append("")
                lines.append(f"**produced_materials** ({len(produced)} 件):")
                for m in produced[:10]:
                    lines.append(f"- {m}")
        elif event_kind == "subagent.blocked":
            lines.append(
                f"**subagent**: `{event_payload.get('subagent_id', '?')}` "
                f"**plan**: `{event_payload.get('plan_id', '?')}`"
            )
            lines.append("")
            lines.append(f"**violation**: {event_payload.get('violation', '?')}")
            tool_call = event_payload.get("tool_call") or {}
            if tool_call:
                lines.append(f"**tool_call**: `{tool_call}`")
            subagent_ctx = event_payload.get("subagent_ctx_summary") or ""
            if subagent_ctx:
                lines.append("")
                lines.append("**subagent_ctx_summary**:")
                lines.append(subagent_ctx[:1000])

        # ── ctx 快照 ───────────────────────────────────────────────────
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("# 当前 ctx 快照")
        lines.append("")
        try:
            from ..cockpit_workflow import format_workflow_ctx_summary

            lines.append(format_workflow_ctx_summary(workflow_summary))
            lines.append("")
        except Exception:  # noqa: BLE001
            _log.exception("render workflow_summary 失败")
            lines.append("## workflow summary")
            lines.append("")
            lines.append("- unavailable")
            lines.append("")
        lines.append(f"## plan 索引（共 {len(plans)} 条；按最近修改倒序前 20）")
        lines.append("")
        for p in plans[:20]:
            todo_done = p.get("todo_done", 0)
            todo_total = p.get("todo_total", 0)
            todo_str = f" [{todo_done}/{todo_total}]" if todo_total else ""
            status = p.get("status") or "?"
            title = p.get("title", "?")
            lines.append(f"- `{p.get('plan_id', '?')}`{todo_str} status={status} — {title}")
        if len(plans) > 20:
            lines.append(f"- ...还有 {len(plans) - 20} 条")

        lines.append("")
        lines.append(f"## subagent 活跃情况（共 {total_count} 个；active {active_count}）")
        lines.append("")
        active_subs = [s for s in subagents if s.get("state") in {"idle", "running", "blocked"}]
        for s in active_subs[:15]:
            lines.append(
                f"- `{s.get('subagent_id', '?')}` state={s.get('state')} "
                f"plan={s.get('plan_id') or '-'} kind={s.get('kind', '?')}"
            )
        if len(active_subs) > 15:
            lines.append(f"- ...还有 {len(active_subs) - 15} 个 active subagent")

        # ── 末步硬约束 ────────────────────────────────────────────────
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(
            "请按 system prompt 的[响应流程]段处理本次唤起，"
            "**最后必须调 `submit_response` 工具**结束本轮。"
        )

        return [{"role": "user", "content": "\n".join(lines)}]


__all__ = ["ControllerPromptBuilder"]
