"""Derive hypothesis proposals externally; keep IDs and persistence in Omni."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from omnicompany.core.pycompat import UTC
import json
from pathlib import Path
import re
from typing import Any

import yaml

from omnicompany.core.config import omni_workspace_root
from omnicompany.packages.services._core.agent import AgentSpec
from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._diagnosis.doctor.tools.write_hypothesis import (
    WriteHypothesisRouter,
)
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.agent_runtime import run_readonly_structured_agent


StructuredDerivationRunner = Callable[..., Awaitable[dict[str, Any]]]
HypothesisPublisher = Callable[[dict[str, Any], str], Awaitable[str]]

_ENTITY_KINDS = ("worker", "material", "team", "agent", "hook", "tool", "plan")
_SOURCE_KINDS = ("spec", "plan", "code", "exemplar")
_MAX_REQUEST_CHARS = 40_000

_PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "source_kind",
        "source_path",
        "source_excerpt",
        "statement",
        "motivation",
        "applies_to",
        "evidence_query",
        "tags",
    ],
    "properties": {
        "source_kind": {"type": "string", "enum": list(_SOURCE_KINDS)},
        "source_path": {"type": "string", "minLength": 1, "maxLength": 1000},
        "source_excerpt": {"type": "string", "minLength": 20, "maxLength": 4000},
        "statement": {"type": "string", "minLength": 30, "maxLength": 4000},
        "motivation": {"type": "string", "minLength": 50, "maxLength": 4000},
        "applies_to": {"type": "string", "enum": list(_ENTITY_KINDS)},
        "evidence_query": {"type": "string", "minLength": 20, "maxLength": 4000},
        "tags": {
            "type": "array",
            "maxItems": 30,
            "items": {"type": "string", "minLength": 1, "maxLength": 200},
        },
    },
}

DERIVATION_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "source_paths", "proposals", "narrative", "gaps"],
    "properties": {
        "status": {"type": "string", "enum": ["ok", "information_insufficient"]},
        "source_paths": {
            "type": "array",
            "maxItems": 50,
            "items": {"type": "string", "minLength": 1, "maxLength": 1000},
        },
        "proposals": {"type": "array", "maxItems": 20, "items": _PROPOSAL_SCHEMA},
        "narrative": {"type": "string", "minLength": 30, "maxLength": 8000},
        "gaps": {
            "type": "array",
            "maxItems": 30,
            "items": {"type": "string", "minLength": 10, "maxLength": 2000},
        },
    },
}

_INSTRUCTIONS = """\
You are a read-only hypothesis derivation worker. Inspect only the supplied source
paths and optional falsified-hypothesis paths. Return novel hypothesis proposals;
do not assign IDs, write YAML, publish Materials, mutate existing hypotheses, or
call another agent. OmniCompany owns deduplication, ID allocation, and persistence.

