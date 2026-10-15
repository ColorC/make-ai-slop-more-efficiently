# [OMNI] material_id="material:core.hr.historical-feedback.py" origin=codex ts=2026-08-18 type=module status=active agent=codex
# [OMNI] summary="从根 CLI 会话的真实用户消息中提取可回链评价，严格排除注入、分叉副本、假设语句并幂等回填"
# [OMNI] tags=hr,user-feedback,history,attribution,evidence
"""Evidence-preserving historical user-feedback extraction.

This module deliberately favours precision over recall.  Deterministic rules only
auto-apply explicit evaluations from a root session.  Ambiguous or broad feedback
is surfaced as a candidate but never silently attached to one model/session.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Iterator

from .note import HrNote, read_notes, record_note


_NEGATIVE_MARKERS = (
    "太不对", "还是不对", "不对啊", "不对了", "改坏", "做的不是很好", "做得不是很好",
    "效率很慢", "确实很慢", "制造垃圾", "一塌糊涂", "无法返回", "无法使用", "跑偏了",
)
_POSITIVE_MARKERS = (
    "做得很好", "做的很好", "这次很好", "非常好", "现在可以了", "这就对了", "没问题了",
)
_PROSPECTIVE_MARKERS = ("我怕", "担心", "可能会", "如果", "假如", "希望不要")
_BROAD_MARKERS = ("近来", "长期以来", "之前很多次", "很多次工作", "总是", "普遍")
_WRAPPER_PREFIXES = (
    "# agents.md instructions", "<instructions>", "<environment_context>",
    "<user_info>",
    "<subagent_notification>", "<teammate-message", "<system-reminder>",
    "another claude session", "base directory for this skill",
)


@dataclass(frozen=True)
class HistoricalFeedbackCandidate:
    provider: str
    session_id: str
    model: str
    transcript_path: str
    line_number: int
    message_id: str
    timestamp: str
    text: str
    previous_assistant_text: str
    source_ref: str
    severity: str
    dimension: str
    task_type: str
    target_scope: str
    confidence: str
    disposition: str
    reason: str
    fingerprint: str

    @property
    def actor_id(self) -> str:
        if self.target_scope != "current_session" or not self.model:
            return ""
        return f"{self.provider}:{self.model}:{self.session_id}"

    @property
    def auto_applicable(self) -> bool:
        return (
            self.disposition == "evaluation"
            and self.confidence == "high"
            and self.target_scope in {"current_session", "facility"}
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "actor_id": self.actor_id, "auto_applicable": self.auto_applicable}


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    chunks: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") in {"input_text", "output_text", "text"}:
            value = item.get("text")
            if isinstance(value, str):
                chunks.append(value)
    return "\n".join(chunks).strip()


def _looks_injected(text: str) -> bool:
    lowered = text.lstrip().lower()
    if any(lowered.startswith(prefix) for prefix in _WRAPPER_PREFIXES):
        return True
    return (
        "<skills_instructions>" in lowered
        or "<permissions instructions>" in lowered
        or "you are `/root`" in lowered
    )


def _classify(text: str, previous_assistant_text: str) -> dict[str, str] | None:
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact or _looks_injected(compact):
        return None
    negative = next((item for item in _NEGATIVE_MARKERS if item in compact), "")
    positive = next((item for item in _POSITIVE_MARKERS if item in compact), "")
    if not negative and not positive:
        return None
    broad = any(item in compact for item in _BROAD_MARKERS)
    if not broad and any(item in compact for item in _PROSPECTIVE_MARKERS):
        return {
            "severity": "imperfect", "dimension": "quality", "task_type": "general",
            "target_scope": "unclear", "confidence": "low", "disposition": "prospective",
            "reason": "prospective or hypothetical wording is not an observed outcome",
        }

    combined = f"{previous_assistant_text[-4000:]}\n{compact}".lower()
    dimension_context = combined if len(compact) <= 20 else compact.lower()
    if positive and not negative:
        dimension = "quality"
    elif "慢" in compact or "效率" in compact:
        dimension = "speed"
    elif any(item in dimension_context for item in ("界面", "页面", "跳转", "返回", "ui", "ux")):
        dimension = "usability"
    elif any(item in compact for item in ("全导出", "范围", "跑偏", "不该")):
        dimension = "scope"
    elif any(item in compact for item in ("垃圾", "乱改", "脏")):
        dimension = "hygiene"
    else:
        dimension = "correctness" if negative else "quality"

    task_type = "general"
    task_needles = (
        ("frontend", ("界面", "页面", "前端", "跳转", "ui", "ux", "css")),
        ("testing", ("测试", "回归", "pytest", "验收")),
        ("backend", ("后端", "接口", "api", "数据库")),
        ("configuration", ("导出", "配表", "excel", "schedule", "dual capture", "dualcapture", "milestone")),
        ("planning", ("计划", "架构", "方案")),
    )
    task_context = compact.lower()
    if not any(needle in task_context for _, needles in task_needles for needle in needles) and len(compact) <= 20:
        task_context = combined
    for name, needles in task_needles:
        if any(needle in task_context for needle in needles):
            task_type = name
            break

    target_scope = "facility" if broad else "current_session"
    severe = negative and any(
        item in compact for item in ("无法返回", "无法使用", "改坏", "崩溃", "全导出", "一塌糊涂")
    )
    return {
        "severity": "pass" if positive and not negative else ("severe" if severe else "imperfect"),
        "dimension": dimension,
        "task_type": task_type,
        "target_scope": target_scope,
        "confidence": "high",
        "disposition": "evaluation",
        "reason": (
            "explicit user evaluation; broad wording kept at facility scope"
            if broad else "explicit user evaluation of the current session result"
        ),
    }


def _fingerprint(provider: str, session_id: str, message_id: str, source_ref: str, text: str) -> str:
    payload = "\x1f".join((provider, session_id, message_id, source_ref, text.strip()))
    return "feedback_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _candidate(
    *, provider: str, session_id: str, model: str, path: Path, line_number: int,
    message_id: str, timestamp: str, text: str, previous_assistant_text: str,
) -> HistoricalFeedbackCandidate | None:
    classified = _classify(text, previous_assistant_text)
    if classified is None:
        return None
    source_ref = f"{path.resolve(strict=False)}#L{line_number}"
    return HistoricalFeedbackCandidate(
        provider=provider,
        session_id=session_id,
        model=model,
        transcript_path=str(path.resolve(strict=False)),
        line_number=line_number,
        message_id=message_id,
        timestamp=timestamp,
        text=text.strip(),
        previous_assistant_text=previous_assistant_text[-4000:],
        source_ref=source_ref,
        fingerprint=_fingerprint(provider, session_id, message_id, source_ref, text),
        **classified,
    )


def _scan_codex(path: Path) -> tuple[list[HistoricalFeedbackCandidate], dict[str, Any]]:
    session_id = ""
    root_session = False
    current_model = ""
    first_model = ""
    previous_assistant = ""
    candidates: list[HistoricalFeedbackCandidate] = []
    parsed = 0
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line_number, raw in enumerate(stream, 1):
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            parsed += 1
            row_type = row.get("type")
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            if row_type == "session_meta":
                session_id = str(payload.get("session_id") or payload.get("id") or "")
                root_session = payload.get("thread_source") in {None, "", "user"}
                if isinstance(payload.get("source"), dict) and payload["source"].get("subagent"):
                    root_session = False
                continue
            if not root_session:
                continue
            if row_type == "turn_context":
                value = str(payload.get("model") or "").strip()
                if value:
                    current_model = value
                    first_model = first_model or value
                continue
            if row_type != "response_item" or payload.get("type") != "message":
                continue
            role = payload.get("role")
            text = _message_text(payload.get("content"))
            if role == "assistant":
                previous_assistant = text or previous_assistant
                continue
            if role != "user" or not text:
                continue
            item = _candidate(
                provider="codex", session_id=session_id, model=current_model, path=path,
                line_number=line_number, message_id=str(payload.get("id") or ""),
                timestamp=str(row.get("timestamp") or ""), text=text,
                previous_assistant_text=previous_assistant,
            )
            if item:
                candidates.append(item)
    if first_model:
        candidates = [replace(item, model=item.model or first_model) for item in candidates]
    return candidates, {
        "provider": "codex", "session_id": session_id, "root_session": root_session,
        "parsed_lines": parsed, "candidate_count": len(candidates),
    }


def _scan_claude(path: Path) -> tuple[list[HistoricalFeedbackCandidate], dict[str, Any]]:
    session_id = ""
    current_model = ""
    first_model = ""
    previous_assistant = ""
    candidates: list[HistoricalFeedbackCandidate] = []
    parsed = 0
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line_number, raw in enumerate(stream, 1):
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            parsed += 1
            session_id = session_id or str(row.get("sessionId") or "")
            if row.get("type") == "assistant":
                message = row.get("message") if isinstance(row.get("message"), dict) else {}
                model = str(message.get("model") or "").strip()
                if model:
                    current_model = model
                    first_model = first_model or model
                previous_assistant = _message_text(message.get("content")) or previous_assistant
                continue
            if row.get("type") != "user":
                continue
            origin = row.get("origin") if isinstance(row.get("origin"), dict) else {}
            if origin.get("kind") != "human" or row.get("promptSource") not in {None, "", "typed"}:
                continue
            message = row.get("message") if isinstance(row.get("message"), dict) else {}
            if message.get("role") != "user":
                continue
            text = _message_text(message.get("content"))
            if not text:
                continue
            item = _candidate(
                provider="claude", session_id=session_id, model=current_model, path=path,
                line_number=line_number, message_id=str(row.get("uuid") or ""),
                timestamp=str(row.get("timestamp") or ""), text=text,
                previous_assistant_text=previous_assistant,
            )
            if item:
                candidates.append(item)
    if first_model:
        candidates = [replace(item, model=item.model or first_model) for item in candidates]
    return candidates, {
        "provider": "claude", "session_id": session_id, "root_session": True,
        "parsed_lines": parsed, "candidate_count": len(candidates),
    }


def _scan_grok(path: Path) -> tuple[list[HistoricalFeedbackCandidate], dict[str, Any]]:
    summary_path = path.with_name("summary.json")
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        summary = {}
    summary = summary if isinstance(summary, dict) else {}
    info = summary.get("info") if isinstance(summary.get("info"), dict) else {}
    session_id = str(info.get("id") or path.parent.name)
    current_model = str(summary.get("current_model_id") or "").strip()
    first_model = current_model
    root_session = not bool(summary.get("parent_session_id"))
    previous_assistant = ""
    candidates: list[HistoricalFeedbackCandidate] = []
    parsed = 0
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line_number, raw in enumerate(stream, 1):
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            parsed += 1
            if not root_session:
                continue
            if row.get("type") == "assistant":
                model = str(row.get("model_id") or "").strip()
                if model:
                    current_model = model
                    first_model = first_model or model
                previous_assistant = _message_text(row.get("content")) or previous_assistant
                continue
            if row.get("type") != "user":
                continue
            # Grok marks copied context, tool continuations, and synthetic turns.
            # Only prompt-indexed human turns are eligible for auto attribution.
            if row.get("synthetic_reason") or "prompt_index" not in row:
                continue
            text = _message_text(row.get("content"))
            if not text:
                continue
            item = _candidate(
                provider="grok", session_id=session_id, model=current_model, path=path,
                line_number=line_number, message_id=str(row.get("prompt_index") or ""),
                timestamp="", text=text, previous_assistant_text=previous_assistant,
            )
            if item:
                candidates.append(item)
    if first_model:
        candidates = [replace(item, model=item.model or first_model) for item in candidates]
    return candidates, {
        "provider": "grok", "session_id": session_id, "root_session": root_session,
        "parsed_lines": parsed, "candidate_count": len(candidates),
    }


def scan_transcript(path: Path) -> tuple[list[HistoricalFeedbackCandidate], dict[str, Any]]:
    """Scan one provider transcript and return candidates plus an audit report."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    lowered = str(source).lower()
    if "\\.grok\\" in lowered or "/.grok/" in lowered:
        candidates, report = _scan_grok(source)
    elif "\\.claude\\" in lowered or "/.claude/" in lowered:
        candidates, report = _scan_claude(source)
    else:
        candidates, report = _scan_codex(source)
    return candidates, {"transcript": str(source.resolve(strict=False)), **report}


