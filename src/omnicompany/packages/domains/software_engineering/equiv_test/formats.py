# [OMNI] origin=human domain=software_engineering/equiv_test ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.equiv_test.semantic_formats.registry.py"
"""equivalence_test.formats — 跨语言语义等价性测试的语义类型体系

数据流:
  test-spec → test-suite → execution-result → comparison-report → diagnosed-report
"""

from omnicompany.protocol.format import Format, FormatRegistry

DOMAIN = "equiv"

FORMATS = [
    Format(
        id=f"{DOMAIN}.test-spec",
        name="TestSpec",
        description="等价性测试规格：Python 源文件路径 + TS 翻译路径 + 接口清单 + 测试类型要求",
        parent="requirement",
    ),
    Format(
        id=f"{DOMAIN}.test-suite",
        name="TestSuite",
        description="生成的测试套件：Python 测试脚本 + TS 测试脚本 + 测试用例清单",
        parent=f"{DOMAIN}.test-spec",
        tags=["structured", "executable"],
    ),
    Format(
        id=f"{DOMAIN}.execution-result",
        name="ExecutionResult",
        description="双语言测试执行结果：Python stdout JSON + TS stdout JSON + 执行状态",
        parent=f"{DOMAIN}.test-suite",
        tags=["structured", "executable", "executed"],
    ),
    Format(
        id=f"{DOMAIN}.comparison-report",
        name="ComparisonReport",
        description="逐 key 对比结果：匹配列表 + 不匹配列表 + 缺失列表 + 总体判定",
        parent=f"{DOMAIN}.execution-result",
        tags=["structured", "executable", "executed", "compared"],
    ),
    Format(
        id=f"{DOMAIN}.diagnosed-report",
        name="DiagnosedReport",
        description="带根因分析的最终报告：每个不匹配项的根因 + 修复建议 + 严重度",
        parent=f"{DOMAIN}.comparison-report",
        tags=["structured", "executable", "executed", "compared", "diagnosed"],
    ),
]


def register_formats(registry: FormatRegistry) -> None:
    for fmt in FORMATS:
        if not registry.is_registered(fmt.id):
            registry.register(fmt)
