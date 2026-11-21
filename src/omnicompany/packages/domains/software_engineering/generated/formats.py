# [OMNI] origin=claude-code domain=software_engineering/generated ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.generated.text_stats_formats.definitions.py"
from omnicompany.protocol.format import Format, FormatRegistry

# Format: sw.text-input
TEXT_INPUT = Format(
    id="sw.text-input",
    name="文本统计输入意图",
    description="用户提交的包含待统计文本的意图对象，结构为 {\"text\": string}。验证标准为 JSON 结构合法且包含 text 字段。下游将作为验证节点的输入源。",
    parent="requirement",
    tags=["sw"],
    json_schema={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "待统计的文本内容"
            }
        },
        "required": ["text"]
    },
    semantic_preconditions=[],
    granted_tags_on_pass=["INPUT_RECEIVED"]
)

# Format: sw.input-check-result
INPUT_CHECK_RESULT = Format(
    id="sw.input-check-result",
    name="输入验证结果",
    description="验证节点对输入文本的非空与合法性检查结果。验证标准为：若输入为空字符串或 null 必须标记为 FAIL，非空标记为 PASS。下游用于控制统计节点是否执行或直接返回错误。",
    parent="requirement",
    tags=["sw"],
    json_schema={
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["PASS", "FAIL"],
                "description": "验证状态，空输入必须为 FAIL"
            },
            "text": {
                "type": "string",
                "description": "验证通过后的有效文本（仅在 PASS 时存在）"
            },
            "reason": {
                "type": "string",
                "description": "失败原因（仅在 FAIL 时存在）"
            }
        },
        "required": ["status"]
    },
    semantic_preconditions=["sw.text-input"],
    granted_tags_on_pass=["VALID_INPUT"]
)

# Format: sw.stats-metrics
STATS_METRICS = Format(
    id="sw.stats-metrics",
    name="文本统计指标",
    description="对通过验证的文本执行的确定性统计结果，包含字数、行数、字符数。验证标准为数值非负且符合统计逻辑（如 'Hello world' 对应 word_count=2）。下游作为最终输出返回给用户。",
    parent="requirement",
    tags=["sw"],
    json_schema={
        "type": "object",
        "properties": {
            "word_count": {
                "type": "integer",
                "minimum": 0
            },
            "line_count": {
                "type": "integer",
                "minimum": 0
            },
            "char_count": {
                "type": "integer",
                "minimum": 0
            }
        },
        "required": ["word_count", "line_count", "char_count"]
    },
    semantic_preconditions=["sw.input-check-result"],
    granted_tags_on_pass=["STATS_COMPLETE"]
)


FORMATS = [TEXT_INPUT, INPUT_CHECK_RESULT, STATS_METRICS]


def register_formats(registry: FormatRegistry) -> None:
    """注册所有格式到 FormatRegistry（dispatch 约定签名: register_fn(registry)）"""
    for fmt in FORMATS:
        if not registry.is_registered(fmt.id):
            registry.register(fmt)