Each proposal must cite one concrete existing file under the supplied source roots
and quote a concrete source excerpt. applies_to must exactly equal derivation_focus.
Do not turn descriptive prose into a mandatory rule without evidence. Return zero
proposals when no grounded novel hypothesis exists. Use information_insufficient
with explicit gaps when sources cannot support a responsible derivation. Return one
schema-valid JSON object without Markdown.
"""


HYPOTHESIS_DERIVER_SPEC = AgentSpec(
    id="doctor.hypothesis_deriver",
    name="HypothesisDeriverAgent",
    domain="doctor",
    llm_model="deepseek-v4-flash",
    llm_max_tokens=12_000,
    llm_max_turns=24,
    llm_timeout_seconds=600,
    output_materials=(
        "doctor.hypothesis_derivation.report",
        "doctor.hypothesis.statement",
    ),
    primary_output="doctor.hypothesis_derivation.report",
    trigger_materials=("doctor.hypothesis_derivation.request",),
    accepted_input_materials=("doctor.hypothesis_derivation.request",),
    forbidden_input_materials=(
        "doctor.hypothesis_derivation.report",
        "doctor.hypothesis.statement",
    ),
    user_input_required_fields=("source_paths", "derivation_focus", "max_hypotheses"),
    tools=(),
    workspace={"permission_mode": "readonly", "write_prefixes": ()},
    test_baseline={
        "green_samples": ("docs/standards/concepts/worker.md",),
        "red_samples": (
            "src/omnicompany/packages/services/_diagnosis/doctor/"
            "_test_fixtures/red_sources/random_readme.md",
        ),
    },
)


def _canonical_path(value: Any, *, label: str) -> tuple[str, Path]:
    workspace = Path(omni_workspace_root()).resolve()
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError(f"{label} must be a non-empty workspace path")
    raw = Path(str(value).strip())
    resolved = raw.resolve() if raw.is_absolute() else (workspace / raw).resolve()
    try:
        relative = resolved.relative_to(workspace)
    except ValueError as error:
        raise ValueError(f"{label} is outside the OmniCompany workspace") from error
    if not resolved.exists():
        raise ValueError(f"{label} does not exist: {relative.as_posix()}")
    return relative.as_posix(), resolved


def _normalized_statement(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _existing_hypotheses() -> list[dict[str, Any]]:
    root = Path(omni_workspace_root()) / "data" / "services" / "doctor" / "hypotheses"
    values: list[dict[str, Any]] = []
    if not root.is_dir():
        return values
    for path in sorted([*root.glob("*.yaml"), *root.glob("*.yml")]):
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(value, dict):
            values.append(dict(value))
    return values


def _allocate_ids(count: int, existing: list[dict[str, Any]]) -> list[str]:
    day = datetime.now(UTC).date().isoformat()
    pattern = re.compile(rf"^H-{re.escape(day)}-(\d+)$")
    highest = max(
        (int(match.group(1)) for item in existing if (match := pattern.match(str(item.get("id") or "")))),
        default=0,
    )
    return [f"H-{day}-{highest + offset:03d}" for offset in range(1, count + 1)]


class HypothesisDeriverAgent(Worker):
    """External proposal generation followed by Omni ID allocation and publishing."""

    SPEC = HYPOTHESIS_DERIVER_SPEC
    DESCRIPTION = "Read-only external derivation with Omni-owned hypothesis persistence."
    FORMAT_IN = "doctor.hypothesis_derivation.request"
    FORMAT_OUT = "doctor.hypothesis_derivation.report"

    def __init__(
        self,
        *,
        bus: Any | None = None,
        model: str | None = None,
        report_runner: StructuredDerivationRunner = run_readonly_structured_agent,
        hypothesis_publisher: HypothesisPublisher | None = None,
        existing_loader: Callable[[], list[dict[str, Any]]] = _existing_hypotheses,
        **_: Any,
    ) -> None:
        self._bus = bus
        self._model = model or "deepseek-v4-flash"
        self._report_runner = report_runner
        self._publisher = hypothesis_publisher or self._publish
        self._existing_loader = existing_loader

    async def _publish(self, hypothesis: dict[str, Any], task_id: str) -> str:
        if self._bus is None:
            raise RuntimeError("hypothesis publication requires an EventBus audit sink")
        verdict = await WriteHypothesisRouter(bus=self._bus).run(
            {
                "trace_id": task_id,
                "tool_name": "write_hypothesis",
                "tool_use_id": f"omni-{hypothesis['id']}",
                "tool_args": hypothesis,
                "context": {
                    "cwd": omni_workspace_root(),
                    "project_root": omni_workspace_root(),
                    "trace_id": task_id,
                    "current_task_id": task_id,
                    "agent_id": self.SPEC.id,
                    "origin": "omni-control-plane",
                },
            }
        )
        if verdict.output.get("is_error"):
            raise RuntimeError(str(verdict.output.get("result") or "hypothesis publication failed"))
        return str(verdict.output.get("result") or "")

    async def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.FAIL, output={}, diagnosis="derivation input must be an object")
        try:
            raw_sources = input_data.get("source_paths")
            if not isinstance(raw_sources, list) or not raw_sources:
                raise ValueError("source_paths must be a non-empty list")
            sources = [_canonical_path(value, label=f"source_paths[{index}]") for index, value in enumerate(raw_sources)]
            raw_falsified = input_data.get("falsified_hypothesis_paths") or []
            if not isinstance(raw_falsified, list):
                raise ValueError("falsified_hypothesis_paths must be a list")
            falsified = [_canonical_path(value, label=f"falsified_hypothesis_paths[{index}]") for index, value in enumerate(raw_falsified)]
            focus = str(input_data.get("derivation_focus") or "").strip()
            if focus not in _ENTITY_KINDS:
                raise ValueError(f"derivation_focus must be one of {_ENTITY_KINDS}")
            maximum = input_data.get("max_hypotheses")
            if type(maximum) is not int or maximum < 1 or maximum > 20:
                raise ValueError("max_hypotheses must be an integer from 1 through 20")
            existing = self._existing_loader()
            request = {
                "source_paths": [item[0] for item in sources],
                "falsified_hypothesis_paths": [item[0] for item in falsified],
                "derivation_focus": focus,
                "max_hypotheses": maximum,
                "existing_hypotheses": [
                    {"id": item.get("id", ""), "statement": item.get("statement", "")}
                    for item in existing[:500]
                ],
            }
            serialized = json.dumps(request, ensure_ascii=False, indent=2)
            if len(serialized) > _MAX_REQUEST_CHARS:
                raise ValueError(f"derivation request exceeds {_MAX_REQUEST_CHARS} characters")
            response = await self._report_runner(
                task="# Hypothesis derivation request\n" + serialized,
                instructions=_INSTRUCTIONS,
                result_schema=DERIVATION_RESULT_SCHEMA,
                workspace_cwd=omni_workspace_root(),
                caller="doctor.hypothesis-deriver",
                model=self._model,
                max_steps=24,
                max_output_tokens=12_000,
                timeout_seconds=600,
            )
        except Exception as error:  # noqa: BLE001
            return Verdict(kind=VerdictKind.FAIL, output={}, diagnosis=f"external derivation failed: {type(error).__name__}: {error}")
        if not response.get("ok") or not isinstance(response.get("final"), dict):
            return Verdict(kind=VerdictKind.FAIL, output={}, diagnosis=f"external derivation failed: {response.get('error') or 'missing result'}", details={"agent_runtime": response.get("runtime", {})})
        result = dict(response["final"])
        if result.get("status") == "information_insufficient":
            gaps = result.get("gaps")
            if not isinstance(gaps, list) or not gaps:
                return Verdict(kind=VerdictKind.FAIL, output=result, diagnosis="information_insufficient result lacks gaps")
            return Verdict(kind=VerdictKind.PARTIAL, output={"andon": True, "gaps": gaps}, diagnosis="deriver requested more evidence", details={"agent_runtime": response.get("runtime", {})})
        proposals = result.get("proposals")
        if result.get("status") != "ok" or not isinstance(proposals, list) or len(proposals) > maximum:
            return Verdict(kind=VerdictKind.FAIL, output={}, diagnosis="invalid derivation proposal envelope")
        if result.get("source_paths") != [item[0] for item in sources]:
            return Verdict(kind=VerdictKind.FAIL, output={}, diagnosis="source_paths must exactly echo the canonical request")

        known = {_normalized_statement(item.get("statement")) for item in existing}
        source_roots = [item[1] for item in sources]
        for index, proposal in enumerate(proposals):
            if not isinstance(proposal, dict) or proposal.get("applies_to") != focus:
                return Verdict(kind=VerdictKind.FAIL, output={}, diagnosis=f"proposal {index} has invalid applies_to")
            statement = _normalized_statement(proposal.get("statement"))
            if not statement or statement in known:
                return Verdict(kind=VerdictKind.FAIL, output={}, diagnosis=f"proposal {index} duplicates an existing or proposed statement")
            known.add(statement)
            try:
                canonical, evidence_path = _canonical_path(proposal.get("source_path"), label=f"proposals[{index}].source_path")
            except ValueError as error:
                return Verdict(kind=VerdictKind.FAIL, output={}, diagnosis=str(error))
            if canonical != proposal.get("source_path") or not evidence_path.is_file():
                return Verdict(kind=VerdictKind.FAIL, output={}, diagnosis=f"proposal {index} source_path must be a canonical file")
            if not any(evidence_path == root or (root.is_dir() and evidence_path.is_relative_to(root)) for root in source_roots):
                return Verdict(kind=VerdictKind.FAIL, output={}, diagnosis=f"proposal {index} source_path is outside supplied sources")
            excerpt = str(proposal.get("source_excerpt") or "").strip()
            try:
                source_text = evidence_path.read_text(encoding="utf-8")
            except Exception as error:
                return Verdict(kind=VerdictKind.FAIL, output={}, diagnosis=f"proposal {index} source cannot be verified: {error}")
            if excerpt not in source_text:
                return Verdict(kind=VerdictKind.FAIL, output={}, diagnosis=f"proposal {index} source_excerpt is not verbatim source text")
            for field, minimum in (("statement", 30), ("motivation", 50), ("evidence_query", 20)):
                if len(str(proposal.get(field) or "").strip()) < minimum:
                    return Verdict(kind=VerdictKind.FAIL, output={}, diagnosis=f"proposal {index}.{field} is too short")

        ids = _allocate_ids(len(proposals), existing)
        applied: list[dict[str, Any]] = []
        for index, (hypothesis_id, proposal) in enumerate(zip(ids, proposals, strict=True)):
            hypothesis = {"id": hypothesis_id, **proposal, "status": "active"}
            try:
                publication = await self._publisher(hypothesis, f"derivation-{hypothesis_id}")
            except Exception as error:  # noqa: BLE001
                return Verdict(kind=VerdictKind.FAIL, output={"applied": applied}, diagnosis=f"hypothesis application {index} failed: {type(error).__name__}: {error}", details={"agent_runtime": response.get("runtime", {})})
            applied.append({"id": hypothesis_id, "publication": publication})
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "source_paths": result["source_paths"],
                "derived_hypothesis_ids": ids,
                "narrative": result.get("narrative", ""),
                "applied": applied,
                "_meta": {"stage": "external_proposal_omni_application", "authority": "omni-control-plane", "agent_runtime": response.get("runtime", {})},
            },
            diagnosis=f"Omni published {len(applied)} derived hypotheses",
            details={"agent_runtime": response.get("runtime", {})},
        )


__all__ = ["DERIVATION_RESULT_SCHEMA", "HYPOTHESIS_DERIVER_SPEC", "HypothesisDeriverAgent"]
