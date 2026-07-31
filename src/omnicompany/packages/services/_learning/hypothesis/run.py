# [OMNI] origin=claude-code domain=services/hypothesis ts=2026-07-10T00:00:00Z type=bindings status=active
# [OMNI] material_id="material:services.learning.hypothesis.team.bindings_builder.py"
"""hypothesis.run — 管线 bindings(v5:薄包装 Router 驱动 run_session)。

照 vilo eval 系模式:管线节点=薄确定性包装,内部驱动真实多轮 agent 循环
(run_session 自建 SQLiteBus 与两个 AgentNodeLoop)。修复旧 bindings 在
构造期裸建 AgentNodeLoop(缺 bus 必炸)导致 `omni run hypothesis` 不可用的问题。
"""

from __future__ import annotations

from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router


class HypothesisSessionRouter(Router):
    """单节点:hypothesis.session → hypothesis.store_diff(内部跑完整多轮循环)。"""

    DESCRIPTION = "假设探索会话驱动器:Experimenter+BeliefReflector 多轮循环,猜想直写统一决策库"
    FORMAT_IN = "hypothesis.session"
    FORMAT_OUT = "hypothesis.store_diff"

    async def run(self, input_data: Any) -> Verdict:
        from omnicompany.packages.services._learning.hypothesis.team import (
            _run_session_async,
            new_session,
        )

        data = input_data if isinstance(input_data, dict) else {}
        # 兼容两种形态: 直接顶层 {domain, goal, ...} 或 {"hypothesis.session": {...}}
        payload = data.get("hypothesis.session") if isinstance(data.get("hypothesis.session"), dict) else data
        domain = (payload.get("domain") or "").strip()
        goal = (payload.get("goal") or "").strip()
        if not domain or not goal:
            return Verdict(kind=VerdictKind.FAIL, output={},
                           diagnosis="缺 domain/goal(-i domain=<x> -i goal=\"...\")")
        try:
            max_iterations = int(payload.get("max_iterations") or 2)
        except (TypeError, ValueError):
            max_iterations = 2
        session = new_session(domain, goal, max_iterations=max_iterations,
                              scene=payload.get("scene") or {})
        result = await _run_session_async(session)
        return Verdict(kind=VerdictKind.PASS, output=result,
                       diagnosis=f"{domain}: belief {result.get('total_beliefs')} 条, "
                                 f"状态分布 {result.get('by_status')}")


def build_bindings(input_dict: dict | None = None) -> dict[str, Router]:
    return {"session": HypothesisSessionRouter()}
