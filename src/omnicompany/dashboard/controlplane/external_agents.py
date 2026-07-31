# [OMNI] origin=codex domain=dashboard ts=2026-05-11 type=api
"""Dashboard control-plane API for external agent workers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from omnicompany.packages.services._core.agent.external_workers import (
    ExternalAgentPermissionMode,
    ExternalAgentRunRequest,
    build_default_external_agent_worker_registry,
    resolve_external_agent_model,
    run_external_agent_request,
)
from omnicompany.packages.services._core.agent.spawn_surface import (
    ENTRY_EXTERNAL_WORKER_RUN,
    ensure_agent_spawn_metadata,
)


external_agents_router = APIRouter(tags=["external-agents"])

_REPO_ROOT = Path(__file__).resolve().parents[4]
_PROVIDER_LABELS = {
    "codex": "Codex CLI",
    "claude-code": "Claude Code SDK",
}


class ExternalAgentRunBody(BaseModel):
    provider: Literal["codex", "claude-code"]
    prompt: str = Field(min_length=1)
    cwd: str | None = None
    permission_mode: ExternalAgentPermissionMode = ExternalAgentPermissionMode.READONLY
    model: str | None = None
    model_policy: Literal["none", "cheap"] = "cheap"
    profile: str | None = None
    timeout_s: float = Field(default=600.0, gt=0, le=3600)
    attached_context: list[str] = Field(default_factory=list)
    output_schema_path: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    trace_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    allow_trusted_bypass: bool = False

    @field_validator("prompt")
    @classmethod
    def _prompt_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt is required")
        return value


def _resolve_cwd(value: str | None) -> Path:
    if not value:
        return _REPO_ROOT
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = _REPO_ROOT / path
    path = path.resolve()
    if not path.exists() or not path.is_dir():
        raise HTTPException(status_code=400, detail=f"cwd must be an existing directory: {path}")
    return path


def _result_payload(result) -> dict[str, Any]:
    status = result.normalized_status().value
    return {
        "run_id": result.run_id,
        "provider": result.provider,
        "status": status,
        "final_text": result.final_text,
        "structured_output": result.structured_output,
        "exit_code": result.exit_code,
        "changed_files": result.changed_files,
        "changed_files_count": len(result.changed_files or []),
        "diff_summary": result.diff_summary,
        "error": result.error,
        "duration_ms": result.duration_ms,
        "event_count": len(result.events or []),
        "events": [
            {
                "type": event.type,
                "message": event.message,
                "payload": event.payload,
                "ts": event.ts,
            }
            for event in (result.events or [])[-50:]
        ],
        "raw": result.raw,
    }


@external_agents_router.get("/external-agents/providers")
def list_external_agent_providers() -> dict[str, Any]:
    registry = build_default_external_agent_worker_registry()
    providers = registry.list_providers()
    return {
        "items": [
            {
                "provider": provider,
                "label": _PROVIDER_LABELS.get(provider, provider),
                "default_permission_mode": ExternalAgentPermissionMode.READONLY.value,
                "cheap_readonly_model": resolve_external_agent_model(
                    provider=provider,
                    permission_mode=ExternalAgentPermissionMode.READONLY,
                    model_policy="cheap",
                ),
                "cheap_write_model": resolve_external_agent_model(
                    provider=provider,
                    permission_mode=ExternalAgentPermissionMode.WORKSPACE_WRITE,
                    model_policy="cheap",
                ),
            }
            for provider in providers
        ],
        "total": len(providers),
        "model_policies": ["none", "cheap"],
        "permission_modes": [mode.value for mode in ExternalAgentPermissionMode],
    }


@external_agents_router.post("/external-agents/runs")
async def run_external_agent(body: ExternalAgentRunBody) -> dict[str, Any]:
    if (
        body.permission_mode == ExternalAgentPermissionMode.TRUSTED_BYPASS
        and not body.allow_trusted_bypass
    ):
        raise HTTPException(
            status_code=400,
            detail="trusted-bypass requires allow_trusted_bypass=true",
        )

    request = ExternalAgentRunRequest(
        provider=body.provider,
        prompt=body.prompt,
        cwd=_resolve_cwd(body.cwd),
        permission_mode=body.permission_mode,
        model=body.model,
        model_policy=body.model_policy,
        profile=body.profile,
        timeout_s=body.timeout_s,
        attached_context=list(body.attached_context),
        output_schema_path=body.output_schema_path,
        env=dict(body.env),
        trace_id=body.trace_id,
        metadata=ensure_agent_spawn_metadata(
            ENTRY_EXTERNAL_WORKER_RUN,
            body.metadata,
            entrypoint="dashboard_controlplane",
        ),
    )
    try:
        result = await run_external_agent_request(request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _result_payload(result)
