# [OMNI] origin=claude-code domain=services/_governance/doc_steward ts=2026-06-13T08:10:00Z type=router
# [OMNI] material_id="material:governance.doc_steward.package_init.py"
"""文档时效性治理 — 维护 plan/report/规范 的引用完整性与语义时效性。"""
from .steward import (
    DocFinding,
    discover_targets,
    latest_findings,
    run_reference_audit,
    run_timeliness,
    scan_references,
)

__all__ = [
    "DocFinding",
    "discover_targets",
    "latest_findings",
    "run_reference_audit",
    "run_timeliness",
    "scan_references",
]
