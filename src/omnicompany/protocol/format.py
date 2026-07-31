# [OMNI] origin=claude-code ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:protocol.semantic_type_system.registry.py"
"""
LAP Format — 语义类型系统

Format 是 LAP 的灵魂。它不是 JSON Schema 那种结构定义，
而是携带语义的类型——描述"流过管线的这个东西是什么"。

Format 之间存在继承关系（语义继承）：
    子类型保持父类型的意图语义，但改变了表达结构。
    Code <: Spec <: Requirement

类型兼容性规则:
    COMPATIBLE(A, B) =
        A == B                          直连（短路）
        OR A <: B                       A 是 B 的子类型，自动向上转型
        OR EXISTS Transformer(A → B)    显式转换可用
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Format(BaseModel):
    """语义类型

    LAP 的类型原语。每个 Format 描述一种数据的语义身份，
    而非仅仅描述其结构。

    V0.2: Format 的完整身份由 (id, tags) 共同定义。
    id 是结构身份 (是什么)，tags 是语义维度 (属于什么领域/状态)。
    对人类: name 应当清晰描述类型全部含义。
    对计算机: (id, tags) 整体是唯一标识。

    示例:
        Format(id="requirement", name="Requirement",
               description="一个有状态的意图")

        Format(id="code-diff", name="CodeDiff",
               description="版本控制系统的代码变更快照",
               parent="requirement",
               tags=["source.vcs", "content.diff"])
    """

    id: str
    """类型唯一标识 (结构身份)"""

    name: str
    """人类可读名称。应当完整描述类型的语义含义。"""

    description: str
    """自然语言语义描述。
    这是协议的"语言锚定"之根——LLM 和人类都能读懂。"""

    parent: str | None = None
    """父类型 ID。None 表示根类型。
    语义继承: 子类型保持父类型的意图，但改变表达结构。"""

    json_schema: dict[str, Any] | None = None
    """可选的结构约束 (JSON Schema)。
    当存在时，可用于硬锚定（结构校验）。
    当不存在时，只有语义约束（需要软锚定）。"""

    examples: list[Any] = Field(default_factory=list)
    """示例实例。用于 LLM prompt 构建和文档。"""

    # V0.2 新增字段

    tags: list[str] = Field(default_factory=list)
    """语义标签。该类型的固有语义维度，点分层级命名。
    标签越多 = 类型越窄 = 语义越精确。
    例: ["source.p4", "domain.battle", "content.diff"]"""

    semantic_preconditions: list[str] = Field(default_factory=list)
    """语义前置条件 (人类可读)。
    成为该类型的数据必须满足的语义约束。
    指导 validator 实现者知道该检查什么。
    例: ["changelist 存在于 P4 中", "changelist 包含至少一个脚本文件"]"""

    required_tags: list[str] = Field(default_factory=list)
    """必需标签 (机器可检查)。
    输入数据必须已具备的标签——即上游 validator 必须已通过
    granted_tags 授予了这些标签。
    TeamChecker 可在编译期验证上游是否覆盖。
    例: ["source.p4.verified", "domain.battle"]"""

    components: list[str] = Field(default_factory=list)
    """组合组件（has-a 关系）。该 Format 由哪些子 Format 共同构成。

    与 parent（is-a）的区别：
      parent: 语义向上收敛，子类型可当父类型用（Code <: Spec）
      components: 结构显式声明，多个独立语义单元的汇聚点

    当 AnchorSpec.format_in 指向 composite Format 时，
    Runner 的 _merge_inputs() 使用 component format_id 作为 key，
    而非不透明的 _from_{node_id}，使 Router.run() 能精确访问各路输入。

    注意：所有 component ID 必须在注册该 Format 之前先行注册。

    示例:
      Format(id="oa.automation-context",
             components=["oa.workflow-info", "feishu.api-spec", "project.codebase"])
      → Router.run() 中: input_data["feishu.api-spec"] 直接访问collab platform信息
    """


class FormatRegistry:
    """Format 注册表 + 类型检查器

    管理所有已注册的 Format，提供子类型检查和兼容性判定。
    这是 LAP 的"编译器"的核心组件。
    """

    def __init__(self) -> None:
        self._formats: dict[str, Format] = {}

    def register(self, fmt: Format, *, force: bool = False) -> None:
        """注册一个 Format。

        S3e.2 (2026-04-08) 起默认禁止重复 id——Format 是全局类型契约,
        静默覆盖会让不同 package 的 Router 读到不同语义的同一个 id,
        是整个 LAP 的严重语义污染。

        如果确实需要覆盖(比如测试 / 热替换),显式传 force=True。
        """
        if fmt.parent and fmt.parent not in self._formats:
            raise ValueError(
                f"Cannot register '{fmt.id}': parent '{fmt.parent}' not found. "
                f"Register parent first."
            )
        for comp_id in fmt.components:
            if comp_id not in self._formats:
                raise ValueError(
                    f"Cannot register '{fmt.id}': component '{comp_id}' not found. "
                    f"Register all components first."
                )
        if fmt.components:
            self._check_component_cycles(fmt)
        if fmt.id in self._formats and not force:
            existing = self._formats[fmt.id]
            raise ValueError(
                f"Format id '{fmt.id}' 已注册 "
                f"(existing: name={existing.name!r}, "
                f"new: name={fmt.name!r}). "
                f"Format id 必须全局唯一。如果是热替换场景,请显式传 force=True。"
            )
        self._formats[fmt.id] = fmt

    def get(self, format_id: str) -> Format:
        """获取已注册的 Format"""
        if format_id not in self._formats:
            raise KeyError(f"Format '{format_id}' not registered")
        return self._formats[format_id]

    def is_registered(self, format_id: str) -> bool:
        return format_id in self._formats

    # 复合 Format 支持

    def _check_component_cycles(self, fmt: Format) -> None:
        """BFS 检测 components 引用是否成环（A.components 含 B → B.components 含 A）。"""
        visited: set[str] = set()
        queue: list[str] = list(fmt.components)
        while queue:
            cid = queue.pop(0)
            if cid == fmt.id:
                raise ValueError(
                    f"Format '{fmt.id}' has circular component reference."
                )
            if cid in visited:
                continue
            visited.add(cid)
            dep = self._formats.get(cid)
            if dep:
                queue.extend(dep.components)

    def is_composite(self, format_id: str) -> bool:
        """是否为复合 Format（有非空 components 列表）。"""
        fmt = self._formats.get(format_id)
        return bool(fmt and fmt.components)

    def get_all_components(self, format_id: str) -> list[str]:
        """递归展开所有叶子 component（BFS，不含自身）。

        对于有嵌套 composite 的 Format，只返回最终叶子（无 components 的 Format）。
        """
        result: list[str] = []
        visited: set[str] = set()
        queue: list[str] = [format_id]
        while queue:
            cid = queue.pop(0)
            if cid in visited:
                continue
            visited.add(cid)
            dep = self._formats.get(cid)
            if dep and dep.components:
                queue.extend(dep.components)
            elif cid != format_id:
                result.append(cid)
        return result

    # 类型关系

    def ancestors(self, format_id: str) -> list[str]:
        """返回从 format_id 到根类型的继承链（不含自身）

        例: ancestors("code") → ["spec", "requirement"]
        """
        chain: list[str] = []
        current = self._formats.get(format_id)
        while current and current.parent:
            chain.append(current.parent)
            current = self._formats.get(current.parent)
        return chain

    def is_subtype(self, child: str, parent: str) -> bool:
        """判断 child 是否是 parent 的子类型

        child <: parent 意味着:
        child 可以在需要 parent 的地方使用（协变）

        例: is_subtype("code", "requirement") → True
            is_subtype("requirement", "code") → False
        """
        if child == parent:
            return True
        return parent in self.ancestors(child)

    def compatible(self, source: str, target: str) -> bool:
        """检查 source 类型是否可以连接到 target 类型

        三种兼容方式:
        1. 相同类型 → 直连
        2. source 是 target 的子类型 → 自动向上转型
        3. 需要 Transformer → 由调用方处理（此处返回 False）

        注意: 反向（父→子）不自动兼容，需要显式 Transformer。
        例如 Requirement → Code 需要 Transformer（本质上就是编程）。
        """
        return self.is_subtype(source, target)

    def check_connection(self, source: str, target: str) -> ConnectionCheck:
        """检查两个类型之间的连接性，返回详细结果"""
        if source == target:
            return ConnectionCheck(
                compatible=True,
                reason=f"直连: {source} == {target}",
            )

        if self.is_subtype(source, target):
            return ConnectionCheck(
                compatible=True,
                reason=f"子类型: {source} <: {target}",
            )

        if self.is_subtype(target, source):
            return ConnectionCheck(
                compatible=False,
                reason=(
                    f"类型不兼容: {source} 是 {target} 的父类型。"
                    f"需要 Transformer: {source} → {target}"
                ),
                needs_transformer=True,
                transformer_from=source,
                transformer_to=target,
            )

        return ConnectionCheck(
            compatible=False,
            reason=f"类型冲突: {source} 与 {target} 无继承关系",
            needs_transformer=True,
            transformer_from=source,
            transformer_to=target,
        )

    async def verify_semantics(self, text: str, format_id: str) -> float:
        """运行时的语义验证：计算输入文本与预设类型之间的 Cosine 相似度 (0.0~1.0)。
        
        对于结构化的 JSON 对象应使用 jsonschema，但对于模糊的自然语言文本，
        以往需要高成本的 LLM Validator 来判断它是否属于某种 Format，
        现在可通过 Embedding 降级加速（>0.9 通常可跳过 LLM 校对）。
        """
        fmt = self.get(format_id)
        # 将 Format 的身份定义拼接为目标空间向量
        fmt_text = f"Format {fmt.name}: {fmt.description}. Tags: " + " ".join(fmt.tags)
        
        from omnicompany.runtime.llm.embedding_client import get_embedding_client
        client = get_embedding_client()
        
        v_text = await client.get_embedding(text)
        v_fmt = await client.get_embedding(fmt_text)
        
        return client.cosine_sim(v_text, v_fmt)

    # 内省

    def all_formats(self) -> list[Format]:
        return list(self._formats.values())

    def type_tree(self) -> dict[str, list[str]]:
        """返回类型继承树 (parent_id → [child_ids])"""
        tree: dict[str, list[str]] = {"__root__": []}
        for fmt in self._formats.values():
            parent_key = fmt.parent or "__root__"
            tree.setdefault(parent_key, []).append(fmt.id)
        return tree


class ConnectionCheck(BaseModel):
    """类型连接检查结果"""

    compatible: bool
    """是否直接兼容"""

    reason: str
    """判定原因（人类可读）"""

    needs_transformer: bool = False
    """是否需要插入 Transformer"""

    transformer_from: str | None = None
    """Transformer 源类型 (needs_transformer=True 时)"""

    transformer_to: str | None = None
    """Transformer 目标类型 (needs_transformer=True 时)"""


# 预定义 Format 层级
#
# 这是 LAP 的"标准库"——一组预定义的语义类型。
# 用户可以扩展，但这些是最常用的。

BUILTIN_FORMATS = [
    # 根类型
    Format(
        id="requirement",
        name="Requirement",
        description="一个有状态的意图。LAP 类型系统的根类型。"
        "所有流过管线的数据都是某种形态的需求。",
    ),
    # Requirement 的直接子类型
    Format(
        id="intent",
        name="Intent",
        description="明确可执行的意图。从模糊的 Requirement 中提炼出的、"
        "可直接被管线消费的结构化输入。",
        parent="requirement",
    ),
    Format(
        id="spec",
        name="Specification",
        description="结构化的意图描述。将模糊的需求明确化为可执行的规格。",
        parent="requirement",
    ),
    Format(
        id="ticket",
        name="Ticket",
        description="工单形态的意图。来自项目管理系统（Jira、GitHub Issues 等）。",
        parent="requirement",
    ),
    Format(
        id="chat-message",
        name="ChatMessage",
        description="对话形态的意图。来自用户的自然语言输入。",
        parent="requirement",
    ),
    Format(
        id="ci-signal",
        name="CISignal",
        description="持续集成信号。构建失败、测试失败、安全告警等。",
        parent="requirement",
    ),
    # Spec 的子类型
    Format(
        id="code",
        name="Code",
        description="可执行的意图实现。源代码、脚本、配置文件。",
        parent="spec",
    ),
    Format(
        id="test-plan",
        name="TestPlan",
        description="可验证的测试策略。描述如何验证 Spec 的实现。",
        parent="spec",
    ),
    Format(
        id="doc",
        name="Document",
        description="人类可读的描述。文档、README、API 说明。",
        parent="spec",
    ),
    # Code 的子类型
    Format(
        id="binary",
        name="Binary",
        description="可运行的编译产物。编译后的二进制、Docker 镜像。",
        parent="code",
    ),
    # TestPlan 的子类型
    Format(
        id="test-result",
        name="TestResult",
        description="测试执行报告。包含通过/失败/跳过的测试结果。",
        parent="test-plan",
    ),
    # Doc 的子类型
    Format(
        id="api-doc",
        name="APIDoc",
        description="机器+人类可读的接口描述。OpenAPI Spec、GraphQL Schema。",
        parent="doc",
    ),
    # 独立类型: Agent 运行时
    Format(
        id="agent-state",
        name="AgentRunState",
        description="Agent 的运行时状态。包含当前指令、历史、上下文。"
        "是需求在 Agent 循环中的运行态表示。",
        parent="requirement",
    ),
    Format(
        id="agent-action",
        name="AgentAction",
        description="Agent 的单步决策输出。tool_call / think / finish / delegate。",
        parent="requirement",
    ),
    Format(
        id="tool-observation",
        name="ToolObservation",
        description="工具执行后的观察结果。执行状态、输出内容。",
        parent="requirement",
    ),
    # Test 相关类型
    Format(
        id="bash.stdout.test_usage",
        name="TestUsageOutput",
        description="测试执行后的标准输出，包含测试覆盖率统计信息。",
        parent="tool-observation",
        tags=["domain.test", "content.output", "format.stdout"],
    ),
    Format(
        id="analysis.test.metrics",
        name="TestMetricsReport",
        description="结构化的测试质量报告，包含覆盖率统计、质量评估指标。",
        parent="tool-observation",
        tags=["domain.analysis", "content.metrics", "format.json"],
    ),
    # 信息不足处理模式 — 思维实验#2 §2 核心要求
    Format(
        id="info.insufficient",
        name="InsufficientInformation",
        description="检测到信息不足的情境。需要选择 Query/Assume/Ask 三种策略之一。",
        parent="requirement",
        tags=["domain.meta", "signal.info_gap"],
    ),
    Format(
        id="kb.query.result",
        name="KBQueryResult",
        description="知识库查询结果。通过检索现有文档、代码、历史记录获得的信息。",
        parent="tool-observation",
        tags=["domain.knowledge", "action.query"],
    ),
    Format(
        id="assumption.proposal",
        name="AssumptionProposal",
        description="基于有限信息的合理假设。需要后续验证，具有置信度评分。",
        parent="requirement",
        tags=["domain.meta", "action.assume", "requires.verification"],
    ),
    Format(
        id="user.answer",
        name="UserAnswer",
        description="用户的直接回答。用于填补信息缺口或确认假设。",
        parent="chat-message",
        tags=["domain.user", "action.ask"],
    ),
    # 状态语义类型 — 思维实验#1 §9 核心要求：状态应当也是一类语义类型
    Format(
        id="state.git.committed",
        name="GitCommittedState",
        description="Git 仓库处于已提交状态。有明确的 commit hash 作为锚点。",
        parent="requirement",
        tags=["state.git", "anchor.hard"],
    ),
    Format(
        id="state.git.uncommitted_changes",
        name="GitUncommittedState",
        description="Git 仓库有未提交的更改。需要小心操作避免丢失。",
        parent="requirement",
        tags=["state.git", "anchor.soft", "caution.required"],
    ),
    Format(
        id="state.code.compiled",
        name="CodeCompiledState",
        description="代码已编译成功。有二进制产物可作为验证锚点。",
        parent="requirement",
        tags=["state.build", "anchor.hard"],
    ),
    Format(
        id="state.tests.passing",
        name="TestsPassingState",
        description="测试全部通过。可以作为安全修改的基准。",
        parent="requirement",
        tags=["state.test", "anchor.hard"],
    ),
    Format(
        id="state.tests.failing",
        name="TestsFailingState",
        description="测试失败。需要修复后才能继续其他修改。",
        parent="requirement",
        tags=["state.test", "anchor.hard", "fix.required"],
    ),
    Format(
        id="state.env.dirty",
        name="EnvironmentDirtyState",
        description="环境有未记录的更改。可能存在依赖问题。",
        parent="requirement",
        tags=["state.env", "anchor.soft", "caution.required"],
    ),
    # 调试循环语义类型 — 思维实验#2 §3 核心要求：调试循环的结构化支持
    Format(
        id="debug.hypothesis",
        name="DebugHypothesis",
        description="关于问题根源的假设陈述。应包含可验证的预测。",
        parent="requirement",
        tags=["domain.debug", "phase.hypothesis"],
    ),
    Format(
        id="debug.breakpoint.log",
        name="DebugBreakpointLog",
        description="在代码关键位置插入的断点/日志输出。",
        parent="tool-observation",
        tags=["domain.debug", "phase.instrumentation"],
    ),
    Format(
        id="debug.execution.trace",
        name="DebugExecutionTrace",
        description="程序执行的实际 trace，包含变量值和调用栈。",
        parent="tool-observation",
        tags=["domain.debug", "phase.observe"],
    ),
    Format(
        id="debug.verification.result",
        name="DebugVerificationResult",
        description="假设验证结果。支持/反驳假设的证据。",
        parent="test-result",
        tags=["domain.debug", "phase.verify"],
    ),
    # 用户模型语义类型 — 思维实验#1 §6 核心要求：用户认知与交互
    Format(
        id="user.model",
        name="UserModel",
        description="用户的能力模型和知识领域。用于调整解释深度和任务分配。",
        parent="requirement",
        tags=["domain.user", "content.model"],
    ),
    Format(
        id="user.preference",
        name="UserPreference",
        description="用户的偏好设置。包括工具选择、输出格式、工作时间等。",
        parent="requirement",
        tags=["domain.user", "content.preference"],
    ),
    Format(
        id="user.privacy.context",
        name="UserPrivacyContext",
        description="隐私敏感上下文。标记不应记录或传播的信息。",
        parent="requirement",
        tags=["domain.user", "content.privacy", "sensitive"],
    ),
    # 探索行为语义类型 — 思维实验#1 §3 核心要求：探索行为
    Format(
        id="exploration.task",
        name="ExplorationTask",
        description="探索任务定义。包含探索类型、目标、安全等级等信息。",
        parent="requirement",
        tags=["domain.exploration", "action.explore"],
    ),
    Format(
        id="exploration.result",
        name="ExplorationResult",
        description="探索结果。包含发现、恐惧锚点、边界发现等。",
        parent="requirement",
        tags=["domain.exploration", "content.discovery"],
    ),
    Format(
        id="exploration.curiosity",
        name="CuriosityDrivenExploration",
        description="好奇心驱动的探索。无明确目标，因'山在那儿'而探索。",
        parent="exploration.task",
        tags=["domain.exploration", "motivation.curiosity"],
    ),
    Format(
        id="exploration.fear_anchor",
        name="FearAnchor",
        description="恐惧区域锚点。将未知转化为可处理的锚点。",
        parent="exploration.task",
        tags=["domain.exploration", "signal.fear", "anchor.fear"],
    ),
    Format(
        id="exploration.boundary_test",
        name="BoundaryTest",
        description="边界测试任务。测试系统能力边界。",
        parent="exploration.task",
        tags=["domain.exploration", "action.test_boundary"],
    ),
    # 自我观测语义类型 — 思维实验#1 §7 核心要求：自我认知
    Format(
        id="self.code.snapshot",
        name="SelfCodeSnapshot",
        description="自身代码的快照。用于自我认知但不直接修改。",
        parent="requirement",
        tags=["self.observation", "read.only"],
    ),
    Format(
        id="self.execution.history",
        name="SelfExecutionHistory",
        description="自身执行历史的聚合视图。",
        parent="requirement",
        tags=["self.observation", "temporal.pattern"],
    ),
    Format(
        id="self.modification.safeguard",
        name="SelfModificationSafeguard",
        description="自我修改的安全检查。避免宕机的截停节点。",
        parent="requirement",
        tags=["self.modification", "safety.critical"],
    ),
    # 状态转换语义类型 — 思维实验#1 §9 核心要求：状态认知
    Format(
        id="state.transition",
        name="StateTransition",
        description="状态转换的显式记录。包含源状态、操作、目标状态。",
        parent="requirement",
        tags=["state.meta", "transition.record"],
    ),
    Format(
        id="state.observation.discrepancy",
        name="ObservationStateDiscrepancy",
        description="观测与预期状态的不一致。触发调试循环。",
        parent="info.insufficient",
        tags=["state.meta", "debug.trigger"],
    ),
    # 知识结晶语义类型 — 思维实验#1 §2 核心要求：知识固化
    Format(
        id="knowledge.reuse.metric",
        name="KnowledgeReuseMetric",
        description="知识复用度指标。用于决定知识应该固化还是按需查询。",
        parent="requirement",
        tags=["domain.knowledge", "content.metric", "decision.crystallization"],
    ),
    Format(
        id="knowledge.crystallized",
        name="CrystallizedKnowledge",
        description="已固化的知识。高频使用，已嵌入 prompts 或知识库索引。",
        parent="requirement",
        tags=["domain.knowledge", "state.crystallized"],
    ),
]


def create_builtin_registry() -> FormatRegistry:
    """创建包含所有内置 Format 的注册表（业务域 + 系统域）"""
    registry = FormatRegistry()
    for fmt in BUILTIN_FORMATS:
        registry.register(fmt)

    from omnicompany.runtime.signals.self_types import register_system_formats
    register_system_formats(registry)

    return registry
