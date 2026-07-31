# [OMNI] origin=codex domain=services/trace_induction ts=2026-07-18 type=worker
# [OMNI] material_id="material:learning.trace_induction.trace_db_reader.worker.py"
"""Deterministic reader for internal and external Agent traces."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


def _as_bool(value: Any, *, default: bool) -> bool:
    """Parse CLI string booleans without treating ``"false"`` as true."""

    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


class TraceReaderWorker(Worker):
    """Read selected traces without passing raw provider transcripts downstream."""

    FORMAT_IN = "ti.task"
    FORMAT_OUT = "ti.trace-data"
    DESCRIPTION = (
        "确定性读取 trace_ids。兼容 intent_steps，并可从外部 Agent 增量索引读取 "
        "Codex、Claude Code、Kimi 的去敏工具步骤。"
    )

    def run(self, input_data: Any) -> Verdict:
        purpose = input_data.get("purpose", "")
        trace_ids = input_data.get("trace_ids", [])
        if isinstance(trace_ids, str):
            trace_ids = [item.strip() for item in trace_ids.split(",") if item.strip()]
        db_path = input_data.get("db_path", "data/intent_traces.db")
        domain = input_data.get("domain", "")
        source = str(input_data.get("source") or "auto").strip().lower()
        provider = str(input_data.get("provider") or "").strip()
        sync_external = _as_bool(input_data.get("sync"), default=True)

        if source not in {"auto", "intent", "external"}:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis="source 必须是 auto、intent 或 external",
            )
        if not purpose or not trace_ids:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis="purpose 和 trace_ids 不能为空",
            )

        traces_raw: dict[str, list[Any]] = {trace_id: [] for trace_id in trace_ids}
        warnings: list[str] = []
        if source in {"auto", "intent"}:
            from omnicompany.packages.services._learning.trace_induction.sop_extractor import (
                read_trace_steps,
            )

            try:
                intent_traces = read_trace_steps(db_path, trace_ids)
            except Exception as exc:
                if source == "intent":
                    raise
                intent_traces = {}
                warnings.append(f"intent trace 读取失败: {type(exc).__name__}")
            for trace_id, steps in intent_traces.items():
                if steps:
                    traces_raw[trace_id] = steps

        missing = [trace_id for trace_id, steps in traces_raw.items() if not steps]
        if source == "external":
            missing = list(trace_ids)
        external_index_path = str(
            input_data.get("external_index_path")
            or "data/services/trace_induction/external_trace_index.db"
        )
        external_stats = None
        if source in {"auto", "external"} and missing:
            from omnicompany.packages.services._learning.trace_induction.external_sources import (
                ExternalSourceRoots,
                sync_selected_external_traces,
            )
            from omnicompany.packages.services._learning.trace_induction.external_trace_index import (
                ExternalTraceIndex,
            )

            index = ExternalTraceIndex(external_index_path)
            if sync_external:
                roots_data = input_data.get("external_roots") or {}
                roots = ExternalSourceRoots(
                    codex_home=(
                        Path(roots_data["codex_home"])
                        if roots_data.get("codex_home") else Path.home() / ".codex"
                    ),
                    claude_home=(
                        Path(roots_data["claude_home"])
                        if roots_data.get("claude_home") else Path.home() / ".claude"
                    ),
                    kimi_home=(
                        Path(roots_data["kimi_home"])
                        if roots_data.get("kimi_home") else Path.home() / ".kimi-code"
                    ),
                )
                external_stats = sync_selected_external_traces(
                    index,
                    missing,
                    provider=provider or None,
                    omni_event_db_paths=input_data.get("omni_event_db_paths"),
                    roots=roots,
                )
                warnings.extend(external_stats.warnings)
            external_traces = index.read_trace_steps(missing, provider=provider or None)
            for trace_id, steps in external_traces.items():
                if steps:
                    traces_raw[trace_id] = steps

        traces = {
            trace_id: [asdict(step) for step in steps]
            for trace_id, steps in traces_raw.items()
            if steps
        }
        if not traces:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={**input_data, "warnings": warnings},
                diagnosis=f"未找到 trace 数据: {trace_ids}",
            )

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "traces": traces,
                "purpose": purpose,
                "trace_count": len(traces),
                "domain": domain,
                "db_path": db_path,
                "source": source,
                "provider": provider,
                "external_index_path": external_index_path,
                "external_sync": (
                    {
                        "sources_checked": external_stats.sources_checked,
                        "bytes_read": external_stats.bytes_read,
                        "events_seen": external_stats.events_seen,
                        "calls_inserted": external_stats.calls_inserted,
                        "results_applied": external_stats.results_applied,
                    }
                    if external_stats is not None else None
                ),
                "warnings": warnings,
            },
            diagnosis=(
                f"读取到 {len(traces)} 个 trace，共 "
                f"{sum(len(value) for value in traces.values())} 步"
            ),
            confidence=1.0,
        )
