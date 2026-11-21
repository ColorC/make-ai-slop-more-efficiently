# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:44Z
# [OMNI] material_id="material:runtime.nodes.pain_system.signal_routers.py"
"""痛觉系统节点 — 分类、传播、奖励、溢出判定

从 semantic.py 拆分。
"""

from __future__ import annotations

import logging
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

logger = logging.getLogger(__name__)


class PainClassifyRouter(Router):
    """痛觉分类节点 — 从 trace step 中分类痛觉事件。

    可以用不同的分类标准替换 → 所以是节点。
    """

    INPUT_KEYS = ["trace_step"]

    def __init__(self, graph: Any = None):
        self.graph = graph

    def run(self, input_data: Any) -> Verdict:
        from omnicompany.runtime.signals.pain_system import PainClassifier

        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.PASS, output={"pain_event": None})

        classifier = PainClassifier()
        trace_step = input_data.get("trace_step", {})

        event = classifier.classify(
            trace_step=trace_step,
            exit_code=trace_step.get("exit_code"),
            token_cost=trace_step.get("token_cost", 0),
            violations=trace_step.get("violations", 0),
            is_success=trace_step.get("is_success", False),
            steps_used=trace_step.get("step_num", 0),
            steps_budget=trace_step.get("steps_budget", 50),
        )

        if event:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"pain_event": event, "has_pain": True},
            )
        return Verdict(
            kind=VerdictKind.PASS,
            output={"pain_event": None, "has_pain": False},
        )


class PainPropagateRouter(Router):
    """痛觉传播节点 — 沿路由图反向传播痛觉信号。"""

    INPUT_KEYS = ["pain_event"]

    def __init__(self, graph: Any = None):
        self.graph = graph

    def run(self, input_data: Any) -> Verdict:
        from omnicompany.runtime.signals.pain_system import PainPropagator

        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.PASS, output={})

        pain_event = input_data.get("pain_event")
        if pain_event is None:
            return Verdict(kind=VerdictKind.PASS, output={"propagated": False})

        if self.graph is None:
            return Verdict(kind=VerdictKind.PASS, output={"propagated": False})

        propagator = PainPropagator(self.graph)
        steps = input_data.get("trace_steps", [])
        updated = propagator.propagate(pain_event, steps)

        return Verdict(
            kind=VerdictKind.PASS,
            output={"propagated": True, "updated_nodes": list(updated)},
        )


class RewardComputeRouter(Router):
    """奖励计算节点 — 从 trace 数据计算六维综合奖励。"""

    INPUT_KEYS = ["actual_tokens", "budget_tokens"]

    def __init__(self, param_registry: Any = None):
        self._param_registry = param_registry

    def run(self, input_data: Any) -> Verdict:
        from omnicompany.runtime.signals.reward import RewardSignal

        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.PASS, output={"reward": 0.0})

        sig = RewardSignal.from_trace(
            actual_tokens=input_data.get("actual_tokens", 0),
            budget_tokens=input_data.get("budget_tokens", 10000),
            actual_time=input_data.get("actual_time", 0.0),
            budget_time=input_data.get("budget_time", 300.0),
            new_route_nodes=input_data.get("new_route_nodes", 0),
            total_steps=input_data.get("total_steps", 1),
            failed_steps=input_data.get("failed_steps", 0),
            mirror_fresh=input_data.get("mirror_fresh", False),
            pain_before=input_data.get("pain_before", 0.0),
            pain_after=input_data.get("pain_after", 0.0),
            param_registry=self._param_registry,
        )

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "reward_composite": sig.composite,
                "reward_dimensions": sig.dimensions,
            },
        )


class EscalationCheckRouter(Router):
    """溢出判定节点 — 决定是否从运行时升级到进化层。

    这个判据本身可以被元进化修改（因为它是 Router，逻辑可替换）。
    """

    INPUT_KEYS = ["avg_pain"]
    THRESHOLD = 0.5

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.PASS, output={"escalate": False})

        avg_pain = input_data.get("avg_pain", 0.0)
        should_escalate = avg_pain > self.THRESHOLD

        return Verdict(
            kind=VerdictKind.FAIL if should_escalate else VerdictKind.PASS,
            output={
                **input_data,
                "escalate": should_escalate,
                "avg_pain": avg_pain,
                "threshold": self.THRESHOLD,
            },
        )
