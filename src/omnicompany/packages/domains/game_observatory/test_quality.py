from __future__ import annotations

import json

from omnicompany.packages.domains.game_observatory.quality import PublicSiteQualityVerifier
from omnicompany.packages.domains.game_observatory.runtime import GameObservatory


def test_public_site_quality_separates_shell_quality_from_archive_completeness(
    tmp_path, monkeypatch
):
    facility = GameObservatory(tmp_path)
    facility.bootstrap()
    catalog = (facility.store.export_root / "public" / "catalog.json").read_bytes()
    semantic = (
        facility.store.export_root / "public" / "afk-journey-hero-upgrade.html"
    ).read_bytes()
    home = b"""<!doctype html><html><head>
    <link href='/game-observatory/styles.css?v=1234'><script src='/game-observatory/app.js?v=1234'></script>
    </head><body></body></html>"""
    css = b""".report-body { grid-template-columns: 1fr; }
    .surface-card { grid-template-columns: 1fr; }
    :focus-visible { outline: 2px solid; }
    @media (max-width: 680px) {}
    @media (max-width: 980px) {}
    @media (prefers-reduced-motion: reduce) {}"""

    def fake_fetch(_base_url, path):
        if path.endswith("catalog"):
            body = catalog
            headers = {"etag": '"test"'}
        elif path.endswith("styles.css"):
            body = css
            headers = {}
        elif path.endswith("app.js"):
            body = b"console.log('quality fixture')"
            headers = {}
        elif "/reports/" in path:
            body = semantic
            headers = {}
        else:
            body = home
            headers = {"content-security-policy": "default-src 'self'"}
        return {
            "url": path,
            "status": 200,
            "bytes": len(body),
            "elapsed_ms": 10.0,
            "headers": headers,
            "body": body,
        }

    browser_path = tmp_path / "browser.json"
    clean_view = {
        "lang": "zh-CN",
        "landmarks": {"main": 1, "nav": 1, "header": 1, "footer": 1},
        "controls_without_name": [],
        "images_without_alt": 0,
        "duplicate_ids": [],
        "positive_tabindex": 0,
        "heading_order_valid": True,
        "skip_target": True,
        "has_surfaces": True,
        "has_journey": True,
        "has_mechanisms": True,
        "has_resources": True,
        "has_sources": True,
    }
    browser_path.write_text(
        json.dumps(
            {
                "navigation_samples_ms": [80, 70, 75],
                "home": clean_view,
                "report": clean_view,
            }
        ),
        encoding="utf-8",
    )
    verifier = PublicSiteQualityVerifier(facility.store)
    monkeypatch.setattr(verifier, "_fetch", fake_fetch)

    result = verifier.verify(
        "http://quality.test",
        browser_evidence_path=browser_path,
    )

    assert result["site_shell_ready"] is True
    assert result["archive_complete"] is False
    assert result["ok"] is False
    incomplete = {item["slug"]: item for item in result["archive_results"] if not item["complete"]}
    assert incomplete["afk-journey-hero-upgrade"]["checks"]["benchmark_evidence"] is False
    assert incomplete["minecraft-stone-pickaxe"]["checks"]["benchmark_evidence"] is False
    assert all(item["checks"]["surfaces"] for item in result["archive_results"])