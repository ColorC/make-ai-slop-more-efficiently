"""Build the run-metrics products from the reports currently on disk."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .dashboard import build_dashboard, dump_json, render_dashboard_md, write_if_changed
from .runs import collect_runs, write_jsonl
from .sources import Inputs, default_paths, load_inputs

RUNS_FILENAME = "runs.v1.jsonl"
DRIVES_FILENAME = "drives.v1.jsonl"
DASHBOARD_JSON = "dashboard.json"
DASHBOARD_MD = "dashboard.md"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_products(inputs: Inputs) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    """Rows, dashboard payload and rendered markdown, without touching disk.

    The drive rows are built inside the dashboard, which is where they are
    aggregated; ``build`` reads them back out of it to write their own product
    rather than deriving them a second time.
    """

    rows = collect_runs(inputs)
    dashboard = build_dashboard(rows, inputs)
    return rows, dashboard, render_dashboard_md(dashboard)


def build(
    paths: dict[str, Path] | None = None,
    *,
    out_dir: Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    resolved = paths or default_paths()
    inputs = load_inputs(resolved)
    rows, dashboard, markdown = build_products(inputs)
    target = out_dir or resolved["out_dir"]
    runs_path = target / RUNS_FILENAME
    drives_path = target / DRIVES_FILENAME
    json_path = target / DASHBOARD_JSON
    md_path = target / DASHBOARD_MD
    drives = (dashboard.get("drives") or {}).get("rows") or []
    write_jsonl(rows, runs_path)
    write_jsonl(drives, drives_path)
    stamped = {**dashboard, "generated_at": generated_at or utc_now()}
    write_if_changed(json_path, dump_json(stamped))
    write_if_changed(md_path, markdown)
    return {
        "ok": True,
        "runs": len(rows),
        "drives": len(drives),
        "runs_jsonl": str(runs_path),
        "drives_jsonl": str(drives_path),
        "dashboard_json": str(json_path),
        "dashboard_md": str(md_path),
        "overview": dashboard["overview"],
        "gates": dashboard["gates"]["summary"],
        "drive_gates": dashboard["gates"]["drive_gates"],
        "stop_taxonomy": dashboard["drives"]["pooled"]["stop_taxonomy"],
        "largest_layer": dashboard["bottleneck"]["largest_layer"],
    }
