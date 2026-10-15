from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from omnicompany.packages.services._core.team_supervisor.run import build_bindings
from omnicompany.packages.services._core.team_supervisor.workers.health_criteria_designer import (
    HEALTH_CRITERIA_OUTPUT_SCHEMA,
)
from omnicompany.packages.services._core.team_supervisor.workers.purpose_interpreter import (
    PURPOSE_OUTPUT_SCHEMA,
)
from omnicompany.packages.services._core.team_supervisor.workers.product_form_analyzer import (
    PRODUCT_FORM_OUTPUT_SCHEMA,
)
from omnicompany.packages.services._core.team_supervisor.workers.hypothesis_generator import (
    HYPOTHESIS_OUTPUT_SCHEMA,
)
from omnicompany.packages.services._core.team_supervisor.workers.test_executor import (
    SAMPLE_INPUT_OUTPUT_SCHEMA,
    TEST_EVALUATION_OUTPUT_SCHEMA,
    TestExecutorRuntimeRouter as _TestExecutorRuntimeRouter,
)
from omnicompany.packages.services._core.team_supervisor.workers.target_ingress import (
    TargetIngressWorker,
)
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.agent_runtime import AgentRuntimeProcessClient
from omnicompany.runtime.exec.agent_runtime_node import AgentRuntimeNodeRouter


class _StructuredInvoker:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def run(self, request: dict[str, Any], event_sink=None) -> dict[str, Any]:
        self.requests.append(request)
        structured = {
            "key_observations": [
                "Inspect whether the output contains concrete evidence references.",
                "Inspect whether the output shape matches the declared product form.",
                "Inspect whether downstream stakeholders can use the emitted result.",
            ],
            "red_flags": [
                "The result contains claims without any traceable evidence source.",
                "The Team performs behavior explicitly excluded by its non-goals.",
            ],
            "oracle_strategies": [
                {
                    "what_to_check": "Check every evidence reference resolves to a real source.",
                    "implementation_hint": "Resolve each reference and assert its selected fragment exists.",
                },
                {
                    "what_to_check": "Check the result contains every required schema field.",
                    "implementation_hint": "Validate the emitted object with the canonical Format schema.",
                },
                {
                    "what_to_check": "Check the result avoids all declared non-goal behavior.",
                    "implementation_hint": "Translate each non-goal into a deterministic negative assertion.",
                },
            ],
        }
        return {
            "kind": "run.result",
            "protocol_version": "0.1.0",
            "request_id": request["request_id"],
            "run_id": request["run_id"],
            "session_id": request["session_id"],
            "status": "succeeded",
            "final_output": {"text": "structured", "structured": structured},
            "material_refs": [],
            "artifact_refs": [],
            "usage": {"input_tokens": 100, "output_tokens": 200},
        }


class _FailedInvoker:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def run(self, request: dict[str, Any], event_sink=None) -> dict[str, Any]:
        self.requests.append(request)
        return {
            "kind": "run.result",
            "protocol_version": "0.1.0",
            "request_id": request["request_id"],
            "run_id": request["run_id"],
            "session_id": request["session_id"],
            "status": "failed",
            "error": {"code": "provider_error", "message": "external failed"},
            "material_refs": [],
            "artifact_refs": [],
            "usage": {},
        }


class _PurposeInvoker:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def run(self, request: dict[str, Any], event_sink=None) -> dict[str, Any]:
        self.requests.append(request)
        return {
            "kind": "run.result",
            "protocol_version": "0.1.0",
            "request_id": request["request_id"],
            "run_id": request["run_id"],
            "session_id": request["session_id"],
            "status": "succeeded",
            "final_output": {
                "text": "structured",
                "structured": {
                    "essence": (
                        "This Team turns source evidence into a repeatable health "
                        "assessment for maintainers."
                    ),
                    "replaces": "It replaces ad-hoc manual source inspection.",
                    "non_goals": [
                        "It does not modify or repair the Team being assessed."
                    ],
                    "stakeholder_use": (
                        "A maintainer uses the report to decide whether investigation "
                        "is needed."
                    ),
                    "evidence_sources": [
                        {"file": "src/omnicompany/example.py", "section": "module"}
                    ],
                },
            },
            "material_refs": [],
            "artifact_refs": [],
            "usage": {},
        }


