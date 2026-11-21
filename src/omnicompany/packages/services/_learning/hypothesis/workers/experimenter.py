# [OMNI] origin=codex domain=services/hypothesis ts=2026-08-15T00:00:00Z type=worker status=active
# [OMNI] material_id="material:learning.hypothesis.experimenter.external_evidence.py"
"""Bounded hypothesis exploration over the external AgentRuntime boundary.

The runtime may inspect the repository through grant-scoped read-only tools.  It
returns source-backed observations; Omni verifies every cited file/line/quote
before the evidence can reach the belief reflector.  Neither DSH nor Cordis
owns the decision library or any workspace mutation.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import json
from pathlib import Path
from typing import Any, ClassVar

from omnicompany.core.config import omni_workspace_root
from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.agent_runtime import (
    READONLY_WORKSPACE_TOOLS,
    WORKSPACE_READ_LINES_TOOL,
    run_readonly_structured_agent,
)


StructuredExperimentRunner = Callable[..., Awaitable[dict[str, Any]]]

_ALLOWED_TOOLS = (*READONLY_WORKSPACE_TOOLS, WORKSPACE_READ_LINES_TOOL)
_MAX_INPUT_CHARS = 120_000
_MAX_EVIDENCE_LINES = 200
_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "summary", "observations", "gaps"],
    "properties": {
        "status": {
            "type": "string",
            "enum": ["ok", "information_insufficient"],
        },
        "summary": {"type": "string", "minLength": 1, "maxLength": 2000},
        "observations": {
            "type": "array",
            "maxItems": 50,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "claim",
                    "path",
                    "line_start",
                    "line_end",
                    "evidence_quote",
                    "implication",
                    "confidence",
                    "related_belief_ids",
                ],
                "properties": {
                    "claim": {"type": "string", "minLength": 10, "maxLength": 2000},
                    "path": {"type": "string", "minLength": 1},
                    "line_start": {"type": "integer", "minimum": 1},
                    "line_end": {"type": "integer", "minimum": 1},
                    "evidence_quote": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 8000,
                    },
                    "implication": {"type": "string", "maxLength": 2000},
                    "confidence": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                    },
                    "related_belief_ids": {
                        "type": "array",
                        "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
        "gaps": {
            "type": "array",
            "maxItems": 30,
            "items": {"type": "string", "minLength": 1, "maxLength": 1000},
        },
    },
}

_INSTRUCTIONS = """\
You are the hypothesis pipeline's evidence explorer. Investigate only the
supplied goal and current belief snapshot. You may use only the granted,
read-only workspace query tools. Do not run commands, access the network,
modify files, propose decision-library mutations, or claim to have observed a
runtime behavior that is not present in a cited file.

