# [OMNI] origin=human ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:protocol.node_processing.primitive_abc.py"
"""Node 原语 ABC（Phase 0）

Node = 接收 Signal、产生 Signal 的处理单元（含 LLM 调用）。

两类 Node：
  BaseNode          — 普通 Node：Signal(Format_A) → Signal(Format_B)
  ConsciousnessNode — 意识 Node：Signal(任意) → Intent(in_Format, out_Format)
                      内部分 Monitor / Judge 两个角色

现有实现：
  omnicompany.runtime.nodes.consciousness.PainJudgeNode(ConsciousnessNode)
  omnicompany.runtime.nodes.consciousness.RoutingGapJudgeNode(ConsciousnessNode)
  omnicompany.runtime.nodes.consciousness.FrontierJudgeNode(ConsciousnessNode)

路线图中待建：
  PainMonitorNode — 聚合多个 Pain Signal → pain_state_summary
  GuardianMonitorNode — 聚合系统健康信号
  EvidenceCollectNode / ErrorLocalizeNode / DeltaComputeNode / MutationApplyNode
    （Phase 3 Evolution Consciousness 环）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseNode(ABC):
    """普通 Node：Signal → Signal 转换。

    实现规范：
    - process() 可以调用 LLM 做决策
    - 输入输出都是 Signal（dict 或 Signal dataclass）
    - 不直接操作外部系统（通过 Tool）
    """

    @abstractmethod
    def process(self, signal: Any) -> Any:
        """接收 Signal，返回处理后的 Signal（或 None 表示忽略）。"""
        ...


class ConsciousnessNode(ABC):
    """意识 Node：Signal → Intent 或 决策。

    意识节点是四元架构中的决策中心。它不直接操作外部系统，
    而是产生 Intent（任务请求），由下游 Node/Tool 执行。

    内部角色：
      Monitor — 聚合多个 Signal，维护状态摘要
      Judge   — 基于状态摘要做决策，产生 Intent

    实现规范：
    - decide() 判断是否产生 Intent，返回执行参数或 None
    - 每个节点有独立冷却期，防止重复触发
    - 冷却期参数应可被进化修改（通过 processing_prompt 驱动）
    """

    @abstractmethod
    def decide(self, signal: Any, *args: Any, **kwargs: Any) -> Any:
        """接收 Signal，返回执行参数（触发下游）或 None（不触发）。"""
        ...

    @abstractmethod
    def ready(self, *args: Any) -> bool:
        """判断冷却期是否结束，可以再次触发。"""
        ...
