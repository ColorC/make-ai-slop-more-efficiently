from __future__ import annotations

import json
from pathlib import Path

from omnicompany.packages.domains.game_observatory.minecraft_live import (
    MinecraftFirstNightDesignBuilder,
    MinecraftFirstNightEvidenceManifest,
    SCREEN_ROLES,
)
from omnicompany.packages.domains.game_observatory.store import ObservatoryStore
from tests.domains.game_observatory.v03_fixture import _artifact


def test_minecraft_live_builder_promotes_real_frames_and_objective_gates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "proto-world"
    screenshot_paths: dict[str, str] = {}
    for role in SCREEN_ROLES:
        artifact = _artifact(source_root / "screens", f"art.input.{role}", "screenshot")
        screenshot_paths[role] = str(Path(artifact.path).relative_to(source_root))
    gates = {
        "time": "2026-07-13 19:19:00",
        "player": "GOFood01",
        "passed": 24,
        "total": 24,
        "gates": [
            {"id": f"G{index}", "name": f"gate {index}", "ok": True, "evidence": "passed"}
            for index in range(1, 25)
        ],
    }
    gates_path = source_root / "verify" / "gates.json"
    gates_path.parent.mkdir(parents=True)
    gates_path.write_text(json.dumps(gates), encoding="utf-8")
    source_evidence = {
        "ok": True,
        "root": str(source_root),
        "files": {
            key: {"path": str(source_root / key), "sha256": char * 64, "symbols": []}
            for key, char in zip(
                (
                    "handcraft",
                    "jade",
                    "recipe_zeroing",
                    "body",
                    "food_profiles",
                    "handbook_entries",
                    "e2e",
                ),
                "abcdef1",
                strict=True,
            )
        },
        "checks": [],
    }
    monkeypatch.setattr(
        MinecraftFirstNightDesignBuilder,
        "source_evidence",
        lambda _self, _manifest: source_evidence,
    )
    manifest = MinecraftFirstNightEvidenceManifest(
        source_root=str(source_root),
        world_snapshot="cfc4132",
        benchmark_player="GOFood01",
        e2e_gates_path=str(gates_path.relative_to(source_root)),
        screenshot_paths=screenshot_paths,
        recipe_probe_response="Unknown recipe: minecraft:stone_pickaxe",
        reset_after_run=True,
    )
    store = ObservatoryStore(tmp_path / "store")

    result = MinecraftFirstNightDesignBuilder(store).promote(manifest)
    report = result["report"]

    assert result["verification"]["ok"] is True
    assert report.status == "published"
    assert report.publication_issues() == []
    assert report.system_title == "世界内生火、营火烹饪与熟食反馈"
    assert len(report.surfaces) == 4
    assert len(report.design_spec.design_artifacts) == 5
    assert any(item.kind == "wireflow" for item in report.design_spec.design_artifacts)
    assert store.get_report(report.id).design_spec.id == report.design_spec.id
    assert store.get_artifact("art.minecraft.live.campfire") is not None
    assert store.get_artifact("art.minecraft.design.first-night-wireflow") is not None