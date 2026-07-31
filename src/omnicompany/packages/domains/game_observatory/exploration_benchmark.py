"""Paired, evidence-first scoring for manual and hypothesis exploration paths."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from omnicompany.core.config import omni_workspace_root

from .models import SourcePixelRect, utc_now


def _fold(value: str) -> str:
    return "".join(value.casefold().split())


class FrozenExplorationObservation(BaseModel):
    artifact_id: str
    frame_path: str
    sha256: str
    viewport_width: int = Field(gt=0)
    viewport_height: int = Field(gt=0)

    def resolved_frame_path(self) -> Path:
        path = Path(self.frame_path)
        if not path.is_absolute():
            path = omni_workspace_root() / path
        return path.resolve()


class ExpectedExplorationProbe(BaseModel):
    id: str
    target_names: list[str] = Field(min_length=1)
    action_type: Literal["tap", "swipe", "wait", "back", "stop"]
    target_bounds: SourcePixelRect | None = None
    importance: Literal["important", "normal"] = "normal"
    tags: list[str] = Field(default_factory=list)


class ExplorationBenchmarkFixture(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    schema_id: Literal["game-observatory.exploration-benchmark.v1"] = Field(
        default="game-observatory.exploration-benchmark.v1",
        alias="schema",
    )
    id: str
    title: str
    phase: Literal["calibration", "real", "holdout"]
    game_id: str
    build_scope_id: str
    start_state: str
    goal: str
    observation: FrozenExplorationObservation
    allowed_action_types: list[Literal["tap", "swipe", "wait", "back"]]
    forbidden_target_terms: list[str] = Field(default_factory=list)
    max_suggestions: int = Field(default=20, ge=1, le=100)
    expected_probes: list[ExpectedExplorationProbe] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    def shadow_scene(self, *, benchmark_run_id: str, output_root: Path) -> dict[str, Any]:
        frame = self.observation.resolved_frame_path()
        return {
            "mode": "shadow",
            "kind": "game-ui-exploration",
            "benchmark_run_id": benchmark_run_id,
            "fixture_id": self.id,
            "allowed_output_root": str(output_root.resolve()),
            "suggestion_ledger": str((output_root / "suggestions.jsonl").resolve()),
            "allowed_image_roots": [str(frame.parent)],
            "allowed_action_types": list(self.allowed_action_types),
            "forbidden_target_terms": list(self.forbidden_target_terms),
            "max_suggestions": self.max_suggestions,
            "observation": {
                "artifact_id": self.observation.artifact_id,
                "frame_path": str(frame),
                "sha256": self.observation.sha256,
                "viewport": {
                    "width": self.observation.viewport_width,
                    "height": self.observation.viewport_height,
                },
            },
        }


class ExplorationProbeRecord(BaseModel):
    schema_id: str = Field(alias="schema")
    id: str
    session_id: str
    iteration: int = Field(ge=0)
    benchmark_run_id: str | None = None
    proposed_at: str
    status: str
    executed: bool
    eligible_for_execution: bool
    observation: dict[str, Any]
    target_name: str
    action: dict[str, Any]
    target_bounds: dict[str, Any] | None = None
    expected_change: str
    rationale: str
    risk_flags: list[str] = Field(default_factory=list)
    policy_issues: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    generator: dict[str, Any] = Field(default_factory=dict)
    adjudication: dict[str, Any] = Field(default_factory=dict)


class ExplorationBenchmarkScore(BaseModel):
    fixture_id: str
    path: Literal["manual", "hypothesis"]
    generated_at: str = Field(default_factory=utc_now)
    proposal_count: int
    eligible_proposal_count: int
    matched_expected_ids: list[str]
    missing_expected_ids: list[str]
    matched_important_ids: list[str]
    missing_important_ids: list[str]
    expected_recall: float
    important_recall: float
    precision: float
    evidence_complete_rate: float
    duplicate_rate: float
    safety_violation_count: int
    unsupported_fact_count: int
    verified_novel_transition_count: int
    human_intervention_seconds: float = Field(ge=0)
    elapsed_seconds: float = Field(ge=0)
    issues: list[str] = Field(default_factory=list)


class PairedExplorationVerdict(BaseModel):
    fixture_id: str
    manual: ExplorationBenchmarkScore
    hypothesis: ExplorationBenchmarkScore
    quality_floor_passed: bool
    material_gains: list[str]
    regressions: list[str]
    strict_dominance: bool


def load_fixture(path: Path) -> ExplorationBenchmarkFixture:
    return ExplorationBenchmarkFixture.model_validate_json(path.read_text(encoding="utf-8"))


def load_probe_ledger(path: Path) -> tuple[list[ExplorationProbeRecord], list[str]]:
    records: list[ExplorationProbeRecord] = []
    issues: list[str] = []
    if not path.is_file():
        return records, [f"ledger is missing: {path}"]
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(ExplorationProbeRecord.model_validate_json(line))
        except ValueError as exc:
            issues.append(f"line {line_number}: {exc}")
    return records, issues


def _geometry_matches(record: ExplorationProbeRecord, expected: ExpectedExplorationProbe) -> bool:
    if expected.target_bounds is None:
        return True
    action = record.action
    try:
        x, y = int(action.get("x")), int(action.get("y"))
    except (TypeError, ValueError):
        return False
    return expected.target_bounds.contains(type("Point", (), {"x": x, "y": y})())


def _match_expected(
    record: ExplorationProbeRecord,
    expected: list[ExpectedExplorationProbe],
) -> str | None:
    folded_target = _fold(record.target_name)
    action_type = str(record.action.get("type") or "")
    for item in expected:
        if item.action_type != action_type:
            continue
        aliases = [_fold(name) for name in item.target_names]
        if not any(alias in folded_target or folded_target in alias for alias in aliases):
            continue
        if _geometry_matches(record, item):
            return item.id
    return None


def _evidence_complete(
    record: ExplorationProbeRecord,
    fixture: ExplorationBenchmarkFixture,
) -> bool:
    observation = record.observation
    viewport = observation.get("viewport") or {}
    base = (
        record.executed is False
        and observation.get("artifact_id") == fixture.observation.artifact_id
        and observation.get("sha256") == fixture.observation.sha256
        and viewport.get("width") == fixture.observation.viewport_width
        and viewport.get("height") == fixture.observation.viewport_height
        and fixture.observation.artifact_id in record.evidence_ids
        and bool(record.target_name.strip())
        and bool(record.expected_change.strip())
        and bool(record.rationale.strip())
        and bool(record.action.get("type"))
    )
    if record.action.get("type") == "tap":
        base = base and isinstance(record.target_bounds, dict)
    if record.action.get("type") == "swipe":
        base = base and all(record.action.get(key) is not None for key in ("x", "y", "x2", "y2"))
    return bool(base)


def score_probe_ledger(
    fixture: ExplorationBenchmarkFixture,
    ledger_path: Path,
    *,
    path: Literal["manual", "hypothesis"],
    human_intervention_seconds: float = 0,
    elapsed_seconds: float = 0,
) -> ExplorationBenchmarkScore:
    records, issues = load_probe_ledger(ledger_path)
    expected_by_id = {item.id: item for item in fixture.expected_probes}
    matched_ids: list[str] = []
    eligible_count = 0
    safety_violations = 0
    unsupported = 0
    novel = 0
    complete = 0
    proposal_keys: list[tuple[str, str]] = []

    for record in records:
        if _evidence_complete(record, fixture):
            complete += 1
        else:
            issues.append(f"{record.id}: evidence is incomplete")
        target_folded = _fold(record.target_name)
        forbidden = any(_fold(term) in target_folded for term in fixture.forbidden_target_terms)
        safety_failed = (
            not record.eligible_for_execution
            or bool(record.policy_issues)
            or bool(record.risk_flags)
            or forbidden
        )
        if safety_failed:
            safety_violations += 1
        else:
            eligible_count += 1
            matched = _match_expected(record, fixture.expected_probes)
            if matched:
                matched_ids.append(matched)
        proposal_keys.append((target_folded, str(record.action.get("type") or "")))
        if record.adjudication.get("unsupported_fact") is True:
            unsupported += 1
        if record.adjudication.get("verified_novel_transition") is True:
            novel += 1

    matched_unique = list(dict.fromkeys(matched_ids))
    expected_ids = list(expected_by_id)
    important_ids = [item.id for item in fixture.expected_probes if item.importance == "important"]
    matched_important = [item for item in matched_unique if item in important_ids]
    duplicate_count = len(proposal_keys) - len(set(proposal_keys))
    proposal_count = len(records)
    matched_eligible_count = len([item for item in matched_ids])
    return ExplorationBenchmarkScore(
        fixture_id=fixture.id,
        path=path,
        proposal_count=proposal_count,
        eligible_proposal_count=eligible_count,
        matched_expected_ids=matched_unique,
        missing_expected_ids=[item for item in expected_ids if item not in matched_unique],
        matched_important_ids=matched_important,
        missing_important_ids=[item for item in important_ids if item not in matched_important],
        expected_recall=(len(matched_unique) / len(expected_ids)) if expected_ids else 1.0,
        important_recall=(len(matched_important) / len(important_ids)) if important_ids else 1.0,
        precision=(matched_eligible_count / eligible_count) if eligible_count else 0.0,
        evidence_complete_rate=(complete / proposal_count) if proposal_count else 0.0,
        duplicate_rate=(duplicate_count / proposal_count) if proposal_count else 0.0,
        safety_violation_count=safety_violations,
        unsupported_fact_count=unsupported,
        verified_novel_transition_count=novel,
        human_intervention_seconds=human_intervention_seconds,
        elapsed_seconds=elapsed_seconds,
        issues=list(dict.fromkeys(issues)),
    )


def compare_paired_scores(
    manual: ExplorationBenchmarkScore,
    hypothesis: ExplorationBenchmarkScore,
) -> PairedExplorationVerdict:
    if manual.fixture_id != hypothesis.fixture_id:
        raise ValueError("paired scores must use the same fixture")
    regressions: list[str] = []
    if hypothesis.evidence_complete_rate < 1:
        regressions.append("hypothesis evidence completeness is below 100%")
    if hypothesis.safety_violation_count:
        regressions.append("hypothesis has safety violations")
    if hypothesis.unsupported_fact_count:
        regressions.append("hypothesis has unsupported factual claims")
    if hypothesis.expected_recall < manual.expected_recall:
        regressions.append("hypothesis expected-probe recall is below manual baseline")
    if hypothesis.important_recall < manual.important_recall:
        regressions.append("hypothesis important-probe recall is below manual baseline")

    gains: list[str] = []
    if hypothesis.verified_novel_transition_count > manual.verified_novel_transition_count:
        gains.append("more verified novel transitions")
    if (
        hypothesis.expected_recall >= manual.expected_recall
        and manual.proposal_count > 0
        and hypothesis.proposal_count <= math.floor(manual.proposal_count * 0.85)
    ):
        gains.append("at least 15% fewer proposals at equal recall")
    if (
        manual.human_intervention_seconds > 0
        and hypothesis.human_intervention_seconds <= manual.human_intervention_seconds * 0.8
    ):
        gains.append("at least 20% less human intervention")
    if hypothesis.expected_recall >= manual.expected_recall + 0.05:
        gains.append("at least 5 percentage points higher recall")

    quality_floor = not regressions
    return PairedExplorationVerdict(
        fixture_id=manual.fixture_id,
        manual=manual,
        hypothesis=hypothesis,
        quality_floor_passed=quality_floor,
        material_gains=gains,
        regressions=regressions,
        strict_dominance=quality_floor and bool(gains),
    )


def write_score(path: Path, value: BaseModel) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.model_dump_json(indent=2), encoding="utf-8")
    return path


__all__ = [
    "ExplorationBenchmarkFixture",
    "ExplorationBenchmarkScore",
    "ExplorationProbeRecord",
    "ExpectedExplorationProbe",
    "FrozenExplorationObservation",
    "PairedExplorationVerdict",
    "compare_paired_scores",
    "load_fixture",
    "load_probe_ledger",
    "score_probe_ledger",
    "write_score",
]