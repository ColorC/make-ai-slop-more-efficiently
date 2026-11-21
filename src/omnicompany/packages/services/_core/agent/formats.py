# [OMNI] origin=claude-code domain=services/agent/formats ts=2026-04-18
# [OMNI] material_id="material:core.agent.format_definitions.registry.py"
"""services.agent.formats — Agent Node Loop 的 10 个 Format

每个 Format 都经过 plan.md §0.2 的 4 项判别：
1. 脱离本 Router 也能独立解释
2. 可作为事件总线上的独立记录读懂
3. 多场景复用可能（不是单向管道胶水）
4. 变化有业务意义

数据流：
    input_data ──prompt_builder──► agent.prompt-built
                                        │
                                        ▼  (每轮循环)
    agent.context-request ──context_compact──► agent.context-compacted
                                                    │
                                                    ▼
    agent.llm-request ──llm_call──► agent.llm-response
                                        │
                              ┌─────────┴─────────┐
                              │ tool_uses? no      │ tool_uses? yes
                              ▼                    ▼
                  agent.result-request     agent.tool-request
                         │                         │
                  ──extract_result──►       ──tool_dispatch──►
                         │                         │
                         ▼                         ▼
                agent.result-final         agent.tool-response
                                                   │
                                                   └──► 回循环顶部
"""

from __future__ import annotations

from omnicompany.protocol.format import Format, FormatRegistry


# ═══════════════════════════════════════════════════════════════════════
# PROMPT 阶段（首轮构造）
# ═══════════════════════════════════════════════════════════════════════

AGENT_PROMPT_REQUEST = Format(
    id="agent.prompt-request",
    name="AgentPromptRequest",
    description=(
        "一次 Agent Loop 开始时提供给 PromptBuilderRouter 的完整构造输入："
        "业务 input_data（由调度器从 Pipeline 传入，如 prefab_name / query / target）"
        "+ node_prompt_template（子类声明的系统 prompt 模板，可含占位符）"
        "+ trace_id（贯穿全 Loop 的 trace）。PromptBuilder 读取后装配出首轮 messages。"
        "脱离 Agent Loop 场景也合理：这是'我要让 LLM 开始处理一个任务'的通用输入合约，"
        "可单独测试 PromptBuilder 的拼装逻辑。调试可见点：input_data 是否带齐 REQUIRED_CONTEXT。"
    ),
    parent="requirement",
    tags=["domain.agent_loop", "phase.prompt_build", "kind.source"],
    json_schema={
        "type": "object",
        "properties": {
            "input_data": {
                "type": "object",
                "description": "业务输入数据。由调度器从 Pipeline 节点传入，结构由子类约定。",
            },
            "node_prompt_template": {
                "type": "string",
                "description": "系统 prompt 模板。含可选 {placeholder} 占位符（由 PromptBuilder 填充）。",
            },
            "trace_id": {
                "type": "string",
                "description": "贯穿全 Agent Loop 的追踪 ID（对齐 Pipeline trace_id）",
            },
        },
        "required": ["input_data", "trace_id"],
    },
)


AGENT_PROMPT_BUILT = Format(
    id="agent.prompt-built",
    name="AgentPromptBuilt",
    description=(
        "PromptBuilderRouter 装配完成的初始 LLM 会话上下文："
        "system_prompt（经过占位符填充的系统指令）+ initial_messages（至少一条 user 消息，"
        "包含业务任务描述）+ 原 trace_id 透传。这是 LLMCallRouter 首轮输入的标准起点，"
        "也可被任何需要'从预备好的 prompt 开始一次 LLM 会话'的场景复用。"
        "调试可见点：initial_messages 是否非空且首条 role=user；system_prompt 是否含未替换占位符。"
    ),
    parent="agent.prompt-request",
    tags=["domain.agent_loop", "phase.prompt_built", "ready_for_llm", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "system_prompt": {"type": "string"},
            "initial_messages": {
                "type": "array",
                "items": {"type": "object"},
                "minItems": 1,
                "description": "Anthropic 格式 messages 列表（role/content blocks）",
            },
            "trace_id": {"type": "string"},
        },
        "required": ["system_prompt", "initial_messages"],
    },
)


