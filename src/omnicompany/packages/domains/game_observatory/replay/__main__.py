"""Command-line inspection and garbage collection for replay storage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .quota import QuotaManager
from .store import ReplayStore


def _emit(value: Any, *, as_json: bool) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif isinstance(value, list):
        value = [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in value]
    if as_json:
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m omnicompany.packages.domains.game_observatory.replay")
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status")
    _add_common_arguments(status)
    runs = commands.add_parser("runs")
    _add_common_arguments(runs)
    gc = commands.add_parser("gc")
    _add_common_arguments(gc)
    gc.add_argument("--dry", action="store_true")
    adoption = gc.add_mutually_exclusive_group()
    adoption.add_argument("--adopt-orphans", dest="adopt_orphans", action="store_true")
    adoption.add_argument("--no-adopt-orphans", dest="adopt_orphans", action="store_false")
    gc.set_defaults(adopt_orphans=True)
    gc.add_argument("--stale-after", type=int, default=600)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "status":
        _emit(ReplayStore(args.root).status(), as_json=args.as_json)
        return 0
    if args.command == "runs":
        _emit(ReplayStore(args.root).list_runs(), as_json=args.as_json)
        return 0
    quota = QuotaManager(args.root) if args.root is not None else QuotaManager(ReplayStore().root)
    _emit(
        quota.gc(
            dry=args.dry,
            adopt_orphans=args.adopt_orphans,
            stale_after_seconds=args.stale_after,
        ),
        as_json=args.as_json,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
