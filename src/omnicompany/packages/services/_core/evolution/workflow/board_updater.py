# [OMNI] origin=claude-code domain=evolution/workflow ts=2026-04-08T03:23:38Z
# [OMNI] material_id="material:core.evolution.workflow.board_state_updater.py"
"""B.5 黑板状态更新器

根据 B.4 的分析结果，更新 HypothesisBoard 中的假设状态：
- 实验成功（improved）→ 假设 CONFIRMED，黑板 done
- 实验无效（unchanged）→ 假设置信度下降，解锁继续探索
- 实验引起回归（regression）→ 假设 ELIMINATED，加入 what_not_to_change
- 所有假设 eliminated → 黑板 escalated
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from omnicompany.packages.services._core.evolution.workflow.hypothesis import HypothesisBoard, HypothesisStatus
from omnicompany.packages.services._core.evolution.workflow.hypothesis_store import HypothesisBoardStore
from omnicompany.packages.services._core.evolution.workflow.result_analyzer import AnalysisResult

logger = logging.getLogger(__name__)

_CONFIDENCE_DECAY_UNCHANGED = 0.3
"""实验无效时置信度衰减系数（乘以此系数）"""

_CONFIDENCE_BOOST_IMPROVED = 0.2
"""实验改善时其他假设置信度提升（用于兄弟假设间的学习）"""


class BoardUpdater:
    """B.5 黑板状态更新器

    用法：
        updater = BoardUpdater(store)
        board = updater.update(board, analysis)
    """

    def __init__(self, store: HypothesisBoardStore):
        self.store = store

    def update(self, board: HypothesisBoard, analysis: AnalysisResult) -> HypothesisBoard:
        """根据实验分析结果更新黑板，返回更新后的黑板（已持久化）"""

        exp_result = analysis.experiment_result
        hypothesis_id = exp_result.hypothesis_id
        focus = board.get_hypothesis(hypothesis_id)

        verdict = analysis.verdict
        logger.info(
            "[board_updater] Updating board %s: verdict=%s hypothesis=%s",
            board.board_id, verdict, hypothesis_id,
        )

        if verdict == "improved":
            self._handle_improved(board, focus, analysis)
        elif verdict == "regression":
            self._handle_regression(board, focus, analysis)
        elif verdict in ("unchanged", "inconclusive"):
            self._handle_unchanged(board, focus, analysis)
        elif verdict in ("failed_to_apply", "requires_human"):
            self._handle_failed(board, focus, analysis)

        # 更新实验记录结果
        if board.experiment_log:
            for rec in board.experiment_log:
                if rec.id == exp_result.experiment_id:
                    rec.outcome = verdict
                    rec.causal_explanation = analysis.conclusion
                    rec.regressed_traces = list(analysis.nodes_regressed)
                    rec.newly_passing_traces = (
                        [exp_result.experiment_trace_id]
                        if exp_result.experiment_trace_id and verdict == "improved"
                        else []
                    )
                    rec.completed_at = datetime.now(timezone.utc)
                    break

        board.updated_at = datetime.now(timezone.utc)

        # 检查是否需要升级
        if board.should_escalate() and board.status not in ("done", "escalated"):
            board.status = "escalated"
            board.escalation_reason = (
                "所有假设已被证伪或置信度过低，需要人工介入"
            )
            logger.warning("[board_updater] Board %s escalated", board.board_id)

        self.store.save(board)
        return board

    def _handle_improved(self, board, focus, analysis):
        """实验成功：固化假设，更新实验 trace"""
        if focus:
            focus.status = HypothesisStatus.CONFIRMED
            focus.confidence = 1.0
            focus.last_experiment_outcome = "PASS"
            if analysis.experiment_result.experiment_trace_id:
                focus.contradicting_traces = []

        board.status = "done"
        board.modification_lock = None
        board.active_experiment_id = None
        logger.info(
            "[board_updater] Hypothesis %s CONFIRMED — board done", focus.id if focus else "?"
        )

    def _handle_regression(self, board, focus, analysis):
        """实验引起回归：淘汰假设，记录不可修改范围"""
        if focus:
            focus.status = HypothesisStatus.ELIMINATED
            focus.confidence = 0.0
            focus.last_experiment_outcome = "FAIL_WITH_REGRESSION"
            focus.anti_pattern = (
                f"修改 {focus.suspect_node} 导致 {analysis.nodes_regressed} 回归"
            )

        # 解锁让其他假设继续
        board.unlock()
        logger.info(
            "[board_updater] Hypothesis %s ELIMINATED (regression)", focus.id if focus else "?"
        )

    def _handle_unchanged(self, board, focus, analysis):
        """实验无效：降低置信度，继续探索"""
        if focus:
            focus.confidence *= _CONFIDENCE_DECAY_UNCHANGED
            focus.last_experiment_outcome = "FAIL_NO_REGRESSION"
            if focus.confidence < 0.05:
                focus.status = HypothesisStatus.ELIMINATED
                logger.info(
                    "[board_updater] Hypothesis %s eliminated (confidence too low)", focus.id
                )

        # 解锁，继续探索其他假设
        board.unlock()
        logger.info(
            "[board_updater] Hypothesis confidence decayed, continuing exploration"
        )

    def _handle_failed(self, board, focus, analysis):
        """实验未能执行：保持状态，解锁继续"""
        if focus:
            focus.last_experiment_outcome = "FAIL_NO_REGRESSION"
            # 记录人工建议
            if analysis.experiment_result.patch_code:
                focus.statement += (
                    f"\n[待人工实验] 补丁描述：{analysis.experiment_result.patch_description}"
                )

        board.unlock()
        logger.info(
            "[board_updater] Experiment not applied (requires_human or failed_to_apply)"
        )
