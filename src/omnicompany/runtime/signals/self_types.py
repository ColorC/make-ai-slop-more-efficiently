# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:44Z
# [OMNI] material_id="material:runtime.signals.system_format_registry.definitions.py"
"""系统域 Format 注册 — 本体同构论核心

将 RouteGraph 结构、算子代码、AST、自我认知文档等
系统内部产物注册为一等公民 Format，实现"代码即数据"的同构。

系统域 id 约定:
  - omnicompany.* — 引擎自身结构/状态
  - trace.*       — 运行时 trace / 信号日志
业务域保持原有 id（requirement / code / …）。
"""

from __future__ import annotations

from omnicompany.protocol.format import Format, FormatRegistry


SYSTEM_FORMATS: list[Format] = [
    Format(
        id="omnicompany.json.route_graph_dump",
        name="RouteGraphDump",
        description="系统语义路由图的完整 JSON 序列化快照",
        tags=["domain.omnicompany", "content.graph", "lifecycle.snapshot"],
    ),
    Format(
        id="omnicompany.python.module_ast",
        name="ModuleAST",
        description="Python 模块的 AST 结构化表示",
        tags=["domain.omnicompany", "content.code", "format.ast"],
    ),
    Format(
        id="omnicompany.python.operator_code",
        name="OperatorCode",
        description="算子实现的完整 Python 源代码",
        tags=["domain.omnicompany", "content.code", "format.source"],
    ),
    Format(
        id="omnicompany.markdown.self_concept",
        name="SelfConcept",
        description="系统自我认知字典——描述自身架构、能力、限制的结构化文档",
        tags=["domain.omnicompany", "content.meta", "lifecycle.live"],
    ),
    Format(
        id="trace.log.pipeline_failure",
        name="PipelineFailureLog",
        description="管线执行失败的完整 trace 日志，含步骤序列、错误信息、耗时",
        tags=["domain.trace", "content.log", "signal.pain"],
    ),
    Format(
        id="trace.log.pain_event",
        name="PainEventLog",
        description="痛觉事件记录，含强度、不可恢复性、传播深度、触发原因",
        tags=["domain.trace", "content.signal", "signal.pain"],
    ),
    Format(
        id="omnicompany.json.operator_registry",
        name="OperatorRegistry",
        description="所有活跃算子的注册表快照，含类型签名、痛觉分数、成功率",
        tags=["domain.omnicompany", "content.registry"],
    ),
    Format(
        id="omnicompany.json.pipeline_spec_dump",
        name="TeamSpecDump",
        description="管线规约的 JSON 序列化，可被进化算子直接消费和修改",
        tags=["domain.omnicompany", "content.spec", "format.json"],
    ),
    # 测试分析相关类型
    Format(
        id="bash.stdout.test_usage",
        name="TestUsageOutput",
        description="测试执行后的标准输出，包含测试覆盖率统计信息。",
        tags=["domain.test", "content.output", "format.stdout"],
    ),
    Format(
        id="analysis.test.metrics",
        name="TestMetricsReport",
        description="结构化的测试质量报告，包含覆盖率统计、质量评估指标。",
        tags=["domain.analysis", "content.metrics", "format.json"],
    ),
]


def register_system_formats(registry: FormatRegistry) -> None:
    """将系统域 Format 注册到全局 FormatRegistry。

    系统域 Format 无 parent（根类型），与业务域 Format 平等共存。
    """
    for fmt in SYSTEM_FORMATS:
        if not registry.is_registered(fmt.id):
            registry.register(fmt)
