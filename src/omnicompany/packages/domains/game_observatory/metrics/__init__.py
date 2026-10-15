"""Cross-run efficiency metrics for the Romantic Restaurant live runs.

``build`` scans every feasibility run report and replay index entry, writes one
row per run to ``metrics/runs.v1.jsonl`` and renders the dashboard views
plan.md's F7 section asks for.
"""

from __future__ import annotations

from .build import build, build_products
from .dashboard import build_dashboard, render_dashboard_md
from .drives import (
    DRIVE_ROW_SCHEMA,
    STOP_REASONS,
    advance_gate,
    build_drives,
    energy_bound_spans,
    energy_ceiling,
    pool_drives,
)
from .free_claims import build_free_claim_audit
from .progress import build_progress
from .runs import ROW_SCHEMA, build_row, collect_runs, percentile
from .save_read import build_save_read_trend
from .sources import Inputs, default_paths, load_inputs

__all__ = [
    "DRIVE_ROW_SCHEMA",
    "Inputs",
    "ROW_SCHEMA",
    "STOP_REASONS",
    "advance_gate",
    "build",
    "build_dashboard",
    "build_drives",
    "build_free_claim_audit",
    "build_products",
    "build_progress",
    "build_row",
    "build_save_read_trend",
    "collect_runs",
    "default_paths",
    "energy_bound_spans",
    "energy_ceiling",
    "load_inputs",
    "percentile",
    "pool_drives",
    "render_dashboard_md",
]
