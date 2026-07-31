from __future__ import annotations

import json

import pytest

from omnicompany.packages.domains.game_observatory.adapters import AdapterError, SourceFixtureAdapter
from omnicompany.packages.domains.game_observatory.benchmark import BenchmarkBundleWriter
from omnicompany.packages.domains.game_observatory.game_adapters import (
    AfkUnityExplorerAdapter,
    MinecraftVisualAdapter,
)
from omnicompany.packages.domains.game_observatory.models import BenchmarkTask, ObjectiveCheck
from omnicompany.packages.domains.game_observatory.store import ObservatoryStore


def _task() -> BenchmarkTask:
    return BenchmarkTask(
        id="task.adapter.contract",
        title="Adapter contract task",
        start_state="known-start",
        goal="known-goal",
        allowed_actions=["tap"],
        reset_method="fixture",
        checks=[ObjectiveCheck(id="level_changed", description="level changes", expected=True)],
        metadata={"target_state": "hero-upgrade", "max_steps": 8},
    )


def test_unity_adapter_uses_registered_dispatch_and_objective_contract(tmp_path):
    calls = []

    async def fake_dispatch(name, inputs, *, max_steps=None):
        calls.append((name, inputs, max_steps))
        return {"checks": {"level_changed": True}, "trace_id": "unity-trace-1"}

    store = ObservatoryStore(tmp_path)
    adapter = AfkUnityExplorerAdapter(
        store,
        dispatcher=fake_dispatch,
        bridge_probe=lambda _host, port: port == 18820,
    )
    target = adapter.connect("source://unity/afk?bridge_port=18820")
    assert target.status == "online"
    result = adapter.evaluate(_task())
    assert result.status == "passed"
    assert result.checks[0].passed is True
    assert calls[0][0] == "unity-playtest"
    assert calls[0][1]["target_state"] == "hero-upgrade"
    assert calls[0][2] == 8
    assert store.get_artifact(result.artifact_ids[0]) is not None


def test_unity_adapter_stops_cleanly_when_agentbridge_is_offline(tmp_path):
    adapter = AfkUnityExplorerAdapter(
        ObservatoryStore(tmp_path), bridge_probe=lambda _host, _port: False
    )
    adapter.connect("source://unity/afk?bridge_port=18820")
    result = adapter.evaluate(_task())
    assert result.status == "stopped"
    assert "not reachable" in result.error


def test_minecraft_visual_adapter_is_read_only_and_preserves_real_evidence(tmp_path, monkeypatch):
    store = ObservatoryStore(tmp_path)
    adapter = MinecraftVisualAdapter(store)
    status = {
        "server": {"online": True},
        "client": {"online": False},
        "world": {"snapshots": [{"hash": "snapshot-abc"}]},
    }

    def fake_json(path):
        if path == "/api/status":
            return status
        if path == "/api/screenshots/list":
            return [{"name": "evidence.png", "mtime": "2026-07-13"}]
        raise AssertionError(path)

    monkeypatch.setattr(adapter, "_json", fake_json)
    monkeypatch.setattr(adapter, "_bytes", lambda path, query=None: b"\x89PNG\r\
\x1a\
fixture")
    target = adapter.connect("minecraft://127.0.0.1:8332")
    observation = adapter.observe()
    assert target.status == "online"
    assert observation.frame.locator == "evidence.png"
    assert observation.runtime_state is not None
    assert adapter.checkpoint() == "snapshot-abc"
    with pytest.raises(AdapterError, match="disabled in read-only mode"):
        adapter.reset()
    assert adapter.evaluate(_task()).status == "stopped"


def test_benchmark_bundle_has_every_required_interchange_file(tmp_path):
    fixture = tmp_path / "fixture.json"
    fixture.write_text('{"title":"fixture","facts":{"level_changed":true}}', encoding="utf-8")
    store = ObservatoryStore(tmp_path / "store")
    adapter = SourceFixtureAdapter(store)
    target = adapter.connect(f"fixture://{fixture}")
    result = adapter.evaluate(_task())
    bundle = BenchmarkBundleWriter(tmp_path / "bundles").write(
        _task(), target, result, report_fragments=[{"kind": "test-fragment"}]
    )
    required = {
        "task.json",
        "target.json",
        "trace.jsonl",
        "objective-result.json",
        "report-fragments.json",
        "artifacts/manifest.json",
    }
    found = {
        str(path.relative_to(bundle)).replace("\\", "/")
        for path in bundle.rglob("*")
        if path.is_file()
    }
    assert required <= found
    objective = json.loads((bundle / "objective-result.json").read_text(encoding="utf-8"))
    assert objective["status"] == "passed"


def test_registered_legacy_unity_explore_binding_factory_exists():
    from omnicompany.packages.domains.demogame.unity_explore import run_pipeline

    assert callable(run_pipeline.build_explore_bindings)