# [OMNI] origin=claude-code domain=services/pipeline_ci ts=2026-04-20T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.pipeline_ci.domain_scanner_collector.worker.python"
"""DomainScannerWorker — 扫描 packages/ 下所有含 routers.py + pipeline.py 的子包。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from omnifactory.protocol.anchor import Verdict, VerdictKind
from omnifactory.packages.services._core.omnicompany import Worker

logger = logging.getLogger(__name__)


def _collect_domains(
    directory: Path,
    pkg_root: Path,
    domain_filter: list[str] | None,
    result: list,
) -> None:
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

    for sub in sorted(directory.iterdir()):
        if sub.is_dir() and not sub.name.startswith("_"):
            _collect_domains(sub, pkg_root, domain_filter, result)


class DomainScannerWorker(Worker):
    """扫描 packages/ 下所有含 routers.py + pipeline.py 的子包，收集文件内容供审计用。"""

    DESCRIPTION = (
        "扫描 project_root/src/omnifactory/packages/ 下的所有子包（包括嵌套包如 sw/plan），"
        "收集同时包含 routers.py 和 pipeline.py 的目录，读取文件内容供后续审计使用。"
    )
    FORMAT_IN = "pipeline_ci.scan-request"
    FORMAT_OUT = "pipeline_ci.domains"

    def run(self, input_data: Any) -> Verdict:
        project_root = input_data.get("project_root", ".")
        domain_filter: list[str] | None = input_data.get("domains")

        pkg_root = Path(project_root) / "src" / "omnifactory" / "packages"
        if not pkg_root.exists():
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"packages 目录不存在: {pkg_root}",
            )

        domains: list[dict] = []
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
