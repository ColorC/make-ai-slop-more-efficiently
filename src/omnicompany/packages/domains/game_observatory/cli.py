from __future__ import annotations

import argparse
import json
from pathlib import Path

from .runtime import GameObservatory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="game-observatory")
    parser.add_argument("command", choices=["bootstrap", "validate", "capture", "export", "proof"])
    parser.add_argument("--root", type=Path)
    parser.add_argument("--serial", default="127.0.0.1:7555")
    args = parser.parse_args(argv)
    facility = GameObservatory(args.root)
    if args.command == "bootstrap":
        result = facility.bootstrap()
    elif args.command == "validate":
        result = facility.validate()
    elif args.command == "capture":
        result = facility.capture_device(args.serial)
    elif args.command == "export":
        result = {"ok": True, "path": str(facility.store.export_reports())}
    else:
        validation = facility.validate()
        result = {"ok": validation["ok"], "path": str(facility.proof_report(validation))}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())