"""Shared external AgentRuntime boundary for Doctor semantic diagnoses.

The external runtime may inspect the workspace and propose a structured verdict.
OmniCompany remains responsible for validating and publishing that verdict; the
runtime receives no finding, Material, Decision, or filesystem write authority.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import json
from pathlib import Path
from typing import Any, ClassVar

from omnicompany.core.config import omni_workspace_root
from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.agent_runtime import run_readonly_structured_agent


StructuredDiagnosisRunner = Callable[..., Awaitable[dict[str, Any]]]

_ENTITY_KINDS = ("worker", "material", "team", "agent", "hook", "tool", "plan")
_FINDING_KINDS = ("spec", "hypothesis", "exemplar", "plan")
_BANNED_SCORING_FIELDS = {
    "severity",
    "score",
    "level",
    "tier",
    "confidence",
    "rating",
    "grade",
}
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
        "entity_id": {"type": "string", "minLength": 1, "maxLength": 1000},
        "entity_kind": {"type": "string", "enum": list(_ENTITY_KINDS)},
        "finding_kind": {"type": "string", "enum": list(_FINDING_KINDS)},
        "evidence": {"type": "string", "minLength": 20, "maxLength": 4000},
        "commentary": {"type": "string", "minLength": 30, "maxLength": 4000},
        "concern": {"type": "string", "minLength": 30, "maxLength": 4000},
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

DIAGNOSIS_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "target_entity_path",
        "target_entity_kind",
        "consulted_references",
        "findings",
        "narrative",
    ],
    "properties": {
        "target_entity_path": {"type": "string", "minLength": 1, "maxLength": 1000},
        "target_entity_kind": {"type": "string", "enum": list(_ENTITY_KINDS)},
        "consulted_references": {
            "type": "array",
            "maxItems": 100,
            "items": {"type": "string", "minLength": 1, "maxLength": 1000},
        },
        "findings": {"type": "array", "maxItems": 50, "items": _FINDING_SCHEMA},
        "narrative": {"type": "string", "minLength": 30, "maxLength": 8000},
    },
}

_INSTRUCTIONS = """\
You are a semantic diagnosis worker inside OmniCompany. Inspect only the supplied
workspace target and reference paths. Use the read/list/search tools for evidence.
Do not modify files, publish Materials, persist findings, make Decisions, or call
other agents. Return a diagnosis proposal only; OmniCompany validates and publishes
it.

