# [OMNI] origin=claude-code domain=protocol/team ts=2026-04-21T00:00:00Z type=config
# [OMNI] material_id="material:protocol.team.specification_engine.py"
"""
Team Protocol — Worker 组合 + 类型检查器 (2026-04-21 从 pipeline.py 正名迁移)

Team 是 Anchor 和 Transformer 的有向图组合。
它回答一个问题: "这些节点连起来，类型能对得上吗？"

Team 的类型检查就是 LAP 的"编译器":
    - 直连: source.format_out == target.format_in → OK
    - 子类型: source.format_out <: target.format_in → 自动协变
    - 需要 Transformer: 存在 T(A→B) → 自动插入
    - 类型冲突: 无法转换 → 编译错误

命名迁移 (2026-04-21):
    PipelineSpec       → TeamSpec
    PipelineNode       → TeamNode
    PipelineEdge       → TeamEdge
    PipelineChecker    → TeamChecker
    PipelineCheckResult → TeamCheckResult
    PipelineExecutionMode → TeamExecutionMode

过渡期别名（旧名 = 新名）在文件尾部保留，供旧消费者逐步迁移。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from omnicompany.protocol.anchor import (
    AnchorSpec,
    OperatorSpec,
    TransformerSpec,
    VerdictKind,
)
from omnicompany.protocol.format import ConnectionCheck, FormatRegistry


# ── Node Maturity ──


class NodeMaturity(str, Enum):
    """节点成熟度等级 — 控制假设性节点的双轨执行策略"""

    HYPOTHETICAL = "hypothetical"
    """刚建立，未经验证。会附加验证 prompt，与 agent_loop 双轨运行。"""

    GROWING = "growing"
    """多次运行中，部分成功。仍双轨运行但权重偏向管线。"""

    MATURE = "mature"
    """多次对比通过，可信赖。直接走专用管线，不再双轨。"""

    CRYSTALLIZED = "crystallized"
    """完全确定性，无需 LLM。用生成的确定性代码替换。"""


# ── Team Execution Mode (Info Audit 基础设施, 2026-04-09) ──


class TeamExecutionMode(str, Enum):
    """Team 执行模式。

    NORMAL   — 默认: 正常跑真 Router.run(), 产真产物
    DRY_RUN  — 信息审计专属模式 (D2): runner 不调 Router.run(),
               而是用节点的 FORMAT_IN/FORMAT_OUT/DESCRIPTION (+ 历史 llm_calls 真 prompt)
               喂 isolated info_audit_probe, 每节点出一份 InfoAuditReport,
               不产真产物, 只产审计报告
    REPLAY   — (预留) 用历史 llm_calls 回放一次, 对比新旧差异, 目前不实现
    """

    NORMAL = "normal"
    DRY_RUN = "dry_run"
    REPLAY = "replay"


class InfoAuditMode(str, Enum):
    """信息审计的运行方式 (D1 决定)。

    OFF       — 不生成 info audit
    PIGGYBACK — 主 LLM 调用时顺带吐 info_audit JSON 块, 成本最低, 可能虚高
    STRICT    — 调完主 LLM 后, 独立 isolated LLM 再审一次, 准但贵

    默认策略: 每条 Team 前 10 次 run 走 STRICT, 之后自动降 PIGGYBACK,
    用户可通过 `Team.info_audit_mode` 显式 override。
    """

    OFF = "off"
    PIGGYBACK = "piggyback"
    STRICT = "strict"


# 每条 Team 的 STRICT 默认次数阈值 (D1 锁定)
STRICT_AUDIT_DEFAULT_RUNS = 10


# Team 节点


class NodeKind(str, Enum):
    """Team 节点种类"""

    ANCHOR = "anchor"
    """锚点节点: 输入 → 判定 → 路由"""

    TRANSFORMER = "transformer"
    """转换器节点: 类型A → 类型B"""

    SCATTER = "scatter"
    """分发节点: Iterable → 并发子 Team → 汇总数组"""

    SUB_PIPELINE = "sub_pipeline"
    """嵌套子 Team 节点: 将另一条 TeamSpec 作为单一节点嵌入父 Team。

    从父 Team 视角，此节点有确定的 format_in / format_out（子 Team 的入口/出口 Format）。
    运行时：TeamRunner 对此节点递归执行内部 sub_team，
            子 Team 有独立的事件记录、调度和重试语义。
    使用场景：① 复用 Team 片段；② 封装阶段边界（Phase A / B / C）；
              ③ 让父 Team 的拓扑更简洁，隐藏内部实现细节。
    """


class TeamPositionActivation(str, Enum):
    """岗位在当前 TeamSpec 版本中的编制状态。

    这是 Team 的声明元数据，不是运行会话状态。运行中的执行者仍由
    Task.assignee、session binding 与 trace 记录，避免另造一份 lease 真源。
    """

    ACTIVE = "active"
    """当前 TeamSpec 中已有 Worker 节点承接该岗位。"""

    ON_DEMAND = "on_demand"
    """岗位已定义，但当前 TeamSpec 不要求存在对应 Worker 节点。"""


class TeamPositionSpec(BaseModel):
    """Team 内的岗位边界。

    岗位只描述组织职责，不复制 AgentSpec 的模型、prompt、工具、权限和运行预算。
    当前激活的岗位由 ``TeamNode.position_id`` 单向引用；一个岗位可覆盖多个 Worker
    节点，未激活岗位可以暂时没有节点。
    """

    id: str
    """Team 内唯一岗位 ID。"""

    name: str
    """人类可读岗位名。"""

    responsibilities: list[str] = Field(min_length=1)
    """该岗位必须承担的职责。"""

    non_responsibilities: list[str] = Field(min_length=1)
    """该岗位明确不承担的职责，用于阻止职责漂移。"""

    activation: TeamPositionActivation = TeamPositionActivation.ON_DEMAND
    """当前 TeamSpec 版本中的编制状态。"""

    activation_evidence_refs: list[str] = Field(default_factory=list)
    """激活该岗位所依据的任务、benchmark、incident 或裁决引用。"""

    context_refs: list[str] = Field(default_factory=list)
    """岗位需要按需装载的外部上下文引用；不把长期记忆复制进 TeamSpec。"""

    benchmark_refs: list[str] = Field(default_factory=list)
    """验证该岗位是否值得长期保留的 benchmark 引用。"""

    exit_conditions: list[str] = Field(default_factory=list)
    """岗位休眠、合并或退役的可判定条件。"""

    @model_validator(mode="after")
    def _active_position_requires_evidence(self) -> "TeamPositionSpec":
        if self.activation == TeamPositionActivation.ACTIVE and not self.activation_evidence_refs:
            raise ValueError(
                f"active position '{self.id}' requires activation_evidence_refs"
            )
        return self


class TeamGenerationMethod(str, Enum):
    """TeamSpec 的产生方式；无论方式如何，产物仍是同一个 TeamSpec。"""

    DECLARED = "declared"
    """由维护者直接声明的 TeamSpec。"""

    TEAM_BUILDER = "team_builder"
    """由唯一 Team Builder 设施生成的 TeamSpec。"""


class TeamGenerationSpec(BaseModel):
    """TeamSpec 的生成溯源，不是另一种 Team 类型。"""

    method: TeamGenerationMethod
    builder_id: str | None = None
    """生成器 ID。``method=team_builder`` 时必须是现有 ``team-builder``。"""

    request_ref: str | None = None
    """生成请求或原始需求 material 的引用。"""

    source_refs: list[str] = Field(default_factory=list)
    """生成时读取的标准、计划、相似 Team 等来源引用。"""

    parent_team_id: str | None = None
    """由父 Team 拆解生成时，记录父 TeamSpec.id；它不表示新的 Team 类。"""

    @model_validator(mode="after")
    def _team_builder_is_the_only_generator(self) -> "TeamGenerationSpec":
        if self.method == TeamGenerationMethod.TEAM_BUILDER:
            if self.builder_id != "team-builder":
                raise ValueError(
                    "method=team_builder requires builder_id='team-builder'"
                )
        elif self.builder_id is not None:
            raise ValueError("builder_id is only valid for method=team_builder")
        return self


class ScatterSpec(BaseModel):
    """分发节点规约 (Map-Reduce 原语)"""

    id: str
    """规约标识"""

    name: str = ""
    """人类可读名称"""

    iterable_key: str
    """从输入数据中提取的数组键名, e.g. 'tasks'"""

    sub_pipeline: str
    """子 Team 的 ID"""

    max_concurrency: int = 1
    """并发数控制上限 (LLM 限流保护)"""


class TeamNode(BaseModel):
    """Team 中的一个节点"""

    id: str
    """节点唯一标识"""

    kind: NodeKind
    """节点种类"""

    anchor: AnchorSpec | None = None
    """锚点规约 (kind=ANCHOR 时)"""

    transformer: TransformerSpec | None = None
    """转换器规约 (kind=TRANSFORMER 时)"""

    scatter: ScatterSpec | None = None
    """分发规约 (kind=SCATTER 时)"""

    operator: OperatorSpec | None = None
    """同构算子规约（可选）。"""

    subgraph_id: str | None = None
    """子图引用。非 None 时此节点是一个子图入口，
    GraphRunner 遇到此节点时递归执行对应子图。"""

    sub_pipeline: Any | None = None
    """嵌套子 Team 对象（kind=SUB_PIPELINE 时使用）。
    应为 TeamSpec 实例；使用 Any 类型以避免 Pydantic 循环引用。
    运行时：TeamRunner 递归执行此 TeamSpec，产生独立事件记录和重试边界。
    """

    sub_pipeline_format_in: str | None = None
    """子 Team 入口 Format ID（kind=SUB_PIPELINE 时使用）。
    若不提供，由 format_in property 从 sub_pipeline.entry 节点推断。
    """

    sub_pipeline_format_out: str | None = None
    """子 Team 出口 Format ID（kind=SUB_PIPELINE 时使用）。
    若不提供，由 format_out property 从 sub_pipeline 终端节点推断。
    """

    position_id: str | None = None
    """承接的 TeamPositionSpec.id。只做 Team 内映射，不表示运行会话或 assignee。"""

    maturity: NodeMaturity = NodeMaturity.MATURE
    """节点成熟度。默认 MATURE 保持向后兼容。"""

    maturity_score: float = 0.0
    """0~1 的成熟度分数，由双轨对比结果更新。"""

    comparison_wins: int = 0
    """双轨对比中 Team 胜出次数。"""

    comparison_total: int = 0
    """双轨对比总次数。"""

    @property
    def format_in(self) -> str:
        """输入类型"""
        if self.kind == NodeKind.ANCHOR and self.anchor:
            return self.anchor.format_in
        if self.kind == NodeKind.TRANSFORMER and self.transformer:
            return self.transformer.from_format
        if self.kind == NodeKind.SCATTER and self.scatter:
            return "any"
        if self.kind == NodeKind.SUB_PIPELINE:
            if self.sub_pipeline_format_in:
                return self.sub_pipeline_format_in
            sp = self.sub_pipeline
            if sp and hasattr(sp, "entry") and hasattr(sp, "nodes"):
                entry_id = sp.entry
                entry_node = next((n for n in sp.nodes if n.id == entry_id), None)
                if entry_node:
                    try:
                        return entry_node.format_in
                    except Exception:
                        pass
            return "any"
        raise ValueError(f"Node '{self.id}' has no spec")

    @property
    def format_out(self) -> str:
        """输出类型"""
        if self.kind == NodeKind.ANCHOR and self.anchor:
            return self.anchor.format_out
        if self.kind == NodeKind.TRANSFORMER and self.transformer:
            return self.transformer.to_format
        if self.kind == NodeKind.SCATTER and self.scatter:
            return "any"
        if self.kind == NodeKind.SUB_PIPELINE:
            if self.sub_pipeline_format_out:
                return self.sub_pipeline_format_out
            sp = self.sub_pipeline
            if sp and hasattr(sp, "edges") and hasattr(sp, "nodes"):
                nodes_with_out = {e.source for e in sp.edges}
                terminals = [n for n in sp.nodes if n.id not in nodes_with_out]
                if terminals:
                    try:
                        return terminals[0].format_out
                    except Exception:
                        pass
            return "any"
        raise ValueError(f"Node '{self.id}' has no spec")


# Team 边


class TeamEdge(BaseModel):
    """Team 中的一条边"""

    source: str
    """源节点 ID"""

    target: str
    """目标节点 ID"""

    condition: VerdictKind | None = None
    """触发条件 (None = 无条件/总是)。
    当源节点是 Anchor 时，按 Verdict 路由。"""

    condition_expr: str | None = None
    """泛化条件表达式（如 "pain > 0.5"）。
    优先于 condition 字段。GraphRunner 通过 eval 执行。
    为 None 时回退到 condition 字段。"""

    label: str | None = None
    """人类可读的边标签 (用于可视化)"""

    feedback: bool = False
    """反馈边（回路）。反馈边不计入 join barrier 的 in-degree，
    使得 DAG 中的循环不会阻塞 fan-in 汇聚。默认 False 保持向后兼容。"""


# Team 规约


class TeamSpec(BaseModel):
    """Team 规约 — Worker 和边的有向图

    TeamSpec 是声明式的——它描述"是什么"，
    不包含执行逻辑。执行由 TeamRunner 处理。
    """

    id: str
    """Team 唯一标识"""

    name: str
    """人类可读名称"""

    description: str
    """Team 用途描述（技术性，面向开发者）"""

    purpose: str = ""
    """Team 业务目标（一两句话，面向 LLM 补全工具和自动修复系统）。
    回答：这条 Team 要完成什么业务价值？输入是什么，输出是什么？
    示例: '从 demogame CSV diff 自动发现字段规律，产出 process_*.py 配置脚本'
    """

    nodes: list[TeamNode]
    """所有节点"""

    edges: list[TeamEdge]
    """所有边"""

    entry: str
    """入口节点 ID"""

    group: str | None = None
    """Team 所属组。同组 Team 共享事件空间，组外消费者不消费。
    例: "demogame-benchmark"。"""

    tags: list[str] = Field(default_factory=list)
    """Team 的语义标签。发射的事件会自动继承这些标签。
    例: ["demogame.benchmark.battle", "unity.lua"]"""

    min_core_version: str | None = None
    """声明此 Team 所需的最低核心版本。
    TeamRunner 启动时校验，不兼容时 warning。
    例: "0.2.0" """

    parallel_groups: list[list[str]] = Field(default_factory=list)
    """可并行执行的节点组。每个组内的节点无数据依赖，可用 asyncio.gather 并行。
    例: [["pain_classify", "reward_compute", "route_accumulate"]]"""

    positions: list[TeamPositionSpec] = Field(default_factory=list)
    """可选岗位目录。它是 TeamSpec 的组成部分，不是 ProjectTeam/AgentTeam 新类型。"""

    generation: TeamGenerationSpec | None = None
    """可选生成溯源。手写和生成的 Team 都使用同一个 TeamSpec。"""

    @model_validator(mode="after")
    def _validate_position_mapping(self) -> "TeamSpec":
        position_ids = [position.id for position in self.positions]
        if len(position_ids) != len(set(position_ids)):
            raise ValueError("duplicate TeamPositionSpec.id")

        known_positions = set(position_ids)
        mapped_positions: set[str] = set()
        for node in self.nodes:
            if node.position_id is None:
                continue
            if node.position_id not in known_positions:
                raise ValueError(
                    f"node '{node.id}' references unknown position_id "
                    f"'{node.position_id}'"
                )
            mapped_positions.add(node.position_id)

        active_without_node = [
            position.id
            for position in self.positions
            if position.activation == TeamPositionActivation.ACTIVE
            and position.id not in mapped_positions
        ]
        if active_without_node:
            raise ValueError(
                "active positions require at least one TeamNode mapping: "
                f"{active_without_node}"
            )

        if self.generation and self.generation.parent_team_id == self.id:
            raise ValueError("generation.parent_team_id cannot equal TeamSpec.id")
        return self


# 类型检查结果


class EdgeCheckResult(BaseModel):
    """单条边的类型检查结果"""

    edge: TeamEdge
    """被检查的边"""

    source_format_out: str
    """源节点的输出类型"""

    target_format_in: str
    """目标节点的输入类型"""

    connection: ConnectionCheck
    """类型连接检查详情"""


class TeamCheckResult(BaseModel):
    """整个 Team 的类型检查结果"""

    team_id: str
    """被检查的 Team ID"""

    valid: bool
    """Team 是否类型安全"""

    edge_results: list[EdgeCheckResult]
    """每条边的检查结果"""

    errors: list[str]
    """结构性错误 (缺失节点、缺失入口等)"""

    warnings: list[str] = Field(default_factory=list)
    """警告 (孤立节点、未使用的路由等)"""

    @property
    def type_errors(self) -> list[EdgeCheckResult]:
        """所有类型不兼容的边"""
        return [r for r in self.edge_results if not r.connection.compatible]

    @property
    def needs_transformers(self) -> list[EdgeCheckResult]:
        """需要插入 Transformer 的边"""
        return [r for r in self.edge_results if r.connection.needs_transformer]


# Team 类型检查器 (编译器)


class TeamChecker:
    """Team 类型检查器 — LAP 的"编译器"

    验证 Team 中所有边的类型安全性。

    使用方式:
        registry = create_builtin_registry()
        checker = TeamChecker(registry)
        result = checker.check(team_spec)
        if not result.valid:
            for err in result.type_errors:
                print(f"类型冲突: {err.source_format_out} → {err.target_format_in}")
    """

    def __init__(self, registry: FormatRegistry):
        self.registry = registry

    def check(self, team: TeamSpec) -> TeamCheckResult:
        """执行完整的 Team 类型检查"""
        errors: list[str] = []
        warnings: list[str] = []
        edge_results: list[EdgeCheckResult] = []

        # ── 构建节点索引 ──
        node_map: dict[str, TeamNode] = {}
        for node in team.nodes:
            if node.id in node_map:
                errors.append(f"重复的节点 ID: '{node.id}'")
            node_map[node.id] = node

        # ── 检查入口 ──
        if team.entry not in node_map:
            errors.append(f"入口节点 '{team.entry}' 不存在于节点列表中")

        # ── 预计算 fan-in 节点（in-degree > 1，不计 feedback 边）──
        in_degree: dict[str, int] = {n: 0 for n in node_map}
        for edge in team.edges:
            if not edge.feedback and edge.target in in_degree:
                in_degree[edge.target] += 1
        fan_in_nodes: set[str] = {nid for nid, deg in in_degree.items() if deg > 1}

        # ── 检查每条边的类型安全性 ──
        referenced_nodes: set[str] = {team.entry}

        for edge in team.edges:
            referenced_nodes.add(edge.source)
            referenced_nodes.add(edge.target)

            if edge.source not in node_map:
                errors.append(f"边引用了不存在的源节点: '{edge.source}'")
                continue
            if edge.target not in node_map:
                errors.append(f"边引用了不存在的目标节点: '{edge.target}'")
                continue

            source_node = node_map[edge.source]
            target_node = node_map[edge.target]

            try:
                source_out = source_node.format_out
                target_in = target_node.format_in
            except ValueError as e:
                errors.append(str(e))
                continue

            if not self.registry.is_registered(source_out):
                errors.append(f"节点 '{edge.source}' 的输出类型 '{source_out}' 未注册")
                continue
            if not self.registry.is_registered(target_in):
                errors.append(f"节点 '{edge.target}' 的输入类型 '{target_in}' 未注册")
                continue

            if edge.target in fan_in_nodes and not edge.feedback:
                connection = self.registry.check_connection(source_out, target_in)
                if not connection.compatible:
                    warnings.append(
                        f"Fan-in 边 '{edge.source}' → '{edge.target}'："
                        f"{source_out} 与 {target_in} 无继承关系，"
                        f"Runner 将按 dict merge 合并，确保 {target_in} 节点能读取所需字段。"
                    )
                    connection = connection.model_copy(
                        update={"compatible": True, "reason": f"fan-in merge: {connection.reason}"}
                    )
            else:
                connection = self.registry.check_connection(source_out, target_in)

            edge_results.append(
                EdgeCheckResult(
                    edge=edge,
                    source_format_out=source_out,
                    target_format_in=target_in,
                    connection=connection,
                )
            )

        # ── 检查孤立节点 ──
        for node_id in node_map:
            if node_id not in referenced_nodes:
                warnings.append(f"孤立节点: '{node_id}' 未被任何边引用")

        # ── 拓扑校验（DAG 支持）──
        topo_warnings = self._check_topology(team, node_map)
        warnings.extend(topo_warnings)

        all_edges_ok = all(r.connection.compatible for r in edge_results)
        valid = len(errors) == 0 and all_edges_ok

        return TeamCheckResult(
            team_id=team.id,
            valid=valid,
            edge_results=edge_results,
            errors=errors,
            warnings=warnings,
        )

    def _check_topology(
        self,
        team: TeamSpec,
        node_map: dict[str, TeamNode],
    ) -> list[str]:
        """拓扑校验：检测 cycle 中未标记 feedback 的边、fan-in 类型兼容性。"""
        warnings: list[str] = []

        adj: dict[str, list[str]] = {n.id: [] for n in team.nodes}
        feedback_pairs: set[tuple[str, str]] = set()
        for edge in team.edges:
            if edge.source in adj and edge.target in node_map:
                adj[edge.source].append(edge.target)
                if edge.feedback:
                    feedback_pairs.add((edge.source, edge.target))

        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {n: WHITE for n in node_map}
        unmarked_back: list[tuple[str, str]] = []

        def dfs(u: str) -> None:
            color[u] = GRAY
            for v in adj.get(u, []):
                if color.get(v) == GRAY and (u, v) not in feedback_pairs:
                    unmarked_back.append((u, v))
                elif color.get(v) == WHITE:
                    dfs(v)
            color[u] = BLACK

        if team.entry in color:
            dfs(team.entry)

        for src, tgt in unmarked_back:
            warnings.append(
                f"回路边 '{src}' → '{tgt}' 未标记 feedback=True。"
                f"DAG 执行时将自动检测为 back-edge，建议显式标记。"
            )

        in_edges: dict[str, list[str]] = {n.id: [] for n in team.nodes}
        for edge in team.edges:
            if not edge.feedback and edge.target in in_edges:
                in_edges[edge.target].append(edge.source)

        for node_id, sources in in_edges.items():
            if len(sources) <= 1:
                continue
            formats = set()
            for src_id in sources:
                src_node = node_map.get(src_id)
                if src_node:
                    try:
                        formats.add(src_node.format_out)
                    except ValueError:
                        pass
            if len(formats) > 1:
                warnings.append(
                    f"Join 节点 '{node_id}' 有 {len(sources)} 条入边，"
                    f"上游 format_out 不一致: {formats}。"
                    f"Runner 将按 dict merge 合并输入。"
                )

        return warnings


# 便捷构建器


def describe_agent_loop() -> TeamSpec:
    """用 LAP 类型库描述 Agent 双锚点循环 (自举验证)。

    数据流: Anchor_LLM --(FAIL)--> Anchor_Tool --> obs_to_state --> Anchor_LLM
    出口:   Anchor_LLM --(PASS)--> EMIT (finish/reject)
    """
    from omnicompany.protocol.anchor import (
        AnchorSpec,
        Route,
        RouteAction,
        TransformerSpec,
        TransformMethod,
        ValidatorKind,
        ValidatorSpec,
    )

    anchor_llm = TeamNode(
        id="anchor_llm",
        kind=NodeKind.ANCHOR,
        anchor=AnchorSpec(
            id="anchor_llm",
            name="LLM 决策锚点",
            format_in="agent-state",
            format_out="agent-action",
            validator=ValidatorSpec(
                id="llm-self",
                kind=ValidatorKind.SOFT,
                description="LLM 自身作为判定器: 接收状态, 产出 Action",
            ),
            routes={
                VerdictKind.PASS: Route(
                    action=RouteAction.EMIT,
                    feedback="Agent 决定结束 (finish/reject)",
                ),
                VerdictKind.PARTIAL: Route(
                    action=RouteAction.RETRY,
                    feedback="Agent 在思考, 不产生副作用, 继续循环",
                ),
                VerdictKind.FAIL: Route(
                    action=RouteAction.NEXT,
                    target="anchor_tool",
                    feedback="Agent 需要工具执行, 需要外部硬锚定",
                ),
            },
        ),
    )

    anchor_tool = TeamNode(
        id="anchor_tool",
        kind=NodeKind.ANCHOR,
        anchor=AnchorSpec(
            id="anchor_tool",
            name="工具执行锚点",
            format_in="agent-action",
            format_out="tool-observation",
            validator=ValidatorSpec(
                id="tool-executor",
                kind=ValidatorKind.HARD,
                description="工具执行器: schema 校验 + 执行, 确定性判定",
            ),
            routes={
                VerdictKind.PASS: Route(
                    action=RouteAction.NEXT,
                    target="obs_to_state",
                    feedback="工具执行成功, 带观察结果进入状态合并",
                ),
                VerdictKind.FAIL: Route(
                    action=RouteAction.NEXT,
                    target="obs_to_state",
                    feedback="工具执行失败, 带错误诊断进入状态合并",
                ),
            },
        ),
    )

    obs_to_state = TeamNode(
        id="obs_to_state",
        kind=NodeKind.TRANSFORMER,
        transformer=TransformerSpec(
            id="obs-to-state",
            name="观察结果 → Agent 状态",
            from_format="tool-observation",
            to_format="agent-state",
            method=TransformMethod.RULE,
            description=(
                "将工具观察结果合并进 Agent 运行状态。"
                "具体操作: state.last_observation = obs, "
                "state.history.append({action, obs})。"
                "确定性规则转换, 不需要 LLM。"
            ),
        ),
    )

    return TeamSpec(
        id="agent-loop",
        name="Agent 双锚点循环",
        description=(
            "Agent 的 step 循环 = 两个 Anchor + 一个 Transformer 的 Team。"
            "Anchor_LLM (软锚定) 做决策, Anchor_Tool (硬锚定) 做执行, "
            "obs_to_state (Transformer) 做类型转换。"
            "这是 LAP 的最小自治单元——双锚点。"
        ),
        nodes=[anchor_llm, anchor_tool, obs_to_state],
        edges=[
            TeamEdge(
                source="anchor_llm",
                target="anchor_tool",
                condition=VerdictKind.FAIL,
                label="需要工具 (tool_call)",
            ),
            TeamEdge(
                source="anchor_tool",
                target="obs_to_state",
                condition=None,
                label="工具执行完毕, 进入类型转换",
            ),
            TeamEdge(
                source="obs_to_state",
                target="anchor_llm",
                condition=None,
                label="状态合并完成, 回到 LLM 决策",
            ),
        ],
        entry="anchor_llm",
    )


# ── Graph 别名（新名 = 新名，保持向后兼容）──

GraphNode = TeamNode
GraphEdge = TeamEdge
GraphSpec = TeamSpec
GraphChecker = TeamChecker

# ── 过渡期别名：旧名 → 新名（供旧消费者逐步迁移，后续删除）──

PipelineNode = TeamNode
PipelineEdge = TeamEdge
PipelineSpec = TeamSpec
PipelineChecker = TeamChecker
PipelineCheckResult = TeamCheckResult
PipelineExecutionMode = TeamExecutionMode
