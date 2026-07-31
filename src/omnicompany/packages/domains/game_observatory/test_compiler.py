from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnicompany.packages.domains.game_observatory import api as api_module
from omnicompany.packages.domains.game_observatory.api import game_observatory_router
from omnicompany.packages.domains.game_observatory.compiler import SemanticReportCompiler
from omnicompany.packages.domains.game_observatory.runtime import GameObservatory


def test_semantic_compiler_outputs_public_json_html_and_sitemap(tmp_path):
    facility = GameObservatory(tmp_path / "store")
    facility.bootstrap()
    reports = facility.store.list_reports()
    compiler = SemanticReportCompiler(tmp_path / "public")
    result = compiler.compile(reports, base_url="https://games.example.test")

    assert result["reports"] == 2
    assert len(result["sha256"]) == 64
    catalog = json.loads((tmp_path / "public" / "catalog.json").read_text(encoding="utf-8"))
    assert catalog["schema"] == "game-observatory.public-catalog.v1"
    afk = next(item for item in catalog["reports"] if item["game_id"] == "afk-journey")
    assert afk["compiled"]["stable_url"].startswith("/game-observatory/reports/")
    assert afk["claims"]
    assert all("D:/P4" not in str(source) for source in afk["sources"])
    semantic = (tmp_path / "public" / "afk-journey-hero-upgrade.html").read_text(encoding="utf-8")
    assert 'id="afk.flow.hero-entry"' in semantic
    assert 'id="claims"' in semantic
    assert "application/ld+json" in semantic
    sitemap = (tmp_path / "public" / "sitemap.xml").read_text(encoding="utf-8")
    assert "https://games.example.test/game-observatory/reports/afk-journey-hero-upgrade" in sitemap


def test_revision_diff_api_uses_stable_object_ids(tmp_path, monkeypatch):
    facility = GameObservatory(tmp_path)
    facility.bootstrap()
    report = facility.store.get_report("afk-journey-hero-upgrade")
    assert report is not None
    report.summary = "A deliberately revised summary."
    report.flow[0].description = "A revised entry description."
    report.updated_at = "2026-07-13T12:30:00+08:00"
    facility.store.upsert_report(report)

    monkeypatch.setattr(api_module, "_facility", lambda: facility)
    app = FastAPI()
    app.include_router(game_observatory_router)
    client = TestClient(app)
    response = client.get(
        "/api/game-observatory/reports/afk-journey-hero-upgrade/diff",
        params={"from_revision": 1, "to_revision": 2},
    )
    assert response.status_code == 200
    changes = response.json()["changes"]
    paths = {item["path"] for item in changes}
    assert "/summary" in paths
    assert "/flow/afk.flow.hero-entry/description" in paths

    semantic = client.get("/game-observatory/reports/afk-journey-hero-upgrade")
    assert semantic.status_code == 200
    assert "AFK Journey" in semantic.text