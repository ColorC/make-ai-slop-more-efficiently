# [OMNI] origin=codex domain=team_builder ts=2026-07-24T00:00:00Z type=infra
# [OMNI] material_id="material:core.team_builder.agent_allocation.gate.py"
"""Agent 执行方式的确定性前门。

本模块只决定一个已存在 Team 内的工作应当由同一会话、上下文 fork，还是冷委派执行。
它不拆 Team，也不产生 ProjectTeam/AgentTeam 等第二种 Team 类型。大需求拆成子 Team 的
权威仍是现有 ScaleAssessor + DecompositionPlanner。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class AgentAllocationMode(str, Enum):
    """同一 Team 内一次工作的 Agent 会话分配方式。"""

    SINGLE_SESSION = "single_session"
    CONTEXT_FORK = "context_fork"
    COLD_DELEGATE = "cold_delegate"


class ContextCoupling(str, Enum):
    """候选工作与父会话工作集的上下文耦合度。"""

    HIGH = "high"
    LOW = "low"
    UNKNOWN = "unknown"


class AgentAllocationGate(str, Enum):
    """三道门及 Gate C 的可判定子条件。"""

    NEED_EVIDENCE = "need_evidence"
    CONTEXT_INVENTORY_COMPLETE = "context_inventory_complete"
    REQUIRED_CONTEXT_AVAILABLE = "required_context_available"
    CONTEXT_COUPLING_KNOWN = "context_coupling_known"
    CONTEXT_FORK_AVAILABLE = "context_fork_available"
    INPUT_CONTRACT_STABLE = "input_contract_stable"
    OUTPUT_CONTRACT_STABLE = "output_contract_stable"
    INDEPENDENT_VERIFICATION = "independent_verification"


class AgentAllocationEvidence(BaseModel):
    """调用方提交的证据；布尔值默认 False，缺证据时始终 fail closed。"""

    need_evidence_refs: list[str] = Field(default_factory=list)
    required_context_refs: list[str] = Field(default_factory=list)
    available_context_refs: list[str] = Field(default_factory=list)
    context_inventory_complete: bool = False
    context_coupling: ContextCoupling = ContextCoupling.UNKNOWN
    input_contract_stable: bool = False
    output_contract_stable: bool = False
    independent_verification: bool = False
    context_fork_available: bool = False


class AgentAllocationDecision(BaseModel):
    """逐项可审计的分配结论；不把安全条件混成总分。"""

    mode: AgentAllocationMode
    passed_gates: list[AgentAllocationGate] = Field(default_factory=list)
    blocked_gates: list[AgentAllocationGate] = Field(default_factory=list)
    missing_context_refs: list[str] = Field(default_factory=list)
    rationale: str


def decide_agent_allocation(
    evidence: AgentAllocationEvidence,
) -> AgentAllocationDecision:
    """按 Gate A/B/C 顺序决策，任何未知或缺失都退回同一会话。"""
    passed: list[AgentAllocationGate] = []
    blocked: list[AgentAllocationGate] = []

    if evidence.need_evidence_refs:
        passed.append(AgentAllocationGate.NEED_EVIDENCE)
    else:
        blocked.append(AgentAllocationGate.NEED_EVIDENCE)
        return AgentAllocationDecision(
            mode=AgentAllocationMode.SINGLE_SESSION,
            passed_gates=passed,
            blocked_gates=blocked,
            rationale="Gate A rejected: no evidence that another Agent session is needed.",
        )

    if evidence.context_inventory_complete:
        passed.append(AgentAllocationGate.CONTEXT_INVENTORY_COMPLETE)
    else:
        blocked.append(AgentAllocationGate.CONTEXT_INVENTORY_COMPLETE)

    available = set(evidence.available_context_refs)
    missing = [
        ref for ref in evidence.required_context_refs
        if ref not in available
    ]
    if missing:
        blocked.append(AgentAllocationGate.REQUIRED_CONTEXT_AVAILABLE)
    else:
        passed.append(AgentAllocationGate.REQUIRED_CONTEXT_AVAILABLE)

    if blocked:
        return AgentAllocationDecision(
            mode=AgentAllocationMode.SINGLE_SESSION,
            passed_gates=passed,
            blocked_gates=blocked,
            missing_context_refs=missing,
            rationale="Gate B rejected: the shared context contract is incomplete.",
        )

    if evidence.context_coupling == ContextCoupling.UNKNOWN:
        blocked.append(AgentAllocationGate.CONTEXT_COUPLING_KNOWN)
        return AgentAllocationDecision(
            mode=AgentAllocationMode.SINGLE_SESSION,
            passed_gates=passed,
            blocked_gates=blocked,
            rationale="Gate C rejected: context coupling is unknown.",
        )

    passed.append(AgentAllocationGate.CONTEXT_COUPLING_KNOWN)
    if evidence.context_coupling == ContextCoupling.HIGH:
        if evidence.context_fork_available:
            passed.append(AgentAllocationGate.CONTEXT_FORK_AVAILABLE)
            return AgentAllocationDecision(
                mode=AgentAllocationMode.CONTEXT_FORK,
                passed_gates=passed,
                rationale="Gate C selected context fork for a high-coupling work item.",
            )
        blocked.append(AgentAllocationGate.CONTEXT_FORK_AVAILABLE)
        return AgentAllocationDecision(
            mode=AgentAllocationMode.SINGLE_SESSION,
            passed_gates=passed,
            blocked_gates=blocked,
            rationale="Gate C rejected delegation: true context fork is unavailable.",
        )

    contract_gates = (
        (evidence.input_contract_stable, AgentAllocationGate.INPUT_CONTRACT_STABLE),
        (evidence.output_contract_stable, AgentAllocationGate.OUTPUT_CONTRACT_STABLE),
        (evidence.independent_verification, AgentAllocationGate.INDEPENDENT_VERIFICATION),
    )
    for is_ready, gate in contract_gates:
        (passed if is_ready else blocked).append(gate)

    if blocked:
        return AgentAllocationDecision(
            mode=AgentAllocationMode.SINGLE_SESSION,
            passed_gates=passed,
            blocked_gates=blocked,
            rationale="Gate C rejected cold delegation: its contracts are not independently verifiable.",
        )

    return AgentAllocationDecision(
        mode=AgentAllocationMode.COLD_DELEGATE,
        passed_gates=passed,
        rationale="Gate C selected cold delegation for a low-coupling, contract-stable work item.",
    )
