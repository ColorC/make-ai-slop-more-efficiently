# [OMNI] origin=human domain=software_engineering/tdd ts=2026-04-08T03:23:43Z
# [OMNI] material_id="material:domains.software_engineering.tdd.format_definitions.protocol.py"
"""sw_tdd.formats — TDD 执行工作流的语义类型 (v2: 共享 Format)

数据流 (1 修复回路):
  task-input → test-code → test-exec-result → (FAIL → impl-code → test-exec-result 回路)
                                                 ↓ (PASS)
                                              report (EMIT)
"""

from omnicompany.protocol.format import Format, FormatRegistry

DOMAIN = "sw_tdd"

FORMATS = [
    # ── 输入 ──
    Format(
        id=f"{DOMAIN}.plan",
        name="TDDPlan",
        description="TDD 计划输入: 分步计划文本 + 项目目录",
        parent="sw.task-input",
    ),

    # ── 测试代码 ──
    Format(
        id=f"{DOMAIN}.test-code",
        name="TDDTestCode",
        description="agent_loop 生成的测试文件: 文件列表 + 代码 + 测试命令",
        parent=f"{DOMAIN}.plan",
        tags=["generated"],
    ),

    # ── 测试结果 ──
    Format(
        id=f"{DOMAIN}.test-result",
        name="TDDTestResult",
        description="测试执行结果: exit_code + stdout/stderr + 通过/失败数",
        parent="sw.test-exec-result",
        tags=["executed"],
    ),

    # ── 实现代码 ──
    Format(
        id=f"{DOMAIN}.impl-code",
        name="TDDImplCode",
        description="agent_loop 生成的实现代码: 文件列表 + 代码",
        parent=f"{DOMAIN}.test-result",
        tags=["generated"],
    ),

    # ── TDD 报告 ──
    Format(
        id=f"{DOMAIN}.report",
        name="TDDReport",
        description="TDD 执行报告: report_text + conclusion + metrics",
        parent="sw.report",
        tags=["finalized"],
        required_tags=["executed"],
    ),
]


def register_formats(registry: FormatRegistry) -> None:
    for fmt in FORMATS:
        if not registry.is_registered(fmt.id):
            registry.register(fmt)
