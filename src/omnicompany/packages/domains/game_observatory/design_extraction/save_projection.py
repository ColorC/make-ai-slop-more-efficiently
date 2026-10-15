"""Project one read-only table out of an offline save copy into a JSON product.

The tutorial chain has three independent evidence sources: the decompiled
configuration table, the survey registration of what the device actually
showed, and the save itself, which records a state per tutorial identifier.
The third one lives inside an encrypted database, so the design generator
cannot read it directly; this module turns it into a small, key-free product
the generator can consume, and records where that product came from.

Nothing here touches a device.  The input is an existing offline snapshot, the
password comes from ``RR_SAVE_KEY_FILE`` and never reaches the output, and the
plaintext database is written to a caller-supplied scratch path and removed
again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
import uuid
from pathlib import Path
from typing import Any, Sequence

from ..delegation.sqlcipher_offline import decrypt_sqlcipher_file, load_save_password


SCHEMA = "game-observatory.save-projection.tutorial-state.v1"

# ETutorialState in synthesis/lua_code/Lua-Project/TutorialDefinition.lua.
STATE_TRUTH = "synthesis/lua_code/Lua-Project/TutorialDefinition.lua"
STATE_NAMES = {0: "NotStarted", 1: "Ongoing", 2: "Finished"}


def read_tutorial_rows(plaintext: str | Path) -> list[dict[str, Any]]:
    """Read ``tutorial`` as ``id``/``state`` pairs, sorted by identifier."""

    connection = sqlite3.connect(str(plaintext))
    try:
        rows = connection.execute("select id, state from tutorial").fetchall()
    finally:
        connection.close()
    return sorted(
        ({"id": str(identifier), "state": int(state)} for identifier, state in rows),
        key=lambda row: row["id"],
    )


def read_progress(plaintext: str | Path) -> dict[str, Any]:
    """Read the few ``user`` counters that date the snapshot.

    The tutorial states only mean something next to the progress the save had
    reached, so the projection carries that context instead of leaving a reader
    to guess how old the snapshot is.
    """

    connection = sqlite3.connect(str(plaintext))
    try:
        rows = connection.execute("select key, value from user").fetchall()
    finally:
        connection.close()
    values = {str(key): str(value) for key, value in rows}
    progress: dict[str, Any] = {}
    for key in ("User_Level", "exp_Give", "gold_Give", "gem_Give"):
        if key in values:
            try:
                progress[key] = int(values[key])
            except ValueError:
                progress[key] = values[key]
    return progress


def build_projection(
    rows: list[dict[str, Any]],
    *,
    source_ref: str,
    encrypted_sha256: str,
    progress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for row in rows:
        name = STATE_NAMES.get(row["state"], str(row["state"]))
        counts[name] = counts.get(name, 0) + 1
    return {
        "schema": SCHEMA,
        "save": {"source_ref": source_ref, "encrypted_sha256": encrypted_sha256},
        "table": "tutorial",
        "state_names": {str(value): name for value, name in sorted(STATE_NAMES.items())},
        "state_truth": STATE_TRUTH,
        "row_count": len(rows),
        "state_counts": dict(sorted(counts.items())),
        "snapshot_progress": dict(sorted((progress or {}).items())),
        "rows": rows,
    }


def project_tutorial_state(
    encrypted: str | Path, *, scratch_dir: str | Path | None = None
) -> dict[str, Any]:
    """Decrypt a save copy in scratch space and project its tutorial table."""

    source = Path(encrypted)
    scratch = Path(scratch_dir) if scratch_dir is not None else Path(tempfile.gettempdir())
    scratch.mkdir(parents=True, exist_ok=True)
    plaintext = scratch / f".tutorial-state.{uuid.uuid4().hex}.sqlite3"
    try:
        result = decrypt_sqlcipher_file(source, plaintext, password=load_save_password())
        rows = read_tutorial_rows(plaintext)
        progress = read_progress(plaintext)
    finally:
        plaintext.unlink(missing_ok=True)
    return build_projection(
        rows,
        source_ref=source.as_posix(),
        encrypted_sha256=result.encrypted_sha256,
        progress=progress,
    )


def _digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save", type=Path, required=True, help="encrypted save copy")
    parser.add_argument("--out", type=Path, required=True, help="projection to write")
    parser.add_argument("--scratch-dir", type=Path, help="where the plaintext copy goes")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = project_tutorial_state(args.save, scratch_dir=args.scratch_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "out": str(args.out),
                "row_count": payload["row_count"],
                "state_counts": payload["state_counts"],
                "digest": _digest(payload),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
