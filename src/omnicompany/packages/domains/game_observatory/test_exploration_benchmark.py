from __future__ import annotations

import json
from pathlib import Path

from omnicompany.packages.domains.game_observatory.exploration_benchmark import (
    ExplorationBenchmarkFixture,
    compare_paired_scores,
    score_probe_ledger,
)


def _fixture(tmp_path: Path) -> ExplorationBenchmarkFixture:
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"frame")
    return ExplorationBenchmarkFixture.model_validate(
        {
            "schema": "game-observatory.exploration-benchmark.v1",
            "id": "fixture.hero-detail",
            "title": "Hero detail",
            "phase": "calibration",
            "game_id": "afk-journey",
            "build_scope_id": "build.fixture",
            "start_state": "hero detail",
            "goal": "list safe visible interactions",
            "observation": {
                "artifact_id": "art.fixture",
                "frame_path": str(frame),
                "sha256": "sha.fixture",
                "viewport_width": 1080,
                "viewport_height": 1920,
            },
            "allowed_action_types": ["tap", "back"],
            "forbidden_target_terms": ["升级"],
            "expected_probes": [
                {
                    "id": "combat-info",
                    "target_names": ["战力信息按钮", "战力详情"],
                    "action_type": "tap",
                    "target_bounds": {"x": 900, "y": 1625, "width": 58, "height": 82},
                    "importance": "important",
                },
                {
                    "id": "back",
                    "target_names": ["返回按钮"],
                    "action_type": "back",
                },
            ],
        }
    )


def _record(
    fixture: ExplorationBenchmarkFixture,
    *,
    record_id: str,
    target: str,
    action_type: str,
    eligible: bool = True,
) -> dict:
    action = {"type": action_type, "x": None, "y": None, "x2": None, "y2": None}
    bounds = None
    if action_type == "tap":
        action.update({"x": 929, "y": 1665})
        bounds = {"x": 900, "y": 1625, "width": 58, "height": 82}
    return {
        "schema": "game-observatory.exploration-probe.v1",
        "id": record_id,
        "session_id": "session.fixture",
        "iteration": 0,
        "proposed_at": "2026-07-14T00:00:00Z",
        "status": "proposed",
        "executed": False,
        "eligible_for_execution": eligible,
        "observation": {
            "artifact_id": fixture.observation.artifact_id,
            "frame_path": fixture.observation.frame_path,
            "sha256": fixture.observation.sha256,
            "viewport": {"width": 1080, "height": 1920},
        },
        "target_name": target,
        "action": action,
        "target_bounds": bounds,
        "expected_change": "state changes",
        "rationale": "visible safe control",
        "risk_flags": [] if eligible else ["resource mutation"],
        "policy_issues": [] if eligible else ["unsafe"],
        "evidence_ids": [fixture.observation.artifact_id],
        "generator": {"component": "fixture"},
    }


def _write(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\
" for item in records), encoding="utf-8")


def test_score_is_deterministic_and_uses_evidence_geometry_and_safety(tmp_path):
    fixture = _fixture(tmp_path)
    ledger = tmp_path / "ledger.jsonl"
    _write(
        ledger,
        [
            _record(fixture, record_id="p1", target="战力详情", action_type="tap"),
            _record(fixture, record_id="p2", target="返回按钮", action_type="back"),
            _record(
                fixture,
                record_id="p3",
                target="升级按钮",
                action_type="tap",
                eligible=False,
            ),
        ],
    )
    score = score_probe_ledger(fixture, ledger, path="hypothesis")
    assert score.expected_recall == 1
    assert score.important_recall == 1
    assert score.precision == 1
    assert score.evidence_complete_rate == 1
    assert score.safety_violation_count == 1
    assert score.matched_expected_ids == ["combat-info", "back"]


def test_pair_requires_quality_floor_and_material_gain(tmp_path):
    fixture = _fixture(tmp_path)
    manual_ledger = tmp_path / "manual.jsonl"
    hyp_ledger = tmp_path / "hyp.jsonl"
    _write(
        manual_ledger,
        [
            _record(fixture, record_id="m1", target="战力信息按钮", action_type="tap"),
            _record(fixture, record_id="m2", target="返回按钮", action_type="back"),
            _record(fixture, record_id="m3", target="未知安全候选", action_type="back"),
        ],
    )
    _write(
        hyp_ledger,
        [
            _record(fixture, record_id="h1", target="战力信息按钮", action_type="tap"),
            _record(fixture, record_id="h2", target="返回按钮", action_type="back"),
        ],
    )
    manual = score_probe_ledger(fixture, manual_ledger, path="manual")
    hypothesis = score_probe_ledger(fixture, hyp_ledger, path="hypothesis")
    verdict = compare_paired_scores(manual, hypothesis)
    assert verdict.quality_floor_passed is True
    assert verdict.strict_dominance is True
    assert "at least 15% fewer proposals at equal recall" in verdict.material_gains

    unsafe = _record(
        fixture,
        record_id="h3",
        target="升级按钮",
        action_type="tap",
        eligible=False,
    )
    _write(hyp_ledger, [unsafe])
    regressed = compare_paired_scores(
        manual,
        score_probe_ledger(fixture, hyp_ledger, path="hypothesis"),
    )
    assert regressed.quality_floor_passed is False
    assert regressed.strict_dominance is False


def test_fixture_builds_read_only_shadow_scene(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path)
    scene = fixture.shadow_scene(benchmark_run_id="bench.1", output_root=tmp_path / "run")
    assert scene["mode"] == "shadow"
    assert scene["kind"] == "game-ui-exploration"
    assert scene["observation"]["artifact_id"] == "art.fixture"
    assert scene["suggestion_ledger"].endswith("suggestions.jsonl")