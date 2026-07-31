from __future__ import annotations

import json
import math
from itertools import combinations
from pathlib import Path
from statistics import median
from typing import Any

from pydantic import BaseModel, Field

from .exploration_benchmark import ExplorationBenchmarkFixture


class LocatorTargetMatch(BaseModel):
    expected_id: str
    matched: bool
    candidate_id: str | None = None
    candidate_bounds: dict[str, int] | None = None
    candidate_center: tuple[float, float] | None = None
    intersection_over_smaller: float = 0.0
    iou: float = 0.0
    center_distance_pixels: float | None = None
    reason: str


class LocatorRunScore(BaseModel):
    schema_id: str = Field(
        default="game-observatory.visual-locator-score.v1",
        alias="schema",
    )
    fixture_id: str
    locator: str
    result_path: str
    image_sha256: str
    candidate_count: int
    interactable_count: int
    expected_count: int
    matched_count: int
    expected_recall: float
    missing_expected_ids: list[str]
    matches: list[LocatorTargetMatch]
    startup_seconds: float
    parse_seconds: float
    process_elapsed_seconds: float
    peak_cuda_memory_bytes: int
    issues: list[str] = Field(default_factory=list)


class LocatorSeriesVerdict(BaseModel):
    schema_id: str = Field(
        default="game-observatory.visual-locator-series-verdict.v1",
        alias="schema",
    )
    fixture_id: str
    run_count: int
    required_run_count: int
    recall_by_run: list[float]
    missing_expected_ids_by_run: list[list[str]]
    maximum_center_jitter_pixels: float
    jitter_by_expected_id: dict[str, float]
    median_startup_seconds: float
    median_parse_seconds: float
    median_process_elapsed_seconds: float
    maximum_peak_cuda_memory_bytes: int
    issues: list[str]
    passed: bool


def _xyxy(bounds: dict[str, Any]) -> tuple[float, float, float, float]:
    x = float(bounds["x"])
    y = float(bounds["y"])
    return x, y, x + float(bounds["width"]), y + float(bounds["height"])


def _center(bounds: dict[str, Any]) -> tuple[float, float]:
    x1, y1, x2, y2 = _xyxy(bounds)
    return (x1 + x2) / 2, (y1 + y2) / 2


def _contains(bounds: dict[str, Any], point: tuple[float, float]) -> bool:
    x1, y1, x2, y2 = _xyxy(bounds)
    return x1 <= point[0] <= x2 and y1 <= point[1] <= y2


def _overlap(
    expected: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, float | bool]:
    ex1, ey1, ex2, ey2 = _xyxy(expected)
    cx1, cy1, cx2, cy2 = _xyxy(candidate)
    intersection = max(0.0, min(ex2, cx2) - max(ex1, cx1)) * max(
        0.0, min(ey2, cy2) - max(ey1, cy1)
    )
    expected_area = max(1.0, (ex2 - ex1) * (ey2 - ey1))
    candidate_area = max(1.0, (cx2 - cx1) * (cy2 - cy1))
    union = expected_area + candidate_area - intersection
    smaller = min(expected_area, candidate_area)
    ratio = max(expected_area, candidate_area) / smaller
    expected_center = _center(expected)
    candidate_center = _center(candidate)
    return {
        "intersection_over_smaller": intersection / smaller,
        "iou": intersection / union if union else 0.0,
        "area_ratio": ratio,
        "candidate_center_inside": _contains(expected, candidate_center),
        "expected_center_inside": _contains(candidate, expected_center),
        "center_distance": math.dist(expected_center, candidate_center),
    }


def _is_match(metrics: dict[str, float | bool]) -> bool:
    return (
        float(metrics["intersection_over_smaller"]) >= 0.5
        and float(metrics["area_ratio"]) <= 6.0
        and bool(
            metrics["candidate_center_inside"]
            or metrics["expected_center_inside"]
        )
    )


