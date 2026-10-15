"""CLI for the Romantic Restaurant run-metrics dashboard."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .build import build


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build",))
    parser.add_argument(
        "--out-dir",
        type=Path,
        help="write the products here instead of the canonical metrics directory",
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = build(out_dir=args.out_dir)
    if args.json_output:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(f"{args.command}: {json.dumps(result, ensure_ascii=False, sort_keys=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
