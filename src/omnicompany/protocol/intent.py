# [OMNI] origin=human ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:protocol.task_intent.primitive.py"
"""Intent 原语（Phase 0）

Intent = 六元语义信号模型中的"任务请求"。
由 Consciousness Node（Judge）产生，表达"需要做什么"。

Intent 与 Signal 的区别：
  Signal = "发生了什么"（Hook/Node 的观测结论，已发生的事实）
  Intent = "需要做什么"（Judge Node 的决策输出，待完成的任务请求）

Intent 的生命周期：
  1. PainJudgeNode 判断"应该修复" → 产生 Intent(pain_solve)
  2. 调度层接收 Intent → 执行 repair_scheduler.run_repair_batch()
  3. CompletionHook 检测"修复完成" → 回传完成 Signal

现有 Intent 流示例：
  PainJudgeNode → {type="pain_solve", target_node_id="...", context="痛觉摘要"}
  FrontierJudgeNode → {type="frontier_explore", context="停滞摘要"}
  GuardianJudgeNode → {type="guardian_action", severity="high", context="健康报告"}

设计原则：Intent 是纯数据，不包含执行逻辑。
执行逻辑在 scheduler/runner 中，判断逻辑在 Judge Node 中。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Intent:
    """六元语义信号模型中的任务请求单元。

    由 Consciousness Node 的 Judge 阶段产生。
    携带"做什么"的语义描述，由调度层转化为具体执行。
    """

    type: str
    """意图类型标识，决定由哪个执行路径处理。
    例：'pain_solve', 'frontier_explore', 'guardian_action', 'evolution_trigger'
    """

    context: str
    """自然语言上下文描述，携带决策依据。
    执行层和 LLM 可直接理解这段文本。
    """

    input_format: str = ""
    """期望的输入 Signal 格式（LAP 类型约束）。"""

    output_format: str = ""
    """期望的输出 Signal 格式（LAP 类型约束）。"""

    target_node_id: str = ""
    """目标节点 ID（pain_solve 场景：待修复节点）。"""

    priority: str = "normal"
    """优先级语义：'critical' | 'high' | 'normal' | 'low'。
    不用数值，用语言表达优先级。
    """

    metadata: dict[str, Any] = field(default_factory=dict)
    """结构化辅助数据（round_num、source_signal 等）。"""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "context": self.context,
            "input_format": self.input_format,
            "output_format": self.output_format,
            "target_node_id": self.target_node_id,
            "priority": self.priority,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Intent":
        return cls(
            type=d.get("type", ""),
            context=d.get("context", ""),
            input_format=d.get("input_format", ""),
            output_format=d.get("output_format", ""),
            target_node_id=d.get("target_node_id", ""),
            priority=d.get("priority", "normal"),
            metadata=d.get("metadata", {}),
        )

    # ── 工厂方法：标准意图类型 ────────────────────────────────────────

    @classmethod
    def pain_solve(cls, target_node_id: str, context: str, priority: str = "high") -> "Intent":
        """构造痛觉修复意图。"""
        return cls(
            type="pain_solve",
            context=context,
            input_format="pain_signal",
            output_format="solved_pain_report",
            target_node_id=target_node_id,
            priority=priority,
        )

    @classmethod
    def frontier_explore(cls, context: str) -> "Intent":
        """构造前沿探索意图。"""
        return cls(
            type="frontier_explore",
            context=context,
            input_format="stagnation_signal",
            output_format="new_task_signal",
        )

    @classmethod
    def evolution_trigger(cls, context: str, target_node_id: str = "") -> "Intent":
        """构造进化触发意图。"""
        return cls(
            type="evolution_trigger",
            context=context,
            input_format="pain_signal",
            output_format="evolution_signal",
            target_node_id=target_node_id,
            priority="high",
        )
