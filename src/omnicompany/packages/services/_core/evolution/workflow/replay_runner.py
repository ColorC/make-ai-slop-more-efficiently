# [OMNI] origin=claude-code domain=evolution/workflow ts=2026-04-08T03:23:38Z
# [OMNI] material_id="material:core.evolution.workflow.node_replay_engine.py"
"""B.3.5 节点重放程序 (ReplayRunner)

从事件总线中读取 record_io 全量 I/O 数据，对目标节点注入补丁 Router，
重跑该节点及其下游的 LLM 节点（ANCHOR SOFT），跳过会对外部系统造成
永久副作用的节点（直接沿用原始 trace 的 verdict）。

结果写回 ExperimentRecord，不产生新 trace。

设计原则：
- 只重跑 ANCHOR SOFT 节点（LLM），以及位于其间的 TRANSFORMER 节点
- 对"是否有副作用"的判断，通过 LLM 风险墙读取节点源码决定
- 所有判断和结果附在实验记录上，不污染主 trace
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from omnicompany.bus.sqlite import SQLiteBus
from omnicompany.packages.services._core.evolution.workflow.diagnosis import (
    _load_node_source,
    _load_pipeline_spec,
)
from omnicompany.packages.services._core.evolution.workflow.hypothesis import HypothesisBoard
from omnicompany.protocol.anchor import Verdict
from omnicompany.protocol.events import FactoryEvent
from omnicompany.protocol.team import NodeKind, TeamSpec
from omnicompany.runtime.llm.llm import LLMClient
from omnicompany.runtime.routing.router import Router

logger = logging.getLogger(__name__)


# ── 重放结果数据结构 ──


@dataclass
class NodeReplayResult:
    """单个节点的重放结果"""

    node_id: str
    original_verdict: str
    replay_verdict: str          # "SKIPPED" 表示跳过（副作用节点）
    direction: str               # improved | regressed | unchanged | skipped
    skipped_reason: str = ""
    original_output_preview: str = ""
    replay_output_preview: str = ""


@dataclass
class ReplayResult:
    """完整重放结果，附在 ExperimentRecord 上"""

    patched_node: str
    patch_type: str              # prompt | logic

    node_results: list[NodeReplayResult] = field(default_factory=list)
    skipped_nodes: list[str] = field(default_factory=list)

    verdict: str = "unknown"
    """improved | unchanged | regression | replay_limited（跳过节点太多，无法判断）"""

    improvement_score: float = 0.0
    """节点改善比例 0.0~1.0"""

    notes: str = ""


# ── LLM 风险墙 ──

_SYSTEM_RISK_WALL = """\
你是一个代码安全分析员。给定一个 Python Router 类的源码，判断：
运行这个 Router 的 run() 方法会不会对外部文件系统或数据库产生**永久性**副作用？

永久性副作用的例子：
- 写入项目目录中的实际源文件（非 temp 目录）
- 执行 git commit / push
- 修改数据库记录
- 发送网络请求造成不可撤销的状态变更

不算永久性副作用：
- 写入 tempfile.mkdtemp() 等临时目录（程序结束后消失）
- 运行 tsc / cargo / pytest 等只读编译/检查命令
- 纯读取文件
- LLM API 调用（只读）

