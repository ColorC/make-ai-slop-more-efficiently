# [OMNI] origin=claude-code domain=services/semantic_auditor ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:diagnosis.semantic_auditor.llm_audit_dispatcher.worker.python"
"""LLMAuditWorker — SemanticAuditor Team Worker #4.

Worker 协议:
  FORMAT_IN  = semantic_auditor.audit-excerpt-set
  FORMAT_OUT = semantic_auditor.finding-set

职责: async HARD 外壳, 对每条 excerpt 启动一次 AuditAgent 单审并合并 Finding。

设计取舍:
  - Pipeline 当前不支持 fan-out, 所以本 Worker 作为"薄循环调度"外壳
  - 单审逻辑在 AuditAgent (AgentNodeLoop 子类), 保证"能 AgentNodeLoop 就 AgentNodeLoop"
  - AuditAgent 的 LLM / tool 调用自动进 bus, 审计优越
"""
from __future__ import annotations

import json
from typing import Any

from omnifactory.protocol.anchor import Verdict, VerdictKind
from omnifactory.packages.services._core.omnicompany import Worker


# 单次审的 user task 模板（复杂字段走 task，避开 NODE_PROMPT 的 str.format）
_TASK_TEMPLATE = """审计以下 artifact 是否符合标准 {standard_id}。

artifact 路径: {artifact_path}
artifact 类型: {artifact_kind}

标准 {standard_id} 的摘录如下：
========= 标准摘录开始 =========
{excerpt_text}
========= 标准摘录结束 =========

请先用 read_file 读 artifact 全文，按需 grep/glob 取证，再对照标准判断违规。
最终通过 finish 工具提交 JSON: {{"findings": [...]}}（见 system prompt 协议）。
"""


class LLMAuditWorker(Worker):
    """async HARD 外壳：对每条 excerpt 启动一次 AuditAgent，合并 Finding。"""

    INPUT_KEYS = ["excerpts"]
    DESCRIPTION = (
        "循环调度 AuditAgent 对每条 excerpt 单审，"
        "合并 Finding 列表作为下游 FindingWriter 的输入"
    )
    FORMAT_IN = "semantic_auditor.audit-excerpt-set"
    FORMAT_OUT = "semantic_auditor.finding-set"

    def __init__(
        self,
        *,
        bus: Any = None,
        model: str | None = None,
        agent: Any = None,
    ):
        """bus 必须传（AuditAgent 的硬要求）。
        agent 允许注入（测试时可传 mock AuditAgent 实例，避免真调 LLM）。
        """
        self._bus = bus
        self._model = model
        self._injected_agent = agent

    def _build_agent(self) -> Any:
        if self._injected_agent is not None:
            return self._injected_agent
        from ..audit_agent import AuditAgent
        return AuditAgent(bus=self._bus, model=self._model)

    async def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict) or "excerpts" not in input_data:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": "input_data 需含 excerpts 字段"},
            )

        excerpts = input_data["excerpts"]
        if not isinstance(excerpts, list):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": "excerpts 必须是 list"},
            )

        try:
            agent = self._build_agent()
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": f"AuditAgent 构造失败: {e}"},
            )

        findings: list[dict[str, Any]] = []
        parse_errors: list[dict[str, str]] = []
        audit_count = 0

        for ex in excerpts:
            if not isinstance(ex, dict):
                continue
            target = ex.get("target") or {}
            standard_id = ex.get("standard_id", "")
            excerpt_text = ex.get("excerpt_text", "")
            if not (standard_id and excerpt_text):
                continue

            task = _TASK_TEMPLATE.format(
                standard_id=standard_id,
                artifact_path=target.get("path", ""),
                artifact_kind=target.get("kind") or "unknown",
                excerpt_text=excerpt_text,
            )

            try:
                verdict = await agent.run({
                    "task": task,
                    "trace_id": f"audit-{standard_id}-{target.get('path', '')}",
                })
            except Exception as e:
                parse_errors.append({
                    "target_path": target.get("path", ""),
                    "standard_id": standard_id,
                    "reason": f"agent.run 异常: {e}",
                })
                continue

            audit_count += 1

            if not verdict or not isinstance(verdict.output, dict):
                parse_errors.append({
                    "target_path": target.get("path", ""),
                    "standard_id": standard_id,
                    "reason": "AuditAgent 未返回 dict output",
                })
                continue

            final_text = (verdict.output.get("text") or "").strip()
            if not final_text:
                continue  # 无违规 + 空 finish 也会落空，忽略

            try:
                data = json.loads(final_text)
            except json.JSONDecodeError as e:
                parse_errors.append({
                    "target_path": target.get("path", ""),
                    "standard_id": standard_id,
                    "reason": f"Finding JSON 解析失败: {e}",
                })
                continue

            batch = data.get("findings") if isinstance(data, dict) else None
            if not isinstance(batch, list):
                parse_errors.append({
                    "target_path": target.get("path", ""),
                    "standard_id": standard_id,
                    "reason": "findings 字段不是 list",
                })
                continue

            # 回填标准/路径（LLM 可能漏填），后续 FindingWriter 再做严格验证
            for f in batch:
                if not isinstance(f, dict):
                    continue
                f.setdefault("standard_id", standard_id)
                f.setdefault("target_path", target.get("path", ""))
                findings.append(f)

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "project_root": input_data.get("project_root", "."),
                "findings": findings,
                "finding_count": len(findings),
                "audit_count": audit_count,
                "parse_errors": parse_errors,
            },
        )
