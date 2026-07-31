"""Aggregate every candidate route replay under one acceptance trust root."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from ..store import ObservatoryStore
from .route_replay import (
    RouteReplayAssessmentV1,
    _sha256,
    _trusted_candidate_from_acceptance,
    assess_candidate_route_replay_from_acceptance,
)


class RouteReplaySuiteInputItemV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route_id: str = Field(min_length=1)
    verification_path: str = Field(min_length=1)


class RouteReplaySuiteInputV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["game-observatory.ai-player.route-replay-suite-input.v1"] = Field(
        default="game-observatory.ai-player.route-replay-suite-input.v1",
        alias="schema",
    )
    benchmark_id: str = Field(min_length=1)
    items: tuple[RouteReplaySuiteInputItemV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_route_ids(self) -> "RouteReplaySuiteInputV1":
        route_ids = [item.route_id for item in self.items]
        if len(route_ids) != len(set(route_ids)):
            raise ValueError("route replay suite input contains duplicate route ids")
        return self


class RouteReplaySuiteResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route_id: str
    candidate_route_path: str
    verification_path: str
    assessment: RouteReplayAssessmentV1 | None = None
    error: str | None = None

    @model_validator(mode="after")
    def exactly_one_outcome(self) -> "RouteReplaySuiteResultV1":
        if (self.assessment is None) == (self.error is None):
            raise ValueError("suite route result requires exactly one assessment or error")
        return self


class RouteReplaySuiteAssessmentV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["game-observatory.ai-player.route-replay-suite-assessment.v1"] = Field(
        default="game-observatory.ai-player.route-replay-suite-assessment.v1",
        alias="schema",
    )
    benchmark_id: str
    acceptance_manifest_sha256: str = Field(min_length=64, max_length=64)
    candidate_manifest_sha256: str = Field(min_length=64, max_length=64)
    suite_input_sha256: str = Field(min_length=64, max_length=64)
    execution_evidence_pass: bool
    results: tuple[RouteReplaySuiteResultV1, ...]
    issues: tuple[str, ...]

    @computed_field(return_type=int)
    @property
    def candidate_route_count(self) -> int:
        return len(self.results)

    @computed_field(return_type=Literal["execution_evidence_only"])
    @property
    def assessment_scope(self) -> Literal["execution_evidence_only"]:
        return "execution_evidence_only"

    @computed_field(return_type=Literal["unadjudicated"])
    @property
    def semantic_goal_status(self) -> Literal["unadjudicated"]:
        return "unadjudicated"

    @computed_field(return_type=Literal[False])
    @property
    def replay_suite_can_be_frozen(self) -> Literal[False]:
        return False


def assess_route_replay_suite_from_acceptance(
    suite_input_path: Path,
    acceptance_manifest_path: Path,
    workspace_root: Path,
    store: ObservatoryStore,
    *,
    expected_game_id: str | None = None,
    expected_build_scope_id: str | None = None,
) -> RouteReplaySuiteAssessmentV1:
    workspace_root = workspace_root.resolve()
    suite_input_path = suite_input_path.resolve()
    acceptance_manifest_path = acceptance_manifest_path.resolve()
    if not suite_input_path.is_relative_to(workspace_root):
        raise ValueError("route replay suite input escapes workspace root")
    suite_input = RouteReplaySuiteInputV1.model_validate_json(
        suite_input_path.read_text(encoding="utf-8")
    )
    candidate_manifest_path, trusted_candidate_sha, acceptance_sha = (
        _trusted_candidate_from_acceptance(
            acceptance_manifest_path,
            workspace_root,
            suite_input.benchmark_id,
        )
    )
    candidate_manifest = json.loads(candidate_manifest_path.read_text(encoding="utf-8"))
    route_refs = list(candidate_manifest.get("collections", {}).get("routes", []))
    expected_route_ids = [str(item.get("id") or "") for item in route_refs]
    supplied = {item.route_id: item for item in suite_input.items}
    supplied_route_ids = list(supplied)
    issues: list[str] = []
    if len(expected_route_ids) != len(set(expected_route_ids)):
        issues.append("candidate manifest contains duplicate route ids")
    missing = sorted(set(expected_route_ids) - set(supplied_route_ids))
    extra = sorted(set(supplied_route_ids) - set(expected_route_ids))
    if missing:
        issues.append("suite input is missing candidate routes: " + ", ".join(missing))
    if extra:
        issues.append("suite input has non-candidate routes: " + ", ".join(extra))

    results: list[RouteReplaySuiteResultV1] = []
    for route_ref in route_refs:
        route_id = str(route_ref.get("id") or "")
        input_item = supplied.get(route_id)
        if input_item is None:
            continue
        candidate_reference = Path(str(route_ref.get("path") or ""))
        candidate_route_path = (candidate_manifest_path.parent / candidate_reference).resolve()
        verification_path = (workspace_root / input_item.verification_path).resolve()
        if not candidate_route_path.is_relative_to(candidate_manifest_path.parent):
            error = "candidate route path escapes candidate manifest directory"
            issues.append(f"{route_id}: {error}")
            results.append(
                RouteReplaySuiteResultV1(
                    route_id=route_id,
                    candidate_route_path=str(candidate_route_path),
                    verification_path=str(verification_path),
                    error=error,
                )
            )
            continue
        if not verification_path.is_relative_to(workspace_root):
            error = "verification path escapes workspace root"
            issues.append(f"{route_id}: {error}")
            results.append(
                RouteReplaySuiteResultV1(
                    route_id=route_id,
                    candidate_route_path=str(candidate_route_path),
                    verification_path=str(verification_path),
                    error=error,
                )
            )
            continue
        try:
            assessment = assess_candidate_route_replay_from_acceptance(
                candidate_route_path,
                acceptance_manifest_path,
                workspace_root,
                suite_input.benchmark_id,
                verification_path,
                store,
                expected_game_id=expected_game_id,
                expected_build_scope_id=expected_build_scope_id,
            )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            message = f"{type(error).__name__}: {error}"
            issues.append(f"{route_id}: {message}")
            results.append(
                RouteReplaySuiteResultV1(
                    route_id=route_id,
                    candidate_route_path=str(candidate_route_path),
                    verification_path=str(verification_path),
                    error=message,
                )
            )
            continue
        if not assessment.execution_evidence_pass:
            issues.append(f"{route_id}: execution evidence failed")
        results.append(
            RouteReplaySuiteResultV1(
                route_id=route_id,
                candidate_route_path=str(candidate_route_path),
                verification_path=str(verification_path),
                assessment=assessment,
            )
        )

    run_ids = [
        run_id
        for result in results
        if result.assessment is not None
        for run_id in result.assessment.evidence_run_ids
    ]
    step_ids = [
        step_id
        for result in results
        if result.assessment is not None
        for step_id in result.assessment.evidence_step_ids
    ]
    if len(run_ids) != len(set(run_ids)):
        issues.append("route replay suite reuses an evidence run across routes")
    if len(step_ids) != len(set(step_ids)):
        issues.append("route replay suite reuses an evidence step across routes")
    issues = list(dict.fromkeys(issues))
    complete = len(results) == len(route_refs) == len(suite_input.items)
    passed = complete and not issues and all(
        result.assessment is not None and result.assessment.execution_evidence_pass
        for result in results
    )
    return RouteReplaySuiteAssessmentV1(
        benchmark_id=suite_input.benchmark_id,
        acceptance_manifest_sha256=acceptance_sha,
        candidate_manifest_sha256=trusted_candidate_sha,
        suite_input_sha256=_sha256(suite_input_path),
        execution_evidence_pass=passed,
        results=results,
        issues=issues,
    )


def write_route_replay_suite_assessment(
    suite_input_path: Path,
    acceptance_manifest_path: Path,
    workspace_root: Path,
    store_root: Path,
    output_path: Path,
    *,
    expected_game_id: str | None = None,
    expected_build_scope_id: str | None = None,
) -> RouteReplaySuiteAssessmentV1:
    assessment = assess_route_replay_suite_from_acceptance(
        suite_input_path,
        acceptance_manifest_path,
        workspace_root,
        ObservatoryStore(store_root),
        expected_game_id=expected_game_id,
        expected_build_scope_id=expected_build_scope_id,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            assessment.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            indent=2,
        )
        + "\
",
        encoding="utf-8",
    )
    return assessment


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite-input", type=Path, required=True)
    parser.add_argument("--acceptance-manifest", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    parser.add_argument("--store-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-game-id")
    parser.add_argument("--expected-build-scope-id")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    assessment = write_route_replay_suite_assessment(
        args.suite_input,
        args.acceptance_manifest,
        args.workspace_root,
        args.store_root,
        args.output,
        expected_game_id=args.expected_game_id,
        expected_build_scope_id=args.expected_build_scope_id,
    )
    return int(not assessment.execution_evidence_pass)


if __name__ == "__main__":
    raise SystemExit(main())