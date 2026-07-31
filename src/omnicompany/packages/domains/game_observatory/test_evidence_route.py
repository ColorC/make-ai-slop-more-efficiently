from __future__ import annotations

from omnicompany.packages.domains.game_observatory.evidence_route import (
    EvidenceRoute,
    EvidenceRouteRunner,
    EvidenceRouteStep,
)
from omnicompany.packages.domains.game_observatory.gateway import DeviceGateway
from omnicompany.packages.domains.game_observatory.models import (
    NormalizedAction,
    SourcePixelRect,
    TargetRecord,
)
from omnicompany.packages.domains.game_observatory.store import ObservatoryStore
from tests.domains.game_observatory.test_evidence_recorder import FakeEvidenceAdapter


def test_route_runner_repeats_complete_atomic_runs_and_exports_verification(
    tmp_path, monkeypatch
):
    store = ObservatoryStore(tmp_path / "store")
    target = TargetRecord(
        id="device://fake/route",
        provider="route-test",
        endpoint="route",
        kind="adb",
        label="Route fixture",
        status="online",
        capabilities=["pixel", "touch", "screenrecord"],
        metadata={"serial": "route"},
    )

    class Provider:
        name = "route-test"

        @staticmethod
        def discover():
            return [target]

    gateway = DeviceGateway(store, [Provider()])
    gateway.refresh()
    lease = gateway.acquire(target.id, "route-test", ttl_seconds=300)
    adapter = FakeEvidenceAdapter(store, tmp_path)
    monkeypatch.setattr(gateway, "_adb_adapter", lambda _target_id: adapter)
    route = EvidenceRoute(
        id="route-test",
        title="route test",
        target_id=target.id,
        viewport_width=100,
        viewport_height=200,
        game_id="afk-journey",
        build_scope_id="build.test",
        scope_id="scope.test",
        start_state="fake detail",
        end_state="fake detail",
        steps=[
            EvidenceRouteStep(
                id="tap",
                action=NormalizedAction(type="tap", x=25, y=50),
                target_name="fixture target",
                target_bounds=SourcePixelRect(x=10, y=20, width=50, height=60),
                settle_timeout_seconds=1,
                sample_interval_seconds=0.05,
                required_consecutive=2,
            )
        ],
    )

    result = EvidenceRouteRunner(store, gateway).run(
        route, lease.token, repetitions=3
    )

    assert result["ok"] is True
    assert result["requested_repetitions"] == 3
    assert result["completed_repetitions"] == 3
    assert len({item["evidence_run_id"] for item in result["runs"]}) == 3
    assert all(item["publishable"] for item in result["runs"])
    assert all(len(item["steps"]) == 1 for item in result["runs"])
    assert all(item["steps"][0]["actual_index"] == 1 for item in result["runs"])
    assert store.counts()["evidence_manifests"] == 3
    from pathlib import Path

    assert Path(result["verification_path"]).is_file()