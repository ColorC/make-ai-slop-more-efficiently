from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from omnicompany.cli.commands import game as game_command


def test_agent_receipt_retries_transient_windows_permission_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = game_command._PlayerCLIContext(root=tmp_path, as_json=True)
    monkeypatch.setattr(
        game_command._PlayerCLIContext,
        "facility",
        lambda _self: SimpleNamespace(store=SimpleNamespace(root=tmp_path)),
    )
    original_write_bytes = Path.write_bytes
    attempts = 0
    sleeps: list[float] = []

    def flaky_write_bytes(path: Path, raw: bytes) -> int:
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise PermissionError("fixture transient Windows sharing violation")
        return original_write_bytes(path, raw)

    monkeypatch.setattr(Path, "write_bytes", flaky_write_bytes)
    monkeypatch.setattr(game_command.time, "sleep", sleeps.append)

    receipt = game_command._atomic_agent_receipt(
        context,
        category="actions",
        payload={"schema": "fixture.agent-receipt.v1", "ok": True},
    )

    assert attempts == 3
    assert sleeps == [0.05, 0.10]
    assert Path(receipt["path"]).read_bytes()
    assert len(receipt["sha256"]) == 64