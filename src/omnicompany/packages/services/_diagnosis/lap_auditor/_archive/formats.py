# [OMNI] origin=claude-code domain=omnifactory/lap_auditor ts=2026-04-21T00:00:00Z type=config
# [OMNI] material_id="material:diagnosis.lap_auditor.material_definitions.python"
"""lap_auditor.formats — LAP 合规审计 Material 定义 (Clean Migration 2026-04-21).

Material kind 标注 (F-19):
  lap_auditor.input   → kind.source   (外部触发, 无 producer Worker)
  lap_auditor.context → kind.internal (由 ContextGetterWorker 产出, SpecAuditorWorker 消费)
  lap_auditor.report  → kind.internal (由 SpecAuditorWorker 产出, ReportFormatterWorker 消费)
  lap_auditor.done    → kind.sink     (最终报告输出, 无 consumer Worker)
"""

from omnifactory.packages.services._core.omnicompany import Material
from omnifactory.protocol.format import FormatRegistry

LAP_AUDITOR_INPUT = Material(
    id="lap_auditor.input",
    name="LAP Audit Request",
    description=(
        "触发 LAP 合规审计管线的入口请求。"
        "指定 target_path（单个 .py 文件或目录），审计器将递归读取所有 Python 源码，"
        "通过 LLM 按四大红线（事件总线/Format真实性/接口规范/Domain隔离）分类评估。"
        "Kind: source（外部触发，无 producer Worker，见 F-19）。"
    ),
    parent="requirement",
    tags=["lap_auditor", "input", "kind.source"],
    examples=[
        {"target_path": "src/omnifactory/packages/services/repair/routers.py"},
        {"target_path": "src/omnifactory/packages/services/doctor/"},
    ],
)

LAP_AUDITOR_CONTEXT = Material(
    id="lap_auditor.context",
    name="LAP Audit Code Context",
    description=(
        "ContextGetterWorker 从 target_path 读取所有 .py 文件后拼装的代码上下文字符串。"
        "包含 target_path（原始路径）和 code_context（多文件拼接的代码文本，"
        "每个文件前有 `--- File: <name> ---` 标题分隔）。"
        "供 SpecAuditorWorker 直接送入 LLM 分析。"
        "Kind: internal（ContextGetterWorker 产出，SpecAuditorWorker 消费，见 F-19）。"
    ),
    parent="requirement",
    tags=["lap_auditor", "context", "kind.internal"],
    examples=[
        {
            "target_path": "src/omnifactory/packages/services/repair/routers.py",
            "code_context": "--- File: routers.py ---\n```python\n...\n```\n",
        }
    ],
)

LAP_AUDITOR_REPORT = Material(
    id="lap_auditor.report",
    name="LAP Audit LLM Report",
    description=(
        "SpecAuditorWorker 调用 LLM 后产出的原始 Markdown 审计报告。"
        "包含 report（Markdown 文本，含整体分类结论/红线剖析/演进建议三节）"
        "和 target_path（透传自上游）。"
        "供 ReportFormatterWorker 格式化打印后存档。"
        "Kind: internal（SpecAuditorWorker 产出，ReportFormatterWorker 消费，见 F-19）。"
    ),
    parent="requirement",
    tags=["lap_auditor", "report", "kind.internal"],
    examples=[
        {
            "target_path": "src/omnifactory/packages/services/repair/routers.py",
            "report": "## 整体分类结论\n规范的 LAP 管线实现...",
        }
    ],
)

LAP_AUDITOR_DONE = Material(
    id="lap_auditor.done",
    name="LAP Audit Done",
    description=(
        "ReportFormatterWorker 格式化打印后产出的最终结果。"
        "包含 summary（固定字符串 'Audit complete'）和 report（同上游 Markdown 报告）。"
        "此 Material 为管线终点，无下游 Worker 消费。"
        "Kind: sink（ReportFormatterWorker 产出，无 consumer，见 F-19）。"
    ),
    parent="requirement",
    tags=["lap_auditor", "output", "kind.sink"],
    examples=[
        {"summary": "Audit complete", "report": "## 整体分类结论\n..."}
    ],
)


ALL_FORMATS = [
    LAP_AUDITOR_INPUT,
    LAP_AUDITOR_CONTEXT,
    LAP_AUDITOR_REPORT,
    LAP_AUDITOR_DONE,
]


def register_formats(registry: FormatRegistry) -> None:
    for fmt in ALL_FORMATS:
        if not registry.is_registered(fmt.id):
            try:
                registry.register(fmt)
            except ValueError:
                pass


__all__ = [
    "LAP_AUDITOR_INPUT",
    "LAP_AUDITOR_CONTEXT",
    "LAP_AUDITOR_REPORT",
    "LAP_AUDITOR_DONE",
    "ALL_FORMATS",
    "register_formats",
]
