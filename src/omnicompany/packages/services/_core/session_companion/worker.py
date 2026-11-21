# [OMNI] origin=codex domain=services/session-companion ts=2026-08-11 type=service
# [OMNI] material_id="material:services.session-companion.worker.py"
"""Detached worker entry for draining the companion event queue."""

from __future__ import annotations

import argparse
import json

from .service import CompanionService
from .store import CompanionStore, LockBusy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--drain", action="store_true")
    parser.add_argument("--max-events", type=int, default=8)
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args(argv)
    store = CompanionStore()
    try:
        with store.lock("worker-supervisor", stale_after_s=1800):
            service = CompanionService(store)
            if args.drain:
                result = service.run_pending(max_events=args.max_events, concurrency=args.concurrency)
            else:
                item = service.run_once()
                result = [] if item is None else [item]
    except LockBusy:
        return 0
    print(json.dumps(result, ensure_ascii=False))
    return 0 if all(item.get("state") == "done" for item in result) else 1


if __name__ == "__main__":
    raise SystemExit(main())

