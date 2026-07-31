# [OMNI] origin=human domain=software_engineering/verify ts=2026-04-08T03:23:43Z
# [OMNI] material_id="material:domains.software_engineering.verify.format_definitions.protocol.py"
"""sw_verify.formats — 验证工作流的语义类型 (v2: 共享 Format)

数据流:
  task-input → env-check → execution → analysis
                                        ↓ (UNCERTAIN)
                                  supplemental → execution [回路]
                                        ↓ (CONFIRMED/REFUTED)
                                      report
"""

from omnicompany.protocol.format import Format, FormatRegistry

DOMAIN = "sw_verify"

FORMATS = [
    # ── 输入 ──
    Format(
        id=f"{DOMAIN}.claim",
        name="VerifyClaim",
        description="待验证的声称: claim + verify_cmd + work_dir + expect_pattern",
        parent="sw.task-input",
    ),

    # ── 环境检查 ──
    Format(
        id=f"{DOMAIN}.env-check",
        name="EnvCheck",
        description="验证环境就绪状态: 工作目录 + 命令可执行",
        parent=f"{DOMAIN}.claim",
        tags=["env-checked"],
    ),

    # ── 命令执行结果 ──
    Format(
        id=f"{DOMAIN}.execution",
        name="CmdExecution",
        description="验证命令执行结果: stdout/stderr/exit_code",
        parent="sw.test-exec-result",
        tags=["executed"],
    ),

    # ── 证据分析 ──
    Format(
        id=f"{DOMAIN}.analysis",
        name="EvidenceAnalysis",
        description="CONFIRMED/REFUTED/UNCERTAIN 判定",
        parent=f"{DOMAIN}.execution",
        tags=["analyzed"],
    ),

    # ── 补充验证计划 ──
    Format(
        id=f"{DOMAIN}.supplemental",
        name="SupplementalPlan",
        description="不确定时的补充验证命令",
        parent=f"{DOMAIN}.analysis",
        tags=["analyzed", "supplemental-planned"],
        required_tags=["analyzed"],
    ),

    # ── 最终报告 ──
    Format(
        id=f"{DOMAIN}.report",
        name="VerifyReport",
        description="最终验证报告: report_text + conclusion + metrics",
        parent="sw.report",
        tags=["reported"],
        required_tags=["analyzed"],
    ),

    # ── 累积上下文 ──
    Format(
        id=f"{DOMAIN}.verify-context",
        name="VerifyContext",
        description="验证全程状态: 声称 + 执行记录 + 判定",
        parent="agent-state",
        tags=["stateful", "accumulating"],
    ),
]


def register_formats(registry: FormatRegistry) -> None:
    for fmt in FORMATS:
        if not registry.is_registered(fmt.id):
            registry.register(fmt)
