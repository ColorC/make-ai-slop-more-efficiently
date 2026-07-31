from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from omnicompany.packages.domains.game_observatory.gateway import (
    DeviceGateway,
    GatewayError,
    LeaseConflict,
    LofaRemoteAdbProvider,
    TargetProvider,
)
from omnicompany.packages.domains.game_observatory.models import TargetRecord
from omnicompany.packages.domains.game_observatory.store import ObservatoryStore


class StaticProvider(TargetProvider):
    name = "static-test"

    def __init__(self, records):
        self.records = records

    def discover(self):
        return self.records


def _target() -> TargetRecord:
    return TargetRecord(
        id="device://adb/test-serial",
        provider="static-test",
        endpoint="test-serial",
        kind="adb",
        label="Test Android",
        status="online",
        capabilities=["pixel", "touch"],
    )


def test_registry_refresh_and_exclusive_expiring_leases(tmp_path):
    store = ObservatoryStore(tmp_path)
    current = [datetime(2026, 7, 13, 1, 0, tzinfo=timezone.utc)]
    gateway = DeviceGateway(
        store,
        [StaticProvider([_target()])],
        clock=lambda: current[0],
    )
    targets = gateway.refresh()
    assert [item.id for item in targets] == ["device://adb/test-serial"]
    assert gateway.target_infos(refresh_if_empty=False)[0].metadata["provider"] == "static-test"

    lease = gateway.acquire(_target().id, "researcher-a", ttl_seconds=15)
    assert gateway.validate(_target().id, lease.token).id == lease.id
    with pytest.raises(LeaseConflict, match="already leased"):
        gateway.acquire(_target().id, "researcher-b")

    renewed = gateway.renew(lease.token, ttl_seconds=30)
    assert datetime.fromisoformat(renewed.expires_at) == current[0] + timedelta(seconds=30)
    current[0] += timedelta(seconds=31)
    with pytest.raises(GatewayError, match="valid active lease"):
        gateway.validate(_target().id, lease.token)
    replacement = gateway.acquire(_target().id, "researcher-b")
    released = gateway.release(replacement.token)
    assert released.status == "released"
    event_types = {item["event_type"] for item in store.list_gateway_events()}
    assert {"provider_refresh", "lease_acquired", "lease_renewed", "lease_expired", "lease_released"} <= event_types


def test_lofa_remote_provider_preserves_record_and_probes_reachability(tmp_path):
    record = tmp_path / "lofa_device.json"
    record.write_text(
        json.dumps({"ip": "10.0.0.25", "adb_port": 5555, "last_seen": 1783870000}),
        encoding="utf-8",
    )
    online = LofaRemoteAdbProvider(record, probe=lambda host, port: (host, port) == ("10.0.0.25", 5555))
    target = online.discover()[0]
    assert target.id == "device://adb-tcp/10.0.0.25:5555"
    assert target.status == "online"
    assert target.metadata["direct_adb_reachable"] is True

    offline = LofaRemoteAdbProvider(record, probe=lambda _host, _port: False).discover()[0]
    assert offline.status == "offline"
    assert offline.capabilities == ["remote"]