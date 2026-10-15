"""Evidence-backed cross-reference ledger for Romantic Restaurant."""

from .evidence_index import FactV1, extract_facts_from_report, index_evidence
from .facility import build_facility_view
from .report import build_summary, render_report_md
from .row_coverage import build_row_coverage
from .rules import RuleResult, apply_rules
from .run_hook import run_hook
from .writeback import writeback_ledger

__all__ = [
    "FactV1",
    "RuleResult",
    "apply_rules",
    "build_facility_view",
    "build_row_coverage",
    "build_summary",
    "extract_facts_from_report",
    "index_evidence",
    "render_report_md",
    "run_hook",
    "writeback_ledger",
]
