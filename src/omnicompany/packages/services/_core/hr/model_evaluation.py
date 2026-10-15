# [OMNI] material_id="material:core.hr.model_evaluation.py" origin=codex ts=2026-08-18 type=module status=active agent=codex
# [OMNI] summary="按模型与任务类型汇总真实工作、用户评价和辅助互评，生成不可强排的证据矩阵与报告"
# [OMNI] tags=hr,evaluation,model,task,user-feedback,evidence,report
"""Evidence-first batch evaluation of models observed in the real workspace.

This module deliberately does not build a global leaderboard.  It joins four
different channels without blending their meaning:

* session registry: a model/provider was available or a PTY existed;
* HR events: work and execution signals actually observed by collectors;
* explicit user feedback: the primary quality verdict;
* peer reviews: auxiliary evidence that cannot override user feedback.

Task classification is deterministic and intentionally conservative.  It is a
workload slicing aid, not an LLM judgement about the quality of a task.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html import escape
from math import log1p, sqrt
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .evaluation_signals import (
    extract_registry_interaction_signals,
    summarize_interaction_signals,
    weighted_signal_metric,
)
from .note import HrNote
from .schema import HrEvent


EVALUATION_SCHEMA = "omnicompany.hr-model-evaluation.v5"
DEFAULT_FAMILIES: tuple[str, ...] = (
    "grok", "gpt", "glm", "kimi", "deepseek", "claude", "qwen",
)
TASK_TYPES: tuple[str, ...] = (
    "frontend",
    "backend",
    "testing",
    "configuration",
    "documentation",
    "governance",
    "data",
    "general",
)

METHODOLOGY_REFERENCES: tuple[dict[str, str], ...] = (
    {
        "name": "HELM",
        "url": "https://crfm.stanford.edu/helm/index.html",
        "adopted": "按场景/任务与多维指标分开报告，不用单一准确率代替全部表现",
    },
    {
        "name": "lm-evaluation-harness task guide",
        "url": "https://github.com/EleutherAI/lm-evaluation-harness/blob/main/docs/task_guide.md",
        "adopted": "固定任务定义、版本与样本，使一次评价可以重放和复算",
    },
    {
        "name": "Inspect AI evaluation logs",
        "url": "https://inspect.aisi.org.uk/eval-logs.html",
        "adopted": "保留逐样本日志和证据引用，允许事后重新评分而不重跑工作",
    },
    {
        "name": "SWE-bench harness",
        "url": "https://www.swebench.com/SWE-bench/api/harness/",
        "adopted": "优先复用真实执行证据与权威检查器，保留逐实例结果",
    },
    {
        "name": "Chatbot Arena style control",
        "url": "https://www.lmsys.org/blog/2024-08-28-style-control/",
        "adopted": "偏好评价可能受长度和格式影响，因此用户原话与任务结果必须保留",
    },
    {
        "name": "PaperBench",
        "url": "https://openai.com/index/paperbench/",
        "adopted": "复杂工作拆成可检查子项；模型裁判必须接受人类/权威证据校准",
    },
)


@dataclass
class _ModelAccumulator:
    model: str
    family: str
    providers: set[str] = field(default_factory=set)
    event_sessions: set[str] = field(default_factory=set)
    registry_sessions: set[str] = field(default_factory=set)
    event_counts: Counter[str] = field(default_factory=Counter)
    task_writes: Counter[str] = field(default_factory=Counter)
    task_event_counts: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    top_targets: Counter[str] = field(default_factory=Counter)
    event_refs: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    user_rows: list[dict[str, Any]] = field(default_factory=list)
    interaction_rows: list[dict[str, Any]] = field(default_factory=list)
    ignored_user_rows: int = 0
    peer_rows: list[dict[str, Any]] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def infer_model_family(model: str, provider: str = "") -> str:
    """Return a broad family label without inventing an upstream model id."""
    model_text = str(model or "").strip().lower()
    provider_text = str(provider or "").strip().lower()
    if "grok" in model_text:
        return "grok"
    if "deepseek" in model_text:
        return "deepseek"
    if "glm" in model_text or "zhipu" in model_text:
        return "glm"
    if "kimi" in model_text or model_text.startswith("k3"):
        return "kimi"
    if any(token in model_text for token in ("claude", "sonnet", "opus", "fable")):
        return "claude"
    if any(token in model_text for token in ("gpt", "codex")):
        return "gpt"
    if "qwen" in model_text:
        return "qwen"
    if "gemini" in model_text:
        return "gemini"
    if model_text not in {"", "-", "unknown", "default", "(local default)"}:
        return "unknown"
    if "grok" in provider_text:
        return "grok"
    if provider_text == "dsh" or "deepseek" in provider_text:
        return "deepseek"
    if "glm" in provider_text or "zhipu" in provider_text:
        return "glm"
    if "kimi" in provider_text:
        return "kimi"
    if "claude" in provider_text:
        return "claude"
    if "codex" in provider_text:
        return "gpt"
    if "gemini" in provider_text:
        return "gemini"
    if "qwen" in provider_text:
        return "qwen"
    return "unknown"


def infer_task_type(target: str) -> str:
    """Classify a written target for cohort slicing; unknowns stay ``general``."""
    normalized = str(target or "").replace("\\", "/").lower()
    name = normalized.rsplit("/", 1)[-1]
    suffix = Path(name).suffix.lower()
    segments = set(part for part in normalized.split("/") if part)

    if (
        "tests" in segments
        or "test" in segments
        or name.startswith("test_")
        or ".test." in name
        or ".spec." in name
        or "fixture" in normalized
        or "e2e" in normalized
    ):
        return "testing"
    if (
        suffix in {".xls", ".xlsx", ".xlsm", ".csv", ".toml", ".ini"}
        or "config" in normalized
        or "settings" in normalized
        or "catalog.json" in normalized
    ):
        return "configuration"
    if (
        suffix in {".tsx", ".jsx", ".vue", ".css", ".scss", ".sass", ".html"}
        or any(part in segments for part in ("frontend", "notes-web", "web", "ui", "views"))
    ):
        return "frontend"
    if (
        "agents.md" in name
        or "guardian" in normalized
        or "governance" in normalized
        or "/skills/" in f"/{normalized}/"
        or "registry" in normalized
    ):
        return "governance"
    if suffix in {".md", ".mdx", ".rst"} or any(
        part in segments for part in ("docs", "plans", "research", "reports")
    ):
        return "documentation"
    if suffix in {".py", ".go", ".rs", ".java", ".cs"} or any(
        part in segments for part in ("backend", "server", "services", "api")
    ):
        return "backend"
    if suffix in {".jsonl", ".db", ".sqlite", ".parquet"} or "data" in segments:
        return "data"
    return "general"


def build_model_aliases(catalog_models: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for row in catalog_models:
        model_id = str(row.get("id") or "").strip()
        if not model_id:
            continue
        aliases[model_id.lower()] = model_id
        for alias in row.get("aliases") or []:
            aliases[str(alias).strip().lower()] = model_id
    return aliases


def _canonical_model(model: str, aliases: Mapping[str, str]) -> str:
    value = str(model or "unknown").strip() or "unknown"
    return aliases.get(value.lower(), value)


def _event_name(event: HrEvent) -> str:
    if event.kind == "write" and event.verdict == "applied":
        return "applied_writes"
    if event.kind == "denial" or event.verdict == "denied":
        return "denied"
    if event.kind == "stray":
        return "stray"
    if event.verdict == "error":
        return "errors"
    if event.kind == "command":
        return "commands"
    if event.kind == "commit":
        return "commits"
    return "other"


def _append_bounded(bucket: list[str], value: str, *, limit: int = 8) -> None:
    value = str(value or "").strip()
    if value and value not in bucket and len(bucket) < limit:
        bucket.append(value)


def _feedback_verdict(rows: Sequence[Mapping[str, Any]]) -> str:
    if not rows:
        return "no_user_verdict"
    counts = Counter(str(row.get("severity") or "") for row in rows)
    if counts["pass"] and (counts["imperfect"] or counts["severe"]):
        return "mixed_user_feedback"
    if counts["severe"]:
        return "user_reported_severe_issue"
    if counts["imperfect"]:
        return "user_reported_issue"
    return "user_reported_pass"


def _severity_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(str(row.get("severity") or "") for row in rows)
    return {
        "total": len(rows),
        "pass": counts["pass"],
        "imperfect": counts["imperfect"],
        "severe": counts["severe"],
    }


def _task_summary(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[str(row.get("task_type") or "general")].append(row)
    return [
        {"task_type": task_type, **_severity_summary(task_rows)}
        for task_type, task_rows in sorted(buckets.items())
    ]


SEVERITY_POINTS: Mapping[str, float] = {"pass": 1.0, "imperfect": 0.5, "severe": 0.0}


def _feedback_metric(
    rows: Sequence[Mapping[str, Any]], *, minimum_comparable_samples: int,
) -> dict[str, Any]:
    """Turn explicit ordinal verdicts into an auditable 0-100 index.

    The score is not an LLM judgement: pass/imperfect/severe map to 100/50/0.
    A Wilson interval is retained even for provisional samples so the UI can
    quantify current evidence without pretending that n=1 is a stable ranking.
    """
    valid = [row for row in rows if str(row.get("severity") or "") in SEVERITY_POINTS]
    total = len(valid)
    distribution = _severity_summary(valid)
    if not total:
        return {
            "score": None,
            "interval_95": None,
            "samples": 0,
            "sample_gap": minimum_comparable_samples,
            "status": "missing",
            "distribution": distribution,
        }
    successes = sum(SEVERITY_POINTS[str(row["severity"])] for row in valid)
    proportion = successes / total
    z = 1.96
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = z * sqrt(
        (proportion * (1 - proportion) + z * z / (4 * total)) / total
    ) / denominator
    interval = [round(max(0.0, centre - margin) * 100, 1), round(min(1.0, centre + margin) * 100, 1)]
    return {
        "score": round(proportion * 100, 1),
        "interval_95": interval,
        "samples": total,
        "sample_gap": max(0, minimum_comparable_samples - total),
        "status": "comparable" if total >= minimum_comparable_samples else "provisional",
        "distribution": distribution,
    }


def _dimension_metrics(
    rows: Sequence[Mapping[str, Any]], *, minimum_comparable_samples: int,
) -> dict[str, dict[str, Any]]:
    dimensions = sorted({str(row.get("dimension") or "quality") for row in rows})
    return {
        dimension: _feedback_metric(
            [row for row in rows if str(row.get("dimension") or "quality") == dimension],
            minimum_comparable_samples=minimum_comparable_samples,
        )
        for dimension in dimensions
    }


def _process_metric(work: Mapping[str, Any]) -> dict[str, Any]:
    successes = sum(int(work.get(key, 0)) for key in ("applied_writes", "commands", "commits"))
    errors = int(work.get("errors", 0))
    stray = int(work.get("stray", 0))
    operations = successes + errors
    denominator = successes + errors + 2 * stray
    score = round(100 * successes / denominator, 1) if denominator else None
    return {
        "score": score,
        "operations": operations,
        "successful_operations": successes,
        "errors": errors,
        "error_rate": round(100 * errors / operations, 2) if operations else None,
        "stray_findings": stray,
        "denials": int(work.get("denied", 0)),
        "interpretation": "execution_reliability_and_hygiene_not_semantic_quality",
    }


def _deep_metric(
    *,
    explicit_rows: Sequence[Mapping[str, Any]],
    interaction_rows: Sequence[Mapping[str, Any]],
    peer_rows: Sequence[Mapping[str, Any]],
    work: Mapping[str, Any],
    minimum_comparable_samples: int,
) -> dict[str, Any]:
    """Score only user evidence; keep process and peer channels diagnostic."""
    explicit = _feedback_metric(
        explicit_rows, minimum_comparable_samples=minimum_comparable_samples,
    )
    reactions = weighted_signal_metric(interaction_rows)
    peer = _feedback_metric(peer_rows, minimum_comparable_samples=minimum_comparable_samples)
    process = _process_metric(work)
    weighted: list[tuple[str, float, float]] = []
    if explicit["score"] is not None:
        weighted.append(("explicit_user", float(explicit["score"]), min(12.0, 4.0 * explicit["samples"])))
    total_weight = sum(weight for _, _, weight in weighted)
    score = (
        round(sum(score * weight for _, score, weight in weighted) / total_weight, 1)
        if total_weight else None
    )
    explicit_sessions = {
        str(row.get("actor_id") or "").rsplit(":", 1)[-1]
        for row in explicit_rows if row.get("actor_id")
    }
    user_signals = int(explicit["samples"])
    user_sessions = len(explicit_sessions)
    comparable = score is not None and explicit["samples"] >= minimum_comparable_samples
    grade = (
        "A" if comparable and user_signals >= 5 and process["operations"] >= 20
        else "B" if comparable
        else "C" if user_signals or reactions["correction_signals"] or peer["samples"]
        else "D" if process["operations"]
        else "none"
    )
    return {
        "score": score,
        "status": "comparable" if comparable else "provisional" if score is not None else "missing",
        "evidence_grade": grade,
        "user_signals": user_signals,
        "user_sessions": user_sessions,
        "explicit_user": explicit,
        "implicit_user": reactions,
        "process": process,
        "peer_auxiliary": peer,
        "source_weights": {name: round(weight, 2) for name, _score, weight in weighted},
        "policy": "headline score uses explicit user verdicts only; interaction, process, and peer evidence remain diagnostics",
    }


def _session_rows(session_registry: Any) -> list[dict[str, Any]]:
    if isinstance(session_registry, Mapping):
        return [
            {"id": str(value.get("id") or key), **dict(value)}
            for key, value in session_registry.items()
            if isinstance(value, Mapping)
        ]
    return [dict(row) for row in session_registry or [] if isinstance(row, Mapping)]


def build_model_evaluation(
    *,
    events: Iterable[HrEvent],
    notes: Iterable[HrNote] = (),
    peer_reviews: Iterable[Mapping[str, Any]] = (),
    interaction_signals: Iterable[Mapping[str, Any]] = (),
    session_registry: Any = (),
    catalog_models: Iterable[Mapping[str, Any]] = (),
    requested_families: Sequence[str] = DEFAULT_FAMILIES,
    minimum_work_samples: int = 3,
    minimum_user_samples: int = 1,
    minimum_peer_samples: int = 3,
    minimum_comparable_samples: int = 3,
) -> dict[str, Any]:
    """Build a model/task evidence matrix from real workspace observations."""
    aliases = build_model_aliases(catalog_models)
    accumulators: dict[str, _ModelAccumulator] = {}

    def get_model(model: str, provider: str = "") -> _ModelAccumulator:
        canonical = _canonical_model(model, aliases)
        accumulator_key = canonical
        display_model = canonical
        if canonical == "unknown":
            normalized_provider = str(provider or "unknown").strip() or "unknown"
            accumulator_key = f"unknown@{normalized_provider}"
            display_model = accumulator_key
        row = accumulators.get(accumulator_key)
        if row is None:
            row = _ModelAccumulator(
                model=display_model,
                family=infer_model_family(canonical, provider),
            )
            accumulators[accumulator_key] = row
        if provider:
            row.providers.add(provider)
        if row.family == "unknown":
            row.family = infer_model_family(canonical, provider)
        return row

    event_total = 0
    session_models: dict[str, tuple[str, str]] = {}
    for event in events:
        event_total += 1
        row = get_model(event.model or "unknown", event.provider)
        if event.model:
            session_models[event.session_id] = (event.provider, event.model)
            session_models[f"{event.provider}:{event.session_id}"] = (event.provider, event.model)
        row.event_sessions.add(f"{event.provider}:{event.session_id}")
        name = _event_name(event)
        row.event_counts[name] += 1
        _append_bounded(row.event_refs[name], event.evidence_ref)
        if event.target:
            task_type = infer_task_type(event.target)
            row.task_event_counts[task_type][name] += 1
            if name == "applied_writes":
                row.task_writes[task_type] += 1
                row.top_targets[event.target] += 1

    registry_rows = _session_rows(session_registry)
    family_registry_counts: Counter[str] = Counter()
    family_registry_unknown_models: Counter[str] = Counter()
    for session in registry_rows:
        provider = str(session.get("provider") or "unknown")
        raw_model = str(session.get("model") or "").strip()
        if raw_model in {"", "-", "(local default)", "default"}:
            family = infer_model_family("", provider)
            family_registry_counts[family] += 1
            family_registry_unknown_models[family] += 1
            continue
        row = get_model(raw_model, provider)
        session_id = str(session.get("id") or session.get("session_id") or "")
        if session_id:
            row.registry_sessions.add(session_id)
        family_registry_counts[row.family] += 1

    registry_interactions, _registry_interaction_audit = extract_registry_interaction_signals(
        registry_rows, session_models=session_models,
    )
    persisted_rows = [dict(signal) for signal in interaction_signals]
    persisted_keys = {
        (str(row.get("model") or ""), str(row.get("user_text") or ""), str(row.get("signal_type") or ""))
        for row in persisted_rows
    }
    merged_interactions: dict[str, dict[str, Any]] = {
        signal.id: signal.to_dict()
        for signal in registry_interactions
        if (signal.model, signal.user_text, signal.signal_type) not in persisted_keys
    }
    for row in persisted_rows:
        signal_id = str(row.get("id") or row.get("evidence_ref") or "")
        if signal_id:
            merged_interactions[signal_id] = row
    interaction_audit = summarize_interaction_signals(merged_interactions.values())
    for signal in merged_interactions.values():
        if not signal.get("model"):
            continue
        row = get_model(str(signal["model"]), str(signal.get("provider") or ""))
        row.interaction_rows.append(signal)

    user_total = 0
    ignored_unattributed = 0
    ignored_low_confidence = 0
    for note in notes:
        if note.source != "user":
            continue
        user_total += 1
        if not note.actor_id:
            ignored_unattributed += 1
            continue
        parts = note.actor_id.split(":", 2)
        if len(parts) != 3 or not parts[1]:
            ignored_unattributed += 1
            continue
        row = get_model(parts[1], parts[0])
        if note.extraction_confidence != "high":
            row.ignored_user_rows += 1
            ignored_low_confidence += 1
            continue
        row.user_rows.append(
            {
                "id": note.id,
                "actor_id": note.actor_id,
                "task_type": note.task_type or "general",
                "dimension": note.dimension,
                "severity": note.severity,
                "note": note.note,
                "source_ref": note.source_ref,
                "evidence_refs": list(note.evidence_refs),
            }
        )

    peer_total = 0
    for review in peer_reviews:
        subject_model = str(review.get("subject_model") or "").strip()
        if not subject_model:
            continue
        peer_total += 1
        row = get_model(subject_model)
        row.peer_rows.append(
            {
                "id": str(review.get("id") or ""),
                "task_type": str(review.get("task_type") or "general"),
                "severity": str(review.get("severity") or ""),
                "reviewer_model": str(review.get("reviewer_model") or ""),
                "summary": str(review.get("summary") or review.get("note") or ""),
                "evidence_refs": list(review.get("evidence") or []),
            }
        )

    model_cards: list[dict[str, Any]] = []
    for _model_key, row in sorted(accumulators.items(), key=lambda item: item[0].lower()):
        if row.family not in requested_families and requested_families:
            continue
        counts = {
            key: int(row.event_counts.get(key, 0))
            for key in ("applied_writes", "commands", "commits", "denied", "errors", "stray", "other")
        }
        user_summary = _severity_summary(row.user_rows)
        peer_summary = _severity_summary(row.peer_rows)
        model_cards.append(
            {
                "model": row.model,
                "family": row.family,
                "providers": sorted(row.providers),
                "coverage": {
                    "registry_sessions_with_model": len(row.registry_sessions),
                    "event_sessions": len(row.event_sessions),
                    "work_evidence_status": (
                        "sufficient_for_workload_description"
                        if counts["applied_writes"] >= minimum_work_samples
                        else "sparse_work_evidence"
                        if sum(counts.values())
                        else "registry_only"
                        if row.registry_sessions
                        else "evaluation_only_no_work_events"
                        if row.user_rows or row.peer_rows
                        else "no_work_evidence"
                    ),
                },
                "observed_work": {
                    "event_counts": counts,
                    "task_writes": [
                        {"task_type": task, "applied_writes": count}
                        for task, count in sorted(row.task_writes.items())
                    ],
                    "task_metrics": [
                        {
                            "task_type": task,
                            **{
                                key: int(task_counts.get(key, 0))
                                for key in (
                                    "applied_writes", "commands", "commits", "denied",
                                    "errors", "stray", "other",
                                )
                            },
                        }
                        for task, task_counts in sorted(row.task_event_counts.items())
                    ],
                    "top_targets": [
                        {"target": target, "applied_writes": count}
                        for target, count in row.top_targets.most_common(8)
                    ],
                    "representative_event_refs": dict(row.event_refs),
                    "interpretation": "execution_and_workload_signals_not_quality_score",
                },
                "user_feedback": {
                    "role": "primary",
                    "status": (
                        "observed" if user_summary["total"] >= minimum_user_samples else "missing"
                    ),
                    "verdict": _feedback_verdict(row.user_rows),
                    "summary": user_summary,
                    "by_task": _task_summary(row.user_rows),
                    "rows": row.user_rows,
                    "ignored_non_high_confidence": row.ignored_user_rows,
                },
                "user_reactions": {
                    "role": "diagnostic_correction_burden",
                    "interpretation": "heuristic interaction signals never enter the headline quality score",
                    "summary": weighted_signal_metric(row.interaction_rows),
                    "observed_turns": len(row.interaction_rows),
                    "neutral_turns": sum(item.get("score") is None for item in row.interaction_rows),
                    "rows": row.interaction_rows,
                },
                "peer_review": {
                    "role": "auxiliary",
                    "can_override_user_feedback": False,
                    "status": (
                        "observed" if peer_summary["total"] >= minimum_peer_samples else "sparse_or_missing"
                    ),
                    "summary": peer_summary,
                    "by_task": _task_summary(row.peer_rows),
                    "rows": row.peer_rows,
                },
                "speed": {
                    "status": "user_observed"
                    if any(item["dimension"] == "speed" for item in row.user_rows)
                    else "insufficient",
                    "reason": (
                        "explicit_user_feedback_exists"
                        if any(item["dimension"] == "speed" for item in row.user_rows)
                        else "active work is not separable from queueing, pauses, and external waits"
                    ),
                },
                "quality_score": None,
                "global_rank": None,
            }
        )

    family_cards: list[dict[str, Any]] = []
    for family in requested_families:
        matching = [row for row in model_cards if row["family"] == family]
        event_sessions = sum(row["coverage"]["event_sessions"] for row in matching)
        applied_writes = sum(
            row["observed_work"]["event_counts"]["applied_writes"] for row in matching
        )
        user_rows = sum(row["user_feedback"]["summary"]["total"] for row in matching)
        peer_rows = sum(row["peer_review"]["summary"]["total"] for row in matching)
        models_with_user_feedback = [
            row["model"] for row in matching if row["user_feedback"]["summary"]["total"]
        ]
        registry_count = family_registry_counts[family]
        if not registry_count:
            registry_count = sum(row["coverage"]["registry_sessions_with_model"] for row in matching)
        family_cards.append(
            {
                "family": family,
                "registry_sessions": registry_count,
                "registry_sessions_without_model_id": family_registry_unknown_models[family],
                "event_sessions": event_sessions,
                "applied_writes": applied_writes,
                "models_with_evidence": [row["model"] for row in matching],
                "models_with_user_feedback": models_with_user_feedback,
                "user_feedback_rows": user_rows,
                "peer_review_rows": peer_rows,
                "evidence_status": (
                    "user_evaluated"
                    if user_rows
                    and len(models_with_user_feedback) == len(matching)
                    and not family_registry_unknown_models[family]
                    else "partially_user_evaluated"
                    if user_rows
                    else "work_observed_no_user_verdict"
                    if applied_writes
                    else "execution_attempts_only_no_user_verdict"
                    if event_sessions
                    else "registry_only_no_attributable_work"
                    if registry_count
                    else "not_observed"
                ),
            }
        )

    user_cohorts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    peer_cohorts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    workload_cohorts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for card in model_cards:
        for task in card["user_feedback"]["by_task"]:
            if task["total"] >= minimum_user_samples:
                user_cohorts[task["task_type"]].append({"model": card["model"], **task})
        for task in card["peer_review"]["by_task"]:
            if task["total"] >= minimum_peer_samples:
                peer_cohorts[task["task_type"]].append({"model": card["model"], **task})
        for task in card["observed_work"]["task_writes"]:
            if task["applied_writes"] >= minimum_work_samples:
                workload_cohorts[task["task_type"]].append({"model": card["model"], **task})

    def cohort_rows(source: Mapping[str, list[dict[str, Any]]], role: str) -> list[dict[str, Any]]:
        return [
            {
                "task_type": task,
                "role": role,
                "models": rows,
                "comparison_status": "eligible_same_task_evidence"
                if len(rows) >= 2
                else "insufficient_models",
            }
            for task, rows in sorted(source.items())
        ]

    observed_task_types = set(TASK_TYPES)
    for card in model_cards:
        observed_task_types.update(item["task_type"] for item in card["observed_work"]["task_metrics"])
        observed_task_types.update(item["task_type"] for item in card["user_feedback"]["rows"])
        observed_task_types.update(item["task_type"] for item in card["peer_review"]["rows"])

    def task_cell(card: Mapping[str, Any], task_type: str) -> dict[str, Any]:
        work = next(
            (
                dict(item) for item in card["observed_work"]["task_metrics"]
                if item["task_type"] == task_type
            ),
            {
                "task_type": task_type, "applied_writes": 0, "commands": 0, "commits": 0,
                "denied": 0, "errors": 0, "stray": 0, "other": 0,
            },
        )
        user_rows = [
            item for item in card["user_feedback"]["rows"] if item["task_type"] == task_type
        ]
        peer_rows = [
            item for item in card["peer_review"]["rows"] if item["task_type"] == task_type
        ]
        interaction_rows = [
            item for item in card["user_reactions"]["rows"] if item["task_type"] == task_type
        ]
        user_metric = _feedback_metric(
            user_rows, minimum_comparable_samples=minimum_comparable_samples,
        )
        peer_metric = _feedback_metric(
            peer_rows, minimum_comparable_samples=minimum_comparable_samples,
        )
        work_samples = sum(int(work.get(key, 0)) for key in ("applied_writes", "commands", "commits"))
        deep_metric = _deep_metric(
            explicit_rows=user_rows,
            interaction_rows=interaction_rows,
            peer_rows=peer_rows,
            work=work,
            minimum_comparable_samples=minimum_comparable_samples,
        )
        status = (
            "comparable" if deep_metric["status"] == "comparable"
            else "provisional" if deep_metric["score"] is not None
            else "needs_user_feedback" if work_samples
            else "unobserved"
        )
        return {
            "model": card["model"],
            "family": card["family"],
            "task_type": task_type,
            "status": status,
            "user": user_metric,
            "user_dimensions": _dimension_metrics(
                user_rows, minimum_comparable_samples=minimum_comparable_samples,
            ),
            "user_reactions": weighted_signal_metric(interaction_rows),
            "peer_auxiliary": peer_metric,
            "work": work,
            "deep_evaluation": deep_metric,
        }

    model_task_matrix: list[dict[str, Any]] = []
    all_model_cells: list[dict[str, Any]] = []
    for task_type in sorted(observed_task_types):
        cells = [task_cell(card, task_type) for card in model_cards]
        all_model_cells.extend(cells)
        rated = sorted(
            (cell for cell in cells if cell["deep_evaluation"]["score"] is not None),
            key=lambda cell: (
                {"A": 0, "B": 1, "C": 2, "D": 3, "none": 4}.get(
                    str(cell["deep_evaluation"]["evidence_grade"]), 4
                ),
                -float(cell["deep_evaluation"]["score"]),
                -int(cell["deep_evaluation"]["user_signals"]), cell["model"],
            ),
        )
        eligible = [cell for cell in rated if cell["status"] == "comparable"]
        model_task_matrix.append(
            {
                "task_type": task_type,
                "status": (
                    "comparable" if len(eligible) >= 2
                    else "provisional_pair" if len(rated) >= 2
                    else "collecting"
                ),
                "rated_models": len(rated),
                "comparable_models": len(eligible),
                "cells": cells,
                "within_task_order": [
                    {
                        "model": cell["model"], "family": cell["family"],
                        "score": cell["deep_evaluation"]["score"],
                        "interval_95": cell["user"]["interval_95"],
                        "samples": cell["deep_evaluation"]["user_signals"],
                        "evidence_grade": cell["deep_evaluation"]["evidence_grade"],
                        "source_weights": cell["deep_evaluation"]["source_weights"],
                        "status": cell["status"],
                    }
                    for cell in rated
                ],
                "eligible_ranking": [cell["model"] for cell in eligible] if len(eligible) >= 2 else [],
            }
        )

    family_task_matrix: list[dict[str, Any]] = []
    for task_type in sorted(observed_task_types):
        cells: list[dict[str, Any]] = []
        for family in requested_families:
            matching_models = [card for card in model_cards if card["family"] == family]
            user_rows = [
                row for card in matching_models for row in card["user_feedback"]["rows"]
                if row["task_type"] == task_type
            ]
            peer_rows = [
                row for card in matching_models for row in card["peer_review"]["rows"]
                if row["task_type"] == task_type
            ]
            interaction_rows = [
                row for card in matching_models for row in card["user_reactions"]["rows"]
                if row["task_type"] == task_type
            ]
            work_rows = [
                item for card in matching_models for item in card["observed_work"]["task_metrics"]
                if item["task_type"] == task_type
            ]
            work = {
                key: sum(int(item.get(key, 0)) for item in work_rows)
                for key in ("applied_writes", "commands", "commits", "denied", "errors", "stray", "other")
            }
            user_metric = _feedback_metric(
                user_rows, minimum_comparable_samples=minimum_comparable_samples,
            )
            peer_metric = _feedback_metric(
                peer_rows, minimum_comparable_samples=minimum_comparable_samples,
            )
            work_samples = sum(work[key] for key in ("applied_writes", "commands", "commits"))
            deep_metric = _deep_metric(
                explicit_rows=user_rows,
                interaction_rows=interaction_rows,
                peer_rows=peer_rows,
                work=work,
                minimum_comparable_samples=minimum_comparable_samples,
            )
            cells.append(
                {
                    "family": family,
                    "task_type": task_type,
                    "status": (
                        "comparable" if deep_metric["status"] == "comparable"
                        else "provisional" if deep_metric["score"] is not None
                        else "needs_user_feedback" if work_samples
                        else "unobserved"
                    ),
                    "user": user_metric,
                    "user_dimensions": _dimension_metrics(
                        user_rows, minimum_comparable_samples=minimum_comparable_samples,
                    ),
                    "peer_auxiliary": peer_metric,
                    "user_reactions": weighted_signal_metric(interaction_rows),
                    "work": work,
                    "deep_evaluation": deep_metric,
                    "models_observed": [card["model"] for card in matching_models],
                }
            )
        rated = sorted(
            (cell for cell in cells if cell["deep_evaluation"]["score"] is not None),
            key=lambda cell: (
                {"A": 0, "B": 1, "C": 2, "D": 3, "none": 4}.get(
                    str(cell["deep_evaluation"]["evidence_grade"]), 4
                ),
                -float(cell["deep_evaluation"]["score"]),
                -int(cell["deep_evaluation"]["user_signals"]), cell["family"],
            ),
        )
        eligible = [cell for cell in rated if cell["status"] == "comparable"]
        family_task_matrix.append(
            {
                "task_type": task_type,
                "status": (
                    "comparable" if len(eligible) >= 2
                    else "provisional_pair" if len(rated) >= 2
                    else "collecting"
                ),
                "rated_families": len(rated),
                "comparable_families": len(eligible),
                "cells": cells,
                "within_task_order": [
                    {
                        "family": cell["family"], "score": cell["deep_evaluation"]["score"],
                        "interval_95": cell["user"]["interval_95"],
                        "samples": cell["deep_evaluation"]["user_signals"],
                        "evidence_grade": cell["deep_evaluation"]["evidence_grade"],
                        "status": cell["status"],
                    }
                    for cell in rated
                ],
            }
        )

    rated_models_per_task = Counter(
        cell["task_type"] for cell in all_model_cells if cell["user"]["samples"]
    )
    sampling_plan = [
        {
            "model": cell["model"],
            "family": cell["family"],
            "task_type": cell["task_type"],
            "current_user_samples": cell["user"]["samples"],
            "missing_user_samples": cell["user"]["sample_gap"],
            "applied_writes": cell["work"]["applied_writes"],
            "priority": (
                3 if rated_models_per_task[cell["task_type"]] >= 2 else 2
            ) + (1 if cell["user"]["samples"] else 0),
            "next_action": (
                "对该模型完成同类真实任务后，记录用户 pass/imperfect/severe 与具体维度"
            ),
        }
        for cell in all_model_cells
        if cell["user"]["sample_gap"] > 0
        and (cell["work"]["applied_writes"] > 0 or cell["user"]["samples"] > 0)
    ]
    sampling_plan.sort(
        key=lambda row: (
            -int(row["priority"]), -int(row["current_user_samples"]),
            -int(row["applied_writes"]), row["task_type"], row["model"],
        )
    )

    return {
        "schema": EVALUATION_SCHEMA,
        "generated_at": _now_iso(),
        "policy": {
            "primary_evidence": "explicit_user_feedback",
            "auxiliary_evidence": "peer_review",
            "peer_review_can_override_user": False,
            "missing_feedback_is_not_pass": True,
            "cross_task_global_ranking": False,
            "cross_provider_denial_rate_ranking": False,
            "minimum_work_samples": minimum_work_samples,
            "minimum_user_samples": minimum_user_samples,
            "minimum_peer_samples": minimum_peer_samples,
            "minimum_comparable_samples": minimum_comparable_samples,
            "severity_points": {"pass": 100, "imperfect": 50, "severe": 0},
            "within_task_quantification": True,
            "deep_evaluation": {
                "explicit_user_weight_per_signal": 4.0,
                "interaction_reactions_in_headline_score": False,
                "process_in_headline_score": False,
                "peer_in_headline_score": False,
                "missing_feedback_is_neutral": False,
            },
        },
        "methodology_references": list(METHODOLOGY_REFERENCES),
        "coverage": {
            "registry_sessions": len(registry_rows),
            "event_rows": event_total,
            "user_feedback_rows": user_total,
            "user_feedback_ignored_unattributed": ignored_unattributed,
            "user_feedback_ignored_low_confidence": ignored_low_confidence,
            "peer_review_rows": peer_total,
            "interaction_signals": interaction_audit,
            "requested_families": list(requested_families),
        },
        "families": family_cards,
        "models": model_cards,
        "cohorts": {
            "user_feedback": cohort_rows(user_cohorts, "primary"),
            "peer_review": cohort_rows(peer_cohorts, "auxiliary"),
            "workload": cohort_rows(workload_cohorts, "descriptive_only"),
        },
        "comparisons": {
            "models_by_task": model_task_matrix,
            "families_by_task": family_task_matrix,
            "comparable_model_tasks": sum(row["status"] == "comparable" for row in model_task_matrix),
            "provisional_model_tasks": sum(row["status"] == "provisional_pair" for row in model_task_matrix),
            "comparable_family_tasks": sum(row["status"] == "comparable" for row in family_task_matrix),
            "provisional_family_tasks": sum(row["status"] == "provisional_pair" for row in family_task_matrix),
        },
        "sampling_plan": {
            "target_user_samples_per_model_task": minimum_comparable_samples,
            "open_cells": len(sampling_plan),
            "missing_user_samples": sum(int(row["missing_user_samples"]) for row in sampling_plan),
            "queue": sampling_plan,
        },
        "overall_score": None,
        "global_ranking": None,
        "caveats": [
            "会话登记只证明入口存在，不证明完成了工作。",
            "客观事件用于描述工作量和执行信号，不直接等价于质量。",
            "用户明确评价是质量裁决主证据；缺少评价不等于通过。",
            "模型互评是辅助证据，不能覆盖用户评价。",
            "速度只在用户明确反馈或存在可分离的 active-work 计时时讨论。",
            "不同任务、不同采集完备度的 provider 不生成统一总榜。",
        ],
    }


def render_model_evaluation_markdown(report: Mapping[str, Any]) -> str:
    """Render a compact human report while preserving evidence links in JSON."""
    coverage = report["coverage"]
    lines = [
        "---",
        "title: 首批多模型真实工作评价报告",
        "date: '2026-08-18'",
        "status: active",
        "---",
        "",
        '<!-- [OMNI] material_id="material:plans.agent-framework.llm-hr-performance-attribution.p6-report.md" origin=codex ts=2026-08-18T00:00:00+08:00 type=report status=active agent=codex -->',
        "",
        "# 首批多模型真实工作评价报告",
        "",
        f"生成时间：{report['generated_at']}",
        "",
        "## 结论口径",
        "",
        "这不是通用能力榜，也不输出跨任务总分。它回答的是：在当前工作环境里，",
        "各模型实际留下了什么工作证据，用户明确说过哪里好/不好，互评提供了什么辅助线索，",
        "以及哪些模型目前只有会话入口而没有足够可归责证据。",
        "",
        "- 用户真实评价是主证据；模型互评是辅助证据，不能覆盖用户评价。",
        "- 缺少用户评价不算通过。错误、拒绝和写入计数也不自动等同于质量。",
        "- 只允许在同任务 cohort 内讨论可比性；当前报告不生成全局排名。",
        "",
        "## 覆盖总览",
        "",
        f"- 会话登记：{coverage['registry_sessions']}",
        f"- HR 事件：{coverage['event_rows']}",
        f"- 用户评价：{coverage['user_feedback_rows']}（未归因 {coverage['user_feedback_ignored_unattributed']}）",
        f"- 模型互评：{coverage['peer_review_rows']}",
        "",
        "## 设施、产物与复现",
        "",
        "- 命令：`omni hr evaluate-models [--family ...] [--report ...] [--json-out ...]`。",
        "- 规则实现：`src/omnicompany/packages/services/_core/hr/model_evaluation.py`。",
        "- 完整证据快照：`P6-first-multi-model-evaluation.json`。",
        "- 人读报告：`P6-first-multi-model-evaluation-report.md`。",
        "- 验证：`tests/services/hr` 220 passed；Ruff 与 compileall 通过。",
        "",
        "| 家族 | 会话登记 | 无模型 ID 会话 | 有事件会话 | applied 写入操作 | 用户评价 | 互评 | 当前证据状态 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in report["families"]:
        lines.append(
            f"| {row['family']} | {row['registry_sessions']} | "
            f"{row['registry_sessions_without_model_id']} | {row['event_sessions']} | "
            f"{row['applied_writes']} | {row['user_feedback_rows']} | "
            f"{row['peer_review_rows']} | {row['evidence_status']} |"
        )

    lines.extend(
        [
            "",
            "## 模型 × 同任务量化",
            "",
            "用户主评分：通过 100、有问题 50、严重问题 0。分数必须和样本量、95% 区间一起读；",
            f"单格少于 {report['policy']['minimum_comparable_samples']} 条用户评价时只标暂定，不进入正式横评。",
            "",
            "| 任务 | 状态 | 暂定/正式顺序 |",
            "|---|---|---|",
        ]
    )
    for task in report["comparisons"]["models_by_task"]:
        if not task["within_task_order"]:
            continue
        ordering = "；".join(
            f"{item['model']} {item['score']}（用户信号={item['samples']}，证据={item.get('evidence_grade', 'C')}）"
            for item in task["within_task_order"]
        )
        lines.append(f"| {task['task_type']} | {task['status']} | {ordering} |")
    lines.extend(
        [
            "",
            f"补样队列：{report['sampling_plan']['open_cells']} 个模型×任务格仍缺 "
            f"{report['sampling_plan']['missing_user_samples']} 条用户评价。",
        ]
    )

    lines.extend(
        [
            "",
            "## 模型证据矩阵",
            "",
            "| 模型 | 家族 | 主要任务（按写入操作） | 事件会话 | applied 写入操作 | 错误信号 | 用户评价 P/I/S | 用户裁决 | 互评 P/I/S |",
            "|---|---|---|---:|---:|---:|---|---|---|",
        ]
    )
    for row in report["models"]:
        events = row["observed_work"]["event_counts"]
        user = row["user_feedback"]["summary"]
        peer = row["peer_review"]["summary"]
        main_tasks = sorted(
            row["observed_work"]["task_writes"],
            key=lambda item: int(item["applied_writes"]),
            reverse=True,
        )[:3]
        task_text = "、".join(
            f"{item['task_type']} {item['applied_writes']}" for item in main_tasks
        ) or "无有效写入"
        lines.append(
            f"| {row['model']} | {row['family']} | {task_text} | "
            f"{row['coverage']['event_sessions']} | "
            f"{events['applied_writes']} | {events['errors']} | "
            f"{user['pass']}/{user['imperfect']}/{user['severe']} | "
            f"{row['user_feedback']['verdict']} | "
            f"{peer['pass']}/{peer['imperfect']}/{peer['severe']} |"
        )

    lines.extend(["", "## 用户明确评价（主证据）", ""])
    user_models = [row for row in report["models"] if row["user_feedback"]["rows"]]
    if not user_models:
        lines.append("当前没有可归责且高置信的用户评价。")
    for model in user_models:
        lines.extend([f"### {model['model']}", ""])
        for row in model["user_feedback"]["rows"]:
            lines.append(
                f"- `{row['task_type']}/{row['dimension']}/{row['severity']}`：{row['note']} "
                f"（证据：`{row['source_ref']}`）"
            )
        lines.append("")

    lines.extend(["## 同任务可比性", ""])
    for channel, label in (
        ("user_feedback", "用户评价"),
        ("peer_review", "模型互评"),
        ("workload", "工作量描述"),
    ):
        rows = report["cohorts"][channel]
        if not rows:
            lines.append(f"- {label}：没有达到门槛的 task cohort。")
            continue
        for row in rows:
            names = "、".join(item["model"] for item in row["models"])
            lines.append(
                f"- {label} / `{row['task_type']}`：{row['comparison_status']}；模型：{names}"
            )

    lines.extend(["", "## 方法来源与本地采用点", ""])
    for ref in report["methodology_references"]:
        lines.append(f"- [{ref['name']}]({ref['url']})：{ref['adopted']}。")

    lines.extend(["", "## 证据缺口", ""])
    for caveat in report["caveats"]:
        lines.append(f"- {caveat}")
    lines.append("")
    return "\n".join(lines)


def _render_model_evaluation_html_legacy(report: Mapping[str, Any]) -> str:
    """Render a self-contained review artifact; JSON remains the machine truth."""
    coverage = report["coverage"]

    def h(value: Any) -> str:
        return escape(str(value if value is not None else ""), quote=True)

    status_labels = {
        "user_evaluated": "已有用户裁决",
        "partially_user_evaluated": "部分用户裁决",
        "work_observed_no_user_verdict": "有工作证据，待用户评价",
        "execution_attempts_only_no_user_verdict": "仅执行尝试，待用户评价",
        "registry_only_no_attributable_work": "仅登记，无可归责工作",
        "not_observed": "未观测到",
        "user_reported_pass": "用户明确通过",
        "user_reported_issue": "用户指出问题",
        "user_reported_severe_issue": "用户指出严重问题",
        "mixed_user_feedback": "用户评价有好有坏",
        "no_user_verdict": "待用户评价",
    }

    family_cards: list[str] = []
    for row in report["families"]:
        status = str(row["evidence_status"])
        tone = "good" if status == "user_evaluated" else "warn" if status != "not_observed" else "muted"
        models = "、".join(row["models_with_evidence"]) or "尚无"
        family_cards.append(f"""
          <article class="family-card {tone}" data-family="{h(row['family'])}">
            <div class="family-head"><h3>{h(row['family']).upper()}</h3><span>{h(status_labels.get(status, status))}</span></div>
            <div class="family-metrics">
              <b>{row['event_sessions']}</b><small>工作会话</small>
              <b>{row['applied_writes']}</b><small>生效写入</small>
              <b>{row['user_feedback_rows']}</b><small>用户评价</small>
            </div>
            <p>模型：{h(models)}</p>
          </article>""")

    model_rows: list[str] = []
    model_details: list[str] = []
    for row in report["models"]:
        events = row["observed_work"]["event_counts"]
        user = row["user_feedback"]["summary"]
        peer = row["peer_review"]["summary"]
        verdict = str(row["user_feedback"]["verdict"])
        tasks = sorted(
            row["observed_work"]["task_writes"],
            key=lambda item: int(item["applied_writes"]), reverse=True,
        )
        task_text = "、".join(
            f"{h(item['task_type'])} {item['applied_writes']}" for item in tasks[:3]
        ) or "无有效写入"
        model_rows.append(f"""
          <tr data-family="{h(row['family'])}">
            <td><strong>{h(row['model'])}</strong><small>{h(' / '.join(row['providers']))}</small></td>
            <td>{h(row['family'])}</td><td>{task_text}</td>
            <td class="num">{row['coverage']['event_sessions']}</td>
            <td class="num">{events['applied_writes']}</td><td class="num">{events['errors']}</td>
            <td>{user['pass']} / {user['imperfect']} / {user['severe']}</td>
            <td><span class="verdict {('positive' if verdict == 'user_reported_pass' else 'negative' if verdict in {'user_reported_issue','user_reported_severe_issue'} else 'pending')}">{h(status_labels.get(verdict, verdict))}</span></td>
            <td>{peer['pass']} / {peer['imperfect']} / {peer['severe']}</td>
          </tr>""")

        task_items = "".join(
            f"<li><b>{h(item['task_type'])}</b><span>{item['applied_writes']} 次写入</span></li>"
            for item in tasks
        ) or "<li><span>没有可归责的有效写入</span></li>"
        targets = "".join(
            f"<li><code>{h(item['target'])}</code><span>{item['applied_writes']}</span></li>"
            for item in row["observed_work"]["top_targets"][:5]
        ) or "<li><span>没有目标文件证据</span></li>"
        feedback = "".join(f"""
            <blockquote class="feedback {h(item['severity'])}">
              <div><b>{h(item['task_type'])} · {h(item['dimension'])} · {h(item['severity'])}</b></div>
              <p>{h(item['note'])}</p><cite>{h(item['source_ref'])}</cite>
            </blockquote>""" for item in row["user_feedback"]["rows"])
        if not feedback:
            feedback = '<p class="empty">历史记录中没有可高置信归因的用户评价；这不等于通过。</p>'
        peer_items = "".join(f"""
            <li><b>{h(item['severity'])}</b> · {h(item['task_type'])} · {h(item['reviewer_model'])}<br>
            <span>{h(item['summary'])}</span></li>""" for item in row["peer_review"]["rows"])
        if not peer_items:
            peer_items = "<li><span>暂无互评证据</span></li>"
        speed_text = (
            "有用户明确速度反馈，请看下方用户原评。"
            if row["speed"]["status"] == "user_observed"
            else "无法把真实工作时长与排队、暂停、外部等待分离，暂不判断快慢。"
        )
        hygiene = events["stray"]
        model_details.append(f"""
          <article class="model-card" data-family="{h(row['family'])}">
            <header><div><span class="eyebrow">{h(row['family'])}</span><h3>{h(row['model'])}</h3></div>
              <span class="verdict {('positive' if verdict == 'user_reported_pass' else 'negative' if verdict in {'user_reported_issue','user_reported_severe_issue'} else 'pending')}">{h(status_labels.get(verdict, verdict))}</span></header>
            <div class="signal-grid">
              <div><b>{events['applied_writes']}</b><span>生效写入</span></div>
              <div><b>{events['commands']}</b><span>命令</span></div>
              <div><b>{events['errors']}</b><span>错误信号</span></div>
              <div><b>{hygiene}</b><span>垃圾/游离物</span></div>
            </div>
            <div class="detail-grid">
              <section><h4>搞了什么</h4><ul class="split-list">{task_items}</ul><h4>主要落点</h4><ul class="target-list">{targets}</ul></section>
              <section><h4>哪里好 / 哪里有问题（用户主评）</h4>{feedback}<h4>快不快</h4><p>{h(speed_text)}</p></section>
              <section><h4>模型互评（辅助）</h4><ul>{peer_items}</ul><p class="policy">互评不能覆盖用户评价。</p></section>
            </div>
          </article>""")

    references = "".join(
        f'<li><a href="{h(item["url"])}">{h(item["name"])}</a><span>{h(item["adopted"])}</span></li>'
        for item in report["methodology_references"]
    )
    caveats = "".join(f"<li>{h(item)}</li>" for item in report["caveats"])
    family_buttons = "".join(
        f'<button type="button" data-filter="{h(row["family"])}">{h(row["family"]).upper()}</button>'
        for row in report["families"]
    )

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LLM 全模型真实工作评价报告</title>
<!-- [OMNI] material_id="material:plans.agent-framework.llm-hr-performance-attribution.p7-full-report.html" origin=codex ts=2026-08-18T00:00:00+08:00 type=report status=active agent=codex -->
<style>
:root{{--bg:#0b1020;--panel:#121a2d;--panel2:#182239;--line:#2a3856;--text:#edf2ff;--muted:#9daccc;--cyan:#65d9ff;--green:#6ee7a8;--amber:#ffc86b;--red:#ff7d8f;--shadow:0 18px 50px #02061166}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 10% 0,#172748 0,transparent 35%),var(--bg);color:var(--text);font:15px/1.65 Inter,"Segoe UI","Microsoft YaHei",sans-serif}}main{{max-width:1440px;margin:auto;padding:48px 28px 80px}}h1{{font-size:clamp(32px,5vw,64px);line-height:1.05;margin:10px 0 18px;letter-spacing:-.04em}}h2{{font-size:28px;margin:52px 0 18px}}h3,h4,p{{margin-top:0}}a{{color:var(--cyan)}}code{{font-family:"Cascadia Code",monospace;color:#c9d9ff;overflow-wrap:anywhere}}.kicker,.eyebrow{{text-transform:uppercase;letter-spacing:.16em;color:var(--cyan);font-weight:700;font-size:12px}}.lede{{max-width:900px;color:#c5d1eb;font-size:18px}}.policy-banner{{display:flex;gap:18px;flex-wrap:wrap;margin:28px 0;padding:18px 22px;border:1px solid #315077;background:#10213a;border-radius:16px}}.policy-banner b{{color:var(--green)}}.metrics{{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:14px;margin:28px 0}}.metric,.family-card,.model-card,.method{{background:linear-gradient(145deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow)}}.metric{{padding:20px}}.metric b{{display:block;font-size:32px}}.metric span,.family-card p,small,.empty,.policy{{color:var(--muted)}}.families{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}}.family-card{{padding:18px;border-top:3px solid var(--amber)}}.family-card.good{{border-top-color:var(--green)}}.family-card.muted{{border-top-color:#66728e}}.family-head{{display:flex;justify-content:space-between;gap:12px;align-items:center}}.family-head h3{{margin:0;font-size:22px}}.family-head span{{color:var(--muted);font-size:12px}}.family-metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:18px 0}}.family-metrics b,.family-metrics small{{display:block}}.family-metrics b{{font-size:22px}}.filters{{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0 18px}}button{{border:1px solid var(--line);background:#111a2c;color:var(--text);padding:8px 13px;border-radius:999px;cursor:pointer}}button.active{{background:var(--cyan);color:#07111d;border-color:var(--cyan)}}.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:18px}}table{{width:100%;border-collapse:collapse;background:#0f1728;min-width:1050px}}th,td{{padding:13px 14px;border-bottom:1px solid #22304a;text-align:left;vertical-align:top}}th{{position:sticky;top:0;background:#182239;color:#b9c8e8;font-size:12px;text-transform:uppercase}}td small{{display:block}}td.num{{text-align:right;font-variant-numeric:tabular-nums}}.verdict{{display:inline-block;padding:5px 9px;border-radius:999px;font-size:12px;white-space:nowrap;background:#27334c;color:#d6def0}}.verdict.positive{{background:#153c32;color:var(--green)}}.verdict.negative{{background:#4a202c;color:#ffadb8}}.verdict.pending{{background:#42351e;color:var(--amber)}}.model-card{{padding:24px;margin:16px 0}}.model-card>header{{display:flex;justify-content:space-between;align-items:start;gap:16px}}.model-card h3{{font-size:25px;margin:2px 0 16px}}.signal-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:22px}}.signal-grid div{{background:#0d1526;border:1px solid #22304a;border-radius:12px;padding:12px}}.signal-grid b,.signal-grid span{{display:block}}.signal-grid b{{font-size:22px}}.signal-grid span{{color:var(--muted);font-size:12px}}.detail-grid{{display:grid;grid-template-columns:1fr 1.4fr 1fr;gap:24px}}ul{{padding-left:20px}}.split-list,.target-list{{list-style:none;padding:0}}.split-list li,.target-list li{{display:flex;justify-content:space-between;gap:10px;border-bottom:1px solid #26334d;padding:7px 0}}blockquote.feedback{{margin:10px 0;padding:12px 14px;border-left:3px solid var(--amber);background:#0e1729;border-radius:0 10px 10px 0}}blockquote.feedback.pass{{border-color:var(--green)}}blockquote.feedback.severe{{border-color:var(--red)}}blockquote p{{margin:5px 0}}cite{{color:var(--muted);font-size:11px;overflow-wrap:anywhere}}.method{{padding:22px}}.method li{{margin:10px 0}}.method li span{{display:block;color:var(--muted)}}footer{{margin-top:50px;color:var(--muted);border-top:1px solid var(--line);padding-top:18px}}[hidden]{{display:none!important}}@media(max-width:1000px){{.families{{grid-template-columns:repeat(2,1fr)}}.detail-grid{{grid-template-columns:1fr}}}}@media(max-width:650px){{main{{padding:30px 16px}}.metrics,.families,.signal-grid{{grid-template-columns:1fr 1fr}}}}
</style></head><body><main>
<header><span class="kicker">OmniCompany · LLM HR · P7</span><h1>全模型真实工作评价</h1><p class="lede">回答“谁在我们的环境里做了什么、哪里好、哪里有问题、能不能判断快慢”。覆盖 Grok、GPT、GLM、Kimi、DeepSeek、Claude；用户历史评价是主证据，模型互评只作辅助。</p></header>
<div class="policy-banner"><span><b>不做总榜</b>：跨任务总分会掩盖任务差异。</span><span><b>缺评价 ≠ 通过</b>：只标“待用户评价”。</span><span><b>速度从严</b>：不能分离排队/暂停就不猜。</span></div>
<section class="metrics"><div class="metric"><b>{coverage['registry_sessions']}</b><span>会话登记</span></div><div class="metric"><b>{coverage['event_rows']}</b><span>可回链 HR 事件</span></div><div class="metric"><b>{coverage['user_feedback_rows']}</b><span>用户评价（主证据）</span></div><div class="metric"><b>{coverage['peer_review_rows']}</b><span>模型互评（辅助）</span></div></section>
<h2>六个目标模型家族</h2><section class="families">{''.join(family_cards)}</section>
<h2>模型 × 任务证据矩阵</h2><div class="filters"><button type="button" class="active" data-filter="all">全部</button>{family_buttons}</div>
<div class="table-wrap"><table><thead><tr><th>模型</th><th>家族</th><th>主要任务</th><th>工作会话</th><th>写入</th><th>错误</th><th>用户 P/I/S</th><th>用户裁决</th><th>互评 P/I/S</th></tr></thead><tbody>{''.join(model_rows)}</tbody></table></div>
<h2>逐模型解释</h2><section>{''.join(model_details)}</section>
<h2>方法与边界</h2><section class="method"><h3>参考的成熟做法</h3><ul>{references}</ul><h3>证据边界</h3><ul>{caveats}</ul></section>
<footer>生成时间：{h(report['generated_at'])} · 机器可复算真源见同批 JSON 快照 · 本报告不包含全局质量分或跨任务排名。</footer>
</main><script>const buttons=[...document.querySelectorAll('[data-filter]')];buttons.forEach(b=>b.addEventListener('click',()=>{{buttons.forEach(x=>x.classList.remove('active'));b.classList.add('active');const f=b.dataset.filter;document.querySelectorAll('tr[data-family],article.model-card').forEach(x=>x.hidden=f!=='all'&&x.dataset.family!==f)}}));</script></body></html>"""