def discover_transcripts(paths: Iterable[Path]) -> Iterator[Path]:
    seen: set[str] = set()
    for value in paths:
        path = Path(value)
        rows = [path] if path.is_file() else path.rglob("*.jsonl") if path.is_dir() else []
        for row in rows:
            lowered = str(row).lower()
            if ("\\.grok\\" in lowered or "/.grok/" in lowered) and row.name != "chat_history.jsonl":
                continue
            key = str(row.resolve(strict=False)).lower()
            if key not in seen:
                seen.add(key)
                yield row


def scan_history(paths: Iterable[Path]) -> tuple[list[HistoricalFeedbackCandidate], list[dict[str, Any]]]:
    candidates: list[HistoricalFeedbackCandidate] = []
    reports: list[dict[str, Any]] = []
    for transcript in discover_transcripts(paths):
        rows, report = scan_transcript(transcript)
        candidates.extend(rows)
        reports.append(report)
    return candidates, reports


def apply_historical_feedback(
    candidates: Iterable[HistoricalFeedbackCandidate], *, data_dir: Path
) -> tuple[list[HrNote], int, int]:
    """Append high-confidence candidates once; return records, duplicate count, skipped count."""
    existing = list(read_notes(data_dir=data_dir))
    fingerprints = {row.feedback_fingerprint for row in existing if row.feedback_fingerprint}
    source_refs = {row.source_ref for row in existing if row.source_ref}
    records: list[HrNote] = []
    duplicate_count = 0
    skipped_count = 0
    for item in candidates:
        if not item.auto_applicable:
            skipped_count += 1
            continue
        if item.fingerprint in fingerprints or item.source_ref in source_refs:
            duplicate_count += 1
            continue
        target_kind = "session" if item.target_scope == "current_session" else "facility"
        target_ref = (
            f"session:{item.provider}:{item.session_id}"
            if target_kind == "session" else "facility:cross-session-user-feedback"
        )
        record = record_note(
            path=target_ref,
            severity=item.severity,
            note=item.text,
            data_dir=data_dir,
            source="user",
            source_ref=item.source_ref,
            attribution_evidence_refs=[item.source_ref],
            target_kind=target_kind,
            target_ref=target_ref,
            task_type=item.task_type,
            dimension=item.dimension,
            extraction_method="historical_rule",
            extraction_confidence=item.confidence,
            feedback_fingerprint=item.fingerprint,
            historical_session_actor_id=item.actor_id,
        )
        records.append(record)
        fingerprints.add(item.fingerprint)
        source_refs.add(item.source_ref)
    return records, duplicate_count, skipped_count


__all__ = [
    "HistoricalFeedbackCandidate", "scan_transcript", "discover_transcripts", "scan_history",
    "apply_historical_feedback",
]
