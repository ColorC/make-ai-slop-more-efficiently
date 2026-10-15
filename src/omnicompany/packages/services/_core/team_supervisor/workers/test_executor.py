# [OMNI] origin=codex domain=services/team_supervisor/workers ts=2026-08-14T00:00:00Z type=worker
# [OMNI] material_id="material:core.team_supervisor.workers.test_executor.runner.py"
"""Omni-owned orchestration for the external-runtime TestExecutor position.

Cordis may infer a missing sample input and evaluate evidence, but it never
receives a Team-dispatch capability.  The fixed, ingress-validated target is
executed between those two reasoning stages by the Omni control plane.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router


TEST_EXECUTOR_DESCRIPTION = (
    "Run the ingress-validated target through the Omni control plane, then use "
    "the external AgentRuntime to evaluate every declared hypothesis."
)
TEST_EXECUTOR_FORMAT_IN = [
    "team_supervisor.hypothesis_set",
    "team_supervisor.target_metadata",
]
TEST_EXECUTOR_FORMAT_OUT = "team_supervisor.test_results"

SAMPLE_INPUT_RUNTIME_INSTRUCTIONS = """You prepare one sample input for an OmniCompany Team.
The target identity is already fixed by the Omni control plane. You cannot run or select a Team.
Inspect only the granted workspace when needed. Prefer a historical input that actually exists;
otherwise derive the smallest schema-compatible input from the target entry Format and code.
Return exactly one JSON object matching the supplied schema. Evidence paths must name files you
actually inspected inside the granted workspace."""

SAMPLE_INPUT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "sample_input": {
            "type": "object",
            "description": "Smallest credible input for the already fixed target Team.",
        },
        "rationale": {"type": "string", "minLength": 20},
        "evidence_paths": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
    },
    "required": ["sample_input", "rationale", "evidence_paths"],
}

TEST_EVALUATION_RUNTIME_INSTRUCTIONS = """You are the evidence evaluator for an OmniCompany Team test.
The Omni control plane has already executed the fixed target and supplied its immutable run results.
You cannot run, select, retry, or modify any Team. Evaluate every supplied hypothesis exactly once.
Use the granted read-only workspace tools only to verify cited code or document evidence. Echo the
control-plane aggregate verdict exactly. Each observed sentence must describe concrete evidence,
and every evidence path must be a real file inside the granted workspace. Return exactly one JSON
object matching the supplied schema, with no prose or Markdown fences."""

TEST_EVALUATION_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "target_run_verdict": {
            "type": "string",
            "enum": ["PASS", "FAIL", "PARTIAL"],
        },
        "target_output_summary": {"type": "string", "minLength": 20},
        "target_traces_path": {"type": "string"},
        "hypothesis_evaluations": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "hypothesis_id": {
                        "type": "string",
                        "pattern": "^H-\\d{3}$",
                    },
                    "passed": {"type": "boolean"},
                    "observed": {"type": "string", "minLength": 10},
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "path": {"type": "string", "minLength": 1},
                                "excerpt": {"type": "string"},
                            },
                            "required": ["path"],
                        },
                    },
                },
                "required": ["hypothesis_id", "passed", "observed"],
            },
        },
    },
    "required": [
        "target_run_verdict",
        "target_output_summary",
        "hypothesis_evaluations",
    ],
}


TeamDispatcher = Callable[[str, dict[str, Any]], Awaitable[Any]]


async def dispatch_registered_team(
    target_team_id: str,
    sample_input: dict[str, Any],
) -> Any:
    """Dispatch through Omni's authoritative registry and TeamRunner."""
    from omnicompany.core.dispatch import dispatch

    return await dispatch(target_team_id, sample_input)


def _normalize_dispatch_result(result: Any) -> dict[str, Any]:
    if isinstance(result, Verdict):
        return {
            "verdict": result.kind.value.upper(),
            "output": result.output if isinstance(result.output, Mapping) else {},
            "diagnosis": result.diagnosis or "",
        }
    if isinstance(result, Mapping):
        verdict = str(result.get("verdict") or "PASS").upper()
        return {
            "verdict": verdict if verdict in {"PASS", "FAIL", "PARTIAL"} else "FAIL",
            "output": dict(result),
            "diagnosis": "",
        }
    return {
        "verdict": "FAIL",
        "output": {},
        "diagnosis": f"unsupported dispatch result: {type(result).__name__}",
    }


def _aggregate_verdict(results: list[dict[str, Any]]) -> str:
    kinds = [str(item.get("verdict") or "FAIL").upper() for item in results]
    if kinds and all(kind == "PASS" for kind in kinds):
        return "PASS"
    if kinds and all(kind == "FAIL" for kind in kinds):
        return "FAIL"
    return "PARTIAL"


