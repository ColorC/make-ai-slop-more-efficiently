# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:43Z
# [OMNI] material_id="material:runtime.exec.dag_specification.operator_definitions.py"
"""graph_builder — 声明式构建运行时 DAG

将 run_agent() 里的所有 if-else / while 硬编码
转化为显式的 GraphSpec + 节点绑定。

单次执行 = DAG（无循环）。
"多步 agent" = GraphRunner 多次执行 DAG（外层循环在底座）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import (
    AnchorSpec,
    OperatorDomain,
    OperatorSpec,
    Route,
    RouteAction,
    TransformerSpec,
    TransformMethod,
    ValidatorKind,
    ValidatorSpec,
    VerdictKind,
)
from omnicompany.protocol.team import (
    GraphEdge,
    GraphNode,
    GraphSpec,
    NodeKind,
)
from omnicompany.runtime.routing.router import Router
from omnicompany.runtime.storage.db_access import open_db_rw


RUNTIME_OPERATOR_SPECS: dict[str, OperatorSpec] = {
    "context": OperatorSpec(
        id="context",
        name="Context Assembler",
        domain=OperatorDomain.RUNTIME,
        input_types=["trace.log.tool_observation"],
        output_types=["omnicompany.json.agent_state"],
        deterministic=True,
        source_file="omnicompany/runtime/graph_builder.py",
    ),
    "truth_inject": OperatorSpec(
        id="truth_inject",
        name="Truth Injector",
        domain=OperatorDomain.RUNTIME,
        input_types=["omnicompany.json.agent_state", "omnicompany.markdown.self_concept"],
        output_types=["omnicompany.json.agent_state"],
        deterministic=True,
        source_file="omnicompany/runtime/graph_builder.py",
    ),
    "llm": OperatorSpec(
        id="llm",
        name="LLM Semantic Router",
        domain=OperatorDomain.RUNTIME,
        input_types=["omnicompany.json.agent_state"],
        output_types=["omnicompany.json.agent_action"],
        deterministic=False,
        source_file="omnicompany/runtime/graph_builder.py",
    ),
    "death_zone": OperatorSpec(
        id="death_zone",
        name="Death Zone Guard",
        domain=OperatorDomain.RUNTIME,
        input_types=["omnicompany.json.agent_action"],
        output_types=["omnicompany.json.agent_action"],
        deterministic=True,
        source_file="omnicompany/runtime/graph_builder.py",
    ),
    "intent_parse": OperatorSpec(
        id="intent_parse",
        name="Intent Parser",
        domain=OperatorDomain.RUNTIME,
        input_types=["omnicompany.json.agent_action"],
        output_types=["omnicompany.json.agent_intent_bundle"],
        deterministic=True,
        source_file="omnicompany/runtime/graph_builder.py",
    ),
    "task_intent_parse": OperatorSpec(
        id="task_intent_parse",
        name="Task Intent Parser",
        domain=OperatorDomain.RUNTIME,
        input_types=["user_request", "omnicompany.trace.intent_tracer"],
        output_types=["omnicompany.json.parsed_user_intent", "omnicompany.trace.intent_tracer"],
        deterministic=False,
        source_file="omnicompany/runtime/nodes/semantic.py",
    ),
    "trace_accumulate": OperatorSpec(
        id="trace_accumulate",
        name="Trace Accumulator",
        domain=OperatorDomain.RUNTIME,
        input_types=["omnicompany.trace.intent_tracer", "omnicompany.json.route_graph"],
        output_types=["omnicompany.json.route_graph", "trace.log.route_accumulation"],
        deterministic=False,
        source_file="omnicompany/runtime/nodes/semantic.py",
    ),
    "route_retrieve": OperatorSpec(
        id="route_retrieve",
        name="Route Retriever",
        domain=OperatorDomain.RUNTIME,
        input_types=["omnicompany.json.agent_intent_bundle", "omnicompany.json.route_graph_dump"],
        output_types=["omnicompany.json.route_candidates"],
        deterministic=True,
        source_file="omnicompany/runtime/graph_builder.py",
    ),
    "boltzmann_select": OperatorSpec(
        id="boltzmann_select",
        name="Boltzmann Route Selector",
        domain=OperatorDomain.RUNTIME,
        input_types=["omnicompany.json.route_candidates"],
        output_types=["omnicompany.json.selected_route"],
        deterministic=False,
        source_file="omnicompany/runtime/graph_builder.py",
    ),
    "tool_dispatch": OperatorSpec(
        id="tool_dispatch",
        name="Tool Dispatch",
        domain=OperatorDomain.RUNTIME,
        input_types=["omnicompany.json.selected_route", "omnicompany.json.agent_action"],
        output_types=["trace.log.tool_execution"],
        deterministic=False,
        source_file="omnicompany/runtime/graph_builder.py",
    ),
    "pain_classify": OperatorSpec(
        id="pain_classify",
        name="Pain Classifier",
        domain=OperatorDomain.RUNTIME,
        input_types=["trace.log.tool_execution"],
        output_types=["trace.log.pain_event"],
        deterministic=False,
        source_file="omnicompany/runtime/graph_builder.py",
    ),
    "pain_propagate": OperatorSpec(
        id="pain_propagate",
        name="Pain Propagator",
        domain=OperatorDomain.RUNTIME,
        input_types=["trace.log.pain_event"],
        output_types=["trace.log.pain_propagation_snapshot"],
        deterministic=True,
        source_file="omnicompany/runtime/graph_builder.py",
    ),
    "route_accumulate": OperatorSpec(
        id="route_accumulate",
        name="Route Accumulator",
        domain=OperatorDomain.RUNTIME,
        input_types=["trace.log.tool_execution", "trace.log.pain_propagation_snapshot"],
        output_types=["omnicompany.json.route_graph_dump"],
        deterministic=False,
        source_file="omnicompany/runtime/graph_builder.py",
    ),
    "reward_compute": OperatorSpec(
        id="reward_compute",
        name="Reward Computer",
        domain=OperatorDomain.RUNTIME,
        input_types=["trace.log.tool_execution", "trace.log.pain_propagation_snapshot"],
        output_types=["trace.log.reward_signal"],
        deterministic=True,
        source_file="omnicompany/runtime/graph_builder.py",
    ),
    "escalation_check": OperatorSpec(
        id="escalation_check",
        name="Escalation Check",
        domain=OperatorDomain.RUNTIME,
        input_types=["trace.log.reward_signal", "trace.log.pain_propagation_snapshot"],
        output_types=["trace.log.escalation_decision"],
        deterministic=True,
        source_file="omnicompany/runtime/graph_builder.py",
    ),
    "convergence_audit": OperatorSpec(
        id="convergence_audit",
        name="Convergence Audit",
        domain=OperatorDomain.RUNTIME,
        input_types=["trace.log.escalation_decision"],
        output_types=["trace.log.fisher_monotonicity_audit"],
        deterministic=True,
        source_file="omnicompany/runtime/graph_builder.py",
    ),
    "guardian_check": OperatorSpec(
        id="guardian_check",
        name="Guardian Health Check",
        domain=OperatorDomain.RUNTIME,
        input_types=["trace.log.fisher_monotonicity_audit"],
        output_types=["trace.log.pipeline_health"],
        deterministic=True,
        source_file="omnicompany/runtime/graph_builder.py",
    ),
    "semantic_classify": OperatorSpec(
        id="semantic_classify",
        name="Semantic Type Classifier",
        domain=OperatorDomain.RUNTIME,
        input_types=["omnicompany.json.parsed_user_intent"],
        output_types=["omnicompany.json.semantic_classification"],
        deterministic=False,
        source_file="omnicompany/runtime/nodes/semantic.py",
    ),
    "specialized_dispatch": OperatorSpec(
        id="specialized_dispatch",
        name="Specialized Pipeline Dispatcher",
        domain=OperatorDomain.RUNTIME,
        input_types=["omnicompany.json.semantic_classification"],
        output_types=["omnicompany.json.dispatch_result"],
        deterministic=False,
        source_file="omnicompany/runtime/nodes/semantic.py",
    ),
}


def _hard_anchor(id: str, name: str, fmt_in: str, fmt_out: str, desc: str, routes: dict) -> AnchorSpec:
    return AnchorSpec(
        id=id, name=name, format_in=fmt_in, format_out=fmt_out,
        validator=ValidatorSpec(id=id, kind=ValidatorKind.HARD, description=desc),
        routes=routes,
    )


def _soft_anchor(id: str, name: str, fmt_in: str, fmt_out: str, desc: str, routes: dict) -> AnchorSpec:
    return AnchorSpec(
        id=id, name=name, format_in=fmt_in, format_out=fmt_out,
        validator=ValidatorSpec(id=id, kind=ValidatorKind.SOFT, description=desc),
        routes=routes,
    )


def _next(target: str, fb: str) -> Route:
    return Route(action=RouteAction.NEXT, target=target, feedback=fb)


def build_runtime_graph() -> GraphSpec:
    """构建运行时 DAG（一次 step 的完整路径，~15 节点）

    DAG 路径:
        context → truth_inject → llm ──PASS──→ [finish/EMIT]
            └─FAIL──→ death_zone ──PASS──→ intent_parse → route_retrieve → boltzmann_select
                                                          → tool_dispatch → pain_classify → pain_propagate
                                                          → reward_compute → escalation_check
                                                          → convergence_audit → guardian_check → context
                                  └─FAIL──→ context (blocked)

    原则：不短路，每个语义操作都是显式节点。
    """
    _pass_next = lambda t, fb: {VerdictKind.PASS: _next(t, fb)}
    _both_next = lambda t, fb: {VerdictKind.PASS: _next(t, fb), VerdictKind.FAIL: _next(t, fb)}

    from omnicompany.protocol.team import NodeMaturity

    nodes = [
        # ── 意图解析 ──
        GraphNode(id="task_intent_parse", kind=NodeKind.ANCHOR,
                  operator=RUNTIME_OPERATOR_SPECS["task_intent_parse"],
                  anchor=_soft_anchor("task-intent-router", "任务意图解析器",
                                      "user_request", "parsed_intent",
                                      "将用户请求解析为结构化意图",
                                      _pass_next("semantic_classify", "意图解析完成"))),
        # ── 语义分类 ──
        GraphNode(id="semantic_classify", kind=NodeKind.ANCHOR,
                  maturity=NodeMaturity.HYPOTHETICAL,
                  operator=RUNTIME_OPERATOR_SPECS["semantic_classify"],
                  anchor=_soft_anchor("semantic-classify-router", "语义类型分类器",
                                      "parsed_intent", "semantic_classification",
                                      "将意图分类到已知语义类型或标记为未知",
                                      {VerdictKind.PASS: _next("specialized_dispatch", "已知类型匹配"),
                                       VerdictKind.FAIL: _next("context", "未知类型→探针模式")})),
        # ── 专用管线分发 ──
        GraphNode(id="specialized_dispatch", kind=NodeKind.ANCHOR,
                  maturity=NodeMaturity.HYPOTHETICAL,
                  operator=RUNTIME_OPERATOR_SPECS["specialized_dispatch"],
                  anchor=_soft_anchor("specialized-dispatch-router", "专用管线分发器",
                                      "semantic_classification", "dispatch_result",
                                      "根据语义类型分发到专用管线或回退到agent_loop",
                                      {VerdictKind.PASS: _next("trace_accumulate", "DAG直达完成→积累trace"),
                                       VerdictKind.FAIL: _next("context", "路径不完整→agent_loop探索")})),
        # ── 消息准备阶段 ──
        GraphNode(
            id="context",
            kind=NodeKind.TRANSFORMER,
            operator=RUNTIME_OPERATOR_SPECS["context"],
            transformer=TransformerSpec(
                id="context-router", name="Context 拼接器",
                from_format="tool-observation", to_format="agent-state",
                method=TransformMethod.RULE,
                description="将 user_input / tool_results 拼接为 messages",
            ),
        ),
        GraphNode(id="truth_inject", kind=NodeKind.ANCHOR,
                  operator=RUNTIME_OPERATOR_SPECS["truth_inject"],
                  anchor=_hard_anchor("truth-inject", "真相注入",
                                      "agent-state", "agent-state",
                                      "将 MirrorNode 自我认知注入 system_prompt",
                                      _pass_next("llm", "认知注入完成"))),
        # ── LLM 决策 ──
        GraphNode(id="llm", kind=NodeKind.ANCHOR,
                  operator=RUNTIME_OPERATOR_SPECS["llm"],
                  anchor=_soft_anchor("llm-router", "LLM 语义整流器",
                                      "agent-state", "agent-action",
                                      "LLM 调用",
                                      {VerdictKind.PASS: _next("trace_accumulate", "任务完成→积累trace"),
                                       VerdictKind.FAIL: _next("death_zone", "需要工具执行")})),
        # ── 安全检查 ──
        GraphNode(id="death_zone", kind=NodeKind.ANCHOR,
                  operator=RUNTIME_OPERATOR_SPECS["death_zone"],
                  anchor=_hard_anchor("death-zone", "禁区拦截",
                                      "agent-action", "agent-action",
                                      "不可变安全规则前置检查",
                                      {VerdictKind.PASS: _next("intent_parse", "安全通过"),
                                       VerdictKind.FAIL: _next("context", "禁区拦截")})),
        # ── 意图分析阶段 ──
        GraphNode(id="intent_parse", kind=NodeKind.ANCHOR,
                  operator=RUNTIME_OPERATOR_SPECS["intent_parse"],
                  anchor=_hard_anchor("intent-parse", "意图解析",
                                      "agent-action", "agent-action-with-intent",
                                      "从 tool_calls 提取结构化意图",
                                      _pass_next("route_retrieve", "意图解析完成"))),
        GraphNode(id="route_retrieve", kind=NodeKind.ANCHOR,
                  operator=RUNTIME_OPERATOR_SPECS["route_retrieve"],
                  anchor=_hard_anchor("route-retrieve", "路由检索",
                                      "agent-action-with-intent", "agent-action-with-route",
                                      "从历史路由图检索相关路径",
                                      _pass_next("boltzmann_select", "路由检索完成"))),
        GraphNode(id="boltzmann_select", kind=NodeKind.ANCHOR,
                  operator=RUNTIME_OPERATOR_SPECS["boltzmann_select"],
                  anchor=_hard_anchor("boltzmann-select", "玻尔兹曼路径选择",
                                      "agent-action-with-route", "agent-action-selected",
                                      "基于能量的概率路由选择",
                                      _pass_next("tool_dispatch", "路径选择完成"))),
        # ── 工具执行 ──
        GraphNode(id="tool_dispatch", kind=NodeKind.ANCHOR,
                  operator=RUNTIME_OPERATOR_SPECS["tool_dispatch"],
                  anchor=_hard_anchor("tool-dispatch", "工具执行分发",
                                      "agent-action-selected", "tool-observation",
                                      "按 tool_name 分发到 bash/editor/think",
                                      _both_next("pain_classify", "工具执行完成"))),
        # ── 痛觉与奖励阶段 ──
        GraphNode(id="pain_classify", kind=NodeKind.ANCHOR,
                  operator=RUNTIME_OPERATOR_SPECS["pain_classify"],
                  anchor=_hard_anchor("pain-classify", "痛觉分类",
                                      "tool-observation", "tool-observation-with-pain",
                                      "对工具执行结果进行痛觉分类",
                                      _both_next("pain_propagate", "痛觉分类完成"))),
        GraphNode(id="pain_propagate", kind=NodeKind.ANCHOR,
                  operator=RUNTIME_OPERATOR_SPECS["pain_propagate"],
                  anchor=_hard_anchor("pain-propagate", "痛觉传播",
                                      "tool-observation-with-pain", "tool-observation-propagated",
                                      "沿路由图反向传播痛觉信号",
                                      _pass_next("route_accumulate", "痛觉传播完成"))),
        GraphNode(id="route_accumulate", kind=NodeKind.ANCHOR,
                  operator=RUNTIME_OPERATOR_SPECS["route_accumulate"],
                  anchor=_hard_anchor("route-accumulate", "实时路由积累",
                                      "tool-observation-propagated", "tool-observation-routed",
                                      "将当前步骤的意图+结果实时写入路由图",
                                      _pass_next("reward_compute", "路由积累完成"))),
        GraphNode(id="reward_compute", kind=NodeKind.ANCHOR,
                  operator=RUNTIME_OPERATOR_SPECS["reward_compute"],
                  anchor=_hard_anchor("reward-compute", "奖励计算",
                                      "tool-observation-propagated", "tool-observation-with-reward",
                                      "六维综合奖励计算",
                                      _pass_next("escalation_check", "奖励计算完成"))),
        # ── 系统健康阶段 ──
        GraphNode(id="escalation_check", kind=NodeKind.ANCHOR,
                  operator=RUNTIME_OPERATOR_SPECS["escalation_check"],
                  anchor=_hard_anchor("escalation-check", "溢出判定",
                                      "tool-observation-with-reward", "tool-observation-checked",
                                      "判定是否从运行时升级到进化层",
                                      _both_next("convergence_audit", "溢出判定完成"))),
        GraphNode(id="convergence_audit", kind=NodeKind.ANCHOR,
                  operator=RUNTIME_OPERATOR_SPECS["convergence_audit"],
                  anchor=_hard_anchor("convergence-audit", "收敛审计",
                                      "tool-observation-checked", "tool-observation-audited",
                                      "检查 Fisher 单调性",
                                      _pass_next("guardian_check", "收敛审计完成"))),
        GraphNode(id="guardian_check", kind=NodeKind.ANCHOR,
                  operator=RUNTIME_OPERATOR_SPECS["guardian_check"],
                  anchor=_hard_anchor("guardian-check", "守护进程健康检查",
                                      "tool-observation-audited", "tool-observation-final",
                                      "心跳/超时检查",
                                      _pass_next("context", "健康检查完成→继续agent_loop"))),
        GraphNode(id="trace_accumulate", kind=NodeKind.ANCHOR,
                  operator=RUNTIME_OPERATOR_SPECS["trace_accumulate"],
                  anchor=_soft_anchor("trace-accumulate-router", "路由积累器",
                                      "trace_log", "route_graph",
                                      "积累本次执行的 trace 到 route_graph",
                                      {VerdictKind.PASS: Route(action=RouteAction.EMIT, feedback="路由积累完成，任务结束"),
                                       VerdictKind.FAIL: Route(action=RouteAction.EMIT, feedback="积累失败但任务结束")})),
    ]

    edges = [
        # 意图解析 → 语义分类
        GraphEdge(source="task_intent_parse", target="semantic_classify", label="意图解析完成"),
        # 语义分类 → 分支
        GraphEdge(source="semantic_classify", target="specialized_dispatch", condition=VerdictKind.PASS, label="已知类型"),
        GraphEdge(source="semantic_classify", target="context", condition=VerdictKind.FAIL, label="未知类型→探针"),
        # 专用管线分发 → DAG直达 or agent_loop
        GraphEdge(source="specialized_dispatch", target="trace_accumulate", condition=VerdictKind.PASS, label="DAG直达完成"),
        GraphEdge(source="specialized_dispatch", target="context", condition=VerdictKind.FAIL, label="路径不完整→agent_loop"),
        # 消息准备
        GraphEdge(source="context", target="truth_inject", label="messages 就绪"),
        GraphEdge(source="truth_inject", target="llm", label="认知注入完成"),
        # LLM 决策
        GraphEdge(source="llm", target="trace_accumulate", condition=VerdictKind.PASS, label="任务完成→积累trace"),
        GraphEdge(source="llm", target="death_zone", condition=VerdictKind.FAIL, label="需要工具"),
        # 安全分支
        GraphEdge(source="death_zone", target="intent_parse", condition=VerdictKind.PASS, label="安全通过"),
        GraphEdge(source="death_zone", target="context", condition=VerdictKind.FAIL, label="禁区拦截"),
        # 意图 → 路由 → 选择 → 执行
        GraphEdge(source="intent_parse", target="route_retrieve", label="意图解析完成"),
        GraphEdge(source="route_retrieve", target="boltzmann_select", label="路由检索完成"),
        GraphEdge(source="boltzmann_select", target="tool_dispatch", label="路径选择完成"),
        # 执行 → 痛觉链
        GraphEdge(source="tool_dispatch", target="pain_classify", label="工具完成"),
        GraphEdge(source="pain_classify", target="pain_propagate", label="痛觉分类完成"),
        GraphEdge(source="pain_propagate", target="route_accumulate", label="痛觉传播完成"),
        GraphEdge(source="route_accumulate", target="reward_compute", label="路由积累完成"),
        # 系统健康链
        GraphEdge(source="reward_compute", target="escalation_check", label="奖励计算完成"),
        GraphEdge(source="escalation_check", target="convergence_audit", label="溢出判定完成"),
        GraphEdge(source="convergence_audit", target="guardian_check", label="收敛审计完成"),
        GraphEdge(source="guardian_check", target="context", label="健康检查完成"),
    ]

    return GraphSpec(
        id="runtime-dag-v3",
        name="LAP Runtime DAG v3 — 完整节点化",
        description="16 节点运行时 DAG：task_intent_parse → context → truth_inject → llm → death_zone → intent → route → boltzmann → tool → pain → propagate → reward → escalation → convergence → guardian → trace_accumulate",
        nodes=nodes,
        edges=edges,
        entry="task_intent_parse",
    )


def apply_topology_mutations(
    graph: GraphSpec,
    mutation_state: Any,
    *,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> tuple[GraphSpec, dict[str, Router]]:
    """将 MutationState 中的活跃拓扑变异应用到 runtime DAG

    返回 (修改后的 GraphSpec, 新增节点的 bindings)。
    调用者需要将 bindings 合并到总 bindings 中。
    """
    from omnicompany.runtime.routing.router import DynamicRouter

    if mutation_state is None:
        return graph, {}

    active_topos = getattr(mutation_state, "active_topology_mutations", [])
    if not active_topos:
        return graph, {}

    import logging
    _logger = logging.getLogger(__name__)

    nodes = list(graph.nodes)
    edges = list(graph.edges)
    new_bindings: dict[str, Router] = {}
    node_ids = {n.id for n in nodes}

    for topo in active_topos:
        if topo.new_node_id in node_ids:
            continue
        if topo.target_node_id not in node_ids:
            _logger.warning("Topo target '%s' not in DAG, skipping", topo.target_node_id)
            continue

        action = topo.action.upper()
        new_node = GraphNode(
            id=topo.new_node_id,
            kind=NodeKind.ANCHOR,
            anchor=_soft_anchor(
                topo.new_node_id, topo.new_node_description,
                "agent-state", "agent-state", topo.new_node_description,
                {VerdictKind.PASS: _next("__placeholder__", "dynamic node done")},
            ),
        )

        if action == "INSERT_BEFORE":
            incoming = [(e, i) for i, e in enumerate(edges) if e.target == topo.target_node_id]
            for edge, idx in incoming:
                edges[idx] = GraphEdge(
                    source=edge.source, target=topo.new_node_id,
                    condition=edge.condition, label=edge.label,
                )
            edges.append(GraphEdge(
                source=topo.new_node_id, target=topo.target_node_id,
                label=f"{topo.new_node_id} → {topo.target_node_id}",
            ))
            new_node.anchor.routes[VerdictKind.PASS] = _next(topo.target_node_id, "pre-processing done")

        elif action == "INSERT_AFTER":
            outgoing = [(e, i) for i, e in enumerate(edges) if e.source == topo.target_node_id]
            first_target = outgoing[0][0].target if outgoing else "context"
            for edge, idx in outgoing:
                edges[idx] = GraphEdge(
                    source=topo.new_node_id, target=edge.target,
                    condition=edge.condition, label=edge.label,
                )
            edges.append(GraphEdge(
                source=topo.target_node_id, target=topo.new_node_id,
                label=f"{topo.target_node_id} → {topo.new_node_id}",
            ))
            new_node.anchor.routes[VerdictKind.PASS] = _next(first_target, "post-processing done")

        elif action == "SPLIT":
            incoming = [(e, i) for i, e in enumerate(edges) if e.target == topo.target_node_id]
            for edge, idx in incoming:
                edges[idx] = GraphEdge(
                    source=edge.source, target=topo.new_node_id,
                    condition=edge.condition, label=edge.label,
                )
            edges.append(GraphEdge(
                source=topo.new_node_id, target=topo.target_node_id,
                label=f"{topo.new_node_id} → {topo.target_node_id}",
            ))
            new_node.anchor.routes[VerdictKind.PASS] = _next(topo.target_node_id, "split phase 1 done")
        else:
            _logger.warning("Unknown topo action '%s', skipping", action)
            continue

        nodes.append(new_node)
        node_ids.add(topo.new_node_id)
        new_bindings[topo.new_node_id] = DynamicRouter(
            node_prompt=topo.new_node_prompt,
            node_description=topo.new_node_description,
            model=model,
            base_url=base_url,
            api_key=api_key,
        )
        _logger.info("Applied topo mutation: %s '%s' at '%s'",
                     action, topo.new_node_id, topo.target_node_id)

    new_graph = GraphSpec(
        id=graph.id,
        name=graph.name,
        description=graph.description,
        nodes=nodes,
        edges=edges,
        entry=graph.entry,
    )
    return new_graph, new_bindings


def build_runtime_bindings(
    *,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: int = 30,
    route_db_path: str | None = None,
    semantic_network_db_path: str | None = None,
    mirror: Any = None,
    route_graph: Any = None,
    mutation_state: Any = None,
    guardian: Any = None,
    param_registry: Any = None,
    max_steps: int = 30,
) -> dict[str, Router]:
    """构建运行时 DAG 的所有节点绑定（15 节点）

    param_registry: ParamRegistry 实例。所有可进化参数从此读取，
    确保元进化的修改能被 runtime 实际消费。
    """
    from omnicompany.runtime.llm.llm import LLMClient
    from omnicompany.runtime.routing.router import ContextRouter, LLMRouter
    from omnicompany.runtime.exec.tool_executor import ToolExecutor
    from omnicompany.runtime.nodes.semantic import (
        BoltzmannSelectRouter,
        ConvergenceAuditRouter,
        GuardianCheckRouter,
        IntentParseRouter,
        RouteRetrieveRouter,
        SemanticTypeClassifierRouter,
        SpecializedDispatchRouter,
        TaskIntentRouter,
        TraceAccumulateRouter,
        TruthInjectRouter,
    )

    from omnicompany.runtime.exec.tools import ALL_TOOLS
    client = LLMClient(model=model, base_url=base_url, api_key=api_key, tools=ALL_TOOLS)
    executor = ToolExecutor(timeout=timeout)

    boltzmann_beta = 2.0
    if param_registry is not None:
        boltzmann_beta = param_registry.get_or_default("boltzmann.beta", 2.0)
    elif mutation_state is not None:
        boltzmann_beta = getattr(mutation_state, "boltzmann_beta", 2.0)

    semantic_registry = None

    # Phase 2.5: 注册路由核心节点到 semantic_nodes（meta-evolvable）
    if semantic_network_db_path:
        try:
            from omnicompany.runtime.nodes.semantic import _ensure_routing_nodes_registered
            _ensure_routing_nodes_registered(semantic_network_db_path)
        except Exception:
            pass

    return {
        "context": ContextRouter(),
        "truth_inject": TruthInjectRouter(mirror=mirror, mutation_state=mutation_state, route_graph=route_graph),
        "llm": LLMRouter(client),
        "death_zone": _DeathZoneAdapter(),
        "intent_parse": IntentParseRouter(),
        "task_intent_parse": TaskIntentRouter(semantic_network_db_path=semantic_network_db_path),
        "semantic_classify": SemanticTypeClassifierRouter(registry=semantic_registry, llm_client=client, route_graph=route_graph),
        "specialized_dispatch": SpecializedDispatchRouter(registry=semantic_registry, route_graph=route_graph, llm_client=client),
        "route_retrieve": RouteRetrieveRouter(route_db_path=route_db_path),
        "boltzmann_select": BoltzmannSelectRouter(route_graph=route_graph, beta=boltzmann_beta),
        "tool_dispatch": _ToolDispatchAdapter(executor),
        "pain_classify": _PainClassifyAdapter(route_graph, param_registry=param_registry),
        "pain_propagate": _PainPropagateAdapter(route_graph, param_registry=param_registry),
        "route_accumulate": _RouteAccumulateAdapter(route_graph=route_graph),
        "trace_accumulate": TraceAccumulateRouter(
            intent_db_path=str(Path(route_db_path).parent / "intent_traces.db") if route_db_path else None,
            route_db_path=route_db_path,
            route_graph=route_graph,
            model=model,
            base_url=base_url,
            api_key=api_key,
        ),
        "reward_compute": _RewardComputeAdapter(max_steps=max_steps, param_registry=param_registry),
        "escalation_check": _EscalationCheckAdapter(param_registry=param_registry),
        "convergence_audit": ConvergenceAuditRouter(param_registry=param_registry),
        "guardian_check": GuardianCheckRouter(guardian=guardian, db_path=semantic_network_db_path),
    }


class _DeathZoneAdapter(Router):
    """适配器：将 DeathZoneCheckRouter 包装为支持 tool_calls 批量检查的 Router。"""

    INPUT_KEYS = ["tool_calls"]

    def __init__(self):
        from omnicompany.runtime.nodes.semantic import DeathZoneCheckRouter
        self._inner = DeathZoneCheckRouter()

    def run(self, input_data: Any) -> Verdict:
        from omnicompany.protocol.anchor import Verdict, VerdictKind

        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.PASS, output=input_data)

        tool_calls = input_data.get("tool_calls", [])
        for tc in tool_calls:
            check_input = {
                "tool_name": tc.get("tool_name", ""),
                "tool_args": tc.get("tool_args", {}),
            }
            result = self._inner.run(check_input)
            if result.kind == VerdictKind.FAIL:
                blocked_result = result.output or {}
                return Verdict(
                    kind=VerdictKind.FAIL,
                    output={
                        "system_prompt": input_data.get("system_prompt", ""),
                        "messages": input_data.get("messages", []),
                        "tool_results": [{
                            "tool_use_id": tc.get("tool_use_id", ""),
                            "content": blocked_result.get("result", "BLOCKED"),
                        }],
                    },
                    diagnosis=result.diagnosis,
                )

        return Verdict(kind=VerdictKind.PASS, output=input_data)


class _ToolDispatchAdapter(Router):
    """适配器：将 ToolDispatchRouter 包装为兼容 TeamRunner 接口。

    执行后将结果回填到 IntentTracer（若可用），
    完成 intent → execution → result 的完整数据闭环。
    """

    INPUT_KEYS = ["tool_calls"]

    def __init__(self, executor: Any, tracer: Any = None):
        from omnicompany.runtime.nodes.tools import ToolDispatchRouter
        self._dispatch = ToolDispatchRouter(executor)
        self.tracer = tracer

    async def run(self, input_data: Any) -> "Verdict":
        import asyncio
        from omnicompany.protocol.anchor import Verdict, VerdictKind

        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.FAIL, diagnosis="Expected dict")

        tool_calls = input_data.get("tool_calls", [])
        tool_results = []

        for tc in tool_calls:
            dispatch_input = {
                "tool_name": tc["tool_name"],
                "tool_args": tc["tool_args"],
            }
            result = await asyncio.to_thread(self._dispatch.run, dispatch_input)
            output = result.output if isinstance(result.output, dict) else {}
            content = output.get("result", str(result.output))
            tool_results.append({
                "tool_use_id": tc.get("tool_use_id", ""),
                "content": content,
            })

            step_num = tc.get("_intent_step_num")
            if self.tracer is not None and step_num is not None:
                has_error = (
                    "Error" in str(content)[:500]
                    or "error" in str(content)[:500]
                    or "FAILED" in str(content)[:500]
                )
                try:
                    self.tracer.record_tool_result(
                        step_num=step_num,
                        result_summary=str(content)[:2000],
                        exit_ok=not has_error,
                    )
                except Exception:
                    pass

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "system_prompt": input_data.get("system_prompt", ""),
                "messages": input_data.get("messages", []),
                "tool_calls": tool_calls,
                "tool_results": tool_results,
                "selected_route_node_id": input_data.get("selected_route_node_id"),
            },
        )


class _PainClassifyAdapter(Router):
    """LLM-based 节点自评 + 向上归责。

    理论依据: 实验 10 (契约可完成度, r=0.897), 实验 8 (多节点独立进化)。
    用一次 LLM 调用，让模型站在每个节点视角评估:
        tool_dispatch: 基础设施是否正常?
        llm: 我的推理/代码给定上下文是否合理? 信息是否充分?
        context: 提供的信息是否完整?
    输出归一化 blame_map + 连续标量 contract_score (实验 10 验证 r=0.897)。
    """

    INPUT_KEYS = ["system_prompt", "messages", "tool_results"]

    _ASSESS_PROMPT = """\
