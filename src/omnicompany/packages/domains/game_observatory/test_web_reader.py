from __future__ import annotations

import socket
import threading
import time

import uvicorn
from playwright.sync_api import sync_playwright

from omnicompany.packages.domains.game_observatory import api as api_module
from omnicompany.packages.domains.game_observatory.public_server import create_public_app
from omnicompany.packages.domains.game_observatory.runtime import GameObservatory
from tests.domains.game_observatory.v03_fixture import publishable_v03_report


def _free_port() -> int:
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def test_design_reader_links_evidence_reconstruction_layout_and_semantics(
    tmp_path,
    monkeypatch,
):
    facility = GameObservatory(tmp_path / "store")
    facility.bootstrap()
    report = publishable_v03_report(tmp_path / "artifacts")
    report.status = "published"
    report.migration_status = "publishable"
    facility.store.upsert_report(report)
    for artifact in report.artifacts:
        facility.store.save_artifact(artifact)
    monkeypatch.setattr(api_module, "_facility", lambda: facility)

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_public_app(),
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.02)
    assert server.started

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            page.goto(
                f"http://127.0.0.1:{port}/game-observatory/report/{report.slug}",
                wait_until="networkidle",
            )
            page.locator(".design-reader").wait_for()
            assert page.locator("h1").first.inner_text() == report.design_spec.title
            assert page.locator("[data-surface-card]").count() == len(report.surfaces)
            assert page.locator(".generated-diagram").count() == 3
            assert page.get_by_text("绑定到具体设计对象的玩家反馈").count() == 1
            assert page.get_by_text("设施暂时无法读取").count() == 0

            first_surface = page.locator("[data-surface-card]").first
            first_surface.locator('[data-surface-mode="reconstruction"]').click()
            assert first_surface.locator(
                '[data-surface-panel="reconstruction"]'
            ).is_visible()
            assert first_surface.locator(
                '[data-surface-panel="evidence"]'
            ).is_hidden()
            first_surface.locator('[data-surface-mode="layout"]').click()
            assert first_surface.locator('[data-surface-panel="layout"]').is_visible()
            assert first_surface.locator(".layout-box").count() >= 1
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    assert not thread.is_alive()