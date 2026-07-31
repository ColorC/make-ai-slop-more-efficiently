# [OMNI] origin=claude-code domain=omnifactory/cleanup_bot ts=2026-04-21T00:00:00Z type=config
# [OMNI] material_id="material:diagnosis.cleanup_bot.material_definitions.formats.py"
"""cleanup_bot.formats — 系统环境清理 Material 定义 (Clean Migration 2026-04-21).

Material kind 标注 (F-19):
  cleanup.input    → kind.source   (外部触发, 无 producer Worker)
  cleanup.evidence → kind.internal (由 EvidenceGathererWorker 产出, AnomalyDetectorWorker 消费)
  cleanup.plan     → kind.internal (由 AnomalyDetectorWorker 产出, RollbackPlannerWorker 消费)
  cleanup.done     → kind.sink     (最终清理计划输出, 无 consumer Worker)
"""

from omnifactory.packages.services._core.omnicompany import Material
from omnifactory.protocol.format import FormatRegistry

CLEANUP_INPUT = Material(
    id="cleanup.input",
    name="Cleanup Scan Request",
    description=(
        "触发系统环境异常清理管线的入口请求。"
        "指定 root_dir（扫描起点，默认 E:\\）和 keyword（路径关键词，如 'WindowsWorkspace'），"
        "EvidenceGatherer 将递归（最大 5 层）收集包含该关键词的所有路径，"
        "供 LLM 判断哪些是 AI 误触产生的错位垃圾。"
        "Kind: source（外部触发，无 producer Worker，见 F-19）。"
    ),
    parent="requirement",
    tags=["cleanup_bot", "input", "kind.source"],
    examples=[
        {"root_dir": "E:\\", "keyword": "WindowsWorkspace"},
        {"root_dir": "C:\\Users\\user", "keyword": "omnifactory"},
    ],
)

CLEANUP_EVIDENCE = Material(
    id="cleanup.evidence",
    name="Cleanup Evidence List",
    description=(
        "EvidenceGathererWorker 扫描磁盘后收集的可疑路径清单。"
        "包含 keyword（原始关键词）、evidence_str（所有可疑路径换行拼接的字符串）、"
        "raw_paths（路径列表）。"
        "供 AnomalyDetectorWorker 送入 LLM 判断合法性。"
        "Kind: internal（EvidenceGathererWorker 产出，AnomalyDetectorWorker 消费，见 F-19）。"
    ),
    parent="requirement",
    tags=["cleanup_bot", "evidence", "kind.internal"],
    examples=[
        {
            "keyword": "WindowsWorkspace",
            "evidence_str": "E:\\e\\WindowsWorkspace\nC:/workspace/",
            "raw_paths": ["E:\\e\\WindowsWorkspace", "C:/workspace/"],
        }
    ],
)

CLEANUP_PLAN = Material(
    id="cleanup.plan",
    name="Cleanup Anomaly Plan",
    description=(
        "AnomalyDetectorWorker 调用 LLM 后产出的 Markdown 清理计划。"
        "包含 anomaly_report（Markdown 文本，含异常判定结论/保留路径/PowerShell 清理脚本三节）。"
        "供 RollbackPlannerWorker 格式化打印后存档。"
        "Kind: internal（AnomalyDetectorWorker 产出，RollbackPlannerWorker 消费，见 F-19）。"
    ),
    parent="requirement",
    tags=["cleanup_bot", "plan", "kind.internal"],
    examples=[
        {
            "anomaly_report": (
                "## 异常判定结论\n`E:\\e\\WindowsWorkspace` 是路径拼接事故...\n"
                "## Windows 清理脚本\n```powershell\nRemove-Item ...\n```"
            )
        }
    ],
)

CLEANUP_DONE = Material(
    id="cleanup.done",
    name="Cleanup Done",
    description=(
        "RollbackPlannerWorker 格式化打印后产出的最终结果。"
        "包含 summary（固定字符串 'Cleanup plan generated'）和 report（同上游 Markdown 计划）。"
        "此 Material 为管线终点，无下游 Worker 消费。"
        "Kind: sink（RollbackPlannerWorker 产出，无 consumer，见 F-19）。"
    ),
    parent="requirement",
    tags=["cleanup_bot", "output", "kind.sink"],
    examples=[
        {"summary": "Cleanup plan generated", "report": "## 异常判定结论\n..."}
    ],
)


ALL_FORMATS = [
    CLEANUP_INPUT,
    CLEANUP_EVIDENCE,
    CLEANUP_PLAN,
    CLEANUP_DONE,
]


def register_formats(registry: FormatRegistry) -> None:
    for fmt in ALL_FORMATS:
        if not registry.is_registered(fmt.id):
            try:
                registry.register(fmt)
            except ValueError:
                pass


__all__ = [
    "CLEANUP_INPUT",
    "CLEANUP_EVIDENCE",
    "CLEANUP_PLAN",
    "CLEANUP_DONE",
    "ALL_FORMATS",
    "register_formats",
]