class _ProductFormInvoker:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def run(self, request: dict[str, Any], event_sink=None) -> dict[str, Any]:
        self.requests.append(request)
        return {
            "kind": "run.result",
            "protocol_version": "0.1.0",
            "request_id": request["request_id"],
            "run_id": request["run_id"],
            "session_id": request["session_id"],
            "status": "succeeded",
            "final_output": {
                "text": "structured",
                "structured": {
                    "essence": (
                        "The Team emits a structured health report backed by "
                        "execution evidence."
                    ),
                    "minimal_passing_evidence": (
                        "The report contains a verdict and at least one concrete "
                        "test result."
                    ),
                    "failure_signals": [
                        "The verdict field is absent from the emitted report.",
                        "The test_results list contains no executed check.",
                    ],
                    "concrete_examples": [
                        {
                            "path": "src/omnicompany/example.py",
                            "note": "The sink returns the registered output shape.",
                        }
                    ],
                    "schema_fields_observed": ["verdict", "test_results"],
                },
            },
            "material_refs": [],
            "artifact_refs": [],
            "usage": {},
        }


class _PurposeRepairInvoker(_PurposeInvoker):
    async def run(self, request: dict[str, Any], event_sink=None) -> dict[str, Any]:
        if not self.requests:
            self.requests.append(request)
            return {
                "kind": "run.result",
                "protocol_version": "0.1.0",
                "request_id": request["request_id"],
                "run_id": request["run_id"],
                "session_id": request["session_id"],
                "status": "failed",
                "final_output": {"text": "not json"},
                "error": {
                    "code": "invalid_output",
                    "message": "provider response is not exactly one JSON value",
                    "retryable": False,
                },
                "material_refs": [],
                "artifact_refs": [],
                "usage": {},
            }
        return await super().run(request, event_sink)


class _HypothesisInvoker:
    def __init__(self, *, duplicate_ids: bool = False) -> None:
        self.requests: list[dict[str, Any]] = []
        self.duplicate_ids = duplicate_ids

    async def run(self, request: dict[str, Any], event_sink=None) -> dict[str, Any]:
        self.requests.append(request)
        hypotheses = [
            {
                "id": "H-001" if self.duplicate_ids else f"H-{index:03d}",
                "condition": f"When concrete execution condition number {index} is observed.",
                "expectation": f"The emitted result satisfies observable expectation {index}.",
                "oracle_code_hint": f"Assert deterministic output predicate number {index}.",
                "rationale": f"Derived from the authoritative three-question brief {index}.",
            }
            for index in range(1, 11)
        ]
        return {
            "kind": "run.result",
            "protocol_version": "0.1.0",
            "request_id": request["request_id"],
            "run_id": request["run_id"],
            "session_id": request["session_id"],
            "status": "succeeded",
            "final_output": {
                "text": "structured",
                "structured": {"hypotheses": hypotheses},
            },
            "material_refs": [],
            "artifact_refs": [],
            "usage": {},
        }


class _TestExecutorInvoker:
    def __init__(self, evidence_path: Path) -> None:
        self.evidence_path = evidence_path
        self.requests: list[dict[str, Any]] = []

    async def run(self, request: dict[str, Any], event_sink=None) -> dict[str, Any]:
        self.requests.append(request)
        position_id = request["position_id"]
        if position_id == "TestExecutorWorker:SampleInput":
            structured = {
                "sample_input": {"request": "inferred fixture"},
                "rationale": "The target entry schema requires one request string.",
                "evidence_paths": [str(self.evidence_path)],
            }
        else:
            payload = json.loads(request["input"]["text"])
            structured = {
                "target_run_verdict": payload["control_plane_verdict"],
                "target_output_summary": (
                    "The fixed target completed and returned deterministic fixture output."
                ),
                "hypothesis_evaluations": [
                    {
                        "hypothesis_id": hypothesis["id"],
                        "passed": True,
                        "observed": "The deterministic target output satisfies this hypothesis.",
                        "evidence": [{"path": str(self.evidence_path)}],
                    }
                    for hypothesis in payload["hypotheses"]
                ],
            }
        return {
            "kind": "run.result",
            "protocol_version": "0.1.0",
            "request_id": request["request_id"],
            "run_id": request["run_id"],
            "session_id": request["session_id"],
            "status": "succeeded",
            "final_output": {"text": "structured", "structured": structured},
            "material_refs": [],
            "artifact_refs": [],
            "usage": {},
        }


def test_target_ingress_resolves_category_organized_team_package() -> None:
    verdict = TargetIngressWorker().run(
        {
            "target_team_id": "csv-to-md",
            "sample_input": {"path": "fixture.csv"},
            "run_count": 1,
        }
    )

    assert verdict.kind == VerdictKind.PASS
    assert verdict.output is not None
    path = Path(verdict.output["team_code_dir"])
    assert path.as_posix().endswith("services/_utility/csv_to_md")
    assert (path / "team.py").is_file()


