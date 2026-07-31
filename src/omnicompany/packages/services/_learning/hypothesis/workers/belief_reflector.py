# [OMNI] origin=claude-code domain=services/hypothesis ts=2026-07-10T00:00:00Z type=router status=active
# [OMNI] summary="BeliefReflector(v5 收编版):总结 agent 直接读写统一决策库 belief——立猜想/挑战/裁定/连边,不再编辑 khyp markdown 文档。"
# [OMNI] why="决策本体合并清单#1:hypothesis 管线改读写决策库;探索与日常决策同一套设施(手册 20-探索通则);主题文档降为生成投影(30-知识)。"
# [OMNI] tags=hypothesis,reflector,belief,decision-ontology
"""BeliefReflectorRouter —— 假设探索的总结 agent(决策库直写版)。

读 Experimenter 行为轨迹 + 当前 belief 快照,用五件套工具维护统一决策库:
list_beliefs / record_belief / challenge_belief / resolve_belief / link_belief。
所有状态判定都是它的语义判断;库层校验(risk_if_wrong 必填/rests_on 必须真 id)是硬门。
"""

from __future__ import annotations

import json
import logging
from typing import Any, ClassVar

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.agent.agent_loop_config import (
    CompactConfig,
    LoopConfig,
    PermissionConfig,
)
from omnicompany.packages.services._core.agent import (
    AgentNodeLoop,
    ExtractResultRouter,
    PromptBuilderRouter,
    SingleToolRouter,
)
from omnicompany.packages.services._core.omnicompany import Worker

from ..belief_tools import (
    ChallengeBeliefRouter,
    LinkBeliefRouter,
    ListBeliefsRouter,
    RecordBeliefRouter,
    ResolveBeliefRouter,
    beliefs_snapshot,
)

log = logging.getLogger(__name__)


_BELIEF_REFLECTOR_PROMPT = """\
你是假设库的总结 agent。你把 Experimenter 的探索观察沉淀进**统一决策库**(belief 记录),
不写任何文档文件——库就是唯一真源,主题摘要由系统另行生成投影。

## 工具箱

- list_beliefs: 看本域当前全部猜想(动手前先看,别立同义重复)
- record_belief: 立新猜想。statement 一句话可证伪;risk_if_wrong 必填(错了多大代价);
  evidence_query 写怎么验证;由前面的猜想推出来的,rests_on 带上前提 id(推导必须带出处)
- challenge_belief: 看到反例就挑战(反证优先——先试图证伪,再谈支持)
- resolve_belief: 观察足够就裁定 supported/partial/falsified,必须带 evidence 与 method;
  证伪后返回值点名下游依赖——逐条复核,仍受影响的调 challenge_belief(回传必做)
- link_belief: 补边(rests_on/related/supersedes)
- finish: 结束

## 判断纪律(手册 docs/ontology/20-探索通则.md 的机器侧)

1. 未挑战过的猜想最高只能停在 untested——不要没经挑战就 resolve supported。
2. 一条观察支持 ≠ supported;反复支持且无反例才升。拿不准就留 untested/challenged。
3. 高风险(risk_if_wrong=high)猜想优先安排验证,不在其上叠推导。
4. 猜想要写成"可证伪的行为规律"(X 情况下会 Y),不是流水账。
5. 与既有猜想同义 → 不新立;是精化 → 新立并 supersedes 旧条。

全部中文。改完库后 finish,不需要总结陈词。
"""


class _BeliefReflectorPromptBuilder(PromptBuilderRouter):
    """首轮 prompt:轨迹 + 当前 belief 快照。"""

    def build_initial_messages(self, input_data: dict) -> list[dict]:
        trace = input_data.get("trace", []) or []
        domain = input_data.get("explore_domain", "")
        iteration = input_data.get("iteration", 0)
        session_id = input_data.get("session_id", "")
        snapshot = input_data.get("beliefs_snapshot") or {}

        lines = [
            f"## 探索域: {domain} · session {session_id[:8]} 第 {iteration} 轮",
            "",
            "## 当前 belief 快照(库内真态)",
            "```json",
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            "```",
            "",
            "## Experimenter 本轮行为轨迹",
        ]
        if trace:
            for i, t in enumerate(trace):
                lines.append(f"### [{i + 1}] 调用 `{t.get('tool', '?')}`")
                args = t.get("args", {})
                if args:
                    lines.append(f"参数: {json.dumps(args, ensure_ascii=False)}")
                result = t.get("result", "")
                if result:
                    lines.append(f"返回:\n```\n{result}\n```")
                lines.append("")
        else:
            lines.append("(本轮无工具调用)")

        lines.extend([
            "",
            "## 步骤建议",
            "1. 对照轨迹逐条过当前猜想:被支持的记证据(暂用 rationale 更新)、被反驳的 challenge_belief",
            "2. 观察里浮现的新规律 → record_belief(先 list_beliefs 防重复)",
            "3. 证据充分的才 resolve_belief;证伪后按返回值复核下游",
            "4. finish",
        ])
        return [{"role": "user", "content": "\n".join(lines)}]


