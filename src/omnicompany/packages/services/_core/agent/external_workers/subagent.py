# [OMNI] origin=codex domain=services/agent ts=2026-05-10 type=infrastructure
"""Expose external workers as explicit AgentRouter-compatible subagents."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from omnicompany.packages.services._core.agent.external_workers.base import (
    ExternalAgentPermissionMode,
    ExternalAgentRunSpec,
    ExternalAgentStatus,
    ExternalAgentWorkerRegistry,
)
from omnicompany.packages.services._core.agent.external_workers.factory import (
    build_default_external_agent_worker_registry,
)
from omnicompany.packages.services._core.agent.spawn_surface import (
    ENTRY_EXTERNAL_WORKER_AS_AGENT,
    ensure_agent_spawn_metadata,
)
from omnicompany.protocol.anchor import Verdict, VerdictKind


class ExternalAgentSubAgent:
    """Thin adapter from AgentRouter's subagent contract to ExternalAgentWorker.

    AgentRouter only needs the spawned object to expose `async run(input_data)`.
    This adapter deliberately stays outside `AgentNodeLoop`: Codex and Claude
    Code are complete local agents, not internal LLM loops.
    """

    def __init__(
        self,
        *,
        provider: str,
        cwd: Path | str,
        worker_registry: ExternalAgentWorkerRegistry,
        permission_mode: ExternalAgentPermissionMode | str = ExternalAgentPermissionMode.READONLY,
        model: str | None = None,
        profile: str | None = None,
        timeout_s: float = 600.0,
    ):
        self.provider = provider
        self.cwd = Path(cwd).expanduser().resolve()
        self.worker_registry = worker_registry
        self.permission_mode = permission_mode
        self.model = model
        self.profile = profile
        self.timeout_s = timeout_s

    async def run(self, input_data: Any) -> Verdict:
        task = _input_text(input_data)
        trace_id = _input_str(input_data, "trace_id")
        worker = self.worker_registry.create(self.provider)
        spec = ExternalAgentRunSpec(
            provider=self.provider,
            prompt=task,
            cwd=self.cwd,
            permission_mode=self.permission_mode,
            trace_id=trace_id,
            model=self.model,
            profile=self.profile,
            timeout_s=self.timeout_s,
            metadata=ensure_agent_spawn_metadata(
                ENTRY_EXTERNAL_WORKER_AS_AGENT,
                {
                    "parent_trace_id": _input_str(input_data, "parent_trace_id"),
                    "subagent_type": self.provider,
                    "description": _input_str(input_data, "description"),
                },
            ),
        )
        result = await worker.run(spec)
        status = result.normalized_status()
        kind = _verdict_kind_for_status(status)
        diagnosis = ""
        if kind != VerdictKind.PASS:
            diagnosis = result.error or f"external agent {self.provider!r} ended with {status.value}"

        return Verdict(
            kind=kind,
            output={
                "text": result.final_text,
                "run_id": result.run_id,
                "provider": result.provider,
                "status": status.value,
                "changed_files": result.changed_files,
                "diff_summary": result.diff_summary,
                "exit_code": result.exit_code,
                "duration_ms": result.duration_ms,
            },
            diagnosis=diagnosis,
            details={
                "external_agent": True,
                "provider": result.provider,
                "run_id": result.run_id,
                "status": status.value,
                "event_count": len(result.events),
            },
        )


ExternalSubAgentFactory = Callable[..., ExternalAgentSubAgent]


def build_external_agent_subagent_registry(
    *,
    cwd: Path | str,
    bus: Any | None = None,
    worker_registry: ExternalAgentWorkerRegistry | None = None,
    permission_mode: ExternalAgentPermissionMode | str = ExternalAgentPermissionMode.READONLY,
    timeout_s: float = 600.0,
    model_by_provider: dict[str, str] | None = None,
) -> dict[str, ExternalSubAgentFactory]:
    """Build AgentRouter subagent factories for external workers.

    The returned registry is intentionally separate from
    `build_default_subagent_registry`. Callers must explicitly merge it into a
    workflow context when they want `Agent(subagent_type="codex")` or
    `Agent(subagent_type="claude-code")` to launch local external agents.
    """

    registry = worker_registry or build_default_external_agent_worker_registry(bus=bus)
    models = model_by_provider or {}

    def _factory(provider: str) -> ExternalSubAgentFactory:
        def _build(model: str | None = None) -> ExternalAgentSubAgent:
            return ExternalAgentSubAgent(
                provider=provider,
                cwd=cwd,
                worker_registry=registry,
                permission_mode=permission_mode,
                model=model or models.get(provider),
                timeout_s=timeout_s,
            )

        return _build

    return {provider: _factory(provider) for provider in registry.list_providers()}


def _input_text(input_data: Any) -> str:
    if isinstance(input_data, dict):
        value = input_data.get("task") or input_data.get("prompt") or ""
        return str(value)
    return str(input_data)


def _input_str(input_data: Any, key: str) -> str:
    if not isinstance(input_data, dict):
        return ""
    value = input_data.get(key) or ""
    return str(value)


def _verdict_kind_for_status(status: ExternalAgentStatus) -> VerdictKind:
    if status == ExternalAgentStatus.SUCCEEDED:
        return VerdictKind.PASS
    if status == ExternalAgentStatus.PERMISSION_VIOLATION:
        return VerdictKind.PARTIAL
    return VerdictKind.FAIL


__all__ = [
    "ExternalAgentSubAgent",
    "ExternalSubAgentFactory",
    "build_external_agent_subagent_registry",
]
