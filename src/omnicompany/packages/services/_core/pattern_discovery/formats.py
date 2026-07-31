# [OMNI] origin=claude-code domain=pattern_discovery/formats.py ts=2026-04-08T03:23:37Z
# [OMNI] material_id="material:core.pattern_discovery.format_schema.registry.py"
"""pattern_discovery formats — 模式发现的 4 个语义类型

Format 链：
  pd.trigger → pd.activities → pd.candidates → pd.done
"""

from omnicompany.protocol.format import Format, FormatRegistry

PD_TRIGGER = Format(
    id="pd.trigger",
    name="模式发现触发",
    description=(
        "模式发现的触发信号，包含数据库路径和配置参数。"
        "验证标准：db_path 非空。"
        "下游用途：summary_reader 从 compression_summaries 表读取未处理摘要。"
    ),
    parent="requirement",
    json_schema={
        "type": "object",
        "properties": {
            "db_path": {"type": "string"},
            "min_cluster_size": {"type": "integer", "minimum": 2},
            "similarity_threshold": {"type": "number"},
        },
        "required": ["db_path"],
    },
    tags=["pd", "domain.pattern_discovery", "stage.input", "kind.source"],
)

PD_ACTIVITIES = Format(
    id="pd.activities",
    name="展平的 Activity 列表",
    description=(
        "从 compression_summaries 中提取并展平的所有 activity 记录。"
        "每条包含 purpose、behavior、tools_used、domain、来源 session_id。"
        "验证标准：activities 非空列表。"
        "下游用途：pattern_clusterer 对 purpose 做语义聚类。"
    ),
    parent="requirement",
    json_schema={
        "type": "object",
        "properties": {
            "activities": {
                "type": "array",
                "items": {"type": "object"},
                "minItems": 1,
            },
            "summary_ids": {"type": "array", "items": {"type": "integer"}},
            "db_path": {"type": "string"},
            "min_cluster_size": {"type": "integer"},
            "similarity_threshold": {"type": "number"},
        },
        "required": ["activities", "summary_ids"],
    },
    tags=["pd", "domain.pattern_discovery", "stage.extraction", "kind.internal"],
)

PD_CANDIDATES = Format(
    id="pd.candidates",
    name="候选重复模式",
    description=(
        "语义聚类后发现的候选重复模式列表。每个候选包含聚类内的 "
        "activity purpose 摘要、出现次数、关联的 session_id/trace_id。"
        "验证标准：candidates 非空，每个候选出现次数 >= min_cluster_size。"
        "下游用途：induction_dispatcher 对每个候选调用 trace-induction。"
    ),
    parent="requirement",
    json_schema={
        "type": "object",
        "properties": {
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "purpose_summary": {"type": "string"},
                        "count": {"type": "integer"},
                        "activities": {"type": "array"},
                        "trace_ids": {"type": "array", "items": {"type": "string"}},
                        "domain": {"type": "string"},
                    },
                    "required": ["purpose_summary", "count"],
                },
            },
            "db_path": {"type": "string"},
        },
        "required": ["candidates"],
    },
    tags=["pd", "domain.pattern_discovery", "stage.analysis", "kind.internal"],
)

PD_DONE = Format(
    id="pd.done",
    name="模式发现完成",
    description=(
        "模式发现流程的结果，包含处理了多少候选、成功归纳了多少 pipeline。"
        "验证标准：processed >= 0。"
        "下游用途：终端输出，无后续节点。"
    ),
    parent="requirement",
    json_schema={
        "type": "object",
        "properties": {
            "processed": {"type": "integer"},
            "induced": {"type": "integer"},
            "details": {"type": "array"},
        },
        "required": ["processed", "induced"],
    },
    tags=["pd", "domain.pattern_discovery", "stage.output", "kind.sink"],
)

ALL_FORMATS = [PD_TRIGGER, PD_ACTIVITIES, PD_CANDIDATES, PD_DONE]


def register_formats(registry: FormatRegistry) -> None:
    for fmt in ALL_FORMATS:
        if not registry.is_registered(fmt.id):
            try:
                registry.register(fmt)
            except Exception:
                pass
