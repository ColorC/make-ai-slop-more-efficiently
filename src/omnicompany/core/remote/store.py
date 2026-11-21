# [OMNI] origin=claude-code domain=omnicompany/core/remote ts=2026-10-17T00:00:00+08:00 type=lib status=active
# [OMNI] summary="omni remote 本机侧: 台账 ops.jsonl、回执目录、本机中转批次与本机备份。"
"""Local side of ``omni remote``: ledger, receipts, local transport batches, local backups.

Default home is ``%LOCALAPPDATA%\\OmniRemote`` (same convention as ``OmniCleanup``);
``OMNI_REMOTE_HOME`` overrides it. Layout::

    ops.jsonl                         ledger, one line per operation
    receipts/<YYYYMMDD>/<op_id>/      request.json, command-N.ps1, native-N.json, result.json
    staging/<op_id>/                  local transport batch, removed file by file after use
    backup/<op_id>/                   local files replaced by ``pull`` (never deleted)
"""
from __future__ import annotations

import json
import os
import secrets
import stat
import time
from datetime import datetime
from pathlib import Path
from typing import Any


def home() -> Path:
    raw = os.environ.get("OMNI_REMOTE_HOME", "").strip()
    if raw:
        return Path(raw)
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) / "OmniRemote") if base else (Path.home() / ".omni-remote")


def ledger_path() -> Path:
    return home() / "ops.jsonl"


def new_op_id() -> str:
    return f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


def receipt_dir(op_id: str) -> Path:
    path = home() / "receipts" / op_id[:8] / op_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def append_ledger(entry: dict[str, Any]) -> Path:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": datetime.now().isoformat(timespec="seconds"), **entry}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def read_ledger() -> list[dict[str, Any]]:
    path = ledger_path()
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def assert_local_plain(path: Path) -> Path:
    """Refuse symlinks/junctions anywhere on the path and hard-linked files (never write through them)."""
    full = Path(os.path.abspath(path))
    for item in (full, *full.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"omni-remote: local link/reparse point preserved: {item}")
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise ValueError(f"omni-remote: local hard-linked file preserved: {item}")
    return full


class LocalBatch:
    """A per-operation local transport directory. Only files this object wrote are removed."""

    def __init__(self, op_id: str, purpose: str) -> None:
        self.root = assert_local_plain(home() / "staging" / op_id)
        if self.root.exists():
            raise ValueError(f"omni-remote: local batch already exists: {self.root}")
        self.root.mkdir(parents=True)
        self.files: list[Path] = []
        self.purpose = purpose

    def path(self, name: str) -> Path:
        if Path(name).name != name:
            raise ValueError(f"omni-remote: batch file must be a bare name: {name}")
        target = self.root / name
        self.files.append(target)
        return target

    def cleanup(self) -> dict[str, Any]:
        unknown = [p for p in self.root.iterdir() if p not in self.files] if self.root.exists() else []
        if unknown:
            return {"status": "kept", "path": str(self.root), "reason": f"unknown objects: {[str(p) for p in unknown]}"}
        for item in self.files:
            assert_local_plain(item)
            if item.is_file():
                item.unlink()
        if self.root.exists():
            self.root.rmdir()
        return {"status": "removed", "path": str(self.root)}


def monotonic() -> float:
    return time.monotonic()
