# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:44Z
# [OMNI] material_id="material:routing.boltzmann_selection.engine.py"
"""玻尔兹曼路由 — 热力学能量分布选路 + Fisher 收敛审计

理论对应：
  - 03§二.4  P(c) = exp(-β·P_c)·S_c / Σ exp(-β·P_i)·S_i
  - 定论§3.1  Fisher 基本定理：Validator 固定窗口内 avg pass_rate 单调不降
  - N8.6      β 退火：初期广泛探索 → 收集痛觉信号 → 趋向开发最优路径

模块职责：
  RouteCandidate     — 候选节点的摘要快照
  BoltzmannRouter    — 核心选路引擎（玻尔兹曼分布 + 退火）
  ConvergenceAuditor — 记录每轮 pass_rate，检查 Fisher 单调性
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────
# RouteCandidate
# ────────────────────────────────────────────────────────────

@dataclass
class RouteCandidate:
    """候选节点快照——BoltzmannRouter.select() 的输入元素"""

    node_id: str
    pain_score: float        # P_c(k) ∈ [0, 1]
    success_rate: float      # S_c — EMA 成功率，-1.0 = 无数据
    hit_count: int = 0
    deprecated: bool = False
    hard_eliminated: bool = False
    embedding_sim: float = 0.0  # 与任务文本的 embedding 相似度（可选）


# ────────────────────────────────────────────────────────────
# BoltzmannRouter
# ────────────────────────────────────────────────────────────

class BoltzmannRouter:
    """玻尔兹曼路由——用热力学能量分布替代 if-else 选路

    P(c) = exp(-β · P_c) · S_c / Σ_i exp(-β · P_i) · S_i

    β 越大越贪婪（趋向低痛觉高成功率），β 越小越探索。
    """

    def __init__(self, beta: float = 2.0):
        self.beta = beta

    def select(self, candidates: list[RouteCandidate]) -> RouteCandidate | None:
        """按玻尔兹曼分布选择路由目标。

        过滤规则：
          - hard_eliminated → 永远排除
          - deprecated → 排除（保留作为避开信号但不参与选路）
        """
        active = [c for c in candidates if not c.hard_eliminated and not c.deprecated]
        if not active:
            return None

        weights = self.compute_weights(active)
        total = sum(weights)
        if total <= 0:
            return random.choice(active) if active else None

        chosen = random.choices(active, weights=weights, k=1)[0]
        return chosen

    def compute_weights(self, candidates: list[RouteCandidate]) -> list[float]:
        """计算未归一化的玻尔兹曼权重。"""
        weights: list[float] = []
        for c in candidates:
            s_c = max(c.success_rate, 0.01) if c.success_rate >= 0 else 0.5
            pain_factor = math.exp(-self.beta * c.pain_score)
            weights.append(pain_factor * s_c)
        return weights

    def compute_probabilities(self, candidates: list[RouteCandidate]) -> list[float]:
        """计算归一化概率分布（用于分析/测试）。"""
        active = [c for c in candidates if not c.hard_eliminated and not c.deprecated]
        if not active:
            return []
        weights = self.compute_weights(active)
        total = sum(weights)
        if total <= 0:
            n = len(active)
            return [1.0 / n] * n
        return [w / total for w in weights]

    def anneal(self, round_k: int, schedule: str = "linear") -> None:
        """退火调度——随轮数增加 β，从探索趋向开发。

        linear:      β = 1.0 + 0.1 × round_k，上限 10.0
        exponential: β = 1.0 × 1.05^round_k，上限 10.0
        """
        if schedule == "linear":
            self.beta = min(10.0, 1.0 + 0.1 * round_k)
        elif schedule == "exponential":
            self.beta = min(10.0, 1.0 * (1.05 ** round_k))


# ────────────────────────────────────────────────────────────
# ConvergenceAuditor
# ────────────────────────────────────────────────────────────

@dataclass
class AuditRecord:
    """单轮审计记录"""
    round_k: int
    pass_rate: float          # 本轮的全局 pass_rate
    validator_version: str    # Validator 的版本标识（用于检测 Validator 变化）
    beta: float = 0.0


class ConvergenceAuditor:
    """Fisher 收敛审计器

    在 Validator 固定的窗口内，检查 avg pass_rate 是否单调不降。
    Validator 版本发生变化时，重置窗口。

    理论对应（定论§3.1）：
      Fisher 基本定理 → 在固定环境下，选择压使得 avg pass_rate 单调不降。
      若观测到 pass_rate 显著下降 → Validator 可能已变化，或系统有 bug。
    """

    def __init__(self, window_size: int = 10, tolerance: float = 0.02):
        self.window_size = window_size
        self.tolerance = tolerance
        self.records: list[AuditRecord] = []

    def record(self, round_k: int, pass_rate: float, validator_version: str, beta: float = 0.0) -> None:
        self.records.append(AuditRecord(
            round_k=round_k,
            pass_rate=pass_rate,
            validator_version=validator_version,
            beta=beta,
        ))

    def check_monotonicity(self) -> bool:
        """检查当前 Validator 窗口内 pass_rate 是否单调不降。

        返回 True = 满足 Fisher 条件；False = 违反（发出警告）。
        """
        if len(self.records) < 2:
            return True

        current_validator = self.records[-1].validator_version
        window = [
            r for r in self.records
            if r.validator_version == current_validator
        ][-self.window_size:]

        if len(window) < 2:
            return True

        for i in range(1, len(window)):
            if window[i].pass_rate < window[i - 1].pass_rate - self.tolerance:
                logger.warning(
                    "Fisher monotonicity violated: round %d pass_rate=%.4f < round %d pass_rate=%.4f "
                    "(validator=%s, tolerance=%.4f)",
                    window[i].round_k, window[i].pass_rate,
                    window[i - 1].round_k, window[i - 1].pass_rate,
                    current_validator, self.tolerance,
                )
                return False

        return True

    def get_window_trend(self) -> list[float]:
        """返回当前 Validator 窗口内的 pass_rate 序列。"""
        if not self.records:
            return []
        current_validator = self.records[-1].validator_version
        window = [
            r for r in self.records
            if r.validator_version == current_validator
        ][-self.window_size:]
        return [r.pass_rate for r in window]
