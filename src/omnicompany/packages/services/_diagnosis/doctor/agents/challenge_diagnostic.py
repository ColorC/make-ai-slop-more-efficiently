"""Challenge one hypothesis through an external proposal / Omni application split.

The external AgentRuntime receives read-only workspace and Git evidence tools.  It
cannot mutate the hypothesis lifecycle, persist a finding, publish a Material, or
use the EventBus as an agent loop.  OmniCompany validates the complete proposal,
then applies the hypothesis transition atomically and publishes the finding through
the canonical Doctor tool.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, ClassVar, Protocol

import yaml

from omnicompany.core.config import omni_workspace_root
from omnicompany.packages.services._core.agent import AgentSpec
from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._diagnosis.doctor.builders import (
    HypothesisChallengeRecorder,
    HypothesisResolutionRecorder,
)
from omnicompany.packages.services._diagnosis.doctor.tools.write_finding import (
    WriteFindingRouter,
)
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.agent_runtime import (
    READONLY_AGENT_TOOLS,
    run_readonly_structured_agent,
)


StructuredChallengeRunner = Callable[..., Awaitable[dict[str, Any]]]
FindingPublisher = Callable[[dict[str, Any], str], Awaitable[str]]

_ENTITY_KINDS = ("worker", "material", "team", "agent", "hook", "tool", "plan")
_EVIDENCE_METHODS = (
    "red_green_test",
    "historical_instance",
    "standards_authority",
    "manual",
)
_MAX_REQUEST_CHARS = 40_000

_FINDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "entity_id",
        "entity_kind",
        "finding_kind",
        "evidence",
        "commentary",
        "concern",
        "applied_standards",
        "applied_hypotheses",
        "applied_exemplars",
        "uncertainty_note",
    ],
    "properties": {
        "entity_id": {"type": "string", "maxLength": 1000},
        "entity_kind": {"type": "string", "maxLength": 40},
        "finding_kind": {"type": "string", "maxLength": 40},
        "evidence": {"type": "string", "maxLength": 4000},
        "commentary": {"type": "string", "maxLength": 4000},
        "concern": {"type": "string", "maxLength": 4000},
        "applied_standards": {
            "type": "array",
            "maxItems": 50,
            "items": {"type": "string", "minLength": 1, "maxLength": 1000},
        },
        "applied_hypotheses": {
            "type": "array",
            "maxItems": 50,
            "items": {"type": "string", "minLength": 1, "maxLength": 1000},
        },
        "applied_exemplars": {
            "type": "array",
            "maxItems": 50,
            "items": {"type": "string", "minLength": 1, "maxLength": 1000},
        },
        "uncertainty_note": {"type": "string", "maxLength": 2000},
    },
}

CHALLENGE_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "hypothesis_id",
        "applies_to",
        "outcome",
        "challenge_reason",
        "challenge_source",
        "falsifying_evidence",
        "falsification_method",
        "consulted_references",
        "finding",
        "narrative",
        "andon_reason",
    ],
    "properties": {
        "hypothesis_id": {"type": "string", "maxLength": 300},
        "applies_to": {"type": "string", "maxLength": 40},
        "outcome": {"type": "string", "enum": ["challenged", "falsified", "andon"]},
        "challenge_reason": {"type": "string", "maxLength": 4000},
        "challenge_source": {"type": "string", "enum": [*_EVIDENCE_METHODS, ""]},
        "falsifying_evidence": {"type": "string", "maxLength": 4000},
        "falsification_method": {"type": "string", "enum": [*_EVIDENCE_METHODS, ""]},
        "consulted_references": {
            "type": "array",
            "maxItems": 100,
            "items": {"type": "string", "minLength": 1, "maxLength": 1000},
        },
        "finding": _FINDING_SCHEMA,
        "narrative": {"type": "string", "minLength": 30, "maxLength": 8000},
        "andon_reason": {"type": "string", "maxLength": 4000},
    },
    "allOf": [
        {
            "if": {"properties": {"outcome": {"const": "challenged"}}},
            "then": {
                "properties": {
                    "falsifying_evidence": {"const": ""},
                    "falsification_method": {"const": ""},
                    "andon_reason": {"const": ""},
                }
            },
        },
        {
            "if": {"properties": {"outcome": {"const": "falsified"}}},
            "then": {"properties": {"andon_reason": {"const": ""}}},
        },
    ],
}

_INSTRUCTIONS = """\
You are the read-only evidence investigator for one OmniCompany hypothesis.
Inspect the supplied immutable hypothesis snapshot and, when useful, workspace or
Git evidence. Do not modify files, persist findings, publish Materials, mutate a
hypothesis, make Decisions, or call another agent. Return a proposal only.