Return source-backed observations. Each observation must cite one path relative
to the workspace root, a tight inclusive line range, and an exact non-empty
quote present inside that range. A claim must distinguish direct file evidence
from its implication. related_belief_ids may contain only ids supplied in the
current snapshot. Use information_insufficient with explicit gaps when the goal
requires shell execution, network access, image understanding, external state,
or evidence absent from the workspace. In that case observations must be empty.
For status=ok, provide at least one verified observation and leave gaps empty.
Return exactly one schema-valid JSON object with no Markdown fence.
"""


def _bounded_task(input_data: dict[str, Any]) -> str:
    session = input_data.get("session") or {}
    store = input_data.get("store") or {}
    payload = {
        "goal": str(session.get("goal") or "").strip(),
        "domain": str(session.get("domain") or "").strip(),
        "iteration": store.get("iteration", 0),
        "current_beliefs": store.get("entries", []),
        "requested_tool_hints": session.get("tools", []),
        "available_capability": "read-only workspace file/list/search/line-window",
    }
    raw = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if len(raw) > _MAX_INPUT_CHARS:
        raise ValueError(
            f"hypothesis exploration input exceeds {_MAX_INPUT_CHARS} characters; "
            "split the session explicitly"
        )
    return "Hypothesis exploration input:\n" + raw


def _resolve_evidence_path(workspace: Path, path_raw: object) -> Path:
    value = str(path_raw or "").strip()
    path = Path(value)
    if not value or path.is_absolute():
        raise ValueError("evidence path must be workspace-relative")
    resolved = (workspace / path).resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError as error:
        raise ValueError("evidence path escapes the workspace") from error
    if not resolved.is_file():
        raise ValueError(f"evidence path is not a file: {value}")
    return resolved


def _validate_observations(
    observations: list[Any],
    *,
    workspace: Path,
    known_belief_ids: set[str],
) -> list[dict[str, Any]]:
    verified: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, str]] = set()
    for index, raw in enumerate(observations):
        if not isinstance(raw, dict):
            raise ValueError(f"observations[{index}] must be an object")
        claim = str(raw.get("claim") or "").strip()
        quote = str(raw.get("evidence_quote") or "")
        if len(claim) < 10 or not quote.strip():
            raise ValueError(f"observations[{index}] lacks a concrete claim or quote")
        line_start = raw.get("line_start")
        line_end = raw.get("line_end")
        if not isinstance(line_start, int) or not isinstance(line_end, int):
            raise ValueError(f"observations[{index}] line range must be integers")
        if line_start < 1 or line_end < line_start:
            raise ValueError(f"observations[{index}] has an invalid line range")
        if line_end - line_start + 1 > _MAX_EVIDENCE_LINES:
            raise ValueError(f"observations[{index}] line range is too broad")

        resolved = _resolve_evidence_path(workspace, raw.get("path"))
        lines = resolved.read_text(encoding="utf-8-sig").splitlines()
        if line_end > len(lines):
            raise ValueError(f"observations[{index}] line range exceeds the file")
        excerpt = "\n".join(lines[line_start - 1 : line_end])
        if quote not in excerpt:
            raise ValueError(f"observations[{index}] quote is absent from the cited lines")

        related = raw.get("related_belief_ids")
        if not isinstance(related, list) or not set(map(str, related)).issubset(
            known_belief_ids
        ):
            raise ValueError(f"observations[{index}] cites an unknown belief id")
        relative = resolved.relative_to(workspace).as_posix()
        key = (relative, line_start, line_end, quote)
        if key in seen:
            raise ValueError(f"observations[{index}] duplicates earlier evidence")
        seen.add(key)
        item = dict(raw)
        item["path"] = relative
        item["claim"] = claim
        item["evidence_quote"] = quote
        item["related_belief_ids"] = [str(value) for value in related]
        verified.append(item)
    return verified


class ExperimenterWorker(Worker):
    """Return Omni-verified file evidence through the external runtime."""

    DESCRIPTION: ClassVar[str] = (
        "External read-only evidence exploration; Omni verifies every source "
        "before the trace reaches the belief reflector."
    )
    FORMAT_IN: ClassVar[str] = "hypothesis.store"
    FORMAT_OUT: ClassVar[str] = "hypothesis.factlog"

    def __init__(
        self,
        *,
        model: str | None = None,
        bus: Any = None,
        report_runner: StructuredExperimentRunner = run_readonly_structured_agent,
        workspace_root: str | Path | None = None,
        **_: Any,
    ) -> None:
        self._model = model or "deepseek-v4-flash"
        self._bus = bus
        self._report_runner = report_runner
        self._workspace = Path(workspace_root or omni_workspace_root()).resolve()

    async def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis="hypothesis experimenter input must be an object",
            )
        session = input_data.get("session") or {}
        store = input_data.get("store") or {}
        if not isinstance(session, dict) or not str(session.get("goal") or "").strip():
            return Verdict(kind=VerdictKind.FAIL, output={}, diagnosis="session.goal is required")
        if not isinstance(store, dict):
            return Verdict(kind=VerdictKind.FAIL, output={}, diagnosis="store must be an object")
        scene = session.get("scene") or {}
        if isinstance(scene, dict) and (
            scene.get("mode") == "shadow"
            and scene.get("kind") == "game-ui-exploration"
        ):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=(
                    "game-ui shadow exploration is not supported by the read-only "
                    "workspace AgentRuntime profile; use the explicit Game Observatory "
                    "visual-proposal migration path"
                ),
            )

        try:
            response = await self._report_runner(
                task=_bounded_task(input_data),
                instructions=_INSTRUCTIONS,
                result_schema=_RESULT_SCHEMA,
                workspace_cwd=self._workspace,
                caller="hypothesis.experimenter",
                model=self._model,
                max_steps=24,
                max_output_tokens=12_000,
                timeout_seconds=300,
                allowed_tools=_ALLOWED_TOOLS,
            )
        except Exception as error:  # noqa: BLE001 - runtime faults fail closed
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=f"hypothesis exploration failed: {type(error).__name__}: {error}",
            )
        if not response.get("ok") or not isinstance(response.get("final"), dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=f"external hypothesis exploration failed: {response.get('error') or 'missing result'}",
                details={"agent_runtime": response.get("runtime", {})},
            )

        result = dict(response["final"])
        observations = result.get("observations")
        gaps = result.get("gaps")
        if result.get("status") == "information_insufficient":
            if observations != [] or not isinstance(gaps, list) or not gaps:
                return Verdict(
                    kind=VerdictKind.FAIL,
                    output={},
                    diagnosis="information_insufficient requires zero observations and explicit gaps",
                    details={"agent_runtime": response.get("runtime", {})},
                )
            return Verdict(
                kind=VerdictKind.PARTIAL,
                output={
                    "iteration": store.get("iteration", 0),
                    "trace": [],
                    "final_text": str(result.get("summary") or ""),
                    "gaps": [str(item) for item in gaps],
                    "andon": True,
                    "turn_count": response.get("turn_count", 0),
                    "stop_reason": "information_insufficient",
                    "_meta": {"evidence_authority": "omni-control-plane"},
                },
                diagnosis="hypothesis experimenter requested unavailable evidence",
                details={"agent_runtime": response.get("runtime", {})},
            )
        if result.get("status") != "ok" or not isinstance(observations, list):
            return Verdict(kind=VerdictKind.FAIL, output={}, diagnosis="invalid evidence proposal envelope")
        if not observations or gaps not in ([], None):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis="status=ok requires observations and no gaps",
            )

        known_ids = {
            str(item.get("id"))
            for item in store.get("entries", [])
            if isinstance(item, dict) and item.get("id")
        }
        try:
            verified = _validate_observations(
                observations,
                workspace=self._workspace,
                known_belief_ids=known_ids,
            )
        except (OSError, UnicodeError, ValueError) as error:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=f"evidence proposal rejected before reflection: {error}",
                details={"agent_runtime": response.get("runtime", {})},
            )

        trace = [
            {
                "tool": "external_workspace_evidence",
                "args": {
                    "path": item["path"],
                    "line_start": item["line_start"],
                    "line_end": item["line_end"],
                    "related_belief_ids": item["related_belief_ids"],
                },
                "result": json.dumps(
                    {
                        "claim": item["claim"],
                        "evidence_quote": item["evidence_quote"],
                        "implication": item["implication"],
                        "confidence": item["confidence"],
                    },
                    ensure_ascii=False,
                ),
            }
            for item in verified
        ]
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "iteration": store.get("iteration", 0),
                "trace": trace,
                "observations": verified,
                "final_text": str(result.get("summary") or ""),
                "turn_count": response.get("turn_count", 0),
                "stop_reason": "structured_output",
                "_meta": {
                    "runtime": "external-agent-runtime",
                    "evidence_authority": "omni-control-plane",
                    "workspace_mutation": False,
                },
            },
            diagnosis=f"verified {len(verified)} workspace evidence observations",
            details={"agent_runtime": response.get("runtime", {})},
        )


__all__ = ["ExperimenterWorker"]
