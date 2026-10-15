"""Command-line entry point for the decompiled config importer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .importer import format_summary, import_configs, import_flat_module


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--module",
        type=Path,
        help=(
            "import one Lua module whose return is a flat key/value map instead "
            "of the *Config*.lua batch; --source is then the file itself"
        ),
    )
    parser.add_argument(
        "--columns",
        default="Type,Value",
        help="key,value column names given to a --module import",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.module is not None or args.source.is_file():
        columns = [part.strip() for part in str(args.columns).split(",")]
        if len(columns) != 2 or not all(columns):
            raise SystemExit("--columns needs exactly two non-empty names")
        flat = import_flat_module(
            args.module or args.source,
            args.out,
            key_column=columns[0],
            value_column=columns[1],
        )
        if args.as_json:
            print(json.dumps(flat.as_dict(), ensure_ascii=False, sort_keys=True))
        else:
            print(
                f"table={flat.table} rows={flat.row_count} "
                f"columns={','.join(flat.columns)} sha256={flat.source_sha256}"
            )
        return 0
    summary = import_configs(args.source, args.out)
    if args.as_json:
        print(json.dumps(summary.as_dict(), ensure_ascii=False, sort_keys=True))
    else:
        print(format_summary(summary))
    return summary.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