You are a pipeline fault analyst. An agent pipeline has 3 nodes that executed in sequence:

1. **context** — assembled the conversation history and system prompt for the LLM
2. **llm** — read the context, reasoned, and produced a tool call (code/command)
3. **tool_dispatch** — executed the tool call and got the result below

The tool execution produced an ERROR. Your job: assess each node's contract fulfillment.

For each node, answer:
- Q1 (input_type_ok): Did this node receive properly formatted input? (true/false)
- Q2 (input_sufficient): Did this node have enough information to do its job? (true/false)  
- Q3 (output_ok): Did this node produce correct output given its input? (true/false)
- blame (0.0-1.0): How much of the fault belongs to THIS node?
- category: One of [infra_failure, syntax_error, type_mismatch, logic_error, info_insufficient, context_truncated, unknown]
- detail: One sentence explaining your assessment

The blame scores across all 3 nodes MUST sum to 1.0.

## Context Given to LLM
System prompt (last 500 chars): {system_prompt_tail}

Recent messages (last 2):
{recent_messages}

## LLM's Action
Tool calls: {tool_calls_summary}

## Error from Execution
{error_content}

Respond in this EXACT JSON format (no markdown, no extra text):
{{"tool_dispatch": {{"input_type_ok": bool, "input_sufficient": bool, "output_ok": bool, "blame": float, "category": str, "detail": str}}, "llm": {{"input_type_ok": bool, "input_sufficient": bool, "output_ok": bool, "blame": float, "category": str, "detail": str}}, "context": {{"input_type_ok": bool, "input_sufficient": bool, "output_ok": bool, "blame": float, "category": str, "detail": str}}, "overall_pain_intensity": float, "primary_fault": str}}"""

    _INFRA_MARKERS = (
        "Permission denied", "command not found", "timed out",
        "No such file or directory", "not recognized",
        "Connection refused", "disk quota", "out of memory",
        "Access is denied",
    )

    def __init__(self, route_graph: Any = None, param_registry: Any = None):
        self.route_graph = route_graph
        self.param_registry = param_registry
        self._llm_client = None

    def _get_client(self):
        if self._llm_client is None:
            from omnicompany.runtime.llm.llm import LLMClient
            self._llm_client = LLMClient.for_role("pain_classify", max_tokens=800, tools=[])
        return self._llm_client

    @staticmethod
    def _extract_error(tool_results: list[dict]) -> tuple[bool, str]:
        for tr in tool_results:
            content = str(tr.get("content", ""))[:2000]
            if "Error" in content or "error" in content or "FAILED" in content:
                return True, content
        return False, ""

    @staticmethod
    def _summarize_tool_calls(tool_calls: list[dict]) -> str:
        parts = []
        for tc in tool_calls[:3]:
            name = tc.get("tool_name", "?")
            args = tc.get("tool_args", {})
            if name == "bash" and isinstance(args, dict):
                cmd = args.get("command", "")[:200]
                parts.append(f"bash: {cmd}")
            elif name == "editor" and isinstance(args, dict):
                path = args.get("path", "?")
                op = args.get("command", "?")
                parts.append(f"editor({op}): {path}")
            else:
                parts.append(f"{name}({str(args)[:150]})")
        return "\n".join(parts) or "(no tool calls)"

    @staticmethod
    def _recent_messages(messages: list) -> str:
        tail = messages[-2:] if len(messages) >= 2 else messages
        parts = []
        for m in tail:
            role = m.get("role", "?") if isinstance(m, dict) else "?"
            content = ""
            if isinstance(m, dict):
                c = m.get("content", "")
                if isinstance(c, str):
                    content = c[-300:]
                elif isinstance(c, list):
                    texts = [b.get("text", "") for b in c if isinstance(b, dict) and "text" in b]
                    content = " ".join(texts)[-300:]
            parts.append(f"[{role}]: ...{content}")
        return "\n".join(parts) or "(empty)"

    def _llm_assess(self, system_prompt: str, messages: list,
                    tool_calls: list, error_content: str) -> dict:
        """一次 LLM 调用完成全部节点自评。"""
        import json as _json

        prompt = self._ASSESS_PROMPT.format(
            system_prompt_tail=system_prompt[-500:],
            recent_messages=self._recent_messages(messages),
            tool_calls_summary=self._summarize_tool_calls(tool_calls),
            error_content=error_content[:1500],
        )

        try:
            client = self._get_client()
            response = client.call(
                messages=[{"role": "user", "content": prompt}],
                system="You are a precise fault analyst. Output ONLY valid JSON.",
            )
            text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    text = block.text.strip()
                    break

            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            return _json.loads(text)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("LLM assess failed (%s), using infra fallback", e)
            return None

    def _fallback_assess(self, error_content: str) -> dict:
        """LLM 调用失败时的最小化 fallback。"""
        is_infra = any(m in error_content for m in self._INFRA_MARKERS)
        if is_infra:
            return {
                "tool_dispatch": {"blame": 0.7, "category": "infra_failure",
                                  "input_type_ok": True, "input_sufficient": True,
                                  "output_ok": False, "detail": "infrastructure error"},
                "llm": {"blame": 0.2, "category": "unknown",
                        "input_type_ok": True, "input_sufficient": True,
                        "output_ok": True, "detail": ""},
                "context": {"blame": 0.1, "category": "unknown",
                            "input_type_ok": True, "input_sufficient": True,
                            "output_ok": True, "detail": ""},
                "overall_pain_intensity": 0.3,
                "primary_fault": "tool_dispatch",
            }
        return {
            "tool_dispatch": {"blame": 0.0, "category": "execution_ok",
                              "input_type_ok": True, "input_sufficient": True,
                              "output_ok": True, "detail": ""},
            "llm": {"blame": 0.7, "category": "unknown",
                    "input_type_ok": True, "input_sufficient": True,
                    "output_ok": False, "detail": "error in LLM output"},
            "context": {"blame": 0.3, "category": "unknown",
                        "input_type_ok": True, "input_sufficient": True,
                        "output_ok": True, "detail": ""},
            "overall_pain_intensity": 0.5,
            "primary_fault": "llm",
        }

    def run(self, input_data: Any) -> "Verdict":
        from omnicompany.protocol.anchor import Verdict, VerdictKind

        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.PASS, output=input_data)

        tool_results = input_data.get("tool_results", [])
        tool_calls = input_data.get("tool_calls", [])
        messages = input_data.get("messages", [])
        system_prompt = input_data.get("system_prompt", "")

        has_error, error_content = self._extract_error(tool_results)

        if not has_error:
            return Verdict(
                kind=VerdictKind.PASS,
                output={
                    **input_data,
                    "pain_event": None,
                    "has_pain": False,
                    "pain_intensity": 0.0,
                    "blame_map": {},
                    "contract_verdicts": {},
                    "error_category": "none",
                    "error_detail": "",
                    "trace_step": {"exit_code": 0, "tool_results": tool_results},
                },
            )

        # ── LLM-based 反向自评 ──
        assessment = self._llm_assess(system_prompt, messages, tool_calls, error_content)
        if assessment is None:
            assessment = self._fallback_assess(error_content)

        blame_map: dict[str, float] = {}
        contract_verdicts: dict[str, dict] = {}
        for node_id in ("tool_dispatch", "llm", "context"):
            node_data = assessment.get(node_id, {})
            blame_map[node_id] = float(node_data.get("blame", 0.0))
            contract_verdicts[node_id] = {
                "node": node_id,
                "input_type_ok": node_data.get("input_type_ok", True),
                "input_sufficient": node_data.get("input_sufficient", True),
                "output_ok": node_data.get("output_ok", True),
                "blame_self": float(node_data.get("blame", 0.0)),
                "category": node_data.get("category", "unknown"),
                "detail": str(node_data.get("detail", ""))[:300],
            }

        total = sum(blame_map.values()) or 1.0
        blame_map = {k: round(v / total, 3) for k, v in blame_map.items()}

        primary_fault = str(assessment.get("primary_fault", ""))
        if primary_fault not in blame_map:
            primary_fault = max(blame_map, key=lambda k: blame_map[k])

        pain_intensity = float(assessment.get("overall_pain_intensity", 0.5))
        pain_intensity = max(0.1, min(1.0, pain_intensity))

        category = contract_verdicts.get(primary_fault, {}).get("category", "unknown")
        pain_tier = 1 if "infra" in category else (3 if "info_insufficient" in category else 2)

        # A1 fix: bind pain to the route_graph IntentNode selected by Boltzmann,
        # not to a generic DAG node name like "tool_dispatch"/"llm"/"context".
        route_node_id = input_data.get("selected_route_node_id", "")
        pain_node_id = route_node_id if route_node_id else primary_fault

        from omnicompany.runtime.signals.pain_system import PainEvent
        pain_event = PainEvent(
            source_trace_id="",
            source_step_num=0,
            node_id=pain_node_id,
            pain_intensity=pain_intensity,
            irrecoverability=0.1 if pain_tier == 1 else 0.3,
            pain_tier=pain_tier,
            propagate_depth=2,
            source_node_id=pain_node_id,
        )

        return Verdict(
            kind=VerdictKind.FAIL,
            output={
                **input_data,
                "pain_event": pain_event,
                "has_pain": True,
                "pain_intensity": pain_intensity,
                "blame_map": blame_map,
                "primary_fault": primary_fault,
                "pain_node_id": pain_node_id,
                "contract_verdicts": contract_verdicts,
                "error_category": category,
                "error_detail": error_content[:500],
                "trace_step": {"exit_code": 1, "tool_results": tool_results},
            },
        )


class _PainPropagateAdapter(Router):
    """因果反向传播 — 沿 route_graph 传导痛觉。

    A2 fix: 调用 PainPropagator.propagate() 将痛觉沿 route_graph
    的因果链反向传播，而非仅做内存 blame 累积。
    同时保留 blame_map 累积用于 escalation 判定。
    """

    INPUT_KEYS = ["system_prompt", "messages", "tool_results"]

    def __init__(self, route_graph: Any = None, param_registry: Any = None):
        self.route_graph = route_graph
        self.param_registry = param_registry
        self._accumulated_blame: dict[str, float] = {}
        self._pain_steps: int = 0
        self._total_steps: int = 0
        self._propagated_nodes: list[str] = []

    def run(self, input_data: Any) -> "Verdict":
        from omnicompany.protocol.anchor import Verdict, VerdictKind

        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.PASS, output=input_data)

        self._total_steps += 1
        has_pain = input_data.get("has_pain", False)
        blame_map = input_data.get("blame_map", {})
        pain_intensity = input_data.get("pain_intensity", 0.0)
        pain_event = input_data.get("pain_event")
        pain_node_id = input_data.get("pain_node_id", "")

        propagated = []
        if has_pain and pain_event is not None and self.route_graph is not None:
            self._pain_steps += 1
            try:
                from omnicompany.runtime.signals.pain_system import PainPropagator
                propagator = PainPropagator(self.route_graph, param_registry=self.param_registry)
                trace_steps = self._build_trace_steps(input_data)
                propagated = propagator.propagate(pain_event, trace_steps)
                self._propagated_nodes.extend(propagated)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    "PainPropagator.propagate() failed: %s", e
                )

        if has_pain and blame_map:
            if not self._pain_steps:
                self._pain_steps += 1
            for node_id, blame_score in blame_map.items():
                weighted = blame_score * pain_intensity
                self._accumulated_blame[node_id] = (
                    self._accumulated_blame.get(node_id, 0.0) + weighted
                )
            if pain_node_id:
                self._accumulated_blame[pain_node_id] = (
                    self._accumulated_blame.get(pain_node_id, 0.0) + pain_intensity
                )

        avg_pain = (
            sum(self._accumulated_blame.values()) / max(self._pain_steps, 1)
            if self._accumulated_blame else 0.0
        )

        pain_rate = self._pain_steps / max(self._total_steps, 1)

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                **input_data,
                "accumulated_pain": dict(self._accumulated_blame),
                "avg_accumulated_pain": avg_pain,
                "pain_rate": round(pain_rate, 3),
                "pain_steps": self._pain_steps,
                "total_steps": self._total_steps,
                "propagated_nodes": propagated,
            },
        )

    @staticmethod
    def _build_trace_steps(input_data: dict) -> list[dict]:
        """从 input_data 中提取最近的 trace_steps 用于反向传播。"""
        steps = []
        messages = input_data.get("messages", [])
        for i, msg in enumerate(messages[-10:]):
            if isinstance(msg, dict):
                route_nid = msg.get("_route_node_id", "")
                if route_nid:
                    steps.append({
                        "step_num": i,
                        "route_node_id": route_nid,
                    })
        selected = input_data.get("selected_route_node_id", "")
        if selected:
            steps.append({
                "step_num": len(steps),
                "route_node_id": selected,
            })
        return steps

    def reset(self):
        self._accumulated_blame = {}
        self._pain_steps = 0
        self._total_steps = 0
        self._propagated_nodes = []


class _RouteAccumulateAdapter(Router):
    """实时路由积累 — 每步执行后将意图+结果+embedding 写入 route_graph。

    async 实现：在 event loop 线程执行，无 SQLite 线程问题。
    每个新节点都计算 embedding（调用 BGE server），使 RouteRetriever 能检索到。
    """

    INPUT_KEYS = ["system_prompt", "messages", "tool_results"]

    def __init__(self, route_graph: Any = None, tracer: Any = None):
        self._route_graph = route_graph
        self.tracer = tracer
        self._prev_output_types: list[str] = ["user_request"]
        self._step_count = 0
        self._emb_client = None

    def _get_emb_client(self):
        if self._emb_client is None:
            from omnicompany.runtime.llm.embedding_client import TextEmbeddingClient
            self._emb_client = TextEmbeddingClient()
        return self._emb_client

    async def run(self, input_data: Any) -> "Verdict":
        from omnicompany.protocol.anchor import Verdict, VerdictKind

        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.PASS, output=input_data)

        self._step_count += 1

        if self._route_graph is None:
            return Verdict(kind=VerdictKind.PASS, output={
                **input_data, "route_accumulated": False,
            })

        tool_calls = input_data.get("tool_calls", [])
        has_pain = input_data.get("has_pain", False)
        error_detail = input_data.get("error_detail", "") or ""

        for tc in tool_calls:
            intent_step = tc.get("_intent_step_num")
            tool_name = tc.get("tool_name", "")

            if self.tracer is not None and intent_step is not None:
                await self._accumulate_from_tracer(
                    intent_step, tool_name, has_pain,
                    error_detail=error_detail if has_pain else "",
                )
            else:
                await self._accumulate_basic(tool_name, has_pain)

        return Verdict(kind=VerdictKind.PASS, output={
            **input_data,
            "route_accumulated": True,
            "route_step_count": self._step_count,
        })

    async def _accumulate_from_tracer(
        self, step_num: int, tool_name: str, has_pain: bool, error_detail: str = ""
    ) -> bool:
        """从 IntentTracer 读取 intent，计算 embedding，写入 route_graph。"""
        import json as _json
        import hashlib
        import logging
        from datetime import datetime, timezone

        try:
            conn = open_db_rw(str(self.tracer.db_path))
            row = conn.execute(
                "SELECT input_types, output_types, action_class, desc, rationale "
                "FROM intent_steps WHERE trace_id=? AND step_num=?",
                (self.tracer.trace_id, step_num),
            ).fetchone()
            conn.close()

            if row is None:
                return False

            input_types = _json.loads(row[0]) if row[0] else []
            output_types = _json.loads(row[1]) if row[1] else []
            action_class = row[2] or ""
            desc = row[3] or ""
            rationale = row[4] or ""

            # 计算 embedding — 与 RouteClassifier 相同的文本格式
            emb_text = (
                f"action:{action_class} tool:{tool_name} "
                f"in:{','.join(input_types)} out:{','.join(output_types)} "
                f"desc:{desc}"
                + (f" rationale:{rationale[:300]}" if rationale else "")
            )
            try:
                embedding = await self._get_emb_client().get_embedding(emb_text)
            except Exception:
                embedding = []

            # C1: node identity includes tool_name explicitly so that
            # bash:execute:file.output and editor:execute:file.output become
            # distinct route nodes, enabling true multi-path routing.
            id_source = f"{tool_name}:{action_class}:{','.join(sorted(output_types))}"
            node_id = hashlib.sha256(id_source.encode()).hexdigest()[:16]
            now = datetime.now(timezone.utc).isoformat()

            from omnicompany.runtime.route_graph import IntentNode
            node = IntentNode(
                node_id=node_id,
                input_types=input_types,
                output_types=output_types,
                action_class=action_class,
                canonical_desc=desc[:200] if desc else f"{tool_name}:{action_class}",
                hit_count=1,
                tool_name=tool_name,
                embedding=embedding,
                created_at=now,
                last_seen=now,
            )

            self._route_graph.upsert_node(node)
            self._route_graph.upsert_edge(self._prev_output_types, node_id)
            self._route_graph.record_outcome(node_id, success=not has_pain)

            # 无论成功失败都更新 semantic_node stats（失败时累积 pain，不增 success_count）
            self._update_semantic_node_stats(
                node_id, input_types, output_types, action_class, desc,
                success=not has_pain, error_detail=error_detail, tool_name=tool_name,
            )

            if has_pain:
                old_node = self._route_graph.get_node(node_id)
                if old_node:
                    new_pain = old_node.pain_score * 0.8 + 0.3 * 0.2
                    self._route_graph.update_pain(node_id, new_pain, increment_count=True)
            else:
                self._route_graph.heal_pain(node_id)

            # 回填 route_node_id
            if self.tracer is not None:
                self.tracer.record_route_decision(
                    step_num=step_num,
                    route_node_id=node_id,
                    route_decision="LIVE",
                )

            if output_types:
                self._prev_output_types = output_types

            return True
        except Exception as e:
            logging.getLogger(__name__).warning("Route accumulate failed: %s", e)
            return False

    async def _accumulate_basic(self, tool_name: str, has_pain: bool) -> None:
        """无 tracer 时的最简路由积累。"""
        import hashlib
        from datetime import datetime, timezone

        try:
            from omnicompany.runtime.route_graph import IntentNode

            node_id = hashlib.sha256(tool_name.encode()).hexdigest()[:16]
            now = datetime.now(timezone.utc).isoformat()

            emb_text = f"action:execute tool:{tool_name}"
            try:
                embedding = await self._get_emb_client().get_embedding(emb_text)
            except Exception:
                embedding = []

            node = IntentNode(
                node_id=node_id,
                input_types=self._prev_output_types,
                output_types=[f"{tool_name}.output"],
                action_class="execute",
                canonical_desc=tool_name,
                hit_count=1,
                tool_name=tool_name,
                embedding=embedding,
                created_at=now,
                last_seen=now,
            )

            self._route_graph.upsert_node(node)
            self._route_graph.upsert_edge(self._prev_output_types, node_id)
            self._route_graph.record_outcome(node_id, success=not has_pain)
        except Exception:
            pass

    def _update_semantic_node_stats(
        self,
        route_node_id: str,
        input_types: list[str],
        output_types: list[str],
        action_class: str,
        desc: str,
        success: bool = True,
        error_detail: str = "",
        tool_name: str = "",
    ) -> None:
        """更新 semantic_nodes 的统计与痛觉。

        success=True:  hit_count+1, success_count+1, pain_score 衰减（愈合）
        success=False: hit_count+1, success_count 不变, pain_score 积累

        成熟度阈值使用绝对成功次数（success_count>=5），避免迁移节点的初始
        hit_count 拉低 SR 导致永远无法升阶。
        """
        import logging
        import time
        from pathlib import Path

        _log = logging.getLogger(__name__)

        try:
            if self._route_graph is None:
                return
            route_db_path = getattr(self._route_graph, '_db_path', None) or \
                            getattr(self._route_graph, 'db_path', None)
            if route_db_path is None:
                return

            semantic_db_path = Path(route_db_path).parent / "semantic_network.db"
            if not semantic_db_path.exists():
                return

            conn = open_db_rw(str(semantic_db_path))

            # 通过 output_types 模糊匹配 semantic_node
            matched_node = None
            for out_type in output_types:
                row = conn.execute(
                    """SELECT node_id, hit_count, success_count, maturity, pain_score
                       FROM semantic_nodes
                       WHERE active=1 AND output_types LIKE ?
                       ORDER BY hit_count DESC LIMIT 1""",
                    (f'%{out_type}%',)
                ).fetchone()
                if row:
                    matched_node = row
                    break

            if matched_node:
                node_id   = matched_node["node_id"]
                old_hit   = matched_node["hit_count"]
                old_succ  = matched_node["success_count"]
                old_mat   = matched_node["maturity"]
                old_pain  = float(matched_node["pain_score"] or 0.0)

                new_hit  = old_hit + 1
                new_succ = old_succ + (1 if success else 0)
                last_used = int(time.time())

                # pain_score: 成功愈合，失败积累（EMA 式衰减）
                if success:
                    new_pain = old_pain * 0.70          # 衰减 30%
                else:
                    new_pain = min(1.0, old_pain * 0.85 + 0.15)  # 积累

                # 成熟度跃迁：使用绝对成功次数，不受初始 hit_count 污染
                new_mat = old_mat
                if new_pain < 0.90:
                    if old_mat == "hypothetical" and new_succ >= 1:
                        new_mat = "growing"
                    elif old_mat == "growing" and new_succ >= 5:
                        new_mat = "mature"
                    elif old_mat == "mature" and new_succ >= 15 and new_succ / new_hit >= 0.70:
                        new_mat = "crystallized"

                # 痛觉驱动降阶：pain 过高 → 退回 hypothetical
                if new_pain >= 0.90 and old_mat in ("growing", "mature"):
                    new_mat = "hypothetical"
                    _log.warning(
                        "Semantic node demoted %s: pain=%.2f → hypothetical", node_id[:16], new_pain
                    )

                # 失败时将 error_detail 追加到 failure_exemplars（保留最近 10 条）
                if not success and error_detail.strip():
                    import json as _json
                    try:
                        existing_raw = conn.execute(
                            "SELECT failure_exemplars FROM semantic_nodes WHERE node_id=?", (node_id,)
                        ).fetchone()
                        existing = _json.loads(existing_raw[0]) if existing_raw and existing_raw[0] else []
                        if not isinstance(existing, list):
                            existing = []
                        existing.append(error_detail[:200])
                        existing = existing[-10:]  # 保留最近 10 条
                        conn.execute(
                            "UPDATE semantic_nodes SET failure_exemplars=? WHERE node_id=?",
                            (_json.dumps(existing, ensure_ascii=False), node_id)
                        )
                    except Exception:
                        pass

                conn.execute(
                    """UPDATE semantic_nodes
                       SET hit_count=?, success_count=?, maturity=?, pain_score=?, last_used=?
                       WHERE node_id=?""",
                    (new_hit, new_succ, new_mat, new_pain, last_used, node_id)
                )
                conn.commit()
                _log.debug(
                    "semantic_node %s: hit=%d succ=%d pain=%.2f mat=%s [%s]",
                    node_id[:16], new_hit, new_succ, new_pain, new_mat,
                    "OK" if success else "FAIL",
                )
                if new_mat != old_mat:
                    _log.info(
                        "Maturity transition %s: %s → %s (succ=%d pain=%.2f)",
                        node_id[:16], old_mat, new_mat, new_succ, new_pain,
                    )
            else:
                # 没有匹配节点，仅在成功时创建新节点（失败不创建）
                if success:
                    self._create_semantic_node_for_route(
                        semantic_db_path, route_node_id, input_types, output_types,
                        action_class, desc, tool_name=tool_name,
                    )

            conn.close()
        except Exception as e:
            logging.getLogger(__name__).debug("Failed to update semantic_node stats: %s", e)
    
    # ── Tag 声明工具 schema ──────────────────────────────────────
    _TAG_TOOL = {
        "name": "declare_node_tags",
        "description": "Declare semantic tags and enriched description for a routing node's output",
        "input_schema": {
            "type": "object",
            "properties": {
                "semantic_description": {
                    "type": "string",
                    "description": (
                        "A rich, search-optimized description of what this node produces. "
                        "1-3 sentences covering: what the output IS, what it contains or represents, "
                        "and what a downstream consumer can do with it. "
                        "Write as if answering 'what does this step produce and why is it useful?' "
                        "This text is the primary index for semantic retrieval — be concrete and specific."
                    ),
                },
                "domain": {
                    "type": ["string", "null"],
                    "description": (
                        "The name of the private system or project this output belongs to. "
                        "Fill this ONLY when the output content is meaningful exclusively within "
                        "a specific system — i.e., someone outside that system could not interpret "
                        "or use the output without knowing that system's internals. "
                        "Use the system's actual name as it would appear in code or documentation. "
                        "Do NOT fill sub-components or sub-modules here (those go in 'module'). "
                        "null when the output is universally interpretable regardless of which project produced it."
                    ),
                },
                "artifact": {
                    "type": "string",
                    "description": (
                        "A short noun phrase (1-3 words) naming the semantic type of this output. "
                        "Describe WHAT the content is, not how it was transported or formatted. "
                        "Use lowercase with underscores. Choose the most specific accurate term: "
                        "source_file, directory_listing, grep_result, test_output, config_file, "
                        "database_record, execution_log, structured_plan, api_response, error_report."
                    ),
                },
                "module": {
                    "type": ["string", "null"],
                    "description": (
                        "The specific named component, subsystem, or file this output concerns. "
                        "Fill this ONLY when a downstream consumer must know WHICH specific one — "
                        "not just that it is a [artifact], but which particular instance. "
                        "If two different outputs of the same artifact type would require different "
                        "downstream handling, that is a signal to fill module. "
                        "null when any instance of this artifact type is interchangeable downstream."
                    ),
                },
                "is_common_knowledge": {
                    "type": "boolean",
                    "description": (
                        "True if a competent general-purpose LLM can correctly interpret and use "
                        "this output knowing NOTHING about the specific project it came from. "
                        "The test: if you gave this output to an LLM with no project context, "
                        "could it handle it correctly? "
                        "True: a sorted list, a file size in bytes, standard error codes, plain text. "
                        "False: an output whose meaning depends on knowing project-specific conventions, "
                        "schemas, terminology, or internal structure."
                    ),
                },
            },
            "required": ["semantic_description", "domain", "artifact", "module", "is_common_knowledge"],
        },
    }

    _TAG_PROMPT = """\
