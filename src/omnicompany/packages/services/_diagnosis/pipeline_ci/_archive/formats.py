# [OMNI] origin=omnifactory domain=pipeline_ci/formats.py ts=2026-04-10T00:00:00Z
# [OMNI] material_id="material:diagnosis.pipeline_ci.material_definitions.python"
"""pipeline_ci — Format 定义"""

from __future__ import annotations

from omnifactory.protocol.format import Format, FormatRegistry


FORMATS = [
    Format(
        id="pipeline_ci.scan-request",
        name="CIScanRequest",
        description=(
            "CI 扫描请求：包含要扫描的 packages/ 根目录路径和可选的域名过滤列表。"
            "验证标准：project_root 必须是存在的目录路径。"
            "下游用途：DomainScannerRouter 用此定位所有待审计的域。"
        ),
        parent="requirement",
        tags=["pipeline_ci", "kind.source"],
        json_schema={
            "type": "object",
            "required": ["project_root"],
            "properties": {
                "project_root": {"type": "string", "description": "packages/ 根目录路径"},
                "domain_filter": {"type": "array", "items": {"type": "string"}, "description": "域名过滤列表"},
            },
        },
    ),
    Format(
        id="pipeline_ci.domains",
        name="CIDomains",
        description=(
            "已发现的管线域列表：每个条目包含域名、routers.py 和 pipeline.py 的文件内容，"
            "以及推断出的 package_path。"
            "验证标准：每个域必须同时有 routers.py 和 pipeline.py。"
            "下游用途：BatchAuditorRouter 对每个域并行执行 ErrorRouteAuditor 和 TeamChecker。"
        ),
        parent="pipeline_ci.scan-request",
        tags=["pipeline_ci", "structured", "kind.internal"],
        json_schema={
            "type": "object",
            "required": ["domains"],
            "properties": {
                "domains": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name", "package_path"],
                        "properties": {
                            "name": {"type": "string"},
                            "package_path": {"type": "string"},
                            "routers_content": {"type": "string"},
                            "pipeline_content": {"type": "string"},
                        },
                    },
                },
            },
        },
    ),
    Format(
        id="pipeline_ci.ci-report",
        name="CIReport",
        description=(
            "CI 审计聚合报告：包含 total_domains、passed_domains、failed_domains、"
            "critical_count、warning_count 以及按域分组的 issues 列表（每条含 severity/check/message）。"
            "验证标准：critical_count 为 0 时 CI 应当通过，否则失败。"
            "下游用途：CIGateRouter 据此决定 PASS（绿灯）或 FAIL（阻断 CI）。"
        ),
        parent="pipeline_ci.domains",
        tags=["pipeline_ci", "structured", "validated", "kind.internal"],
        json_schema={
            "type": "object",
            "required": ["total_domains", "critical_count"],
            "properties": {
                "total_domains": {"type": "integer"},
                "passed_domains": {"type": "integer"},
                "failed_domains": {"type": "integer"},
                "critical_count": {"type": "integer"},
                "warning_count": {"type": "integer"},
                "issues": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "domain": {"type": "string"},
                            "severity": {"type": "string", "enum": ["critical", "warning", "info"]},
                            "check": {"type": "string"},
                            "message": {"type": "string"},
                        },
                    },
                },
            },
        },
    ),
]


def register_formats(registry: FormatRegistry) -> None:
    for fmt in FORMATS:
        registry.register(fmt)
