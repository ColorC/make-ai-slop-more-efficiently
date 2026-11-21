# [OMNI] origin=claude-code domain=evolution/workflow ts=2026-04-08T03:23:38Z
# [OMNI] material_id="material:core.evolution.workflow.hypothesis_board_model.py"
"""假设黑板数据结构

HypothesisBoard 是本次进化会话的唯一状态载体。
持久化在数据库里，每次 Agent 调用读板→处理→写板→退出。

详细设计见：docs/plans/[2026-04-04]EVOLUTION-WORKFLOW-DESIGN/HYPOTHESIS_BLACKBOARD.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class HypothesisStatus(str, Enum):
    ACTIVE = "active"
    """当前活跃，可被选为 focus"""

    DORMANT = "dormant"
    """因修改锁定而暂停，锁定解除后重新评估"""

    ELIMINATED = "eliminated"
    """被实验证伪，或置信度过低"""

    CONFIRMED = "confirmed"
    """实验 PASS，已固化"""


@dataclass
class Hypothesis:
    """单条假设

    每条假设描述一个关于"哪里出了问题、为什么"的具体猜测。
    """

    id: str

    # ── 假设内容 ──
    statement: str
    """具体的假设陈述（不允许 '节点 X 质量差' 这类空话）
    例：'SummaryRouter 的 prompt 没有要求输出具体工具名，
         导致在工具密集型任务中输出只有描述性文字'"""

    suspect_node: str | None
    """主要怀疑的节点 ID（None = 跨节点/Format 定义问题）"""

    suspect_edge: str | None = None
    """怀疑的边（Format 不匹配时使用，格式 'node_a→node_b'）"""

    # ── 评估状态 ──
    confidence: float = 0.5
    """0.0 ~ 1.0，驱动排序、锁定决策、淘汰"""

    status: HypothesisStatus = HypothesisStatus.ACTIVE

    # ── 证据 ──
    supporting_traces: list[str] = field(default_factory=list)
    """支持该假设的 trace_id 列表"""

    contradicting_traces: list[str] = field(default_factory=list)
    """反驳该假设的 trace_id 列表"""

    # ── 相关性范围（控制上下文加载） ──
    relevant_nodes: list[str] = field(default_factory=list)
    """这个假设涉及哪些节点（含上下游）"""

    relevant_traces: list[str] = field(default_factory=list)
    """需要加载哪些 trace 才能评估这个假设"""

    # ── 可证伪性 ──
    falsification_test: str = ""
    """什么实验结果会推翻这个假设"""

    # ── 元信息 ──
    created_by: str = "shallow_trace"
    """谁创建了这条假设（shallow_trace | deep_diagnosis | experiment_result）"""

    parent_hypothesis_id: str | None = None
    """从哪个假设细化来的（None = 根假设）"""

    created_at: datetime = field(default_factory=_utcnow)
    last_updated: datetime = field(default_factory=_utcnow)

    # ── 实验记录（简化版，完整版在 ExperimentRecord） ──
    experiment_count: int = 0
    """已做过的实验次数"""

    last_experiment_outcome: str | None = None
    """最近一次实验结论（PASS | FAIL_NO_REGRESSION | FAIL_WITH_REGRESSION）"""

    anti_pattern: str = ""
    """如果该假设被证伪：记录'什么方向不行、为什么'，供后续诊断参考"""


@dataclass
class ExperimentRecord:
    """一次受控实验的完整记录"""

    id: str
    hypothesis_id: str
    locked_node: str

    # 实验设计
    change_description: str
    """改了什么（具体描述，不是抽象策略）"""

    change_type: str
    """prompt | logic | format | insert_node | split_node"""

    # 测试集
    failing_traces_tested: list[str] = field(default_factory=list)
    passing_traces_tested: list[str] = field(default_factory=list)

    # 结果
    outcome: str | None = None
    """PASS | FAIL_NO_REGRESSION | FAIL_WITH_REGRESSION"""

    newly_passing_traces: list[str] = field(default_factory=list)
    still_failing_traces: list[str] = field(default_factory=list)
    regressed_traces: list[str] = field(default_factory=list)

    causal_explanation: str = ""
    """为什么有效/无效（因果解释，不是'reward 提高了'）"""

    anti_pattern: str = ""
    """如果失败：什么情况下这个方向不起作用"""

    created_at: datetime = field(default_factory=_utcnow)
    completed_at: datetime | None = None


@dataclass
class HypothesisBoard:
    """假设黑板 — 本次进化会话的唯一持久化状态

    不在内存里，存在数据库里。
    每次 Agent 调用从板读取当前状态，处理完写回去，然后退出。
    """

    board_id: str
    """本次进化会话的唯一 ID"""

    pipeline_id: str
    trace_id: str
    """触发本次进化的疼痛 trace"""

    quality_verdict: str
    """疼痛描述（来自 QualityPainSignal）"""

    # ── 原始输入（用于重跑实验）──
    pipeline_input: dict = field(default_factory=dict)
    """触发本次诊断的原始管线输入，用于 B.3 ExperimentRunner 重跑管线"""

    # ── 假设池 ──
    hypotheses: list[Hypothesis] = field(default_factory=list)

    # ── 修改锁定 ──
    modification_lock: str | None = None
    """当前锁定的节点 ID（None = 未锁定）"""

    locked_edge: str | None = None
    """跨节点问题时锁定的边（None = 锁定的是节点）"""

    # ── 实验记录 ──
    experiment_log: list[ExperimentRecord] = field(default_factory=list)

    active_experiment_id: str | None = None
    """当前正在进行的实验 ID（None = 无进行中实验）"""

    # ── 状态机 ──
    status: str = "diagnosing"
    """diagnosing | experimenting | validating | done | escalated"""

    escalation_reason: str = ""
    """升级原因（当所有假设 confidence < 0.1 时填写）"""

    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    # ── 辅助方法 ──

    def active_hypotheses(self) -> list[Hypothesis]:
        return [h for h in self.hypotheses if h.status == HypothesisStatus.ACTIVE]

    def focus_candidate(self) -> Hypothesis | None:
        """选置信度最高的 ACTIVE 假设作为下一个 focus"""
        actives = self.active_hypotheses()
        return max(actives, key=lambda h: h.confidence) if actives else None

    def get_hypothesis(self, hypothesis_id: str) -> Hypothesis | None:
        for h in self.hypotheses:
            if h.id == hypothesis_id:
                return h
        return None

    def should_escalate(self) -> bool:
        """所有活跃假设 confidence 都低于阈值 → 需要升级"""
        actives = self.active_hypotheses()
        if not actives:
            return True
        return all(h.confidence < 0.1 for h in actives)

    def lock(self, node_id: str) -> None:
        """锁定节点，其余 ACTIVE 假设 → DORMANT"""
        self.modification_lock = node_id
        self.status = "experimenting"
        for h in self.hypotheses:
            if h.status == HypothesisStatus.ACTIVE:
                h.status = HypothesisStatus.DORMANT
        self.updated_at = _utcnow()

    def unlock(self) -> None:
        """解锁，DORMANT 假设重新激活"""
        self.modification_lock = None
        self.locked_edge = None
        self.active_experiment_id = None
        self.status = "diagnosing"
        for h in self.hypotheses:
            if h.status == HypothesisStatus.DORMANT:
                h.status = HypothesisStatus.ACTIVE
        self.updated_at = _utcnow()
