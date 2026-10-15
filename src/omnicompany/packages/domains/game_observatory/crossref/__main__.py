"""CLI for Romantic Restaurant evidence cross-reference indexing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from ..delegation.save_channel import DEFAULT_LUA_DIR
from .evidence_index import index_evidence, read_facts
from .facility import build_facility_view
from .report import render_report_md, write_report_md, write_summary
from .row_coverage import build_row_coverage
from .rules import apply_rules
from .run_hook import default_paths, run_hook
from .save_schema import (
    build_from_encrypted_save,
    read_save_schema_coverage,
    write_save_schema_coverage,
)
from .writeback import writeback_ledger


def _defaults() -> dict[str, Path]:
    return default_paths()


def latest_save_copy(feasibility_root: Path) -> Path | None:
    """The most recently captured save copy already on disk.

    The save channel deletes the plaintext it decrypts and the encrypted pull
    directory with it, so the copies that survive are the ones the feasibility
    probes kept.  Reconciling schema never needs the newest gameplay state, and
    picking a stored copy keeps this command off the device entirely.
    """

    copies = sorted(
        (path for path in feasibility_root.rglob("db.sqlite3") if path.is_file()),
        key=lambda path: (path.stat().st_mtime, path.as_posix()),
    )
    return copies[-1] if copies else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("index", "writeback", "report", "run-hook", "save-schema")
    )
    parser.add_argument(
        "report_path",
        nargs="?",
        help="run report to stamp with crossref_delta (run-hook only)",
    )
    parser.add_argument(
        "--save",
        type=Path,
        help="encrypted save copy to reconcile (save-schema only; "
        "defaults to the most recent copy under feasibility/)",
    )
    parser.add_argument(
        "--lua-dir",
        type=Path,
        default=DEFAULT_LUA_DIR,
        help="decompiled Lua directory used to resolve ConfigCreator declarations",
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def _save_schema(args: argparse.Namespace, paths: dict[str, Path]) -> dict[str, object]:
    save = args.save or latest_save_copy(paths["feasibility"])
    if save is None:
        return {"ok": False, "error": "no save copy found under feasibility/"}
    payload = build_from_encrypted_save(
        save,
        config_path=paths["config"] / "DBTableConfigs.json",
        lua_dir=args.lua_dir if Path(args.lua_dir).is_dir() else None,
        save_ref=_relative_to_repo(save),
    )
    write_save_schema_coverage(payload, paths["save_schema"])
    return {
        "ok": True,
        **payload["totals"],
        "save_reader_read_tables": payload["save_reader"]["read_table_count"],
        "output": str(paths["save_schema"]),
    }


def _relative_to_repo(path: Path) -> str:
    from .run_hook import repo_root

    try:
        return path.resolve().relative_to(repo_root()).as_posix()
    except ValueError:
        return path.as_posix()


def _report(paths: dict[str, Path]) -> dict[str, object]:
    ledger = json.loads(paths["ledger"].read_text(encoding="utf-8"))
    facts = read_facts(paths["facts"])
    facts_by_id = {fact.fact_id: fact for fact in facts}
    results = apply_rules(facts, paths["config"])
    provisional = write_summary(ledger, paths["summary"])
    row_coverage = build_row_coverage(
        results,
        facts_by_id,
        paths["config"],
        facts=facts,
        generated_at=provisional.get("generated_at", ""),
    )
    facility = build_facility_view(facts)
    summary = write_summary(ledger, paths["summary"], row_coverage, facility)
    fact_kind_counts: dict[str, int] = {}
    for fact in facts:
        fact_kind_counts[fact.kind] = fact_kind_counts.get(fact.kind, 0) + 1
    write_report_md(
        render_report_md(
            summary,
            row_coverage,
            results,
            ledger,
            fact_kind_counts,
            save_schema=read_save_schema_coverage(paths["save_schema"]),
            facility=facility,
        ),
        paths["report_md"],
    )
    return {
        **summary,
        "row_coverage_path": str(paths["row_coverage"]),
        "report_md": str(paths["report_md"]),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = _defaults()
    if args.command == "index":
        skipped: list[str] = []
        facts = index_evidence(paths["feasibility"], paths["facts"], skipped=skipped)
        kinds: dict[str, int] = {}
        for fact in facts:
            kinds[fact.kind] = kinds.get(fact.kind, 0) + 1
        result = {
            "ok": True,
            "facts": len(facts),
            "fact_kinds": dict(sorted(kinds.items())),
            "skipped_reports": sorted(skipped),
            "output": str(paths["facts"]),
        }
    elif args.command == "writeback":
        result = writeback_ledger(
            facts_path=paths["facts"],
            ledger_path=paths["ledger"],
            config_root=paths["config"],
            summary_path=paths["summary"],
            docs_dir=paths["docs"],
            observations_path=paths["observations"],
            row_coverage_path=paths["row_coverage"],
            report_md_path=paths["report_md"],
            save_schema_path=paths["save_schema"],
        )
        result["ok"] = True
    elif args.command == "save-schema":
        result = _save_schema(args, paths)
    elif args.command == "run-hook":
        result = run_hook(args.report_path, paths)
    else:
        result = _report(paths)
        result["ok"] = True
    if args.json_output:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(f"{args.command}: {json.dumps(result, ensure_ascii=False, sort_keys=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
