"""The save read-back, staged and in time order.

``read_after`` is the layer that has decided the action rate since F4-05, and
F1-04 took it apart into flush, pull, decrypt, decode and cleanup.  Only a few
products ever recorded that breakdown -- the survey report, the two acceptance
probes and the live probes F1-04 wrote per window -- while every playing run
recorded the round-level ``read_after_s`` it added up to.  This module puts both
on one time line, so a change of profile or a code regression shows up against
the runs either side of it instead of only inside the report that measured it.

Nothing is measured here.  Every number is a median of samples the products
already wrote, and a stage a product never recorded stays ``null``.
"""

from __future__ import annotations

from typing import Any, Iterator

from .runs import percentile
from .sources import Inputs, Report, SaveReadProbe

TREND_SCHEMA = "omnicompany.game-observatory.rr-save-read-trend.v1"

STAGES = ("flush_s", "pull_ms", "decrypt_ms", "decode_ms", "cleanup_ms")
FLAGS = ("pull_adb_calls", "pull_fast_path", "derived_key_cached", "flush_adb_calls")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _timings(payload: Any, depth: int = 0) -> Iterator[dict[str, Any]]:
    """Every ``SaveReadTimingV1``-shaped dict anywhere in a payload.

    The shape is recognised by ``pull_ms`` rather than by the path it sits on:
    the survey nests it under a step's state, the acceptance probe under a read,
    and the live probe under a per-profile read detail.
    """

    if depth > 6:
        return
    if isinstance(payload, dict):
        if "pull_ms" in payload and "decrypt_ms" in payload:
            yield payload
            return
        for value in payload.values():
            yield from _timings(value, depth + 1)
    elif isinstance(payload, list):
        for value in payload:
            yield from _timings(value, depth + 1)


