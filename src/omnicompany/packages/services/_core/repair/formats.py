# [OMNI] origin=omnicompany domain=omnicompany/repair ts=2026-04-20T00:00:00Z
# [OMNI] material_id="material:core.repair.format_schema.registry.py"
"""repair.formats — Format 修复管线的 Material 定义 (Clean Migration 2026-04-20).

Material kind 标注 (F-19):
  repair.fmt.request  → kind.source (外部触发, 无 producer Worker)
  repair.fmt.report   → kind.sink   (最终修复报告, 返回调用者 / 无 consumer Worker)
"""

from omnicompany.packages.services._core.omnicompany import Material
from omnicompany.protocol.format import FormatRegistry


FORMAT_REPAIR_REQUEST = Material(
    id="repair.fmt.request",
    name="Format Repair Request",
    description=(
        "触发 Format 自动修复 AgentLoop 的入口请求。"
        "指定待修复的 Format ID、源码根目录，以及最大修复迭代次数（默认 3）。"
        "修复循环：诊断 → LLM 修复规划 → 源码 Patch → 重新诊断，直至 A 级或达到上限。"
        "Kind: source (外部触发 · 无 producer Worker · 见 F-19)。"
    ),
    parent="requirement",
    tags=["repair", "input", "service", "kind.source"],
    examples=[
        {"format_id": "bw.combat_balance_matrix"},
        {
            "format_id": "bw.code_spec",
            "source_root": "e:/WindowsWorkspace/omnicompany/src/omnicompany",
            "max_iterations": 3,
        },
    ],
)

FORMAT_REPAIR_REPORT = Material(
    id="repair.fmt.report",
    name="Format Repair Report",
    description=(
        "FormatRepairAgentLoop 输出的修复报告。"
        "含修复前后的健康等级（initial_grade / final_grade）、实际迭代次数、"
        "每轮迭代的 delta 和 patch 结果（iterations 列表）、以及是否修复成功（success）。"
        "success=True 当且仅当 final_grade=='A'。"
        "Kind: sink (最终产出, 无 consumer Worker · 见 F-19)。"
    ),
    parent="requirement",
    tags=["repair", "report", "output", "kind.sink"],
    examples=[
        {
            "format_id": "bw.combat_balance_matrix",
            "source_root": "e:/WindowsWorkspace/omnicompany/src/omnicompany",
            "initial_grade": "C",
            "final_grade": "A",
            "success": True,
            "iterations": [
                {
                    "iter": 1,
                    "grade_before": "C",
                    "delta": {"description": "修改后描述文本", "tags": ["bw", "domain.voxelcraft"]},
                    "patch_ok": True,
                    "grade_after": "A",
                }
            ],
        }
    ],
)


ALL_FORMATS = [FORMAT_REPAIR_REQUEST, FORMAT_REPAIR_REPORT]


def register_formats(registry: FormatRegistry) -> None:
    for fmt in ALL_FORMATS:
        if not registry.is_registered(fmt.id):
            try:
                registry.register(fmt)
            except ValueError:
                pass
