# [OMNI] origin=claude-code domain=services/team_supervisor/run ts=2026-04-26T00:00:00Z type=config
# [OMNI] material_id="material:core.team_supervisor.worker_bindings.builder.py"
"""team_supervisor Team · build_bindings."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from typing import Any
import uuid

from omnicompany.core.config import omni_workspace_root
from omnicompany.protocol.format import create_builtin_registry
from omnicompany.runtime.agent_runtime.profiles import build_the_company_dsh_readonly_invoker
from omnicompany.runtime.agent_runtime.structured_runner import READONLY_WORKSPACE_TOOLS
from omnicompany.runtime.exec.agent_runtime_node import (
    AgentRuntimeInvoker,
    AgentRuntimeNodePolicy,
    AgentRuntimeNodeRouter,
    OutputValidator,
)
from omnicompany.runtime.routing.router import Router

from .formats import register_formats

from .workers.target_ingress import TargetIngressWorker
from .workers.product_form_analyzer import (
    PRODUCT_FORM_DESCRIPTION,
    PRODUCT_FORM_FORMAT_IN,
    PRODUCT_FORM_FORMAT_OUT,
    PRODUCT_FORM_OUTPUT_SCHEMA,
    PRODUCT_FORM_RUNTIME_INSTRUCTIONS,
)
from .workers.purpose_interpreter import (
    PURPOSE_DESCRIPTION,
    PURPOSE_FORMAT_IN,
    PURPOSE_FORMAT_OUT,
    PURPOSE_OUTPUT_SCHEMA,
    PURPOSE_RUNTIME_INSTRUCTIONS,
)
from .workers.health_criteria_designer import (
    HEALTH_CRITERIA_DESCRIPTION,
    HEALTH_CRITERIA_FORMAT_IN,
    HEALTH_CRITERIA_FORMAT_OUT,
    HEALTH_CRITERIA_OUTPUT_SCHEMA,
    HEALTH_CRITERIA_RUNTIME_INSTRUCTIONS,
)
from .workers.hypothesis_generator import (
    HYPOTHESIS_DESCRIPTION,
    HYPOTHESIS_FORMAT_IN,
    HYPOTHESIS_FORMAT_OUT,
    HYPOTHESIS_OUTPUT_SCHEMA,
    HYPOTHESIS_RUNTIME_INSTRUCTIONS,
)
from .workers.test_executor import (
    SAMPLE_INPUT_OUTPUT_SCHEMA,
    SAMPLE_INPUT_RUNTIME_INSTRUCTIONS,
    TEST_EVALUATION_OUTPUT_SCHEMA,
    TEST_EVALUATION_RUNTIME_INSTRUCTIONS,
    TEST_EXECUTOR_DESCRIPTION,
    TEST_EXECUTOR_FORMAT_IN,
    TEST_EXECUTOR_FORMAT_OUT,
    TeamDispatcher,
    TestExecutorRuntimeRouter,
    dispatch_registered_team,
)
from .workers.health_report_assembler import HealthReportAssemblerWorker


_TEAM_ID = "team_supervisor"


@dataclass(frozen=True, slots=True)
class _ReadonlyPositionContract:
    position_id: str
    description: str
    format_in: str | list[str]
    format_out: str
    instructions: str
    output_schema: dict[str, Any]
    allowed_tools: tuple[str, ...]
    max_steps: int
    max_output_tokens: int
    evidence_path_field: tuple[str, str] | None = None
    unique_item_field: tuple[str, str] | None = None
    max_validation_attempts: int = 1


_HEALTH_CRITERIA = _ReadonlyPositionContract(
    position_id="HealthCriteriaDesignerWorker",
    description=HEALTH_CRITERIA_DESCRIPTION,
    format_in=HEALTH_CRITERIA_FORMAT_IN,
    format_out=HEALTH_CRITERIA_FORMAT_OUT,
    instructions=HEALTH_CRITERIA_RUNTIME_INSTRUCTIONS,
    output_schema=HEALTH_CRITERIA_OUTPUT_SCHEMA,
    allowed_tools=(),
    max_steps=2,
    max_output_tokens=2048,
)
_PURPOSE = _ReadonlyPositionContract(
    position_id="PurposeInterpreterWorker",
    description=PURPOSE_DESCRIPTION,
    format_in=PURPOSE_FORMAT_IN,
    format_out=PURPOSE_FORMAT_OUT,
    instructions=PURPOSE_RUNTIME_INSTRUCTIONS,
    output_schema=PURPOSE_OUTPUT_SCHEMA,
    allowed_tools=READONLY_WORKSPACE_TOOLS,
    max_steps=12,
    max_output_tokens=4096,
    evidence_path_field=("evidence_sources", "file"),
    max_validation_attempts=2,
)
_PRODUCT_FORM = _ReadonlyPositionContract(
    position_id="ProductFormAnalyzerWorker",
    description=PRODUCT_FORM_DESCRIPTION,
    format_in=PRODUCT_FORM_FORMAT_IN,
    format_out=PRODUCT_FORM_FORMAT_OUT,
    instructions=PRODUCT_FORM_RUNTIME_INSTRUCTIONS,
    output_schema=PRODUCT_FORM_OUTPUT_SCHEMA,
    allowed_tools=READONLY_WORKSPACE_TOOLS,
    max_steps=12,
    max_output_tokens=4096,
    evidence_path_field=("concrete_examples", "path"),
    max_validation_attempts=2,
)
_HYPOTHESIS = _ReadonlyPositionContract(
    position_id="HypothesisGeneratorWorker",
    description=HYPOTHESIS_DESCRIPTION,
    format_in=HYPOTHESIS_FORMAT_IN,
    format_out=HYPOTHESIS_FORMAT_OUT,
    instructions=HYPOTHESIS_RUNTIME_INSTRUCTIONS,
    output_schema=HYPOTHESIS_OUTPUT_SCHEMA,
    allowed_tools=(),
    max_steps=4,
    max_output_tokens=8192,
    unique_item_field=("hypotheses", "id"),
    max_validation_attempts=2,
)
_TEST_SAMPLE_INPUT = _ReadonlyPositionContract(
    position_id="TestExecutorWorker:SampleInput",
    description="Infer a missing sample input for the fixed target Team.",
    format_in="team_supervisor.target_metadata",
    format_out="team_supervisor.sample_input_candidate",
    instructions=SAMPLE_INPUT_RUNTIME_INSTRUCTIONS,
    output_schema=SAMPLE_INPUT_OUTPUT_SCHEMA,
    allowed_tools=READONLY_WORKSPACE_TOOLS,
    max_steps=10,
    max_output_tokens=4096,
    max_validation_attempts=2,
)
_TEST_EVALUATION = _ReadonlyPositionContract(
    position_id="TestExecutorWorker",
    description=TEST_EXECUTOR_DESCRIPTION,
    format_in=TEST_EXECUTOR_FORMAT_IN,
    format_out=TEST_EXECUTOR_FORMAT_OUT,
    instructions=TEST_EVALUATION_RUNTIME_INSTRUCTIONS,
    output_schema=TEST_EVALUATION_OUTPUT_SCHEMA,
    allowed_tools=READONLY_WORKSPACE_TOOLS,
    max_steps=12,
    max_output_tokens=8192,
    max_validation_attempts=2,
)


def _readonly_grant(
    workspace: Path,
    *,
    allowed_tools: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "grant_id": f"grant:team-supervisor-readonly:{uuid.uuid4().hex}",
        "policy_ref": "policy:agent-runtime-readonly-workspace",
        "permission_mode": "readonly",
        "allowed_tools": list(allowed_tools),
        "allowed_material_refs": [],
        "allowed_read_roots": [str(workspace)],
        "allowed_write_paths": [],
        "allowed_write_roots": [],
        "allowed_shell_roots": [],
        "allowed_shell_command_prefixes": [],
        "network_access": False,
        "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    }


def _workspace_evidence_validator(
    workspace: Path,
    field: tuple[str, str] | None,
) -> OutputValidator | None:
    if field is None:
        return None
    collection_key, path_key = field

    def validate(output: Any, _input: Any) -> str | None:
        if not isinstance(output, dict):
            return "structured output is not an object"
        values = output.get(collection_key, [])
        if not isinstance(values, list):
            return f"{collection_key} is not a list"
        for index, item in enumerate(values):
            if not isinstance(item, dict) or not str(item.get(path_key) or "").strip():
                return f"{collection_key}[{index}].{path_key} is blank"
            candidate = Path(str(item[path_key]))
            resolved = (
                candidate.resolve()
                if candidate.is_absolute()
                else (workspace / candidate).resolve()
            )
            try:
                resolved.relative_to(workspace)
            except ValueError:
                return f"{collection_key}[{index}] escapes the granted workspace"
            if not resolved.is_file():
                return f"{collection_key}[{index}] does not resolve to an existing file"
        return None

    return validate


def _existing_workspace_file(workspace: Path, value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return "path is blank"
    candidate = Path(raw)
    resolved = candidate.resolve() if candidate.is_absolute() else (workspace / candidate).resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError:
        return "path escapes the granted workspace"
    if not resolved.is_file():
        return "path does not resolve to an existing file"
    return None


def _sample_input_validator(workspace: Path) -> OutputValidator:
    def validate(output: Any, _input: Any) -> str | None:
        if not isinstance(output, dict) or not isinstance(output.get("sample_input"), dict):
            return "sample_input is not an object"
        paths = output.get("evidence_paths")
        if not isinstance(paths, list) or not paths:
            return "evidence_paths is empty"
        for index, value in enumerate(paths):
            error = _existing_workspace_file(workspace, value)
            if error:
                return f"evidence_paths[{index}] {error}"
        return None

    return validate


def _test_evaluation_validator(workspace: Path) -> OutputValidator:
    def validate(output: Any, input_data: Any) -> str | None:
        if not isinstance(output, dict) or not isinstance(input_data, dict):
            return "evaluation payload is not an object"
        expected_verdict = input_data.get("control_plane_verdict")
        if output.get("target_run_verdict") != expected_verdict:
            return "target_run_verdict does not match the Omni control-plane verdict"

        hypotheses = input_data.get("hypotheses")
        evaluations = output.get("hypothesis_evaluations")
        if not isinstance(hypotheses, list) or not isinstance(evaluations, list):
            return "hypotheses or hypothesis_evaluations is not a list"
        expected_ids = [
            item.get("id") if isinstance(item, dict) else None for item in hypotheses
        ]
        actual_ids = [
            item.get("hypothesis_id") if isinstance(item, dict) else None
            for item in evaluations
        ]
        if len(actual_ids) != len(set(actual_ids)):
            return "hypothesis_evaluations contains duplicate hypothesis_id values"
        if actual_ids != expected_ids:
            return "hypothesis_evaluations must cover every hypothesis once and in order"

        for evaluation_index, evaluation in enumerate(evaluations):
            evidence = evaluation.get("evidence", []) if isinstance(evaluation, dict) else []
            if not isinstance(evidence, list):
                return f"hypothesis_evaluations[{evaluation_index}].evidence is not a list"
            for evidence_index, item in enumerate(evidence):
                path = item.get("path") if isinstance(item, dict) else None
                error = _existing_workspace_file(workspace, path)
                if error:
                    return (
                        f"hypothesis_evaluations[{evaluation_index}]."
                        f"evidence[{evidence_index}].path {error}"
                    )
        return None

    return validate


def _position_output_validator(
    workspace: Path,
    contract: _ReadonlyPositionContract,
) -> OutputValidator | None:
    evidence_validator = _workspace_evidence_validator(
        workspace,
        contract.evidence_path_field,
    )
    if evidence_validator is None and contract.unique_item_field is None:
        return None

    def validate(output: Any, input_data: Any) -> str | None:
        if evidence_validator is not None:
            error = evidence_validator(output, input_data)
            if error:
                return error
        if contract.unique_item_field is not None:
            collection_key, item_key = contract.unique_item_field
            if not isinstance(output, dict):
                return "structured output is not an object"
            values = output.get(collection_key, [])
            if not isinstance(values, list):
                return f"{collection_key} is not a list"
            identities = [
                item.get(item_key) if isinstance(item, dict) else None
                for item in values
            ]
            if len(set(identities)) != len(identities):
                return f"{collection_key} contains duplicate {item_key} values"
        return None

    return validate


def _readonly_position_binding(
    contract: _ReadonlyPositionContract,
    *,
    agent_runtime_invoker: AgentRuntimeInvoker | None,
    workspace_cwd: Path,
    output_validator: OutputValidator | None = None,
) -> Router:
    invoker = agent_runtime_invoker or build_the_company_dsh_readonly_invoker(
        workspace_cwd,
        allowed_tools=contract.allowed_tools,
    )
    return AgentRuntimeNodeRouter(
        invoker,
        AgentRuntimeNodePolicy(
            provider="the_company",
            model="deepseek-v4-flash",
            workspace_cwd=workspace_cwd,
            capability_grant=lambda _input: _readonly_grant(
                workspace_cwd,
                allowed_tools=contract.allowed_tools,
            ),
            budget={
                "max_steps": contract.max_steps,
                "max_output_tokens": contract.max_output_tokens,
            },
            team_id=_TEAM_ID,
            position_id=contract.position_id,
            instructions=contract.instructions,
            expected_output_schema=contract.output_schema,
            provenance=(
                "plan:_cross/[2026-08-14]AGENT-RUNTIME-EXTERNALIZATION",
            ),
            metadata={"rollout": "production-position-default"},
            output_validator=(
                output_validator
                if output_validator is not None
                else _position_output_validator(workspace_cwd, contract)
            ),
            max_validation_attempts=contract.max_validation_attempts,
        ),
        description=contract.description,
        format_in=contract.format_in,
        format_out=contract.format_out,
    )


def _test_executor_binding(
    *,
    agent_runtime_invoker: AgentRuntimeInvoker | None,
    workspace_cwd: Path,
    team_dispatcher: TeamDispatcher | None,
) -> Router:
    invoker = agent_runtime_invoker or build_the_company_dsh_readonly_invoker(
        workspace_cwd,
        allowed_tools=READONLY_WORKSPACE_TOOLS,
    )
    sample_agent = _readonly_position_binding(
        _TEST_SAMPLE_INPUT,
        agent_runtime_invoker=invoker,
        workspace_cwd=workspace_cwd,
        output_validator=_sample_input_validator(workspace_cwd),
    )
    evaluation_agent = _readonly_position_binding(
        _TEST_EVALUATION,
        agent_runtime_invoker=invoker,
        workspace_cwd=workspace_cwd,
        output_validator=_test_evaluation_validator(workspace_cwd),
    )
    return TestExecutorRuntimeRouter(
        sample_input_agent=sample_agent,
        evaluation_agent=evaluation_agent,
        team_dispatcher=team_dispatcher or dispatch_registered_team,
    )


def build_bindings(
    input_dict: dict | None = None,
    *,
    agent_runtime_invoker: AgentRuntimeInvoker | None = None,
    workspace_cwd: str | os.PathLike[str] | None = None,
    team_dispatcher: TeamDispatcher | None = None,
) -> dict[str, Router]:
    """构建 team_supervisor 节点绑定."""
    registry = create_builtin_registry()
    register_formats(registry)
    workspace = Path(workspace_cwd or omni_workspace_root()).resolve()
    return {
        "TargetIngressWorker": TargetIngressWorker(),
        "ProductFormAnalyzerWorker": _readonly_position_binding(
            _PRODUCT_FORM,
            agent_runtime_invoker=agent_runtime_invoker,
            workspace_cwd=workspace,
        ),
        "PurposeInterpreterWorker": _readonly_position_binding(
            _PURPOSE,
            agent_runtime_invoker=agent_runtime_invoker,
            workspace_cwd=workspace,
        ),
        "HealthCriteriaDesignerWorker": _readonly_position_binding(
            _HEALTH_CRITERIA,
            agent_runtime_invoker=agent_runtime_invoker,
            workspace_cwd=workspace,
        ),
        "HypothesisGeneratorWorker": _readonly_position_binding(
            _HYPOTHESIS,
            agent_runtime_invoker=agent_runtime_invoker,
            workspace_cwd=workspace,
        ),
        "TestExecutorWorker": _test_executor_binding(
            agent_runtime_invoker=agent_runtime_invoker,
            workspace_cwd=workspace,
            team_dispatcher=team_dispatcher,
        ),
        "HealthReportAssemblerWorker": HealthReportAssemblerWorker(),
    }
