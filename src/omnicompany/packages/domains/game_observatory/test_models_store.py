from __future__ import annotations

from omnicompany.packages.domains.game_observatory.content import seed_reports
from omnicompany.packages.domains.game_observatory.store import ObservatoryStore


def test_reports_are_publishable_and_round_trip(tmp_path):
    store = ObservatoryStore(tmp_path)
    reports = seed_reports()
    for report in reports:
        report.assert_publishable()
        store.upsert_report(report)

    assert store.counts()["reports"] == 2
    assert store.counts()["sources"] >= 10
    loaded = store.get_report("afk-journey-hero-upgrade")
    assert loaded is not None
    assert loaded.game_id == "afk-journey"
    assert len(loaded.flow) == 5
    assert len(loaded.player_voices) == 3


def test_tag_and_text_search_use_canonical_content(tmp_path):
    store = ObservatoryStore(tmp_path)
    for report in seed_reports():
        store.upsert_report(report)

    assert [item.game_id for item in store.list_reports(tag="crafting")] == ["minecraft-java"]
    assert [item.game_id for item in store.list_reports(query="英雄精华")] == ["afk-journey"]
    assert any(item["tag"] == "source-probe" and item["count"] == 2 for item in store.list_tags())