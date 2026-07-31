"""Operate the Sanguo seven-natural-day ledger through the canonical local DB."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from ..runtime import GameObservatory
from .sanguo_daily_continuity import (
    RecordSanguoDailyDutyCommand,
    SanguoDailyContinuityError,
    SanguoDailyContinuityLedger,
    SanguoDailyStateCommand,
    SealSanguoDailyContinuityCommand,
)
from .store import AIPlayerStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="Observatory 根目录；省略时使用正式本地根")
    subparsers = parser.add_subparsers(dest="operation", required=True)

    list_parser = subparsers.add_parser("list", help="列出环境中的连续日账")
    list_parser.add_argument("--environment-id", required=True)

    for operation in ("show", "assess", "schedule"):
        read_parser = subparsers.add_parser(operation)
        read_parser.add_argument("--environment-id", required=True)
        read_parser.add_argument("--continuity-run-id", required=True)

    for operation in ("record-duty", "interrupt", "resume", "seal"):
        mutation_parser = subparsers.add_parser(operation)
        mutation_parser.add_argument(
            "--command",
            type=Path,
            required=True,
            help="符合公开 JSON Schema 的 UTF-8 JSON 命令文件",
        )
    return parser


def _dump(value: Any) -> None:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _command(path: Path, model: type[BaseModel]) -> BaseModel:
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _read_projection(
    ledger: SanguoDailyContinuityLedger,
    operation: str,
    environment_id: str,
    continuity_run_id: str,
) -> Any:
    if operation == "assess":
        return ledger.assess(environment_id, continuity_run_id)
    if operation == "schedule":
        return ledger.schedule(environment_id, continuity_run_id)
    days = ledger.list_days(environment_id, continuity_run_id)
    return {
        "days": [day.model_dump(mode="json", by_alias=True) for day in days],
        "events": {
            day.natural_day.isoformat(): [
                event.model_dump(mode="json", by_alias=True)
                for event in ledger.list_events(
                    environment_id, continuity_run_id, day.natural_day
                )
            ]
            for day in days
        },
        "schedule": ledger.schedule(
            environment_id, continuity_run_id
        ).model_dump(mode="json", by_alias=True),
        "assessment": ledger.assess(
            environment_id, continuity_run_id
        ).model_dump(mode="json", by_alias=True),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    facility = GameObservatory(args.root)
    ledger = SanguoDailyContinuityLedger(AIPlayerStore(facility.store))
    try:
        if args.operation == "list":
            _dump(
                {
                    "environment_id": args.environment_id,
                    "continuity_run_ids": ledger.list_run_ids(args.environment_id),
                }
            )
            return 0
        if args.operation in {"show", "assess", "schedule"}:
            _dump(
                _read_projection(
                    ledger,
                    args.operation,
                    args.environment_id,
                    args.continuity_run_id,
                )
            )
            return 0
        command_models: dict[str, type[BaseModel]] = {
            "record-duty": RecordSanguoDailyDutyCommand,
            "interrupt": SanguoDailyStateCommand,
            "resume": SanguoDailyStateCommand,
            "seal": SealSanguoDailyContinuityCommand,
        }
        operations = {
            "record-duty": "record_duty",
            "interrupt": "interrupt",
            "resume": "resume",
            "seal": "seal",
        }
        command = _command(args.command, command_models[args.operation])
        result = getattr(ledger, operations[args.operation])(command)
        _dump(result)
        return 0
    except (SanguoDailyContinuityError, ValidationError, OSError, ValueError) as exc:
        code = getattr(exc, "code", type(exc).__name__)
        _dump({"ok": False, "code": code, "message": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
