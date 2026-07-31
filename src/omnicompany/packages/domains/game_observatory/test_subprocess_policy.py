from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import omnicompany.packages.domains.game_observatory.adapters as adapters_module
import omnicompany.packages.domains.game_observatory.gateway as gateway_module
import omnicompany.packages.domains.game_observatory.scrcpy_control as scrcpy_module
from omnicompany.packages.domains.game_observatory.subprocess_policy import (
    headless_process_kwargs,
)


def _completed(args: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(args, 0, stdout=b"ok\
", stderr=b"")


def test_headless_process_policy_is_platform_safe() -> None:
    kwargs = headless_process_kwargs(creationflags=0x200)
    if sys.platform == "win32":
        assert kwargs["creationflags"] & getattr(
            subprocess, "CREATE_NO_WINDOW", 0x08000000
        )
        assert kwargs["creationflags"] & 0x200
        assert kwargs["startupinfo"].wShowWindow == getattr(subprocess, "SW_HIDE", 0)
    else:
        assert kwargs == {}


def test_adb_and_mumu_commands_use_headless_policy(tmp_path: Path, monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(args, **kwargs):
        calls.append(kwargs)
        return _completed(list(args))

    monkeypatch.setattr(adapters_module.subprocess, "run", fake_run)
    monkeypatch.setattr(gateway_module.subprocess, "run", fake_run)

    assert adapters_module._run(["adb.exe", "devices"]) == "ok\
"
    executable = tmp_path / "mumu-cli.exe"
    executable.touch()
    assert gateway_module.MumuCli(executable)._run("info") == "ok"

    if sys.platform == "win32":
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        assert all(int(call["creationflags"]) & no_window for call in calls)
        assert all("startupinfo" in call for call in calls)
    else:
        assert all("creationflags" not in call for call in calls)


def test_scrcpy_adb_commands_use_headless_policy(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(args, **kwargs):
        captured.update(kwargs)
        return _completed(list(args))

    monkeypatch.setattr(scrcpy_module.subprocess, "run", fake_run)
    transport = object.__new__(scrcpy_module.ScrcpyControlTransport)
    transport.adb_path = Path("adb.exe")
    transport.serial = "127.0.0.1:16384"

    assert transport._adb("shell", "true") == "ok\
"
    if sys.platform == "win32":
        assert int(captured["creationflags"]) & getattr(
            subprocess, "CREATE_NO_WINDOW", 0x08000000
        )
        assert "startupinfo" in captured
    else:
        assert "creationflags" not in captured