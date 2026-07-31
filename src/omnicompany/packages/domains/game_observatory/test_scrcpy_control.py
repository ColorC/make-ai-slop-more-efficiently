from __future__ import annotations

import struct

import pytest

from omnicompany.packages.domains.game_observatory.scrcpy_control import (
    ScrcpyControlError,
    ScrcpyControlTransport,
)


def _transport(tmp_path) -> ScrcpyControlTransport:
    server = tmp_path / "scrcpy-win64-v4.0" / "scrcpy-server"
    server.parent.mkdir(parents=True)
    server.write_bytes(b"fixture")
    return ScrcpyControlTransport(
        adb_path=tmp_path / "adb.exe",
        serial="test-serial",
        width=1080,
        height=1920,
        server_path=server,
    )


def test_touch_message_matches_scrcpy_v4_control_protocol(tmp_path):
    transport = _transport(tmp_path)

    class FakeSocket:
        payload = b""

        def sendall(self, payload):
            self.payload += payload

    fake = FakeSocket()
    transport._socket = fake

    transport._send_touch(transport.ACTION_DOWN, 7, 540, 960, 1.0)

    assert len(fake.payload) == 32
    assert struct.unpack(">BBqiiHHHII", fake.payload) == (
        2,
        0,
        7,
        540,
        960,
        1080,
        1920,
        65535,
        0,
        0,
    )


def test_pinch_keeps_both_pointers_in_one_session_and_releases_secondary_first(
    tmp_path, monkeypatch
):
    transport = _transport(tmp_path)
    events = []
    monkeypatch.setattr(
        transport,
        "_send_touch",
        lambda action, pointer_id, x, y, pressure: events.append(
            (action, pointer_id, x, y, pressure)
        ),
    )
    monkeypatch.setattr(
        "omnicompany.packages.domains.game_observatory.scrcpy_control.time.sleep",
        lambda _seconds: None,
    )

    transport.pinch(
        center=(540, 960),
        percent=0.4,
        duration=0.6,
        steps=3,
        in_or_out="out",
    )

    assert events[:2] == [
        (transport.ACTION_DOWN, 0, 538, 958, 1.0),
        (transport.ACTION_DOWN, 1, 542, 962, 1.0),
    ]
    assert events[-2:] == [
        (transport.ACTION_UP, 1, 756, 1344, 0.0),
        (transport.ACTION_UP, 0, 324, 576, 0.0),
    ]
    assert len([event for event in events if event[0] == transport.ACTION_MOVE]) == 6


def test_input_state_gate_detects_stale_synthetic_fingers(tmp_path, monkeypatch):
    transport = _transport(tmp_path)
    dump = """
      mDeviceStates=-1:[touchingPointers=[Pointer(id=0, FINGER), Pointer(id=1, FINGER)]]
      InputState: mMotionMementos: {deviceId=-1, hovering=0, downTime=1000},
    """
    monkeypatch.setattr(transport, "_adb", lambda *_args, **_kwargs: dump)

    state = transport.input_state()

    assert state["idle"] is False
    assert len(state["active_lines"]) == 2
    with pytest.raises(ScrcpyControlError, match="active at after pinch"):
        transport.assert_input_idle("after pinch")


def test_input_state_gate_accepts_empty_pointer_state(tmp_path, monkeypatch):
    transport = _transport(tmp_path)
    monkeypatch.setattr(
        transport,
        "_adb",
        lambda *_args, **_kwargs: "mDeviceStates=-1:[touchingPointers=[]]",
    )

    assert transport.assert_input_idle("before multi-touch") == {
        "idle": True,
        "active_lines": [],
    }