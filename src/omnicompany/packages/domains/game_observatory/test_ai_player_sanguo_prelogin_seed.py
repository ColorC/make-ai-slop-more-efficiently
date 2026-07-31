from __future__ import annotations

import json
from pathlib import Path

from omnicompany.packages.domains.game_observatory.ai_player.sanguo_prelogin_seed import (
    seed_sanguo_prelogin_memory,
)
from omnicompany.packages.domains.game_observatory.ai_player.store import AIPlayerStore
from omnicompany.packages.domains.game_observatory.store import ObservatoryStore


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_real_prelogin_inputs_persist_exact_source_identity_without_device_actions(tmp_path):
    result = seed_sanguo_prelogin_memory(
        workspace_root=REPO_ROOT,
        environment_path=REPO_ROOT
        / "data/domains/game_observatory/benchmarks/ai_player/environments/"
        "sanguo_mouding_pure_ai_v1.environment.json",
        guide_seed_path=REPO_ROOT
        / "data/domains/game_observatory/benchmarks/ai_player/guides/"
        "sanguo_guide_seed.v1.json",
        research_native_path=REPO_ROOT
        / "data/domains/research/runs/run_2026-07-15T20-57-26/native.json",
        store_root=tmp_path / "runtime",
        output_path=tmp_path / "result.json",
    )

    seed = json.loads(
        (REPO_ROOT / result.guide_seed_path).read_text(encoding="utf-8")
    )
    assert result.research_record_id == seed["research_record_id"]
    assert "?" not in result.research_record_id
    assert result.guide_count == 14
    assert result.guide_source_snapshot_count == 1
    assert result.all_guides_unverified_before_live_identity is True
    assert result.persistence_reopen_verified is True
    assert result.device_actions_performed == 0

    store = AIPlayerStore(ObservatoryStore(tmp_path / "runtime"))
    assert {
        ref.source_ids[0]
        for guide in store.list_guide_knowledge(result.environment_id)
        for ref in guide.evidence_refs
    } == {seed["research_record_id"]}