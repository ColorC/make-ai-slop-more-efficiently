# [OMNI] origin=codex domain=services/team_builder ts=2026-07-24T00:00:00Z type=gate
# [OMNI] summary="Stateless evidence gate before a domain may establish or run TeamSpec"
# [OMNI] why="Facility-first plans need one fail-closed admission result without duplicating WhatNow progress state"
"""Domain admission gate for the shared Team infrastructure.

This gate is stateless.  WhatNow remains the plan/progress authority; the gate
only evaluates references produced by a concrete integration run.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum


class TeamFacility(str, Enum):
    TEAM_CONTRACT = "team_contract"
    PROJECT_BINDING = "project_binding"
    TEAM_BUILDER_OUTPUT = "team_builder_output"
    RUNTIME = "runtime"
    AGENT_ALLOCATION = "agent_allocation"
    CONTEXT_FORK = "context_fork"
    LIFECYCLE_ASSIGNMENT = "lifecycle_assignment"
    AGENT_SPEC_REVIEW = "agent_spec_review"
    GUARDIAN = "guardian"
    TEAM_SUPERVISOR = "team_supervisor"
    REGISTRY = "registry"
    COMPATIBILITY = "compatibility"


@dataclass(frozen=True)
class TeamFacilityEvidence:
    """Reference-only proof from one domain-neutral integration run."""

    team_contract_refs: tuple[str, ...] = ()
    project_binding_refs: tuple[str, ...] = ()
    team_builder_output_refs: tuple[str, ...] = ()
    runtime_refs: tuple[str, ...] = ()
    agent_allocation_refs: tuple[str, ...] = ()
    context_fork_refs: tuple[str, ...] = ()
    lifecycle_assignment_refs: tuple[str, ...] = ()
    agent_spec_review_refs: tuple[str, ...] = ()
    guardian_refs: tuple[str, ...] = ()
    team_supervisor_refs: tuple[str, ...] = ()
    registry_refs: tuple[str, ...] = ()
    compatibility_refs: tuple[str, ...] = ()
    blocking_findings: tuple[str, ...] = ()


_FACILITY_TO_FIELD: dict[TeamFacility, str] = {
    TeamFacility.TEAM_CONTRACT: "team_contract_refs",
    TeamFacility.PROJECT_BINDING: "project_binding_refs",
    TeamFacility.TEAM_BUILDER_OUTPUT: "team_builder_output_refs",
    TeamFacility.RUNTIME: "runtime_refs",
    TeamFacility.AGENT_ALLOCATION: "agent_allocation_refs",
    TeamFacility.CONTEXT_FORK: "context_fork_refs",
    TeamFacility.LIFECYCLE_ASSIGNMENT: "lifecycle_assignment_refs",
    TeamFacility.AGENT_SPEC_REVIEW: "agent_spec_review_refs",
    TeamFacility.GUARDIAN: "guardian_refs",
    TeamFacility.TEAM_SUPERVISOR: "team_supervisor_refs",
    TeamFacility.REGISTRY: "registry_refs",
    TeamFacility.COMPATIBILITY: "compatibility_refs",
}


@dataclass(frozen=True)
class TeamFacilityDecision:
    ready: bool
    status: str
    missing_facilities: tuple[TeamFacility, ...]
    blocking_findings: tuple[str, ...]
    evidence_refs: tuple[str, ...]


def decide_team_facility_admission(
    evidence: TeamFacilityEvidence,
) -> TeamFacilityDecision:
    """Return ``blocked_by_facility`` unless every shared facility has proof."""

    known_fields = {item.name for item in fields(TeamFacilityEvidence)}
    if set(_FACILITY_TO_FIELD.values()) - known_fields:
        raise RuntimeError("Team facility gate field mapping is incomplete")

    missing = tuple(
        facility
        for facility, field_name in _FACILITY_TO_FIELD.items()
        if not tuple(getattr(evidence, field_name))
    )
    refs: list[str] = []
    for field_name in _FACILITY_TO_FIELD.values():
        for ref in getattr(evidence, field_name):
            normalized = str(ref).strip()
            if normalized and normalized not in refs:
                refs.append(normalized)
    blockers = tuple(
        str(item).strip()
        for item in evidence.blocking_findings
        if str(item).strip()
    )
    ready = not missing and not blockers
    return TeamFacilityDecision(
        ready=ready,
        status="ready" if ready else "blocked_by_facility",
        missing_facilities=missing,
        blocking_findings=blockers,
        evidence_refs=tuple(refs),
    )


__all__ = [
    "TeamFacility",
    "TeamFacilityDecision",
    "TeamFacilityEvidence",
    "decide_team_facility_admission",
]
