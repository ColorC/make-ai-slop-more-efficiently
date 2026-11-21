# [OMNI] origin=claude-code domain=trace_induction/formats.py ts=2026-04-08T03:23:37Z
# [OMNI] material_id="material:learning.trace_induction.format_definitions.registry.py"
"""trace_induction formats — 轨迹归纳的 7 个语义类型

Format 链（7 节点对应 7 个 Format 转换）：
  intent → ti.task → ti.trace-data → ti.essential → ti.sop
         → ti.requirement → ti.wf-result → ti.done
"""

from omnicompany.protocol.format import Format, FormatRegistry

TI_TASK = Format(
    id="ti.task",
    name="轨迹归纳任务",
    description=(
        "用户发起的轨迹归纳请求，包含目的描述、历史 trace ID 列表和数据库路径。"
        "验证标准：purpose 非空，trace_ids 至少 1 个。"
        "下游用途：trace_reader 据此从 intent_steps 表读取原始步骤数据。"
    ),
    parent="requirement",
    json_schema={
        "type": "object",
        "properties": {
            "purpose": {"type": "string"},
            "trace_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "domain": {"type": "string"},
            "db_path": {"type": "string"},
            "source": {"type": "string", "enum": ["auto", "intent", "external"]},
            "provider": {"type": "string", "enum": ["", "codex", "claude", "kimi"]},
            "sync": {"type": "boolean"},
            "external_index_path": {"type": "string"},
            "omni_event_db_paths": {"type": "array", "items": {"type": "string"}},
            "external_roots": {
                "type": "object",
                "properties": {
                    "codex_home": {"type": "string"},
                    "claude_home": {"type": "string"},
                    "kimi_home": {"type": "string"},
                },
            },
        },
        "required": ["purpose", "trace_ids"],
    },
    tags=["domain.trace_induction", "stage.input", "kind.source"],
)

TI_TRACE_DATA = Format(
    id="ti.trace-data",
    name="原始 Trace 步骤数据",
    description=(
        "从 intent_steps 表读取的原始操作步骤记录，按 trace_id 分组。"
        "每步包含 tool_name、desc、rationale、tool_args_summary、tool_result、"
        "tool_exit_ok、action_class 等字段。"
        "验证标准：traces 非空 dict，至少一个 trace_id 有步骤数据。"
        "下游用途：noise_filter 对每步标注 ESSENTIAL/EXPLORATION/MISTAKE/RETRY。"
    ),
    parent="requirement",
    json_schema={
        "type": "object",
        "properties": {
            "traces": {
                "type": "object",
                "description": "trace_id → list[step_dict]，按 step_num 排序",
            },
            "purpose": {"type": "string"},
            "trace_count": {"type": "integer", "minimum": 1},
            "domain": {"type": "string"},
            "db_path": {"type": "string"},
            "source": {"type": "string"},
            "provider": {"type": "string"},
            "external_index_path": {"type": "string"},
            "external_sync": {"type": ["object", "null"]},
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["traces", "purpose", "trace_count"],
    },
    tags=["domain.trace_induction", "stage.extraction", "kind.internal"],
)

TI_ESSENTIAL = Format(
    id="ti.essential",
    name="过滤后核心步骤",
    description=(
        "经 LLM 噪音过滤后保留的核心操作步骤（ESSENTIAL 标注）。"
        "已移除探索尝试（EXPLORATION）、错误路径（MISTAKE）、失败重试（RETRY）。"
        "验证标准：essential_steps 非空列表。"
        "下游用途：sop_generator 合并为结构化 SOP。"
    ),
    parent="requirement",
    json_schema={
        "type": "object",
        "properties": {
            "essential_steps": {
                "type": "array",
                "items": {"type": "object"},
                "minItems": 1,
            },
            "purpose": {"type": "string"},
            "trace_count": {"type": "integer"},
            "domain": {"type": "string"},
            "db_path": {"type": "string"},
        },
        "required": ["essential_steps", "purpose"],
    },
    tags=["domain.trace_induction", "stage.extraction", "kind.internal"],
)

TI_SOP = Format(
    id="ti.sop",
    name="结构化 SOP",
    description=(
        "从核心步骤提炼的结构化操作规范，包含 purpose、preconditions、"
        "steps（id/action/tool/input/output/notes/rules）、error_handling。"
        "验证标准：steps 非空，每步有 action 和 tool 字段。"
        "下游用途：req_writer 转化为 Workflow Factory 可消费的需求文档。"
    ),
    parent="requirement",
    json_schema={
        "type": "object",
        "properties": {
            "sop": {
                "type": "object",
                "properties": {
                    "purpose": {"type": "string"},
                    "preconditions": {"type": "array", "items": {"type": "string"}},
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "integer"},
                                "action": {"type": "string"},
                                "tool": {"type": "string"},
                            },
                            "required": ["action", "tool"],
                        },
                        "minItems": 1,
                    },
                    "error_handling": {"type": "array"},
                },
                "required": ["purpose", "steps"],
            },
            "derived_from": {"type": "array", "items": {"type": "string"}},
            "extraction_method": {"type": "string"},
            "domain": {"type": "string"},
            "db_path": {"type": "string"},
        },
        "required": ["sop", "derived_from"],
    },
    tags=["domain.trace_induction", "stage.extraction", "kind.internal"],
)

