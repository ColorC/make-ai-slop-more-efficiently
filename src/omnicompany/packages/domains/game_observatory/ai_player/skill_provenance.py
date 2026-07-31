"""Canonical post-execution evidence writer for trusted skill-run validation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from ..models import (
    EvidenceRun,
    EvidenceRunManifest,
    EvidenceStep,
    NormalizedAction,
    utc_now,
)
from .contracts import EvidenceReferenceV1, SkillVersionV1
from .store import AIPlayerStore


def _dedupe_references(
    references: Sequence[EvidenceReferenceV1],
) -> list[EvidenceReferenceV1]:
    unique: dict[str, EvidenceReferenceV1] = {}
    for reference in references:
        unique.setdefault(reference.model_dump_json(by_alias=True), reference)
    return list(unique.values())


class SkillRunEvidenceWriter:
    """Writes terminal validation telemetry after the outcome is known."""

    def __init__(self, store: AIPlayerStore) -> None:
        self.store = store

    def write(
        self,
        *,
        skill: SkillVersionV1,
        validator_id: str,
        run_id: str,
        independent_reset_id: str,
        visual_variant_id: str,
        outcome: str,
        precondition_satisfied: bool,
        objective_success: bool,
        validation_passed: bool,
        false_success: bool,
        safety_violation_count: int,
        recovery_attempted: bool,
        recovery_succeeded: bool,
        action_count: int,
        model_input_tokens: int,
        baseline_model_input_tokens: int,
        decision_latency_ms: float,
        baseline_decision_latency_ms: float,
        source_evidence_refs: Sequence[EvidenceReferenceV1],
    ) -> tuple[list[EvidenceReferenceV1], str, list[str]]:
        environment = self.store.get_environment(skill.environment_id)
        if environment is None:
            raise ValueError("skill-run evidence writer cannot find its environment")
        source_refs = _dedupe_references(source_evidence_refs)
        self.store.resolve_evidence_references(source_refs)
        artifact_ids = sorted(
            {
                artifact_id
                for reference in source_refs
                for artifact_id in reference.artifact_ids
            }
        )
        if not artifact_ids:
            raise ValueError("skill-run validation requires at least one captured artifact")
        telemetry: dict[str, Any] = {
            "skill_version_id": skill.id,
            "skill_run_id": run_id,
            "validator_id": validator_id,
            "independent_reset_id": independent_reset_id,
            "visual_variant_id": visual_variant_id,
            "outcome": outcome,
            "precondition_satisfied": precondition_satisfied,
            "objective_success": objective_success,
            "validation_passed": validation_passed,
            "false_success": false_success,
            "safety_violation_count": safety_violation_count,
            "recovery_attempted": recovery_attempted,
            "recovery_succeeded": recovery_succeeded,
            "action_count": action_count,
            "model_input_tokens": model_input_tokens,
            "baseline_model_input_tokens": baseline_model_input_tokens,
            "decision_latency_ms": decision_latency_ms,
            "baseline_decision_latency_ms": baseline_decision_latency_ms,
        }
        identity = json.dumps(
            {
                "environment_id": skill.environment_id,
                "skill_version_id": skill.id,
                "validator_id": validator_id,
                "run_id": run_id,
                "telemetry": telemetry,
                "source_evidence_refs": [
                    reference.model_dump(mode="json", by_alias=True)
                    for reference in source_refs
                ],
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        evidence_run_id = f"evidence.skill-validation.{digest}"
        evidence_step_id = f"evidence-step.skill-validation.{digest}"
        manifest_id = f"manifest.skill-validation.{digest}"
        timestamp = utc_now()
        terminal_status = (
            "failed"
            if outcome in {"failed", "false_success"}
            else "stopped"
            if outcome == "interrupted"
            else "passed"
        )
        step = EvidenceStep(
            id=evidence_step_id,
            evidence_run_id=evidence_run_id,
            step_index=1,
            status=terminal_status,
            ended_at=timestamp,
            before_frame_id=artifact_ids[0],
            action=NormalizedAction(type="wait", seconds=0),
            after_frame_id=artifact_ids[-1],
            artifact_ids=artifact_ids,
            viewport_width=environment.viewport_width,
            viewport_height=environment.viewport_height,
        )
        evidence_run = EvidenceRun(
            id=evidence_run_id,
            target_id=environment.device_scope_id,
            adapter="ai-player-skill-validation-writer",
            status=terminal_status,
            game_id=environment.game_id,
            build_scope_id=environment.build_scope_id,
            scope_id=environment.id,
            viewport_width=environment.viewport_width,
            viewport_height=environment.viewport_height,
            orientation=(
                "landscape"
                if environment.viewport_width >= environment.viewport_height
                else "portrait"
            ),
            environment={
                "environment_id": environment.id,
                "skill_validation": telemetry,
                "source_evidence_refs": [
                    reference.model_dump(mode="json", by_alias=True)
                    for reference in source_refs
                ],
            },
            ended_at=timestamp,
            step_ids=[step.id],
            artifact_ids=artifact_ids,
            manifest_id=manifest_id,
        )
        manifest = EvidenceRunManifest(
            id=manifest_id,
            evidence_run_id=evidence_run.id,
            run=evidence_run,
            steps=[step],
            artifact_ids=artifact_ids,
            action_run_ids=[],
            observation_run_ids=[],
            publishable=True,
        )
        observatory = self.store.observatory_store
        existing_run = observatory.get_evidence_run(evidence_run.id)
        existing_step = observatory.get_evidence_step(step.id)
        existing_manifest = observatory.get_evidence_manifest(evidence_run.id)
        existing_values = (existing_run, existing_step, existing_manifest)
        expected_values = (evidence_run, step, manifest)
        if any(value is not None for value in existing_values):
            if existing_values != expected_values:
                raise ValueError("skill-run validation evidence id already has different content")
        else:
            observatory.save_evidence_run(evidence_run)
            observatory.save_evidence_step(step)
            observatory.save_evidence_manifest(manifest)
        provenance_ref = EvidenceReferenceV1(
            environment_id=environment.id,
            artifact_ids=artifact_ids,
            evidence_run_ids=[evidence_run.id],
            evidence_step_ids=[step.id],
            note="独立验证者在结果确定后写入的技能运行证明。",
        )
        return _dedupe_references([*source_refs, provenance_ref]), evidence_run.id, [step.id]


__all__ = ["SkillRunEvidenceWriter"]