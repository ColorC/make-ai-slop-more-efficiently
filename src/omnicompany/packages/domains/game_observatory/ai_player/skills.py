"""Evidence-backed procedural-skill lifecycle for the long-running AI player.

The lifecycle deliberately separates an immutable candidate, individual replay records,
an aggregate validation, and a successor version selected for execution. A successful
trace can create a candidate, but it cannot make that candidate preferred.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from ..models import utc_now
from .contracts import (
    EnvironmentScopeV1,
    EvidenceReferenceV1,
    FrontierTaskV1,
    SkillApplicabilityScopeV1,
    SkillLocatorV1,
    SkillRunV1,
    SkillStepV1,
    SkillValidationV1,
    SkillVersionV1,
)
from .skill_validation import derive_skill_validation
from .store import AIPlayerStore


class SkillLifecycleError(ValueError):
    """Raised when an immutable skill lifecycle transition is not valid."""


def build_skill_version(**values: object) -> SkillVersionV1:
    """Build a skill version with a hash derived from its executable content."""

    normalized = dict(values)
    normalized.pop("content_sha256", None)
    normalized["applicability_scope"] = SkillApplicabilityScopeV1.model_validate(
        normalized["applicability_scope"]
    )
    normalized["locators"] = [
        SkillLocatorV1.model_validate(item) for item in normalized.get("locators", [])
    ]
    normalized["steps"] = [
        SkillStepV1.model_validate(item) for item in normalized.get("steps", [])
    ]
    normalized["evidence_refs"] = [
        EvidenceReferenceV1.model_validate(item)
        for item in normalized.get("evidence_refs", [])
    ]
    draft = SkillVersionV1.model_construct(content_sha256="0" * 64, **normalized)
    payload = draft.model_dump()
    payload["content_sha256"] = draft.compute_content_sha256()
    return SkillVersionV1.model_validate(payload)


def applicability_scope_from_environment(
    environment: EnvironmentScopeV1,
    *,
    required_state_ids: list[str],
    visual_variant_ids: list[str] | None = None,
    include_account: bool = False,
) -> SkillApplicabilityScopeV1:
    """Freeze the exact environment family in which a candidate may be evaluated."""

    return SkillApplicabilityScopeV1(
        game_id=environment.game_id,
        build_scope_ids=[environment.build_scope_id],
        channel=environment.channel,
        locale=environment.locale,
        device_scope_ids=[environment.device_scope_id],
        viewport_widths=[environment.viewport_width],
        viewport_heights=[environment.viewport_height],
        required_state_ids=required_state_ids,
        account_scope_ids=[environment.account_scope_id] if include_account else [],
        server_scope_ids=(
            [environment.server_scope_id] if environment.server_scope_id is not None else []
        ),
        world_scope_ids=(
            [environment.world_scope_id] if environment.world_scope_id is not None else []
        ),
        visual_variant_ids=visual_variant_ids or [],
    )


def skill_is_applicable(
    skill: SkillVersionV1,
    environment: EnvironmentScopeV1,
    *,
    current_state_id: str,
    visual_variant_id: str | None = None,
) -> bool:
    """Return true only when every declared scope dimension matches."""

    scope = skill.applicability_scope
    game_ids = {environment.game_id, *environment.game_id_aliases}
    build_ids = {environment.build_scope_id, *environment.build_scope_id_aliases}
    device_ids = {environment.device_scope_id, *environment.device_scope_id_aliases}
    if scope.game_id not in game_ids:
        return False
    if not build_ids.intersection(scope.build_scope_ids):
        return False
    if scope.channel != environment.channel or scope.locale != environment.locale:
        return False
    if not device_ids.intersection(scope.device_scope_ids):
        return False
    if environment.viewport_width not in scope.viewport_widths:
        return False
    if environment.viewport_height not in scope.viewport_heights:
        return False
    if current_state_id not in scope.required_state_ids:
        return False
    optional_checks = (
        (scope.account_scope_ids, environment.account_scope_id),
        (scope.server_scope_ids, environment.server_scope_id),
        (scope.world_scope_ids, environment.world_scope_id),
    )
    if any(accepted and observed not in accepted for accepted, observed in optional_checks):
        return False
    if scope.visual_variant_ids and visual_variant_id not in scope.visual_variant_ids:
        return False
    return True


def _dedupe_evidence(
    references: Iterable[EvidenceReferenceV1],
) -> list[EvidenceReferenceV1]:
    unique: dict[str, EvidenceReferenceV1] = {}
    for reference in references:
        key = reference.model_dump_json(by_alias=True)
        unique.setdefault(key, reference)
    return list(unique.values())


class SkillLifecycle:
    """Append, validate, promote, select, and degrade procedural skills."""

    def __init__(self, store: AIPlayerStore) -> None:
        self.store = store

    def record_run(self, run: SkillRunV1) -> SkillRunV1:
        persisted = self.store.append_skill_run(run)
        # OperationMemory is a canonical projection of this immutable SkillRun.
        # Keep the bridge here so ordinary runtime replay does not wait for a
        # later maintenance sweep; reconciliation remains idempotent after a crash.
        from .operation_memory import OperationMemory

        OperationMemory(self.store).record_skill_run(persisted)
        return persisted

    def reconcile_after_run(
        self,
        run: SkillRunV1,
    ) -> tuple[SkillValidationV1 | None, SkillVersionV1 | None]:
        """Persist lifecycle evidence and promote only after every strict gate passes.

        Live replay used to stop after writing a SkillRun, leaving the validation
        table empty and requiring a human-only CLI sequence to advance the
        lifecycle.  Reconciliation is intentionally sparse before the formal
        20-run gate so ordinary warm-path replay does not pay an ever-growing
        provenance scan on every action.
        """

        skill = self.store.get_skill_version_by_id(run.environment_id, run.skill_version_id)
        if skill is None:
            raise SkillLifecycleError(f"unknown skill version: {run.skill_version_id}")
        if skill.status not in {"candidate", "validated"}:
            return None, None
        runs = self.store.list_skill_runs(
            run.environment_id,
            skill_version_id=run.skill_version_id,
        )
        run_count = len(runs)
        if run_count not in {1, 2, 3, 5, 10} and run_count < 20:
            return None, None
        validation = self.validate(
            run.environment_id,
            run.skill_version_id,
            evaluator=run.validator_id,
        )
        preferred = (
            self.promote_preferred(run.environment_id, validation.id)
            if validation.status == "passed"
            else None
        )
        return validation, preferred

    def validate(
        self,
        environment_id: str,
        skill_version_id: str,
        *,
        evaluator: str,
    ) -> SkillValidationV1:
        skill = self.store.get_skill_version_by_id(environment_id, skill_version_id)
        if skill is None:
            raise SkillLifecycleError(f"unknown skill version: {skill_version_id}")
        if evaluator == skill.creator_id:
            raise SkillLifecycleError("a skill creator cannot validate its own candidate")
        runs = self.store.list_skill_runs(
            environment_id,
            skill_version_id=skill_version_id,
        )
        if not runs:
            raise SkillLifecycleError("a skill cannot be validated without independent runs")
        for run in runs:
            self.store.verify_skill_run_provenance(run)

        validation = derive_skill_validation(
            environment_id=environment_id,
            skill_version_id=skill_version_id,
            evaluator=evaluator,
            runs=runs,
        )
        existing = self.store.get_skill_validation(environment_id, validation.id)
        if existing is not None:
            if existing.model_dump(exclude={"created_at"}) != validation.model_dump(
                exclude={"created_at"}
            ):
                raise SkillLifecycleError(f"skill validation conflicts: {validation.id}")
            return existing
        return self.store.append_skill_validation(validation)

    def promote_preferred(
        self,
        environment_id: str,
        validation_id: str,
    ) -> SkillVersionV1:
        validation = self.store.get_skill_validation(environment_id, validation_id)
        if validation is None:
            raise SkillLifecycleError(f"unknown skill validation: {validation_id}")
        if validation.status != "passed":
            raise SkillLifecycleError("a failed validation cannot promote a skill")
        validation_runs = [
            self.store.get_skill_run(environment_id, run_id)
            for run_id in validation.skill_run_ids
        ]
        if any(run is None for run in validation_runs):
            raise SkillLifecycleError("the stored validation references a missing skill run")
        for run in validation_runs:
            if run is not None:
                self.store.verify_skill_run_provenance(run)
        derived = derive_skill_validation(
            environment_id=environment_id,
            skill_version_id=validation.skill_version_id,
            evaluator=validation.evaluator,
            runs=[run for run in validation_runs if run is not None],
            created_at=validation.created_at,
        )
        if derived != validation:
            raise SkillLifecycleError("the stored validation is not derived from its skill runs")
        source = self.store.get_skill_version_by_id(
            environment_id,
            validation.skill_version_id,
        )
        if source is None:
            raise SkillLifecycleError("the validated skill version is missing")
        latest = self.store.get_skill_version(environment_id, source.skill_id)
        if latest is None:
            raise SkillLifecycleError("the skill lifecycle has no latest version")
        if latest.id != source.id:
            if (
                latest.status == "preferred"
                and latest.validation_id == validation.id
                and latest.source_skill_version_id == source.id
            ):
                return latest
            raise SkillLifecycleError("the validated candidate is no longer the latest version")
        successor = build_skill_version(
            **{
                **source.model_dump(),
                "id": f"{source.skill_id}.version.{source.version + 1}",
                "version": source.version + 1,
                "status": "preferred",
                "validation_run_ids": validation.skill_run_ids,
                "validation_id": validation.id,
                "source_skill_version_id": source.id,
                "independent_reset_count": validation.independent_reset_count,
                "visual_variant_count": validation.visual_variant_count,
                "failure_recovery_verified": validation.successful_recovery_count > 0,
                "invalidation_reason": None,
                "created_at": utc_now(),
                "evidence_refs": _dedupe_evidence(
                    [*source.evidence_refs, *validation.evidence_refs]
                ),
            },
        )
        return self.store.append_skill_version(successor)

    def degrade(
        self,
        environment_id: str,
        skill_id: str,
        *,
        reason: str,
        evidence_refs: list[EvidenceReferenceV1],
        invalidate: bool = False,
    ) -> SkillVersionV1:
        latest = self.store.get_skill_version(environment_id, skill_id)
        if latest is None:
            raise SkillLifecycleError(f"unknown skill: {skill_id}")
        if latest.status in {"degraded", "invalidated"}:
            if latest.invalidation_reason == reason:
                return latest
            raise SkillLifecycleError("the latest skill version is already non-executable")
        successor = build_skill_version(
            **{
                **latest.model_dump(),
                "id": f"{latest.skill_id}.version.{latest.version + 1}",
                "version": latest.version + 1,
                "status": "invalidated" if invalidate else "degraded",
                "source_skill_version_id": latest.id,
                "invalidation_reason": reason,
                "created_at": utc_now(),
                "evidence_refs": _dedupe_evidence([*latest.evidence_refs, *evidence_refs]),
            },
        )
        degraded = self.store.append_skill_version(successor)
        task_digest = hashlib.sha256(
            f"{environment_id}:{degraded.id}:{reason}".encode("utf-8")
        ).hexdigest()[:24]
        task = FrontierTaskV1(
            id=f"task.failed-skill.{task_digest}",
            environment_id=environment_id,
            evidence_refs=evidence_refs,
            title=f"重新探索失效技能：{degraded.title}",
            source="failed_skill",
            reason=reason,
            value_score=8,
            novelty_score=6,
            expected_coverage_gain=6,
            risk_score=0,
            action_budget=8,
            time_budget_seconds=600,
            max_attempts=3,
        )
        existing_task = self.store.get_task(environment_id, task.id)
        if existing_task is None:
            self.store.enqueue_task(task)
        elif existing_task != task:
            raise SkillLifecycleError(f"failed-skill task conflicts: {task.id}")
        return degraded

    def select_preferred(
        self,
        environment_id: str,
        *,
        current_state_id: str,
        visual_variant_id: str | None = None,
        skill_id: str | None = None,
    ) -> list[SkillVersionV1]:
        environment = self.store.get_environment(environment_id)
        if environment is None:
            raise SkillLifecycleError(f"unknown environment: {environment_id}")
        compact_loader = getattr(
            self.store,
            "list_executable_preferred_skill_versions",
            None,
        )
        if callable(compact_loader):
            candidates = compact_loader(environment_id)
        else:
            versions = self.store.list_skill_versions(environment_id, latest_only=False)
            grouped: dict[str, list[SkillVersionV1]] = {}
            for version in versions:
                grouped.setdefault(version.skill_id, []).append(version)
            candidates = []
            for skill_versions in grouped.values():
                newest = max(skill_versions, key=lambda item: item.version)
                if newest.status in {"degraded", "invalidated"}:
                    continue
                preferred = [item for item in skill_versions if item.status == "preferred"]
                if preferred:
                    candidates.append(max(preferred, key=lambda item: item.version))
        return [
            skill
            for skill in candidates
            if (skill_id is None or skill.skill_id == skill_id)
            and skill_is_applicable(
                skill,
                environment,
                current_state_id=current_state_id,
                visual_variant_id=visual_variant_id,
            )
        ]
