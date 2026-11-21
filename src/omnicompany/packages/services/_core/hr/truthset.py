# [OMNI] origin=codex ts=2026-08-18T00:00:00+08:00 type=module
# [OMNI] material_id=material:core.hr.truthset.py
# [OMNI] summary="HR transcript parser 的人工真值集评测：按原始证据区间逐 provider 计算 precision / recall"
# [OMNI] why="P1.5 要求用保留窗口内可人工核对的真实调用验收 parser，不能以合成单测或 collector 文件覆盖率代替事件准确率"
# [OMNI] tags=hr,truth-set,evaluation,attribution
"""Evaluate transcript-derived HR events against a manually labelled truth set.

The truth set stores source coordinates and expected normalized events, never raw
transcript content.  A cohort covers a continuous locator range in one source
file, so unexpected emitted events inside that range count as false positives and
missing labels count as false negatives.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from .claude_source import collect_claude_sessions, collect_qoder_sessions
from .codebuddy_source import collect_codebuddy_sessions
from .codex_source import collect_codex_sessions
from .kiro_source import collect_kiro_sessions
from .schema import PROVIDERS, HrEvent

TRUTHSET_SCHEMA = "omnicompany.hr-truth-set.v1"
DEFAULT_THRESHOLD = 0.95
DEFAULT_MINIMUM_EVENTS = 20

_COLLECTORS = {
    "claude": collect_claude_sessions,
    "qoder": collect_qoder_sessions,
    "codebuddy": collect_codebuddy_sessions,
    "kiro": collect_kiro_sessions,
    "codex": collect_codex_sessions,
}


class TruthSetError(ValueError):
    """The labelled truth-set manifest is invalid."""


def load_truth_set(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TruthSetError(f"cannot read truth set {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise TruthSetError("truth set root must be an object")
    return value


def _norm_path(value: str) -> str:
    return os.path.normcase(os.path.normpath(value))


def _split_evidence_ref(value: str) -> tuple[str, int] | None:
    source, marker, locator_text = value.rpartition("#L")
    if not marker or not source:
        return None
    try:
        return _norm_path(source), int(locator_text)
    except ValueError:
        return None


def _event_view(event: HrEvent) -> dict[str, Any]:
    parsed = _split_evidence_ref(event.evidence_ref)
    return {
        "event_id": event.event_id,
        "evidence_ref": event.evidence_ref,
        "locator": parsed[1] if parsed else None,
        "kind": event.kind,
        "tool": event.tool,
        "target": event.target,
        "verdict": event.verdict,
        "operation_id": event.operation_id,
    }


def _matches(expected: Mapping[str, Any], actual: HrEvent) -> bool:
    parsed = _split_evidence_ref(actual.evidence_ref)
    if parsed is None:
        return False
    optional_exact = ("kind", "tool", "verdict", "operation_id")
    if "locator" in expected and int(expected["locator"]) != parsed[1]:
        return False
    for key in optional_exact:
        if key in expected and str(expected[key]) != str(getattr(actual, key)):
            return False
    if "target" in expected and _norm_path(str(expected["target"])) != _norm_path(actual.target):
        return False
    if "target_suffix" in expected:
        suffix = _norm_path(str(expected["target_suffix"])).lstrip("\\/")
        if not _norm_path(actual.target).endswith(suffix):
            return False
    return True


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _validate_manifest(manifest: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    if manifest.get("schema") != TRUTHSET_SCHEMA:
        raise TruthSetError(f"schema must be {TRUTHSET_SCHEMA!r}")
    cohorts = manifest.get("cohorts")
    if not isinstance(cohorts, list):
        raise TruthSetError("cohorts must be a list")
    required = manifest.get("required_providers", list(PROVIDERS))
    if not isinstance(required, list) or not required:
        raise TruthSetError("required_providers must be a non-empty list")
    required_providers = [str(value) for value in required]
    unknown_required = sorted(set(required_providers) - set(PROVIDERS))
    if unknown_required:
        raise TruthSetError(f"unknown required providers: {unknown_required}")

    checked: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, raw in enumerate(cohorts):
        if not isinstance(raw, dict):
            raise TruthSetError(f"cohorts[{index}] must be an object")
        cohort = dict(raw)
        cohort_id = str(cohort.get("id", ""))
        provider = str(cohort.get("provider", ""))
        source_path = str(cohort.get("source_path", ""))
        expected = cohort.get("expected")
        if not cohort_id or cohort_id in ids:
            raise TruthSetError(f"cohorts[{index}].id must be unique and non-empty")
        ids.add(cohort_id)
        if provider not in PROVIDERS:
            raise TruthSetError(f"cohort {cohort_id}: unknown provider {provider!r}")
        if not source_path:
            raise TruthSetError(f"cohort {cohort_id}: source_path is required")
        try:
            start = int(cohort["locator_start"])
            end = int(cohort["locator_end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TruthSetError(f"cohort {cohort_id}: integer locator_start/end required") from exc
        if start < 1 or end < start:
            raise TruthSetError(f"cohort {cohort_id}: invalid locator range {start}..{end}")
        if not isinstance(expected, list) or any(not isinstance(row, dict) for row in expected):
            raise TruthSetError(f"cohort {cohort_id}: expected must be a list of objects")
        expanded_expected: list[dict[str, Any]] = []
        for row in expected:
            if "locators" not in row:
                expanded_expected.append(dict(row))
                continue
            locators = row.get("locators")
            if not isinstance(locators, list) or not locators:
                raise TruthSetError(f"cohort {cohort_id}: locators must be a non-empty list")
            common = {key: value for key, value in row.items() if key != "locators"}
            for locator in locators:
                try:
                    expanded_expected.append({**common, "locator": int(locator)})
                except (TypeError, ValueError) as exc:
                    raise TruthSetError(
                        f"cohort {cohort_id}: locators must contain integers"
                    ) from exc
        checked.append(
            {**cohort, "locator_start": start, "locator_end": end, "expected": expanded_expected}
        )
    return checked, required_providers


def _scratch_relative_path(provider: str, source_path: Path) -> Path:
    """Place one retained transcript under the layout its collector expects."""
    name = source_path.name
    lowered = [part.lower() for part in source_path.parts]
    if provider == "claude":
        if "subagents" in lowered:
            subagents_index = lowered.index("subagents")
            parent = source_path.parts[subagents_index - 1]
            return Path("projects") / "truthset" / parent / "subagents" / name
        return Path("projects") / "truthset" / name
    if provider == "qoder":
        if "segments" in lowered:
            return Path("logs") / "sessions" / "truthset" / "truthset" / "segments" / name
        if "subagents" in lowered:
            subagents_index = lowered.index("subagents")
            parent = source_path.parts[subagents_index - 1]
            return Path("projects") / "truthset" / parent / "subagents" / name
        return Path("projects") / "truthset" / name
    if provider == "codebuddy":
        return Path("projects") / "truthset" / "subagents" / name
    if provider == "kiro":
        return Path("sessions") / "cli" / name
    if provider == "codex":
        return Path("sessions") / "2000" / "01" / "01" / name
    raise TruthSetError(f"no scratch layout for provider {provider!r}")


def collect_truth_set_sources(manifest: Mapping[str, Any]) -> list[HrEvent]:
    """Freshly parse only source files named by the truth-set cohorts.

    Each retained transcript is copied into an isolated temporary installation
    root and parsed without watermarks.  Evidence refs are then mapped back to the
    original source.  This avoids both stale ledger rows and a full home-directory
    rescan, while never modifying provider session storage.
    """
    cohorts, _ = _validate_manifest(manifest)
    sources = sorted({(str(row["provider"]), str(row["source_path"])) for row in cohorts})
    collected: list[HrEvent] = []
    for provider, source_text in sources:
        source = Path(source_text)
        if not source.is_file():
            raise TruthSetError(f"truth-set source is missing: {source}")
        with tempfile.TemporaryDirectory(prefix=f"omni-hr-truthset-{provider}-") as tmp:
            root = Path(tmp)
            copied = root / _scratch_relative_path(provider, source)
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, copied)
            if provider in {"claude", "qoder"} and copied.parent.name == "subagents":
                parent = copied.parent.parent
                (parent.parent / f"{parent.name}.jsonl").touch()
            result = _COLLECTORS[provider](root, watermark_state={})
            if result.files_failed:
                detail = "; ".join(result.failures[:3]) or "unknown parse failure"
                raise TruthSetError(f"fresh parse failed for {source}: {detail}")
            copied_norm = _norm_path(str(copied.resolve()))
            for event in result.events:
                parsed = _split_evidence_ref(event.evidence_ref)
                if parsed is None or parsed[0] != copied_norm:
                    continue
                collected.append(
                    replace(event, evidence_ref=f"{source.resolve()}#L{parsed[1]}")
                )
    return collected


def evaluate_truth_set(
    manifest: Mapping[str, Any],
    events: Iterable[HrEvent],
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    """Return per-provider and per-cohort event precision/recall.

    Expected matchers may contain ``locator``, ``kind``, ``tool``, ``target``,
    ``target_suffix``, ``verdict`` and ``operation_id``.  Missing keys are
    intentionally wildcards, allowing labels to assert only fields visible in the
    retained raw call.  Every actual event is consumed at most once.
    """
    if not 0.0 <= threshold <= 1.0:
        raise TruthSetError("threshold must be between 0 and 1")
    cohorts, required_providers = _validate_manifest(manifest)
    minimum_events = manifest.get("minimum_events_per_provider", DEFAULT_MINIMUM_EVENTS)
    try:
        minimum_events = int(minimum_events)
    except (TypeError, ValueError) as exc:
        raise TruthSetError("minimum_events_per_provider must be an integer") from exc
    if minimum_events < 1:
        raise TruthSetError("minimum_events_per_provider must be >= 1")
    all_events = list(events)
    cohort_rows: list[dict[str, Any]] = []

    for cohort in cohorts:
        source = _norm_path(str(cohort["source_path"]))
        start = int(cohort["locator_start"])
        end = int(cohort["locator_end"])
        actual: list[HrEvent] = []
        for event in all_events:
            parsed = _split_evidence_ref(event.evidence_ref)
            if (
                event.provider == cohort["provider"]
                and parsed is not None
                and parsed[0] == source
                and start <= parsed[1] <= end
            ):
                actual.append(event)

        unmatched_actual = list(actual)
        false_negatives: list[dict[str, Any]] = []
        true_positives = 0
        for expected in cohort["expected"]:
            match_index = next(
                (index for index, candidate in enumerate(unmatched_actual) if _matches(expected, candidate)),
                None,
            )
            if match_index is None:
                false_negatives.append(dict(expected))
            else:
                true_positives += 1
                unmatched_actual.pop(match_index)
        false_positives = [_event_view(event) for event in unmatched_actual]
        predicted = true_positives + len(false_positives)
        labelled = true_positives + len(false_negatives)
        cohort_rows.append(
            {
                "id": cohort["id"],
                "provider": cohort["provider"],
                "source_path": cohort["source_path"],
                "locator_start": start,
                "locator_end": end,
                "true_positive": true_positives,
                "false_positive": len(false_positives),
                "false_negative": len(false_negatives),
                "precision": _ratio(true_positives, predicted),
                "recall": _ratio(true_positives, labelled),
                "false_positives": false_positives,
                "false_negatives": false_negatives,
            }
        )

    provider_rows: dict[str, dict[str, Any]] = {}
    for provider in required_providers:
        selected = [row for row in cohort_rows if row["provider"] == provider]
        tp = sum(row["true_positive"] for row in selected)
        fp = sum(row["false_positive"] for row in selected)
        fn = sum(row["false_negative"] for row in selected)
        precision = _ratio(tp, tp + fp)
        recall = _ratio(tp, tp + fn)
        passed = bool(selected) and (tp + fn) >= minimum_events and precision is not None and recall is not None
        passed = passed and precision >= threshold and recall >= threshold
        provider_rows[provider] = {
            "cohorts": len(selected),
            "labelled_events": tp + fn,
            "minimum_events": minimum_events,
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "precision": precision,
            "recall": recall,
            "passed": passed,
        }

    return {
        "schema": TRUTHSET_SCHEMA,
        "threshold": threshold,
        "minimum_events_per_provider": minimum_events,
        "passed": all(row["passed"] for row in provider_rows.values()),
        "providers": provider_rows,
        "cohorts": cohort_rows,
    }


__all__ = [
    "TRUTHSET_SCHEMA",
    "DEFAULT_THRESHOLD",
    "TruthSetError",
    "load_truth_set",
    "collect_truth_set_sources",
    "evaluate_truth_set",
]
