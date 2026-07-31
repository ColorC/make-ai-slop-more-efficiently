# [OMNI] origin=codex domain=services/_core/team_builder/scripts ts=2026-07-25T00:00:00Z type=tool
# [OMNI] summary="CLI for deterministic dry-run registration-plan location repair"
"""Rebuild one Team Builder dry-run registration plan without model calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from omnicompany.packages.services._core.team_builder.registration_plan_rebuild import (
    rebuild_registration_plan_location,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--target-package-path", required=True)
    parser.add_argument("--halt-on-non-pass", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    plan = json.loads(args.input.read_text(encoding="utf-8"))
    rebuilt = rebuild_registration_plan_location(
        plan,
        target_package_path=args.target_package_path,
        halt_on_non_pass=args.halt_on_non_pass,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(rebuilt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