# ═══════════════════════════════════════════════════════════════════════
# CONTEXT 阶段（每轮压缩 / 滑窗 / 截断）
# ═══════════════════════════════════════════════════════════════════════

AGENT_CONTEXT_REQUEST = Format(
    id="agent.context-request",
    name="AgentContextRequest",
    description=(
        "每轮 LLM 调用前，调度器向 ContextCompactRouter 提交的上下文整理请求："
        "messages（累积对话历史，含 user/assistant/tool_result blocks）"
        "+ compact_cfg（L1-L4 压缩参数：aging_threshold/max_tool_output/max_messages/auto_compact 等）"
        "+ context_window（模型上下文窗口上限，用于 L4 触发判定）"
        "+ turn 轮次。这是一次'可能需要压缩的对话整理'请求，脱离 Agent Loop 也有意义——"
        "例如离线把一段长对话 replay 压缩出结果。调试可见点：messages 长度、L4 是否触发。"
    ),
    parent="requirement",
    tags=["domain.agent_loop", "phase.context_compact", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "messages": {
                "type": "array",
                "items": {"type": "object"},
                "description": "当前累积的对话历史",
            },
            "compact_cfg": {
                "type": "object",
                "description": "压缩参数（对应 LoopConfig.compact）",
            },
            "context_window": {
                "type": "integer",
                "description": "模型上下文窗口 token 数（L4 比较用）",
            },
            "turn": {"type": "integer", "minimum": 0},
        },
        "required": ["messages", "turn"],
    },
)


AGENT_CONTEXT_COMPACTED = Format(
    id="agent.context-compacted",
    name="AgentContextCompacted",
    description=(
        "ContextCompactRouter 处理后的对话历史。字段：messages（经 L1 工具结果老化 + "
        "L2 单条截断 + L3 滑窗 + 可选 L4 LLM 压缩后的新 messages）+ compact_events "
        "（本轮实际触发的压缩操作清单，如 [{layer:'L1',aged:3},{layer:'L3',trimmed:5}]）。"
        "此 Format 是'进 LLMCall 前最后一次干净的 messages 快照'，可用来验证每层压缩效果。"
        "调试可见点：before/after 消息数差、compact_events 是否如预期触发。"
    ),
    parent="agent.context-request",
    tags=["domain.agent_loop", "phase.context_compacted", "ready_for_llm", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "messages": {"type": "array", "items": {"type": "object"}},
            "compact_events": {
                "type": "array",
                "items": {"type": "object"},
                "description": "本轮触发的压缩动作清单，每项含 layer/action/count/detail",
            },
        },
        "required": ["messages", "compact_events"],
    },
)


# ═══════════════════════════════════════════════════════════════════════
# LLM 阶段（调用 + 响应）
# ═══════════════════════════════════════════════════════════════════════

AGENT_LLM_REQUEST = Format(
    id="agent.llm-request",
    name="AgentLLMRequest",
    description=(
        "LLMCallRouter 的输入：一次完整的 LLM 会话请求。字段：messages（已压缩的对话历史）"
        "+ system_prompt（系统指令）+ tools_spec（Anthropic 格式的工具规范列表）"
        "+ model（模型名，默认 qwen-3.6-plus）+ turn（第几轮，用于审计/调试）。"
        "脱离 Agent Loop 场景仍有意义：这是'LLM 被调用的完整输入合约'，"
        "可被任何 replay / fuzz / benchmark 工具直接消费。"
        "调试可见点：messages 最后一条 role、tools_spec 数量、model 是否合规（禁 opus/sonnet-高档）。"
    ),
    parent="requirement",
    tags=["domain.agent_loop", "phase.llm_call", "llm_input", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "messages": {"type": "array", "items": {"type": "object"}},
            "system_prompt": {"type": "string"},
            "tools_spec": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Anthropic tool_use schemas",
            },
            "model": {"type": "string"},
            "turn": {"type": "integer", "minimum": 0},
        },
        "required": ["messages", "system_prompt"],
    },
)