def test_health_criteria_position_defaults_to_external_runtime(tmp_path: Path) -> None:
    bindings = build_bindings(
        agent_runtime_invoker=_StructuredInvoker(),
        workspace_cwd=tmp_path,
    )

    assert isinstance(bindings["HealthCriteriaDesignerWorker"], AgentRuntimeNodeRouter)
    assert HEALTH_CRITERIA_OUTPUT_SCHEMA["additionalProperties"] is False
    oracle_schema = HEALTH_CRITERIA_OUTPUT_SCHEMA["properties"]["oracle_strategies"]
    assert oracle_schema["items"]["additionalProperties"] is False


@pytest.mark.asyncio
async def test_purpose_position_default_gets_only_workspace_read_tools(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "src" / "omnicompany" / "example.py"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("# evidence\n", encoding="utf-8")
    invoker = _PurposeInvoker()
    binding = build_bindings(
        agent_runtime_invoker=invoker,
        workspace_cwd=tmp_path,
    )["PurposeInterpreterWorker"]

    assert isinstance(binding, AgentRuntimeNodeRouter)
    verdict = await binding.run(
        {
            "target_team_id": "team_supervisor",
            "team_code_dir": "src/omnicompany/packages/services/_core/team_supervisor",
            "team_py_path": (
                "src/omnicompany/packages/services/_core/team_supervisor/team.py"
            ),
            "worker_files": ["purpose_interpreter.py"],
        }
    )

    assert verdict.kind == VerdictKind.PASS
    assert len(invoker.requests) == 1
    request = invoker.requests[0]
    AgentRuntimeProcessClient(["unused"]).validate(request)
    assert request["position_id"] == "PurposeInterpreterWorker"
    assert request["capability_grant"]["allowed_tools"] == [
        "omni_workspace_read",
        "omni_workspace_list",
        "omni_workspace_search",
    ]
    assert request["capability_grant"]["allowed_write_roots"] == []
    assert request["capability_grant"]["network_access"] is False
    assert request["input"]["expected_output_schema"] == PURPOSE_OUTPUT_SCHEMA


@pytest.mark.asyncio
async def test_product_form_position_default_gets_only_workspace_read_tools(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "src" / "omnicompany" / "example.py"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("# evidence\n", encoding="utf-8")
    invoker = _ProductFormInvoker()
    binding = build_bindings(
        agent_runtime_invoker=invoker,
        workspace_cwd=tmp_path,
    )["ProductFormAnalyzerWorker"]

    assert isinstance(binding, AgentRuntimeNodeRouter)
    verdict = await binding.run(
        {
            "target_team_id": "team_supervisor",
            "team_code_dir": "src/omnicompany/packages/services/_core/team_supervisor",
            "format_out_id": "team_supervisor.health_report",
        }
    )

    assert verdict.kind == VerdictKind.PASS
    request = invoker.requests[0]
    AgentRuntimeProcessClient(["unused"]).validate(request)
    assert request["position_id"] == "ProductFormAnalyzerWorker"
    assert request["capability_grant"]["allowed_tools"] == [
        "omni_workspace_read",
        "omni_workspace_list",
        "omni_workspace_search",
    ]
    assert request["capability_grant"]["allowed_write_roots"] == []
    assert request["input"]["expected_output_schema"] == PRODUCT_FORM_OUTPUT_SCHEMA


@pytest.mark.asyncio
async def test_workspace_evidence_missing_after_runtime_is_rejected(
    tmp_path: Path,
) -> None:
    invoker = _PurposeInvoker()
    binding = build_bindings(
        agent_runtime_invoker=invoker,
        workspace_cwd=tmp_path,
    )["PurposeInterpreterWorker"]

    verdict = await binding.run({"target_team_id": "team_supervisor"})

    assert verdict.kind == VerdictKind.FAIL
    assert verdict.diagnosis == (
        "AgentRuntime output rejected: "
        "evidence_sources[0] does not resolve to an existing file"
    )
    assert len(invoker.requests) == 2


@pytest.mark.asyncio
async def test_invalid_structured_output_gets_one_external_repair_attempt(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "src" / "omnicompany" / "example.py"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("# evidence\n", encoding="utf-8")
    invoker = _PurposeRepairInvoker()
    binding = build_bindings(
        agent_runtime_invoker=invoker,
        workspace_cwd=tmp_path,
    )["PurposeInterpreterWorker"]

    verdict = await binding.run({"target_team_id": "team_supervisor"})

    assert verdict.kind == VerdictKind.PASS
    assert len(invoker.requests) == 2
    first, second = invoker.requests
    assert first["run_id"] != second["run_id"]
    assert second["metadata"]["validation_attempt"] == 2
    assert "previous external-runtime attempt was rejected" in second["input"][
        "instructions"
    ]


@pytest.mark.asyncio
async def test_hypothesis_position_defaults_to_external_and_enforces_unique_ids(
    tmp_path: Path,
) -> None:
    invoker = _HypothesisInvoker()
    binding = build_bindings(
        agent_runtime_invoker=invoker,
        workspace_cwd=tmp_path,
    )["HypothesisGeneratorWorker"]

    verdict = await binding.run({"_from_ProductFormAnalyzerWorker": {}})

    assert verdict.kind == VerdictKind.PASS
    request = invoker.requests[0]
    AgentRuntimeProcessClient(["unused"]).validate(request)
    assert request["capability_grant"]["allowed_tools"] == []
    assert request["input"]["expected_output_schema"] == HYPOTHESIS_OUTPUT_SCHEMA

    duplicate_invoker = _HypothesisInvoker(duplicate_ids=True)
    duplicate_binding = build_bindings(
        agent_runtime_invoker=duplicate_invoker,
        workspace_cwd=tmp_path,
    )["HypothesisGeneratorWorker"]
    rejected = await duplicate_binding.run({"_from_ProductFormAnalyzerWorker": {}})
    assert rejected.kind == VerdictKind.FAIL
    assert rejected.diagnosis == (
        "AgentRuntime output rejected: hypotheses contains duplicate id values"
    )
    assert len(duplicate_invoker.requests) == 2


@pytest.mark.asyncio
async def test_test_executor_keeps_team_dispatch_in_omni_control_plane(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "target" / "team.py"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("# target\n", encoding="utf-8")
    invoker = _TestExecutorInvoker(evidence)
    dispatch_calls: list[tuple[str, dict[str, Any]]] = []

    async def dispatch_target(target: str, payload: dict[str, Any]) -> Verdict:
        dispatch_calls.append((target, payload))
        return Verdict(kind=VerdictKind.PASS, output={"result": "fixture"})

    binding = build_bindings(
        agent_runtime_invoker=invoker,
        workspace_cwd=tmp_path,
        team_dispatcher=dispatch_target,
    )["TestExecutorWorker"]
    verdict = await binding.run(
        {
            "_from_TargetIngressWorker": {
                "target_team_id": "fixed-target",
                "sample_input": {"request": "provided fixture"},
                "run_count": 1,
            },
            "_from_HypothesisGeneratorWorker": {
                "hypotheses": [
                    {"id": "H-001", "condition": "When the target runs."},
                    {"id": "H-002", "condition": "When output is inspected."},
                ]
            },
        }
    )

    assert isinstance(binding, _TestExecutorRuntimeRouter)
    assert verdict.kind == VerdictKind.PASS
    assert dispatch_calls == [("fixed-target", {"request": "provided fixture"})]
    assert len(invoker.requests) == 1
    request = invoker.requests[0]
    AgentRuntimeProcessClient(["unused"]).validate(request)
    assert request["position_id"] == "TestExecutorWorker"
    assert request["capability_grant"]["allowed_tools"] == [
        "omni_workspace_read",
        "omni_workspace_list",
        "omni_workspace_search",
    ]
    assert "dispatch" not in " ".join(request["capability_grant"]["allowed_tools"])
    assert request["input"]["expected_output_schema"] == TEST_EVALUATION_OUTPUT_SCHEMA
    assert verdict.details["test_executor_control_plane"] == {
        "target_team_id": "fixed-target",
        "run_count": 1,
        "aggregate_verdict": "PASS",
        "sample_input_inferred": False,
        "sample_runtime": None,
    }


@pytest.mark.asyncio
async def test_test_executor_infers_missing_input_before_omni_dispatch(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "target" / "formats.py"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("# schema\n", encoding="utf-8")
    invoker = _TestExecutorInvoker(evidence)
    dispatch_calls: list[tuple[str, dict[str, Any]]] = []

    async def dispatch_target(target: str, payload: dict[str, Any]) -> Verdict:
        dispatch_calls.append((target, payload))
        return Verdict(kind=VerdictKind.PASS, output={"result": "fixture"})

    binding = build_bindings(
        agent_runtime_invoker=invoker,
        workspace_cwd=tmp_path,
        team_dispatcher=dispatch_target,
    )["TestExecutorWorker"]
    verdict = await binding.run(
        {
            "_from_TargetIngressWorker": {
                "target_team_id": "fixed-target",
                "team_code_dir": str(evidence.parent),
                "sample_input": None,
                "run_count": 1,
            },
            "_from_HypothesisGeneratorWorker": {
                "hypotheses": [{"id": "H-001", "condition": "When target runs."}]
            },
        }
    )

    assert verdict.kind == VerdictKind.PASS
    assert dispatch_calls == [("fixed-target", {"request": "inferred fixture"})]
    assert [request["position_id"] for request in invoker.requests] == [
        "TestExecutorWorker:SampleInput",
        "TestExecutorWorker",
    ]
    AgentRuntimeProcessClient(["unused"]).validate(invoker.requests[0])
    assert (
        invoker.requests[0]["input"]["expected_output_schema"]
        == SAMPLE_INPUT_OUTPUT_SCHEMA
    )
    assert verdict.details["test_executor_control_plane"]["sample_input_inferred"] is True


@pytest.mark.asyncio
async def test_test_executor_default_dispatcher_calls_authoritative_core_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = tmp_path / "target" / "team.py"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("# target\n", encoding="utf-8")
    invoker = _TestExecutorInvoker(evidence)
    dispatch_calls: list[tuple[str, dict[str, Any], int | None]] = []

    async def core_dispatch(
        target: str,
        payload: dict[str, Any],
        *,
        db_path: str | None = None,
        max_steps: int | None = None,
    ) -> Verdict:
        dispatch_calls.append((target, payload, max_steps))
        return Verdict(kind=VerdictKind.PASS, output={"result": "core fixture"})

    monkeypatch.setattr("omnicompany.core.dispatch.dispatch", core_dispatch)
    binding = build_bindings(
        agent_runtime_invoker=invoker,
        workspace_cwd=tmp_path,
    )["TestExecutorWorker"]
    verdict = await binding.run(
        {
            "_from_TargetIngressWorker": {
                "target_team_id": "registry-fixed-target",
                "sample_input": {"request": "fixture"},
                "run_count": 1,
            },
            "_from_HypothesisGeneratorWorker": {
                "hypotheses": [{"id": "H-001", "condition": "When target runs."}]
            },
        }
    )

    assert verdict.kind == VerdictKind.PASS
    assert dispatch_calls == [
        ("registry-fixed-target", {"request": "fixture"}, None)
    ]


@pytest.mark.asyncio
async def test_exact_production_position_uses_external_contract_without_fallback(
    tmp_path: Path,
) -> None:
    invoker = _StructuredInvoker()
    bindings = build_bindings(
        agent_runtime_invoker=invoker,
        workspace_cwd=tmp_path,
    )
    binding = bindings["HealthCriteriaDesignerWorker"]

    assert isinstance(binding, AgentRuntimeNodeRouter)
    verdict = await binding.run(
        {
            "_from_ProductFormAnalyzerWorker": {
                "essence": "A health report with traceable evidence.",
                "failure_signals": ["missing evidence"],
            },
            "_from_PurposeInterpreterWorker": {
                "essence": "Help a maintainer decide whether the Team is healthy.",
                "non_goals": ["repair the target Team"],
            },
        }
    )

    assert verdict.kind == VerdictKind.PASS
    assert verdict.output is not None
    assert len(verdict.output["oracle_strategies"]) == 3
    assert len(invoker.requests) == 1
    request = invoker.requests[0]
    AgentRuntimeProcessClient(["unused"]).validate(request)
    assert request["team_id"] == "team_supervisor"
    assert request["position_id"] == "HealthCriteriaDesignerWorker"
    assert request["capability_grant"]["allowed_tools"] == []
    assert request["capability_grant"]["network_access"] is False
    assert request["input"]["expected_output_schema"]["required"] == [
        "key_observations",
        "red_flags",
        "oracle_strategies",
    ]


@pytest.mark.asyncio
async def test_default_external_failure_is_explicit_without_legacy_fallback(
    tmp_path: Path,
) -> None:
    invoker = _FailedInvoker()
    binding = build_bindings(
        agent_runtime_invoker=invoker,
        workspace_cwd=tmp_path,
    )["HealthCriteriaDesignerWorker"]

    verdict = await binding.run({"input": "health criteria"})

    assert verdict.kind == VerdictKind.FAIL
    assert verdict.diagnosis == "external failed"
    assert verdict.details["runtime_error_code"] == "provider_error"
    assert len(invoker.requests) == 1