Evidence budget: use at most eight tool calls. Start with source_path from the
hypothesis snapshot when it exists, make at most two narrowly targeted workspace
searches and at most one Git evidence query, then return. Do not attempt an
exhaustive repository proof of a universal statement.

Use outcome=challenged when there is a concrete reason to challenge but no decisive
falsification. Use outcome=falsified only with specific evidence that actually
contradicts the statement. Use outcome=andon when information is insufficient even
to formulate a grounded challenge. For andon, leave mutation and finding fields
empty and explain the missing information in andon_reason. challenge_source and
falsification_method may only be red_green_test, historical_instance,
standards_authority, or manual (use the empty string where the field is inapplicable).
For outcome=challenged, falsifying_evidence and falsification_method must both be
exactly empty; put investigated but non-decisive evidence in finding.evidence.
Never use scoring fields.
For a mutation proposal, finding_kind must be hypothesis, entity_id must be the
canonical hypothesis path, entity_kind must equal applies_to, and
applied_hypotheses must contain exactly the supplied hypothesis id. Return exactly
one schema-valid JSON object with no Markdown fence.
"""


CHALLENGE_DIAGNOSTIC_SPEC = AgentSpec(
    id="doctor.challenge_diagnostic",
    name="ChallengeDiagnosticAgent",
    domain="doctor",
    llm_model="deepseek-v4-flash",
    llm_max_tokens=12_000,
    llm_max_turns=24,
    llm_timeout_seconds=600,
    output_materials=(
        "doctor.challenge_diagnosis.verdict",
        "doctor.health_finding",
    ),
    primary_output="doctor.challenge_diagnosis.verdict",
    trigger_materials=("doctor.challenge_diagnosis.request",),
    accepted_input_materials=("doctor.challenge_diagnosis.request",),
    forbidden_input_materials=(
        "doctor.challenge_diagnosis.verdict",
        "doctor.health_finding",
    ),
    user_input_required_fields=("focus_hypothesis_yaml_path", "applies_to"),
    tools=(),
    workspace={"permission_mode": "readonly", "write_prefixes": ()},
)


def _snapshot_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_existing_path(value: Any, *, label: str) -> tuple[str, Path]:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError(f"{label} must be a non-empty workspace path")
    workspace = Path(omni_workspace_root()).resolve()
    raw = Path(str(value).strip())
    resolved = raw.resolve() if raw.is_absolute() else (workspace / raw).resolve()
    try:
        relative = resolved.relative_to(workspace)
    except ValueError as error:
        raise ValueError(f"{label} is outside the OmniCompany workspace: {resolved}") from error
    if not resolved.is_file():
        raise ValueError(f"{label} is not an existing file: {relative.as_posix()}")
    return relative.as_posix(), resolved


class HypothesisGateway(Protocol):
    def load(self, relative_path: str) -> dict[str, Any]: ...

    def apply(
        self,
        relative_path: str,
        *,
        expected_snapshot_hash: str,
        proposal: dict[str, Any],
    ) -> dict[str, Any]: ...


class YamlHypothesisGateway:
    """Omni-owned hypothesis repository with optimistic concurrency and atomic IO."""

    def __init__(self, workspace: str | Path | None = None) -> None:
        self._workspace = Path(workspace or omni_workspace_root()).resolve()

    def _path(self, relative_path: str) -> Path:
        target = (self._workspace / relative_path).resolve()
        try:
            target.relative_to(self._workspace)
        except ValueError as error:
            raise ValueError("hypothesis path escaped the OmniCompany workspace") from error
        return target

    def load(self, relative_path: str) -> dict[str, Any]:
        path = self._path(relative_path)
        with path.open(encoding="utf-8") as stream:
            value = yaml.safe_load(stream)
        if not isinstance(value, dict):
            raise ValueError("hypothesis YAML top level must be an object")
        return dict(value)

    def apply(
        self,
        relative_path: str,
        *,
        expected_snapshot_hash: str,
        proposal: dict[str, Any],
    ) -> dict[str, Any]:
        path = self._path(relative_path)
        current = self.load(relative_path)
        if _snapshot_hash(current) != expected_snapshot_hash:
            raise RuntimeError("hypothesis changed after the external evidence snapshot")

        challenged = HypothesisChallengeRecorder().record(
            current,
            proposal["challenge_reason"],
            source=proposal["challenge_source"],
            challenger_id="agent-runtime:doctor.challenge_diagnostic",
        )
        if not challenged.recorded or challenged.upgraded_dict is None:
            raise RuntimeError(f"challenge transition rejected: {challenged.skip_reason}")
        updated = challenged.upgraded_dict
        if proposal["outcome"] == "falsified":
            resolved = HypothesisResolutionRecorder().record(
                updated,
                proposal["falsifying_evidence"],
                method=proposal["falsification_method"],
                falsifier_id="agent-runtime:doctor.challenge_diagnostic",
            )
            if not resolved.falsified or resolved.upgraded_dict is None:
                raise RuntimeError(f"falsification transition rejected: {resolved.skip_reason}")
            updated = resolved.upgraded_dict

        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_name = ""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".yaml.tmp",
                prefix=f".{path.name}.",
                dir=path.parent,
                delete=False,
            ) as stream:
                temporary_name = stream.name
                yaml.safe_dump(updated, stream, allow_unicode=True, sort_keys=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, path)
        finally:
            if temporary_name and Path(temporary_name).exists():
                Path(temporary_name).unlink()
        return dict(updated)


def _proposal_error(
    proposal: dict[str, Any],
    *,
    hypothesis_path: str,
    hypothesis: dict[str, Any],
    applies_to: str,
) -> str | None:
    hypothesis_id = str(hypothesis.get("id") or "").strip()
    if not hypothesis_id:
        return "hypothesis snapshot has no id"
    if proposal.get("hypothesis_id") != hypothesis_id:
        return "hypothesis_id must exactly echo the current snapshot"
    if proposal.get("applies_to") != applies_to:
        return "applies_to must exactly echo the request"

    outcome = proposal.get("outcome")
    if outcome == "andon":
        if len(str(proposal.get("andon_reason") or "").strip()) < 20:
            return "andon requires a concrete information gap"
        return None
    if outcome not in {"challenged", "falsified"}:
        return "outcome must be challenged, falsified, or andon"

    reason = str(proposal.get("challenge_reason") or "").strip()
    if len(reason) < 20:
        return "challenge_reason is too short"
    if proposal.get("challenge_source") not in _EVIDENCE_METHODS:
        return "challenge_source is not an allowed evidence method"
    if outcome == "falsified":
        if len(str(proposal.get("falsifying_evidence") or "").strip()) < 20:
            return "falsified outcome lacks specific falsifying evidence"
        if proposal.get("falsification_method") not in _EVIDENCE_METHODS:
            return "falsification_method is not allowed"
    elif proposal.get("falsifying_evidence") or proposal.get("falsification_method"):
        return "challenged outcome must not claim a falsification"

    references = proposal.get("consulted_references")
    if not isinstance(references, list) or not references:
        return "a mutation proposal requires at least one consulted workspace reference"
    for index, reference in enumerate(references):
        try:
            canonical, _ = _canonical_existing_path(reference, label=f"consulted_references[{index}]")
        except ValueError as error:
            return str(error)
        if canonical != reference:
            return f"consulted_references[{index}] must be canonical"

    finding = proposal.get("finding")
    if not isinstance(finding, dict):
        return "finding must be an object"
    expected_fields = {
        "entity_id",
        "entity_kind",
        "finding_kind",
        "evidence",
        "commentary",
        "concern",
        "applied_standards",
        "applied_hypotheses",
        "applied_exemplars",
        "uncertainty_note",
    }
    if set(finding) != expected_fields:
        return "finding fields do not match the canonical proposal contract"
    if finding.get("entity_id") != hypothesis_path:
        return "finding.entity_id must equal the canonical hypothesis path"
    if finding.get("entity_kind") != applies_to:
        return "finding.entity_kind must equal applies_to"
    if finding.get("finding_kind") != "hypothesis":
        return "finding.finding_kind must be hypothesis"
    if finding.get("applied_hypotheses") != [hypothesis_id]:
        return "finding.applied_hypotheses must contain exactly the current hypothesis id"
    for field, minimum in (("evidence", 20), ("commentary", 30), ("concern", 30)):
        if len(str(finding.get(field) or "").strip()) < minimum:
            return f"finding.{field} is too short"
    if len(str(proposal.get("narrative") or "").strip()) < 30:
        return "narrative is too short"
    return None


class ChallengeDiagnosticAgent(Worker):
    """DSH evidence proposal followed by Omni-owned state and finding writes."""

    SPEC = CHALLENGE_DIAGNOSTIC_SPEC
    DESCRIPTION = (
        "Read-only external hypothesis challenge investigation; OmniCompany owns "
        "validation, lifecycle transition, and finding publication."
    )
    FORMAT_IN = "doctor.challenge_diagnosis.request"
    FORMAT_OUT = "doctor.challenge_diagnosis.verdict"
    CALLER: ClassVar[str] = "doctor.challenge-diagnostic"

    def __init__(
        self,
        *,
        bus: Any | None = None,
        model: str | None = None,
        report_runner: StructuredChallengeRunner = run_readonly_structured_agent,
        hypothesis_gateway: HypothesisGateway | None = None,
        finding_publisher: FindingPublisher | None = None,
        **_: Any,
    ) -> None:
        self._bus = bus
        self._model = model or "deepseek-v4-flash"
        self._report_runner = report_runner
        self._gateway = hypothesis_gateway or YamlHypothesisGateway()
        self._finding_publisher = finding_publisher or self._publish_finding

    async def _publish_finding(self, finding: dict[str, Any], task_id: str) -> str:
        if self._bus is None:
            raise RuntimeError("Doctor finding publication requires an EventBus audit sink")
        verdict = await WriteFindingRouter(bus=self._bus).run(
            {
                "trace_id": task_id,
                "tool_name": "write_finding",
                "tool_use_id": f"omni-{task_id}",
                "tool_args": finding,
                "context": {
                    "cwd": omni_workspace_root(),
                    "project_root": omni_workspace_root(),
                    "trace_id": task_id,
                    "current_task_id": task_id,
                    "agent_id": self.SPEC.id,
                    "origin": "omni-control-plane",
                    "permission_mode": "default",
                },
            }
        )
        if verdict.output.get("is_error"):
            raise RuntimeError(str(verdict.output.get("result") or "finding publication failed"))
        return str(verdict.output.get("result") or "")

    async def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.FAIL, output={}, diagnosis="challenge input must be an object")
        try:
            hypothesis_path, _ = _canonical_existing_path(
                input_data.get("focus_hypothesis_yaml_path"),
                label="focus_hypothesis_yaml_path",
            )
            applies_to = str(input_data.get("applies_to") or "").strip()
            if applies_to not in _ENTITY_KINDS:
                raise ValueError(f"applies_to must be one of {_ENTITY_KINDS}")
            hypothesis = self._gateway.load(hypothesis_path)
            if str(hypothesis.get("applies_to") or "").strip() != applies_to:
                raise ValueError("request applies_to does not match the hypothesis snapshot")
            snapshot_hash = _snapshot_hash(hypothesis)
            request = {
                "focus_hypothesis_yaml_path": hypothesis_path,
                "applies_to": applies_to,
                "hypothesis_snapshot": hypothesis,
            }
            serialized = json.dumps(request, ensure_ascii=False, indent=2)
            if len(serialized) > _MAX_REQUEST_CHARS:
                raise ValueError(f"challenge request exceeds {_MAX_REQUEST_CHARS} characters")
            response = await self._report_runner(
                task="# Hypothesis challenge request\n" + serialized,
                instructions=_INSTRUCTIONS,
                result_schema=CHALLENGE_RESULT_SCHEMA,
                workspace_cwd=omni_workspace_root(),
                caller=self.CALLER,
                model=self._model,
                max_steps=24,
                max_output_tokens=12_000,
                timeout_seconds=600,
                allowed_tools=READONLY_AGENT_TOOLS,
            )
        except Exception as error:  # noqa: BLE001 - fail closed at runtime boundary
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=f"external challenge execution failed: {type(error).__name__}: {error}",
            )

        if not response.get("ok") or not isinstance(response.get("final"), dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=f"external challenge failed: {response.get('error') or 'missing result'}",
                details={"agent_runtime": response.get("runtime", {})},
            )
        proposal = dict(response["final"])
        error = _proposal_error(
            proposal,
            hypothesis_path=hypothesis_path,
            hypothesis=hypothesis,
            applies_to=applies_to,
        )
        if error:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"proposal_narrative": proposal.get("narrative", "")},
                diagnosis=f"challenge proposal rejected before application: {error}",
                details={"agent_runtime": response.get("runtime", {})},
            )
        if proposal["outcome"] == "andon":
            return Verdict(
                kind=VerdictKind.PARTIAL,
                output={"andon": True, "reason": proposal["andon_reason"]},
                diagnosis="challenge investigator requested more evidence",
                details={"agent_runtime": response.get("runtime", {})},
            )

        applied: dict[str, Any] = {}
        try:
            applied["hypothesis"] = self._gateway.apply(
                hypothesis_path,
                expected_snapshot_hash=snapshot_hash,
                proposal=proposal,
            )
            task_id = f"challenge-{str(hypothesis.get('id') or 'unknown')}"
            applied["finding"] = await self._finding_publisher(dict(proposal["finding"]), task_id)
        except Exception as application_error:  # noqa: BLE001 - return explicit applied prefix
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"applied": applied},
                diagnosis=(
                    "Omni challenge application failed: "
                    f"{type(application_error).__name__}: {application_error}"
                ),
                details={"agent_runtime": response.get("runtime", {})},
            )

        output = {
            "hypothesis_id": proposal["hypothesis_id"],
            "outcome": proposal["outcome"],
            "narrative": proposal["narrative"],
            "consulted_references": proposal["consulted_references"],
            "applied": applied,
            "_meta": {
                "stage": "external_proposal_omni_application",
                "authority": "omni-control-plane",
                "event_bus_role": "audit-only",
                "agent_runtime": response.get("runtime", {}),
            },
        }
        return Verdict(
            kind=VerdictKind.PASS,
            output=output,
            diagnosis=f"challenge proposal applied by Omni: {proposal['outcome']}",
            details={"agent_runtime": response.get("runtime", {})},
        )


__all__ = [
    "CHALLENGE_DIAGNOSTIC_SPEC",
    "CHALLENGE_RESULT_SCHEMA",
    "ChallengeDiagnosticAgent",
    "HypothesisGateway",
    "YamlHypothesisGateway",
]
