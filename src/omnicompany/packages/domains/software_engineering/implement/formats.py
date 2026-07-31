# [OMNI] origin=human domain=software_engineering/implement ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.implement.workflow_formats.definitions.py"
"""sw_implement.formats — 独立实施工作流的语义类型体系 (v2: 共享 Format)

数据流 (1 上下文收集回路):
  task-input → project-snapshot → context-state → (回路: 不够 → 再扫描)
                                                     ↓ (SUFFICIENT)
                                                 change-set → report (EMIT)
"""

from omnicompany.protocol.format import Format, FormatRegistry

DOMAIN = "sw_implement"

FORMATS = [
    # ── 输入（继承 sw.task-input）──
    Format(
        id=f"{DOMAIN}.task",
        name="ImplTask",
        description="实施任务: 需求文本 + 项目目录 + 范围 + 相关文件",
        parent="sw.task-input",
    ),

    # ── 项目快照（继承 sw.project-snapshot）──
    Format(
        id=f"{DOMAIN}.snapshot",
        name="ImplProjectSnapshot",
        description="项目快照: 目录结构 + 主语言 + 关键文件列表",
        parent="sw.project-snapshot",
        tags=["scanned"],
    ),

    # ── 上下文累积状态 ──
    Format(
        id=f"{DOMAIN}.context-state",
        name="ImplContextState",
        description=(
            "实施上下文状态: file_batch(已读文件批次) + "
            "iteration + sufficient 标记。"
            "回路载体，每轮累积更多文件。"
        ),
        parent="agent-state",
        tags=["stateful", "accumulating"],
    ),

    # ── 实施变更集（继承 sw.change-set）──
    Format(
        id=f"{DOMAIN}.changes",
        name="ImplChangeSet",
        description="LLM 生成的变更集: code-change 列表 + 测试状态",
        parent="sw.change-set",
        tags=["generated"],
    ),

    # ── 报告（继承 sw.report）──
    Format(
        id=f"{DOMAIN}.report",
        name="ImplReport",
        description="实施报告: report_text + conclusion + metrics",
        parent="sw.report",
        tags=["finalized"],
    ),
]


def register_formats(registry: FormatRegistry) -> None:
    for fmt in FORMATS:
        if not registry.is_registered(fmt.id):
            registry.register(fmt)
