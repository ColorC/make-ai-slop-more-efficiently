from __future__ import annotations

import hashlib
import json
import re

from fastapi.testclient import TestClient

from omnicompany.packages.domains.game_observatory import api as api_module
from omnicompany.packages.domains.game_observatory.maintenance import FacilityMaintenance
from omnicompany.packages.domains.game_observatory.models import ArtifactRef
from omnicompany.packages.domains.game_observatory.public_server import create_public_app
from omnicompany.packages.domains.game_observatory.runtime import GameObservatory


def test_monitor_backup_and_backup_verification_are_real(tmp_path):
    facility = GameObservatory(tmp_path / "store")
    facility.bootstrap()
    evidence = tmp_path / "evidence.bin"
    evidence.write_bytes(b"immutable evidence")
    artifact = ArtifactRef(
        id="art.production.test",
        kind="source",
        path=str(evidence),
        sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),
        media_type="application/octet-stream",
    )
    facility.store.save_artifact(artifact)
    maintenance = FacilityMaintenance(facility.store)
    healthy = maintenance.monitor()
    assert healthy["ok"] is True
    assert healthy["artifacts_checked"] == 1

    backup = maintenance.backup(tmp_path / "backups")
    assert backup["ok"] is True
    assert backup["verification"]["database_integrity"] == "ok"
    assert (tmp_path / "backups").is_dir()

    evidence.write_bytes(b"tampered")
    unhealthy = maintenance.monitor(write=False)
    assert unhealthy["ok"] is False
    assert unhealthy["corrupt_artifacts"] == [artifact.id]


def test_public_server_cache_security_fragments_and_accessibility(tmp_path, monkeypatch):
    facility = GameObservatory(tmp_path)
    facility.bootstrap()
    monkeypatch.setattr(api_module, "_facility", lambda: facility)
    client = TestClient(create_public_app())

    catalog = client.get("/api/game-observatory/catalog")
    assert catalog.status_code == 200
    assert catalog.headers["etag"].startswith('"')
    assert "stale-while-revalidate" in catalog.headers["cache-control"]
    assert catalog.headers["x-content-type-options"] == "nosniff"
    claim_id = catalog.json()["reports"][0]["claims"][0]["id"]
    fragment = client.get(f"/api/game-observatory/fragments/{claim_id}")
    assert fragment.status_code == 200
    assert fragment.json()["url"].endswith(f"#{claim_id}")

    semantic = client.get("/game-observatory/reports/afk-journey-hero-upgrade")
    assert semantic.status_code == 200
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', semantic.text, re.S)
    assert match is not None
    assert json.loads(match.group(1))["compiled"]["schema"] == "game-observatory.public-report.v1"
    assert client.get("/game-observatory/sitemap.xml").status_code == 200
    assert "Allow: /game-observatory/" in client.get("/robots.txt").text
    site = client.get("/game-observatory/").text
    css = client.get("/game-observatory/styles.css").text
    assert 'class="skip-link"' in site
    assert "prefers-color-scheme: dark" in css
    assert "prefers-reduced-motion: reduce" in css


def test_author_reviewer_roles_enforce_publish_transition(tmp_path, monkeypatch):
    facility = GameObservatory(tmp_path)
    facility.bootstrap()
    monkeypatch.setattr(api_module, "_facility", lambda: facility)
    monkeypatch.setenv(
        "OMNI_GAME_OBSERVATORY_TOKENS",
        json.dumps({"author-token": "author", "reviewer-token": "reviewer"}),
    )
    client = TestClient(create_public_app(), client=("192.168.10.22", 54321))
    base = "/api/game-observatory/reports/afk-journey-hero-upgrade/transition"

    to_review = client.post(
        base,
        json={"status": "review", "note": "reviewer reopens"},
        headers={"X-Game-Observatory-Token": "reviewer-token"},
    )
    assert to_review.status_code == 200
    forbidden = client.post(
        base,
        json={"status": "published"},
        headers={"X-Game-Observatory-Token": "author-token"},
    )
    assert forbidden.status_code == 403
    to_draft = client.post(
        base,
        json={"status": "draft"},
        headers={"X-Game-Observatory-Token": "author-token"},
    )
    assert to_draft.status_code == 200
    submit = client.post(
        base,
        json={"status": "review"},
        headers={"X-Game-Observatory-Token": "author-token"},
    )
    assert submit.status_code == 200
    publish = client.post(
        base,
        json={"status": "published"},
        headers={"X-Game-Observatory-Token": "reviewer-token"},
    )
    assert publish.status_code == 200