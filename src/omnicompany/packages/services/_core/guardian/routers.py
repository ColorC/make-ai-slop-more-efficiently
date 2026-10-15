# [OMNI] origin=codex domain=omnicompany/guardian ts=2026-08-14T19:25:00+08:00 type=router
# [OMNI] material_id="material:core.guardian.external_health_reporter.routers.py"
"""Guardian compatibility aliases and external-runtime health reporter."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import json
from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.agent_runtime import run_readonly_structured_agent
from omnicompany.runtime.routing.router import Router

from .workers import ArchAuditorWorker, FsScannerWorker


FsScannerRouter = FsScannerWorker
ArchAuditorRouter = ArchAuditorWorker
StructuredHealthRunner = Callable[..., Awaitable[dict[str, Any]]]

_ISSUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["severity", "category", "field", "message", "evidence", "fix_hint"],
    "properties": {
        "severity": {"type": "string", "enum": ["critical", "major", "minor"]},
        "category": {"type": "string", "minLength": 1},
        "field": {"type": "string"},
        "message": {"type": "string", "minLength": 1},
        "evidence": {"type": "string", "minLength": 1},
        "fix_hint": {"type": "string", "minLength": 1},
    },
}
_HEALTH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "issues", "summary", "top_actions", "report"],
    "properties": {
        "verdict": {"type": "string", "enum": ["healthy", "unhealthy", "uncertain"]},
        "issues": {"type": "array", "items": _ISSUE_SCHEMA},
        "summary": {"type": "string", "minLength": 1},
        "top_actions": {
            "type": "array",
            "maxItems": 3,
            "items": {"type": "string", "minLength": 1},
        },
        "report": {"type": "string", "minLength": 1},
    },
}

_INSTRUCTIONS = """\
You are OmniGuardian's repository health reporter. Evaluate only the scanner
facts supplied by Omni. You may use the granted read/list/search tools to check
specific paths, deprecated imports or directory contents. Do not invent issues.

Severity is categorical, never a numeric score:
- critical: structural violation, fabrication or missing core; this fails the gate
- major: incorrect naming/reference, convention violation or missing migration path
- minor: wording, formatting or small metadata issue

