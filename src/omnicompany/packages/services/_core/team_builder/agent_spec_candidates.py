# [OMNI] origin=codex domain=services/team_builder ts=2026-07-24T00:00:00Z type=facility
# [OMNI] summary="Versioned AgentSpec candidate flow backed only by Reviewstage"
# [OMNI] why="Agent configuration changes need diff, review, Guardian, canary and rollback gates without a second config type or candidate store"
"""Versioned candidate flow for the existing :class:`AgentSpec`.

Reviewstage is the only persistence owner for candidates.  This module owns
only deterministic value conversion and gates; it neither stores a second copy
of the queue nor edits Agent source files.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Mapping, Protocol

from omnicompany.packages.services._core.agent.configurable import (
    AgentSpec,
    ConfigurableAgent,
)


AGENT_SPEC_REVIEW_CARRIER = "markdown"
AGENT_SPEC_CANDIDATE_PAYLOAD_KEY = "agent_spec_candidate"
AGENT_SPEC_CANDIDATE_SCHEMA_VERSION = 1


class ReviewMaterialLike(Protocol):
    id: str
    kind: Any
    status: Any
    version: int | None
    version_family: str
    extra: dict[str, Any]


class ReviewStoreLike(Protocol):
    def create(self, **kwargs: Any) -> ReviewMaterialLike: ...

    def get(self, material_id: str) -> ReviewMaterialLike | None: ...

    def list(self, **kwargs: Any) -> list[ReviewMaterialLike]: ...

    def patch_extra(
        self,
        material_id: str,
        patch: dict[str, Any],
        *,
        by: str = "system",
    ) -> ReviewMaterialLike: ...


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(
        "AgentSpec candidate values must be JSON-compatible, "
        f"got {type(value).__name__}"
    )


def agent_spec_to_dict(spec: AgentSpec) -> dict[str, Any]:
    """Serialize the canonical AgentSpec without defining a mirror schema."""

    return _json_value(asdict(spec))


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def agent_spec_digest(spec: AgentSpec) -> str:
    payload = _canonical_json(agent_spec_to_dict(spec))
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


_TUPLE_FIELDS = frozenset(
    {
        "output_materials",
        "trigger_materials",
        "accepted_input_materials",
        "forbidden_input_materials",
        "user_input_required_fields",
        "tools",
    }
)
_SEQUENCE_MAPPING_FIELDS = frozenset({"gates", "context_triggers"})
_MAPPING_FIELDS = frozenset(
    {
        "llm_extra_body",
        "prompt_substitutions",
        "workspace",
        "test_baseline",
    }
)


def agent_spec_from_dict(value: Mapping[str, Any]) -> AgentSpec:
    """Rebuild the existing AgentSpec and reject unknown/missing fields."""

    known = {item.name for item in fields(AgentSpec)}
    unknown = sorted(set(value) - known)
    if unknown:
        raise ValueError(f"unknown AgentSpec fields: {unknown}")
    data = dict(value)
    for name in _TUPLE_FIELDS:
        if name in data:
            data[name] = tuple(data[name] or [])
    for name in _SEQUENCE_MAPPING_FIELDS:
        if name in data:
            data[name] = tuple(dict(item) for item in (data[name] or []))
    for name in _MAPPING_FIELDS:
        if name in data:
            data[name] = dict(data[name] or {})
    try:
        spec = AgentSpec(**data)
    except TypeError as exc:
        raise ValueError(f"invalid AgentSpec payload: {exc}") from exc
    if not spec.id.strip() or not spec.name.strip():
        raise ValueError("AgentSpec.id and AgentSpec.name must be non-empty")
    return spec


@dataclass(frozen=True)
class AgentSpecFieldDiff:
    field: str
    before: Any
    after: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "before": _json_value(self.before),
            "after": _json_value(self.after),
        }


def _field_diff(
    base: AgentSpec,
    proposed: AgentSpec,
) -> tuple[AgentSpecFieldDiff, ...]:
    before = agent_spec_to_dict(base)
    after = agent_spec_to_dict(proposed)
    return tuple(
        AgentSpecFieldDiff(field=name, before=before[name], after=after[name])
        for name in before
        if before[name] != after[name]
    )


@dataclass(frozen=True)
class AgentSpecCandidate:
    """Immutable value stored inside one Reviewstage material."""

    agent_id: str
    version: int
    base_digest: str
    candidate_digest: str
    proposed_spec: Mapping[str, Any]
    diff: tuple[AgentSpecFieldDiff, ...]
    source_ref: str
    rollback_ref: str

    def to_agent_spec(self) -> AgentSpec:
        spec = agent_spec_from_dict(self.proposed_spec)
        if spec.id != self.agent_id:
            raise ValueError(
                f"candidate agent_id={self.agent_id!r} does not match "
                f"proposed AgentSpec.id={spec.id!r}"
            )
        if agent_spec_digest(spec) != self.candidate_digest:
            raise ValueError("candidate_digest does not match proposed AgentSpec")
        return spec

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": AGENT_SPEC_CANDIDATE_SCHEMA_VERSION,
            "agent_id": self.agent_id,
            "version": self.version,
            "base_digest": self.base_digest,
            "candidate_digest": self.candidate_digest,
            "proposed_spec": _json_value(self.proposed_spec),
            "diff": [item.to_dict() for item in self.diff],
            "source_ref": self.source_ref,
            "rollback_ref": self.rollback_ref,
            "evidence": AgentSpecVerificationEvidence().to_dict(),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "AgentSpecCandidate":
        if payload.get("schema_version") != AGENT_SPEC_CANDIDATE_SCHEMA_VERSION:
            raise ValueError(
                "unsupported AgentSpec candidate schema_version: "
                f"{payload.get('schema_version')!r}"
            )
        diff = tuple(
            AgentSpecFieldDiff(
                field=str(item.get("field") or ""),
                before=_json_value(item.get("before")),
                after=_json_value(item.get("after")),
            )
            for item in (payload.get("diff") or [])
            if isinstance(item, Mapping)
        )
        candidate = cls(
            agent_id=str(payload.get("agent_id") or ""),
            version=int(payload.get("version") or 0),
            base_digest=str(payload.get("base_digest") or ""),
            candidate_digest=str(payload.get("candidate_digest") or ""),
            proposed_spec=dict(payload.get("proposed_spec") or {}),
            diff=diff,
            source_ref=str(payload.get("source_ref") or ""),
            rollback_ref=str(payload.get("rollback_ref") or ""),
        )
        if candidate.version <= 0:
            raise ValueError("AgentSpec candidate version must be positive")
        if not candidate.source_ref.strip():
            raise ValueError("AgentSpec candidate source_ref is required")
        if not candidate.rollback_ref.strip():
            raise ValueError("AgentSpec candidate rollback_ref is required")
        candidate.to_agent_spec()
        return candidate


def build_agent_spec_candidate(
    *,
    base: AgentSpec,
    proposed: AgentSpec,
    version: int,
    source_ref: str,
    rollback_ref: str,
) -> AgentSpecCandidate:
    """Build a versioned candidate around the canonical AgentSpec."""

    if base.id != proposed.id:
        raise ValueError("AgentSpec version candidates cannot change AgentSpec.id")
    if version <= 0:
        raise ValueError("AgentSpec candidate version must be positive")
    if not source_ref.strip():
        raise ValueError("AgentSpec candidate source_ref is required")
    if not rollback_ref.strip():
        raise ValueError("AgentSpec candidate rollback_ref is required")
    diff = _field_diff(base, proposed)
    if not diff:
        raise ValueError("AgentSpec candidate must change at least one field")
    return AgentSpecCandidate(
        agent_id=base.id,
        version=version,
        base_digest=agent_spec_digest(base),
        candidate_digest=agent_spec_digest(proposed),
        proposed_spec=agent_spec_to_dict(proposed),
        diff=diff,
        source_ref=source_ref.strip(),
        rollback_ref=rollback_ref.strip(),
    )


@dataclass(frozen=True)
class AgentSpecVerificationEvidence:
    """Reference-only verification carried by the Reviewstage material."""

    shadow_run_refs: tuple[str, ...] = ()
    shadow_failure_refs: tuple[str, ...] = ()
    guardian_report_refs: tuple[str, ...] = ()
    guardian_blocking_findings: tuple[str, ...] = ()
    benchmark_refs: tuple[str, ...] = ()
    canary_run_refs: tuple[str, ...] = ()
    canary_failure_refs: tuple[str, ...] = ()
    rollback_test_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            item.name: list(getattr(self, item.name))
            for item in fields(self)
        }

    @classmethod
    def from_value(cls, value: Any) -> "AgentSpecVerificationEvidence":
        if not isinstance(value, Mapping):
            return cls()
        known = {item.name for item in fields(cls)}
        return cls(
            **{
                name: tuple(
                    str(ref).strip()
                    for ref in (value.get(name) or [])
                    if str(ref).strip()
                )
                for name in known
            }
        )


def _short(value: Any, limit: int = 96) -> str:
    text = json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _review_body(candidate: AgentSpecCandidate) -> str:
    rows = [
        f"| `{item.field}` | `{_short(item.before)}` | `{_short(item.after)}` |"
        for item in candidate.diff
    ]
    return "\n".join(
        [
            f"# AgentSpec candidate · {candidate.agent_id} · v{candidate.version}",
            "",
            f"- Source: `{candidate.source_ref}`",
            f"- Base: `{candidate.base_digest}`",
            f"- Candidate: `{candidate.candidate_digest}`",
            f"- Rollback: `{candidate.rollback_ref}`",
            "",
            "| Field | Before | After |",
            "|---|---|---|",
            *rows,
            "",
            "Machine payload: `material.extra.agent_spec_candidate`.",
        ]
    )


def _value(value: Any) -> str:
    raw = value.value if hasattr(value, "value") else value
    return str(raw or "").strip()


def _payload_from_material(material: ReviewMaterialLike) -> dict[str, Any]:
    payload = (material.extra or {}).get(AGENT_SPEC_CANDIDATE_PAYLOAD_KEY)
    if not isinstance(payload, dict):
        raise ValueError(
            f"review material {material.id!r} has no agent_spec_candidate payload"
        )
    return dict(payload)


def submit_agent_spec_candidate(
    store: ReviewStoreLike,
    candidate: AgentSpecCandidate,
    *,
    project: str,
    source_plan_id: str,
    submitted_by: str,
    previous_material_id: str | None = None,
) -> ReviewMaterialLike:
    """Submit to the existing Reviewstage store; never persist elsewhere."""

    payload = candidate.to_payload()
    payload["submitted_by"] = submitted_by.strip()
    payload["submitted_at"] = _now_iso()

    for material in store.list(include_internal=True, project=project):
        try:
            existing = _payload_from_material(material)
        except ValueError:
            continue
        if (
            existing.get("candidate_digest") == candidate.candidate_digest
            and _value(material.status) == "pending"
        ):
            return material

    links: dict[str, Any] = {}
    if previous_material_id:
        links["supersedes"] = [previous_material_id]
    return store.create(
        # ``kind`` describes the Reviewstage carrier.  Candidate semantics live
        # under the explicit payload key, so "kind" is not overloaded.
        kind=AGENT_SPEC_REVIEW_CARRIER,
        tier="mandatory",
        title=f"AgentSpec {candidate.agent_id} v{candidate.version}",
        inline_content=_review_body(candidate),
        source_plan_id=source_plan_id.strip() or None,
        project=project,
        track="AgentSpec candidate",
        version=candidate.version,
        version_family=f"agent-spec:{candidate.agent_id}",
        links=links,
        extra={
            "reviewstage_visibility": "internal",
            AGENT_SPEC_CANDIDATE_PAYLOAD_KEY: payload,
        },
    )


def record_agent_spec_verification(
    store: ReviewStoreLike,
    material_id: str,
    evidence: AgentSpecVerificationEvidence,
    *,
    by: str,
) -> ReviewMaterialLike:
    material = store.get(material_id)
    if material is None:
        raise KeyError(material_id)
    payload = _payload_from_material(material)
    payload["evidence"] = evidence.to_dict()
    return store.patch_extra(
        material_id,
        {AGENT_SPEC_CANDIDATE_PAYLOAD_KEY: payload},
        by=by,
    )


class AgentSpecGateStage(str, Enum):
    CANARY = "canary"
    PROMOTION = "promotion"


@dataclass(frozen=True)
class AgentSpecGateDecision:
    material_id: str
    stage: AgentSpecGateStage
    allowed: bool
    blockers: tuple[str, ...]
    candidate_digest: str = ""


def decide_agent_spec_gate(
    material: ReviewMaterialLike,
    *,
    current_spec: AgentSpec,
    stage: AgentSpecGateStage,
) -> AgentSpecGateDecision:
    """Fail closed on review, stale base, Guardian, shadow and canary evidence."""

    blockers: list[str] = []
    try:
        payload = _payload_from_material(material)
        candidate = AgentSpecCandidate.from_payload(payload)
        proposed = candidate.to_agent_spec()
    except (TypeError, ValueError) as exc:
        return AgentSpecGateDecision(
            material_id=material.id,
            stage=stage,
            allowed=False,
            blockers=(f"invalid_candidate:{exc}",),
        )

    if _value(material.kind) != AGENT_SPEC_REVIEW_CARRIER:
        blockers.append("wrong_review_carrier")
    if _value(material.status) != "accepted":
        blockers.append("review_not_accepted")
    if material.version != candidate.version:
        blockers.append("review_version_mismatch")
    if material.version_family != f"agent-spec:{candidate.agent_id}":
        blockers.append("review_version_family_mismatch")
    if current_spec.id != candidate.agent_id or proposed.id != candidate.agent_id:
        blockers.append("agent_id_mismatch")
    if agent_spec_digest(current_spec) != candidate.base_digest:
        blockers.append("stale_base_digest")
    if tuple(item.to_dict() for item in _field_diff(current_spec, proposed)) != tuple(
        item.to_dict() for item in candidate.diff
    ):
        blockers.append("diff_mismatch")
    if payload.get("applied"):
        blockers.append("candidate_already_applied")

    evidence = AgentSpecVerificationEvidence.from_value(payload.get("evidence"))
    if not evidence.shadow_run_refs:
        blockers.append("missing_shadow_run_evidence")
    if evidence.shadow_failure_refs:
        blockers.append("shadow_failures_present")
    if not evidence.guardian_report_refs:
        blockers.append("missing_guardian_report")
    if evidence.guardian_blocking_findings:
        blockers.append("guardian_blocking_findings_present")
    if not evidence.rollback_test_refs:
        blockers.append("missing_rollback_test")

    if stage == AgentSpecGateStage.PROMOTION:
        if not evidence.benchmark_refs:
            blockers.append("missing_benchmark_evidence")
        if not evidence.canary_run_refs:
            blockers.append("missing_canary_run_evidence")
        if evidence.canary_failure_refs:
            blockers.append("canary_failures_present")

    return AgentSpecGateDecision(
        material_id=material.id,
        stage=stage,
        allowed=not blockers,
        blockers=tuple(blockers),
        candidate_digest=candidate.candidate_digest,
    )


def build_agent_spec_canary_class(
    base_agent_type: type[ConfigurableAgent],
    candidate: AgentSpecCandidate,
) -> type[ConfigurableAgent]:
    """Create an isolated runtime class; never mutate the live class SPEC."""

    current_spec = base_agent_type._resolve_spec()
    if agent_spec_digest(current_spec) != candidate.base_digest:
        raise ValueError("cannot start AgentSpec canary from a stale base")
    proposed = candidate.to_agent_spec()
    return type(
        f"{base_agent_type.__name__}CanaryV{candidate.version}",
        (base_agent_type,),
        {
            "__module__": base_agent_type.__module__,
            "SPEC": proposed,
            "NODE_PROMPT": "",
            "TOOL_ROUTERS": [],
            "FORMAT_IN": None,
            "FORMAT_OUT": None,
        },
    )


def record_agent_spec_promotion(
    store: ReviewStoreLike,
    material_id: str,
    *,
    reviewed_base_spec: AgentSpec,
    applied_ref: str,
    by: str,
) -> ReviewMaterialLike:
    """Record an already-applied authoritative source change after all gates."""

    material = store.get(material_id)
    if material is None:
        raise KeyError(material_id)
    decision = decide_agent_spec_gate(
        material,
        current_spec=reviewed_base_spec,
        stage=AgentSpecGateStage.PROMOTION,
    )
    if not decision.allowed:
        raise ValueError(
            "AgentSpec promotion blocked: " + ", ".join(decision.blockers)
        )
    if not applied_ref.strip():
        raise ValueError("applied_ref is required")
    payload = _payload_from_material(material)
    payload["applied"] = {
        "ref": applied_ref.strip(),
        "at": _now_iso(),
        "by": by.strip(),
        "candidate_digest": decision.candidate_digest,
    }
    return store.patch_extra(
        material_id,
        {AGENT_SPEC_CANDIDATE_PAYLOAD_KEY: payload},
        by=by,
    )


def record_agent_spec_rollback(
    store: ReviewStoreLike,
    material_id: str,
    *,
    rollback_applied_ref: str,
    by: str,
) -> ReviewMaterialLike:
    """Record rollback performed through the existing authoritative source."""

    material = store.get(material_id)
    if material is None:
        raise KeyError(material_id)
    payload = _payload_from_material(material)
    if not payload.get("applied"):
        raise ValueError("cannot roll back an AgentSpec candidate that was not applied")
    if payload.get("rollback"):
        raise ValueError("AgentSpec candidate rollback is already recorded")
    if not rollback_applied_ref.strip():
        raise ValueError("rollback_applied_ref is required")
    payload["rollback"] = {
        "ref": rollback_applied_ref.strip(),
        "at": _now_iso(),
        "by": by.strip(),
        "rollback_ref": payload.get("rollback_ref"),
    }
    return store.patch_extra(
        material_id,
        {AGENT_SPEC_CANDIDATE_PAYLOAD_KEY: payload},
        by=by,
    )


__all__ = [
    "AGENT_SPEC_CANDIDATE_PAYLOAD_KEY",
    "AGENT_SPEC_REVIEW_CARRIER",
    "AgentSpecCandidate",
    "AgentSpecFieldDiff",
    "AgentSpecGateDecision",
    "AgentSpecGateStage",
    "AgentSpecVerificationEvidence",
    "agent_spec_digest",
    "agent_spec_from_dict",
    "agent_spec_to_dict",
    "build_agent_spec_candidate",
    "build_agent_spec_canary_class",
    "decide_agent_spec_gate",
    "record_agent_spec_promotion",
    "record_agent_spec_rollback",
    "record_agent_spec_verification",
    "submit_agent_spec_candidate",
]
