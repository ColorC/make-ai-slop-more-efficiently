# [OMNI] origin=claude-code domain=services/pipeline_ci ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.pipeline_ci.batch_auditor_aggregator.worker.python"
"""BatchAuditorWorker — 对每个域并行执行 ErrorRouteAuditor + TeamChecker，聚合报告。"""
from __future__ import annotations

import importlib
import logging
from typing import Any

from omnifactory.protocol.anchor import Verdict, VerdictKind
from omnifactory.packages.services._core.omnicompany import Worker

logger = logging.getLogger(__name__)


def _lazy_import(package_path: str, module_name: str, attr: str):
    full_module = f"{package_path}.{module_name}"
    try:
        mod = importlib.import_module(full_module)
        return getattr(mod, attr, None)
    except Exception:
        return None


class BatchAuditorWorker(Worker):
    """对每个域依次执行 ErrorRouteAuditor + TeamChecker，聚合 critical/warning 报告。"""

    DESCRIPTION = (
        "对 DomainScannerWorker 发现的每个域，依次执行："
        "1) ErrorRouteAuditorRouter（FAIL路由覆盖、LLM失败声明、验证绑定、DESCRIPTION完整性）；"
        "2) TeamChecker 静态类型检查。"
        "聚合所有域的 issues，按严重度排序，计算 critical_count 和 warning_count。"
    )
    FORMAT_IN = "pipeline_ci.domains"
    FORMAT_OUT = "pipeline_ci.ci-report"

    def run(self, input_data: Any) -> Verdict:
        from omnifactory.packages.services._core.team_builder.routers import ErrorRouteAuditorRouter
        from omnifactory.protocol.format import create_builtin_registry
        from omnifactory.protocol.team import TeamChecker

        domains: list[dict] = input_data.get("domains", [])
        auditor = ErrorRouteAuditorRouter()

        domain_results = []
        total_critical = 0
        total_warning = 0

        for domain in domains:
            domain_name = domain["domain_name"]
            package_path = domain["package_path"]
            issues: list[dict] = []

            try:
                ea_verdict = auditor.run({
                    "package_path": package_path,
                    "files": {
                        "routers.py": domain["routers_code"],
                        "pipeline.py": domain["pipeline_code"],
                    },
                    "pipeline_name": domain_name,
                })
                ea_report = ea_verdict.output.get("error_route_report", {})
                for issue in ea_report.get("issues", []):
                    issues.append({
                        "source": "error_route_auditor",
                        "severity": issue.get("severity", "warning"),
                        "check": issue.get("check", "unknown"),
                        "message": issue.get("message", ""),
                    })
            except Exception as exc:
                issues.append({
                    "source": "error_route_auditor",
                    "severity": "critical",
                    "check": "auditor_crash",
                    "message": f"ErrorRouteAuditor 崩溃: {exc}",
                })

            try:
                registry = create_builtin_registry()
                checker = TeamChecker(registry)
                build_pipeline_fn = _lazy_import(package_path, "pipeline", "build_pipeline")
                if build_pipeline_fn:
                    pipeline_spec = build_pipeline_fn()
                    result = checker.check(pipeline_spec)
                    if not result.valid:
                        for err in result.type_errors:
                            issues.append({
                                "source": "pipeline_checker",
                                "severity": "critical",
                                "check": "type_error",
                                "message": str(err),
                            })
                    for warn in getattr(result, "warnings", []):
                        issues.append({
                            "source": "pipeline_checker",
                            "severity": "warning",
                            "check": "type_warning",
                            "message": str(warn),
                        })
            except Exception as exc:
                issues.append({
                    "source": "pipeline_checker",
                    "severity": "warning",
                    "check": "checker_error",
                    "message": f"TeamChecker 执行失败（可能依赖问题）: {exc}",
                })

            critical = sum(1 for i in issues if i["severity"] == "critical")
            warning = sum(1 for i in issues if i["severity"] == "warning")
            total_critical += critical
            total_warning += warning

            domain_results.append({
                "domain_name": domain_name,
                "critical": critical,
                "warning": warning,
                "issues": issues,
                "status": "FAIL" if critical > 0 else "PASS",
            })

        passed = sum(1 for d in domain_results if d["status"] == "PASS")
        failed = len(domain_results) - passed

        return Verdict(
            kind=VerdictKind.PASS,
            diagnosis=(
                f"审计完成: {len(domains)} 域, "
                f"{passed} PASS, {failed} FAIL, "
                f"{total_critical} critical, {total_warning} warnings"
            ),
            output={
                "project_root": input_data.get("project_root", ""),
                "total_domains": len(domains),
                "passed_domains": passed,
                "failed_domains": failed,
                "critical_count": total_critical,
                "warning_count": total_warning,
                "domain_results": domain_results,
            },
        )