你必须用以下 JSON 格式回答（不含其他文字）：
{
    "has_permanent_side_effects": true 或 false,
    "reason": "一句话说明判断依据"
}
"""


class RiskWall:
    """LLM 风险墙：读取 Router 源码，判断重放是否安全"""

    def __init__(self, llm: LLMClient, pipeline_id: str):
        self._llm = llm
        self._pipeline_id = pipeline_id
        self._cache: dict[str, bool] = {}  # node_id → has_permanent_side_effects

    def is_safe_to_replay(self, node_id: str) -> tuple[bool, str]:
        """返回 (safe, reason)"""
        if node_id in self._cache:
            return not self._cache[node_id], "cached"

        source = _load_node_source(node_id, self._pipeline_id)
        if source.startswith("# 未找到"):
            self._cache[node_id] = False
            return False, "找不到源码，保守跳过"

        try:
            response = self._llm.call(
                messages=[{"role": "user", "content": f"以下是节点 {node_id} 的 Router 源码：\n\n{source[:6000]}"}],
                system=_SYSTEM_RISK_WALL,
            )
            text = ""
            if hasattr(response, "content"):
                for block in response.content:
                    if hasattr(block, "text"):
                        text += block.text
            elif isinstance(response, str):
                text = response
            text = text.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            parsed = json.loads(text.strip())
            has_side_effects = bool(parsed.get("has_permanent_side_effects", True))
            reason = parsed.get("reason", "")
        except Exception as e:
            logger.warning("[replay] risk wall failed for %s: %s, defaulting to unsafe", node_id, e)
            has_side_effects = True
            reason = f"风险墙判断失败，保守跳过: {e}"

        self._cache[node_id] = has_side_effects
        safe = not has_side_effects
        logger.info("[replay] %s risk=%s: %s", node_id, "UNSAFE" if has_side_effects else "safe", reason)
        return safe, reason


# ── 从事件总线提取全量 I/O ──


def _extract_io_from_events(
    events: list[FactoryEvent],
) -> dict[str, dict[str, Any]]:
    """从 record_io 事件中提取每个节点的完整输入输出。

    返回 {node_id: {"input": ..., "output": ..., "verdict": ...}}
    """
    node_io: dict[str, dict[str, Any]] = {}

    for ev in events:
        payload = ev.payload
        node_id = payload.get("node")
        if not node_id:
            continue

        if node_id not in node_io:
            node_io[node_id] = {}

        # enter 事件: input_data
        if "input_data" in payload and "verdict" not in payload:
            node_io[node_id]["input"] = payload["input_data"]

        # exit 事件: output_data + verdict
        if "verdict" in payload:
            node_io[node_id]["verdict"] = payload["verdict"]
            if "output_data" in payload:
                node_io[node_id]["output"] = payload["output_data"]

    return node_io


# ── 拓扑排序工具 ──


def _downstream_subgraph(start_node: str, spec: TeamSpec) -> list[str]:
    """从 start_node 往下做 BFS，返回所有下游节点（含 start_node，拓扑序）"""
    fwd: dict[str, list[str]] = {}
    for edge in spec.edges:
        if getattr(edge, "feedback", False):
            continue
        fwd.setdefault(edge.source, []).append(edge.target)

    result: list[str] = []
    visited: set[str] = set()
    queue = [start_node]
    while queue:
        cur = queue.pop(0)
        if cur in visited:
            continue
        visited.add(cur)
        result.append(cur)
        for nxt in fwd.get(cur, []):
            queue.append(nxt)
    return result


# ── 主程序 ──


class ReplayRunner:
    """B.3.5 节点重放程序

    用法：
        rr = ReplayRunner(llm=llm_client)  # Move 8: unified path
        result = await rr.run(board, patched_node_id, patched_router_cls, patch_type="prompt")
    """

    def __init__(
        self,
        bus_path: str | None = None,  # Move 8: None → unified data/events.db
        llm: LLMClient | None = None,
    ):
        self.bus_path = bus_path
        self._llm = llm or LLMClient()

    async def run(
        self,
        board: HypothesisBoard,
        patched_node_id: str,
        patched_router_cls: Any,
        patch_type: str = "prompt",
    ) -> ReplayResult:
        """执行节点重放，返回 ReplayResult（不产生新 trace）"""
        result = ReplayResult(patched_node=patched_node_id, patch_type=patch_type)

        # 1. 加载原始 trace 全量 I/O
        bus_path = board.__dict__.get("_bus_path") or self.bus_path
        bus = SQLiteBus(bus_path)
        await bus.connect()
        try:
            events = await bus.read_trace(board.trace_id)
        finally:
            await bus.close()

        if not events:
            result.verdict = "replay_limited"
            result.notes = "原始 trace 无事件"
            return result

        node_io = _extract_io_from_events(events)

        # 检查目标节点是否有全量 I/O 数据
        if patched_node_id not in node_io or "input" not in node_io[patched_node_id]:
            result.verdict = "replay_limited"
            result.notes = (
                f"节点 {patched_node_id} 无 record_io 数据。"
                "请用 record_io=True 重跑原始管线以启用重放。"
            )
            return result

        # 2. 确定下游子图
        spec = _load_pipeline_spec(board.pipeline_id)
        if spec is None:
            result.verdict = "replay_limited"
            result.notes = f"找不到 pipeline spec: {board.pipeline_id}"
            return result

        subgraph = _downstream_subgraph(patched_node_id, spec)
        logger.info("[replay] subgraph from %s: %s", patched_node_id, subgraph)

        # 3. 风险墙：判断下游各节点是否可以重放
        risk_wall = RiskWall(self._llm, board.pipeline_id)

        # 4. 实例化补丁 Router，重跑目标节点
        try:
            patched_router = patched_router_cls()
        except Exception as e:
            result.verdict = "replay_limited"
            result.notes = f"无法实例化补丁 Router: {e}"
            return result

        # 累积输出：初始化为目标节点的原始输入
        accumulated_output: dict[str, Any] = dict(node_io[patched_node_id].get("input", {}))

        # 5. 按子图顺序逐节点重放
        improved = 0
        regressed = 0
        unchanged_count = 0

        for node_id in subgraph:
            original_io = node_io.get(node_id, {})
            original_verdict = original_io.get("verdict", "UNKNOWN")

            # 判断节点类型
            node_spec = next((n for n in spec.nodes if n.id == node_id), None)
            is_llm = (
                node_spec is not None
                and node_spec.kind == NodeKind.ANCHOR
                and node_spec.anchor is not None
                and hasattr(node_spec.anchor, "validator")
                and node_spec.anchor.validator is not None
                and node_spec.anchor.validator.kind.value == "soft"
            )

            # 只重跑 LLM 节点（ANCHOR SOFT），对其余节点检查风险
            if node_id != patched_node_id:
                if not is_llm:
                    # 非 LLM 节点：用原始 output 作为下一节点的输入，不重跑
                    if "output" in original_io:
                        accumulated_output = dict(original_io["output"])
                    node_results_entry = NodeReplayResult(
                        node_id=node_id,
                        original_verdict=original_verdict,
                        replay_verdict="SKIPPED",
                        direction="skipped",
                        skipped_reason="非 LLM 节点，沿用原始输出",
                        original_output_preview=str(original_io.get("output", ""))[:200],
                    )
                    result.node_results.append(node_results_entry)
                    result.skipped_nodes.append(node_id)
                    continue

                # LLM 节点（非目标节点）：先过风险墙
                safe, reason = risk_wall.is_safe_to_replay(node_id)
                if not safe:
                    if "output" in original_io:
                        accumulated_output = dict(original_io["output"])
                    node_results_entry = NodeReplayResult(
                        node_id=node_id,
                        original_verdict=original_verdict,
                        replay_verdict="SKIPPED",
                        direction="skipped",
                        skipped_reason=f"风险墙: {reason}",
                    )
                    result.node_results.append(node_results_entry)
                    result.skipped_nodes.append(node_id)
                    continue

            # 执行节点（目标节点用补丁 Router，其他 LLM 节点用原始 Router）
            router_to_use: Router
            if node_id == patched_node_id:
                router_to_use = patched_router
            else:
                # 从 bindings 或直接实例化原始 Router（无法从这里拿到 bindings）
                # 退路：如果没有 bindings，也跳过非目标 LLM 节点
                if "output" in original_io:
                    accumulated_output = dict(original_io["output"])
                node_results_entry = NodeReplayResult(
                    node_id=node_id,
                    original_verdict=original_verdict,
                    replay_verdict="SKIPPED",
                    direction="skipped",
                    skipped_reason="非目标节点 LLM，无 bindings 可重跑",
                )
                result.node_results.append(node_results_entry)
                result.skipped_nodes.append(node_id)
                continue

            try:
                import inspect as _inspect
                if _inspect.iscoroutinefunction(router_to_use.run):
                    verdict_obj: Verdict = await router_to_use.run(accumulated_output)
                else:
                    import asyncio as _asyncio
                    verdict_obj = await _asyncio.to_thread(router_to_use.run, accumulated_output)
                replay_verdict = verdict_obj.kind.value
                replay_output = verdict_obj.output if isinstance(verdict_obj.output, dict) else {}
                accumulated_output = {**accumulated_output, **replay_output}
            except Exception as e:
                logger.error("[replay] node %s run failed: %s", node_id, e)
                replay_verdict = "ERROR"
                replay_output = {}

            # 比较 verdict
            orig_v = original_verdict.upper()
            rep_v = replay_verdict.upper()
            if orig_v == "FAIL" and rep_v == "PASS":
                direction = "improved"
                improved += 1
            elif orig_v == "PASS" and rep_v == "FAIL":
                direction = "regressed"
                regressed += 1
            else:
                direction = "unchanged"
                unchanged_count += 1

            result.node_results.append(NodeReplayResult(
                node_id=node_id,
                original_verdict=original_verdict,
                replay_verdict=replay_verdict,
                direction=direction,
                original_output_preview=str(original_io.get("output", ""))[:200],
                replay_output_preview=str(replay_output)[:200],
            ))

        # 6. 综合判断
        total_run = improved + regressed + unchanged_count
        if total_run == 0:
            result.verdict = "replay_limited"
            result.notes = "所有节点都被跳过，无法判断改善情况"
        elif regressed > 0:
            result.verdict = "regression"
            result.improvement_score = max(0.0, improved / total_run - regressed / total_run)
        elif improved > 0:
            result.verdict = "improved"
            result.improvement_score = improved / total_run
        else:
            result.verdict = "unchanged"
            result.improvement_score = 0.0

        logger.info(
            "[replay] %s → verdict=%s improved=%d regressed=%d skipped=%d",
            patched_node_id, result.verdict, improved, regressed, len(result.skipped_nodes),
        )
        return result
