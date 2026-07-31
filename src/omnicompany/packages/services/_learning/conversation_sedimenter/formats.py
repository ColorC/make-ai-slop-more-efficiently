# [OMNI] origin=claude-code domain=services/_learning ts=2026-06-23T00:00:00Z type=format
# [OMNI] material_id="material:services._learning.conversation_sedimenter.formats.py"
"""conversation-operation-sedimenter 的 Material 契约（source/internal×2/sink, 各带 kind.* tag）。"""
from omnicompany.packages.services._core.omnicompany import Material

# --- convop.request (kind=source) -------------------------------------------
REQUEST = Material(
    id="convop.request",
    name="convop.request",
    description=(
        "对话沉淀请求(source)。要从哪段 claude-code/codex 对话提取常见操作。"
        "transcript_path=.jsonl 绝对路径(优先); session_id=会话 uuid(在 ~/.claude/projects 下找); "
        "source=claude-code|codex; min_freq=操作最小出现次数阈值(默认1)。被 ConversationTraceReaderWorker 消费。"
    ),
    json_schema={
        "type": "object",
        "properties": {
            "transcript_path": {"type": "string"},
            "session_id": {"type": "string"},
            "source": {"type": "string", "enum": ["claude-code", "codex"]},
            "min_freq": {"type": "integer"},
        },
    },
    tags=["domain.agent_framework", "team.conversation_sedimenter", "kind.source"],
)

# --- convop.trace (kind=internal) -------------------------------------------
TRACE = Material(
    id="convop.trace",
    name="convop.trace",
    description=(
        "紧凑动作轨迹(internal)。ConversationTraceReaderWorker 纯 Python 流式解析 .jsonl, "
        "把上 MB 对话压成: events[]({turn,tool,target,brief})、tool_histogram(工具→次数)、"
        "n_events/n_lines_scanned、meta(path/source)。供 gpt-5.5 Miner 便宜地聚类。"
    ),
    json_schema={
        "type": "object",
        "properties": {
            "events": {"type": "array", "items": {"type": "object"}},
            "tool_histogram": {"type": "object"},
            "n_events": {"type": "integer"},
            "n_lines_scanned": {"type": "integer"},
            "meta": {"type": "object"},
        },
        "required": ["events", "tool_histogram", "n_events"],
    },
    tags=["domain.agent_framework", "team.conversation_sedimenter", "kind.internal"],
)

# --- convop.operations (kind=internal) --------------------------------------
OPERATIONS = Material(
    id="convop.operations",
    name="convop.operations",
    description=(
        "常见操作清单(internal)。ConversationOperationMinerWorker 的 gpt-5.5 tool-agent 从轨迹"
        "聚类出反复出现的操作: 每条 {name(操作名), trigger(触发条件), steps[](有序步骤), "
        "frequency(出现次数估计), evidence[](指向真实轨迹的佐证)} + summary。被 Proposer 消费。"
    ),
    json_schema={
        "type": "object",
        "properties": {
            "operations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "trigger": {"type": "string"},
                        "steps": {"type": "array", "items": {"type": "string"}},
                        "frequency": {"type": "integer"},
                        "evidence": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["name", "steps"],
                },
            },
            "summary": {"type": "string"},
        },
        "required": ["operations"],
    },
    tags=["domain.agent_framework", "team.conversation_sedimenter", "kind.internal"],
)

# --- convop.team_skeleton (kind=sink) ---------------------------------------
TEAM_SKELETON = Material(
    id="convop.team_skeleton",
    name="convop.team_skeleton",
    description=(
        "可沉淀 team 骨架(sink)。TeamSkeletonProposerWorker 取频次最高的操作, 确定性装成 team 骨架: "
        "candidate_op、proposed_team({name,materials[],workers[],entry,topology})、draft_path(草稿落盘位置)、"
        "validation(doctor-lite 自检结果)。这是'下一个待沉淀 team'的候选, 供人/team-builder 接力硬化, 不自动注册。"
    ),
    json_schema={
        "type": "object",
        "properties": {
            "candidate_op": {"type": "string"},
            "proposed_team": {"type": "object"},
            "draft_path": {"type": "string"},
            "validation": {"type": "object"},
        },
        "required": ["proposed_team", "validation"],
    },
    tags=["domain.agent_framework", "team.conversation_sedimenter", "kind.sink"],
)

ALL_FORMATS = [REQUEST, TRACE, OPERATIONS, TEAM_SKELETON]
