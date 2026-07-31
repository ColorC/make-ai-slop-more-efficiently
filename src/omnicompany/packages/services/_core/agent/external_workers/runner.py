# [OMNI] origin=codex domain=services/agent ts=2026-05-11 type=infrastructure
"""Internal workflow/API entry for external agent worker runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from omnicompany.packages.services._core.agent.external_workers.base import (
    ExternalAgentPermissionMode,
    ExternalAgentResult,
    ExternalAgentRunSpec,
    ExternalAgentWorkerRegistry,
)
from omnicompany.packages.services._core.agent.external_workers.factory import (
    build_default_external_agent_worker_registry,
)
from omnicompany.packages.services._core.agent.external_workers.runtime_contract import (
    AgentCapabilityGrant,
    AgentContextEnvelope,
    AgentRuntimeProfile,
)
from omnicompany.packages.services._core.agent.spawn_surface import (
    ENTRY_EXTERNAL_WORKER_RUN,
    ensure_agent_spawn_metadata,
)

ExternalAgentModelPolicy = Literal["none", "cheap"]


@dataclass(frozen=True)
class ExternalAgentRunRequest:
    """Stable request shape for workflow/API callers.

    This is the narrow entry point above provider adapters. Callers can use it
    without knowing Codex CLI flags or Claude SDK option names.
    """

    provider: str
    prompt: str
    cwd: Path | str
    harness_id: str = ""
    model_provider: str = ""
    runtime_profile: AgentRuntimeProfile | str | None = None
    run_id: str = ""
    permission_mode: ExternalAgentPermissionMode | str = ExternalAgentPermissionMode.READONLY
    model: str | None = None
    model_policy: ExternalAgentModelPolicy = "cheap"
    profile: str | None = None
    timeout_s: float | None = None
    attached_context: list[str] = field(default_factory=list)
    context_envelope: AgentContextEnvelope | None = None
    capability_grant: AgentCapabilityGrant | None = None
    image_paths: list[Path | str] = field(default_factory=list)
    output_schema_path: Path | str | None = None
    watch_paths: list[Path | str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    trace_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


async def run_external_agent_request(
    request: ExternalAgentRunRequest,
    *,
    bus: Any | None = None,
    worker_registry: ExternalAgentWorkerRegistry | None = None,
) -> ExternalAgentResult:
    """Run an external agent from a workflow/API request object."""

    provider = request.provider.strip()
    owned_bus = None
    effective_bus = bus
    # These providers are integrated as EventBus-native workers. CLI/API callers do
    # not have to remember to construct a bus; tests and embedded callers may
    # still inject MemoryBus or another implementation explicitly.
    if worker_registry is None and effective_bus is None and provider in {
        "codex",
        "opencode",
        "kimi",
        "omni-native",
    }:
        from omnicompany.bus.sqlite import SQLiteBus

        owned_bus = SQLiteBus()
        await owned_bus.connect()
        effective_bus = owned_bus
    registry = worker_registry or build_default_external_agent_worker_registry(bus=effective_bus)
    model = request.model or resolve_external_agent_model(
        provider=provider,
        permission_mode=request.permission_mode,
        model_policy=request.model_policy,
    )
    metadata_extra: dict[str, Any] = {
        "runner_entrypoint": "external_agent_runner",
        "model_policy": request.model_policy,
        "model_resolved_by_runner": request.model is None and model is not None,
    }
    if "entrypoint" not in request.metadata:
        metadata_extra["entrypoint"] = "external_agent_runner"
    spec_kwargs: dict[str, Any] = {
        "provider": provider,
        "harness_id": request.harness_id or provider,
        "model_provider": request.model_provider,
        "runtime_profile": request.runtime_profile,
        "prompt": request.prompt,
        "cwd": request.cwd,
        "permission_mode": request.permission_mode,
        "trace_id": request.trace_id,
        "model": model,
        "profile": request.profile or ("omni-worker" if provider == "opencode" else None),
        "timeout_s": request.timeout_s,
        "attached_context": list(request.attached_context),
        "context_envelope": request.context_envelope,
        "capability_grant": request.capability_grant,
        "image_paths": list(request.image_paths),
        "output_schema_path": request.output_schema_path,
        "watch_paths": list(request.watch_paths),
        "env": dict(request.env),
        "metadata": ensure_agent_spawn_metadata(
            ENTRY_EXTERNAL_WORKER_RUN,
            request.metadata,
            **metadata_extra,
        ),
    }
    if request.run_id:
        spec_kwargs["run_id"] = request.run_id
    spec = ExternalAgentRunSpec(**spec_kwargs)
    worker = registry.create(provider)
    try:
        return await worker.run(spec)
    finally:
        if owned_bus is not None:
            await owned_bus.close()


def resolve_external_agent_model(
    *,
    provider: str,
    permission_mode: ExternalAgentPermissionMode | str,
    model_policy: ExternalAgentModelPolicy = "cheap",
) -> str | None:
    """Resolve a default model for external worker requests.

    `none` means leave provider defaults untouched. `cheap` only selects models
    verified in the local Codex CLI lane; Claude Code is left to its configured
    default because subscription/SDK model names differ by account.
    """

    if model_policy == "none":
        return None
    if model_policy != "cheap":
        raise ValueError("model_policy must be one of: none, cheap")

    if provider == "codex":
        mode = (
            permission_mode
            if isinstance(permission_mode, ExternalAgentPermissionMode)
            else ExternalAgentPermissionMode(str(permission_mode))
        )
        if mode == ExternalAgentPermissionMode.READONLY:
            return "gpt-5.3-codex-spark"
        return "gpt-5.4-mini"
    if provider == "opencode":
        return "the_company/gpt-5.6-terra"
    if provider == "omni-native":
        return "qwen3.7-max"
    if provider == "kimi":
        return "kimi-code/kimi-for-coding"
    return None


__all__ = [
    "ExternalAgentModelPolicy",
    "ExternalAgentRunRequest",
    "resolve_external_agent_model",
    "run_external_agent_request",
]
