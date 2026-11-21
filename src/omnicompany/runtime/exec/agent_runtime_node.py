"""Single TeamRunner binding that delegates an Agent node through AgentRuntime.

TeamSpec and TeamRunner stay authoritative.  This Router is the only adapter:
it converts a Team node input into the language-neutral protocol and converts
the bounded child result back into a Verdict.  It contains no DSH/Cordis type.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Protocol
import uuid

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router


RuntimeMessage = dict[str, Any]
CapabilityGrantFactory = Callable[[Any], Mapping[str, Any]]
ReferenceFactory = Callable[[Any], Sequence[str]]
ParentRunFactory = Callable[[Any], str | None]
TaskIdFactory = Callable[[Any], str | None]
OutputValidator = Callable[[Any, Any], str | None]


class AgentRuntimeInvoker(Protocol):
    async def run(
        self,
        request: RuntimeMessage,
        event_sink: Callable[[RuntimeMessage], None | Awaitable[None]] | None = None,
    ) -> RuntimeMessage: ...


def _no_refs(_input: Any) -> Sequence[str]:
    return ()


def _no_parent(_input: Any) -> str | None:
    return None


@dataclass(frozen=True, slots=True)
class AgentRuntimeNodePolicy:
    provider: str
    model: str
    workspace_cwd: str | Path
    capability_grant: CapabilityGrantFactory
    budget: Mapping[str, int]
    team_id: str
    position_id: str
    instructions: str
    expected_output_schema: Mapping[str, Any] | None = None
    material_refs: ReferenceFactory = _no_refs
    artifact_refs: ReferenceFactory = _no_refs
    task_id: TaskIdFactory = _no_parent
    parent_run_id: ParentRunFactory = _no_parent
    context_refs: tuple[str, ...] = ()
    inline_context: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    output_validator: OutputValidator | None = None
    max_validation_attempts: int = 1

    def __post_init__(self) -> None:
        required = (
            self.provider,
            self.model,
            self.team_id,
            self.position_id,
            self.instructions,
        )
        if any(not value.strip() for value in required):
            raise ValueError("AgentRuntime node policy identity cannot be blank")
        if not self.budget:
            raise ValueError("AgentRuntime node policy requires an explicit budget")
        if self.max_validation_attempts < 1 or self.max_validation_attempts > 3:
            raise ValueError("AgentRuntime validation attempts must be between 1 and 3")


class AgentRuntimeNodeRouter(Router):
    """Vendor-neutral AgentRuntime binding for one declared Team node."""

    def __init__(
        self,
        invoker: AgentRuntimeInvoker,
        policy: AgentRuntimeNodePolicy,
        *,
        description: str,
        format_in: str | list[str],
        format_out: str,
    ) -> None:
        self._invoker = invoker
        self._policy = policy
        self.DESCRIPTION = description
        self.FORMAT_IN = format_in
        self.FORMAT_OUT = format_out

    async def run(self, input_data: Any) -> Verdict:
        run_id = f"run-{uuid.uuid4().hex}"
        trace_id = str(getattr(self, "_trace_id", run_id))
        material_refs = list(dict.fromkeys(self._policy.material_refs(input_data)))
        artifact_refs = list(dict.fromkeys(self._policy.artifact_refs(input_data)))
        request: RuntimeMessage = {
            "kind": "run.start",
            "protocol_version": "0.1.0",
            "request_id": f"req-{uuid.uuid4().hex}",
            "run_id": run_id,
            "session_id": f"team-{self._policy.team_id}-{self._policy.position_id}-{trace_id}",
            "team_id": self._policy.team_id,
            "position_id": self._policy.position_id,
            "provider": self._policy.provider,
            "model": self._policy.model,
            "workspace_cwd": str(Path(self._policy.workspace_cwd).resolve()),
            "input": {
                "instructions": self._policy.instructions,
                "text": json.dumps(input_data, ensure_ascii=False, default=str),
                "material_refs": material_refs,
                "artifact_refs": artifact_refs,
            },
            "context": {
                "context_refs": list(self._policy.context_refs),
                "material_refs": material_refs,
                "artifact_refs": artifact_refs,
                "inline_context": list(self._policy.inline_context),
                "provenance": list(self._policy.provenance),
            },
            "capability_grant": dict(self._policy.capability_grant(input_data)),
            "budget": dict(self._policy.budget),
            "metadata": {
                **dict(self._policy.metadata),
                "source": "teamrunner.agent-runtime-node",
                "team_trace_id": trace_id,
                "format_in": self.FORMAT_IN,
                "format_out": self.FORMAT_OUT,
            },
        }
        if self._policy.expected_output_schema is not None:
            request["input"]["expected_output_schema"] = dict(
                self._policy.expected_output_schema
            )
        parent_run_id = self._policy.parent_run_id(input_data)
        task_id = self._policy.task_id(input_data)
        if task_id:
            request["task_id"] = task_id
        if parent_run_id:
            request["parent_run_id"] = parent_run_id
        for attempt in range(1, self._policy.max_validation_attempts + 1):
            result = await self._invoker.run(request)
            verdict = self._verdict(result)
            rejection: str | None = None
            if verdict.kind == VerdictKind.PASS and self._policy.output_validator is not None:
                try:
                    rejection = self._policy.output_validator(
                        verdict.output,
                        input_data,
                    )
                except Exception as error:  # noqa: BLE001 - validator is a trust boundary
                    rejection = (
                        f"output validator crashed: {type(error).__name__}: {error}"
                    )
                if rejection:
                    verdict = Verdict(
                        kind=VerdictKind.FAIL,
                        output=verdict.output,
                        diagnosis=f"AgentRuntime output rejected: {rejection}",
                        details=verdict.details,
                    )
            elif verdict.details.get("runtime_error_code") == "invalid_output":
                rejection = verdict.diagnosis or "runtime rejected structured output"

            if not rejection or attempt == self._policy.max_validation_attempts:
                return verdict
            request = self._validation_retry_request(
                request,
                input_data=input_data,
                rejection=rejection,
                attempt=attempt + 1,
            )
        raise AssertionError("unreachable AgentRuntime validation loop")

    def _validation_retry_request(
        self,
        request: RuntimeMessage,
        *,
        input_data: Any,
        rejection: str,
        attempt: int,
    ) -> RuntimeMessage:
        retry = dict(request)
        retry["request_id"] = f"req-{uuid.uuid4().hex}"
        retry["run_id"] = f"run-{uuid.uuid4().hex}"
        retry["session_id"] = (
            f"team-{self._policy.team_id}-{self._policy.position_id}-"
            f"validation-retry-{uuid.uuid4().hex}"
        )
        retry["capability_grant"] = dict(self._policy.capability_grant(input_data))
        retry_input = dict(request["input"])
        retry_input["instructions"] = (
            f"{self._policy.instructions}\n\n"
            "The previous external-runtime attempt was rejected by the Omni-owned "
            f"output contract: {rejection}. Correct that specific defect. Return "
            "exactly one schema-valid JSON value and no prose or Markdown fences."
        )
        retry["input"] = retry_input
        retry["metadata"] = {
            **dict(request.get("metadata") or {}),
            "validation_attempt": attempt,
        }
        return retry

    @staticmethod
    def _verdict(result: Mapping[str, Any]) -> Verdict:
        status = result.get("status")
        details = {
            "agent_runtime": {
                "run_id": result.get("run_id"),
                "session_id": result.get("session_id"),
                "status": status,
                "usage": result.get("usage", {}),
                "material_refs": result.get("material_refs", []),
                "artifact_refs": result.get("artifact_refs", []),
                "checkpoint_ref": result.get("checkpoint_ref"),
                "metadata": result.get("metadata", {}),
            }
        }
        if status == "succeeded":
            final_output = result.get("final_output")
            if not isinstance(final_output, Mapping):
                return Verdict(
                    kind=VerdictKind.FAIL,
                    diagnosis="AgentRuntime succeeded without a valid final_output",
                    details=details,
                )
            output = final_output.get("structured")
            if output is None:
                output = {"text": str(final_output.get("text", ""))}
            return Verdict(
                kind=VerdictKind.PASS,
                output=output,
                diagnosis=str(final_output.get("text", ""))[:500] or None,
                details=details,
            )
        error = result.get("error")
        message = error.get("message") if isinstance(error, Mapping) else None
        code = error.get("code") if isinstance(error, Mapping) else status
        return Verdict(
            kind=VerdictKind.FAIL,
            diagnosis=str(message or f"AgentRuntime node ended with {status}"),
            details={**details, "runtime_error_code": code},
        )

    def summarize_output(self, verdict: Verdict) -> str:
        runtime = verdict.details.get("agent_runtime", {})
        return f"AgentRuntime {runtime.get('status', verdict.kind.value)}"


__all__ = [
    "AgentRuntimeInvoker",
    "AgentRuntimeNodePolicy",
    "AgentRuntimeNodeRouter",
    "OutputValidator",
]
