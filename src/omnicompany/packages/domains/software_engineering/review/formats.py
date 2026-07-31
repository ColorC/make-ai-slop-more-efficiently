# [OMNI] origin=human domain=software_engineering/review ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.review.format_definitions.protocol.py"
"""sw_review.formats — 代码审查工作流的语义类型体系

数据流:
  diff → context → test-coverage → review-context
           ↑                            ↓ (INSUFFICIENT → 回路)
           └────────────────────────────┘
                                        ↓ (SUFFICIENT)
                                    findings → validated → report

累积上下文: review-context（diff + 周围代码 + 测试覆盖）
"""

from omnicompany.protocol.format import Format, FormatRegistry

DOMAIN = "sw_review"

FORMATS = [
    # ── 输入 ──
    Format(
        id=f"{DOMAIN}.diff",
        name="ReviewDiff",
        description="待审查的代码差异: git diff 输出或直接 diff 文本，包含描述信息和来源(git SHA/直接文本)",
        parent="tool-observation",
    ),

    # ── 上下文收集 ──
    Format(
        id=f"{DOMAIN}.context",
        name="CodeContext",
        description="diff 涉及的周围代码上下文: 每个修改文件的 imports、被调用函数、调用者、文件级注释",
        parent=f"{DOMAIN}.diff",
        tags=["context-gathered"],
    ),

    # ── 测试覆盖 ──
    Format(
        id=f"{DOMAIN}.test-coverage",
        name="TestCoverage",
        description="变更对应的测试覆盖情况: 找到的测试文件列表、测试内容摘要、覆盖缺口",
        parent=f"{DOMAIN}.context",
        tags=["context-gathered", "tests-scanned"],
    ),

    # ── 累积上下文（信息收集回路载体）──
    Format(
        id=f"{DOMAIN}.review-context",
        name="ReviewContext",
        description="审查累积状态: diff + 已收集的上下文 + 测试覆盖 + 加探过的文件列表 + 是否信息充分",
        parent="agent-state",
        tags=["stateful", "accumulating"],
    ),

    # ── LLM 审查发现 ──
    Format(
        id=f"{DOMAIN}.findings",
        name="ReviewFindings",
        description="LLM 审查发现: Critical(🔴)/Important(🟡)/Minor(🔵) 分级问题列表，每个问题含文件、行号、描述",
        parent=f"{DOMAIN}.review-context",
        tags=["reviewed"],
    ),

    # ── 交叉验证 ──
    Format(
        id=f"{DOMAIN}.validated-findings",
        name="ValidatedFindings",
        description="交叉验证后的发现: 检查每个 Critical/Important 发现是否有代码证据支持，过滤无依据的误报",
        parent=f"{DOMAIN}.findings",
        tags=["reviewed", "validated"],
        required_tags=["reviewed"],
    ),

    # ── 最终报告 ──
    Format(
        id=f"{DOMAIN}.report",
        name="ReviewReport",
        description="最终审查报告: APPROVE/REQUEST_CHANGES/NEEDS_DISCUSSION 结论 + 分级问题列表 + 报告文本",
        parent=f"{DOMAIN}.validated-findings",
        tags=["reported"],
        required_tags=["validated"],
    ),
]


def register_formats(registry: FormatRegistry) -> None:
    for fmt in FORMATS:
        if not registry.is_registered(fmt.id):
            registry.register(fmt)
