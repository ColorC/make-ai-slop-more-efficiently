# [OMNI] origin=codex domain=services/agent ts=2026-05-11 type=infrastructure
"""TeamRunner workflow node for external agent workers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.agent.external_workers.base import (
    ExternalAgentPermissionMode,
    ExternalAgentStatus,
    ExternalAgentWorkerRegistry,
)
from omnicompany.packages.services._core.agent.external_workers.runner import (
    ExternalAgentModelPolicy,
    ExternalAgentRunRequest,
    run_external_agent_request,
)
from omnicompany.packages.services._core.agent.external_workers.runtime_contract import (
    AgentCapabilityGrant,
    AgentContextEnvelope,
    AgentRuntimeProfile,
)
from omnicompany.packages.services._core.agent.external_workers.runtime_profiles import (
    AgentRuntimeRequest,
    run_agent_runtime_request,
    select_agent_runtime,
)
from omnicompany.packages.services._core.agent.spawn_surface import (
    ENTRY_TEAMRUNNER_NODE,
    ensure_agent_spawn_metadata,
)
from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


class ExternalAgentWorkerNode(Worker):
    """Run the canonical native profile or a compatibility harness as a Team node.

    This node is the DAG-level integration point. It does not depend on the
    Agent tool or dashboard API: a TeamSpec binding can instantiate this Router
    directly and place it in a workflow. It remains an adapter over
    ExternalAgentRunRequest, not a new provider launch surface.
    """

    DESCRIPTION = (
        "Run the audited Omni EventBus Agent or a compatibility harness from "
        "a TeamRunner workflow node. Defaults to readonly and returns normalized "
        "harness/model/run metadata plus the final text."
    )
    FORMAT_IN = "external_agent.request"
    FORMAT_OUT = "external_agent.result"

    def __init__(
        self,
        *,
        provider: str | None = None,
        cwd: Path | str | None = None,
        permission_mode: ExternalAgentPermissionMode | str = ExternalAgentPermissionMode.READONLY,
        model: str | None = None,
        model_policy: ExternalAgentModelPolicy = "cheap",
        profile: str | None = None,
        runtime_profile: AgentRuntimeProfile | str | None = None,
        timeout_s: float = 600.0,
        attached_context: list[str] | None = None,
        worker_registry: ExternalAgentWorkerRegistry | None = None,
        allow_trusted_bypass: bool = False,
    ):
        self.provider = provider
        self.cwd = Path(cwd).expanduser().resolve() if cwd is not None else None
        self.permission_mode = permission_mode
        self.model = model
        self.model_policy = model_policy
        self.profile = profile
        self.runtime_profile = runtime_profile
        self.timeout_s = timeout_s
        self.attached_context = list(attached_context or [])
        self.worker_registry = worker_registry
        self.allow_trusted_bypass = allow_trusted_bypass

    async def run(self, input_data: Any) -> Verdict:
        data = input_data if isinstance(input_data, dict) else {"prompt": str(input_data)}
        provider = str(data.get("provider") or self.provider or "").strip()
        runtime_profile = data.get("runtime_profile") or self.runtime_profile
        selection = None
        if runtime_profile is not None:
            try:
                selection = select_agent_runtime(
                    runtime_profile,
                    requested_harness=provider,
                )
            except ValueError as exc:
                return self._fail(data, str(exc))
            provider = selection.harness_id
        prompt = str(data.get("prompt") or data.get("task") or "").strip()
        permission_mode = data.get("permission_mode") or self.permission_mode

        if not provider:
            return self._fail(data, "external agent provider is required")
        if not prompt:
            return self._fail(data, "external agent prompt is required")

        try:
            normalized_permission = (
                permission_mode
                if isinstance(permission_mode, ExternalAgentPermissionMode)
                else ExternalAgentPermissionMode(str(permission_mode))
            )
        except ValueError:
            return self._fail(
                data,
                "permission_mode must be one of: readonly, workspace-write, trusted-bypass",
            )

        allow_bypass = bool(data.get("allow_trusted_bypass") or self.allow_trusted_bypass)
        if normalized_permission == ExternalAgentPermissionMode.TRUSTED_BYPASS and not allow_bypass:
            return self._fail(data, "trusted-bypass requires allow_trusted_bypass=true")

        try:
            cwd = self._resolve_cwd(data.get("cwd"))
        except ValueError as exc:
            return self._fail(data, str(exc))

        attached_context = list(self.attached_context)
        extra_context = data.get("attached_context") or []
        if not isinstance(extra_context, list) or not all(isinstance(item, str) for item in extra_context):
            return self._fail(data, "attached_context must be a list of strings")
        attached_context.extend(extra_context)
        context_envelope = AgentContextEnvelope.from_legacy(
            attached_context,
            provenance=("teamrunner:external-agent-node",),
        )
        capability_grant = None
        raw_grant = data.get("capability_grant")
        if raw_grant is not None:
            try:
                capability_grant = _parse_capability_grant(raw_grant)
                capability_grant.validate(cwd)
            except (TypeError, ValueError) as exc:
                return self._fail(data, f"invalid capability_grant: {exc}")
        elif provider == "omni-native":
            if normalized_permission == ExternalAgentPermissionMode.TRUSTED_BYPASS:
                return self._fail(
                    data,
                    f"{provider} canonical runtime does not support trusted-bypass",
                )
            capability_grant = AgentCapabilityGrant.for_workspace(
                cwd,
                permission_mode=normalized_permission.value,
                grant_id=f"grant:{data.get('trace_id') or provider}",
                policy_ref=str(
                    data.get("capability_policy_ref")
                    or "policy:teamrunner-external-agent"
                ),
                allowed_shell_command_prefixes=(
                    data.get("allowed_bash_command_prefixes") or ()
                ),
            )

        trace_id = str(getattr(self, "_trace_id", "") or data.get("trace_id") or "")
        metadata = ensure_agent_spawn_metadata(
            ENTRY_TEAMRUNNER_NODE,
            dict(data.get("metadata") or {}),
            entrypoint="teamrunner_workflow_node",
            node_id=str(getattr(self, "_node_id", "") or ""),
        )
        if selection is not None:
            runtime_request = AgentRuntimeRequest(
                prompt=prompt,
                cwd=cwd,
                runtime_profile=selection.profile,
                harness_id=selection.harness_id,
                permission_mode=normalized_permission,
                model_provider=str(data.get("model_provider") or ""),
                model=data.get("model") or self.model,
                model_policy=data.get("model_policy") or self.model_policy,
                provider_profile=data.get("profile") or self.profile,
                timeout_s=float(data.get("timeout_s") or self.timeout_s),
                context_envelope=context_envelope,
                capability_grant=capability_grant,
                output_schema_path=data.get("output_schema_path"),
                env=dict(data.get("env") or {}),
                watch_paths=list(data.get("watch_paths") or []),
                trace_id=trace_id,
                metadata=metadata,
            )
            result = await run_agent_runtime_request(
                runtime_request,
                bus=getattr(self, "_bus", None),
                worker_registry=self.worker_registry,
            )
        else:
            request = ExternalAgentRunRequest(
                provider=provider,
                harness_id=provider,
                model_provider=str(data.get("model_provider") or ""),
                prompt=prompt,
                cwd=cwd,
                permission_mode=normalized_permission,
                model=data.get("model") or self.model,
                model_policy=data.get("model_policy") or self.model_policy,
                profile=data.get("profile") or self.profile,
                timeout_s=float(data.get("timeout_s") or self.timeout_s),
                attached_context=(
                    []
                    if provider == "omni-native"
                    else attached_context
                ),
                context_envelope=(
                    context_envelope
                    if provider == "omni-native"
                    else None
                ),
                capability_grant=capability_grant,
                output_schema_path=data.get("output_schema_path"),
                env=dict(data.get("env") or {}),
                watch_paths=list(data.get("watch_paths") or []),
                trace_id=trace_id,
                metadata=metadata,
            )
            result = await run_external_agent_request(
                request,
                bus=getattr(self, "_bus", None),
                worker_registry=self.worker_registry,
            )
        status = result.normalized_status()
        output = {
            **data,
            "text": result.final_text,
            "external_agent": {
                "run_id": result.run_id,
                "provider": result.provider,
                "harness_id": result.raw.get("harness_id") or result.provider,
                "model_provider": result.raw.get("model_provider"),
                "runtime_profile": result.raw.get("runtime_profile"),
                "status": status.value,
                "final_text": result.final_text,
                "structured_output": result.structured_output,
                "exit_code": result.exit_code,
                "changed_files": result.changed_files,
                "diff_summary": result.diff_summary,
                "error": result.error,
                "duration_ms": result.duration_ms,
                "event_count": len(result.events or []),
                "raw": result.raw,
            },
        }
        kind = _verdict_kind_for_status(status)
        diagnosis = "external agent completed"
        if kind != VerdictKind.PASS:
            diagnosis = result.error or f"external agent {provider!r} ended with {status.value}"
        return Verdict(kind=kind, output=output, diagnosis=diagnosis)

    def _resolve_cwd(self, value: Any) -> Path:
        cwd = Path(value).expanduser() if value else self.cwd
        if cwd is None:
            cwd = Path.cwd()
        cwd = cwd.resolve()
        if not cwd.exists() or not cwd.is_dir():
            raise ValueError(f"cwd must be an existing directory: {cwd}")
        return cwd

    @staticmethod
    def _fail(input_data: dict[str, Any], diagnosis: str) -> Verdict:
        return Verdict(
            kind=VerdictKind.FAIL,
            output={**input_data, "external_agent_error": diagnosis},
            diagnosis=diagnosis,
        )

def _verdict_kind_for_status(status: ExternalAgentStatus) -> VerdictKind:
    if status == ExternalAgentStatus.SUCCEEDED:
        return VerdictKind.PASS
    if status == ExternalAgentStatus.PERMISSION_VIOLATION:
        return VerdictKind.PARTIAL
    return VerdictKind.FAIL


def _parse_capability_grant(raw: Any) -> AgentCapabilityGrant:
    if isinstance(raw, AgentCapabilityGrant):
        return raw
    if not isinstance(raw, dict):
        raise TypeError("capability_grant must be an object")
    payload = dict(raw)
    for key in (
        "allowed_tools",
        "allowed_read_roots",
        "allowed_write_paths",
        "allowed_write_roots",
        "allowed_shell_roots",
        "allowed_shell_command_prefixes",
    ):
        if key in payload:
            payload[key] = tuple(payload[key] or ())
    return AgentCapabilityGrant(**payload)


__all__ = ["ExternalAgentWorkerNode"]