AGENT_LLM_RESPONSE = Format(
    id="agent.llm-response",
    name="AgentLLMResponse",
    description=(
        "LLMCallRouter 的输出：一次 LLM 响应的完整解析结果。字段："
        "assistant_message（Anthropic 格式的 assistant 消息，含 text 和 tool_use blocks）"
        "+ text（所有 text block 拼接的纯文本）"
        "+ tool_uses（[{tool_name,tool_args,tool_use_id,...}]，已解析为 Python dict）"
        "+ stop_reason（end_turn/tool_use/max_tokens/...）"
        "+ usage（input/output tokens + model）+ turn。"
        "是'一次 LLM 响应的完整审计记录'，可独立 replay、复盘幻觉来源。"
        "调试可见点：stop_reason=max_tokens 时 tool_uses 可能畸形（应丢弃）；"
        "usage 是否记录（没记录说明审计链断了）。"
    ),
    parent="agent.llm-request",
    tags=["domain.agent_loop", "phase.llm_response", "auditable", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "assistant_message": {"type": "object"},
            "text": {"type": "string"},
            "tool_uses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tool_name": {"type": "string"},
                        "tool_args": {"type": "object"},
                        "tool_use_id": {"type": "string"},
                    },
                    "required": ["tool_name", "tool_use_id"],
                },
            },
            "stop_reason": {"type": "string"},
            "usage": {
                "type": "object",
                "properties": {
                    "input_tokens": {"type": "integer"},
                    "output_tokens": {"type": "integer"},
                    "model": {"type": "string"},
                },
            },
            "turn": {"type": "integer", "minimum": 0},
        },
        "required": ["assistant_message", "text", "tool_uses", "turn"],
    },
)


# ═══════════════════════════════════════════════════════════════════════
# TOOL 阶段（调用 + 结果）
# ═══════════════════════════════════════════════════════════════════════

AGENT_TOOL_REQUEST = Format(
    id="agent.tool-request",
    name="AgentToolRequest",
    description=(
        "ToolDispatchRouter 的输入：一次工具调用请求。字段："
        "tool_name（目标工具名，如 glob/grep/read_file/submit_findings）"
        "+ tool_args（Anthropic 返回的参数 dict，已剥离 intent 等非工具字段）"
        "+ tool_use_id（Anthropic 的 tool_use block id，用于后续 tool_result 关联）"
        "+ turn（第几轮）+ context（ToolContext：cwd/origin/trace_id/permission_mode 等）。"
        "脱离 Agent Loop 也有意义：这是'让系统执行一次工具'的通用合约，"
        "可被任何工具 gateway / 权限审计 / replay 工具复用。"
        "调试可见点：tool_name 是否在 dispatch 注册表内、tool_args 是否符合 tool 的 schema。"
    ),
    parent="requirement",
    tags=["domain.agent_loop", "phase.tool_call", "tool_input", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
            "tool_args": {"type": "object"},
            "tool_use_id": {"type": "string"},
            "turn": {"type": "integer", "minimum": 0},
            "context": {
                "type": "object",
                "description": "ToolContext（cwd/origin/permission_mode/trace_id/...）",
            },
        },
        "required": ["tool_name", "tool_args", "tool_use_id"],
    },
)


AGENT_TOOL_RESPONSE = Format(
    id="agent.tool-response",
    name="AgentToolResponse",
    description=(
        "ToolDispatchRouter 的输出：一次工具调用结果。字段："
        "tool_name（回显，便于审计）+ tool_use_id（与 request 配对）"
        "+ result（工具返回文本，可能是 JSON/纯文本/路径列表）"
        "+ is_error（bool，执行异常或权限拒绝时为 true）"
        "+ duration_ms（耗时，用于性能分析）+ turn。"
        "是'一次工具执行的完整审计结果'，独立看也能知道哪个工具干了什么。"
        "调试可见点：result 被截断时应明确标注；is_error=true 时 result 应是诊断文本。"
    ),
    parent="agent.tool-request",
    tags=["domain.agent_loop", "phase.tool_result", "auditable", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
            "tool_use_id": {"type": "string"},
            "result": {"type": "string"},
            "is_error": {"type": "boolean"},
            "duration_ms": {"type": "number"},
            "turn": {"type": "integer", "minimum": 0},
        },
        "required": ["tool_name", "tool_use_id", "result", "is_error"],
    },
)


