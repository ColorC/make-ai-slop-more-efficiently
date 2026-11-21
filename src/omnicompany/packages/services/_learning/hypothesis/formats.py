# [OMNI] origin=claude-code domain=services/hypothesis ts=2026-07-10T00:00:00Z type=format status=active
# [OMNI] material_id="material:services.learning.hypothesis.format_definitions.py"
"""hypothesis.formats — 假设探索的语义类型定义(外置 AgentRuntime 版,4 个 Format)。

数据流:

  hypothesis.session ──┐
                       │  ExperimenterWorker (SOFT · 外置只读证据提案)
  hypothesis.store  ───┘  (store=统一决策库 belief 快照)
                       出: hypothesis.factlog
                           │  BeliefReflectorRouter (SOFT · 外置零工具提案)
                           │  Omni 整批校验后用 canonical decision library 应用
                       出: hypothesis.store_diff (=改库后终态快照)

真源=统一决策库 records.jsonl(kind=belief);本服务不再有自有存储。
旧 lockstep 专用 Format(step_observation/reflection_result/context_substitution)
已随决策本体合并清单#1 拆除。
"""

from __future__ import annotations

from omnicompany.protocol.format import Format, FormatRegistry


HYPOTHESIS_SESSION = Format(
    id="hypothesis.session",
    name="HypothesisSession",
    description=(
        "假设探索会话的配置,是整条循环的入口。"
        "`session_id` 为 uuid,作为事件流 trace_id 贯穿;"
        "`domain` 为探索域(如 'lark-cli'),决定 belief 的 domain:<x> 标签归属;"
        "`goal` 为自然语言目标,ExperimenterRouter 读此生成第一条 probe;"
        "`max_iterations` 防止无限循环,典型值 2-5。"
        "上游承诺:无(入口)。"
        "下游:ExperimenterRouter 读 goal/max_iterations 做决策。"
    ),
    parent="requirement",
    tags=["domain.hypothesis", "stage.config", "kind.source"],
    json_schema={
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "domain": {"type": "string"},
            "goal": {"type": "string"},
            "max_iterations": {"type": "integer"},
            "env": {"type": "object"},
            "scene": {"type": "object"},
        },
        "required": ["session_id", "domain", "goal"],
    },
)


HYPOTHESIS_FACTLOG = Format(
    id="hypothesis.factlog",
    name="HypothesisFactlog",
    description=(
        "Experimenter 单轮探索的 Omni 已验证证据轨迹；每条证据包含工作区相对路径、"
        "精确行区间和逐字命中的引用，外置运行时原始私有会话不构成业务真源。"
        "上游承诺:ExperimenterWorker 每轮探索结束时产出，伪造路径/行号/引文整批拒绝。"
        "下游消费:BeliefReflectorRouter 对照轨迹提出操作,由 Omni 验证后维护统一决策库 belief。"
    ),
    tags=["domain.hypothesis", "stage.trace", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "trace": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tool": {"type": "string"},
                        "args": {"type": "object"},
                        "result": {"type": "string"},
                    },
                    "required": ["tool", "args", "result"],
                },
            },
        },
        "required": ["trace"],
    },
)


HYPOTHESIS_STORE = Format(
    id="hypothesis.store",
    name="HypothesisStore",
    description=(
        "本探索域在统一决策库里的 belief 快照(只读投影,真源=records.jsonl)。"
        "entries 每条含 id(BLF-...)/state(status)/trigger(evidence_query)/predicted(statement)。"
        "上游承诺:pipeline 每轮开跑前由 belief_tools.beliefs_snapshot() 现算。"
        "下游消费:ExperimenterWorker 只读它决定探什么；DSH 不写该快照或决策库。"
    ),
    tags=["domain.hypothesis", "stage.snapshot", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "iteration": {"type": "integer"},
            "entries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "kind": {"type": "string"},
                        "state": {"type": "string"},
                        "trigger": {"type": "string"},
                        "predicted": {"type": "string"},
                    },
                },
            },
        },
    },
)


HYPOTHESIS_STORE_DIFF = Format(
    id="hypothesis.store_diff",
    name="HypothesisStoreDiff",
    description=(
        "BeliefReflector 收工快照:DSH 提案经 Omni 整批校验和 canonical library 应用后的终态。"
        "含 domain、snapshot(total/beliefs 列表)、applied 与 applied_count；不靠 agent 自述。"
        "上游承诺:BeliefReflectorRouter 回读决策库后产出。"
        "下游消费:pipeline 记事件/日志;30-知识 投影另由 knowledge_projection 重渲。"
    ),
    tags=["domain.hypothesis", "stage.result", "kind.sink"],
    json_schema={
        "type": "object",
        "properties": {
            "domain": {"type": "string"},
            "snapshot": {"type": "object"},
        },
    },
)


ALL_FORMATS = [
    HYPOTHESIS_SESSION,
    HYPOTHESIS_FACTLOG,
    HYPOTHESIS_STORE,
    HYPOTHESIS_STORE_DIFF,
]


def register_formats(registry: FormatRegistry) -> None:
    for fmt in ALL_FORMATS:
        if not registry.is_registered(fmt.id):
            registry.register(fmt)
