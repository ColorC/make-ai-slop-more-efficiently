from __future__ import annotations

import json
from pathlib import Path


EXPECTED_FILES = {
    "owned/target.txt",
    "scope.json",
    "user-changes/notes.txt",
    "verify_scope.py",
}


def read_exact(path: Path) -> str:
    return path.read_text(encoding="utf-8").rstrip("\r\n")


def main() -> int:
    root = Path(__file__).resolve().parent
    contract = json.loads((root / "scope.json").read_text(encoding="utf-8"))
    target = read_exact(root / contract["allowed_change"])
    protected = read_exact(root / contract["protected_path"])
    actual_files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }
    if target != contract["expected_target"]:
        print("TARGET=mismatch")
        return 3
    if protected != contract["protected_content"]:
        print("PROTECTED=mismatch")
        return 4
    if actual_files != EXPECTED_FILES:
        print(f"EXTRA_PATHS={len(actual_files - EXPECTED_FILES)}")
        print(f"MISSING_PATHS={len(EXPECTED_FILES - actual_files)}")
        return 5
    print("TARGET=match")
    print("PROTECTED=match")
    print("EXTRA_PATHS=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