TI_REQUIREMENT = Format(
    id="ti.requirement",
    name="需求文档",
    description=(
        "可供人工审阅和后续真实任务消费的 Markdown 需求候选，"
        "包含目标、触发场景、操作流程、数据流、错误处理、验证标准、约束。"
        "验证标准：非空 Markdown 字符串，长度 >= 100 字符。"
        "下游用途：作为本次按需归纳的终端产物；不会自动生成或注册工作流。"
    ),
    parent="requirement",
    json_schema={
        "type": "object",
        "properties": {
            "requirement_doc": {"type": "string", "minLength": 100},
            "purpose": {"type": "string"},
            "domain": {"type": "string"},
            "trace_ids": {"type": "array", "items": {"type": "string"}},
            "db_path": {"type": "string"},
        },
        "required": ["requirement_doc", "purpose"],
    },
    tags=["domain.trace_induction", "stage.design", "kind.sink"],
)

TI_WF_RESULT = Format(
    id="ti.wf-result",
    name="WF 产出",
    description=(
        "Workflow Factory 产出的完整 pipeline 代码包，包含 files 字典"
        "（文件名→内容）、pipeline_name、package_path。"
        "验证标准：files 至少包含 formats.py/routers.py/pipeline.py/run.py。"
        "下游用途：registrar 将产出注册到 pipeline_index。"
    ),
    parent="requirement",
    json_schema={
        "type": "object",
        "properties": {
            "pipeline_name": {"type": "string"},
            "package_path": {"type": "string"},
            "files": {"type": "object"},
            "purpose": {"type": "string"},
            "domain": {"type": "string"},
            "db_path": {"type": "string"},
        },
        "required": ["pipeline_name", "package_path", "files"],
    },
    tags=["domain.trace_induction", "stage.generation", "kind.internal"],
)

TI_DONE = Format(
    id="ti.done",
    name="归纳完成",
    description=(
        "轨迹归纳流程的最终产物，包含注册结果和全流程摘要。"
        "验证标准：pipeline_name 非空，status 为 registered。"
        "下游用途：终端输出，无后续节点。"
    ),
    parent="requirement",
    json_schema={
        "type": "object",
        "properties": {
            "pipeline_name": {"type": "string"},
            "package_path": {"type": "string"},
            "registered": {"type": "boolean"},
            "status": {"type": "string"},
            "summary": {"type": "string"},
        },
        "required": ["pipeline_name", "status"],
    },
    tags=["domain.trace_induction", "stage.output", "kind.sink"],
)

ALL_FORMATS = [
    TI_TASK, TI_TRACE_DATA, TI_ESSENTIAL, TI_SOP,
    TI_REQUIREMENT,
]


def register_formats(registry: FormatRegistry) -> None:
    """注册所有 trace_induction Format。"""
    for fmt in ALL_FORMATS:
        if not registry.is_registered(fmt.id):
            try:
                registry.register(fmt)
            except Exception:
                pass
