from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from omnicompany.bus.sqlite import SQLiteBus
from omnicompany.packages.services._core.guardian import sentinel


def test_default_sqlite_bus_writes_only_to_test_runtime_state(tmp_path: Path) -> None:
    bus = SQLiteBus()
    assert bus.db_path == tmp_path / "runtime-state" / "events.db"

    async def connect_and_close() -> None:
        await bus.connect()
        assert await bus.count() == 0
        await bus.close()

    asyncio.run(connect_and_close())
    assert bus.db_path.is_file()


@pytest.mark.parametrize("source", [None, "test-default-root"])
def test_default_sentinel_activity_uses_test_runtime_state(tmp_path: Path, source: str | None) -> None:
    if source is None:
        sentinel.write_activity_ts()
    else:
        sentinel.write_activity_ts(source=source)
    path = tmp_path / "runtime-state" / ".omni" / "core_activity_ts.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["source"] == (source or "unknown")
    assert payload["last_activity_ts"]


@pytest.mark.parametrize("positional", [False, True])
def test_explicit_sentinel_root_and_source_are_preserved(tmp_path: Path, positional: bool) -> None:
    explicit_root = tmp_path / "explicit-runtime"
    if positional:
        sentinel.write_activity_ts(explicit_root, "explicit-source")
    else:
        sentinel.write_activity_ts(root=explicit_root, source="explicit-source")
    payload = json.loads((explicit_root / ".omni/core_activity_ts.json").read_text(encoding="utf-8"))
    assert payload["source"] == "explicit-source"
    assert not (tmp_path / "runtime-state/.omni/core_activity_ts.json").exists()
