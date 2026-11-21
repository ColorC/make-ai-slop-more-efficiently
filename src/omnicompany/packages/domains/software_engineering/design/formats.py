# [OMNI] origin=human domain=software_engineering/design ts=2026-04-08T03:23:41Z
# [OMNI] material_id="material:domains.software_engineering.design.semantic_formats.registry.py"
"""sw_design.formats — 设计审查工作流的语义类型 (v2: 共享 Format)

数据流 (1 回路):
  task-input → snapshot → context-state → (回路: 不够 → 继续扫描/读文件)
                                              ↓ (SUFFICIENT)
                                          patterns → llm-review → report (EMIT)
"""

from omnicompany.protocol.format import Format, FormatRegistry

DOMAIN = "sw_design"

FORMATS = [
    # ── 输入 ──
    Format(
        id=f"{DOMAIN}.task",
        name="DesignTask",
        description="设计审查任务: 设计提案文本 + 项目目录 + 审查目标",
        parent="sw.task-input",
    ),

    # ── 项目快照 ──
    Format(
        id=f"{DOMAIN}.snapshot",
        name="DesignProjectSnapshot",
        description="项目快照: 目录结构 + 语言分布 + 关键文件",
        parent="sw.project-snapshot",
        tags=["scanned"],
    ),

    # ── 上下文累积 ──
    Format(
        id=f"{DOMAIN}.context-state",
        name="DesignContextState",
        description="设计审查上下文: file_batch + iteration + sufficient",
        parent="agent-state",
        tags=["stateful", "accumulating"],
    ),

    # ── 架构模式 ──
    Format(
        id=f"{DOMAIN}.patterns",
        name="ArchPatterns",
        description="现有架构模式: 命名规范 + 分层 + 测试策略 + 错误处理 + DI",
        parent=f"{DOMAIN}.context-state",
        tags=["analyzed"],
    ),

    # ── LLM 审查 ──
    Format(
        id=f"{DOMAIN}.review",
        name="DesignReview",
        description="LLM 审查结果: findings + conclusion + summary",
        parent="sw.llm-review",
        tags=["reviewed"],
    ),

    # ── 最终报告 ──
    Format(
        id=f"{DOMAIN}.report",
        name="DesignReport",
        description="报告: report_text + conclusion + metrics",
        parent="sw.report",
        tags=["finalized"],
        required_tags=["reviewed"],
    ),
]


def register_formats(registry: FormatRegistry) -> None:
    for fmt in FORMATS:
        if not registry.is_registered(fmt.id):
            registry.register(fmt)
