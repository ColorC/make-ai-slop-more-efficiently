# [OMNI] origin=human ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:protocol.package_exports.aggregator.py"
# LAP — Language Anchoring Protocol
#
# 事件层
from omnicompany.protocol.events import EventMetadata, FactoryEvent
from omnicompany.protocol.registry import EventType

# 语义类型层
from omnicompany.protocol.format import (
    ConnectionCheck,
    Format,
    FormatRegistry,
    create_builtin_registry,
)

# 锚定原语层
from omnicompany.protocol.anchor import (
    AnchorSpec,
    Route,
    RouteAction,
    Transformer,
    TransformerSpec,
    TransformMethod,
    Validator,
    ValidatorKind,
    ValidatorSpec,
    Verdict,
    VerdictKind,
)

# Team 组合层 (2026-04-21 正名: Pipeline → Team)
from omnicompany.protocol.team import (
    STRICT_AUDIT_DEFAULT_RUNS,
    EdgeCheckResult,
    InfoAuditMode,
    NodeKind,
    describe_agent_loop,
    # 过渡期别名 — 旧消费者可继续用，新代码用上面的 Team* 名
    TeamChecker,
    TeamCheckResult,
    TeamEdge,
    TeamExecutionMode,
    TeamNode,
    TeamSpec,
)

# 信息审计层 (2026-04-09)
from omnicompany.protocol.info_audit import (
    INFO_AUDIT_PROMPT_APPENDIX,
    InfoAuditReport,
    MissingInfoItem,
    Sufficiency,
)

# V0.3：状态锚点层
from omnicompany.protocol.state import (
    StateAnchor,
    StateKind,
    StateSnapshot,
)

# 六元原语层 (2026-05-01 从 omnicompany.primitives 合并过来)
# Hook = 感知 (只观测) / Tool = 执行 (改外部状态) / Node = 处理 (含 LLM 决策)
# Signal = 运行时数据流 / Intent = 任务请求流
from omnicompany.protocol.hook import BaseHook, PeriodicHook, EventHook
from omnicompany.protocol.node import BaseNode, ConsciousnessNode
from omnicompany.protocol.signal import Signal
from omnicompany.protocol.intent import Intent
from omnicompany.protocol.tool import BaseTool, AsyncBaseTool, DBWriteTool, PainSignalWriteTool

__all__ = [
    # 事件
    "FactoryEvent",
    "EventMetadata",
    "EventType",
    # 语义类型
    "Format",
    "FormatRegistry",
    "ConnectionCheck",
    "create_builtin_registry",
    # 锚定原语
    "Verdict",
    "VerdictKind",
    "Route",
    "RouteAction",
    "ValidatorSpec",
    "ValidatorKind",
    "Validator",
    "AnchorSpec",
    "TransformerSpec",
    "TransformMethod",
    "Transformer",
    # Team 组合 (正名)
    "TeamSpec",
    "TeamNode",
    "TeamEdge",
    "NodeKind",
    "TeamChecker",
    "TeamCheckResult",
    "EdgeCheckResult",
    "describe_agent_loop",
    "TeamExecutionMode",
    "InfoAuditMode",
    "STRICT_AUDIT_DEFAULT_RUNS",
    # 过渡期别名 (旧名，后续删除)
    "TeamSpec",
    "TeamNode",
    "TeamEdge",
    "TeamChecker",
    "TeamCheckResult",
    "TeamExecutionMode",
    # 信息审计
    "InfoAuditReport",
    "MissingInfoItem",
    "Sufficiency",
    "INFO_AUDIT_PROMPT_APPENDIX",
    # V0.3 状态锚点
    "StateAnchor",
    "StateKind",
    "StateSnapshot",
    # 六元原语 (从 primitives 合并)
    "BaseHook", "PeriodicHook", "EventHook",
    "BaseNode", "ConsciousnessNode",
    "BaseTool", "AsyncBaseTool", "DBWriteTool", "PainSignalWriteTool",
    "Signal", "Intent",
]
