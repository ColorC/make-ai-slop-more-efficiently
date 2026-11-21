# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:44Z
# [OMNI] material_id="material:runtime.signals.reward_computation.engine.py"
"""RewardSignal — 综合奖励函数

理论对应：
  终点§3   准则明确 — 基于多维度的综合奖励
  03§四    "管线"和"动力"的双分结构

设计原则：
  - 管线：TeamSpec + TeamRunner + OperatorSpec → 定义数据如何流转
  - 动力：PainSystem + BoltzmannRouter + RewardSignal → 定义数据为什么这样流转
  - 两者用统一抽象实现：动力系统的信号也通过 EventBus 流转

六维奖励：
  1. token_efficiency:     1 - (actual_tokens / budget_tokens)
  2. time_efficiency:      1 - (actual_time / budget_time)
  3. semantic_richness:    路由图新增有效节点数 / 总步骤数
  4. self_awareness_score: MirrorNode 概念的新鲜度
  5. error_rate:           1 - (failed_steps / total_steps)
  6. pain_delta:           本轮全局痛觉场的净变化量

权重可由元进化调整（初始值手写 = 冷启动问题）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class RewardSignal:
    """综合奖励信号 — 同时是运行时评价和进化方向指引"""

    token_efficiency: float = 0.0
    time_efficiency: float = 0.0
    semantic_richness: float = 0.0
    self_awareness_score: float = 0.0
    error_rate: float = 0.0
    pain_delta: float = 0.0
    workspace_cleanliness: float = 0.0

    W_TOKEN: float = 0.22
    W_TIME: float = 0.10
    W_SEMANTIC: float = 0.20
    W_AWARENESS: float = 0.10
    W_ERROR: float = 0.20
    W_PAIN: float = 0.10
    W_CLEAN: float = 0.08

    def load_weights_from_registry(self, param_registry: Any) -> None:
        """从 ParamRegistry 读取可进化权重，使元进化对奖励系统生效。"""
        if param_registry is None:
            return
        try:
            self.W_TOKEN = param_registry.get_or_default("reward.w_token", self.W_TOKEN)
            self.W_TIME = param_registry.get_or_default("reward.w_time", self.W_TIME)
            self.W_SEMANTIC = param_registry.get_or_default("reward.w_semantic", self.W_SEMANTIC)
            self.W_AWARENESS = param_registry.get_or_default("reward.w_awareness", self.W_AWARENESS)
            self.W_ERROR = param_registry.get_or_default("reward.w_error", self.W_ERROR)
            self.W_PAIN = param_registry.get_or_default("reward.w_pain", self.W_PAIN)
        except Exception:
            pass

    @property
    def composite(self) -> float:
        """加权综合得分 ∈ [0, 1]。"""
        raw = (
            self.W_TOKEN * self._clamp(self.token_efficiency)
            + self.W_TIME * self._clamp(self.time_efficiency)
            + self.W_SEMANTIC * self._clamp(self.semantic_richness)
            + self.W_AWARENESS * self._clamp(self.self_awareness_score)
            + self.W_ERROR * self._clamp(self.error_rate)
            + self.W_PAIN * self._clamp(1.0 - abs(self.pain_delta))
            + self.W_CLEAN * self._clamp(self.workspace_cleanliness)
        )
        return self._clamp(raw)

    @property
    def dimensions(self) -> dict[str, float]:
        """返回所有维度及其值。"""
        return {
            "token_efficiency": self.token_efficiency,
            "time_efficiency": self.time_efficiency,
            "semantic_richness": self.semantic_richness,
            "self_awareness_score": self.self_awareness_score,
            "error_rate": self.error_rate,
            "pain_delta": self.pain_delta,
            "workspace_cleanliness": self.workspace_cleanliness,
        }

    @staticmethod
    def _clamp(v: float) -> float:
        return max(0.0, min(1.0, v))

    @classmethod
    def from_trace(
        cls,
        *,
        actual_tokens: int,
        budget_tokens: int,
        actual_time: float,
        budget_time: float,
        new_route_nodes: int,
        total_steps: int,
        failed_steps: int,
        mirror_fresh: bool,
        pain_before: float,
        pain_after: float,
        workspace_cleanliness: float = 0.0,
        param_registry: Any = None,
    ) -> "RewardSignal":
        """从 trace 数据构造 RewardSignal。"""
        token_eff = 1.0 - (actual_tokens / max(budget_tokens, 1))
        time_eff = 1.0 - (actual_time / max(budget_time, 0.001))
        semantic = new_route_nodes / max(total_steps, 1)
        awareness = 1.0 if mirror_fresh else 0.5
        error = 1.0 - (failed_steps / max(total_steps, 1))
        pain_d = pain_after - pain_before

        sig = cls(
            token_efficiency=token_eff,
            time_efficiency=time_eff,
            semantic_richness=semantic,
            self_awareness_score=awareness,
            error_rate=error,
            pain_delta=pain_d,
            workspace_cleanliness=workspace_cleanliness,
        )
        sig.load_weights_from_registry(param_registry)
        return sig
