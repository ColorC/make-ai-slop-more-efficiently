# [OMNI] origin=claude-code domain=pipeline_ci/routers.py ts=2026-04-08T03:23:37Z
# [OMNI] material_id="material:diagnosis.pipeline_ci.legacy_router_archive.python"
# OMNI-024 ALLOW: _archive/ 归档文件，不在标准位置属预期
"""pipeline_ci — Router 实现

三个纯确定性 Router（不调 LLM）：
  DomainScannerRouter   — 发现所有待审计的域
  BatchAuditorRouter    — 对每个域跑 ErrorRouteAuditor + PipelineChecker
  CIGateRouter          — critical > 0 则 FAIL，否则 PASS
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any

from omnifactory.protocol.anchor import Verdict, VerdictKind
from omnifactory.runtime.routing.router import Router

logger = logging.getLogger(__name__)


class DomainScannerRouter(Router):
    """扫描 packages/ 下所有含 routers.py + pipeline.py 的子包。"""

    DESCRIPTION = (
        "扫描 project_root/src/omnifactory/packages/ 下的所有子包（包括嵌套包如 sw/plan），"
        "收集同时包含 routers.py 和 pipeline.py 的目录，读取文件内容供后续审计使用。"
    )
    FORMAT_IN = "pipeline_ci.scan-request"
    FORMAT_OUT = "pipeline_ci.domains"

    def run(self, input_data: Any) -> Verdict:
        project_root = input_data.get("project_root", ".")
        domain_filter: list[str] | None = input_data.get("domains")  # 可选白名单

        pkg_root = Path(project_root) / "src" / "omnifactory" / "packages"
        if not pkg_root.exists():
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"packages 目录不存在: {pkg_root}",
            )

        domains = []
        for candidate in sorted(pkg_root.iterdir()):
            if not candidate.is_dir():
                continue
            if candidate.name.startswith("_") or candidate.name.startswith("."):
                continue

            _collect_domains(candidate, pkg_root, domain_filter, domains)

        if not domains:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"在 {pkg_root} 下未发现任何有效包（需要 routers.py + pipeline.py）",
            )

        return Verdict(
            kind=VerdictKind.PASS,
            diagnosis=f"发现 {len(domains)} 个包",
            output={
                "project_root": str(Path(project_root).resolve()),
                "domains": domains,
            },
        )


def _collect_domains(
    directory: Path,
    pkg_root: Path,
    domain_filter: list[str] | None,
    result: list,
) -> None:
    """递归收集包（包括嵌套子包如 sw/plan, omnifactory/guardian）。"""
    routers_file = directory / "routers.py"
    pipeline_file = directory / "pipeline.py"

    if routers_file.exists() and pipeline_file.exists():
        rel = directory.relative_to(pkg_root)
        domain_name = str(rel).replace("\\", "/")
        if domain_filter is None or domain_name in domain_filter:
            package_path = "omnifactory.packages." + ".".join(rel.parts)
            result.append({
                "domain_name": domain_name,
                "package_path": package_path,
                "routers_code": routers_file.read_text(encoding="utf-8", errors="replace"),
                "pipeline_code": pipeline_file.read_text(encoding="utf-8", errors="replace"),
            })

    # 扫描子目录
    for sub in sorted(directory.iterdir()):
        if sub.is_dir() and not sub.name.startswith("_"):
            _collect_domains(sub, pkg_root, domain_filter, result)


class BatchAuditorRouter(Router):
    """对每个域并行执行 ErrorRouteAuditor + PipelineChecker，聚合报告。"""

    DESCRIPTION = (
        "对 DomainScannerRouter 发现的每个域，依次执行："
        "1) ErrorRouteAuditorRouter（FAIL路由覆盖、LLM失败声明、验证绑定、DESCRIPTION完整性）；"
        "2) PipelineChecker 静态类型检查。"
        "聚合所有域的 issues，按严重度排序，计算 critical_count 和 warning_count。"
    )
    FORMAT_IN = "pipeline_ci.domains"
    FORMAT_OUT = "pipeline_ci.ci-report"

    def run(self, input_data: Any) -> Verdict:
        from omnifactory.packages.services._core.workflow_factory.routers import ErrorRouteAuditorRouter
        from omnifactory.protocol.format import create_builtin_registry
        from omnifactory.protocol.pipeline import PipelineChecker

        domains: list[dict] = input_data.get("domains", [])
        auditor = ErrorRouteAuditorRouter()

        domain_results = []
        total_critical = 0
        total_warning = 0

        for domain in domains:
            domain_name = domain["domain_name"]
            package_path = domain["package_path"]
            issues: list[dict] = []

            # ── ErrorRouteAuditor ──
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

            # ── PipelineChecker ──
            try:
                registry = create_builtin_registry()
                checker = PipelineChecker(registry)
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
                    "message": f"PipelineChecker 执行失败（可能依赖问题）: {exc}",
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


def _lazy_import(package_path: str, module_name: str, attr: str):
    """尝试导入 package_path.module_name.attr，失败返回 None。"""
    full_module = f"{package_path}.{module_name}"
    try:
        mod = importlib.import_module(full_module)
        return getattr(mod, attr, None)
    except Exception:
        return None


class CIGateRouter(Router):
    """CI 门控：critical_count > 0 → FAIL，否则 PASS。输出可接入 CI 的结构化报告。"""

    DESCRIPTION = (
        "CI 门控节点：读取 BatchAuditorRouter 的聚合报告，"
        "若 critical_count > 0 则返回 VerdictKind.FAIL（阻断 CI），"
        "否则返回 VerdictKind.PASS（绿灯）。"
        "输出标准 CI 报告 JSON，含每域详情和顶层通过/失败状态。"
    )
    FORMAT_IN = "pipeline_ci.ci-report"
    FORMAT_OUT = "pipeline_ci.ci-report"

    def run(self, input_data: Any) -> Verdict:
        critical = input_data.get("critical_count", 0)
        total = input_data.get("total_domains", 0)
        passed = input_data.get("passed_domains", 0)
        failed = input_data.get("failed_domains", 0)
        warnings = input_data.get("warning_count", 0)

        # 打印人类可读摘要
        print(f"\n{'='*60}")
        print("  Pipeline CI Report")
        print(f"{'='*60}")
        print(f"  Total domains : {total}")
        print(f"  Passed        : {passed}")
        print(f"  Failed        : {failed}")
        print(f"  Critical      : {critical}")
        print(f"  Warnings      : {warnings}")
        print(f"{'='*60}")

        for domain in input_data.get("domain_results", []):
            status_icon = "✓" if domain["status"] == "PASS" else "✗"
            print(f"  {status_icon} {domain['domain_name']} "
                  f"({domain['critical']} critical, {domain['warning']} warnings)")
            for issue in domain.get("issues", []):
                if issue["severity"] == "critical":
                    print(f"      [CRITICAL] {issue['check']}: {issue['message'][:100]}")

        print(f"{'='*60}\n")

        if critical > 0:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"CI 失败: {critical} 个 critical 问题，{failed} 个域不通过",
                output=input_data,
            )

        return Verdict(
            kind=VerdictKind.PASS,
            diagnosis=f"CI 通过: {total} 个域，{warnings} 个 warnings（无 critical）",
            output=input_data,
        )
