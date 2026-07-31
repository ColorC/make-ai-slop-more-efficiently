# [OMNI] origin=claude-code domain=evolution/workflow ts=2026-04-08T03:23:38Z
# [OMNI] material_id="material:core.evolution.workflow.shallow_trace_scanner.py"
"""B.1 浅层追踪 — 自动，不用 Agent

沿管线倒序逐节点检查：
  - actual_output 是否符合该节点声明的 Format_out？
  - 判定工具：LLM（prompt 引用 format 语义描述）
  - 记录：PASS / FAIL / UNCERTAIN + confidence

停止条件：找到第一个 FAIL 节点（候选根因），
或全部 PASS（问题在 Format 定义本身）。

输出：初始化好的 HypothesisBoard，已写入 HypothesisBoardStore。
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from omnicompany.bus.sqlite import SQLiteBus
from omnicompany.packages.services._core.evolution.workflow.hypothesis import (
    Hypothesis,
    HypothesisBoard,
    HypothesisStatus,
)
from omnicompany.packages.services._core.evolution.workflow.hypothesis_store import HypothesisBoardStore
from omnicompany.packages.services._core.evolution.workflow.pain_signal import QualityPainSignal
from omnicompany.protocol.events import FactoryEvent
from omnicompany.protocol.format import FormatRegistry
from omnicompany.protocol.registry import EventType
from omnicompany.runtime.llm.llm import LLMClient

logger = logging.getLogger(__name__)

# ── 每个节点的追踪结果 ──


@dataclass
class NodeTraceResult:
    node_id: str
    step: int
    format_out: str
    description: str
    output_summary: str
    verdict_kind: str                  # PASS | FAIL | PARTIAL（管线自身的判定）
    format_check: dict[str, Any]       # 已有的结构校验结果
    diagnosis: str                     # 管线诊断（如有）

    # 浅层追踪新增
    semantic_check: str = "PENDING"    # PASS | FAIL | UNCERTAIN
    semantic_confidence: float = 0.0
    semantic_reason: str = ""


# ── LLM Format 语义判定 ──


_SYSTEM_FORMAT_JUDGE = """\
你是一个管线输出质量检查员。你的任务是判断一个管线节点的实际输出是否符合其声明的 Format（语义类型）要求。

判定原则：
1. 只关注语义符合性，不关注格式美观
2. 如果输出大体上满足了 Format 的语义意图，判 PASS
3. 如果输出明显缺失关键内容或偏离 Format 意图，判 FAIL
4. 如果信息不足以判断，判 UNCERTAIN

你必须用以下 JSON 格式回答（不要有任何其他文字）：
{
    "judgment": "PASS" | "FAIL" | "UNCERTAIN",
    "confidence": 0.0 到 1.0,
    "reason": "一句话解释"
}
"""

_PROMPT_FORMAT_JUDGE = """\
节点 ID: {node_id}
节点描述: {description}
期望输出 Format: {format_out}
{format_description_section}
实际输出（节录，最多 2000 字）:
---
{output_summary}
---
{diagnosis_section}

