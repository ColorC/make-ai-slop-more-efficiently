# [OMNI] origin=human domain=software_engineering/lang_rewrite_verifier ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.lang_rewrite_verifier.smoke_formats.definitions.py"
"""lang_rewrite_verifier.formats — 冒烟验证管线的格式类型

数据流:
  rewrite.verified-code
      → smoke.test-suite       (SmokeTestGeneratorRouter 产出)
      → smoke.result           (SmokeRunnerRouter PASS 时产出)
      → debug.error-report     (SmokeRunnerRouter FAIL 时转入 debugger)
"""

from omnicompany.protocol.format import Format, FormatRegistry

DOMAIN = "smoke"

FORMATS = [
    Format(
        id=f"{DOMAIN}.test-suite",
        name="SmokeTestSuite",
        description=(
            "冒烟测试套件：由 SmokeTestGeneratorRouter 产出，"
            "包含 work_dir、compile_command、test_cases 列表（由简到繁）"
        ),
        parent="code",
        tags=["smoke", "test-plan"],
    ),
    Format(
        id=f"{DOMAIN}.result",
        name="SmokeResult",
        description=(
            "冒烟测试最终结果：全部用例通过时由 SmokeRunnerRouter PASS 产出，"
            "含 passed_cases 列表和 smoke_passed=True"
        ),
        parent=f"{DOMAIN}.test-suite",
        tags=["smoke", "verified"],
        required_tags=["smoke"],
    ),
]


def register_formats(registry: FormatRegistry) -> None:
    for fmt in FORMATS:
        if not registry.is_registered(fmt.id):
            registry.register(fmt)
