from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnicompany.packages.domains.game_observatory import api as api_module
from omnicompany.packages.domains.game_observatory.api import game_observatory_router
from omnicompany.packages.domains.game_observatory.runtime import GameObservatory


def test_full_runtime_validation_with_target_fixture(tmp_path, monkeypatch):
    facility = GameObservatory(tmp_path)
    monkeypatch.setattr(
        facility,
        "discover_targets",
        lambda: [{"id": "device://adb/test", "kind": "adb", "label": "test", "status": "online", "capabilities": ["pixel"], "metadata": {}}],
    )
    result = facility.validate()
    assert result["ok"] is True
    assert result["counts"]["reports"] == 2
    assert result["counts"]["runs"] == 2
    assert (tmp_path / "exports" / "catalog.json").is_file()


def test_public_api_and_site_are_connected(tmp_path, monkeypatch):
    facility = GameObservatory(tmp_path)
    facility.bootstrap()
    monkeypatch.setattr(api_module, "_facility", lambda: facility)
    app = FastAPI()
    app.include_router(game_observatory_router)
    client = TestClient(app)

    health = client.get("/api/game-observatory/health")
    assert health.status_code == 200
    catalog = client.get("/api/game-observatory/catalog").json()
    assert len(catalog["reports"]) == 2
    afk = next(item for item in catalog["reports"] if item["game_id"] == "afk-journey")
    private = next(item for item in afk["sources"] if not item["public"])
    assert private["url"] == "#internal-source-withheld"
    assert "D:/" not in str(private)
    detail = client.get("/api/game-observatory/reports/afk-journey-hero-upgrade")
    assert detail.status_code == 200
    assert detail.json()["flow"][0]["id"] == "afk.flow.hero-entry"
    site = client.get("/game-observatory/")
    assert site.status_code == 200
    assert "游戏观测站" in site.text


def test_remote_mutation_requires_editor_token(tmp_path, monkeypatch):
    facility = GameObservatory(tmp_path)
    monkeypatch.setattr(api_module, "_facility", lambda: facility)
    app = FastAPI()
    app.include_router(game_observatory_router)
    client = TestClient(app, client=("192.168.1.50", 50000))
    response = client.post("/api/game-observatory/bootstrap")
    assert response.status_code == 403