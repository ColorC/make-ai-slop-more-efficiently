from __future__ import annotations

import sys
from pathlib import Path


EXPECTED = "ROUTE=event-bus-terra"


def main() -> int:
    if len(sys.argv) != 2:
        print("USAGE: verify_route.py <path>")
        return 2
    path = Path(sys.argv[1])
    actual = path.read_text(encoding="utf-8").rstrip("\r\n")
    if actual != EXPECTED:
        print("ROUTE=mismatch")
        print(f"EXPECTED={EXPECTED}")
        print(f"ACTUAL={actual}")
        return 7
    print("ROUTE=match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
