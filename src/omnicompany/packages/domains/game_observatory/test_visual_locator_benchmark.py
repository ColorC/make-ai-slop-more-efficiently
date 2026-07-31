from __future__ import annotations

import json
from pathlib import Path

from omnicompany.packages.domains.game_observatory.exploration_benchmark import (
    ExplorationBenchmarkFixture,
)
from omnicompany.packages.domains.game_observatory.visual_locator_benchmark import (
    score_locator_result,
    score_locator_series,
)


def _fixture() -> ExplorationBenchmarkFixture:
    return ExplorationBenchmarkFixture.model_validate(
        {
            "schema": "game-observatory.exploration-benchmark.v1",
            "id": "locator.fixture",
            "title": "locator fixture",
            "phase": "calibration",
            "game_id": "afk-journey",
            "build_scope_id": "build.fixture",
            "start_state": "fixture",
            "goal": "locate controls",
            "observation": {
                "artifact_id": "art.fixture",
                "frame_path": "fixture.png",
                "sha256": "a" * 64,
                "viewport_width": 400,
                "viewport_height": 400,
            },
            "allowed_action_types": ["tap"],
            "expected_probes": [
                {
                    "id": "left",
                    "target_names": ["left"],
                    "action_type": "tap",
                    "target_bounds": {"x": 10, "y": 10, "width": 80, "height": 80},
                },
                {
                    "id": "right",
                    "target_names": ["right"],
                    "action_type": "tap",
                    "target_bounds": {"x": 210, "y": 10, "width": 80, "height": 80},
                },
            ],
        }
    )


def _write_result(path: Path, *, right_x: int = 210, include_right: bool = True) -> Path:
    elements = [
        {
            "id": "candidate.left",
            "interactivity": True,
            "source_bounds": {"x": 12, "y": 12, "width": 76, "height": 76},
        },
        {
            "id": "candidate.fullscreen",
            "interactivity": True,
            "source_bounds": {"x": 0, "y": 0, "width": 400, "height": 400},
        },
    ]
    if include_right:
        elements.append(
            {
                "id": "candidate.right",
                "interactivity": True,
                "source_bounds": {"x": right_x, "y": 10, "width": 80, "height": 80},
            }
        )
    path.write_text(
        json.dumps(
            {
                "ok": True,
                "locator": "fixture-locator",
                "image": {
                    "sha256": "a" * 64,
                    "width": 400,
                    "height": 400,
                },
                "metrics": {
                    "startup_seconds": 1.0,
                    "parse_seconds": 2.0,
                    "peak_cuda_memory_bytes": 100,
                },
                "process_elapsed_seconds": 3.0,
                "elements": elements,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_score_locator_result_uses_one_to_one_interactable_geometry(tmp_path: Path):
    score = score_locator_result(_fixture(), _write_result(tmp_path / "result.json"))
    assert score.expected_recall == 1.0
    assert score.matched_count == 2
    assert {item.candidate_id for item in score.matches} == {
        "candidate.left",
        "candidate.right",
    }


def test_score_locator_result_reports_missing_target(tmp_path: Path):
    score = score_locator_result(
        _fixture(),
        _write_result(tmp_path / "result.json", include_right=False),
    )
    assert score.expected_recall == 0.5
    assert score.missing_expected_ids == ["right"]


def test_score_locator_series_enforces_recall_and_jitter(tmp_path: Path):
    fixture = _fixture()
    stable = [
        score_locator_result(
            fixture,
            _write_result(tmp_path / f"stable-{index}.json", right_x=210 + index),
        )
        for index in range(3)
    ]
    assert score_locator_series(stable).passed is True

    jittered = stable[:2] + [
        score_locator_result(
            fixture,
            _write_result(tmp_path / "jittered.json", right_x=230),
        )
    ]
    verdict = score_locator_series(jittered)
    assert verdict.passed is False
    assert verdict.maximum_center_jitter_pixels == 20.0