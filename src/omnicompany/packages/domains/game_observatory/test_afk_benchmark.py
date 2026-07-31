from __future__ import annotations

from omnicompany.packages.domains.game_observatory.afk_benchmark import (
    AfkHeroUpgradeOracle,
    AfkHeroUpgradeSnapshot,
    write_contract_snapshot,
)


def _source_tree(root):
    values = {
        "Binary/Src/UI/Hero/View/HeroUpgradeView.lua": (
            "HeroUpgradeView GetLevelUpCost nextLevel btn_upgrade"
        ),
        "Binary/Src/UI/Hero/HeroModel.lua": "function GetLevelUpCost() end",
        "Binary/Src/UI/Tutorial/Hero/HeroUpgrade/HeroUpgradeTutorialTask.lua": (
            "HeroUpgradeTutorialTask btn_upgrade"
        ),
    }
    for relative, text in values.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_afk_task_contains_external_and_white_box_objectives(tmp_path):
    _source_tree(tmp_path)
    snapshot = AfkHeroUpgradeSnapshot(
        id="snapshot.verified",
        availability="verified",
        account_alias="isolated",
        hero_id="hero.1",
        starting_level=20,
        target_level=21,
        resources_before={"hero_exp": 1000},
        attributes_before={"HP": 100, "ATK": 10, "DEF": 5},
        reset_strategy="restore snapshot.verified",
        build_scope_id="scope.test",
        state_hash="abc123",
        verified_at="2026-07-13T00:00:00+00:00",
    )
    task = AfkHeroUpgradeOracle(tmp_path).task(snapshot)
    assert {item.id for item in task.checks} == {
        "hero_level_delta",
        "resource_delta_matches_oracle",
        "attributes_match_oracle",
        "ui_before_after_visible",
        "source_formula_present",
    }
    assert task.metadata["safety"]["stop_on_insufficient_resources"] is True
    assert "arbitrary_lua_mutation" in task.metadata["safety"]["forbidden"]


def test_afk_preflight_fails_closed_without_verified_snapshot(tmp_path, monkeypatch):
    _source_tree(tmp_path)
    snapshot_path = write_contract_snapshot(tmp_path / "snapshot.json")
    oracle = AfkHeroUpgradeOracle(tmp_path)

    def bridge(path, *, port):
        if path == "/status":
            return {
                "isPlaying": True,
                "projectPath": str(tmp_path / "Assets"),
                "port": port,
            }
        return {"ok": True}

    monkeypatch.setattr(oracle, "_bridge_json", bridge)
    result = oracle.preflight(snapshot_path)
    assert result["ready"] is False
    assert "contract_only" in " ".join(result["errors"])

    payload = AfkHeroUpgradeSnapshot.model_validate_json(snapshot_path.read_text(encoding="utf-8"))
    payload.availability = "verified"
    payload.verified_at = "2026-07-13T00:00:00+00:00"
    snapshot_path.write_text(payload.model_dump_json(indent=2), encoding="utf-8")
    result = oracle.preflight(snapshot_path)
    assert result["ready"] is True
    assert result["source"]["ok"] is True