You are declaring the semantic type and description for what a workflow step PRODUCES.

Step description: {desc}
Tool used: {tool_name}
Action class: {action_class}
Input types: {input_types}
Output types (raw): {output_types}

Your job:
1. semantic_description — write a rich, retrieval-optimized description of the output.
   Focus on content and meaning, not transport format. What IS it? What does it contain?
   What can a downstream step do with it?
2. domain — is this output's meaning tied to a specific private system?
   Think about the CONTENT, not the format. Even a generic output format (like a text file
   or shell output) can contain private-system-specific information.
   Fill the system name only if the content is uninterpretable without knowing that system.
3. artifact — semantic type name for the output content (not the transport format).
4. module — only if downstream handling depends on knowing WHICH specific component this is.
5. is_common_knowledge — would an LLM with no project context handle this correctly?

Use the declare_node_tags tool.
"""

    def _declare_tags_for_node(
        self,
        tool_name: str,
        action_class: str,
        desc: str,
        input_types: list[str],
        output_types: list[str],
    ) -> dict:
        """调用 LLM 为新节点声明 tag-based 类型。失败时返回最小化默认值。"""
        import logging
        _log = logging.getLogger(__name__)
        try:
            from omnicompany.runtime.llm.llm import LLMClient
            llm = LLMClient.for_role("pain_classify", max_tokens=500, tools=[self._TAG_TOOL])
            prompt = self._TAG_PROMPT.format(
                desc=desc[:200],
                tool_name=tool_name or "(none)",
                action_class=action_class or "(none)",
                input_types=", ".join(input_types[:5]) or "(none)",
                output_types=", ".join(output_types[:5]) or "(none)",
            )
            resp = llm.call(
                messages=[{"role": "user", "content": prompt}],
                tool_choice={"type": "tool", "name": "declare_node_tags"},
            )
            for block in getattr(resp, "content", []):
                if getattr(block, "type", None) == "tool_use" and block.name == "declare_node_tags":
                    result = dict(block.input)
                    # 清理 LLM 偶发的字符串 "null" → None
                    for k in ("domain", "module"):
                        if result.get(k) in ("null", "none", "None", "NULL", ""):
                            result[k] = None
                    return result
        except Exception as e:
            _log.debug("_declare_tags_for_node failed: %s", e)
        # 降级：semantic_description 留空（调用方用原始 desc 填充）
        raw = output_types[0] if output_types else "unknown"
        return {"semantic_description": None, "domain": None, "artifact": raw, "module": None, "is_common_knowledge": False}

    def _create_semantic_node_for_route(
        self,
        semantic_db_path: Path,
        route_node_id: str,
        input_types: list[str],
        output_types: list[str],
        action_class: str,
        desc: str,
        tool_name: str = "",
    ) -> None:
        """为新的路由模式创建 semantic_node，附带 LLM tag 声明。"""
        import json
        import hashlib
        from datetime import datetime, timezone

        try:
            conn = open_db_rw(str(semantic_db_path))

            # node_id 包含 tool_name 以区分同类型不同工具的节点
            id_src = f"{tool_name}:{action_class}:{','.join(sorted(output_types))}"
            node_id = hashlib.sha256(id_src.encode()).hexdigest()[:16]
            now = datetime.now(timezone.utc).isoformat()

            existing = conn.execute(
                "SELECT node_id FROM semantic_nodes WHERE node_id = ?", (node_id,)
            ).fetchone()

            if not existing:
                # LLM 声明 tags + 丰富描述（一次调用同步完成）
                tags = self._declare_tags_for_node(
                    tool_name=tool_name,
                    action_class=action_class,
                    desc=desc,
                    input_types=input_types,
                    output_types=output_types,
                )
                # 用 LLM 输出的丰富描述覆盖稀疏描述；降级时保留原始描述
                rich_desc = tags.pop("semantic_description", None) or desc
                tags_json = json.dumps(tags, ensure_ascii=False)

                conn.execute(
                    """INSERT OR IGNORE INTO semantic_nodes (
                        node_id, description, impl_kind, tool_name, processing_prompt,
                        input_types, output_types, tags, maturity, hit_count, success_count,
                        pain_score, energy, last_used, node_guidance,
                        source_channel, round_created, parent_node_ids,
                        success_exemplars, failure_exemplars, failure_modes,
                        embedding, created_at, active
                    ) VALUES (
                        ?, ?, 'soft', ?, ?,
                        ?, ?, ?, 'growing', 1, 1,
                        0.0, 1.0, ?, '',
                        'route_accumulate', 0, '[]',
                        '[]', '[]', '[]',
                        '[]', ?, 1
                    )""",
                    (
                        node_id, rich_desc[:500], tool_name or "", rich_desc[:500],
                        json.dumps(input_types, ensure_ascii=False),
                        json.dumps(output_types, ensure_ascii=False),
                        tags_json,
                        now, now,
                    ),
                )
                conn.commit()
                logging.getLogger(__name__).info(
                    "Created semantic_node %s tool=%s tags=%s",
                    node_id[:16], tool_name or "(none)",
                    json.dumps(tags, ensure_ascii=False),
                )

            conn.close()
        except Exception as e:
            logging.getLogger(__name__).debug("Failed to create semantic_node: %s", e)


class _RewardComputeAdapter(Router):
    """适配器：六维奖励计算。

    从 pipeline 数据流中提取 token/步骤/痛觉/路由等指标，
    构造 RewardSignal 并输出 composite 得分。
    """

    INPUT_KEYS = ["system_prompt", "messages", "tool_results"]

    def __init__(self, max_steps: int = 30, max_time: float = 300.0, param_registry: Any = None):
        self._max_steps = max_steps
        self._max_time = max_time
        self._start_time: float | None = None
        self._pain_before = 0.0
        self.param_registry = param_registry

    def run(self, input_data: Any) -> "Verdict":
        from omnicompany.protocol.anchor import Verdict, VerdictKind
        from omnicompany.runtime.signals.reward import RewardSignal
        import time

        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.PASS, output=input_data)

        if self._start_time is None:
            self._start_time = time.monotonic()

        tool_calls = input_data.get("tool_calls", [])
        messages = input_data.get("messages", [])
        total_steps = input_data.get("total_steps", len(tool_calls)) or 1
        pain_steps = input_data.get("pain_steps", 0)
        route_step_count = input_data.get("route_step_count", 0)
        avg_pain = input_data.get("avg_accumulated_pain", 0.0)

        token_estimate = sum(len(str(m.get("content", ""))) // 4 for m in messages)
        budget_tokens = self._max_steps * 2000

        elapsed = time.monotonic() - self._start_time

        # Compute workspace_cleanliness score
        workspace_cleanliness = self._compute_workspace_cleanliness()

        signal = RewardSignal.from_trace(
            actual_tokens=token_estimate,
            budget_tokens=budget_tokens,
            actual_time=elapsed,
            budget_time=self._max_time,
            new_route_nodes=route_step_count,
            total_steps=max(total_steps, 1),
            failed_steps=pain_steps,
            mirror_fresh=True,
            pain_before=self._pain_before,
            pain_after=avg_pain,
            workspace_cleanliness=workspace_cleanliness,
            param_registry=self.param_registry,
        )
        self._pain_before = avg_pain

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                **input_data,
                "reward_composite": signal.composite,
                "reward_dimensions": signal.dimensions,
                "reward_computed": True,
            },
        )

    def _compute_workspace_cleanliness(self) -> float:
        """Compute workspace cleanliness score.
        
        cleanliness = organized_files / max(total_files, 1)
        where organized_files = files in artifacts/ and knowledge/
        and total_files = all files in data/autonomous/ excluding .db, .db-shm, .db-wal, .gitignore, .git
        """
        import os
        
        base_path = "data/autonomous"
        excluded_extensions = {".db", ".db-shm", ".db-wal"}
        excluded_names = {".gitignore", ".git"}
        
        organized_count = 0
        total_count = 0
        
        # Count organized files in artifacts/ and knowledge/
        for organized_dir in ["artifacts", "knowledge"]:
            dir_path = os.path.join(base_path, organized_dir)
            if os.path.isdir(dir_path):
                for root, dirs, files in os.walk(dir_path):
                    # Skip .git directories
                    dirs[:] = [d for d in dirs if d != ".git"]
                    for f in files:
                        organized_count += 1
        
        # Count total files in data/autonomous/ excluding specified files
        if os.path.isdir(base_path):
            for root, dirs, files in os.walk(base_path):
                # Skip .git directories
                dirs[:] = [d for d in dirs if d != ".git"]
                for f in files:
                    file_path = os.path.join(root, f)
                    # Check if file should be excluded
                    _, ext = os.path.splitext(f)
                    if ext in excluded_extensions or f in excluded_names:
                        continue
                    total_count += 1
        
        return organized_count / max(total_count, 1)


class _EscalationCheckAdapter(Router):
    """溢出判定 — 基于分布式 blame_map 累积水位。

    升级条件（同时满足所有条件才升级）:
    1. 已有足够多步骤（MIN_STEPS）——防止单个探索任务早期失败误触发
    2. 单节点累积 blame 持续超过阈值（该节点在多数步骤中都出问题）
    3. 痛觉发生率持续过高（sustained pain，而非单次尖峰）

    设计原则：
    - 面向整体任务/进化后重试，不应在正常节点运作中频繁触发
    - 探索性任务前期失败是正常的，不应上报进化层
    - 触发信号代表"系统性、持续性"问题，而非"偶发失败"
    """

    INPUT_KEYS = ["system_prompt", "messages", "tool_results"]
    NODE_BLAME_THRESHOLD = 0.6    # 提高：单节点 blame 需 > 0.6（原 0.4 太宽松）
    PAIN_RATE_THRESHOLD = 0.7     # 提高：痛觉率需 > 0.7（原 0.5 太宽松）
    MIN_STEPS = 8                 # 新增：至少 8 步后才允许升级判定
    MIN_PAIN_STEPS = 3            # 新增：累积 blame 需来自至少 3 个不同节点或 3 次痛觉事件

    def __init__(self, param_registry: Any = None):
        self.param_registry = param_registry

    def run(self, input_data: Any) -> "Verdict":
        from omnicompany.protocol.anchor import Verdict, VerdictKind

        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.PASS, output=input_data)

        node_blame_threshold = self.param_registry.get_or_default(
            "escalation.node_blame_threshold", self.NODE_BLAME_THRESHOLD
        ) if self.param_registry is not None else self.NODE_BLAME_THRESHOLD
        pain_rate_threshold = self.param_registry.get_or_default(
            "escalation.pain_rate_threshold", self.PAIN_RATE_THRESHOLD
        ) if self.param_registry is not None else self.PAIN_RATE_THRESHOLD
        min_steps = self.param_registry.get_or_default(
            "escalation.min_steps", self.MIN_STEPS
        ) if self.param_registry is not None else self.MIN_STEPS

        accumulated = input_data.get("accumulated_pain", {})
        pain_rate = input_data.get("pain_rate", 0.0)
        has_pain = input_data.get("has_pain", False)
        # messages 列表长度是对话轮次的代理：每个工具调用轮次至少产生 2 条消息
        # 用 // 2 粗估实际执行步数（比 total_steps 字段更可靠，该字段从未被写入）
        messages = input_data.get("messages", [])
        inferred_steps = len(messages) // 2 if isinstance(messages, list) else 0

        # 步数不足时不升级 — 探索任务早期失败是正常的
        if inferred_steps < min_steps:
            return Verdict(
                kind=VerdictKind.PASS,
                output={
                    **input_data,
                    "escalate": False,
                    "escalation_reason": f"too_few_steps(inferred={inferred_steps}<{min_steps})",
                    "pain_by_node": accumulated,
                    "worst_node": "none",
                    "max_node_blame": 0.0,
                },
            )

        max_node_blame = max(accumulated.values()) if accumulated else 0.0
        worst_node = max(accumulated, key=accumulated.get) if accumulated else "none"
        # 累积痛觉需来自多个节点（系统性问题），而非单一节点孤立失败
        pain_node_count = sum(1 for v in accumulated.values() if v > 0.1)

        should_escalate = (
            (max_node_blame > node_blame_threshold and pain_node_count >= self.MIN_PAIN_STEPS)
            or (pain_rate > pain_rate_threshold and has_pain and pain_node_count >= self.MIN_PAIN_STEPS)
        )

        return Verdict(
            kind=VerdictKind.FAIL if should_escalate else VerdictKind.PASS,
            output={
                **input_data,
                "escalate": should_escalate,
                "escalation_target": worst_node if should_escalate else "",
                "escalation_pain": round(max_node_blame, 3) if should_escalate else 0.0,
                "escalation_reason": (
                    f"node:{worst_node}={max_node_blame:.2f},pain_nodes={pain_node_count}"
                    if max_node_blame > node_blame_threshold
                    else f"pain_rate={pain_rate:.2f},pain_nodes={pain_node_count}"
                ) if should_escalate else "none",
                "pain_by_node": accumulated,
                "worst_node": worst_node,
                "max_node_blame": round(max_node_blame, 3),
            },
        )
