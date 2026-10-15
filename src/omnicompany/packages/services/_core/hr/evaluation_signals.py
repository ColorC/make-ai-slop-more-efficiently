# [OMNI] material_id="material:core.hr.evaluation-signals.py" origin=codex ts=2026-08-18 type=module status=active agent=codex
# [OMNI] summary="从真实用户回合提取显式评价、纠错返工、澄清成本、接受与继续等分层反应信号"
# [OMNI] tags=hr,evaluation,user-reaction,process,evidence
"""Deterministic, evidence-preserving interaction signals for LLM HR.

Every genuine user turn is retained as an observation.  Only turns with a
bounded behavioural interpretation receive a score; ordinary requests remain
neutral instead of being silently discarded or falsely treated as approval.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


INTERACTION_SIGNAL_SCHEMA = "omnicompany.hr-interaction-signal.v7"
INTERACTION_SIGNALS_FILENAME = "interaction_signals.jsonl"

_INJECTED_PREFIXES = (
    "# agents.md instructions", "<instructions>", "<environment_context>",
    "<skills_instructions>", "<permissions instructions>", "you are `/root`",
    "<subagent_notification>", "<teammate-message", "<system-reminder>",
    "[omni agent message", "classification: inter-agent coordination",
)
_EXPLICIT_POSITIVE = (
    "做得很好", "做的很好", "这次很好", "非常好", "现在可以了", "这就对了",
    "没问题了", "很好", "不错", "符合预期", "通过", "解决了",
)
_EXPLICIT_NEGATIVE = (
    "太不对", "还是不对", "不对啊", "不对了", "改坏", "不是很好", "效率很慢",
    "确实很慢", "制造垃圾", "一塌糊涂", "无法返回", "无法使用", "跑偏",
    "很难反馈", "难以进行", "统计不出来", "参考出个啥", "这么平淡", "这么点",
    "没什么结论", "人看不来", "没有显示", "不支持", "有问题", "不工作",
)
_SEVERE_NEGATIVE = (
    "制造垃圾", "一塌糊涂", "无法使用", "无法返回", "完全不能", "统计不出来",
    "改坏", "崩溃", "啥也没有", "根本没", "严重",
)
_REWORK = (
    "重做", "重新做", "返工", "修复", "改一下", "改掉", "补上", "补全", "需要做到",
    "要改", "继续做这件", "重新生成", "再做一版",
)
_CLARIFICATION = (
    "啥叫", "什么意思", "为什么叫", "怎么看", "看不懂", "没看明白", "解释一下",
)
_ACCEPTANCE = (
    "好的", "好，", "ok", "可以", "行", "对的", "没问题", "就这样", "通过了",
)
_CONTINUATION = (
    "继续", "继续？", "开做吧", "开工吧", "往下做", "接着做", "继续吧",
)

_HIGH_PRECISION_POSITIVE = (
    "做得很好", "做的很好", "这次很好", "这就对了", "符合预期", "完全解决了",
    "现在可以了", "整体通过", "已经通过", "基本通过", "这版不错", "版本不错", "很好",
)
_HIGH_PRECISION_NEGATIVE = (
    "完全不对", "还是不对", "根本不对", "严重不符合", "不符合原本", "差得离谱",
    "太差", "太菜", "赶紧回退", "一团乱", "无法使用", "统计不出来", "人看不来",
    "制造垃圾", "改坏", "根本没有", "完全没有", "一大堆问题", "很多问题", "跑偏",
    "做错", "严重问题", "几乎相反",
)
_INSTRUCTIONAL_POSITIVE = (
    "审批通过", "标记为通过", "必须通过", "通过和否决", "通过条件", "验收通过",
    "是否符合预期",
)

_TASK_NEEDLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("frontend", ("界面", "页面", "前端", "侧边栏", "画布", "ui", "ux", "css", "html")),
    ("testing", ("测试", "回归", "pytest", "验收", "e2e")),
    ("backend", ("后端", "接口", "api", "数据库", "服务端")),
    ("configuration", ("导出", "配表", "excel", "配置", "schedule", "milestone")),
    ("documentation", ("报告", "文档", "结论", "可视化", "材料")),
    ("governance", ("规范", "治理", "skill", "worker", "agent", "归因", "责任")),
    ("data", ("数据", "统计", "样本", "量化", "指标", "矩阵")),
    ("planning", ("计划", "架构", "方案", "路线")),
)


@dataclass(frozen=True)
class InteractionSignal:
    id: str
    provider: str
    session_id: str
    model: str
    turn_index: int
    signal_type: str
    dimension: str
    task_type: str
    score: float | None
    weight: float
    confidence: str
    user_text: str
    evidence_ref: str
    reason: str
    preceded_by_assistant: bool = False
    schema: str = INTERACTION_SIGNAL_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _compact(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _is_injected(text: str) -> bool:
    lowered = text.lstrip().lower()
    return any(lowered.startswith(prefix) for prefix in _INJECTED_PREFIXES)


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(item.get("text") or "")
        for item in content if isinstance(item, Mapping) and item.get("type") in {"text", "input_text", "output_text"}
    ).strip()


def infer_text_task_type(user_text: str, previous_assistant_text: str = "") -> str:
    compact = _compact(user_text).lower()
    context = compact if len(compact) > 24 else f"{_compact(previous_assistant_text)[-2500:]} {compact}".lower()
    for task_type, needles in _TASK_NEEDLES:
        if any(needle in context for needle in needles):
            return task_type
    return "general"


def classify_user_reaction(user_text: str, previous_assistant_text: str = "") -> dict[str, Any] | None:
    """Classify one real user turn without inventing a quality verdict."""
    compact = _compact(user_text)
    if not compact or _is_injected(compact):
        return None
    lowered = compact.lower()
    task_type = infer_text_task_type(compact, previous_assistant_text)
    if not _compact(previous_assistant_text):
        return {
            "signal_type": "request_or_context", "dimension": "quality",
            "task_type": task_type, "score": None, "weight": 0.0,
            "confidence": "high", "reason": "first user turn has no preceding model result",
        }
    negative = min(
        (marker for marker in (*_HIGH_PRECISION_NEGATIVE, *_EXPLICIT_NEGATIVE) if marker in lowered),
        key=lambda marker: lowered.index(marker), default="",
    )
    if negative == "有问题":
        marker_at = lowered.index(negative)
        prefix = lowered[max(0, marker_at - 10):marker_at]
        if any(token in prefix for token in ("如果", "假如", "是否", "有没有")):
            negative = ""
    instructional_positive = any(marker in lowered for marker in _INSTRUCTIONAL_POSITIVE)
    positive_candidates = [] if instructional_positive else [
        marker for marker in _HIGH_PRECISION_POSITIVE
        if marker in lowered
        and "不" not in lowered[max(0, lowered.index(marker) - 3):lowered.index(marker)]
        and "没" not in lowered[max(0, lowered.index(marker) - 3):lowered.index(marker)]
        and "是否" not in lowered[max(0, lowered.index(marker) - 3):lowered.index(marker)]
        and not any(
            token in lowered[
                lowered.index(marker) + len(marker):lowered.index(marker) + len(marker) + 3
            ]
            for token in ("吗", "么", "?", "？")
        )
    ]
    positive = min(
        positive_candidates, key=lambda marker: lowered.index(marker), default="",
    )
    severe = any(marker in lowered for marker in _SEVERE_NEGATIVE)
    rework = any(marker in lowered for marker in _REWORK)
    clarification = any(marker in lowered for marker in _CLARIFICATION)
    continuation = bool(re.fullmatch(r"(?:好的?[，, ]*)?(?:继续|继续吧|接着做|往下做|开做吧|开工吧)[？?。！!…]*", lowered))
    acceptance = bool(re.fullmatch(r"(?:好的?|ok|可以|行|对的|没问题|就这样)[，,。！!…]*", lowered))

    contrast = any(marker in lowered for marker in ("但是", "不过", "然而", "可是"))
    if positive and (negative or contrast):
        return {
            "signal_type": "mixed_feedback", "dimension": "quality",
            "task_type": task_type, "score": None, "weight": 0.0,
            "confidence": "high",
            "reason": f"mixed reaction contains positive marker {positive} and issue/contrast marker {negative or 'contrast'}",
        }
    if negative:
        dimension = (
            "speed" if any(token in lowered for token in ("慢", "效率", "快"))
            else "hygiene" if negative == "制造垃圾" or any(token in lowered for token in ("乱改", "脏"))
            else "usability" if task_type == "frontend" or "看不" in lowered
            else "communication" if clarification or task_type == "documentation" or "结论" in lowered
            else "correctness"
        )
        return {
            "signal_type": "explicit_criticism",
            "dimension": dimension,
            "task_type": task_type,
            "score": (0.0 if severe else 20.0 if rework else 30.0) if len(compact) <= 240 else None,
            "weight": 1.0 if len(compact) <= 240 else 0.0,
            "confidence": "high" if len(compact) <= 240 else "medium",
            "reason": f"explicit negative reaction marker: {negative}",
        }
    if positive and len(compact) <= 160:
        return {
            "signal_type": "explicit_approval", "dimension": "quality",
            "task_type": task_type, "score": 100.0, "weight": 1.0,
            "confidence": "high", "reason": f"explicit positive reaction marker: {positive}",
        }
    if rework:
        return {
            "signal_type": "rework_request", "dimension": "correctness",
            "task_type": task_type,
            "score": 35.0 if len(compact) <= 240 else None,
            "weight": 0.7 if len(compact) <= 240 else 0.0,
            "confidence": "medium", "reason": "user requested revision of preceding work",
        }
    if clarification:
        return {
            "signal_type": "clarification_cost", "dimension": "communication",
            "task_type": task_type,
            "score": 45.0 if len(compact) <= 240 else None,
            "weight": 0.45 if len(compact) <= 240 else 0.0,
            "confidence": "medium", "reason": "user needed clarification after the preceding response",
        }
    if acceptance:
        return {
            "signal_type": "acceptance", "dimension": "quality",
            "task_type": task_type, "score": None, "weight": 0.0,
            "confidence": "medium", "reason": "acknowledgement is retained but is not a quality verdict",
        }
    if continuation:
        return {
            "signal_type": "continuation", "dimension": "communication",
            "task_type": task_type, "score": None, "weight": 0.0,
            "confidence": "low", "reason": "continuation is workflow state, not a quality verdict",
        }
    return {
        "signal_type": "request_or_context", "dimension": "quality",
        "task_type": task_type, "score": None, "weight": 0.0,
        "confidence": "high", "reason": "real user turn retained as neutral context",
    }


def _session_rows(session_registry: Any) -> list[dict[str, Any]]:
    if isinstance(session_registry, Mapping):
        return [
            {"id": str(value.get("id") or key), **dict(value)}
            for key, value in session_registry.items() if isinstance(value, Mapping)
        ]
    return [dict(row) for row in session_registry or [] if isinstance(row, Mapping)]


def extract_registry_interaction_signals(
    session_registry: Any,
    *,
    session_models: Mapping[str, tuple[str, str]] | None = None,
) -> tuple[list[InteractionSignal], dict[str, Any]]:
    """Extract scored and neutral user reactions from registry history summaries."""
    lookup = dict(session_models or {})
    signals: list[InteractionSignal] = []
    counts: Counter[str] = Counter()
    unattributed = 0
    for session in _session_rows(session_registry):
        session_id = str(session.get("id") or session.get("session_id") or "")
        provider = str(session.get("provider") or "unknown")
        model = str(session.get("model") or "").strip()
        if model in {"", "-", "default", "(local default)"}:
            model = ""
        candidate_ids = (
            session_id,
            str(session.get("provider_session_id") or ""),
            str(session.get("claude_session_id") or ""),
            str(session.get("native_session_id") or ""),
        )
        for candidate in candidate_ids:
            resolved = lookup.get(candidate)
            if resolved:
                provider, model = resolved
                break
        previous_assistant = ""
        for index, message in enumerate(session.get("history_summary") or []):
            if not isinstance(message, Mapping):
                continue
            role = str(message.get("role") or "")
            text = _compact(message.get("text"))
            if role == "assistant":
                previous_assistant = text or previous_assistant
                continue
            if role != "user" or not text:
                continue
            classified = classify_user_reaction(text, previous_assistant)
            if classified is None:
                continue
            evidence_ref = f"registry:{session_id}#history_summary[{index}]"
            digest = hashlib.sha256(f"{session_id}\x1f{index}\x1f{text}".encode("utf-8")).hexdigest()[:24]
            signal = InteractionSignal(
                id=f"interaction-{digest}", provider=provider, session_id=session_id,
                model=model, turn_index=index, user_text=text[:2000], evidence_ref=evidence_ref,
                preceded_by_assistant=bool(previous_assistant),
                **classified,
            )
            signals.append(signal)
            counts[signal.signal_type] += 1
            if not model:
                unattributed += 1
    return signals, {
        "observed_user_turns": len(signals),
        "scored_user_reactions": sum(signal.score is not None for signal in signals),
        "neutral_user_turns": sum(signal.score is None for signal in signals),
        "unattributed_user_turns": unattributed,
        "by_signal_type": dict(sorted(counts.items())),
    }


def _make_signal(
    *, provider: str, session_id: str, model: str, turn_index: int,
    user_text: str, previous_assistant_text: str, evidence_ref: str,
) -> InteractionSignal | None:
    classified = classify_user_reaction(user_text, previous_assistant_text)
    if classified is None:
        return None
    digest = hashlib.sha256(
        f"{provider}\x1f{session_id}\x1f{evidence_ref}\x1f{user_text}".encode("utf-8")
    ).hexdigest()[:24]
    return InteractionSignal(
        id=f"interaction-{digest}", provider=provider, session_id=session_id,
        model=model, turn_index=turn_index, user_text=user_text[:2000],
        evidence_ref=evidence_ref, preceded_by_assistant=bool(previous_assistant_text),
        **classified,
    )


def _kimi_model(alias: str, request_model: str = "") -> str:
    """Resolve Kimi Code's display alias to the model actually sent upstream."""
    model = _compact(request_model)
    if model:
        if model == "k3":
            return "kimi-k3"
        return model
    alias = _compact(alias)
    if alias in {"kimi-code/k3", "sfkey/k3"}:
        return "kimi-k3"
    if alias == "kimi-code/kimi-for-coding":
        return "kimi-for-coding"
    if alias == "__kimi_env_model__":
        return ""
    return alias