该节点的实际输出是否符合 Format "{format_out}" 的语义要求？
"""


def _build_judge_prompt(
    node: NodeTraceResult,
    format_registry: FormatRegistry | None,
) -> str:
    # 如果有 format_registry，附上语义描述
    fmt_desc_section = ""
    if format_registry and format_registry.is_registered(node.format_out):
        fmt = format_registry.get(node.format_out)
        lines = [f"Format 语义描述: {fmt.description}"]
        if fmt.semantic_preconditions:
            lines.append("语义前置条件:")
            for p in fmt.semantic_preconditions:
                lines.append(f"  - {p}")
        fmt_desc_section = "\n".join(lines)

    diag_section = ""
    if node.diagnosis:
        diag_section = f"管线自身诊断: {node.diagnosis}"

    return _PROMPT_FORMAT_JUDGE.format(
        node_id=node.node_id,
        description=node.description,
        format_out=node.format_out,
        format_description_section=fmt_desc_section,
        output_summary=node.output_summary or "(无输出内容)",
        diagnosis_section=diag_section,
    )


def _llm_judge_node(
    llm: LLMClient,
    node: NodeTraceResult,
    format_registry: FormatRegistry | None,
) -> NodeTraceResult:
    """用 LLM 判定单节点输出是否符合 Format_out 语义"""
    prompt = _build_judge_prompt(node, format_registry)
    try:
        response = llm.call(
            messages=[{"role": "user", "content": prompt}],
            system=_SYSTEM_FORMAT_JUDGE,
        )
        # 提取 text
        text = ""
        if hasattr(response, "content"):
            for block in response.content:
                if hasattr(block, "text"):
                    text += block.text
        elif isinstance(response, str):
            text = response

        # 解析 JSON
        text = text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]

        parsed = json.loads(text)
        node.semantic_check = parsed.get("judgment", "UNCERTAIN")
        node.semantic_confidence = float(parsed.get("confidence", 0.5))
        node.semantic_reason = parsed.get("reason", "")
    except Exception as e:
        logger.warning("LLM judge failed for node %s: %s", node.node_id, e)
        node.semantic_check = "UNCERTAIN"
        node.semantic_confidence = 0.3
        node.semantic_reason = f"判定失败: {e}"

    return node


# ── 从事件流中提取节点轨迹 ──

_ENTER_TYPES = {
    EventType.TOOL_CALL.value,
    EventType.LLM_REQUEST.value,
    # STATE_CHANGE 同时作为 enter 和 exit，靠 verdict 区分
}
_EXIT_TYPES = {
    EventType.TOOL_RESULT.value,
    EventType.LLM_RESPONSE.value,
    EventType.STATE_CHANGE.value,
}
_ALL_NODE_TYPES = _ENTER_TYPES | _EXIT_TYPES


def _extract_node_traces(events: list[FactoryEvent]) -> list[NodeTraceResult]:
    """从 trace 的事件流中提取各节点的执行结果，按 step 排序

    兼容新旧两种 payload 格式：
    - 新格式（V1.1）：enter 和 exit 都携带 format_out/description/output_summary
    - 旧格式：enter 携带 format_out/description，exit 只携带 verdict/diagnosis
    """
    # key = node_id, value = 合并后的字段 dict（取最后一次调用）
    node_data: dict[str, dict] = {}

    for event in events:
        if event.event_type not in _ALL_NODE_TYPES:
            continue
        payload = event.payload
        node_id = payload.get("node")
        if not node_id:
            continue

        step = payload.get("step", 0)
        raw_verdict = payload.get("verdict")
        is_exit = raw_verdict is not None  # exit 事件有 verdict；enter 没有

        # 对同一节点，用 (node_id, step 的量级) 来判断是否新的调用轮次
        prev = node_data.get(node_id, {})
        if prev.get("step", -1) != step or not prev:
            # 新的调用轮次（同一节点被再次执行）
            node_data[node_id] = {"node_id": node_id, "step": step}

        rec = node_data[node_id]

        # 从 enter 或 exit 事件中提取 format_out/description（两种格式都可能有）
        if payload.get("format_out"):
            rec["format_out"] = payload["format_out"]
        if payload.get("description"):
            rec["description"] = payload["description"]
        if payload.get("output_summary"):
            rec["output_summary"] = payload["output_summary"]
        if payload.get("format_check"):
            rec["format_check"] = payload["format_check"]

        # exit 事件才有 verdict 和 diagnosis
        if is_exit:
            rec["verdict_kind"] = str(raw_verdict).upper()
            if payload.get("diagnosis"):
                rec["diagnosis"] = payload["diagnosis"]
            rec["step"] = step  # 用 exit 的 step 作为权威值

    # 过滤掉没有 verdict（从未执行完成）的节点，按 step 排序
    results = []
    for rec in node_data.values():
        if "verdict_kind" not in rec:
            continue
        results.append(NodeTraceResult(
            node_id=rec["node_id"],
            step=rec.get("step", 0),
            format_out=rec.get("format_out") or "",
            description=rec.get("description") or rec["node_id"],
            output_summary=rec.get("output_summary") or "",
            verdict_kind=rec["verdict_kind"],
            format_check=rec.get("format_check") or {},
            diagnosis=rec.get("diagnosis") or "",
        ))

    return sorted(results, key=lambda n: n.step)


# ── 主入口 ──


class ShallowTracer:
    """B.1 浅层追踪

    用法：
        tracer = ShallowTracer(store)  # Move 8: unified path
        board = await tracer.run(pain_signal, format_registry=registry)
    """

    def __init__(
        self,
        store: HypothesisBoardStore,
        bus_path: str | None = None,  # Move 8: None → unified data/events.db
        llm: LLMClient | None = None,
    ):
        self.store = store
        self.bus_path = bus_path
        self._llm = llm or LLMClient()

    async def run(
        self,
        pain: QualityPainSignal,
        format_registry: FormatRegistry | None = None,
    ) -> HypothesisBoard:
        """执行浅层追踪，返回初始化好的 HypothesisBoard"""

        # 1. 读取 trace 事件
        bus_path = pain.bus_path or self.bus_path
        bus = SQLiteBus(bus_path)
        await bus.connect()
        try:
            events = await bus.read_trace(pain.trace_id)
        finally:
            await bus.close()

        if not events:
            logger.warning("No events found for trace_id=%s", pain.trace_id)
            return self._make_empty_board(pain)

        logger.info(
            "Loaded %d events for trace %s", len(events), pain.trace_id
        )

        # 2. 提取各节点执行结果
        node_traces = _extract_node_traces(events)
        if not node_traces:
            logger.warning("No node exit events found in trace %s", pain.trace_id)
            return self._make_empty_board(pain)

        # 3. 优先找管线自身判定的 FAIL 节点（比 LLM 语义判定更可靠）
        candidates: list[tuple[NodeTraceResult, float]] = []
        found_fail = False

        # 第一遍：找所有 pipeline verdict=FAIL 的节点（取最靠近输出的一个）
        pipeline_fails = [n for n in node_traces if n.verdict_kind == "FAIL"]
        if pipeline_fails:
            # 最靠近输出的 FAIL 节点（step 最大的）是首要候选根因
            focus_fail = max(pipeline_fails, key=lambda n: n.step)
            focus_fail.semantic_check = "FAIL"
            focus_fail.semantic_confidence = 0.9
            focus_fail.semantic_reason = f"管线自身判定 FAIL: {focus_fail.diagnosis or '无诊断'}"
            candidates.append((focus_fail, 0.9))
            found_fail = True
            logger.info(
                "[shallow] FAIL (pipeline verdict): %s step=%d diag=%s",
                focus_fail.node_id, focus_fail.step,
                (focus_fail.diagnosis or "")[:80],
            )

        # 第二遍：如果没有 pipeline FAIL，对 PASS 节点做 LLM 语义判定（倒序）
        if not found_fail:
            for node in reversed(node_traces):
                if node.verdict_kind == "FAIL":
                    continue  # 已在第一遍处理

                # 没有 format_out 的 PASS 节点跳过
                if not node.format_out:
                    logger.debug("Node %s has no format_out, skipping semantic check", node.node_id)
                    continue

                # LLM 语义判定
                node = _llm_judge_node(self._llm, node, format_registry)
                logger.info(
                    "[shallow] %s %s (%.1f) — %s: %s",
                    node.semantic_check,
                    node.node_id,
                    node.semantic_confidence,
                    node.format_out,
                    node.semantic_reason[:80],
                )

                if node.semantic_check == "FAIL":
                    candidates.append((node, node.semantic_confidence))
                    found_fail = True
                    break  # 停止，找到候选根因
                elif node.semantic_check == "UNCERTAIN":
                    candidates.append((node, node.semantic_confidence * 0.5))
                    # UNCERTAIN 不停止，继续往前找

        # 如果全部 PASS（问题可能在 Format 定义本身）
        if not found_fail and not candidates:
            logger.info(
                "[shallow] All nodes PASS — problem may be in Format definition or quality criteria"
            )
            # 将质检节点本身作为候选（Format 定义问题）
            last_node = node_traces[-1]
            candidates.append((last_node, 0.3))

        # 4. 构建初始假设列表
        hypotheses: list[Hypothesis] = []
        for node, confidence in candidates:
            h = Hypothesis(
                id=str(uuid.uuid4()),
                statement=self._generate_statement(node, pain),
                suspect_node=node.node_id,
                confidence=confidence,
                status=HypothesisStatus.ACTIVE,
                relevant_nodes=[node.node_id],
                relevant_traces=[pain.trace_id],
                supporting_traces=[pain.trace_id],
                falsification_test=(
                    f"修改 {node.node_id} 后，trace {pain.trace_id} 的输入"
                    f" 重跑应输出符合 '{node.format_out}' 语义的结果"
                ),
                created_by="shallow_trace",
            )
            hypotheses.append(h)
            logger.info(
                "[shallow] Hypothesis: node=%s confidence=%.2f",
                node.node_id,
                confidence,
            )

        # 5. 初始化并持久化黑板
        board = HypothesisBoard(
            board_id=HypothesisBoardStore.new_board_id(),
            pipeline_id=pain.pipeline_id,
            trace_id=pain.trace_id,
            quality_verdict=pain.quality_verdict,
            hypotheses=hypotheses,
            pipeline_input=pain.pipeline_input,
            status="diagnosing",
        )
        self.store.save(board)

        logger.info(
            "[shallow] Board %s created with %d hypothesis(es)",
            board.board_id,
            len(hypotheses),
        )
        return board

    def _make_empty_board(self, pain: QualityPainSignal) -> HypothesisBoard:
        """当 trace 中无节点级事件时，用 pain.failing_node_id 构造合成假设。

        不直接 escalate——让 DiagnosisAgent 有机会根据 quality_verdict 和
        actual_output_summary 做进一步诊断。
        """
        hypotheses: list[Hypothesis] = []
        if pain.failing_node_id:
            h = Hypothesis(
                id=str(uuid.uuid4()),
                statement=(
                    f"节点 '{pain.failing_node_id}' 报告失败：{pain.quality_verdict[:200]}"
                ),
                suspect_node=pain.failing_node_id,
                confidence=0.6,
                status=HypothesisStatus.ACTIVE,
                relevant_nodes=[pain.failing_node_id],
                relevant_traces=[pain.trace_id],
                supporting_traces=[pain.trace_id],
                falsification_test=(
                    f"修复 {pain.failing_node_id} 后重跑，确认 cargo check 通过"
                ),
                created_by="shallow_trace_synthetic",
            )
            hypotheses.append(h)
            logger.info(
                "[shallow] No trace events — created synthetic hypothesis for node=%s from pain signal",
                pain.failing_node_id,
            )

        board = HypothesisBoard(
            board_id=HypothesisBoardStore.new_board_id(),
            pipeline_id=pain.pipeline_id,
            trace_id=pain.trace_id,
            quality_verdict=pain.quality_verdict,
            hypotheses=hypotheses,
            pipeline_input=pain.pipeline_input,
            status="diagnosing" if hypotheses else "escalated",
            escalation_reason="" if hypotheses else "无法从 trace 中提取节点执行数据且 pain 未指定 failing_node_id",
        )
        self.store.save(board)
        return board

    @staticmethod
    def _generate_statement(node: NodeTraceResult, pain: QualityPainSignal) -> str:
        if node.semantic_reason:
            return (
                f"节点 '{node.node_id}' 的输出不符合 Format '{node.format_out}'：{node.semantic_reason}"
            )
        return (
            f"节点 '{node.node_id}' 的输出可能不符合 Format '{node.format_out}' 的语义要求，"
            f"导致质检节点 '{pain.failing_node_id}' 判定失败。"
        )
