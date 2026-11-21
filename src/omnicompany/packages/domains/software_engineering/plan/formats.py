# [OMNI] origin=human domain=software_engineering/plan ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.plan.workflow_formats.definitions.py"
"""sw_plan.formats — 实施计划工作流的语义类型 (v2: 共享 Format)

数据流 (2 回路):
  task-input → snapshot → context-state → (回路1: 不够 → 再扫描)
                                              ↓ (SUFFICIENT)
                                          file-map → draft → review-result
                                                      ↑          ↓ (FAIL → 回路2)
                                                      └──────────┘
                                                                 ↓ (PASS)
                                                               plan (EMIT)
"""

from omnicompany.protocol.format import Format, FormatRegistry

DOMAIN = "sw_plan"

FORMATS = [
    # ── 输入 ──
    Format(
        id=f"{DOMAIN}.spec",
        name="PlanSpec",
        description="设计文档/需求: 文本内容 + 来源路径 + 项目目录",
        parent="sw.task-input",
    ),

    # ── 代码库扫描 ──
    Format(
        id=f"{DOMAIN}.codebase-scan",
        name="PlanCodebaseScan",
        description="代码库扫描结果: 目录结构树、文件列表、关键文件识别",
        parent="sw.project-snapshot",
        tags=["scanned"],
    ),

    # ── 代码上下文（累积，回路1载体）──
    Format(
        id=f"{DOMAIN}.code-context",
        name="PlanCodeContext",
        description="累积代码上下文: 已读文件 + 模式 + 充分性",
        parent="agent-state",
        tags=["stateful", "accumulating"],
    ),

    # ── 文件映射 ──
    Format(
        id=f"{DOMAIN}.file-map",
        name="FileMap",
        description="文件创建/修改计划: 每个文件的操作 + 依赖关系",
        parent=f"{DOMAIN}.code-context",
        tags=["mapped"],
    ),

    # ── 计划草稿 ──
    Format(
        id=f"{DOMAIN}.draft",
        name="PlanDraft",
        description="LLM 生成的实施计划草稿: Task 列表 + 代码 + 验证命令",
        parent=f"{DOMAIN}.file-map",
        tags=["drafted"],
    ),

    # ── 自检结果（回路2载体）──
    Format(
        id=f"{DOMAIN}.review-result",
        name="SelfReviewResult",
        description="计划自检: 占位符扫描 + 覆盖度 + 问题列表",
        parent=f"{DOMAIN}.draft",
        tags=["reviewed"],
    ),

    # ── 终版计划 ──
    Format(
        id=f"{DOMAIN}.plan",
        name="FinalPlan",
        description="终版实施计划: 零占位符 + 完整代码块 + TDD 步骤",
        parent=f"{DOMAIN}.review-result",
        tags=["validated", "finalized"],
        required_tags=["reviewed"],
    ),
]


def register_formats(registry: FormatRegistry) -> None:
    for fmt in FORMATS:
        if not registry.is_registered(fmt.id):
            registry.register(fmt)
