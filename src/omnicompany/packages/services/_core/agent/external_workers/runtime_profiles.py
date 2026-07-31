# [OMNI] origin=codex domain=services/agent ts=2026-07-29 type=infrastructure
# [OMNI] material_id="material:core.agent.external_worker.runtime_profiles.py"
"""Explicit stable/native/compat routing above provider adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.agent.external_workers.base import (
    ExternalAgentPermissionMode,
    ExternalAgentResult,
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
    HarnessCapabilities,
    get_harness_capabilities,
)


_COMPAT_HARNESSES = frozenset({"opencode", "codex", "claude-code", "kimi"})


@dataclass(frozen=True)
class AgentRuntimeSelection:
    profile: AgentRuntimeProfile
    harness_id: str
    capabilities: HarnessCapabilities
    reason: str


@dataclass(frozen=True)
class AgentRuntimeRequest:
    """Canonical high-level request. The daily lane defaults to native_bus."""

    prompt: str
    cwd: Path | str
    runtime_profile: AgentRuntimeProfile | str = AgentRuntimeProfile.NATIVE_BUS
    harness_id: str = ""
    run_id: str = ""
    permission_mode: ExternalAgentPermissionMode | str = (
        ExternalAgentPermissionMode.READONLY
    )
    model_provider: str = ""
    model: str | None = None
    model_policy: ExternalAgentModelPolicy = "cheap"
    provider_profile: str | None = None
    timeout_s: float | None = None
    context_envelope: AgentContextEnvelope | None = None
    capability_grant: AgentCapabilityGrant | None = None
    image_paths: list[Path | str] = field(default_factory=list)
    output_schema_path: Path | str | None = None
    watch_paths: list[Path | str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    trace_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def select_agent_runtime(
    profile: AgentRuntimeProfile | str,
    *,
    requested_harness: str = "",
) -> AgentRuntimeSelection:
    normalized = (
        profile if isinstance(profile, AgentRuntimeProfile) else AgentRuntimeProfile(str(profile))
    )
    requested = requested_harness.strip()
    if normalized == AgentRuntimeProfile.STABLE_PI:
        if requested and requested != "omni-native":
            raise ValueError(
                "stable_pi is a legacy profile name pinned to the single "
                "omni-native EventBus Agent; Pi is an alignment reference, "
                "not a second runtime"
            )
        harness = "omni-native"
        reason = (
            "legacy stable_pi profile selects the same Pi-aligned "
            "Omni EventBus Agent as native_bus"
        )
    elif normalized == AgentRuntimeProfile.NATIVE_BUS:
        if requested and requested != "omni-native":
            raise ValueError(
                "native_bus is pinned to omni-native; use compat for another harness"
            )
        harness = "omni-native"
        reason = "daily profile explicitly selects the EventBus-native AgentNodeLoop"
    else:
        if not requested:
            raise ValueError("compat runtime profile requires harness_id")
        if requested not in _COMPAT_HARNESSES:
            allowed = ", ".join(sorted(_COMPAT_HARNESSES))
            raise ValueError(
                f"compat harness must be one of: {allowed}; got {requested!r}"
            )
        harness = requested
        reason = "compat profile preserves an explicitly requested external harness"
    return AgentRuntimeSelection(
        profile=normalized,
        harness_id=harness,
        capabilities=get_harness_capabilities(harness),
        reason=reason,
    )


async def run_agent_runtime_request(
    request: AgentRuntimeRequest,
    *,
    bus: Any | None = None,
    worker_registry: ExternalAgentWorkerRegistry | None = None,
) -> ExternalAgentResult:
    """Resolve one profile and reuse the authoritative external-worker launch surface."""

    selection = select_agent_runtime(
        request.runtime_profile,
        requested_harness=request.harness_id,
    )
    if selection.profile == AgentRuntimeProfile.STABLE_PI and (
        not request.model_provider.strip() or not (request.model or "").strip()
    ):
        raise ValueError(
            "stable_pi requires explicit model_provider and model; "
            "ambient model selection is disabled for the stable profile"
        )
    metadata = dict(request.metadata)
    metadata.update(
        {
            "runtime_selection_reason": selection.reason,
            "runtime_harness_capabilities": {
                "rpc": selection.capabilities.rpc,
                "native_event_stream": selection.capabilities.native_event_stream,
                "native_session_persistence": (
                    selection.capabilities.native_session_persistence
                ),
                "native_compaction": selection.capabilities.native_compaction,
                "native_sandbox": selection.capabilities.native_sandbox,
                "omni_tool_bridge": selection.capabilities.omni_tool_bridge,
                "terminal_event": selection.capabilities.terminal_event,
            },
        }
    )
    external = ExternalAgentRunRequest(
        provider=selection.harness_id,
        harness_id=selection.harness_id,
        model_provider=request.model_provider,
        runtime_profile=selection.profile,
        prompt=request.prompt,
        cwd=request.cwd,
        run_id=request.run_id,
        permission_mode=request.permission_mode,
        model=request.model,
        model_policy=request.model_policy,
        profile=request.provider_profile,
        timeout_s=request.timeout_s,
        context_envelope=request.context_envelope,
        capability_grant=request.capability_grant,
        image_paths=list(request.image_paths),
        output_schema_path=request.output_schema_path,
        watch_paths=list(request.watch_paths),
        env=dict(request.env),
        trace_id=request.trace_id,
        metadata=metadata,
    )
    return await run_external_agent_request(
        external,
        bus=bus,
        worker_registry=worker_registry,
    )


__all__ = [
    "AgentRuntimeRequest",
    "AgentRuntimeSelection",
    "run_agent_runtime_request",
    "select_agent_runtime",
]