def scan_transcript_interactions(path: Path) -> tuple[list[InteractionSignal], dict[str, Any]]:
    """Stream one native Codex/Claude/Grok/Kimi root transcript and retain every human turn."""
    source = Path(path)
    lowered_path = str(source).lower()
    provider = (
        "grok" if ".grok" in lowered_path
        else "kimi_code" if ".kimi-code" in lowered_path
        else "claude" if ".claude" in lowered_path
        else "codex"
    )
    session_id = ""
    current_model = ""
    first_model = ""
    previous_assistant = ""
    root_session = True
    signals: list[InteractionSignal] = []
    parsed_lines = 0
    if provider == "grok":
        try:
            summary = json.loads(source.with_name("summary.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summary = {}
        info = summary.get("info") if isinstance(summary, Mapping) else {}
        session_id = str((info or {}).get("id") or source.parent.name)
        current_model = str((summary or {}).get("current_model_id") or "")
        first_model = current_model
        root_session = not bool((summary or {}).get("parent_session_id"))
    elif provider == "kimi_code":
        root_session = source.parent.name == "main" and source.name == "wire.jsonl"
        session_dir = next(
            (part for part in reversed(source.parts) if part.startswith("session_")), ""
        )
        session_id = session_dir.removeprefix("session_")
    with source.open("r", encoding="utf-8", errors="replace") as stream:
        for line_number, raw in enumerate(stream, 1):
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, Mapping):
                continue
            parsed_lines += 1
            role = ""
            text = ""
            human = False
            if provider == "codex":
                payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
                if row.get("type") == "session_meta":
                    session_id = str(payload.get("session_id") or payload.get("id") or "")
                    root_session = payload.get("thread_source") in {None, "", "user"}
                    if isinstance(payload.get("source"), Mapping) and payload["source"].get("subagent"):
                        root_session = False
                    continue
                if row.get("type") == "turn_context":
                    current_model = str(payload.get("model") or current_model)
                    first_model = first_model or current_model
                    continue
                if row.get("type") != "response_item" or payload.get("type") != "message":
                    continue
                role = str(payload.get("role") or "")
                text = _message_text(payload.get("content"))
                human = role == "user"
            elif provider == "claude":
                session_id = session_id or str(row.get("sessionId") or "")
                message = row.get("message") if isinstance(row.get("message"), Mapping) else {}
                role = str(row.get("type") or "")
                if role == "assistant":
                    current_model = str(message.get("model") or current_model)
                    first_model = first_model or current_model
                text = _message_text(message.get("content"))
                origin = row.get("origin") if isinstance(row.get("origin"), Mapping) else {}
                human = role == "user" and origin.get("kind") == "human" and row.get("promptSource") in {None, "", "typed"}
            elif provider == "kimi_code":
                row_type = str(row.get("type") or "")
                if row_type == "config.update":
                    resolved = _kimi_model(str(row.get("modelAlias") or ""))
                    current_model = resolved or current_model
                    first_model = first_model or resolved
                    continue
                if row_type == "llm.request":
                    resolved = _kimi_model(
                        str(row.get("modelAlias") or ""), str(row.get("model") or "")
                    )
                    current_model = resolved or current_model
                    first_model = first_model or resolved
                    continue
                if row_type == "context.append_message":
                    message = row.get("message") if isinstance(row.get("message"), Mapping) else {}
                    role = str(message.get("role") or "")
                    text = _message_text(message.get("content"))
                elif row_type == "context.append_loop_event":
                    event = row.get("event") if isinstance(row.get("event"), Mapping) else {}
                    part = event.get("part") if isinstance(event.get("part"), Mapping) else {}
                    if event.get("type") != "content.part" or part.get("type") != "text":
                        continue
                    role = "assistant"
                    text = str(part.get("text") or "").strip()
                elif row_type == "turn.prompt":
                    origin = row.get("origin") if isinstance(row.get("origin"), Mapping) else {}
                    role = "user"
                    text = _message_text(row.get("input"))
                    human = origin.get("kind") == "user"
                else:
                    continue
            else:
                role = str(row.get("type") or "")
                current_model = str(row.get("model_id") or current_model) if role == "assistant" else current_model
                first_model = first_model or current_model
                text = _message_text(row.get("content"))
                human = role == "user" and not row.get("synthetic_reason") and "prompt_index" in row
            if not root_session:
                continue
            if role == "assistant":
                previous_assistant = text or previous_assistant
                continue
            if not human or not text:
                continue
            signal = _make_signal(
                provider=provider, session_id=session_id, model=current_model or first_model,
                turn_index=line_number, user_text=text, previous_assistant_text=previous_assistant,
                evidence_ref=f"{source.resolve(strict=False)}#L{line_number}",
            )
            if signal:
                signals.append(signal)
    if first_model:
        signals = [
            InteractionSignal(**{
                **signal.to_dict(),
                "model": first_model if provider == "kimi_code" and not signal.model else signal.model or first_model,
            })
            for signal in signals
        ]
    return signals, {
        "transcript": str(source.resolve(strict=False)), "provider": provider,
        "session_id": session_id, "root_session": root_session,
        "parsed_lines": parsed_lines, "observed_user_turns": len(signals),
    }


def scan_interaction_history(paths: Iterable[Path]) -> tuple[list[InteractionSignal], list[dict[str, Any]]]:
    signals: list[InteractionSignal] = []
    reports: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for value in paths:
        path = Path(value)
        files = [path] if path.is_file() else path.rglob("*.jsonl") if path.is_dir() else []
        for transcript in files:
            lowered = str(transcript).lower()
            if ".grok" in lowered and transcript.name != "chat_history.jsonl":
                continue
            if ".kimi-code" in lowered and not (
                transcript.name == "wire.jsonl" and transcript.parent.name == "main"
            ):
                continue
            if "subagents" in transcript.parts:
                continue
            key = str(transcript.resolve(strict=False)).lower()
            if key in seen_paths:
                continue
            seen_paths.add(key)
            rows, report = scan_transcript_interactions(transcript)
            signals.extend(rows)
            reports.append(report)
    return signals, reports


def summarize_interaction_signals(signals: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(signals)
    counts = Counter(str(row.get("signal_type") or "unknown") for row in rows)
    correction_types = {
        "explicit_criticism", "rework_request", "clarification_cost", "mixed_feedback",
    }
    followups = sum(bool(row.get("preceded_by_assistant")) for row in rows)
    corrections = sum(str(row.get("signal_type") or "") in correction_types for row in rows)
    return {
        "observed_user_turns": len(rows),
        "followup_user_turns": followups,
        "correction_signals": corrections,
        "correction_rate_per_100_followups": (
            round(100 * corrections / followups, 1) if followups else None
        ),
        "scored_user_reactions": sum(row.get("score") is not None for row in rows),
        "neutral_user_turns": sum(row.get("score") is None for row in rows),
        "unattributed_user_turns": sum(not row.get("model") for row in rows),
        "by_signal_type": dict(sorted(counts.items())),
    }


def read_interaction_signals(*, data_dir: Path) -> list[dict[str, Any]]:
    path = Path(data_dir) / INTERACTION_SIGNALS_FILENAME
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for raw in stream:
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("schema") == INTERACTION_SIGNAL_SCHEMA:
                rows.append(row)
    return rows


def append_interaction_signals(signals: Iterable[InteractionSignal], *, data_dir: Path) -> tuple[int, int]:
    destination = Path(data_dir) / INTERACTION_SIGNALS_FILENAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    existing = read_interaction_signals(data_dir=data_dir)
    seen = {str(row.get("id") or "") for row in existing}
    appended = 0
    duplicates = 0
    with destination.open("a", encoding="utf-8", newline="\n") as stream:
        for signal in signals:
            if signal.id in seen:
                duplicates += 1
                continue
            stream.write(json.dumps(signal.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
            seen.add(signal.id)
            appended += 1
    return appended, duplicates


def weighted_signal_metric(signals: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    all_rows = [dict(row) for row in signals]
    rows = [row for row in all_rows if row.get("score") is not None and float(row.get("weight") or 0) > 0]
    total_weight = sum(float(row["weight"]) for row in rows)
    score = (
        sum(float(row["score"]) * float(row["weight"]) for row in rows) / total_weight
        if total_weight else None
    )
    sessions = {str(row.get("session_id") or "") for row in all_rows if row.get("session_id")}
    correction_types = {
        "explicit_criticism", "rework_request", "clarification_cost", "mixed_feedback",
    }
    followups = sum(bool(row.get("preceded_by_assistant")) for row in all_rows)
    corrections = sum(str(row.get("signal_type") or "") in correction_types for row in all_rows)
    return {
        "score": round(score, 1) if score is not None else None,
        "signals": len(rows),
        "observed_turns": len(all_rows),
        "followup_turns": followups,
        "correction_signals": corrections,
        "correction_rate_per_100_followups": (
            round(100 * corrections / followups, 1) if followups else None
        ),
        "acknowledgements": sum(
            str(row.get("signal_type") or "") in {"acceptance", "continuation"}
            for row in all_rows
        ),
        "effective_weight": round(total_weight, 2),
        "sessions": len(sessions),
        "by_type": dict(sorted(Counter(str(row.get("signal_type") or "unknown") for row in all_rows).items())),
        "interpretation": "diagnostic_correction_burden_not_quality_score",
    }


__all__ = [
    "INTERACTION_SIGNAL_SCHEMA", "INTERACTION_SIGNALS_FILENAME", "InteractionSignal",
    "append_interaction_signals", "classify_user_reaction", "extract_registry_interaction_signals",
    "infer_text_task_type", "read_interaction_signals", "scan_interaction_history",
    "scan_transcript_interactions", "summarize_interaction_signals", "weighted_signal_metric",
]
