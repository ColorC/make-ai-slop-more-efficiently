"""Regression coverage for the declared Python 3.10 public runtime contract."""

import asyncio
from datetime import datetime, timezone
from enum import auto
import json
import sys
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from omnicompany.core.pycompat import UTC, NotRequired, StrEnum, TypedDict


def test_utc_and_real_runtime_consumers_import():
    from omnicompany.runtime.agent_runtime import structured_runner
    from omnicompany.packages.services._core.team_supervisor import run

    assert UTC is timezone.utc
    assert datetime(2026, 1, 1, tzinfo=UTC).utcoffset().total_seconds() == 0
    assert structured_runner.UTC is UTC
    assert run.UTC is UTC


def test_not_required_preserves_typed_dict_optional_keys():
    from omnicompany.packages.services._core.material_core.types import (
        Material, MaterialSourceRef,
    )

    class Example(TypedDict):
        required: str
        optional: NotRequired[str]

    assert Example.__required_keys__ == frozenset({"required"})
    assert Example.__optional_keys__ == frozenset({"optional"})
    assert MaterialSourceRef.__required_keys__ == frozenset({"kind", "ref"})
    assert MaterialSourceRef.__optional_keys__ == frozenset({"selector"})
    assert "metadata" in Material.__optional_keys__
    assert "metadata" not in Material.__required_keys__


def test_str_enum_preserves_auto_string_format_alias_and_decoding():
    class Example(StrEnum):
        Mixed_CASE = auto()
        EXPLICIT = "explicit"
        ALIAS = "explicit"
        DECODED = b"decoded", "utf-8"

    assert Example.Mixed_CASE.value == "mixed_case"
    assert str(Example.Mixed_CASE) == "mixed_case"
    assert format(Example.Mixed_CASE, ">12") == "  mixed_case"
    assert f"{Example.EXPLICIT}" == "explicit"
    assert json.dumps(Example.EXPLICIT) == '\"explicit\"'
    assert Example.ALIAS is Example.EXPLICIT
    assert Example.DECODED.value == "decoded"
    assert Example("explicit") is Example.EXPLICIT
    assert repr(Example.EXPLICIT) == "<Example.EXPLICIT: 'explicit'>"


@pytest.mark.parametrize("value", [1, b"bytes", (b"bytes", 1), (b"bytes", "utf-8", 1)])
def test_str_enum_rejects_non_string_values_or_invalid_decode_arguments(value):
    with pytest.raises(TypeError):
        StrEnum("Invalid", {"VALUE": value})


class _Socket:
    def __init__(self, mode):
        self.mode = mode
        self.started = asyncio.Event()
        self.receive_cancelled = False
        self.closed = False
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        self.closed = True

    async def recv(self):
        self.started.set()
        if self.mode == "snapshot":
            return json.dumps({"type": "snapshot_end"})
        if self.mode == "exit":
            return json.dumps({"type": "exit", "reason": "fixture exited"})
        try:
            await asyncio.Future()
        finally:
            self.receive_cancelled = True

    async def send(self, data):
        self.sent.append(data)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["snapshot", "exit", "timeout", "cancel"])
async def test_managed_pane_snapshot_timeout_and_external_cancellation(monkeypatch, mode):
    from omnicompany.dashboard.ccdaemon import api
    from omnicompany.packages.services._focus import meegle_dispatch

    socket = _Socket(mode)
    monkeypatch.setattr(api, "read_status", lambda: SimpleNamespace(serving=True, port=1))
    monkeypatch.setitem(sys.modules, "websockets", SimpleNamespace(
        connect=lambda *_args, **_kwargs: socket, WebSocketException=OSError,
    ))
    original_wait_for = asyncio.wait_for
    limits = []

    async def bounded(awaitable, timeout):
        limits.append(timeout)
        return await original_wait_for(awaitable, timeout=0.02 if timeout == 10 else timeout)

    monkeypatch.setattr(asyncio, "wait_for", bounded)
    task = asyncio.create_task(meegle_dispatch._submit_managed_pane("fixture", "hello"))
    if mode == "cancel":
        await original_wait_for(socket.started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert socket.receive_cancelled
    else:
        result = await task
        assert result["submitted"] is (mode == "snapshot")
        if mode == "snapshot":
            assert json.loads(socket.sent[0])["data"] == "\x1b[200~hello\x1b[201~\r"
        elif mode == "timeout":
            assert not result["ok"]
            assert "error" in result
            assert socket.receive_cancelled
        else:
            assert result["error"] == "fixture exited"
    assert limits == [10]
    assert socket.closed
    if mode != "snapshot":
        assert socket.sent == []


def test_terminal_probe_timeout_cancels_reader_and_closes_connections(monkeypatch, tmp_path):
    from omnicompany.cli.commands import cc

    sockets = []

    class ProbeSocket(_Socket):
        async def ping(self):
            return asyncio.Future()

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await self.recv()

    def connect(*_args, **_kwargs):
        socket = ProbeSocket("timeout")
        sockets.append(socket)
        return socket

    monkeypatch.setitem(sys.modules, "websockets", SimpleNamespace(connect=connect))
    monkeypatch.setattr(cc, "_pty_daemon_base", lambda: "http://127.0.0.1:1")
    original_wait_for = asyncio.wait_for
    limits = []

    async def bounded(awaitable, timeout):
        limits.append(timeout)
        return await original_wait_for(awaitable, timeout=0.02 if timeout == 20 else timeout)

    monkeypatch.setattr(asyncio, "wait_for", bounded)
    receipt = tmp_path / "probe.json"
    result = CliRunner().invoke(cc.cc_session_probe, ["0123456789abcdef", "--receipt", str(receipt)])

    assert result.exit_code == 1
    assert "terminal probe failed" in result.output
    assert limits.count(20) == 2
    assert len(sockets) == 2
    assert all(socket.closed and socket.receive_cancelled for socket in sockets)
    assert not receipt.exists()
