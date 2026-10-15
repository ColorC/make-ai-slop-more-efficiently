# [OMNI] origin=codex domain=services/plan_audit ts=2026-08-14T19:12:00+08:00 type=service
# [OMNI] material_id="material:core.plan_audit.external_runtime_auditor.py"
"""Plan/conversation landing audit over the external AgentRuntime boundary.

The runtime receives only bounded read/list/search, line-window and Git-evidence
tools. It returns a structured proposal; Omni validates the audit contract and
retains all report persistence authority in ``plan_audit.run``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.agent_runtime import (
    GIT_EVIDENCE_TOOL,
    READONLY_WORKSPACE_TOOLS,
    WORKSPACE_READ_LINES_TOOL,
    run_readonly_structured_agent,
)


StructuredAuditRunner = Callable[..., Awaitable[dict[str, Any]]]
_AUDIT_TOOLS = (
    *READONLY_WORKSPACE_TOOLS,
    WORKSPACE_READ_LINES_TOOL,
    GIT_EVIDENCE_TOOL,
)

_INSTRUCTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["text", "status", "evidence", "landed_then_removed"],
    "properties": {
        "text": {"type": "string", "minLength": 1},
        "status": {"type": "string", "enum": ["DONE", "PARTIAL", "PENDING"]},
        "evidence": {"type": "string", "minLength": 1},
        "landed_then_removed": {"type": "boolean"},
    },
}


def _result_schema(*, plan_mode: bool) -> dict[str, Any]:
    required = ["instructions", "not_landed", "summary"]
    properties: dict[str, Any] = {
        "instructions": {"type": "array", "items": _INSTRUCTION_SCHEMA},
        "not_landed": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "summary": {"type": "string", "minLength": 10},
    }
    if plan_mode:
        required.append("relevant_conversations")
        properties["relevant_conversations"] = {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["session_id", "is_executing_plan", "reason"],
                "properties": {
                    "session_id": {"type": "string", "minLength": 1},
                    "is_executing_plan": {"type": "boolean"},
                    "reason": {"type": "string", "minLength": 1},
                },
            },
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


_AUDIT_INSTRUCTIONS = """\
You are an independent OmniCompany landing auditor. Treat only real role:user
intent as instructions; exclude tool feedback, system injection and pure
acknowledgements. Split messages containing multiple requirements without
inventing requirements.

For each instruction, inspect both evidence sources:
1. later assistant text in the complete transcript for an explicit landed action;
2. current repository files and bounded Git history evidence.

The central rule is: work that landed and was later deleted, renamed or moved is
not PENDING. Mark it DONE or PARTIAL, set landed_then_removed=true, and cite the
historical evidence. Use PENDING only when neither source shows any landed work.
Use PARTIAL when evidence or requirement coverage is incomplete. Do not guess.

Read transcript files to their final line with omni_workspace_read_lines. For
Git evidence, use only omni_git_evidence operations log_path, log_search and
show_path. All tool fields are required: pass empty strings for unused path,
query or revision and a max_results value from 1 to 50. There is no shell,
network, write, Material mutation or Team dispatch authority.

