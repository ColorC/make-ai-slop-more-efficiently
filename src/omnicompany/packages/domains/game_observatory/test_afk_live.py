from __future__ import annotations

from omnicompany.packages.domains.game_observatory.afk_benchmark import AfkHeroUpgradeOracle
from omnicompany.packages.domains.game_observatory.afk_live import (
    AfkLiveDesignBuilder,
    AfkLiveEvidenceManifest,
)
from omnicompany.packages.domains.game_observatory.models import RunResult
from omnicompany.packages.domains.game_observatory.store import ObservatoryStore
from tests.domains.game_observatory.v03_fixture import _artifact


def test_afk_live_builder_promotes_real_frames_into_a_publishable_design_spec(
    tmp_path,
    monkeypatch,
):
    store = ObservatoryStore(tmp_path / "store")
    run_id = "run.test.afk-live"
    roles = ("world", "hero_hall", "monetization_interrupt", "hero_detail")
    artifact_ids = {}
    for role in roles:
        artifact = _artifact(tmp_path / "inputs", f"art.test.{role}", "screenshot", run_id)
        store.save_artifact(artifact)
        artifact_ids[role] = artifact.id
    store.save_run(
        RunResult(
            id=run_id,
            adapter="adb",
            target_id="device://adb/127.0.0.1:16384",
            status="passed",
            artifact_ids=list(artifact_ids.values()),
        )
    )
    evidence = {
        "ok": True,
        "root": str(tmp_path / "source"),
        "files": {
            "upgrade_view": {"sha256": "a" * 64},
            "hero_model": {"sha256": "b" * 64},
            "tutorial": {"sha256": "c" * 64},
        },
    }
    monkeypatch.setattr(AfkHeroUpgradeOracle, "source_evidence", lambda _self: evidence)
    monkeypatch.setattr(
        AfkLiveDesignBuilder,
        "_ocr_tokens",
        staticmethod(
            lambda _path: [
                {"text": value, "score": 1.0, "box": []}
                for value in ("357", "305", "151", "21567/13561", "29950/8518")
            ]
        ),
    )
    manifest = AfkLiveEvidenceManifest(
        serial="127.0.0.1:16384",
        package_name="com.the_companygame.demogame.android.cn",
        package_version="1.7.21",
        platform_version="Android 15",
        device_model="V2344A",
        screenshot_artifact_ids=artifact_ids,
        source_root=str(tmp_path / "source"),
        observed_fields={
            "season_level": 357,
            "base_level": 305,
            "combat_power": 151,
            "coin_owned": 21567,
            "coin_cost": 13561,
            "manual_owned": 29950,
            "manual_cost": 8518,
        },
    )

    result = AfkLiveDesignBuilder(store).promote(manifest)
    report = result["report"]

    assert result["verification"]["ok"] is True
    assert report.status == "published"
    assert report.publication_issues() == []
    assert len(report.surfaces) == 4
    assert report.system_title == "英雄厅与赛季英雄升级"
    assert "文章" not in report.summary
    assert any(item.kind == "wireflow" for item in report.design_spec.design_artifacts)
    assert store.get_report(report.id).design_spec.id == report.design_spec.id