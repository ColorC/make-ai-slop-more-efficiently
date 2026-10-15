# [OMNI] material_id="material:core.hr.workers.digest.py" origin=codex ts=2026-08-18 type=worker status=active agent=codex
# [OMNI] summary="用统一结构化 LLM 把 HR 证据包整理成人读画像，并强制证据引用与非排名边界"
# [OMNI] tags=hr,worker,digest,llm,evidence
"""Registered semantic worker for one evidence-bounded HR digest."""

from __future__ import annotations

import json
from typing import Any, Callable

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ..digest import validate_digest_output


DIGEST_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "minLength": 1, "maxLength": 1200},
        "strength_signals": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string", "minLength": 1},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                },
                "required": ["statement", "evidence_refs"],
                "additionalProperties": False,
            },
        },
        "risk_signals": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string", "minLength": 1},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                },
                "required": ["statement", "evidence_refs"],
                "additionalProperties": False,
            },
        },
        "speed_assessment": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["observed", "insufficient"]},
                "statement": {"type": "string", "minLength": 1},
                "evidence_refs": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["status", "statement", "evidence_refs"],
            "additionalProperties": False,
        },
        "review_reminders": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "minLength": 1},
                    "target": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                },
                "required": ["reason", "target", "evidence_refs"],
                "additionalProperties": False,
            },
        },
        "caveats": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "overall_conclusion": {
            "type": "string",
            "enum": ["evidence_summary_only", "insufficient_for_comparison"],
        },
    },
    "required": [
        "summary", "strength_signals", "risk_signals", "speed_assessment",
        "review_reminders", "caveats", "overall_conclusion",
    ],
    "additionalProperties": False,
}


_SYSTEM = """You are the semantic reviewer for an evidence-first LLM work ledger.
Summarize only facts and cautious inferences supported by the supplied packet.
Never rank providers, models, sessions, or actors. Never treat missing feedback as a pass.
Every strength, risk, speed, or review-reminder claim must cite one or more exact strings
from allowed_evidence_refs. A cross-actor takeover is only a reminder to review, not a defect.
Explicit user feedback is primary evidence. Peer review is auxiliary and cannot override it.
If speed_evidence.status is insufficient, speed_assessment.status must be insufficient.
Use Chinese for human-readable statements. Return the required structured JSON only."""


class HrDigestWorker(Worker):
    DESCRIPTION = "把有界 HR 客观证据、反馈、互评和 Material 链接整理为非排名的人读画像"
    FORMAT_IN = "hr.digest-request.v1"
    FORMAT_OUT = "hr.digest.v1"
    INPUT_KEYS = ["packet"]
    OUTPUT_KEYS = ["digest", "model", "packet_fingerprint"]

    def __init__(self, *, model: str = "deepseek-v4-flash", call_json_fn: Callable[..., Any] | None = None):
        self.model = model
        self._call_json_fn = call_json_fn

    def run(self, input_data: Any) -> Verdict:
        packet = input_data.get("packet") if isinstance(input_data, dict) else None
        if not isinstance(packet, dict):
            return Verdict(kind=VerdictKind.FAIL, diagnosis="packet must be an object", output={})
        try:
            call_json_fn = self._call_json_fn
            if call_json_fn is None:
                from omnicompany.runtime.llm.structured import call_json

                call_json_fn = call_json
            digest = call_json_fn(
                system=_SYSTEM,
                user=json.dumps(packet, ensure_ascii=False, sort_keys=True),
                schema=DIGEST_OUTPUT_SCHEMA,
                model=self.model,
                caller="hr.digest.worker",
                max_tokens=1800,
            )
            validate_digest_output(digest, packet)
        except Exception as exc:  # noqa: BLE001 - worker returns one standard failure verdict.
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"HR digest rejected: {exc}",
                output={"packet_fingerprint": packet.get("packet_fingerprint"), "model": self.model},
            )
        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output={
                "digest": digest,
                "model": self.model,
                "packet_fingerprint": packet.get("packet_fingerprint"),
            },
        )


__all__ = ["DIGEST_OUTPUT_SCHEMA", "HrDigestWorker"]