def render_model_evaluation_html(report: Mapping[str, Any]) -> str:
    """Render a conclusion-first visual report with evidence available on demand."""
    coverage = report["coverage"]
    families = list(report["families"])
    models = list(report["models"])
    comparisons = report.get("comparisons") or {}
    sampling = report.get("sampling_plan") or {"queue": []}

    def h(value: Any) -> str:
        return escape(str(value if value is not None else ""), quote=True)

    def n(value: Any) -> str:
        return f"{int(value or 0):,}"

    status_labels = {
        "user_evaluated": "已有用户裁决",
        "partially_user_evaluated": "部分有用户裁决",
        "work_observed_no_user_verdict": "有工作，缺用户评价",
        "execution_attempts_only_no_user_verdict": "仅执行尝试",
        "registry_only_no_attributable_work": "仅登记",
        "not_observed": "未观测",
        "user_reported_pass": "用户明确通过",
        "user_reported_issue": "用户指出问题",
        "user_reported_severe_issue": "用户指出严重问题",
        "mixed_user_feedback": "有通过，也有问题",
        "no_user_verdict": "没有用户裁决",
    }
    family_colors = {
        "grok": "#52d6c7", "gpt": "#6ca8ff", "glm": "#b28cff",
        "kimi": "#f0b55a", "deepseek": "#ff7f8d", "claude": "#ef8ccd",
        "qwen": "#53d18c", "gemini": "#76a7ff",
    }
    task_labels = {
        "backend": "后端", "frontend": "前端", "testing": "测试",
        "configuration": "配置", "documentation": "文档", "governance": "治理",
        "data": "数据", "general": "综合", "coding": "编码", "planning": "规划",
    }

    rated_models = [row for row in models if row["user_feedback"]["summary"]["total"]]
    top_work = max(
        models,
        key=lambda row: int(row["observed_work"]["event_counts"]["applied_writes"]),
        default=None,
    )
    unrated_work_families = [
        row["family"] for row in families
        if row["applied_writes"] and not row["user_feedback_rows"]
    ]
    attempt_only = [
        row["family"] for row in families
        if row["event_sessions"] and not row["applied_writes"]
    ]
    speed_models = [
        row["model"] for row in rated_models
        if any(item.get("dimension") == "speed" for item in row["user_feedback"]["rows"])
    ]
    report_rework_observed = any(
        any(token in str(item.get("note") or "") for token in ("可视化", "冗长", "结论"))
        for row in rated_models for item in row["user_feedback"]["rows"]
    )
    interaction_audit = coverage.get("interaction_signals") or {}
    observed_turns = int(interaction_audit.get("observed_user_turns") or 0)
    followup_turns = int(interaction_audit.get("followup_user_turns") or 0)
    correction_signals = int(interaction_audit.get("correction_signals") or 0)

    def task_result(task_type: str) -> str:
        task = next(
            (item for item in comparisons.get("models_by_task") or [] if item["task_type"] == task_type),
            None,
        )
        if not task:
            return "暂无"
        rows = [
            item for item in task["within_task_order"]
            if item.get("evidence_grade") in {"A", "B", "C"}
        ][:4]
        return "、".join(
            f"{h(item['model'])} {h(item['score'])}（{h(item.get('evidence_grade', 'C'))}级）"
            for item in rows
        ) or "暂无"

    top_name = h(top_work["model"]) if top_work else "无"
    top_writes = n(top_work["observed_work"]["event_counts"]["applied_writes"]) if top_work else "0"
    conclusions = [
        (
            "现在能评价谁",
            f"显式裁决覆盖 {len(rated_models)} 个模型："
            + "、".join(h(row["model"]) for row in rated_models)
            + f"；另外从 {observed_turns} 个真实用户回合中识别出 {correction_signals} 次纠错/返工/澄清。"
            + "这些自动信号只描述纠错负担，不参与质量排名。"
            + "其他模型仍不能因为‘没被批评’就算通过。",
            "primary",
        ),
        (
            "同任务横评已经有结果",
            f"前端：{task_result('frontend')}。文档：{task_result('documentation')}。"
            f"工作量最大仍是 {top_name}（{top_writes} 次写入），但不拿工作量冒充质量。",
            "work",
        ),
        (
            "已知质量问题",
            "GPT-5.6-Sol 同时有配置通过、额外导出严重问题和两次速度批评；Fable 有界面跳转/返回严重问题；Grok-4.6-Build 有页面错误与界面过度膨胀问题。"
            + ("本报告旧版也因缺少可视化、冗长且结论不清被用户退回。" if report_rework_observed else ""),
            "risk",
        ),
        (
            "横评现在能到哪一步",
            ("GLM、Kimi 有工作记录但没有用户裁决；" if unrated_work_families else "")
            + ("DeepSeek 只有执行尝试、没有生效写入；" if attempt_only else "")
            + f"当前有 {int(comparisons.get('provisional_model_tasks') or 0)} 类任务可做暂定同任务横评；"
            + "每个分数同时显示样本量、95% 区间和补样缺口。仍不做跨任务‘谁最好’或可靠速度排名。",
            "unknown",
        ),
    ]
    conclusion_html = "".join(
        f'<article class="finding {tone}"><span>{index:02d}</span><div><h3>{h(title)}</h3><p>{body}</p></div></article>'
        for index, (title, body, tone) in enumerate(conclusions, 1)
    )

    model_task_rows = list(comparisons.get("models_by_task") or [])
    family_task_rows = list(comparisons.get("families_by_task") or [])
    task_types = [
        row["task_type"] for row in model_task_rows
        if any(
            cell["user"]["samples"] or cell["work"]["applied_writes"]
            for cell in row["cells"]
        )
    ]
    model_cell_index = {
        (cell["model"], row["task_type"]): cell
        for row in model_task_rows for cell in row["cells"]
    }
    family_cell_index = {
        (cell["family"], row["task_type"]): cell
        for row in family_task_rows for cell in row["cells"]
    }

    def heat_cell(cell: Mapping[str, Any]) -> str:
        metric = cell["deep_evaluation"]
        score = metric["score"]
        writes = int(cell["work"]["applied_writes"])
        if score is None:
            if writes:
                return (
                    f'<td class="heat waiting" title="已有 {n(writes)} 次生效写入，缺用户评价">'
                    f'<b>待评</b><small>{n(writes)} 写入</small></td>'
                )
            return '<td class="heat empty"><b>—</b><small>无样本</small></td>'
        hue = max(0.0, min(120.0, float(score) * 1.2))
        heat_color = "#26344d" if metric["evidence_grade"] == "D" else f"hsl({hue:.0f} 48% 24%)"
        explicit = metric["explicit_user"]
        reactions = metric["implicit_user"]
        process = metric["process"]
        peer = metric["peer_auxiliary"]
        status = "可比" if metric["status"] == "comparable" else "暂定"
        sample_label = "仅过程" if metric["evidence_grade"] == "D" else f'U={metric["user_signals"]}'
        return (
            f'<td class="heat scored" style="--heat:{heat_color}" '
            f'title="显式用户 {explicit["samples"]}；纠错负担 {reactions["correction_signals"]}；'
            f'过程操作 {process["operations"]}；互评 {peer["samples"]}；{status}">'
            f'<b>{h(score)}</b><small>{sample_label} · {metric["evidence_grade"]}级 · {status}</small></td>'
        )

    heat_headers = "".join(f"<th>{h(task_labels.get(task, task))}</th>" for task in task_types)
    family_heat_rows = "".join(
        f'<tr><th><i style="--c:{family_colors.get(str(family["family"]), "#7f91b3")}"></i>{h(str(family["family"]).upper())}</th>'
        + "".join(heat_cell(family_cell_index[(family["family"], task)]) for task in task_types)
        + "</tr>"
        for family in families
    )
    matrix_models = [
        row for row in models
        if any(
            model_cell_index[(row["model"], task)]["user"]["samples"]
            or model_cell_index[(row["model"], task)]["work"]["applied_writes"]
            for task in task_types
        )
    ]
    model_heat_rows = "".join(
        f'<tr><th><strong>{h(row["model"])}</strong><small>{h(row["family"])}</small></th>'
        + "".join(heat_cell(model_cell_index[(row["model"], task)]) for task in task_types)
        + "</tr>"
        for row in matrix_models
    )

    comparison_panels: list[str] = []
    for task in model_task_rows:
        ordering = [
            item for item in task["within_task_order"]
            if item.get("evidence_grade") in {"A", "B", "C"}
        ]
        if len(ordering) < 2:
            continue
        bars = "".join(
            f"""<div class="compare-row"><strong>{h(item['model'])}</strong>
              <div class="score-track"><i style="width:{float(item['score']):.1f}%"></i></div>
              <b>{h(item['score'])}</b><small>用户信号 {item['samples']} · {h(item.get('evidence_grade', 'C'))}级证据 · {'可比' if item['status'] == 'comparable' else '暂定'}</small></div>"""
            for item in ordering
        )
        comparison_panels.append(f"""
          <article class="compare-panel"><header><h3>{h(task_labels.get(task['task_type'], task['task_type']))}</h3>
            <span>{'正式横评' if task['status'] == 'comparable' else '暂定横评 · 继续补样'}</span></header>{bars}</article>""")

    sampling_rows = "".join(
        f"""<tr><td><strong>{h(item['model'])}</strong><small>{h(item['family'])}</small></td>
        <td>{h(task_labels.get(item['task_type'], item['task_type']))}</td>
        <td class="num">{item['current_user_samples']}</td><td class="num">+{item['missing_user_samples']}</td>
        <td class="num">{n(item['applied_writes'])}</td><td>{h(item['next_action'])}</td></tr>"""
        for item in list(sampling.get("queue") or [])[:16]
    )
    reaction_labels = {
        "explicit_criticism": "明确批评", "explicit_approval": "明确认可",
        "rework_request": "返工要求", "clarification_cost": "澄清成本",
        "mixed_feedback": "混合反馈",
        "acceptance": "接受/确认", "continuation": "继续推进",
        "request_or_context": "需求/上下文（中性）",
    }
    reaction_cards = "".join(
        f'<div class="metric"><b>{n(count)}</b><span>{h(reaction_labels.get(kind, kind))}</span></div>'
        for kind, count in sorted((interaction_audit.get("by_signal_type") or {}).items())
    )
    reaction_model_rows = "".join(
        f"""<tr><td><strong>{h(row['model'])}</strong><small>{h(row['family'])}</small></td>
        <td class="num">{n(row['user_reactions']['observed_turns'])}</td>
        <td class="num">{n(row['user_reactions']['summary']['followup_turns'])}</td>
        <td class="num">{n(row['user_reactions']['summary']['correction_signals'])}</td>
        <td class="num">{h(row['user_reactions']['summary']['correction_rate_per_100_followups'])}</td>
        <td class="num">{n(row['user_reactions']['summary']['sessions'])}</td></tr>"""
        for row in sorted(
            (item for item in models if item["user_reactions"]["observed_turns"]),
            key=lambda item: -int(item["user_reactions"]["summary"]["correction_signals"]),
        )
    )
    process_hotspots = [
        (cell["model"], task["task_type"], cell["deep_evaluation"]["process"])
        for task in model_task_rows for cell in task["cells"]
        if cell["deep_evaluation"]["process"]["operations"]
    ]
    process_hotspots.sort(
        key=lambda item: (
            -int(item[2]["stray_findings"]),
            -float(item[2]["error_rate"] or 0),
            -int(item[2]["operations"]),
        )
    )
    process_rows = "".join(
        f"""<tr><td><strong>{h(model)}</strong></td><td>{h(task_labels.get(task, task))}</td>
        <td class="num">{n(metric['operations'])}</td><td class="num">{n(metric['errors'])}</td>
        <td class="num">{h(metric['error_rate'])}%</td><td class="num">{n(metric['stray_findings'])}</td>
        <td class="num">{n(metric['denials'])}</td><td class="num">{h(metric['score'])}</td></tr>"""
        for model, task, metric in process_hotspots[:24]
    )

    # Dual logarithmic bars keep small families visible next to high-volume GPT evidence.
    max_sessions = max((int(row["event_sessions"]) for row in families), default=1)
    max_writes = max((int(row["applied_writes"]) for row in families), default=1)
    svg_height = 76 + 58 * len(families)
    family_svg_rows: list[str] = []
    for index, row in enumerate(families):
        y = 62 + index * 58
        session_width = 230 * log1p(int(row["event_sessions"])) / max(log1p(max_sessions), 1)
        write_width = 230 * log1p(int(row["applied_writes"])) / max(log1p(max_writes), 1)
        color = family_colors.get(str(row["family"]), "#7f91b3")
        family_svg_rows.append(f"""
          <g class="bar-row">
            <text x="0" y="{y + 16}" class="family-label">{h(str(row['family']).upper())}</text>
            <rect x="92" y="{y}" width="230" height="12" rx="6" class="track"/>
            <rect x="92" y="{y}" width="{session_width:.1f}" height="12" rx="6" fill="{color}"/>
            <text x="330" y="{y + 11}" class="value">{n(row['event_sessions'])}</text>
            <rect x="426" y="{y}" width="230" height="12" rx="6" class="track"/>
            <rect x="426" y="{y}" width="{write_width:.1f}" height="12" rx="6" fill="{color}"/>
            <text x="664" y="{y + 11}" class="value">{n(row['applied_writes'])}</text>
          </g>""")
    family_svg = f"""
      <svg class="work-chart" viewBox="0 0 760 {svg_height}" role="img" aria-labelledby="work-title work-desc">
        <title id="work-title">六个模型家族的工作会话与生效写入</title>
        <desc id="work-desc">条形长度使用对数刻度，仅用于显示观测量级。</desc>
        <text x="92" y="30" class="axis-title">工作会话</text>
        <text x="426" y="30" class="axis-title">生效写入</text>
        {''.join(family_svg_rows)}
      </svg>"""

    evidence_rows: list[str] = []
    for row in families:
        has_work = bool(row["applied_writes"])
        has_user = bool(row["user_feedback_rows"])
        has_peer = bool(row["peer_review_rows"])
        quality = "部分可回答" if has_user else "不能回答"
        evidence_rows.append(f"""
          <tr><th><i style="--c:{family_colors.get(str(row['family']), '#7f91b3')}"></i>{h(str(row['family']).upper())}</th>
          <td class="signal {'yes' if has_work else 'no'}">{'● 有' if has_work else '○ 无'}</td>
          <td class="signal {'yes' if has_user else 'no'}">{'● ' + n(row['user_feedback_rows']) + ' 条' if has_user else '○ 缺失'}</td>
          <td class="signal {'yes' if has_peer else 'no'}">{'● ' + n(row['peer_review_rows']) + ' 条' if has_peer else '○ 缺失'}</td>
          <td><b class="answer {'yes' if has_user else 'no'}">{quality}</b></td></tr>""")

    verdict_cards: list[str] = []
    severity_label = {"pass": "通过", "imperfect": "有问题", "severe": "严重"}
    for row in rated_models:
        summary = row["user_feedback"]["summary"]
        total = max(1, int(summary["total"]))
        segments = "".join(
            f'<span class="{severity}" style="width:{100 * int(summary[severity]) / total:.2f}%" title="{severity_label[severity]} {summary[severity]}"></span>'
            for severity in ("pass", "imperfect", "severe") if summary[severity]
        )
        notes = "".join(
            f"""<li class="{h(item['severity'])}"><b>{h(item['task_type'])} · {h(item['dimension'])}</b>
            <span>{h(item['note'])}</span><code>{h(item['source_ref'])}</code></li>"""
            for item in row["user_feedback"]["rows"]
        )
        verdict_cards.append(f"""
          <article class="verdict-card">
            <header><div><small>{h(str(row['family']).upper())}</small><h3>{h(row['model'])}</h3></div>
              <b>{h(status_labels.get(row['user_feedback']['verdict'], row['user_feedback']['verdict']))}</b></header>
            <div class="stacked">{segments}</div>
            <div class="legend"><span>通过 {summary['pass']}</span><span>有问题 {summary['imperfect']}</span><span>严重 {summary['severe']}</span></div>
            <ul class="quotes">{notes}</ul>
          </article>""")

    task_colors = {
        "backend": "#6ca8ff", "frontend": "#52d6c7", "testing": "#b28cff",
        "documentation": "#f0b55a", "configuration": "#ef8ccd", "governance": "#ff7f8d",
        "data": "#7bd88f", "general": "#8291ad",
    }
    workload_models = sorted(
        models,
        key=lambda row: int(row["observed_work"]["event_counts"]["applied_writes"]),
        reverse=True,
    )[:8]
    workload_rows: list[str] = []
    for row in workload_models:
        writes = int(row["observed_work"]["event_counts"]["applied_writes"])
        segments = "".join(
            f'<span style="width:{100 * int(item["applied_writes"]) / max(writes, 1):.2f}%;background:{task_colors.get(str(item["task_type"]), "#8291ad")}" title="{h(item["task_type"])} {n(item["applied_writes"])}"></span>'
            for item in row["observed_work"]["task_writes"] if item["applied_writes"]
        )
        workload_rows.append(f"""
          <div class="work-row"><strong>{h(row['model'])}</strong><div class="task-stack">{segments}</div><b>{n(writes)}</b></div>""")
    task_legend = "".join(
        f'<span><i style="background:{color}"></i>{h(task)}</span>' for task, color in task_colors.items()
    )

    model_rows: list[str] = []
    for row in sorted(models, key=lambda item: (str(item["family"]), str(item["model"]))):
        events = row["observed_work"]["event_counts"]
        tasks = sorted(row["observed_work"]["task_writes"], key=lambda item: int(item["applied_writes"]), reverse=True)[:3]
        task_text = "、".join(f"{h(item['task_type'])} {n(item['applied_writes'])}" for item in tasks) or "无生效写入"
        verdict = row["user_feedback"]["verdict"]
        model_rows.append(f"""
          <tr><td><strong>{h(row['model'])}</strong><small>{h(row['family'])}</small></td>
          <td>{task_text}</td><td class="num">{n(row['coverage']['event_sessions'])}</td>
          <td class="num">{n(events['applied_writes'])}</td><td class="num">{n(events['errors'])}</td>
          <td>{h(status_labels.get(verdict, verdict))}</td></tr>""")

    references = "".join(
        f'<li><a href="{h(item["url"])}">{h(item["name"])}</a>：{h(item["adopted"])}</li>'
        for item in report["methodology_references"]
    )
    caveats = "".join(f"<li>{h(item)}</li>" for item in report["caveats"])
    speed_note = (
        "速度只有用户原话证据（" + "、".join(h(name) for name in speed_models) + "）；没有可分离 active-work 计时。"
        if speed_models else "没有可分离 active-work 计时，也没有可归因用户速度评价。"
    )

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LLM 深度工作表现横评</title>
<!-- [OMNI] material_id="material:plans.agent-framework.llm-hr-performance-attribution.p7-full-report.html" origin=codex ts=2026-08-18T00:00:00+08:00 type=report status=active agent=codex -->
<style>
:root{{--bg:#0a1020;--panel:#121b2f;--panel2:#17233a;--line:#2b3a57;--text:#eef3ff;--muted:#a8b5ce;--green:#64dda2;--amber:#f4bd61;--red:#ff7f8d;--blue:#6ca8ff}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 15% 0,#182b50 0,transparent 34%),var(--bg);color:var(--text);font:15px/1.55 Inter,"Segoe UI","Microsoft YaHei",sans-serif}}main{{max-width:1180px;margin:auto;padding:42px 24px 72px}}h1{{font-size:clamp(34px,6vw,68px);line-height:1.02;letter-spacing:-.05em;margin:10px 0 16px}}h2{{font-size:27px;margin:48px 0 18px}}h3,p{{margin-top:0}}a{{color:#79c6ff}}.kicker{{color:#76dff1;text-transform:uppercase;letter-spacing:.17em;font-size:12px;font-weight:800}}.lede{{max-width:850px;color:#c8d4eb;font-size:18px}}.scope{{display:flex;gap:8px;flex-wrap:wrap;margin:22px 0}}.scope span{{border:1px solid var(--line);border-radius:999px;padding:6px 11px;color:var(--muted)}}.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:26px 0}}.metric,.finding,.panel,.verdict-card,.compare-panel,details{{background:linear-gradient(145deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:16px}}.metric{{padding:17px}}.metric b{{display:block;font-size:30px}}.metric span,small{{color:var(--muted)}}.findings{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}.finding{{display:flex;gap:14px;padding:18px;border-left:4px solid var(--blue)}}.finding>span{{color:#71809e;font:700 22px/1 monospace}}.finding h3{{font-size:17px;margin-bottom:6px}}.finding p{{color:#cbd6eb;margin:0}}.finding.risk{{border-left-color:var(--red)}}.finding.unknown{{border-left-color:var(--amber)}}.finding.primary{{border-left-color:var(--green)}}.panel{{padding:20px}}.visual-grid{{display:grid;grid-template-columns:1.35fr 1fr;gap:14px}}figure{{margin:0}}figcaption{{color:var(--muted);font-size:12px;margin-top:8px}}.work-chart{{width:100%;height:auto;display:block}}.work-chart text{{fill:#dbe5f8;font-family:inherit}}.work-chart .axis-title{{fill:#93a4c3;font-size:13px;font-weight:700}}.work-chart .family-label{{font-size:13px;font-weight:800}}.work-chart .value{{fill:#a9b7d1;font-size:12px}}.track{{fill:#25334c}}table{{width:100%;border-collapse:collapse}}th,td{{padding:11px 10px;border-bottom:1px solid #293750;text-align:left;vertical-align:top}}th{{color:#c7d3e7}}td.num{{text-align:right;font-variant-numeric:tabular-nums}}.matrix th i,.heatmap th i{{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--c);margin-right:8px}}.signal.yes,.answer.yes{{color:var(--green)}}.signal.no,.answer.no{{color:var(--muted)}}.answer{{font-size:12px}}.quant-rule{{display:flex;gap:12px;align-items:center;justify-content:space-between;margin-bottom:14px;padding:12px 14px;border:1px solid #35517a;border-radius:12px;background:#10223d}}.quant-rule b{{color:#8fe3ff}}.heatmap-wrap{{overflow:auto}}.heatmap{{min-width:860px;font-size:12px}}.heatmap th:first-child{{position:sticky;left:0;background:#152039;z-index:1;min-width:145px}}.heatmap td{{text-align:center;min-width:82px;padding:8px 6px}}.heatmap td b,.heatmap td small{{display:block}}.heatmap td b{{font-size:16px}}.heat.scored{{background:var(--heat)}}.heat.waiting{{background:#3b321e;color:var(--amber)}}.heat.empty{{color:#667691;background:#11192b}}.comparisons{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}.compare-panel{{padding:16px}}.compare-panel header{{display:flex;justify-content:space-between;gap:10px}}.compare-panel header span{{color:var(--amber);font-size:12px}}.compare-row{{display:grid;grid-template-columns:150px 1fr 45px;gap:10px;align-items:center;margin:11px 0}}.compare-row small{{grid-column:2/4}}.score-track{{height:10px;border-radius:99px;background:#283650;overflow:hidden}}.score-track i{{display:block;height:100%;background:linear-gradient(90deg,var(--red),var(--amber),var(--green));border-radius:inherit}}.verdicts{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}.verdict-card{{padding:17px}}.verdict-card header{{display:flex;justify-content:space-between;gap:12px;align-items:start}}.verdict-card h3{{font-size:18px;margin:2px 0 14px}}.verdict-card header>b{{color:var(--amber);font-size:12px;text-align:right}}.stacked,.task-stack{{height:12px;display:flex;overflow:hidden;border-radius:99px;background:#283650}}.stacked .pass{{background:var(--green)}}.stacked .imperfect{{background:var(--amber)}}.stacked .severe{{background:var(--red)}}.legend,.task-legend{{display:flex;flex-wrap:wrap;gap:10px;color:var(--muted);font-size:11px;margin:8px 0 14px}}.quotes{{padding:0;margin:0;list-style:none}}.quotes li{{padding:10px 0;border-top:1px solid #28364f}}.quotes b,.quotes span,.quotes code{{display:block}}.quotes span{{margin:4px 0;color:#d5def0}}.quotes code{{font-size:10px;color:#7f91b3;overflow-wrap:anywhere}}.quotes li.severe b{{color:var(--red)}}.quotes li.imperfect b{{color:var(--amber)}}.quotes li.pass b{{color:var(--green)}}.work-row{{display:grid;grid-template-columns:180px 1fr 72px;gap:12px;align-items:center;margin:11px 0}}.work-row strong{{overflow:hidden;text-overflow:ellipsis}}.work-row>b{{text-align:right;font-variant-numeric:tabular-nums}}.task-stack{{height:16px}}.task-legend i{{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:4px}}details{{margin-top:18px;padding:0 18px}}summary{{cursor:pointer;padding:16px 0;font-weight:750}}details .table-wrap{{overflow:auto;padding-bottom:14px}}details table{{min-width:760px}}td small{{display:block}}.boundary{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}.boundary h3{{font-size:16px}}.boundary ul{{padding-left:19px}}footer{{margin-top:44px;padding-top:16px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}}@media(max-width:850px){{.visual-grid,.findings,.verdicts,.boundary,.comparisons{{grid-template-columns:1fr}}.metrics{{grid-template-columns:1fr 1fr}}.work-row{{grid-template-columns:130px 1fr 58px}}}}@media(max-width:520px){{main{{padding:28px 14px 60px}}.metrics{{grid-template-columns:1fr 1fr}}.metric b{{font-size:24px}}}}
</style><style>.metrics{{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}}</style></head><body><main>
<header><span class="kicker">OmniCompany · LLM HR · P11</span><h1>不只看几条评价，<br>看整段工作表现</h1>
<p class="lede">每个模型×任务格同时汇总：用户显式裁决、用户后续纠错负担、错误与成果过程、卫生问题和模型互评。标题分只使用用户明确裁决；自动识别的批评/返工/澄清只做诊断，“继续/好的/可以”、工具成功和模型互评都不进入质量分。没有评价，不算通过。</p>
<div class="scope"><span>Grok</span><span>GPT</span><span>GLM</span><span>Kimi</span><span>DeepSeek</span><span>Claude</span><span>只做同任务横评</span><span>不做跨任务总榜</span></div></header>
<section class="metrics"><div class="metric"><b>{n(coverage['registry_sessions'])}</b><span>会话登记</span></div><div class="metric"><b>{n(coverage['event_rows'])}</b><span>过程与成果事件</span></div><div class="metric"><b>{n(coverage['user_feedback_rows'])}</b><span>显式用户裁决</span></div><div class="metric"><b>{n(observed_turns)}</b><span>真实用户回合</span></div><div class="metric"><b>{n(followup_turns)}</b><span>有前序结果的回合</span></div><div class="metric"><b>{n(correction_signals)}</b><span>纠错负担信号</span></div><div class="metric"><b>{n(coverage['peer_review_rows'])}</b><span>模型互评（辅助）</span></div></section>
<h2>先看结论</h2><section class="findings">{conclusion_html}</section>
<h2>家族 × 任务：一眼横看</h2><section class="panel">
<div class="quant-rule"><span><b>用户质量分</b>　只使用显式用户裁决；自动纠错信号不参与排名</span><span>纠错负担、过程可靠性与模型互评分栏展示，不进入标题分</span></div>
<section class="metrics"><div class="metric"><b>{int(comparisons.get('comparable_model_tasks') or 0)}</b><span>模型正式可比任务</span></div><div class="metric"><b>{int(comparisons.get('provisional_model_tasks') or 0)}</b><span>模型暂定横评任务</span></div><div class="metric"><b>{int(comparisons.get('comparable_family_tasks') or 0)}</b><span>家族正式可比任务</span></div><div class="metric"><b>{int(comparisons.get('provisional_family_tasks') or 0)}</b><span>家族暂定横评任务</span></div><div class="metric"><b>{n(sampling.get('open_cells'))}</b><span>待补模型×任务格</span></div><div class="metric"><b>{n(sampling.get('missing_user_samples'))}</b><span>缺少显式用户裁决</span></div></section>
<div class="heatmap-wrap"><table class="heatmap"><thead><tr><th>模型家族</th>{heat_headers}</tr></thead><tbody>{family_heat_rows}</tbody></table></div>
</section>
<details open><summary>展开模型 × 任务评分矩阵（{len(matrix_models)} 个有工作或评价的模型）</summary><div class="heatmap-wrap"><table class="heatmap"><thead><tr><th>模型</th>{heat_headers}</tr></thead><tbody>{model_heat_rows}</tbody></table></div></details>
<h2>同任务横评</h2><section class="comparisons">{''.join(comparison_panels) if comparison_panels else '<article class="panel"><p>当前还没有两种模型在同一任务上同时获得用户评价；下方队列会把缺口转成补样任务。</p></article>'}</section>
<h2>把数据补到正式可比</h2><section class="panel"><div class="quant-rule"><span><b>自动补样队列</b>　优先补已有多模型评价的同类任务</span><span>目标：每个模型 × 任务至少 {int(sampling.get('target_user_samples_per_model_task') or 3)} 条用户裁决</span></div>
<div class="heatmap-wrap"><table><thead><tr><th>模型</th><th>任务</th><th>当前 n</th><th>还缺</th><th>已有写入</th><th>下一步</th></tr></thead><tbody>{sampling_rows or '<tr><td colspan="6">当前没有待补样格。</td></tr>'}</tbody></table></div></section>
<h2>证据覆盖到哪里</h2><section class="visual-grid"><figure class="panel">{family_svg}<figcaption>工作会话与写入使用对数刻度，避免低量级家族被压成零；它们描述工作量，不是质量分。</figcaption></figure>
<div class="panel"><table class="matrix"><thead><tr><th>家族</th><th>工作</th><th>用户评价</th><th>互评</th><th>质量问题</th></tr></thead><tbody>{''.join(evidence_rows)}</tbody></table></div></section>
<h2>用户后续纠错负担</h2><section class="panel"><p>真实用户回合全部保留；自动分类仅用于统计模型交付后引发了多少批评、返工、澄清或混合反馈。它可能受任务难度、用户表达方式和会话切分影响，因此不再换算为质量分，也不参与横评排名。</p><div class="metrics">{reaction_cards}</div></section>
<details open><summary>纠错负担按模型归因（诊断，不是质量排名）</summary><div class="table-wrap"><table><thead><tr><th>模型</th><th>真实回合</th><th>有前序结果</th><th>纠错/返工/澄清</th><th>每百次后续纠错</th><th>会话数</th></tr></thead><tbody>{reaction_model_rows or '<tr><td colspan="6">当前用户回合尚未可靠归因到具体模型。</td></tr>'}</tbody></table></div></details>
<h2>犯错、恢复与垃圾制造</h2><section class="panel"><p>这里单列过程可靠性，不用“命令执行成功”冒充成果质量。排序优先暴露游离垃圾，其次是错误率；拒绝数单列，不直接算模型缺陷。</p><div class="heatmap-wrap"><table><thead><tr><th>模型</th><th>任务</th><th>操作</th><th>错误</th><th>错误率</th><th>垃圾</th><th>拒绝</th><th>过程可靠性</th></tr></thead><tbody>{process_rows}</tbody></table></div></section>
<h2>用户真实评价</h2><section class="verdicts">{''.join(verdict_cards)}</section>
<h2>做了什么：任务构成</h2><figure class="panel"><div class="task-legend">{task_legend}</div>{''.join(workload_rows)}<figcaption>按生效写入量展示前 8 个模型，并显示其任务构成。排序仅为工作量视图，不代表能力或质量排名。</figcaption></figure>
<details><summary>展开全部 {len(models)} 个模型的证据表</summary><div class="table-wrap"><table><thead><tr><th>模型</th><th>主要任务</th><th>工作会话</th><th>生效写入</th><th>错误信号</th><th>用户裁决</th></tr></thead><tbody>{''.join(model_rows)}</tbody></table></div></details>
<h2>下一步怎么让比较真正成立</h2><section class="panel boundary"><div><h3>优先补证据</h3><ul><li>按“模型 × 同类任务”收集至少 3 条用户裁决。</li><li>优先复核 GPT 配置导出、Fable 导航返回、Grok 页面/密度问题。</li><li>给 GLM、Kimi 安排可验收任务；DeepSeek 先查清为何没有生效写入。</li><li>{speed_note}</li></ul></div><div><h3>方法与边界</h3><ul>{caveats}</ul><details><summary>参考的成熟评测方法</summary><ul>{references}</ul></details></div></section>
<footer>生成时间：{h(report['generated_at'])} · 机器可复算真源见同批 JSON · 无全局质量分、无跨任务排名。</footer>
</main></body></html>"""


__all__ = [
    "DEFAULT_FAMILIES",
    "EVALUATION_SCHEMA",
    "METHODOLOGY_REFERENCES",
    "TASK_TYPES",
    "build_model_aliases",
    "build_model_evaluation",
    "infer_model_family",
    "infer_task_type",
    "render_model_evaluation_html",
    "render_model_evaluation_markdown",
]