class TestExecutorRuntimeRouter(Router):
    """Two external reasoning stages around one Omni-owned Team dispatch stage."""

    DESCRIPTION = TEST_EXECUTOR_DESCRIPTION
    FORMAT_IN = TEST_EXECUTOR_FORMAT_IN
    FORMAT_OUT = TEST_EXECUTOR_FORMAT_OUT

    def __init__(
        self,
        *,
        sample_input_agent: Router,
        evaluation_agent: Router,
        team_dispatcher: TeamDispatcher = dispatch_registered_team,
        max_run_count: int = 10,
    ) -> None:
        self._sample_input_agent = sample_input_agent
        self._evaluation_agent = evaluation_agent
        self._team_dispatcher = team_dispatcher
        self._max_run_count = max_run_count

    def bind_runtime_context(
        self,
        *,
        bus: Any,
        trace_id: str,
        parent_event_id: str,
    ) -> None:
        super().bind_runtime_context(
            bus=bus,
            trace_id=trace_id,
            parent_event_id=parent_event_id,
        )
        for child in (self._sample_input_agent, self._evaluation_agent):
            child.bind_runtime_context(
                bus=bus,
                trace_id=trace_id,
                parent_event_id=parent_event_id,
            )

    async def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, Mapping):
            return self._fail("TestExecutor input must be an object")
        metadata = input_data.get("_from_TargetIngressWorker") or input_data
        hypothesis_set = input_data.get("_from_HypothesisGeneratorWorker") or input_data
        if not isinstance(metadata, Mapping) or not isinstance(hypothesis_set, Mapping):
            return self._fail("TestExecutor fan-in payloads must be objects")

        target_team_id = str(metadata.get("target_team_id") or "").strip()
        if not target_team_id:
            return self._fail("ingress-validated target_team_id is missing")
        if target_team_id in {"team-supervisor", "team_supervisor"}:
            return self._fail("team-supervisor cannot recursively supervise itself")

        hypotheses = hypothesis_set.get("hypotheses")
        if not isinstance(hypotheses, list) or not hypotheses:
            return self._fail("hypotheses are missing")

        run_count = metadata.get("run_count", 1)
        if (
            not isinstance(run_count, int)
            or isinstance(run_count, bool)
            or run_count < 1
            or run_count > self._max_run_count
        ):
            return self._fail(
                f"run_count must be between 1 and {self._max_run_count}"
            )

        sample_input = metadata.get("sample_input")
        sample_details: dict[str, Any] = {}
        if sample_input is None:
            sample_verdict = await self._sample_input_agent.run(dict(metadata))
            if sample_verdict.kind != VerdictKind.PASS:
                return self._stage_failure("sample-input inference", sample_verdict)
            sample_output = sample_verdict.output
            if not isinstance(sample_output, Mapping) or not isinstance(
                sample_output.get("sample_input"), Mapping
            ):
                return self._fail("sample-input runtime returned no sample_input object")
            sample_input = dict(sample_output["sample_input"])
            sample_details = dict(sample_verdict.details)
        elif not isinstance(sample_input, Mapping):
            return self._fail("sample_input must be an object or null")
        else:
            sample_input = dict(sample_input)

        dispatch_results: list[dict[str, Any]] = []
        for _ in range(run_count):
            try:
                raw_result = await self._team_dispatcher(target_team_id, sample_input)
            except Exception as error:  # noqa: BLE001 - dispatch is a control-plane boundary
                dispatch_results.append(
                    {
                        "verdict": "FAIL",
                        "output": {},
                        "diagnosis": f"{type(error).__name__}: {error}",
                    }
                )
            else:
                dispatch_results.append(_normalize_dispatch_result(raw_result))

        aggregate = _aggregate_verdict(dispatch_results)
        evaluation_input = {
            "target_metadata": dict(metadata),
            "hypotheses": hypotheses,
            "sample_input": sample_input,
            "target_runs": dispatch_results,
            "control_plane_verdict": aggregate,
        }
        evaluation = await self._evaluation_agent.run(evaluation_input)
        if evaluation.kind != VerdictKind.PASS:
            return self._stage_failure("test evaluation", evaluation)
        return Verdict(
            kind=VerdictKind.PASS,
            output=evaluation.output,
            diagnosis=evaluation.diagnosis,
            confidence=evaluation.confidence,
            details={
                **dict(evaluation.details),
                "test_executor_control_plane": {
                    "target_team_id": target_team_id,
                    "run_count": run_count,
                    "aggregate_verdict": aggregate,
                    "sample_input_inferred": metadata.get("sample_input") is None,
                    "sample_runtime": sample_details.get("agent_runtime"),
                },
            },
        )

    @staticmethod
    def _fail(message: str) -> Verdict:
        return Verdict(kind=VerdictKind.FAIL, diagnosis=message)

    @staticmethod
    def _stage_failure(stage: str, verdict: Verdict) -> Verdict:
        return Verdict(
            kind=verdict.kind,
            output=verdict.output,
            diagnosis=f"TestExecutor {stage} failed: {verdict.diagnosis or 'unknown error'}",
            details=dict(verdict.details),
        )


__all__ = [
    "SAMPLE_INPUT_OUTPUT_SCHEMA",
    "SAMPLE_INPUT_RUNTIME_INSTRUCTIONS",
    "TEST_EVALUATION_OUTPUT_SCHEMA",
    "TEST_EVALUATION_RUNTIME_INSTRUCTIONS",
    "TEST_EXECUTOR_DESCRIPTION",
    "TEST_EXECUTOR_FORMAT_IN",
    "TEST_EXECUTOR_FORMAT_OUT",
    "TeamDispatcher",
    "TestExecutorRuntimeRouter",
]
