"""Incremental cross-reference refresh executed at the end of a live run."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .evidence_index import index_evidence
from .writeback import writeback_ledger


def repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / "data").is_dir():
            return parent
    raise RuntimeError("repository root not found")


def default_paths() -> dict[str, Path]:
    root = repo_root()
    model = root / "data/domains/game_observatory/game_models/romantic-restaurant"
    case = root / "docs/projects/game-observatory/cases/romantic-restaurant"
    return {
        "feasibility": model / "feasibility",
        "config": model / "config",
        "facts": model / "crossref/facts.v1.ndjson",
        "ledger": model / "crossref/config_crossref_ledger.v1.json",
        "summary": model / "crossref/summary.v1.json",
        "row_coverage": model / "crossref/row_coverage.v1.json",
        "report_md": model / "crossref/report.md",
        "save_schema": model / "crossref/save_schema_coverage.v1.json",
        "docs": case / "design",
        "observations": case / "records/unregistered-observations.md",
    }


def _coverage_snapshot(summary_path: Path) -> dict[str, Any]:
    if not summary_path.is_file():
        return {"field_coverage_rate": None, "row_coverage": {}}
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    return {
        "field_coverage_rate": (summary.get("totals") or {}).get("coverage_rate"),
        "row_coverage": {
            table: value.get("verified_rows")
            for table, value in (summary.get("row_coverage") or {}).items()
        },
    }


def refresh(paths: dict[str, Path] | None = None) -> dict[str, Any]:
    """Run index, writeback and report, returning the coverage delta."""

    paths = paths or default_paths()
    before_facts = len(
        [
            line
            for line in (
                paths["facts"].read_text(encoding="utf-8").splitlines()
                if paths["facts"].is_file()
                else []
            )
            if line.strip()
        ]
    )
    before = _coverage_snapshot(paths["summary"])
    skipped: list[str] = []
    facts = index_evidence(paths["feasibility"], paths["facts"], skipped=skipped)
    outcome = writeback_ledger(
        facts_path=paths["facts"],
        ledger_path=paths["ledger"],
        config_root=paths["config"],
        summary_path=paths["summary"],
        docs_dir=paths.get("docs"),
        observations_path=paths.get("observations"),
        row_coverage_path=paths.get("row_coverage"),
        report_md_path=paths.get("report_md"),
        save_schema_path=paths.get("save_schema"),
    )
    after = _coverage_snapshot(paths["summary"])
    fact_kinds: dict[str, int] = {}
    for fact in facts:
        fact_kinds[fact.kind] = fact_kinds.get(fact.kind, 0) + 1
    return {
        "ok": True,
        "facts_before": before_facts,
        "facts_after": len(facts),
        "new_facts": len(facts) - before_facts,
        "fact_kinds": dict(sorted(fact_kinds.items())),
        # The gameplay line writes reports into the same tree, so a report can
        # be caught half-written; it is skipped and named rather than failing
        # the refresh.
        "skipped_reports": sorted(skipped),
        # Only the headline counts are stamped onto a run report; the full
        # facility blocks live in summary.v1.json and report.md.
        "facility_counts": {
            "free_claim_measurement_groups": (
                ((outcome.get("facility_facts") or {}).get("free_claim") or {}).get(
                    "measurement_groups", 0
                )
            ),
            "order_display_samples": (
                (
                    (outcome.get("facility_facts") or {}).get("order_display_rank") or {}
                ).get("samples", 0)
            ),
            "save_read_timing_samples": (
                (
                    (outcome.get("facility_facts") or {}).get("save_read_timing") or {}
                ).get("samples", 0)
            ),
            "guard_events": (outcome.get("guard_events") or {}).get("total", 0),
        },
        "field_coverage_rate_before": before["field_coverage_rate"],
        "field_coverage_rate_after": after["field_coverage_rate"],
        "verified_rows_before": before["row_coverage"],
        "verified_rows_after": after["row_coverage"],
        "contradicted_results": outcome["contradicted_results"],
        "generated_at": (outcome["summary"] or {}).get("generated_at", ""),
    }


def run_hook(
    report_path: str | Path | None = None, paths: dict[str, Path] | None = None
) -> dict[str, Any]:
    """Refresh the ledger and stamp ``crossref_delta`` onto a run report.

    Any failure is reported as an alert rather than raised: F6-02 requires the
    live run result to stand on its own even when cross-referencing breaks.
    """

    try:
        delta = refresh(paths)
    except Exception as exc:  # noqa: BLE001 - the hook must never fail a run
        delta = {"ok": False, "alert": f"{type(exc).__name__}: {exc}"}
    if report_path:
        target = Path(report_path)
        if target.is_file():
            try:
                report = json.loads(target.read_text(encoding="utf-8"))
                report["crossref_delta"] = delta
                target.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
            except (OSError, ValueError) as exc:
                delta = dict(delta, report_write_alert=f"{type(exc).__name__}: {exc}")
        else:
            delta = dict(delta, report_write_alert=f"report not found: {target}")
    return delta
