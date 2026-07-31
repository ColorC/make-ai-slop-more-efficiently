from __future__ import annotations

import json

import pytest

from omnicompany.packages.domains.game_observatory.adapters import AdbAdapter, AdapterError, SourceFixtureAdapter
from omnicompany.packages.domains.game_observatory.models import BenchmarkTask, NormalizedAction, ObjectiveCheck
from omnicompany.packages.domains.game_observatory.store import ObservatoryStore


def test_source_fixture_runs_objective_checks(tmp_path):
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({"title": "fixture", "facts": {"done": True}}), encoding="utf-8")
    task = BenchmarkTask(
        id="task.fixture",
        title="fixture",
        start_state="start",
        goal="done",
        allowed_actions=[],
        reset_method="reload",
        checks=[ObjectiveCheck(id="done", description="done", expected=True)],
    )
    adapter = SourceFixtureAdapter(ObservatoryStore(tmp_path / "store"))
    adapter.connect(f"fixture://{fixture}")
    result = adapter.evaluate(task)
    assert result.status == "passed"
    assert result.checks[0].passed is True


def test_adb_action_guard_rejects_unsafe_text(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "omnicompany.packages.domains.game_observatory.adapters.resolve_adb",
        lambda explicit=None: tmp_path / "adb.exe",
    )
    adapter = AdbAdapter(ObservatoryStore(tmp_path / "store"))
    adapter.serial = "test"
    with pytest.raises(AdapterError, match="conservative ASCII"):
        adapter.act(NormalizedAction(type="text", text="hello; rm -rf /"))