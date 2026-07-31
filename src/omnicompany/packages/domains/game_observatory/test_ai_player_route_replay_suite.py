from __future__ import annotations

import hashlib
import json
from pathlib import Path

from omnicompany.packages.domains.game_observatory.ai_player.route_replay_suite import (
    assess_route_replay_suite_from_acceptance,
    write_route_replay_suite_assessment,
)
from tests.domains.game_observatory.test_ai_player_route_replay import fixture_replay


def suite_fixture(tmp_path: Path, monkeypatch):
    store, candidate_path, manifest_path, manifest_sha, verification_path = fixture_replay(
        tmp_path, monkeypatch
    )
    acceptance_path = tmp_path / "acceptance.json"
    acceptance_path.write_text(
        json.dumps(
            {
                "schema": "ai-player-acceptance-manifest.v1",
                "targets": [
                    {
                        "benchmark_id": "afk-fixture",
                        "candidate_manifest": manifest_path.relative_to(tmp_path).as_posix(),
                        "candidate_manifest_sha256": manifest_sha,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    suite_input_path = tmp_path / "suite-input.json"
    suite_input_path.write_text(
        json.dumps(
            {
                "schema": "game-observatory.ai-player.route-replay-suite-input.v1",
                "benchmark_id": "afk-fixture",
                "items": [
                    {
                        "route_id": "route.fixture.hero-to-overlay",
                        "verification_path": verification_path.relative_to(tmp_path).as_posix(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return store, acceptance_path, suite_input_path


def test_route_replay_suite_requires_every_route_once_under_one_trust_root(tmp_path, monkeypatch):
    store, acceptance_path, suite_input_path = suite_fixture(tmp_path, monkeypatch)

    assessment = assess_route_replay_suite_from_acceptance(
        suite_input_path,
        acceptance_path,
        tmp_path,
        store,
        expected_game_id="afk-journey",
        expected_build_scope_id="build.afk.fixture",
    )

    assert assessment.execution_evidence_pass is True
    assert assessment.candidate_route_count == 1
    assert not assessment.issues
    assert assessment.semantic_goal_status == "unadjudicated"
    assert assessment.replay_suite_can_be_frozen is False
    assert assessment.acceptance_manifest_sha256 == hashlib.sha256(
        acceptance_path.read_bytes()
    ).hexdigest()

    output_path = tmp_path / "suite-assessment.json"
    written = write_route_replay_suite_assessment(
        suite_input_path,
        acceptance_path,
        tmp_path,
        store.root,
        output_path,
        expected_game_id="afk-journey",
        expected_build_scope_id="build.afk.fixture",
    )
    assert json.loads(output_path.read_text(encoding="utf-8")) == written.model_dump(
        mode="json", by_alias=True
    )


def test_route_replay_suite_rejects_missing_extra_and_escaped_verification(tmp_path, monkeypatch):
    store, acceptance_path, suite_input_path = suite_fixture(tmp_path, monkeypatch)
    payload = json.loads(suite_input_path.read_text(encoding="utf-8"))
    payload["items"] = [
        {
            "route_id": "route.foreign",
            "verification_path": str((tmp_path.parent / "foreign.json").resolve()),
        }
    ]
    suite_input_path.write_text(json.dumps(payload), encoding="utf-8")

    assessment = assess_route_replay_suite_from_acceptance(
        suite_input_path,
        acceptance_path,
        tmp_path,
        store,
    )

    assert assessment.execution_evidence_pass is False
    assert any(issue.startswith("suite input is missing") for issue in assessment.issues)
    assert any(issue.startswith("suite input has non-candidate") for issue in assessment.issues)
    assert not assessment.results