def _summarise(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Stage medians and the flags the samples agree or disagree on."""

    stages: dict[str, float | None] = {}
    for stage in STAGES:
        values = [
            value
            for sample in samples
            for value in [_number(sample.get(stage))]
            if value is not None
        ]
        stages[stage] = percentile(values, 0.5) if values else None
    totals = [
        value
        for sample in samples
        for value in [_number(sample.get("total_s"))]
        if value is not None
    ]
    flags: dict[str, Any] = {}
    for flag in FLAGS:
        seen = sorted(
            {
                sample[flag]
                for sample in samples
                if flag in sample and not isinstance(sample[flag], dict)
            },
            key=repr,
        )
        flags[flag] = seen[0] if len(seen) == 1 else (seen or None)
    profiles = sorted(
        {
            str(sample["flush_focus_command"])
            for sample in samples
            if sample.get("flush_focus_command")
        }
    )
    return {
        "samples": len(samples),
        "stages": stages,
        "total_s_p50": percentile(totals, 0.5) if totals else None,
        "flags": flags,
        "flush_focus_commands": profiles or None,
    }


def _report_rows(inputs: Inputs) -> list[dict[str, Any]]:
    rows = []
    for report in inputs.reports:
        samples = list(_timings(report.payload))
        if not samples:
            continue
        rows.append(
            {
                "kind": "report",
                "at": _captured_at(report),
                "source": report.name,
                "profile": None,
                **_summarise(samples),
            }
        )
    return rows


def _captured_at(report: Report) -> str | None:
    payload = report.payload
    loop = payload.get("loop")
    for value in (
        (loop or {}).get("started_at") if isinstance(loop, dict) else None,
        payload.get("captured_at"),
        payload.get("started_at"),
        payload.get("generated_at"),
    ):
        if isinstance(value, str) and value:
            return value.replace("+00:00", "Z")
    return None


def _probe_rows(probes: list[SaveReadProbe]) -> list[dict[str, Any]]:
    """One row per profile of each F1-04 live probe.

    A probe measures the same device twice, once per flush profile, so the two
    are never pooled: the whole point of the probe is the difference between
    them.
    """

    rows = []
    for probe in probes:
        payload = probe.payload
        at = payload.get("captured_at")
        profiles = payload.get("profiles")
        if isinstance(profiles, dict) and profiles:
            for name, profile in profiles.items():
                if not isinstance(profile, dict):
                    continue
                samples = list(_timings(profile))
                if not samples:
                    continue
                summary = _summarise(samples)
                declared = profile.get("medians")
                rows.append(
                    {
                        "kind": "live_probe",
                        "at": at if isinstance(at, str) else None,
                        "source": probe.relative,
                        "profile": str(name),
                        "declared_medians": declared
                        if isinstance(declared, dict)
                        else None,
                        **summary,
                    }
                )
            continue
        samples = list(_timings(payload))
        if samples:
            rows.append(
                {
                    "kind": "live_probe",
                    "at": at if isinstance(at, str) else None,
                    "source": probe.relative,
                    "profile": None,
                    "declared_medians": None,
                    **_summarise(samples),
                }
            )
    return rows


def _run_read_after(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The round-level ``read_after`` median of every run that recorded one."""

    trend = []
    for row in rows:
        layers = ((row.get("efficiency") or {}).get("layers_s") or {}) or {}
        read_after = layers.get("read_after") if isinstance(layers, dict) else None
        if not isinstance(read_after, dict) or read_after.get("p50") is None:
            continue
        identity = row.get("identity") or {}
        trend.append(
            {
                "at": identity.get("started_at"),
                "run_id": row["run_id"],
                "task": identity.get("task"),
                "drive_id": identity.get("drive_id"),
                "rounds": identity.get("rounds"),
                "read_after_p50_s": read_after.get("p50"),
                "read_after_p95_s": read_after.get("p95"),
            }
        )
    trend.sort(key=lambda item: (str(item["at"] or ""), item["run_id"]))
    return trend


def _baselines(inputs: Inputs) -> dict[str, Any]:
    """The two lines this trend is read against, taken from their own reports."""

    baseline = None
    baseline_source = None
    if inputs.multi_day is not None:
        comparison = (inputs.multi_day.payload.get("apm_retest") or {}).get("comparison")
        segment = (comparison or {}).get("f4_05_segment_c")
        if isinstance(segment, dict):
            baseline = _number(segment.get("read_after_s"))
            baseline_source = f"{inputs.multi_day.name} · apm_retest.comparison.f4_05_segment_c"
    target = None
    target_source = None
    if inputs.latency is not None:
        gate = (inputs.latency.payload.get("projection") or {}).get("gate")
        if isinstance(gate, dict):
            target = _number(gate.get("target_read_after_s"))
            target_source = f"{inputs.latency.name} · projection.gate.target_read_after_s"
    return {
        "f4_05_baseline_s": baseline,
        "f4_05_baseline_source": baseline_source,
        "f1_04_target_s": target,
        "f1_04_target_source": target_source,
        "missing": [
            name
            for name, value in (("f4_05_baseline_s", baseline), ("f1_04_target_s", target))
            if value is None
        ],
    }


def build_save_read_trend(
    rows: list[dict[str, Any]], inputs: Inputs, *, recent: int = 3
) -> dict[str, Any]:
    staged = _report_rows(inputs) + _probe_rows(inputs.save_read_probes)
    staged.sort(key=lambda item: (str(item["at"] or ""), item["source"], str(item["profile"])))
    trend = _run_read_after(rows)
    values = [item["read_after_p50_s"] for item in trend]
    recent_values = values[-recent:] if values else []
    baselines = _baselines(inputs)
    current = percentile(recent_values, 0.5) if recent_values else None
    return {
        "schema": TREND_SCHEMA,
        "staged": staged,
        "staged_sources": len(staged),
        "live_probe_rows": sum(1 for item in staged if item["kind"] == "live_probe"),
        "trend": trend,
        "runs": len(trend),
        "recent_window": recent,
        "read_after_median_all_s": percentile(values, 0.5) if values else None,
        "read_after_median_recent_s": current,
        **baselines,
        "recent_vs_baseline": (
            current - baselines["f4_05_baseline_s"]
            if current is not None and baselines["f4_05_baseline_s"] is not None
            else None
        ),
        "recent_meets_target": (
            current <= baselines["f1_04_target_s"]
            if current is not None and baselines["f1_04_target_s"] is not None
            else None
        ),
    }


__all__ = ["STAGES", "TREND_SCHEMA", "build_save_read_trend"]
