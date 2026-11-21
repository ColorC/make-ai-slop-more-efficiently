# [OMNI] origin=omnicompany domain=selftest/formats.py ts=2026-04-20T00:00:00Z
# [OMNI] material_id="material:core.selftest.material_definitions.registry.py"
"""selftest — Material 定义 (原 Format 类, terminology §6 · standards 术语说明段)。

Material kind 标注 (F-16):
  selftest.request          → kind.source    (外部触发, 无 producer Worker)
  selftest.registry-report  → kind.internal  (Worker 间流转)
  selftest.selftest-report  → kind.internal  (Worker 间流转)
  selftest.health-report    → kind.sink      (最终健康报告, 返回调用者 / 无 consumer Worker)
"""

from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Material
from omnicompany.protocol.format import FormatRegistry


FORMATS = [
    Material(
        id="selftest.request",
        name="SelftestRequest",
        description=(
            "Selftest 请求：可选传入 project_root（默认自动探测），"
            "控制要运行哪些检查套件（默认全部）。"
            "验证标准：project_root 若传入则必须是已存在的目录。"
            "下游用途：RegistryCheckerRouter 用此确定项目根目录并加载所有管线。"
            "Kind: source (外部触发 · 无 producer Worker · 见 F-16)。"
        ),
        parent="requirement",
        tags=["selftest", "kind.source"],
        json_schema={
            "type": "object",
            "properties": {
                "project_root": {"type": "string", "description": "项目根目录路径，可选"},
                "suites": {"type": "array", "items": {"type": "string"}, "description": "要运行的检查套件列表"},
            },
        },
    ),
    Material(
        id="selftest.registry-report",
        name="SelftestRegistryReport",
        description=(
            "Team 注册检查报告：对每个已注册 Team（管线）记录 build_pipeline() 是否成功、"
            "build_bindings() 是否成功、node_ids 与 binding_keys 是否完全对应。"
            "验证标准：每条记录必须有 name/ok/error 三个字段。"
            "下游用途：FunctionalTester Worker 基于此列表决定需要跑哪些功能测试。"
            "Kind: internal (Worker 间流转 · 见 F-16)。"
        ),
        parent="selftest.request",
        tags=["selftest", "structured", "kind.internal"],
        json_schema={
            "type": "object",
            "properties": {
                "pipelines": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name", "ok"],
                        "properties": {
                            "name": {"type": "string"},
                            "ok": {"type": "boolean"},
                            "error": {"type": "string"},
                        },
                    },
                },
                "total": {"type": "integer"},
                "passed": {"type": "integer"},
            },
            "required": ["pipelines"],
        },
    ),
    Material(
        id="selftest.selftest-report",
        name="SelftestReport",
        description=(
            "Selftest 综合报告：包含 registry_failures（注册失败列表）、"
            "functional_failures（功能测试失败列表）、stock_ok（Stock 读写测试 · 原 EventBus）、"
            "total_checks、passed_checks、failed_checks。"
            "验证标准：failed_checks == 0 时系统功能完好。"
            "下游用途：SelftestGate Worker 据此决定最终 PASS/FAIL。"
            "Kind: internal (Worker 间流转 · 见 F-16)。"
        ),
        parent="selftest.registry-report",
        tags=["selftest", "structured", "validated", "kind.internal"],
        json_schema={
            "type": "object",
            "required": ["total_checks", "passed_checks", "failed_checks"],
            "properties": {
                "registry_failures": {"type": "array", "items": {"type": "string"}},
                "functional_failures": {"type": "array", "items": {"type": "string"}},
                "eventbus_ok": {"type": "boolean"},
                "total_checks": {"type": "integer"},
                "passed_checks": {"type": "integer"},
                "failed_checks": {"type": "integer"},
            },
        },
    ),
    Material(
        id="selftest.health-report",
        name="SelftestHealthReport",
        description=(
            "Selftest 最终健康报告：在 selftest-report 基础上追加 llm_ok（LLM 连通性）、"
            "llm_summary（LLM 生成的自然语言摘要，LLM 不可用时为空）。"
            "验证标准：含 total_checks/passed_checks/failed_checks/llm_ok 四字段。"
            "下游用途：终端输出，供人类或 CI 读取系统整体健康状态。"
            "Kind: sink (最终产出, 无 consumer Worker · 见 F-16)。"
        ),
        parent="selftest.selftest-report",
        tags=["selftest", "structured", "validated", "kind.sink"],
        json_schema={
            "type": "object",
            "required": ["total_checks", "passed_checks", "failed_checks", "llm_ok"],
            "properties": {
                "total_checks": {"type": "integer"},
                "passed_checks": {"type": "integer"},
                "failed_checks": {"type": "integer"},
                "llm_ok": {"type": "boolean"},
                "llm_summary": {"type": "string"},
            },
        },
    ),
]


def register_formats(registry: FormatRegistry) -> None:
    for fmt in FORMATS:
        registry.register(fmt)