# ═══════════════════════════════════════════════════════════════════════
# RESULT 阶段（LLM 无 tool_uses 时收尾）
# ═══════════════════════════════════════════════════════════════════════

AGENT_RESULT_REQUEST = Format(
    id="agent.result-request",
    name="AgentResultRequest",
    description=(
        "LLM 返回纯文本（无 tool_uses）或调用 finish 时，调度器向 ExtractResultRouter "
        "提交的结果整理请求。字段：messages（完整历史，用于子类提取所需证据）"
        "+ final_text（最后一次 assistant 的纯文本）+ turn_count（实际跑了几轮）"
        "+ stop_reason（end_turn/finish/max_turns/...）+ trace_id。"
        "这是'一次 Agent Loop 已完成工作，请把对话转成业务产物'的通用合约，"
        "业务子类可按自己约定的产物格式提取（如 findings.md / selection json / ...）。"
        "调试可见点：final_text 是否空、turn_count 是否等于 max_turns（预算耗尽）。"
    ),
    parent="requirement",
    tags=["domain.agent_loop", "phase.result_extract", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "messages": {"type": "array", "items": {"type": "object"}},
            "final_text": {"type": "string"},
            "turn_count": {"type": "integer", "minimum": 0},
            "stop_reason": {"type": "string"},
            "trace_id": {"type": "string"},
        },
        "required": ["messages", "final_text", "turn_count"],
    },
)


AGENT_RESULT_FINAL = Format(
    id="agent.result-final",
    name="AgentResultFinal",
    description=(
        "ExtractResultRouter 的输出：Agent Loop 的最终 Verdict 载荷。字段："
        "verdict_kind（PASS/FAIL/PARTIAL，对应 Verdict.kind.value）"
        "+ output（业务产物主体：字符串/dict/列表；由子类 extract_result 决定）"
        "+ diagnosis（失败或部分完成时的原因描述）+ trace_id。"
        "这是'一次 Agent Loop 交付出的最终结论'，供 Pipeline 上层或人类直接消费。"
        "调试可见点：PARTIAL 时 diagnosis 必须明确，不能空串。"
    ),
    parent="agent.result-request",
    tags=["domain.agent_loop", "phase.result_final", "loop_exit", "kind.sink"],
    json_schema={
        "type": "object",
        "properties": {
            "verdict_kind": {
                "type": "string",
                "enum": ["PASS", "FAIL", "PARTIAL"],
            },
            "output": {"description": "业务产物，结构由子类约定"},
            "diagnosis": {"type": "string"},
            "trace_id": {"type": "string"},
        },
        "required": ["verdict_kind"],
    },
)


# ═══════════════════════════════════════════════════════════════════════
# 注册入口
# ═══════════════════════════════════════════════════════════════════════

ALL_AGENT_FORMATS = [
    AGENT_PROMPT_REQUEST,
    AGENT_PROMPT_BUILT,
    AGENT_CONTEXT_REQUEST,
    AGENT_CONTEXT_COMPACTED,
    AGENT_LLM_REQUEST,
    AGENT_LLM_RESPONSE,
    AGENT_TOOL_REQUEST,
    AGENT_TOOL_RESPONSE,
    AGENT_RESULT_REQUEST,
    AGENT_RESULT_FINAL,
]


def register_formats(registry: FormatRegistry) -> None:
    """把 services.agent 的 10 个 Format 注册到全局 FormatRegistry。

    依赖内置 Format `requirement`（BUILTIN_FORMATS 提供）。
    可被 cli/unified.py 的 _try_load_format_registry 自动发现。
    """
    for fmt in ALL_AGENT_FORMATS:
        if not registry.is_registered(fmt.id):
            registry.register(fmt)
