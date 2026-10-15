# [OMNI] origin=codex domain=runtime/info_audit/fallback ts=2026-08-14T00:00:00Z
# [OMNI] material_id="material:runtime.info_audit.fallback.external_runtime.py"
"""Information-gap fallback over the external AgentRuntime contract.

Cordis receives read-only workspace tools and returns a structured report.  It
never receives a write or shell capability.  Omni's deterministic
``guarded_write`` remains the sole authority that may persist the one allowed
scratch output.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Any

from omnicompany.core.config import _project_root, resolve_runtime_data_dir
from omnicompany.runtime.agent_runtime.structured_runner import (
    run_readonly_structured_agent,
)
from omnicompany.runtime.info_audit.guarded_write import guarded_write


_INSTRUCTIONS = """You are a strictly read-only information-gap investigator.
Inspect the granted workspace to investigate every missing-critical item supplied by Omni.
Do not modify files, run shell commands, use the network, repair the upstream output, or invent
evidence. Return one schema-valid JSON object. report_markdown must be a self-contained report
with Trigger, Investigation, Findings, and Next Steps sections. evidence_paths must list only real
files you inspected inside the granted workspace. If evidence cannot be found, set status to
not_found and state that explicitly in the report."""

_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["found", "not_found"]},
        "summary": {"type": "string", "minLength": 10},
        "report_markdown": {"type": "string", "minLength": 40},
        "evidence_paths": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
    },
    "required": ["status", "summary", "report_markdown", "evidence_paths"],
}

StructuredReportRunner = Callable[..., Awaitable[dict[str, Any]]]


@dataclass
class FallbackConfig:
    """One bounded fallback request.

    ``custom_tools`` remains in the public shape for compatibility but is
    rejected when non-empty: undeclared tools cannot cross AgentRuntime.
    """

    trigger: dict[str, Any]
    scratch_dir: Path | None = None
    max_turns: int = 30
    wall_clock_s: float = 300.0
    scratch_max_bytes: int = 100 * 1024
    custom_tools: list[Any] = field(default_factory=list)
    model: str | None = None
    description_hint: str = ""


@dataclass
class FallbackResult:
    status: str  # found / not_found / error / timeout
    output_path: str
    summary: str
    turns_used: int = 0
    elapsed_s: float = 0.0
    reject_log: list[str] = field(default_factory=list)


class UniversalFallbackLoop:
    """Public compatibility name for the external-runtime fallback job."""

    def __init__(
        self,
        *,
        report_runner: StructuredReportRunner = run_readonly_structured_agent,
    ) -> None:
        self._report_runner = report_runner

    async def handle(self, config: FallbackConfig) -> FallbackResult:
        """Investigate once, then let Omni persist exactly one guarded report."""
        started = time.monotonic()
        trace_id = str(config.trigger.get("trace_id") or "no-trace")
        node_id = str(config.trigger.get("node_id") or "unknown")
        scratch_dir = config.scratch_dir or (
            resolve_runtime_data_dir("scratch_fallback") / trace_id
        )
        allowed_output = scratch_dir / f"{node_id}.md"
        reject_log: list[str] = []

        invalid = self._validate_config(config)
        if invalid:
            return self._result(
                "error",
                allowed_output,
                invalid,
                started,
                reject_log,
            )

        try:
            response = await asyncio.wait_for(
                self._report_runner(
                    task=_build_user_prompt(config),
                    instructions=_INSTRUCTIONS,
                    result_schema=_RESULT_SCHEMA,
                    workspace_cwd=_project_root(),
                    caller="info-audit-fallback",
                    model=config.model or "deepseek-v4.1-flash",
                    max_steps=config.max_turns,
                    max_output_tokens=min(16_384, max(1024, config.scratch_max_bytes // 2)),
                    timeout_seconds=max(1, int(config.wall_clock_s)),
                ),
                timeout=config.wall_clock_s,
            )
        except asyncio.TimeoutError:
            return self._result(
                "timeout",
                allowed_output,
                f"fallback 超时 (wall_clock={config.wall_clock_s}s)",
                started,
                reject_log,
            )
        except Exception as error:  # noqa: BLE001 - public fallback never raises
            return self._result(
                "error",
                allowed_output,
                f"external AgentRuntime 执行异常: {type(error).__name__}: {error}",
                started,
                reject_log,
            )

        if not response.get("ok") or not isinstance(response.get("final"), dict):
            return self._result(
                "error",
                allowed_output,
                f"external AgentRuntime 失败: {response.get('error') or 'missing result'}",
                started,
                reject_log,
                turns_used=int(response.get("turn_count") or 0),
            )

        final = response["final"]
        semantic_error = _validate_report(final, Path(_project_root()).resolve())
        if semantic_error:
            return self._result(
                "error",
                allowed_output,
                f"external report rejected: {semantic_error}",
                started,
                reject_log,
                turns_used=int(response.get("turn_count") or 0),
            )

        write = guarded_write(
            target_path=allowed_output,
            content=str(final["report_markdown"]),
            allowed_output=allowed_output,
            trace_id=trace_id,
            max_bytes=config.scratch_max_bytes,
        )
        if write.status != "ok":
            reject_log.append(write.reason)
            return self._result(
                "error",
                allowed_output,
                f"scratch report rejected: {write.reason}",
                started,
                reject_log,
                turns_used=int(response.get("turn_count") or 0),
            )

        return self._result(
            str(final["status"]),
            allowed_output,
            str(final["summary"]),
            started,
            reject_log,
            turns_used=int(response.get("turn_count") or 0),
            include_path=True,
        )

    @staticmethod
    def _validate_config(config: FallbackConfig) -> str | None:
        if config.custom_tools:
            return "custom_tools are unsupported by the external read-only fallback"
        if config.max_turns < 1:
            return "max_turns must be positive"
        if config.wall_clock_s <= 0:
            return "wall_clock_s must be positive"
        if config.scratch_max_bytes < 1:
            return "scratch_max_bytes must be positive"
        return None

    @staticmethod
    def _result(
        status: str,
        output: Path,
        summary: str,
        started: float,
        reject_log: list[str],
        *,
        turns_used: int = 0,
        include_path: bool = False,
    ) -> FallbackResult:
        return FallbackResult(
            status=status,
            output_path=str(output) if include_path or output.exists() else "",
            summary=summary,
            turns_used=turns_used,
            elapsed_s=time.monotonic() - started,
            reject_log=reject_log,
        )


def _validate_report(report: dict[str, Any], workspace: Path) -> str | None:
    if report.get("status") not in {"found", "not_found"}:
        return "status is invalid"
    if len(str(report.get("summary") or "").strip()) < 10:
        return "summary is shorter than the output contract"
    if len(str(report.get("report_markdown") or "").strip()) < 40:
        return "report_markdown is shorter than the output contract"
    paths = report.get("evidence_paths")
    if not isinstance(paths, list):
        return "evidence_paths is not a list"
    for index, raw in enumerate(paths):
        candidate = Path(str(raw))
        resolved = candidate.resolve() if candidate.is_absolute() else (workspace / candidate).resolve()
        try:
            resolved.relative_to(workspace)
        except ValueError:
            return f"evidence_paths[{index}] escapes the granted workspace"
        if not resolved.is_file():
            return f"evidence_paths[{index}] does not resolve to an existing file"
    return None


def _build_user_prompt(config: FallbackConfig) -> str:
    trigger = config.trigger
    missing = trigger.get("missing_critical") or []
    lines = [
        "# Information-gap investigation",
        f"node_id: {trigger.get('node_id', '?')}",
        f"trace_id: {trigger.get('trace_id', 'no-trace')}",
        f"sufficiency: {trigger.get('sufficiency', '?')}",
        "missing_critical:",
    ]
    lines.extend(f"- {item}" for item in missing)
    if config.description_hint:
        lines.extend(("description_hint:", config.description_hint))
    lines.append(
        "Investigate read-only and return the complete report as structured JSON; "
        "Omni will perform the only permitted scratch write after validation."
    )
    return "\n".join(lines)


__all__ = ["FallbackConfig", "FallbackResult", "UniversalFallbackLoop"]
