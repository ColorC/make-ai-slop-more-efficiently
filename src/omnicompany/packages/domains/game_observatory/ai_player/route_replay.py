"""Fail-closed assessment for linking live evidence routes to candidate routes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from ..evidence_route import EvidenceRoute, evidence_route_sha256
from ..store import ObservatoryStore


class RouteReplayAssessmentV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["game-observatory.ai-player.route-replay-assessment.v1"] = Field(
        default="game-observatory.ai-player.route-replay-assessment.v1",
        alias="schema",
    )
    candidate_route_id: str
    benchmark_id: str | None = None
    acceptance_manifest_sha256: str | None = Field(
        default=None, min_length=64, max_length=64
    )
    candidate_manifest_sha256: str = Field(min_length=64, max_length=64)
    candidate_route_sha256: str = Field(min_length=64, max_length=64)
    verification_sha256: str = Field(min_length=64, max_length=64)
    execution_evidence_pass: bool
    evidence_run_ids: tuple[str, ...]
    evidence_manifest_ids: tuple[str, ...]
    evidence_step_ids: tuple[str, ...]
    issues: tuple[str, ...]

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
    def replay_can_be_frozen(self) -> Literal[False]:
        return False


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _corrupt_text(value: str | None) -> bool:
    if not value:
        return False
    if "�" in value or re.search(r"\?{2,}", value):
        return True
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in value):
        return True
    for encoding in ("latin-1", "gbk"):
        try:
            repaired = value.encode(encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if repaired != value and all(character.isprintable() for character in repaired):
            return True
    return False


def _collapse_adjacent(values: list[str]) -> list[str]:
    collapsed: list[str] = []
    for value in values:
        if not collapsed or collapsed[-1] != value:
            collapsed.append(value)
    return collapsed


def _artifact_issue(store: ObservatoryStore, artifact_id: str) -> str | None:
    artifact = store.get_artifact(artifact_id)
    if artifact is None:
        return f"dead artifact: {artifact_id}"
    path = Path(artifact.path)
    if not path.is_file():
        return f"missing artifact file: {artifact_id}"
    if _sha256(path) != artifact.sha256:
        return f"artifact hash mismatch: {artifact_id}"
    return None


def assess_candidate_route_replay(
    candidate_route_path: Path,
    candidate_manifest_path: Path,
    expected_candidate_manifest_sha256: str,
    verification_path: Path,
    store: ObservatoryStore,
    *,
    expected_game_id: str | None = None,
    expected_build_scope_id: str | None = None,
    benchmark_id: str | None = None,
    acceptance_manifest_sha256: str | None = None,
) -> RouteReplayAssessmentV1:
    """Check execution evidence without claiming that the semantic goal is true."""

    candidate_route_path = candidate_route_path.resolve()
    candidate_manifest_path = candidate_manifest_path.resolve()
    verification_path = verification_path.resolve()
    candidate = json.loads(candidate_route_path.read_text(encoding="utf-8"))
    candidate_manifest = json.loads(candidate_manifest_path.read_text(encoding="utf-8"))
    verification = json.loads(verification_path.read_text(encoding="utf-8"))
    route = EvidenceRoute.model_validate(verification.get("route"))
    route_definition_sha256 = evidence_route_sha256(route)
    issues: list[str] = []
    candidate_id = str(candidate.get("id", ""))
    interaction_ids = list(candidate.get("interaction_ids", []))
    candidate_route_sha256 = _sha256(candidate_route_path)
    candidate_manifest_sha256 = _sha256(candidate_manifest_path)

    if candidate_manifest_sha256 != expected_candidate_manifest_sha256:
        issues.append("candidate manifest does not match frozen acceptance hash")
    if (
        candidate_manifest.get("schema")
        != "game-observatory.ai-player.afk-freeze-candidate-manifest.v1"
    ):
        issues.append("unsupported candidate manifest schema")
    if benchmark_id is not None and candidate_manifest.get("benchmark_id") != benchmark_id:
        issues.append("candidate manifest benchmark does not match acceptance benchmark")
    manifest_routes = [
        item
        for item in candidate_manifest.get("collections", {}).get("routes", [])
        if item.get("id") == candidate_id
    ]
    if not manifest_routes:
        issues.append("candidate route is absent from frozen candidate manifest")
    elif len(manifest_routes) != 1:
        issues.append("candidate route is duplicated in frozen candidate manifest")
    else:
        manifest_route = manifest_routes[0]
        route_reference = Path(str(manifest_route.get("path") or ""))
        if route_reference.is_absolute():
            issues.append("candidate route path must be relative")
        manifest_route_path = (candidate_manifest_path.parent / route_reference).resolve()
        if not manifest_route_path.is_relative_to(candidate_manifest_path.parent):
            issues.append("candidate route path escapes candidate manifest directory")
        elif manifest_route_path != candidate_route_path:
            issues.append("candidate route path does not match frozen candidate manifest")
        if manifest_route.get("sha256") != candidate_route_sha256:
            issues.append("candidate route hash does not match frozen candidate manifest")

    if verification.get("schema") != "game-observatory.evidence-route-verification.v1":
        issues.append("unsupported route verification schema")
    if route.schema_id != "game-observatory.evidence-route.v1":
        issues.append("unsupported inner evidence route schema")
    if route.id != candidate_id:
        issues.append(f"route id mismatch: {route.id} != {candidate_id}")
    if route.start_state != candidate.get("start_state_id"):
        issues.append("route start state does not match candidate")
    if route.end_state != candidate.get("goal_state_id"):
        issues.append("route end state does not match candidate")
    physical_step_ids = [step.id for step in route.steps]
    if _collapse_adjacent(physical_step_ids) != interaction_ids:
        issues.append("route semantic step order does not match candidate interactions")
    if len(route.steps) > int(candidate.get("action_budget", 0)):
        issues.append("route exceeds candidate action budget")
    if expected_game_id is not None and route.game_id != expected_game_id:
        issues.append("route game does not match expected game")
    if expected_build_scope_id is not None and route.build_scope_id != expected_build_scope_id:
        issues.append("route build does not match expected build")
    if _corrupt_text(route.title):
        issues.append("route title contains corrupted text")
    for step in route.steps:
        if _corrupt_text(step.target_name):
            issues.append(f"route target name contains corrupted text: {step.id}")

    requested = int(verification.get("requested_repetitions", 0))
    completed = int(verification.get("completed_repetitions", 0))
    runs = list(verification.get("runs", []))
    if not verification.get("ok"):
        issues.append("route verification did not pass")
    if requested < 1 or completed != requested or len(runs) != requested:
        issues.append("route repetition counts are incomplete")

    evidence_run_ids: list[str] = []
    evidence_step_ids: list[str] = []
    manifest_ids: list[str] = []
    last_manifest_generated_at: str | None = None
    for expected_repetition, run_summary in enumerate(runs, start=1):
        if run_summary.get("repetition") != expected_repetition:
            issues.append(f"route repetition index mismatch: {expected_repetition}")
        evidence_run_id = str(run_summary.get("evidence_run_id") or "")
        if not evidence_run_id:
            issues.append("route repetition lacks evidence run id")
            continue
        evidence_run_ids.append(evidence_run_id)
        manifest_id = str(run_summary.get("manifest_id") or "")
        manifest_ids.append(manifest_id)
        evidence_run = store.get_evidence_run(evidence_run_id)
        manifest = store.get_evidence_manifest(evidence_run_id)
        if evidence_run is None:
            issues.append(f"dead evidence run: {evidence_run_id}")
            continue
        if manifest is None:
            issues.append(f"dead evidence manifest: {evidence_run_id}")
        else:
            last_manifest_generated_at = manifest.generated_at
            if (
                manifest.id != run_summary.get("manifest_id")
                or not manifest.publishable
                or manifest.publication_issues
            ):
                issues.append(f"evidence manifest is not publishable: {evidence_run_id}")
            if manifest.run != evidence_run:
                issues.append(f"evidence manifest run snapshot mismatch: {evidence_run_id}")
        if not run_summary.get("ok") or not run_summary.get("publishable"):
            issues.append(f"route repetition is not publishable: {evidence_run_id}")
        if run_summary.get("publication_issues"):
            issues.append(f"route repetition has publication issues: {evidence_run_id}")
        if evidence_run.game_id != route.game_id:
            issues.append(f"evidence run game mismatch: {evidence_run_id}")
        if evidence_run.build_scope_id != route.build_scope_id:
            issues.append(f"evidence run build mismatch: {evidence_run_id}")
        if evidence_run.environment.get("route_id") != candidate_id:
            issues.append(f"evidence run route identity mismatch: {evidence_run_id}")
        expected_environment = {
            "route_title": route.title,
            "route_repetition": expected_repetition,
            "route_start_state": route.start_state,
            "route_end_state": route.end_state,
            "route_definition_sha256": route_definition_sha256,
        }
        for key, expected_value in expected_environment.items():
            if evidence_run.environment.get(key) != expected_value:
                issues.append(f"evidence run {key} mismatch: {evidence_run_id}")
        run_truth = {
            "target_id": route.target_id,
            "viewport_width": route.viewport_width,
            "viewport_height": route.viewport_height,
            "scope_id": route.scope_id,
            "status": "passed",
            "manifest_id": run_summary.get("manifest_id"),
            "error": run_summary.get("error"),
        }
        for field_name, expected_value in run_truth.items():
            if getattr(evidence_run, field_name) != expected_value:
                issues.append(f"evidence run {field_name} mismatch: {evidence_run_id}")

        run_steps = list(run_summary.get("steps", []))
        summary_step_ids = [str(item.get("route_step_id") or "") for item in run_steps]
        if summary_step_ids != physical_step_ids:
            issues.append(f"physical evidence step order mismatch: {evidence_run_id}")
        stored_step_ids = [str(item.get("evidence_step_id") or "") for item in run_steps]
        if evidence_run.step_ids != stored_step_ids:
            issues.append(f"evidence run step set mismatch: {evidence_run_id}")
        if manifest is not None and [step.id for step in manifest.steps] != stored_step_ids:
            issues.append(f"evidence manifest step set mismatch: {evidence_run_id}")
        for expected_index, step_summary in enumerate(run_steps, start=1):
            evidence_step_id = str(step_summary.get("evidence_step_id") or "")
            evidence_step_ids.append(evidence_step_id)
            step = store.get_evidence_step(evidence_step_id)
            if step is None:
                issues.append(f"dead evidence step: {evidence_step_id}")
                continue
            if (
                manifest is not None
                and expected_index <= len(manifest.steps)
                and manifest.steps[expected_index - 1] != step
            ):
                issues.append(f"evidence manifest step snapshot mismatch: {evidence_step_id}")
            if step.evidence_run_id != evidence_run_id or step.step_index != expected_index:
                issues.append(f"evidence step identity mismatch: {evidence_step_id}")
            route_step = route.steps[expected_index - 1]
            if step.action != route_step.action:
                issues.append(f"evidence action does not match route: {evidence_step_id}")
            if step.target_name != route_step.target_name:
                issues.append(f"evidence target name does not match route: {evidence_step_id}")
            if step.target_bounds != route_step.target_bounds:
                issues.append(f"evidence target bounds do not match route: {evidence_step_id}")
            summary_truth = {
                "expected_index": expected_index,
                "actual_index": step.step_index,
                "status": step.status,
                "before_frame_id": step.before_frame_id,
                "after_frame_id": step.after_frame_id,
                "video_artifact_id": step.video_artifact_id,
                "action_run_id": step.action_run_id,
                "intermediate_frame_count": len(step.intermediate_frame_ids),
                "stability": step.stability.model_dump(mode="json"),
                "quality_issues": step.quality_issues,
                "error": step.error,
            }
            for field_name, expected_value in summary_truth.items():
                if step_summary.get(field_name) != expected_value:
                    issues.append(
                        f"evidence summary {field_name} mismatch: {evidence_step_id}"
                    )
            issues.extend(step.publication_issues())
            if _corrupt_text(step.target_name):
                issues.append(f"evidence target name contains corrupted text: {evidence_step_id}")
            for artifact_id in step.artifact_ids:
                artifact_issue = _artifact_issue(store, artifact_id)
                if artifact_issue:
                    issues.append(artifact_issue)
            if step.action_run_id and store.get_run(step.action_run_id) is None:
                issues.append(f"dead action run: {step.action_run_id}")

    if len(evidence_run_ids) != len(set(evidence_run_ids)):
        issues.append("route repetitions reuse an evidence run")
    if len(manifest_ids) != len(set(manifest_ids)):
        issues.append("route repetitions reuse an evidence manifest")
    if len(evidence_step_ids) != len(set(evidence_step_ids)):
        issues.append("route repetitions reuse an evidence step")
    if runs and verification.get("generated_at") != last_manifest_generated_at:
        issues.append("verification generated_at does not match final evidence manifest")

    issues = list(dict.fromkeys(issues))
    return RouteReplayAssessmentV1(
        candidate_route_id=candidate_id,
        benchmark_id=benchmark_id,
        acceptance_manifest_sha256=acceptance_manifest_sha256,
        candidate_manifest_sha256=candidate_manifest_sha256,
        candidate_route_sha256=candidate_route_sha256,
        verification_sha256=_sha256(verification_path),
        execution_evidence_pass=not issues,
        evidence_run_ids=evidence_run_ids,
        evidence_manifest_ids=manifest_ids,
        evidence_step_ids=evidence_step_ids,
        issues=issues,
    )


def _trusted_candidate_from_acceptance(
    acceptance_manifest_path: Path,
    workspace_root: Path,
    benchmark_id: str,
) -> tuple[Path, str, str]:
    acceptance_manifest_path = acceptance_manifest_path.resolve()
    workspace_root = workspace_root.resolve()
    if not acceptance_manifest_path.is_relative_to(workspace_root):
        raise ValueError("acceptance manifest escapes workspace root")
    payload = json.loads(acceptance_manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema") != "ai-player-acceptance-manifest.v1":
        raise ValueError("unsupported AI-player acceptance manifest schema")
    targets = [
        item for item in payload.get("targets", []) if item.get("benchmark_id") == benchmark_id
    ]
    if not targets:
        raise ValueError(f"benchmark is absent from acceptance manifest: {benchmark_id}")
    if len(targets) != 1:
        raise ValueError(f"benchmark is duplicated in acceptance manifest: {benchmark_id}")
    target = targets[0]
    candidate_manifest_path = (workspace_root / str(target["candidate_manifest"])).resolve()
    if not candidate_manifest_path.is_relative_to(workspace_root):
        raise ValueError("candidate manifest escapes workspace root")
    if not candidate_manifest_path.is_file():
        raise ValueError("candidate manifest does not exist")
    trusted_sha256 = str(target.get("candidate_manifest_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", trusted_sha256):
        raise ValueError("acceptance manifest has no valid candidate manifest SHA-256")
    return candidate_manifest_path, trusted_sha256, _sha256(acceptance_manifest_path)


def assess_candidate_route_replay_from_acceptance(
    candidate_route_path: Path,
    acceptance_manifest_path: Path,
    workspace_root: Path,
    benchmark_id: str,
    verification_path: Path,
    store: ObservatoryStore,
    *,
    expected_game_id: str | None = None,
    expected_build_scope_id: str | None = None,
) -> RouteReplayAssessmentV1:
    """Assess a route using the goal acceptance manifest as the trust root."""

    candidate_manifest_path, trusted_sha256, acceptance_sha256 = (
        _trusted_candidate_from_acceptance(
            acceptance_manifest_path,
            workspace_root,
            benchmark_id,
        )
    )
    return assess_candidate_route_replay(
        candidate_route_path,
        candidate_manifest_path,
        trusted_sha256,
        verification_path,
        store,
        expected_game_id=expected_game_id,
        expected_build_scope_id=expected_build_scope_id,
        benchmark_id=benchmark_id,
        acceptance_manifest_sha256=acceptance_sha256,
    )


def write_route_replay_assessment(
    candidate_route_path: Path,
    candidate_manifest_path: Path,
    expected_candidate_manifest_sha256: str,
    verification_path: Path,
    store_root: Path,
    output_path: Path,
    *,
    expected_game_id: str | None = None,
    expected_build_scope_id: str | None = None,
) -> RouteReplayAssessmentV1:
    assessment = assess_candidate_route_replay(
        candidate_route_path,
        candidate_manifest_path,
        expected_candidate_manifest_sha256,
        verification_path,
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
        + "\n",
        encoding="utf-8",
    )
    return assessment


def write_route_replay_assessment_from_acceptance(
    candidate_route_path: Path,
    acceptance_manifest_path: Path,
    workspace_root: Path,
    benchmark_id: str,
    verification_path: Path,
    store_root: Path,
    output_path: Path,
    *,
    expected_game_id: str | None = None,
    expected_build_scope_id: str | None = None,
) -> RouteReplayAssessmentV1:
    assessment = assess_candidate_route_replay_from_acceptance(
        candidate_route_path,
        acceptance_manifest_path,
        workspace_root,
        benchmark_id,
        verification_path,
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
        + "\n",
        encoding="utf-8",
    )
    return assessment


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-route", type=Path, required=True)
    parser.add_argument("--acceptance-manifest", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    parser.add_argument("--benchmark-id", required=True)
    parser.add_argument("--verification", type=Path, required=True)
    parser.add_argument("--store-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-game-id")
    parser.add_argument("--expected-build-scope-id")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    assessment = write_route_replay_assessment_from_acceptance(
        args.candidate_route,
        args.acceptance_manifest,
        args.workspace_root,
        args.benchmark_id,
        args.verification,
        args.store_root,
        args.output,
        expected_game_id=args.expected_game_id,
        expected_build_scope_id=args.expected_build_scope_id,
    )
    return int(not assessment.execution_evidence_pass)


if __name__ == "__main__":
    raise SystemExit(main())