Every issue requires concrete evidence from scanner facts or file content and a
specific fix_hint. Empty __init__.py files are normal and must not be reported.
Return exactly one schema-valid JSON object. Never return health_score or any
other numeric quality score.
"""


def _task(input_data: dict[str, Any]) -> str:
    facts = {
        "project_root": input_data.get("project_root"),
        "fs_issues": input_data.get("fs_issues") or [],
        "arch_issues": input_data.get("arch_issues") or [],
    }
    return "# Guardian scanner facts\n" + json.dumps(facts, ensure_ascii=False, indent=2)


def _normalize_health_output(
    parsed: dict[str, Any],
    *,
    input_data: dict[str, Any] | None = None,
) -> Verdict:
    raw_issues = parsed.get("issues")
    if not isinstance(raw_issues, list):
        return _infrastructure_failure("health report issues is not a list", input_data=input_data)
    issues: list[dict[str, Any]] = []
    for index, item in enumerate(raw_issues):
        if not isinstance(item, dict):
            return _infrastructure_failure(
                f"health report issues[{index}] is not an object", input_data=input_data
            )
        severity = item.get("severity")
        if severity not in {"critical", "major", "minor"}:
            return _infrastructure_failure(
                f"health report issues[{index}] has invalid severity", input_data=input_data
            )
        normalized = {
            "severity": severity,
            "category": str(item.get("category") or "").strip(),
            "field": str(item.get("field") or "").strip(),
            "message": str(item.get("message") or "").strip(),
            "evidence": str(item.get("evidence") or "").strip(),
            "fix_hint": str(item.get("fix_hint") or "").strip(),
        }
        if not all(normalized[key] for key in ("category", "message", "evidence", "fix_hint")):
            return _infrastructure_failure(
                f"health report issues[{index}] lacks required evidence", input_data=input_data
            )
        issues.append(normalized)
    reported_verdict = parsed.get("verdict")
    top_actions = parsed.get("top_actions")
    summary = str(parsed.get("summary") or "").strip()
    report = str(parsed.get("report") or "").strip()
    if reported_verdict not in {"healthy", "unhealthy", "uncertain"}:
        return _infrastructure_failure("health report verdict is invalid", input_data=input_data)
    if (
        not isinstance(top_actions, list)
        or len(top_actions) > 3
        or any(not isinstance(item, str) or not item.strip() for item in top_actions)
    ):
        return _infrastructure_failure("health report top_actions is invalid", input_data=input_data)
    if not summary or not report:
        return _infrastructure_failure("health report summary/report is blank", input_data=input_data)
    counts = {
        "critical": sum(item["severity"] == "critical" for item in issues),
        "major": sum(item["severity"] == "major" for item in issues),
        "minor": sum(item["severity"] == "minor" for item in issues),
    }
    passed = counts["critical"] == 0
    source = input_data or {}
    output = {
        "verdict": "unhealthy" if not passed else reported_verdict,
        "passed": passed,
        "issues": issues,
        "counts": counts,
        "total_issues": len(issues),
        "top_actions": top_actions,
        "fs_issues": list(source.get("fs_issues") or []),
        "arch_issues": list(source.get("arch_issues") or []),
        "report": report,
        "summary": summary,
    }
    return Verdict(
        kind=VerdictKind.PASS if passed else VerdictKind.FAIL,
        output=output,
        diagnosis=output["report"] if not passed else None,
    )


def _infrastructure_failure(
    message: str,
    *,
    input_data: dict[str, Any] | None = None,
    runtime: Any = None,
) -> Verdict:
    source = input_data or {}
    issue = {
        "severity": "major",
        "category": "agent_runtime_error",
        "field": "health_reporter",
        "message": message,
        "evidence": "external AgentRuntime did not return a usable health report",
        "fix_hint": "inspect the runtime error and retry; do not treat this as repository health evidence",
    }
    return Verdict(
        kind=VerdictKind.FAIL,
        output={
            "verdict": "uncertain",
            "passed": False,
            "issues": [issue],
            "counts": {"critical": 0, "major": 1, "minor": 0},
            "total_issues": 1,
            "top_actions": [],
            "fs_issues": list(source.get("fs_issues") or []),
            "arch_issues": list(source.get("arch_issues") or []),
            "infrastructure_error": True,
            "report": message,
            "summary": message,
        },
        diagnosis=message,
        details={"agent_runtime": runtime or {}},
    )


class HealthReporterRouter(Router):
    """Evaluate deterministic scanner facts through the external runtime."""

    DESCRIPTION = "Guardian health reporter over the vendor-neutral external AgentRuntime boundary."
    FORMAT_IN = "guardian.arch-report"
    FORMAT_OUT = "guardian.health-report"

    def __init__(
        self,
        *,
        model: str | None = None,
        report_runner: StructuredHealthRunner = run_readonly_structured_agent,
    ) -> None:
        self._model = model or "deepseek-v4-flash"
        self._report_runner = report_runner

    async def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return _infrastructure_failure("health reporter input is not an object")
        if not (input_data.get("fs_issues") or input_data.get("arch_issues")):
            return _normalize_health_output(
                {
                    "verdict": "healthy",
                    "issues": [],
                    "summary": "自动扫描未发现问题",
                    "top_actions": [],
                    "report": "自动扫描未发现问题",
                },
                input_data=input_data,
            )
        raw_root = str(input_data.get("project_root") or "").strip()
        root = Path(raw_root).resolve()
        if not raw_root or not root.is_dir():
            return _infrastructure_failure(
                f"health reporter project_root does not exist: {raw_root or '(missing)'}",
                input_data=input_data,
            )
        try:
            response = await self._report_runner(
                task=_task(input_data),
                instructions=_INSTRUCTIONS,
                result_schema=_HEALTH_SCHEMA,
                workspace_cwd=root,
                caller="guardian.health-reporter",
                model=self._model,
                max_steps=15,
                max_output_tokens=4096,
                timeout_seconds=180,
            )
        except Exception as error:  # noqa: BLE001 - external failure is a verdict
            return _infrastructure_failure(
                f"external AgentRuntime execution failed: {type(error).__name__}: {error}",
                input_data=input_data,
            )
        if not response.get("ok") or not isinstance(response.get("final"), dict):
            return _infrastructure_failure(
                f"external AgentRuntime health report failed: {response.get('error') or 'missing result'}",
                input_data=input_data,
                runtime=response.get("runtime"),
            )
        verdict = _normalize_health_output(response["final"], input_data=input_data)
        verdict.details["agent_runtime"] = response.get("runtime", {})
        return verdict


__all__ = [
    "FsScannerWorker",
    "ArchAuditorWorker",
    "FsScannerRouter",
    "ArchAuditorRouter",
    "HealthReporterRouter",
]
