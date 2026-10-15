"""Command-line entry point for configuration-derived design artifacts."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from .config_facts import ConfigFacts
from .crossref_ledger import write_crossref_ledger
from .evidence import EvidenceBundle
from .render_docs import utc_now, write_design_documents


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--out-docs", type=Path, required=True)
    parser.add_argument("--out-ledger", type=Path, required=True)
    parser.add_argument(
        "--crossref-dir",
        type=Path,
        help="directory holding facts.v1.ndjson, the ledger and the coverage products",
    )
    parser.add_argument("--records-dir", type=Path, help="plan records directory")
    parser.add_argument("--navigation", type=Path, help="navigation survey json")
    parser.add_argument("--bi-source", type=Path, help="decompiled telemetry manager source")
    parser.add_argument("--energy-source", type=Path, help="decompiled energy model source")
    parser.add_argument(
        "--tutorial-state",
        type=Path,
        help="offline save projection of the tutorial table",
    )
    parser.add_argument(
        "--metrics",
        type=Path,
        help="efficiency dashboard json, read for the wall clock and the audits",
    )
    parser.add_argument(
        "--order-geometry",
        type=Path,
        help="order tray calibration json",
    )
    parser.add_argument(
        "--feasibility-dir",
        type=Path,
        help="directory holding the run reports the verified layer cites",
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    facts = ConfigFacts.load(args.config_dir)
    generated_at = utc_now()
    evidence = None
    if any(
        value is not None
        for value in (
            args.crossref_dir,
            args.records_dir,
            args.navigation,
            args.bi_source,
            args.energy_source,
            args.tutorial_state,
            args.metrics,
            args.order_geometry,
            args.feasibility_dir,
        )
    ):
        evidence = EvidenceBundle.load(
            crossref_dir=args.crossref_dir,
            navigation=args.navigation,
            records_dir=args.records_dir,
            bi_source=args.bi_source,
            energy_source=args.energy_source,
            tutorial_state=args.tutorial_state,
            metrics=args.metrics,
            order_geometry=args.order_geometry,
            feasibility_dir=args.feasibility_dir,
        )
    ledger = write_crossref_ledger(
        facts, args.out_ledger, generated_at=generated_at, evidence=evidence
    )
    if evidence is not None:
        # The index quotes the ledger this run just wrote, not the previous one.
        evidence = replace(evidence, ledger=ledger)
    payloads = write_design_documents(
        facts, args.out_docs, generated_at=generated_at, evidence=evidence
    )
    result = {
        "ok": True,
        "documents": sorted(payloads),
        "document_count": len(payloads),
        "evidence_levels": {
            slug: payload["evidence_level"] for slug, payload in sorted(payloads.items())
        },
        "live_verified_rows": {
            slug: payload["live_verified_rows"]
            for slug, payload in sorted(payloads.items())
            if "live_verified_rows" in payload
        },
        "ledger": str(args.out_ledger),
        "ledger_summary": ledger["summary"],
    }
    if args.json_output:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(f"generated {len(payloads)} documents and {args.out_ledger}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
