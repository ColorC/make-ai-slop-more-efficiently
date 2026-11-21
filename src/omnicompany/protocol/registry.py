# [OMNI] origin=human ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:protocol.event_type.lifecycle_registry.py"
"""
OmniCompany 事件类型注册表

所有合法的事件类型，按 Agent 生命周期分组。
命名规范: 点分层级，全小写 (domain.entity.verb)

设计原则:
1. 覆盖 Agent 完整决策循环的每一步
2. 每个事件类型都是一个可观测/可干预的锚点
3. 借鉴 OpenHands 的 Action/Observation 二元分类，
   但以更细粒度的生命周期事件替代
"""

from enum import Enum


class EventType(str, Enum):
    """事件类型枚举，按语义域分组"""

    # 任务级 (Task Lifecycle)
    # 一个任务从 INTENT 到 FINISH/ERROR 的完整生命周期
    TASK_INTENT = "task.intent"
    """编排层下发任务意图，包含指令文本和约束条件"""

    TASK_FINISH = "task.finish"
    """Agent 宣告任务完成，包含结果摘要"""

    TASK_REJECT = "task.reject"
    """Agent 评估后拒绝任务（能力不匹配、约束冲突等）"""

    TASK_ERROR = "task.error"
    """不可恢复错误导致任务终止"""

    TASK_POSITION_CLAIMED = "task.position.claimed"
    """任务已原子认领并路由到 canonical Team 岗位；不表示已经开始执行。"""

    TASK_POSITION_CLAIM_REJECTED = "task.position.claim_rejected"
    """任务认领或岗位路由因归属、冲突或合同问题被拒绝。"""

    # Agent 决策循环 (Agent Step Loop)
    # 每一轮 step() 调用产生的细粒度事件
    LLM_REQUEST = "agent.llm.request"
    """即将向 LLM 发送推理请求。payload 包含 prompt 摘要和 token 预估。
    干预点: Prompt Interceptor 可在此修改/增强 prompt"""

    LLM_RESPONSE = "agent.llm.response"
    """LLM 返回推理结果。payload 包含 response 摘要、token 消耗、耗时"""

    TOOL_CALL = "agent.tool.call"
    """Agent 决定调用某个工具。payload 包含工具名和参数。
    干预点: Policy Interceptor 可拦截危险操作（如 rm -rf）"""

    TOOL_RESULT = "agent.tool.result"
    """工具执行完毕返回结果。payload 包含执行状态和输出摘要。
    干预点: Observation Modifier 可篡改/过滤返回值"""

    AGENT_THINK = "agent.think"
    """Agent 的内部思考/推理记录（scratchpad）。
    不触发任何副作用，仅供可观测性"""

    # 状态与控制 (State & Control)
    STATE_CHANGE = "agent.state.change"
    """Agent 状态机转换（如 idle→running, running→awaiting）。
    payload 包含 from_state 和 to_state"""

    DELEGATE = "agent.delegate"
    """当前 Agent 将子任务委派给另一个 Agent。
    payload 包含 delegate_to (目标 Agent 名) 和子任务描述"""

    DELEGATE_RETURN = "agent.delegate.return"
    """被委派的子 Agent 完成并返回结果"""

    # 系统级 (System)
    INTERVENTION = "system.intervention"
    """外部干预事件（影子干预）。
    可由不在流程图上的组件发出，用于实时修正 Agent 行为"""

    RESOURCE_ALERT = "system.resource"
    """资源层告警（沙箱 OOM、磁盘满、网络不可达等）"""

    HEARTBEAT = "system.heartbeat"
    """Agent 或 Controller 的存活心跳"""

    # 信息审计 (Info Audit Infrastructure, 2026-04-09)
    NODE_INFO_AUDIT_REPORTED = "node.info_audit_reported"
    """节点产出 InfoAuditReport。payload 包含 node_id / trace_id / sufficiency /
    missing_critical / fallback_recommended / confidence_self / mode (strict|piggyback)。
    长期观测 & 跨 run diff 的单一来源。"""

    NODE_FALLBACK_TRIGGERED = "node.fallback_triggered"
    """节点触发 UniversalFallbackLoop 兜底。payload 包含 node_id / trace_id /
    trigger_reason (sufficiency/verdict_fail/design_hint) / missing_critical 摘要。
    用于识别"依赖 fallback 成常态"的设计病灶。"""

    LLM_CALL_RECORDED = "llm.call_recorded"
    """LLMClient 统一审计层记录的一次真实 LLM 调用 (Phase 2.5)。
    payload 包含 node_id / role / model / prompt_preview / response_preview /
    tools / info_audit_ref / latency_ms。
    异步非阻塞写入; 失败只 WARN 不阻塞正常路径。"""
