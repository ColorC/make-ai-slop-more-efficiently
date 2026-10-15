# [OMNI] material_id="material:core.hr.workers.historical-feedback.py" origin=codex ts=2026-08-18 type=worker status=active agent=codex
# [OMNI] summary="用统一结构化 LLM 复核历史用户评价候选，限制为原文摘录和证据边界内分类"
# [OMNI] tags=hr,worker,user-feedback,llm,evidence
"""Optional semantic classifier for ambiguous historical feedback candidates."""

from __future__ import annotations

import json
from typing import Any, Callable

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ..note import EXTRACTION_CONFIDENCES, FEEDBACK_DIMENSIONS, SEVERITIES


HISTORICAL_FEEDBACK_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_evaluation": {"type": "boolean"},
        "severity": {"type": "string", "enum": list(SEVERITIES)},
        "dimension": {"type": "string", "enum": list(FEEDBACK_DIMENSIONS)},
        "task_type": {"type": "string", "minLength": 1, "maxLength": 80},
        "target_scope": {
            "type": "string",
            "enum": ["current_session", "facility", "unclear", "prospective"],
        },
        "confidence": {"type": "string", "enum": list(EXTRACTION_CONFIDENCES)},
        "reason": {"type": "string", "minLength": 1, "maxLength": 500},
        "quoted_basis": {"type": "string", "maxLength": 500},
    },
    "required": [
        "is_evaluation", "severity", "dimension", "task_type", "target_scope",
        "confidence", "reason", "quoted_basis",
    ],
    "additionalProperties": False,
}

_SYSTEM = """Classify whether an exact human message evaluates completed or observed agent work.
The user message is primary evidence. Prior assistant text is context only. Never invent an actor,
file, outcome, or quote. Broad multi-session criticism has target_scope=facility. Worries and
hypotheticals are not evaluations and have target_scope=prospective. If attribution is unclear,
say unclear. quoted_basis must be an exact contiguous substring of user_text, or empty when
is_evaluation=false. Return structured JSON only."""


class HistoricalFeedbackClassifierWorker(Worker):
    DESCRIPTION = "复核一条历史用户消息是否为评价，并给出不越过原文的严重度、维度与作用域"
    FORMAT_IN = "hr.historical-feedback-candidate.v1"
    FORMAT_OUT = "hr.historical-feedback-classification.v1"
    INPUT_KEYS = ["candidate"]
    OUTPUT_KEYS = ["classification", "model"]

    def __init__(self, *, model: str = "deepseek-v4-flash", call_json_fn: Callable[..., Any] | None = None):
        self.model = model
        self._call_json_fn = call_json_fn

    def run(self, input_data: Any) -> Verdict:
        candidate = input_data.get("candidate") if isinstance(input_data, dict) else None
        if not isinstance(candidate, dict):
            return Verdict(kind=VerdictKind.FAIL, diagnosis="candidate must be an object", output={})
        user_text = str(candidate.get("user_text") or "")[:8000]
        if not user_text.strip():
            return Verdict(kind=VerdictKind.FAIL, diagnosis="user_text is required", output={})
        packet = {
            "user_text": user_text,
            "previous_assistant_text": str(candidate.get("previous_assistant_text") or "")[-8000:],
            "provider": str(candidate.get("provider") or ""),
            "session_id": str(candidate.get("session_id") or ""),
            "source_ref": str(candidate.get("source_ref") or ""),
        }
        try:
            call_json_fn = self._call_json_fn
            if call_json_fn is None:
                from omnicompany.runtime.llm.structured import call_json

                call_json_fn = call_json
            result = call_json_fn(
                system=_SYSTEM,
                user=json.dumps(packet, ensure_ascii=False, sort_keys=True),
                schema=HISTORICAL_FEEDBACK_OUTPUT_SCHEMA,
                model=self.model,
                caller="hr.historical_feedback.worker",
                max_tokens=900,
            )
            quote = str(result.get("quoted_basis") or "")
            if bool(result.get("is_evaluation")) and (not quote or quote not in user_text):
                raise ValueError("quoted_basis must be an exact substring of user_text")
            if not bool(result.get("is_evaluation")) and quote:
                raise ValueError("non-evaluation classification must not quote a basis")
        except Exception as exc:  # noqa: BLE001
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"historical feedback classification rejected: {exc}",
                output={"model": self.model},
            )
        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output={"classification": result, "model": self.model},
        )


__all__ = ["HISTORICAL_FEEDBACK_OUTPUT_SCHEMA", "HistoricalFeedbackClassifierWorker"]