Return exactly one schema-valid JSON object. not_landed must contain exactly the
text values of PENDING instruction entries. Evidence must name transcript
sections, file paths/lines, or commits; vague claims are invalid.
"""


def _validate_result(value: dict[str, Any], *, plan_mode: bool) -> str | None:
    instructions = value.get("instructions")
    not_landed = value.get("not_landed")
    if not isinstance(instructions, list) or not isinstance(not_landed, list):
        return "instructions and not_landed must be lists"
    pending: list[str] = []
    for index, item in enumerate(instructions):
        if not isinstance(item, dict):
            return f"instructions[{index}] is not an object"
        text = str(item.get("text") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        status = item.get("status")
        if not text or not evidence or status not in {"DONE", "PARTIAL", "PENDING"}:
            return f"instructions[{index}] violates the audit contract"
        if not isinstance(item.get("landed_then_removed"), bool):
            return f"instructions[{index}].landed_then_removed is not boolean"
        if status == "PENDING":
            pending.append(text)
    if not_landed != pending:
        return "not_landed does not exactly match PENDING instruction text"
    if len(str(value.get("summary") or "").strip()) < 10:
        return "summary is too short"
    if plan_mode:
        relevant = value.get("relevant_conversations")
        if not isinstance(relevant, list):
            return "relevant_conversations is missing"
        for index, item in enumerate(relevant):
            if (
                not isinstance(item, dict)
                or not str(item.get("session_id") or "").strip()
                or not isinstance(item.get("is_executing_plan"), bool)
                or not str(item.get("reason") or "").strip()
            ):
                return f"relevant_conversations[{index}] violates the audit contract"
    return None


class _BaseAuditor:
    plan_mode = False
    caller = "plan-audit.conversation"

    def __init__(
        self,
        *,
        model: str | None = None,
        report_runner: StructuredAuditRunner = run_readonly_structured_agent,
    ) -> None:
        self._model = model or "deepseek-v4-flash"
        self._report_runner = report_runner

    async def run(self, input_data: dict[str, Any]) -> Verdict:
        repo_root = Path(str(input_data.get("repo_root") or "")).resolve()
        if not repo_root.is_dir():
            return self._failure(f"audit repository root does not exist: {repo_root}")
        transcript_files = self._transcript_files(input_data)
        missing = [str(path) for path in transcript_files if not path.is_file()]
        if missing:
            return self._failure(f"audit transcript files do not exist: {missing}")
        extra_roots = tuple(dict.fromkeys(path.parent for path in transcript_files))
        try:
            response = await self._report_runner(
                task=self._task(input_data),
                instructions=_AUDIT_INSTRUCTIONS,
                result_schema=_result_schema(plan_mode=self.plan_mode),
                workspace_cwd=repo_root,
                caller=self.caller,
                model=self._model,
                max_steps=60,
                max_output_tokens=8192,
                timeout_seconds=600,
                allowed_tools=_AUDIT_TOOLS,
                allowed_read_roots=extra_roots,
            )
        except Exception as error:  # noqa: BLE001 - external failure is a verdict
            return self._failure(
                f"external AgentRuntime execution failed: {type(error).__name__}: {error}"
            )
        if not response.get("ok") or not isinstance(response.get("final"), dict):
            return self._failure(
                f"external AgentRuntime audit failed: {response.get('error') or 'missing result'}",
                runtime=response.get("runtime"),
            )
        output = dict(response["final"])
        semantic_error = _validate_result(output, plan_mode=self.plan_mode)
        if semantic_error:
            return self._failure(
                f"external AgentRuntime audit rejected: {semantic_error}",
                runtime=response.get("runtime"),
            )
        return Verdict(
            kind=VerdictKind.PASS,
            output=output,
            diagnosis=str(output["summary"])[:500],
            details={"agent_runtime": response.get("runtime", {})},
        )

    @staticmethod
    def _failure(message: str, *, runtime: Any = None) -> Verdict:
        return Verdict(
            kind=VerdictKind.FAIL,
            output={
                "instructions": [],
                "not_landed": [],
                "summary": message,
                "runtime_error": True,
            },
            diagnosis=message,
            details={"agent_runtime": runtime or {}},
        )

    def _task(self, input_data: dict[str, Any]) -> str:
        raise NotImplementedError

    def _transcript_files(self, input_data: dict[str, Any]) -> tuple[Path, ...]:
        raise NotImplementedError


class ConversationAuditor(_BaseAuditor):
    """Audit all real user instructions in one complete transcript."""

    @staticmethod
    def _transcript_files(input_data: dict[str, Any]) -> tuple[Path, ...]:
        return (Path(str(input_data.get("transcript_file") or "")).resolve(),)

    def _task(self, input_data: dict[str, Any]) -> str:
        return "\n".join(
            (
                "# Conversation landing audit",
                f"provider: {input_data.get('provider', '')}",
                f"session_id: {input_data.get('session_id', '')}",
                f"conversation_cwd: {input_data.get('cwd', '')}",
                f"repository_root: {input_data.get('repo_root', '')}",
                f"transcript_file: {input_data.get('transcript_file', '')}",
                f"message_count: {input_data.get('message_count', '?')}",
                f"character_count: {input_data.get('char_count', '?')}",
                f"source_truncated: {bool(input_data.get('truncated'))}",
                "Read the transcript through total_lines before reaching a conclusion.",
            )
        )


class PlanAuditor(_BaseAuditor):
    """Audit a plan, its exit criteria and candidate execution conversations."""

    plan_mode = True
    caller = "plan-audit.plan"

    @staticmethod
    def _transcript_files(input_data: dict[str, Any]) -> tuple[Path, ...]:
        return tuple(
            Path(str(item.get("transcript_file") or "")).resolve()
            for item in input_data.get("conversations") or []
            if isinstance(item, dict)
        )

    def _task(self, input_data: dict[str, Any]) -> str:
        conversations = input_data.get("conversations") or []
        candidates = "\n".join(
            f"- session_id={item.get('session_id', '')}; provider={item.get('provider', '')}; "
            f"match_reason={item.get('match_reason', '')}; transcript_file={item.get('transcript_file', '')}; "
            f"message_count={item.get('message_count', '?')}; truncated={bool(item.get('truncated'))}"
            for item in conversations
            if isinstance(item, dict)
        ) or "- none"
        criteria = "\n".join(
            f"- {criterion}" for criterion in input_data.get("exit_criteria") or []
        ) or "- none"
        return "\n".join(
            (
                "# Plan landing audit",
                f"plan_id: {input_data.get('plan_id', '')}",
                f"repository_root: {input_data.get('repo_root', '')}",
                "",
                "## Plan source",
                str(input_data.get("plan_md") or "(missing)"),
                "",
                "## Exit criteria",
                criteria,
                "",
                "## Candidate conversations",
                candidates,
                "",
                "First classify every candidate as genuinely executing/drafting this plan or incidental.",
                "Audit only real execution conversations. Add every exit criterion as an instruction",
                "whose text begins '[exit_criteria] '. Read each relevant transcript through total_lines.",
            )
        )


__all__ = ["ConversationAuditor", "PlanAuditor"]