Every finding must cite concrete file, section, symbol, or line evidence. Explain
the observation in commentary and its consequence/trade-off in concern. Do not use
numeric or categorical scoring fields such as severity, score, confidence, tier,
rating, grade, or level. Put genuine uncertainty in uncertainty_note. Echo the
canonical target path and kind exactly. Include every requested reference that you
actually inspected in consulted_references. Return exactly one schema-valid JSON
object with no Markdown fence.
"""


def _contains_banned_scoring_field(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in _BANNED_SCORING_FIELDS:
                return key
            nested = _contains_banned_scoring_field(child)
            if nested:
                return nested
    elif isinstance(value, list):
        for child in value:
            nested = _contains_banned_scoring_field(child)
            if nested:
                return nested
    return None


def _canonical_workspace_path(value: Any, *, label: str) -> str:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError(f"{label} must be a non-empty workspace path")
    workspace = Path(omni_workspace_root()).resolve()
    raw = Path(str(value).strip())
    resolved = raw.resolve() if raw.is_absolute() else (workspace / raw).resolve()
    try:
        relative = resolved.relative_to(workspace)
    except ValueError as error:
        raise ValueError(f"{label} is outside the OmniCompany workspace: {resolved}") from error
    if not resolved.exists():
        raise ValueError(f"{label} does not exist: {relative.as_posix()}")
    return relative.as_posix()


class ExternalDiagnosticWorker(Worker):
    """Base for read-only DSH diagnosis proposals with Omni-owned validation."""

    DIAGNOSIS_KIND: ClassVar[str]
    TARGET_FIELD: ClassVar[str]
    TARGET_KIND_FIELD: ClassVar[str | None] = "target_entity_kind"
    FIXED_TARGET_KIND: ClassVar[str | None] = None
    REFERENCE_FIELD: ClassVar[str] = ""
    FIXED_REFERENCES: ClassVar[tuple[str, ...]] = ()
    EXTRA_FIELDS: ClassVar[tuple[str, ...]] = ()
    CALLER: ClassVar[str]
    RESULT_SCHEMA: ClassVar[dict[str, Any]] = DIAGNOSIS_RESULT_SCHEMA
    INSTRUCTIONS: ClassVar[str] = _INSTRUCTIONS
    ALLOWED_FINDING_KINDS: ClassVar[tuple[str, ...] | None] = None
    ALLOWED_TOOLS: ClassVar[tuple[str, ...] | None] = None

    def __init__(
        self,
        *,
        model: str | None = None,
        report_runner: StructuredDiagnosisRunner = run_readonly_structured_agent,
        **_: Any,
    ) -> None:
        self._model = model or "deepseek-v4-flash"
        self._report_runner = report_runner

    def _build_request(self, input_data: Any) -> tuple[dict[str, Any], str, str, list[str]]:
        if not isinstance(input_data, dict):
            raise ValueError("diagnosis input must be an object")
        target = _canonical_workspace_path(input_data.get(self.TARGET_FIELD), label=self.TARGET_FIELD)
        if self.FIXED_TARGET_KIND:
            target_kind = self.FIXED_TARGET_KIND
        else:
            target_kind = str(input_data.get(self.TARGET_KIND_FIELD or "") or "").strip()
        if target_kind not in _ENTITY_KINDS:
            raise ValueError(f"target entity kind must be one of {_ENTITY_KINDS}")

        raw_references = (
            list(self.FIXED_REFERENCES)
            if self.FIXED_REFERENCES
            else input_data.get(self.REFERENCE_FIELD)
        )
        if not isinstance(raw_references, list) or not raw_references:
            label = self.REFERENCE_FIELD or "fixed references"
            raise ValueError(f"{label} must be a non-empty path list")
        references = [
            _canonical_workspace_path(
                item,
                label=f"{self.REFERENCE_FIELD or 'fixed_references'}[{index}]",
            )
            for index, item in enumerate(raw_references)
        ]
        request: dict[str, Any] = {
            "diagnosis_kind": self.DIAGNOSIS_KIND,
            "target_entity_path": target,
            "target_entity_kind": target_kind,
            "requested_references": references,
        }
        for field in self.EXTRA_FIELDS:
            request[field] = input_data.get(field)
        serialized = json.dumps(request, ensure_ascii=False, indent=2)
        if len(serialized) > _MAX_REQUEST_CHARS:
            raise ValueError(f"diagnosis request exceeds {_MAX_REQUEST_CHARS} characters")
        return request, target, target_kind, references

    def _validate_result(
        self,
        result: dict[str, Any],
        *,
        request: dict[str, Any],
        target: str,
        target_kind: str,
        references: list[str],
    ) -> str | None:
        del request
        if result.get("target_entity_path") != target:
            return "target_entity_path must exactly echo the canonical request target"
        if result.get("target_entity_kind") != target_kind:
            return "target_entity_kind must exactly echo the request target kind"
        narrative = str(result.get("narrative") or "").strip()
        if len(narrative) < 30:
            return "narrative must contain at least 30 characters"
        banned = _contains_banned_scoring_field(result)
        if banned:
            return f"banned scoring field present: {banned}"

        consulted = result.get("consulted_references")
        if not isinstance(consulted, list) or any(not isinstance(item, str) for item in consulted):
            return "consulted_references must be a string list"
        missing = sorted(set(references) - set(consulted))
        if missing:
            return f"requested references were not consulted: {missing}"

        findings = result.get("findings")
        if not isinstance(findings, list):
            return "findings must be a list"
        for index, finding in enumerate(findings):
            if not isinstance(finding, dict):
                return f"findings[{index}] must be an object"
            if finding.get("entity_id") != target:
                return f"findings[{index}].entity_id must equal the request target"
            if finding.get("entity_kind") != target_kind:
                return f"findings[{index}].entity_kind must equal the request target kind"
            allowed_finding_kinds = self.ALLOWED_FINDING_KINDS or (self.DIAGNOSIS_KIND,)
            if finding.get("finding_kind") not in allowed_finding_kinds:
                return (
                    f"findings[{index}].finding_kind must be one of "
                    f"{allowed_finding_kinds!r}"
                )
            for field, minimum in (("evidence", 20), ("commentary", 30), ("concern", 30)):
                if len(str(finding.get(field) or "").strip()) < minimum:
                    return f"findings[{index}].{field} is too short"
        return None

    async def run(self, input_data: Any) -> Verdict:
        try:
            request, target, target_kind, references = self._build_request(input_data)
            runner_kwargs: dict[str, Any] = {
                "task": "# Doctor diagnosis request\n" + json.dumps(request, ensure_ascii=False, indent=2),
                "instructions": self.INSTRUCTIONS,
                "result_schema": self.RESULT_SCHEMA,
                "workspace_cwd": omni_workspace_root(),
                "caller": self.CALLER,
                "model": self._model,
                "max_steps": 24,
                "max_output_tokens": 12_000,
                "timeout_seconds": 600,
            }
            if self.ALLOWED_TOOLS is not None:
                runner_kwargs["allowed_tools"] = self.ALLOWED_TOOLS
            response = await self._report_runner(
                **runner_kwargs,
            )
        except Exception as error:  # noqa: BLE001 - fail closed at the runtime boundary
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=f"external AgentRuntime execution failed: {type(error).__name__}: {error}",
            )
        if not response.get("ok") or not isinstance(response.get("final"), dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=f"external diagnosis failed: {response.get('error') or 'missing result'}",
                details={"agent_runtime": response.get("runtime", {})},
            )

        result = dict(response["final"])
        semantic_error = self._validate_result(
            result,
            request=request,
            target=target,
            target_kind=target_kind,
            references=references,
        )
        if semantic_error:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=result,
                diagnosis=f"external diagnosis rejected: {semantic_error}",
                details={"agent_runtime": response.get("runtime", {})},
            )
        result["_meta"] = {
            "stage": "external_agent_runtime",
            "agent_runtime": response.get("runtime", {}),
            "authority": "omnicompany",
        }
        return Verdict(
            kind=VerdictKind.PASS,
            output=result,
            diagnosis=f"external {self.DIAGNOSIS_KIND} diagnosis accepted",
            details={"agent_runtime": response.get("runtime", {})},
        )


__all__ = [
    "DIAGNOSIS_RESULT_SCHEMA",
    "ExternalDiagnosticWorker",
    "StructuredDiagnosisRunner",
]
