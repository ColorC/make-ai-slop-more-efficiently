# [OMNI] origin=codex domain=services/agent ts=2026-07-16 type=infrastructure
# [OMNI] material_id="material:core.agent.external_worker.omni_native_adapter.py"
"""Expose the EventBus-native workspace Agent through the standard worker surface."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.agent.context_fork import (
    CONTEXT_FORK_CHECKPOINT_KEY,
    AgentContextCheckpoint,
)
from omnicompany.packages.services._core.agent.external_workers.base import (
    ExternalAgentEvent,
    ExternalAgentPermissionMode,
    ExternalAgentResult,
    ExternalAgentRunSpec,
    ExternalAgentStatus,
    ExternalAgentWorker,
)
from omnicompany.packages.services._core.agent.external_workers.codex import (
    _append_watched_path_summary,
    _diff_watch_snapshots,
    _git_changed_files,
    _git_diff_stat,
    _relative_watch_paths,
    _snapshot_watch_paths,
)
from omnicompany.packages.services._core.agent.material_workspace import (
    MaterialWorkspaceAgent,
)


class OmniNativeWorkspaceWorker(ExternalAgentWorker):
    """Run one native Agent conversation inside one declared workspace."""

    provider_name = "omni-native"
    handles_timeout = False

    async def _run_impl(self, spec: ExternalAgentRunSpec) -> ExternalAgentResult:
        cwd = spec.normalized_cwd()
        metadata = spec.metadata or {}
        watch_paths = spec.normalized_watch_paths()
        before_watch = _snapshot_watch_paths(cwd, watch_paths)
        before_changed = set(_git_changed_files(cwd))
        before_diff = _git_diff_stat(cwd)
        started = time.monotonic()
        permission = spec.normalized_permission_mode()
        grant = spec.resolved_capability_grant()

        raw_prefixes = metadata.get("allowed_bash_command_prefixes") or []
        command_prefixes = [raw_prefixes] if isinstance(raw_prefixes, str) else list(raw_prefixes)
        resume_checkpoint = _load_native_resume_checkpoint(metadata, cwd=cwd)
        export_checkpoint = _native_truthy(metadata.get("export_context_checkpoint")) or bool(
            resume_checkpoint
        )
        run_record_dir = _native_run_record_dir(metadata) if export_checkpoint else None

        model = spec.model or "qwen3.7-max"
        agent = MaterialWorkspaceAgent(
            model=model,
            bus=self._bus,
            llm_extra_body=_native_model_extra_body(model, metadata),
        )
        agent_input = {
            "task": spec.full_prompt(),
            "trace_id": spec.trace_id or spec.run_id,
            "workspace_root": str(cwd),
            "readonly": permission == ExternalAgentPermissionMode.READONLY,
            "allowed_read_roots": list(grant.allowed_read_roots),
            "allowed_write_paths": list(grant.allowed_write_paths),
            "allowed_write_roots": list(grant.allowed_write_roots),
            "allowed_bash_roots": list(grant.allowed_shell_roots),
            "allowed_powershell_roots": list(grant.allowed_shell_roots),
            "allowed_bash_command_prefixes": list(
                grant.allowed_shell_command_prefixes or command_prefixes
            ),
            "origin": "omnicompany.worker",
            "domain": "services/agent",
            "agent_name": "OmniNativeWorkspaceWorker",
            "model": model,
            "model_provider": spec.model_provider,
            "image_paths": [str(path) for path in spec.normalized_image_paths()],
            "export_context_checkpoint": export_checkpoint,
        }
        if resume_checkpoint is not None:
            agent_input[CONTEXT_FORK_CHECKPOINT_KEY] = resume_checkpoint.model_dump(
                mode="python"
            )
            agent_input["parent_trace_id"] = resume_checkpoint.parent_trace_id
        verdict = await agent.run(agent_input)

        after_watch = _snapshot_watch_paths(cwd, watch_paths)
        watched_changes = _diff_watch_snapshots(before_watch, after_watch)
        after_changed = set(_git_changed_files(cwd))
        changed_files = sorted(after_changed - before_changed)
        after_diff = _git_diff_stat(cwd)
        diff_summary = _append_watched_path_summary(
            after_diff if after_diff != before_diff else "",
            watched_changes,
        )
        output = dict(verdict.output) if isinstance(verdict.output, dict) else {}
        raw_checkpoint = output.pop("context_fork_checkpoint", None)
        checkpoint_path: Path | None = None
        checkpoint_id: str | None = None
        if raw_checkpoint is not None:
            checkpoint = AgentContextCheckpoint.model_validate(raw_checkpoint)
            checkpoint_id = checkpoint.checkpoint_id
            checkpoint_path = run_record_dir / "context_checkpoint.json"
            _write_native_checkpoint(checkpoint_path, checkpoint)
        final_text = _native_final_text(output)
        performance = output.get("performance") or {}
        usage = {
            "input_tokens": performance.get("input_tokens"),
            "output_tokens": performance.get("output_tokens"),
            "reasoning_tokens": performance.get("reasoning_tokens"),
            "total_tokens": performance.get("total_tokens"),
            "models": performance.get("models") or [],
        }
        status = ExternalAgentStatus.SUCCEEDED
        error = ""
        if str(verdict.kind.value) not in {"pass", "partial"}:
            status = ExternalAgentStatus.FAILED
            error = str(output.get("diagnosis") or "native Agent returned a non-pass verdict")
        completion_error = _native_completion_error(
            final_text=final_text,
            stop_reason=str(performance.get("stop_reason") or output.get("stop_reason") or ""),
        )
        if status == ExternalAgentStatus.SUCCEEDED and completion_error:
            status = ExternalAgentStatus.FAILED
            error = completion_error
        if permission == ExternalAgentPermissionMode.READONLY and (
            changed_files or watched_changes.get("has_changes") or after_diff != before_diff
        ):
            status = ExternalAgentStatus.PERMISSION_VIOLATION
            error = (
                "readonly workspace changed during worker window; ownership is unproven "
                "and detected changes were preserved"
            )

        return ExternalAgentResult(
            run_id=spec.run_id,
            provider=self.provider_name,
            status=status,
            final_text=final_text,
            events=[
                ExternalAgentEvent(
                    type="native-agent-summary",
                    message="native Agent conversation completed",
                    payload={
                        "performance": performance,
                        "model_visible_contract": output.get(
                            "model_visible_contract"
                        ),
                        "resumed_from_checkpoint": (
                            resume_checkpoint.checkpoint_id
                            if resume_checkpoint is not None
                            else None
                        ),
                        "exported_checkpoint": checkpoint_id,
                    },
                )
            ],
            changed_files=changed_files,
            diff_summary=diff_summary,
            error=error,
            duration_ms=(time.monotonic() - started) * 1000,
            raw={
                "harness_id": "omni-native",
                "model_provider": spec.model_provider or None,
                "runtime_profile": (
                    spec.normalized_runtime_profile().value
                    if spec.normalized_runtime_profile() is not None
                    else None
                ),
                "context_envelope": spec.normalized_context_envelope().audit_payload(),
                "capability_grant": grant.audit_payload(),
                "performance": performance,
                "model_visible_contract": output.get("model_visible_contract"),
                "usage": usage,
                "turn_count": output.get("turn_count"),
                "stop_reason": output.get("stop_reason"),
                "resumed_from_checkpoint_id": (
                    resume_checkpoint.checkpoint_id
                    if resume_checkpoint is not None
                    else None
                ),
                "context_checkpoint_id": checkpoint_id,
                "context_checkpoint_path": (
                    str(checkpoint_path) if checkpoint_path is not None else None
                ),
                "watch_paths": _relative_watch_paths(cwd, watch_paths),
                "watched_path_changes": watched_changes,
            },
        )


def _native_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _native_run_record_dir(metadata: dict[str, Any]) -> Path:
    raw = str(metadata.get("run_record_dir") or "").strip()
    if not raw:
        raise ValueError(
            "omni-native context checkpoint export requires a run_record_dir "
            "(CLI: pass --run-root)"
        )
    path = Path(raw).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_native_resume_checkpoint(
    metadata: dict[str, Any],
    *,
    cwd: Path,
) -> AgentContextCheckpoint | None:
    embedded = metadata.get("resume_context_checkpoint")
    raw_path = str(metadata.get("resume_context_checkpoint_path") or "").strip()
    if embedded is not None and raw_path:
        raise ValueError(
            "provide only one of resume_context_checkpoint or "
            "resume_context_checkpoint_path"
        )
    if embedded is not None:
        return AgentContextCheckpoint.model_validate(embedded)
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = cwd / path
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"resume context checkpoint does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return AgentContextCheckpoint.model_validate(payload)


def _write_native_checkpoint(
    path: Path,
    checkpoint: AgentContextCheckpoint,
) -> None:
    path.write_text(
        json.dumps(
            checkpoint.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _native_model_extra_body(model: str, metadata: dict[str, Any]) -> dict[str, Any]:
    """Resolve endpoint-native reasoning controls without a time cutoff.

    Callers may lower or raise endpoint-native reasoning controls for the task.
    This controls model work directly and avoids treating a healthy long
    response as a timeout.
    """

    normalized = model.casefold()
    if normalized == "k3":
        return {"reasoning_effort": "max"}
    if not normalized.startswith("qwen"):
        return {}
    raw_budget = metadata.get("thinking_budget", 8192)
    try:
        budget = int(str(raw_budget))
    except (TypeError, ValueError) as exc:
        raise ValueError("thinking_budget metadata must be an integer") from exc
    if not 512 <= budget <= 32768:
        raise ValueError("thinking_budget metadata must be between 512 and 32768")
    return {"enable_thinking": True, "thinking_budget": budget}


def _native_final_text(output: dict[str, Any]) -> str:
    """Normalize the unified Agent result without discarding a valid answer.

    ``ExtractResultRouter`` owns the native result contract and currently emits
    ``text``. Some external workers emit ``final_text``. The adapter accepts
    both at this boundary instead of reporting a succeeded run with an empty
    deliverable.
    """

    return str(output.get("final_text") or output.get("text") or "")


def _native_completion_error(*, final_text: str, stop_reason: str) -> str:
    """Reject incomplete native runs at the external-worker boundary."""

    normalized_reason = stop_reason.strip()
    if normalized_reason.startswith("tool_fuse:"):
        return f"native Agent stopped before completion: {normalized_reason}"
    if not final_text.strip():
        return "native Agent produced no final text"
    return ""


__all__ = [
    "OmniNativeWorkspaceWorker",
    "_load_native_resume_checkpoint",
    "_native_completion_error",
    "_native_final_text",
    "_native_model_extra_body",
    "_native_run_record_dir",
    "_native_truthy",
    "_write_native_checkpoint",
]