class _BeliefReflectorExtractResult(ExtractResultRouter):
    """收尾:返回库内本域 belief 终态快照(确定性,不靠 agent 自述)。"""

    def __init__(self, *, bus: Any, domain_ref: dict):
        super().__init__(bus=bus)
        self._domain_ref = domain_ref  # {"domain": "..."}

    def extract(
        self, *, final_text: str, messages: list[dict], turn_count: int, stop_reason: str,
    ) -> Verdict:
        domain = self._domain_ref.get("domain") or ""
        result: dict[str, Any] = {"domain": domain}
        if domain:
            result["snapshot"] = beliefs_snapshot(domain)
        return Verdict(kind=VerdictKind.PASS, output=result)


class BeliefReflectorRouter(AgentNodeLoop):
    """总结 AgentNodeLoop:读轨迹,维护统一决策库 belief(五件套工具)。"""

    DESCRIPTION: ClassVar[str] = "总结 AgentNodeLoop:轨迹 → 统一决策库 belief 维护"
    FORMAT_IN: ClassVar[str] = "hypothesis.factlog"
    FORMAT_OUT: ClassVar[str] = "hypothesis.store_diff"

    NODE_PROMPT: ClassVar[str] = _BELIEF_REFLECTOR_PROMPT
    LOOP_CONFIG: ClassVar[LoopConfig] = LoopConfig(
        max_turns=60,
        compact=CompactConfig(auto_compact_enabled=False),
        permission=PermissionConfig(mode="default"),
    )
    TOOL_ROUTERS: ClassVar[list[type[SingleToolRouter]]] = [
        ListBeliefsRouter,
        RecordBeliefRouter,
        ChallengeBeliefRouter,
        ResolveBeliefRouter,
        LinkBeliefRouter,
        # FinishRouter 由基类自动追加
    ]

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("role", "runtime_main")
        self._domain_ref: dict = {"domain": None}
        self._session_ref: dict = {"session_id": ""}
        super().__init__(**kwargs)

    def build_prompt_builder(self, *, bus: Any) -> PromptBuilderRouter:
        return _BeliefReflectorPromptBuilder(template=self.NODE_PROMPT, bus=bus)

    def build_extract_result(self, *, bus: Any) -> ExtractResultRouter:
        return _BeliefReflectorExtractResult(bus=bus, domain_ref=self._domain_ref)

    def build_tool_context(self, *, input_data: dict, turn: int, trace_id: str) -> dict:
        """把探索域/会话注入工具上下文(SingleToolRouter._build_ctx 把未声明键透传给 ctx)。"""
        ctx = super().build_tool_context(input_data=input_data, turn=turn, trace_id=trace_id)
        ctx["explore_domain"] = self._domain_ref.get("domain") or ""
        ctx["hyp_session_id"] = self._session_ref.get("session_id") or ""
        ctx["origin"] = input_data.get("origin", "internal-engine")
        ctx["agent_name"] = "BeliefReflectorRouter"
        ctx["domain"] = "services/hypothesis"
        return ctx

    async def run(self, input_data: Any) -> Verdict:
        if isinstance(input_data, dict):
            self._domain_ref["domain"] = input_data.get("explore_domain")
            self._session_ref["session_id"] = input_data.get("session_id", "")
        return await super().run(input_data)


class BeliefReflectorWorker(Worker, BeliefReflectorRouter):
    """总结 AgentNodeLoop(Worker 形态):hypothesis.factlog → hypothesis.store_diff。"""


__all__ = ["BeliefReflectorRouter", "BeliefReflectorWorker"]