def score_locator_result(
    fixture: ExplorationBenchmarkFixture,
    result_path: Path,
) -> LocatorRunScore:
    result_path = result_path.resolve()
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    issues: list[str] = []
    if payload.get("ok") is not True:
        issues.append("locator result is not marked ok")
    image = payload.get("image") or {}
    if image.get("sha256") != fixture.observation.sha256:
        issues.append("locator image hash does not match fixture")
    if (
        image.get("width") != fixture.observation.viewport_width
        or image.get("height") != fixture.observation.viewport_height
    ):
        issues.append("locator viewport does not match fixture")
    elements = payload.get("elements") or []
    interactable = [
        item
        for item in elements
        if item.get("interactivity") is True
        and isinstance(item.get("source_bounds"), dict)
    ]
    expected = [item for item in fixture.expected_probes if item.target_bounds]
    edges: list[tuple[float, str, str, dict[str, float | bool]]] = []
    candidates_by_expected: dict[str, list[tuple[dict[str, Any], dict[str, float | bool]]]] = {
        item.id: [] for item in expected
    }
    for target in expected:
        target_bounds = target.target_bounds.model_dump()
        for candidate in interactable:
            metrics = _overlap(target_bounds, candidate["source_bounds"])
            candidates_by_expected[target.id].append((candidate, metrics))
            if _is_match(metrics):
                score = float(metrics["intersection_over_smaller"]) - min(
                    float(metrics["center_distance"]) / 10000,
                    0.1,
                )
                edges.append((score, target.id, str(candidate["id"]), metrics))
    assigned_targets: set[str] = set()
    assigned_candidates: set[str] = set()
    assignments: dict[str, tuple[str, dict[str, float | bool]]] = {}
    for _, expected_id, candidate_id, metrics in sorted(edges, reverse=True):
        if expected_id in assigned_targets or candidate_id in assigned_candidates:
            continue
        assigned_targets.add(expected_id)
        assigned_candidates.add(candidate_id)
        assignments[expected_id] = (candidate_id, metrics)
    elements_by_id = {str(item.get("id")): item for item in interactable}
    matches: list[LocatorTargetMatch] = []
    for target in expected:
        assignment = assignments.get(target.id)
        if assignment:
            candidate_id, metrics = assignment
            candidate = elements_by_id[candidate_id]
            bounds = {key: int(value) for key, value in candidate["source_bounds"].items()}
            matches.append(
                LocatorTargetMatch(
                    expected_id=target.id,
                    matched=True,
                    candidate_id=candidate_id,
                    candidate_bounds=bounds,
                    candidate_center=_center(bounds),
                    intersection_over_smaller=round(
                        float(metrics["intersection_over_smaller"]), 6
                    ),
                    iou=round(float(metrics["iou"]), 6),
                    center_distance_pixels=round(float(metrics["center_distance"]), 3),
                    reason="one-to-one geometry match",
                )
            )
            continue
        ranked = sorted(
            candidates_by_expected[target.id],
            key=lambda item: float(item[1]["intersection_over_smaller"]),
            reverse=True,
        )
        if ranked:
            candidate, metrics = ranked[0]
            bounds = {key: int(value) for key, value in candidate["source_bounds"].items()}
            matches.append(
                LocatorTargetMatch(
                    expected_id=target.id,
                    matched=False,
                    candidate_id=str(candidate["id"]),
                    candidate_bounds=bounds,
                    candidate_center=_center(bounds),
                    intersection_over_smaller=round(
                        float(metrics["intersection_over_smaller"]), 6
                    ),
                    iou=round(float(metrics["iou"]), 6),
                    center_distance_pixels=round(float(metrics["center_distance"]), 3),
                    reason="best interactable candidate failed geometry contract",
                )
            )
        else:
            matches.append(
                LocatorTargetMatch(
                    expected_id=target.id,
                    matched=False,
                    reason="no interactable candidates",
                )
            )
    matched_ids = [item.expected_id for item in matches if item.matched]
    missing_ids = [item.expected_id for item in matches if not item.matched]
    metrics = payload.get("metrics") or {}
    return LocatorRunScore(
        fixture_id=fixture.id,
        locator=str(payload.get("locator") or "unknown"),
        result_path=str(result_path),
        image_sha256=str(image.get("sha256") or ""),
        candidate_count=len(elements),
        interactable_count=len(interactable),
        expected_count=len(expected),
        matched_count=len(matched_ids),
        expected_recall=(len(matched_ids) / len(expected) if expected else 1.0),
        missing_expected_ids=missing_ids,
        matches=matches,
        startup_seconds=float(metrics.get("startup_seconds") or 0),
        parse_seconds=float(metrics.get("parse_seconds") or 0),
        process_elapsed_seconds=float(payload.get("process_elapsed_seconds") or 0),
        peak_cuda_memory_bytes=int(metrics.get("peak_cuda_memory_bytes") or 0),
        issues=issues,
    )


def score_locator_series(
    scores: list[LocatorRunScore],
    *,
    required_run_count: int = 3,
    maximum_center_jitter_pixels: float = 16,
) -> LocatorSeriesVerdict:
    if not scores:
        raise ValueError("locator series requires at least one run score")
    fixture_id = scores[0].fixture_id
    issues = [
        f"run {index + 1}: {issue}"
        for index, score in enumerate(scores)
        for issue in score.issues
    ]
    if any(score.fixture_id != fixture_id for score in scores):
        issues.append("series contains multiple fixture ids")
    points: dict[str, list[tuple[float, float]]] = {}
    for score in scores:
        for match in score.matches:
            if match.matched and match.candidate_center:
                points.setdefault(match.expected_id, []).append(match.candidate_center)
    jitter: dict[str, float] = {}
    for expected_id, centers in points.items():
        jitter[expected_id] = round(
            max((math.dist(left, right) for left, right in combinations(centers, 2)), default=0.0),
            3,
        )
    maximum_jitter = max(jitter.values(), default=0.0)
    if len(scores) < required_run_count:
        issues.append(
            f"series has {len(scores)} runs; {required_run_count} are required"
        )
    if any(score.expected_recall < 1.0 for score in scores):
        issues.append("at least one run has incomplete expected-target recall")
    if maximum_jitter > maximum_center_jitter_pixels:
        issues.append(
            f"maximum center jitter {maximum_jitter}px exceeds "
            f"{maximum_center_jitter_pixels}px"
        )
    return LocatorSeriesVerdict(
        fixture_id=fixture_id,
        run_count=len(scores),
        required_run_count=required_run_count,
        recall_by_run=[round(score.expected_recall, 6) for score in scores],
        missing_expected_ids_by_run=[score.missing_expected_ids for score in scores],
        maximum_center_jitter_pixels=maximum_jitter,
        jitter_by_expected_id=jitter,
        median_startup_seconds=round(median(score.startup_seconds for score in scores), 6),
        median_parse_seconds=round(median(score.parse_seconds for score in scores), 6),
        median_process_elapsed_seconds=round(
            median(score.process_elapsed_seconds for score in scores), 6
        ),
        maximum_peak_cuda_memory_bytes=max(
            score.peak_cuda_memory_bytes for score in scores
        ),
        issues=issues,
        passed=not issues,
    )


def write_locator_score(path: Path, score: BaseModel) -> Path:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        score.model_dump_json(by_alias=True, indent=2),
        encoding="utf-8",
    )
    return path


__all__ = [
    "LocatorRunScore",
    "LocatorSeriesVerdict",
    "LocatorTargetMatch",
    "score_locator_result",
    "score_locator_series",
    "write_locator_score",
]