from __future__ import annotations

import pytest

from omnicompany.packages.domains.game_observatory.compiler import SemanticReportCompiler
from omnicompany.packages.domains.game_observatory.models import (
    ContentKind,
    PlayerVoice,
    SourceRef,
)
from omnicompany.packages.domains.game_observatory.runtime import GameObservatory
from omnicompany.packages.domains.game_observatory.source_voice import SourceVoicePipeline


def _voice_source() -> SourceRef:
    return SourceRef(
        id="voice.test.hero-upgrade-friction",
        kind=ContentKind.player_voice,
        title="A public player discussion",
        url="https://example.test/discussion/42",
        locator="comment-7",
        author="Player42",
        published_at="2026-07-01",
        captured_at="2026-07-13T01:00:00+08:00",
        version_context="1.7.x",
    )


def _voice() -> PlayerVoice:
    return PlayerVoice(
        id="pv.test.hero-upgrade-friction",
        summary="The player says the repeated upgrade confirmation interrupts the flow.",
        theme="interaction-friction",
        sentiment="negative",
        source_id="voice.test.hero-upgrade-friction",
        system_node_id="afk.flow.confirm",
        version_context="1.7.x",
    )


def test_voice_ingest_is_deduplicated_node_bound_and_retractable(tmp_path):
    facility = GameObservatory(tmp_path)
    facility.bootstrap()
    pipeline = SourceVoicePipeline(facility.store)

    first = pipeline.ingest_player_voice(
        "afk-journey-hero-upgrade",
        _voice_source(),
        _voice(),
        excerpt="A short excerpt retained for internal review.",
    )
    assert first["deduplicated"] is False
    assert facility.store.counts()["source_snapshots"] == 1
    assert facility.store.counts()["voice_entries"] == 1
    report = facility.store.get_report("afk-journey-hero-upgrade")
    assert report is not None
    assert report.player_voices[-1].system_node_id == "afk.flow.confirm"

    duplicate = pipeline.ingest_player_voice(
        "afk-journey-hero-upgrade",
        _voice_source(),
        _voice().model_copy(update={"summary": "  THE PLAYER says the repeated upgrade confirmation interrupts the flow.  "}),
        excerpt="A short excerpt retained for internal review.",
    )
    assert duplicate["deduplicated"] is True
    assert facility.store.counts()["source_snapshots"] == 1
    assert facility.store.counts()["voice_entries"] == 1

    retracted = pipeline.retract_source(
        "voice.test.hero-upgrade-friction",
        "author requested removal",
    )
    assert retracted["affected_reports"] == ["report.afk-journey.hero-upgrade.v1"]
    canonical = facility.store.get_report("afk-journey-hero-upgrade")
    assert canonical is not None
    source = next(item for item in canonical.sources if item.id == "voice.test.hero-upgrade-friction")
    assert source.status == "retracted"
    public = SemanticReportCompiler.public_report(canonical)
    tombstone = next(item for item in public["sources"] if item["id"] == source.id)
    assert tombstone["url"] == "#source-retracted"
    assert all(item["id"] != "pv.test.hero-upgrade-friction" for item in public["player_voices"])
    assert facility.store.list_source_snapshots(source.id)[0].status == "retracted"
    assert facility.store.list_voice_records(canonical.id)[0].status == "retracted"


def test_voice_ingest_rejects_unknown_flow_node(tmp_path):
    facility = GameObservatory(tmp_path)
    facility.bootstrap()
    voice = _voice().model_copy(update={"system_node_id": "missing.flow.node"})
    with pytest.raises(ValueError, match="unknown flow node"):
        SourceVoicePipeline(facility.store).ingest_player_voice(
            "afk-journey-hero-upgrade",
            _voice_source(),
            voice,
        )