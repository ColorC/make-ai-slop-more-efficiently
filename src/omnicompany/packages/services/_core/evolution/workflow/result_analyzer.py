# [OMNI] origin=claude-code domain=evolution/workflow ts=2026-04-08T03:23:38Z
# [OMNI] material_id="material:core.evolution.workflow.experiment_result_analyzer.py"
"""B.4 结果分析器

对比实验前后两次 trace 的节点判定差异，
量化改善程度，检测副作用（回归）。

输出 AnalysisResult 供 B.5 BoardUpdater 使用。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from omnicompany.bus.sqlite import SQLiteBus
from omnicompany.packages.services._core.evolution.workflow.experiment_runner import ExperimentResult
from omnicompany.packages.services._core.evolution.workflow.shallow_tracer import _extract_node_traces, NodeTraceResult

logger = logging.getLogger(__name__)


@dataclass
class NodeDiff:
    """单节点前后对比"""
    node_id: str
    before_verdict: str
    after_verdict: str
    verdict_changed: bool
    direction: str
    """improved | regressed | unchanged"""


@dataclass
class AnalysisResult:
    """B.4 分析结果"""

    experiment_result: ExperimentResult

    # 节点级对比
    node_diffs: list[NodeDiff] = field(default_factory=list)

    # 汇总
    nodes_improved: list[str] = field(default_factory=list)
    nodes_regressed: list[str] = field(default_factory=list)
    nodes_unchanged: list[str] = field(default_factory=list)

    # 量化
    improvement_score: float = 0.0
    """正数=改善，负数=退化，范围 -1~1"""

    # 最终结论
    verdict: str = "unchanged"
    """improved | unchanged | regression | inconclusive"""

    conclusion: str = ""
    """自然语言结论（一句话）"""


async def _load_node_map(bus_path: str, trace_id: str) -> dict[str, NodeTraceResult]:
    """加载 trace 的节点结果，按 node_id 索引"""
    bus = SQLiteBus(bus_path)
    await bus.connect()
    try:
        events = await bus.read_trace(trace_id)
    finally:
        await bus.close()

    if not events:
        return {}

    nodes = _extract_node_traces(events)
    return {n.node_id: n for n in nodes}


def _verdict_score(verdict: str) -> float:
    """将 verdict 转换为数值分（越高越好）"""
    v = verdict.upper()
    if v == "PASS":
        return 1.0
    elif v == "PARTIAL":
        return 0.5
    elif v == "FAIL":
        return 0.0
    return 0.3  # UNKNOWN / other


class ResultAnalyzer:
    """B.4 结果分析器

    用法：
        analyzer = ResultAnalyzer()  # Move 8: unified path
        analysis = await analyzer.analyze(experiment_result)
    """

    def __init__(
        self,
        bus_path: str | None = None,  # Move 8: None → unified data/events.db
        experiment_bus_path: str | None = None,
    ):
        from omnicompany.core.config import resolve_unified_db_path
        self.bus_path = bus_path or str(resolve_unified_db_path("events.db"))
        # Move 8: experiment_bus_path 之前是 _exp.db 后缀分裂；现在 fall back 到同一
        # unified events.db（experiment 事件靠 source 字段区分）。引擎层无论如何会
        # 把任何外部路径折回 unified。
        self.experiment_bus_path = experiment_bus_path or self.bus_path

    async def analyze(self, exp_result: ExperimentResult) -> AnalysisResult:
        """对比实验前后 trace，生成分析报告"""
        analysis = AnalysisResult(experiment_result=exp_result)

        # 如果实验未成功执行，直接返回
        if exp_result.verdict in ("failed_to_apply", "requires_human") or not exp_result.experiment_trace_id:
            analysis.verdict = exp_result.verdict
            analysis.conclusion = exp_result.notes or "实验未执行"
            return analysis

        # 加载前后节点结果
        before_nodes = await _load_node_map(self.bus_path, exp_result.original_trace_id)
        after_nodes = await _load_node_map(self.experiment_bus_path, exp_result.experiment_trace_id)

        if not before_nodes or not after_nodes:
            analysis.verdict = "inconclusive"
            analysis.conclusion = "无法读取实验 trace 数据"
            return analysis

        # 对比每个节点
        all_node_ids = set(before_nodes) | set(after_nodes)
        score_delta = 0.0
        n_compared = 0

        for node_id in all_node_ids:
            before = before_nodes.get(node_id)
            after = after_nodes.get(node_id)

            if before is None or after is None:
                # 节点出现/消失（管线路径不同）
                continue

            before_v = before.verdict_kind
            after_v = after.verdict_kind
            changed = before_v != after_v

            delta = _verdict_score(after_v) - _verdict_score(before_v)
            score_delta += delta
            n_compared += 1

            if delta > 0:
                direction = "improved"
                analysis.nodes_improved.append(node_id)
            elif delta < 0:
                direction = "regressed"
                analysis.nodes_regressed.append(node_id)
            else:
                direction = "unchanged"
                analysis.nodes_unchanged.append(node_id)

            analysis.node_diffs.append(NodeDiff(
                node_id=node_id,
                before_verdict=before_v,
                after_verdict=after_v,
                verdict_changed=changed,
                direction=direction,
            ))

        logger.info(
            "[result_analyzer] %d nodes compared: +%d -%d ==%d",
            n_compared, len(analysis.nodes_improved),
            len(analysis.nodes_regressed), len(analysis.nodes_unchanged),
        )

        # 归一化改善分
        if n_compared > 0:
            analysis.improvement_score = score_delta / n_compared
        else:
            analysis.improvement_score = 0.0

        # 最终结论
        has_improvement = len(analysis.nodes_improved) > 0
        has_regression = len(analysis.nodes_regressed) > 0

        if has_regression and not has_improvement:
            analysis.verdict = "regression"
            analysis.conclusion = (
                f"变更引起回归：{analysis.nodes_regressed} 判定变差，无改善"
            )
        elif has_improvement and not has_regression:
            analysis.verdict = "improved"
            analysis.conclusion = (
                f"变更有效：{analysis.nodes_improved} 改善，无回归"
            )
        elif has_improvement and has_regression:
            if analysis.improvement_score > 0:
                analysis.verdict = "improved"
                analysis.conclusion = (
                    f"变更净改善（score={analysis.improvement_score:.2f}）："
                    f"改善 {analysis.nodes_improved}，但 {analysis.nodes_regressed} 出现回归"
                )
            else:
                analysis.verdict = "regression"
                analysis.conclusion = (
                    f"变更净退化（score={analysis.improvement_score:.2f}）："
                    f"改善 {analysis.nodes_improved}，回归 {analysis.nodes_regressed}"
                )
        else:
            analysis.verdict = "unchanged"
            analysis.conclusion = "变更无可测量效果（所有节点判定相同）"

        logger.info("[result_analyzer] Verdict: %s — %s", analysis.verdict, analysis.conclusion[:80])
        return analysis
