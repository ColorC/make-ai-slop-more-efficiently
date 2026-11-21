# [OMNI] origin=claude-code domain=services/evolution ts=2026-04-08T03:23:38Z
# [OMNI] material_id="material:core.evolution.workflow.evolution_orchestrator.py"
"""进化工作流编排器

将 B.1 ~ B.5 串联成完整的进化循环：

  Pain Signal
     ↓ B.1 ShallowTracer
  HypothesisBoard (initial)
     ↓ B.2 DiagnosisAgent
  DiagnosisReport
     ↓ B.3 ExperimentRunner
  ExperimentResult
     ↓ B.4 ResultAnalyzer
  AnalysisResult
     ↓ B.5 BoardUpdater
  HypothesisBoard (updated)
     ↓
  done | escalated | next_cycle

循环退出条件：
- board.status == "done"（假设已 CONFIRMED）
- board.status == "escalated"（所有假设 confidence < 0.1）
- 达到最大循环次数（默认 5 轮）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from omnicompany.packages.services._core.evolution.workflow.board_updater import BoardUpdater
from omnicompany.packages.services._core.evolution.workflow.diagnosis import DiagnosisAgent, DiagnosisReport
from omnicompany.packages.services._core.evolution.workflow.events import DEFAULT_WORKFLOW_TAGS, publish_workflow_event
from omnicompany.packages.services._core.evolution.workflow.experiment_runner import ExperimentResult, ExperimentRunner
from omnicompany.packages.services._core.evolution.workflow.hypothesis import HypothesisBoard
from omnicompany.packages.services._core.evolution.workflow.hypothesis_store import HypothesisBoardStore
from omnicompany.packages.services._core.evolution.workflow.user_inquiry import UserInquiry, UserInquiryStore, write_inquiry_to_file, get_default_store
from omnicompany.packages.services._core.evolution.workflow.pain_signal import QualityPainSignal
from omnicompany.packages.services._core.evolution.workflow.result_analyzer import AnalysisResult, ResultAnalyzer
from omnicompany.packages.services._core.evolution.workflow.shallow_tracer import ShallowTracer
from omnicompany.protocol.format import FormatRegistry
from omnicompany.runtime.llm.llm import LLMClient

logger = logging.getLogger(__name__)

_MAX_CYCLES = 5


@dataclass
class OrchestrationResult:
    """一次完整进化会话的结果"""

    board: HypothesisBoard
    cycles: int
    """实际运行了几轮"""

    final_status: str
    """done | escalated | max_cycles_reached"""

    diagnosis_reports: list[DiagnosisReport] = field(default_factory=list)
    experiment_results: list[ExperimentResult] = field(default_factory=list)
    analysis_results: list[AnalysisResult] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"进化会话：{self.board.board_id}",
            f"管线：{self.board.pipeline_id}  Trace：{self.board.trace_id}",
            f"状态：{self.final_status}  轮次：{self.cycles}",
        ]
        if self.board.hypotheses:
            lines.append(f"\n假设摘要（共 {len(self.board.hypotheses)} 条）：")
            for h in self.board.hypotheses:
                lines.append(
                    f"  [{h.status.value}] {h.suspect_node} conf={h.confidence:.2f}"
                    f"  — {h.statement[:80]}"
                )
        if self.experiment_results:
            lines.append(f"\n实验摘要（共 {len(self.experiment_results)} 次）：")
            for i, exp in enumerate(self.experiment_results, 1):
                an = self.analysis_results[i - 1] if i <= len(self.analysis_results) else None
                verdict = an.verdict if an else exp.verdict
                lines.append(
                    f"  [{i}] {exp.proposed_change.target_node}"
                    f"  {exp.proposed_change.change_type}"
                    f"  → {verdict}"
                )
        if self.final_status == "done":
            confirmed = [h for h in self.board.hypotheses if h.status.value == "confirmed"]
            if confirmed:
                lines.append(f"\n根因修复确认：{confirmed[0].suspect_node}")
        elif self.final_status == "escalated":
            lines.append(f"\n升级原因：{self.board.escalation_reason}")
        return "\n".join(lines)


class EvolutionOrchestrator:
    """进化工作流编排器

    用法（完整流程）：
        orch = EvolutionOrchestrator(store)  # Move 8: unified path
        result = await orch.run(pain_signal)
        print(result.summary())

    用法（从已有 board 继续）：
        result = await orch.continue_from_board(board_id)
    """

    def __init__(
        self,
        store: HypothesisBoardStore,
        bus_path: str | None = None,  # Move 8: None → unified data/events.db
        experiment_bus_path: str | None = None,
        llm: LLMClient | None = None,
        format_registry: FormatRegistry | None = None,
        max_cycles: int = _MAX_CYCLES,
        inquiry_store: UserInquiryStore | None = None,
        event_bus: Any | None = None,
    ):
        self.store = store
        self.bus_path = bus_path
        self.experiment_bus_path = experiment_bus_path or (
            str(bus_path).replace(".db", "_exp.db") if bus_path else None
        )
        self._llm = llm or LLMClient()
        self._format_registry = format_registry
        self.max_cycles = max_cycles
        self._inquiry_store = inquiry_store or get_default_store()
        self._event_bus = event_bus

        self._shallow_tracer = ShallowTracer(store, bus_path=bus_path, llm=self._llm)
        self._diagnosis_agent = DiagnosisAgent(store, bus_path=bus_path, llm=self._llm)
        self._experiment_runner = ExperimentRunner(
            store, bus_path=bus_path,
            experiment_bus_path=self.experiment_bus_path,
            llm=self._llm,
            event_bus=event_bus,
        )
        self._result_analyzer = ResultAnalyzer(
            bus_path=bus_path,
            experiment_bus_path=self.experiment_bus_path,
        )
        self._board_updater = BoardUpdater(store)

    async def _publish_event(
        self,
        event_type: str,
        *,
        trace_id: str,
        payload: dict[str, Any],
        tags: list[str] | None = None,
    ) -> None:
        await publish_workflow_event(
            self._event_bus,
            trace_id=trace_id,
            event_type=event_type,
            source="evolution.workflow.orchestrator",
            payload=payload,
            tags=[*DEFAULT_WORKFLOW_TAGS, *(tags or [])],
            bus_path=self.bus_path,
        )

    async def run(self, pain: QualityPainSignal) -> OrchestrationResult:
        """从 Pain Signal 开始，执行完整进化工作流"""
        logger.info(
            "[orchestrator] Starting evolution: pipeline=%s trace=%s",
            pain.pipeline_id, pain.trace_id,
        )
        await self._publish_event(
            "evolution.workflow.started",
            trace_id=pain.trace_id,
            payload={
                "pipeline_id": pain.pipeline_id,
                "failing_node_id": pain.failing_node_id,
                "severity": pain.severity,
            },
        )

        # B.1 浅层追踪，初始化黑板
        board = await self._shallow_tracer.run(pain, format_registry=self._format_registry)
        await self._publish_event(
            "evolution.workflow.board_ready",
            trace_id=board.trace_id,
            payload={
                "board_id": board.board_id,
                "pipeline_id": board.pipeline_id,
                "status": board.status,
                "hypotheses": len(board.hypotheses),
            },
        )
        return await self._run_cycles(board)

    async def continue_from_board(self, board_id: str) -> OrchestrationResult:
        """从已有黑板继续进化（用于中断恢复）"""
        board = self.store.load(board_id)
        if not board:
            raise ValueError(f"Board not found: {board_id}")
        logger.info("[orchestrator] Continuing from board %s (status=%s)", board_id, board.status)
        await self._publish_event(
            "evolution.workflow.continued",
            trace_id=board.trace_id,
            payload={
                "board_id": board.board_id,
                "pipeline_id": board.pipeline_id,
                "status": board.status,
            },
        )

        user_answer: str | None = None
        if board.status == "awaiting_user_input":
            # 查找该 board 已回答的 inquiry
            all_inqs = self._inquiry_store.list_all(limit=50)
            answered = [
                inq for inq in all_inqs
                if inq.board_id == board_id and inq.status == "answered" and inq.answer
            ]
            if not answered:
                logger.info("[orchestrator] Board still awaiting user input, no answered inquiry yet")
                await self._publish_event(
                    "evolution.workflow.awaiting_user",
                    trace_id=board.trace_id,
                    payload={"board_id": board.board_id, "status": board.status},
                )
                return OrchestrationResult(board=board, cycles=0, final_status="awaiting_user_input")
            latest = sorted(answered, key=lambda x: x.answered_at or "", reverse=True)[0]
            user_answer = f"[用户回答 inquiry {latest.id}]\n问：{latest.question}\n答：{latest.answer}"
            logger.info("[orchestrator] Found answered inquiry %s, resuming with user answer", latest.id)
            board.status = "diagnosing"
            self.store.save(board)

        return await self._run_cycles(board, user_answer=user_answer)

    async def _run_cycles(
        self,
        board: HypothesisBoard,
        user_answer: str | None = None,
    ) -> OrchestrationResult:
        result = OrchestrationResult(
            board=board,
            cycles=0,
            final_status=board.status,
        )

        for cycle in range(self.max_cycles):
            result.cycles = cycle + 1

            if board.status in ("done", "escalated", "awaiting_user_input"):
                break

            await self._publish_event(
                "evolution.workflow.cycle_started",
                trace_id=board.trace_id,
                payload={
                    "board_id": board.board_id,
                    "cycle": cycle + 1,
                    "max_cycles": self.max_cycles,
                    "active_hypotheses": len(board.active_hypotheses()),
                },
            )
            logger.info(
                "[orchestrator] Cycle %d/%d  board=%s  active_hypotheses=%d",
                cycle + 1, self.max_cycles, board.board_id,
                len(board.active_hypotheses()),
            )

            # B.2 深度诊断（第一轮携带用户答案）
            report = await self._diagnosis_agent.run(board, user_answer=user_answer)
            user_answer = None  # 仅首轮传递
            if report is None:
                logger.warning("[orchestrator] DiagnosisAgent returned None, escalating")
                board.status = "escalated"
                board.escalation_reason = "诊断 Agent 返回空报告"
                self.store.save(board)
                await self._publish_event(
                    "evolution.workflow.diagnosis_empty",
                    trace_id=board.trace_id,
                    payload={"board_id": board.board_id, "cycle": cycle + 1},
                )
                break
            result.diagnosis_reports.append(report)
            await self._publish_event(
                "evolution.workflow.diagnosis_reported",
                trace_id=board.trace_id,
                payload={
                    "board_id": board.board_id,
                    "cycle": cycle + 1,
                    "root_cause_node": report.root_cause_node,
                    "error_category": report.error_category,
                    "confidence": report.confidence,
                    "proposed_changes": len(report.proposed_changes),
                },
            )

            # ── needs_user_clarification：提交询问，本轮暂停 ──
            if report.error_category == "needs_user_clarification":
                inquiry = UserInquiry.new(
                    board_id=board.board_id,
                    trace_id=board.trace_id,
                    pipeline_id=board.pipeline_id,
                    question=report.user_inquiry or report.root_cause_explanation,
                    context=(
                        f"根因节点（待确认）: {report.root_cause_node}\n"
                        f"诊断解释: {report.root_cause_explanation}\n"
                        f"不确定点: {report.uncertainty}"
                    ),
                )
                self._inquiry_store.submit(inquiry)
                # 同时写到文件，方便离线查看
                write_inquiry_to_file(inquiry)
                logger.info(
                    "[orchestrator] User inquiry submitted: %s — %s",
                    inquiry.id, inquiry.question[:80],
                )
                board.status = "awaiting_user_input"
                board.escalation_reason = f"需要用户澄清 (inquiry_id={inquiry.id})"
                self.store.save(board)
                result.final_status = "awaiting_user_input"
                await self._publish_event(
                    "evolution.workflow.user_inquiry",
                    trace_id=board.trace_id,
                    payload={
                        "board_id": board.board_id,
                        "cycle": cycle + 1,
                        "inquiry_id": inquiry.id,
                    },
                )
                break

            # ── tool_programming：标记后升级，不走实验 ──
            if report.error_category == "tool_programming":
                logger.info(
                    "[orchestrator] tool_programming detected at %s, escalating to code-fix workflow",
                    report.root_cause_node,
                )
                board.status = "escalated"
                board.escalation_reason = (
                    f"工具节点实现 bug: {report.root_cause_node} — {report.root_cause_explanation[:120]}"
                )
                self.store.save(board)
                break

            # 锁定 focus 节点（在 lock() 前捕获 ID，lock() 会把所有 ACTIVE→DORMANT）
            focus = board.focus_candidate()
            focus_id = focus.id if focus else ""
            if focus and focus.suspect_node:
                board.lock(focus.suspect_node)

            # B.3 受控实验（传入 focus_id 保证回归时能正确淘汰假设）
            exp_result = await self._experiment_runner.run(
                board, report, focus_hypothesis_id=focus_id
            )
            result.experiment_results.append(exp_result)
            await self._publish_event(
                "evolution.workflow.experiment_recorded",
                trace_id=board.trace_id,
                payload={
                    "board_id": board.board_id,
                    "cycle": cycle + 1,
                    "experiment_id": exp_result.experiment_id,
                    "verdict": exp_result.verdict,
                    "target_node": exp_result.proposed_change.target_node,
                },
            )

            # B.4 结果分析
            analysis = await self._result_analyzer.analyze(exp_result)
            result.analysis_results.append(analysis)
            await self._publish_event(
                "evolution.workflow.analysis_result",
                trace_id=board.trace_id,
                payload={
                    "board_id": board.board_id,
                    "cycle": cycle + 1,
                    "verdict": analysis.verdict,
                    "improvement_score": analysis.improvement_score,
                    "nodes_improved": len(analysis.nodes_improved),
                    "nodes_regressed": len(analysis.nodes_regressed),
                },
            )

            # B.5 黑板更新
            board = self._board_updater.update(board, analysis)
            result.board = board

            logger.info(
                "[orchestrator] Cycle %d done: verdict=%s  board_status=%s",
                cycle + 1, analysis.verdict, board.status,
            )
            await self._publish_event(
                "evolution.workflow.cycle_completed",
                trace_id=board.trace_id,
                payload={
                    "board_id": board.board_id,
                    "cycle": cycle + 1,
                    "board_status": board.status,
                },
            )

            if board.status in ("done", "escalated", "awaiting_user_input"):
                break

        result.final_status = (
            board.status if board.status in ("done", "escalated", "awaiting_user_input")
            else "max_cycles_reached"
        )

        logger.info(
            "[orchestrator] Evolution complete: status=%s cycles=%d",
            result.final_status, result.cycles,
        )
        await self._publish_event(
            "evolution.workflow.completed",
            trace_id=board.trace_id,
            payload={
                "board_id": board.board_id,
                "pipeline_id": board.pipeline_id,
                "final_status": result.final_status,
                "cycles": result.cycles,
                "diagnosis_reports": len(result.diagnosis_reports),
                "experiment_results": len(result.experiment_results),
            },
        )
        return result
