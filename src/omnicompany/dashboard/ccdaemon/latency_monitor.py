"""Low-overhead, passive latency observation for ccdaemon session traffic.

The monitor never injects terminal input, scans processes, or writes individual
samples to disk. It only times work the daemon already performs and keeps
fixed-size histograms plus a bounded list of slow observations in memory.
Periodically (default every 60s) it appends one aggregate histogram snapshot
to a rotating JSONL history file; this is a bounded, best-effort dump of the
in-memory aggregates, not per-sample logging, and any dump failure is
swallowed (fail-open) so it never affects monitoring or ccdaemon itself.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from bisect import bisect_left
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root

logger = logging.getLogger(__name__)


SCHEMA = "omni.latency-monitor.v1"
_BUCKET_LIMITS_MS = (
    1,
    2,
    4,
    8,
    16,
    32,
    64,
    128,
    256,
    512,
    1_000,
    2_000,
    5_000,
    10_000,
    30_000,
)
_BUCKET_LIMITS_NS = tuple(value * 1_000_000 for value in _BUCKET_LIMITS_MS)
_METRIC_NAMES = (
    "event_loop_lag",
    "pty_host_reconnect",
    "pty_input_write",
    "pty_input_to_first_output",
    "websocket_output_send",
)
# Value-domain buckets for non-time histograms (PERF-OBSERVATORY P1.5b). These
# never touch _BUCKET_LIMITS_NS/_MS above — bytes and queue depth aren't ms.
_BYTES_BUCKET_LIMITS = (
    1_024,
    4_096,
    16_384,
    65_536,
    262_144,
    1_048_576,
    4_194_304,
    16_777_216,
    67_108_864,
)
_QUEUE_DEPTH_BUCKET_LIMITS = (1, 2, 4, 8, 16, 32, 64, 128, 256, 512)
_DUMP_EVERY_TICKS = 12  # loop_interval_s default 5s * 12 = 60s
_HISTORY_MAX_BYTES = int(
    os.environ.get("OMNI_LATENCY_MONITOR_HISTORY_MAX_BYTES") or 20 * 1024 * 1024
)


def _env_float(name: str, default: float, *, minimum: float) -> float:
    try:
        value = float(os.environ.get(name) or default)
    except ValueError:
        value = default
    return max(minimum, value)


def _env_int(name: str, default: int, *, minimum: int) -> int:
    try:
        value = int(os.environ.get(name) or default)
    except ValueError:
        value = default
    return max(minimum, value)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _append_jsonl(path: Path, payload: Any, max_bytes: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size >= max_bytes:
        archive = path.with_suffix(path.suffix + ".1")
        os.replace(path, archive)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


class _Histogram:
    __slots__ = ("buckets", "count", "max_ns", "total_ns")

    def __init__(self) -> None:
        self.buckets = [0] * (len(_BUCKET_LIMITS_NS) + 1)
        self.count = 0
        self.total_ns = 0
        self.max_ns = 0

    def observe(self, duration_ns: int) -> None:
        duration_ns = max(0, int(duration_ns))
        self.count += 1
        self.total_ns += duration_ns
        if duration_ns > self.max_ns:
            self.max_ns = duration_ns
        self.buckets[bisect_left(_BUCKET_LIMITS_NS, duration_ns)] += 1

    def _quantile_upper_ms(self, quantile: float) -> float | None:
        if not self.count:
            return None
        target = max(1, math.ceil(self.count * quantile))
        seen = 0
        for index, count in enumerate(self.buckets):
            seen += count
            if seen >= target:
                if index < len(_BUCKET_LIMITS_MS):
                    return float(_BUCKET_LIMITS_MS[index])
                return round(self.max_ns / 1_000_000, 3)
        return round(self.max_ns / 1_000_000, 3)

    def snapshot(self) -> dict[str, Any]:
        if not self.count:
            return {
                "count": 0,
                "avg_ms": None,
                "max_ms": None,
                "p50_upper_ms": None,
                "p95_upper_ms": None,
                "p99_upper_ms": None,
            }
        return {
            "count": self.count,
            "avg_ms": round(self.total_ns / self.count / 1_000_000, 3),
            "max_ms": round(self.max_ns / 1_000_000, 3),
            "p50_upper_ms": self._quantile_upper_ms(0.50),
            "p95_upper_ms": self._quantile_upper_ms(0.95),
            "p99_upper_ms": self._quantile_upper_ms(0.99),
        }


class _ValueHistogram:
    """Bounded histogram over a raw value domain (bytes, queue depth, ...).

    Same fixed-bucket/no-percentile-interpolation design as ``_Histogram``,
    but the bucket limits and observed values are the metric's own unit —
    there is no ns/ms conversion, unlike time-based metrics.
    """

    __slots__ = ("buckets", "count", "max_value", "total", "_limits")

    def __init__(self, limits: tuple[int, ...]) -> None:
        self._limits = limits
        self.buckets = [0] * (len(limits) + 1)
        self.count = 0
        self.total = 0.0
        self.max_value = 0.0

    def observe(self, value: float) -> None:
        value = max(0.0, float(value))
        self.count += 1
        self.total += value
        if value > self.max_value:
            self.max_value = value
        self.buckets[bisect_left(self._limits, value)] += 1

    def _quantile_upper(self, quantile: float) -> float | None:
        if not self.count:
            return None
        target = max(1, math.ceil(self.count * quantile))
        seen = 0
        for index, count in enumerate(self.buckets):
            seen += count
            if seen >= target:
                if index < len(self._limits):
                    return float(self._limits[index])
                return round(self.max_value, 3)
        return round(self.max_value, 3)

    def snapshot(self) -> dict[str, Any]:
        if not self.count:
            return {
                "count": 0,
                "avg": None,
                "max": None,
                "p50_upper": None,
                "p95_upper": None,
                "p99_upper": None,
            }
        return {
            "count": self.count,
            "avg": round(self.total / self.count, 3),
            "max": round(self.max_value, 3),
            "p50_upper": self._quantile_upper(0.50),
            "p95_upper": self._quantile_upper(0.95),
            "p99_upper": self._quantile_upper(0.99),
        }


class _Counter:
    """A monotonic count with no value/duration attached (e.g. "kicked N times")."""

    __slots__ = ("count",)

    def __init__(self) -> None:
        self.count = 0

    def observe(self, amount: int = 1) -> None:
        self.count += int(amount)

    def snapshot(self) -> dict[str, Any]:
        return {"count": self.count}


@dataclass
class _SessionMetrics:
    metrics: dict[str, _Histogram] = field(
        default_factory=lambda: {
            name: _Histogram() for name in _METRIC_NAMES if name != "event_loop_lag"
        }
    )
    pending_input_ns: int = 0
    active: bool = True


class LatencyMonitor:
    """Constant-memory passive latency monitor owned by the ccdaemon loop."""

    def __init__(
        self,
        *,
        loop_interval_s: float | None = None,
        max_sessions: int | None = None,
        incident_limit: int | None = None,
        root: Path | None = None,
    ) -> None:
        self.loop_interval_s = (
            max(0.25, float(loop_interval_s))
            if loop_interval_s is not None
            else _env_float("OMNI_LATENCY_LOOP_INTERVAL_S", 5.0, minimum=0.25)
        )
        self.max_sessions = (
            max(1, int(max_sessions))
            if max_sessions is not None
            else _env_int("OMNI_LATENCY_MAX_SESSIONS", 64, minimum=1)
        )
        self.incident_limit = (
            max(1, int(incident_limit))
            if incident_limit is not None
            else _env_int("OMNI_LATENCY_INCIDENT_LIMIT", 50, minimum=1)
        )
        self._slow_threshold_ns = {
            "event_loop_lag": int(
                _env_float("OMNI_LATENCY_SLOW_LOOP_MS", 100.0, minimum=1.0)
                * 1_000_000
            ),
            "pty_host_reconnect": int(
                _env_float("OMNI_LATENCY_SLOW_HOST_RECONNECT_MS", 500.0, minimum=1.0)
                * 1_000_000
            ),
            "pty_input_write": int(
                _env_float("OMNI_LATENCY_SLOW_INPUT_WRITE_MS", 50.0, minimum=1.0)
                * 1_000_000
            ),
            "pty_input_to_first_output": int(
                _env_float("OMNI_LATENCY_SLOW_REACTION_MS", 250.0, minimum=1.0)
                * 1_000_000
            ),
            "websocket_output_send": int(
                _env_float("OMNI_LATENCY_SLOW_WS_SEND_MS", 50.0, minimum=1.0)
                * 1_000_000
            ),
        }
        self._aggregate: dict[str, Any] = {name: _Histogram() for name in _METRIC_NAMES}
        self._sessions: dict[str, _SessionMetrics] = {}
        self._incidents: deque[dict[str, Any]] = deque(maxlen=self.incident_limit)
        self._task: asyncio.Task[None] | None = None
        self._executor: Any = None
        self.started_at = time.time()

        # PERF-OBSERVATORY P1.5b: daemon-global counters/histograms with no
        # per-session breakdown (meta store I/O, subscriber kicks, snapshot
        # replay size, threadpool queue depth). Registered through the same
        # extensible path any future metric would use — see register_metric().
        self.register_metric(
            "meta_store_lock_wait",
            "histogram_ms",
            default_slow_threshold_ms=100.0,
            slow_threshold_env="OMNI_LATENCY_SLOW_META_LOCK_WAIT_MS",
        )
        self.register_metric(
            "meta_store_write",
            "histogram_ms",
            default_slow_threshold_ms=100.0,
            slow_threshold_env="OMNI_LATENCY_SLOW_META_WRITE_MS",
        )
        self.register_metric("subscriber_queue_kick", "counter")
        # PERF-OBSERVATORY P4.1: overflowed subscribers are now paused + inline
        # resynced instead of kicked (see pty.py PtyManager._publish_output /
        # _resync_if_overflowed_locked). This counter should carry the load
        # subscriber_queue_kick used to; that one should trend toward 0,
        # reserved only for a subscriber that never drains and is force-reset.
        self.register_metric("subscriber_inline_resync", "counter")
        self.register_metric(
            "snapshot_replay", "value_histogram", bucket_limits=_BYTES_BUCKET_LIMITS
        )
        self.register_metric("snapshot_replay_count", "counter")
        self.register_metric(
            "threadpool_queue_depth",
            "value_histogram",
            bucket_limits=_QUEUE_DEPTH_BUCKET_LIMITS,
        )

        self._dump_enabled = _env_bool("OMNI_LATENCY_MONITOR_DUMP", True)
        self._dump_every_ticks = _DUMP_EVERY_TICKS
        self._history_max_bytes = _HISTORY_MAX_BYTES
        dump_dir_override = os.environ.get("OMNI_LATENCY_MONITOR_DIR")
        if dump_dir_override:
            self._dump_dir = Path(dump_dir_override)
        else:
            self._dump_dir = (root or omni_workspace_root()) / "data" / "services" / "latency_monitor"

    def register_metric(
        self,
        name: str,
        kind: str,
        *,
        bucket_limits: tuple[int, ...] | None = None,
        default_slow_threshold_ms: float | None = None,
        slow_threshold_env: str | None = None,
    ) -> None:
        """Add an aggregate-only metric to ``self._aggregate`` (no per-session copy).

        ``kind`` is one of ``"histogram_ms"`` (time, reuses the ns-bucketed
        ``_Histogram`` used by the original 5 metrics and participates in the
        slow-incident feed), ``"value_histogram"`` (a raw-value domain like
        bytes or queue depth — needs ``bucket_limits``), or ``"counter"``
        (a plain monotonic count). Idempotent: re-registering an existing name
        is a no-op, so callers don't need to guard against double-init.
        """
        if name in self._aggregate:
            return
        if kind == "histogram_ms":
            self._aggregate[name] = _Histogram()
            if default_slow_threshold_ms is not None:
                threshold_ms = (
                    _env_float(slow_threshold_env, default_slow_threshold_ms, minimum=1.0)
                    if slow_threshold_env
                    else default_slow_threshold_ms
                )
                self._slow_threshold_ns[name] = int(threshold_ms * 1_000_000)
        elif kind == "value_histogram":
            self._aggregate[name] = _ValueHistogram(
                bucket_limits or _QUEUE_DEPTH_BUCKET_LIMITS
            )
        elif kind == "counter":
            self._aggregate[name] = _Counter()
        else:
            raise ValueError(f"unknown metric kind: {kind!r}")

    @property
    def history_path(self) -> Path:
        return self._dump_dir / "history.jsonl"

    def _history_snapshot(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "ts": time.time(),
            "metrics": {
                name: (
                    {**metric.snapshot(), "buckets": list(metric.buckets)}
                    if hasattr(metric, "buckets")
                    else metric.snapshot()
                )
                for name, metric in self._aggregate.items()
            },
        }

    def _dump_history(self) -> None:
        try:
            _append_jsonl(self.history_path, self._history_snapshot(), self._history_max_bytes)
        except Exception:  # noqa: BLE001 - dump is best-effort, never affects monitoring
            logger.warning("latency monitor history dump failed", exc_info=True)

    def _session(self, sid: str) -> _SessionMetrics:
        stats = self._sessions.get(sid)
        if stats is not None:
            return stats
        if len(self._sessions) >= self.max_sessions:
            # Dicts retain insertion order. Eviction is paid only when a brand
            # new session exceeds the cap, never on ordinary traffic.
            oldest_sid = next(iter(self._sessions))
            self._sessions.pop(oldest_sid, None)
        stats = _SessionMetrics()
        self._sessions[sid] = stats
        return stats

    def _observe(
        self,
        metric: str,
        duration_ns: int,
        *,
        sid: str | None = None,
        stats: _SessionMetrics | None = None,
    ) -> None:
        duration_ns = max(0, int(duration_ns))
        self._aggregate[metric].observe(duration_ns)
        if sid is not None:
            stats = stats or self._session(sid)
            stats.metrics[metric].observe(duration_ns)
            stats.active = True
        if duration_ns >= self._slow_threshold_ns[metric]:
            item: dict[str, Any] = {
                "occurred_at": time.time(),
                "metric": metric,
                "duration_ms": round(duration_ns / 1_000_000, 3),
                "threshold_ms": round(
                    self._slow_threshold_ns[metric] / 1_000_000,
                    3,
                ),
            }
            if sid is not None:
                item["session_id"] = sid
            self._incidents.append(item)

    def begin_pty_input(self, sid: str, *, track_reaction: bool) -> int:
        started_ns = time.perf_counter_ns()
        stats = self._session(sid)
        stats.active = True
        if track_reaction and not stats.pending_input_ns:
            stats.pending_input_ns = started_ns
        return started_ns

    def finish_pty_input(self, sid: str, started_ns: int) -> None:
        self._observe(
            "pty_input_write",
            time.perf_counter_ns() - started_ns,
            sid=sid,
        )

    def cancel_pty_input(self, sid: str, started_ns: int) -> None:
        stats = self._sessions.get(sid)
        if stats is not None and stats.pending_input_ns == started_ns:
            stats.pending_input_ns = 0

    def finish_pty_reaction(self, sid: str, started_ns: int) -> None:
        stats = self._sessions.get(sid)
        if stats is None:
            return
        if stats.pending_input_ns == started_ns:
            stats.pending_input_ns = 0
        self._observe(
            "pty_input_to_first_output",
            time.perf_counter_ns() - started_ns,
            sid=sid,
            stats=stats,
        )

    def observe_websocket_send(self, sid: str, duration_ns: int) -> None:
        self._observe("websocket_output_send", duration_ns, sid=sid)

    def observe_pty_host_reconnect(self, sid: str, duration_ns: int) -> None:
        self._observe("pty_host_reconnect", duration_ns, sid=sid)

    def end_session(self, sid: str) -> None:
        # Per-session detail is operational scratch data, not history. Drop it
        # as soon as the PTY ends; aggregate histograms and bounded incidents
        # retain enough signal for diagnosing daemon-wide stalls.
        self._sessions.pop(sid, None)

    def observe_meta_store_lock_wait(self, duration_ns: int) -> None:
        """Time a caller was blocked waiting for the cc_sessions.json cross-process lock.

        Daemon-global only (no per-session sid) — the guard is shared across every
        PTY, hook subprocess and CLI writer.
        """
        self._observe("meta_store_lock_wait", duration_ns)

    def observe_meta_store_write(self, duration_ns: int) -> None:
        """Time spent serializing + atomically replacing cc_sessions.json."""
        self._observe("meta_store_write", duration_ns)

    def record_subscriber_kick(self) -> None:
        """A slow WebSocket subscriber's queue overflowed and was force-reset.

        Named for SLO id ``subscriber-kick-1012``, which mirrors the historical
        WS close code 1012 (service restart) semantics used by the frontend's
        ``terminal_ws_reconnect`` telemetry for the same kind of event; this
        counter's own call site (``_disconnect_slow_subscriber``) resets the
        proxy queue in place rather than sending a literal close frame.
        """
        self._aggregate["subscriber_queue_kick"].observe()

    def record_subscriber_inline_resync(self) -> None:
        """A paused/overflowed subscriber drained to the low-water mark (or was
        explicitly revealed via visibility:true) and got one inline ring
        resync instead of a forced kick. See ``record_subscriber_kick``.
        """
        self._aggregate["subscriber_inline_resync"].observe()

    def observe_snapshot_replay(self, byte_count: int) -> None:
        """A PTY attach replayed its buffered ring to a newly attached client."""
        self._aggregate["snapshot_replay"].observe(max(0, int(byte_count)))
        self._aggregate["snapshot_replay_count"].observe()

    def attach_executor(self, executor: Any) -> None:
        """Explicitly pin the executor the 5s tick samples for queue depth.

        Optional: ccdaemon never needs to call this. ``_sample_threadpool_queue_depth``
        auto-discovers ``asyncio.get_running_loop()``'s own default executor —
        the same one ``configure_default_executor()`` installs at startup via
        ``loop.set_default_executor()`` — so no wiring in main.py is required.
        This setter exists for tests and for any future caller that wants to
        observe a *non-default* executor instead.
        """
        self._executor = executor

    def _sample_threadpool_queue_depth(
        self, loop: asyncio.AbstractEventLoop | None = None
    ) -> None:
        executor = self._executor
        if executor is None and loop is not None:
            try:
                # No public getter exists for a loop's default executor either
                # (the inverse of set_default_executor()); ``_default_executor``
                # is the same private-but-stable attribute asyncio's own
                # run_in_executor() reads. Fail open on any shape change.
                executor = loop._default_executor  # noqa: SLF001
            except Exception:
                executor = None
        if executor is None:
            return
        try:
            # ThreadPoolExecutor exposes no public queue-depth API either;
            # ``_work_queue`` is the documented-by-convention private attribute
            # every profiler (including stdlib's own tracing) reads for this.
            depth = executor._work_queue.qsize()  # noqa: SLF001
        except Exception:
            return
        self._aggregate["threadpool_queue_depth"].observe(max(0, int(depth)))

    def _observe_loop_lag(self, duration_ns: int) -> None:
        self._observe("event_loop_lag", duration_ns)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        target = loop.time() + self.loop_interval_s
        tick = 0
        while True:
            await asyncio.sleep(max(0.0, target - loop.time()))
            observed = loop.time()
            self._observe_loop_lag(int(max(0.0, observed - target) * 1_000_000_000))
            self._sample_threadpool_queue_depth(loop)
            target = observed + self.loop_interval_s
            tick += 1
            if self._dump_enabled and tick % self._dump_every_ticks == 0:
                self._dump_history()

    def start(self) -> asyncio.Task[None]:
        if self._task is not None and not self._task.done():
            return self._task
        self._task = asyncio.create_task(self.run(), name="cc-latency-monitor")
        return self._task

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._task = None

    def snapshot(self) -> dict[str, Any]:
        now_ns = time.perf_counter_ns()
        sessions: list[dict[str, Any]] = []
        for sid, stats in self._sessions.items():
            sessions.append(
                {
                    "id": sid,
                    "active": stats.active,
                    "pending_input_age_ms": (
                        round((now_ns - stats.pending_input_ns) / 1_000_000, 3)
                        if stats.pending_input_ns
                        else None
                    ),
                    "metrics": {
                        name: histogram.snapshot()
                        for name, histogram in stats.metrics.items()
                    },
                }
            )
        sessions.sort(
            key=lambda item: (
                bool(item["active"]),
                item["pending_input_age_ms"] or 0.0,
                sum(metric["count"] for metric in item["metrics"].values()),
            ),
            reverse=True,
        )
        return {
            "schema": SCHEMA,
            "running": bool(self._task is not None and not self._task.done()),
            "started_at": self.started_at,
            "sampled_at": time.time(),
            "policy": {
                "mode": "passive",
                "active_probes": False,
                "filesystem_writes": False,
                "process_scans": False,
                "retention": "bounded_process_memory",
                "loop_interval_s": self.loop_interval_s,
                "max_sessions": self.max_sessions,
                "incident_limit": self.incident_limit,
                "histogram_buckets_ms": list(_BUCKET_LIMITS_MS),
                "slow_thresholds_ms": {
                    name: round(value / 1_000_000, 3)
                    for name, value in self._slow_threshold_ns.items()
                },
            },
            "metric_definitions": {
                "event_loop_lag": "ccdaemon scheduler delay beyond the one-second heartbeat",
                "pty_host_reconnect": "detached PTY host reconnect including replay recovery",
                "pty_input_write": "accepted input to PTY host or in-process write completion",
                "pty_input_to_first_output": "accepted input to first PTY output bytes observed",
                "websocket_output_send": "output JSON encoding plus WebSocket send await time",
                "meta_store_lock_wait": "time blocked waiting for the cc_sessions.json cross-process lock",
                "meta_store_write": "cc_sessions.json serialize + atomic replace duration",
                "subscriber_queue_kick": "slow WebSocket subscriber queue reset (SLO subscriber-kick-1012)",
                "snapshot_replay": "bytes replayed from the PTY ring buffer on client attach",
                "snapshot_replay_count": "number of PTY attach snapshot replays",
                "threadpool_queue_depth": "sampled pending-work depth of the shared executor (every tick)",
            },
            "metrics": {
                name: histogram.snapshot()
                for name, histogram in self._aggregate.items()
            },
            "sessions": sessions,
            "recent_slow_sample_count": len(self._incidents),
        }

    def incidents(self, *, limit: int = 100) -> dict[str, Any]:
        count = max(1, min(int(limit), self.incident_limit))
        return {
            "schema": SCHEMA,
            "items": list(self._incidents)[-count:],
            "bounded_in_memory": True,
            "filesystem_writes": False,
        }


_MONITOR: LatencyMonitor | None = None


def get_latency_monitor() -> LatencyMonitor:
    global _MONITOR
    if _MONITOR is None:
        _MONITOR = LatencyMonitor()
    return _MONITOR


__all__ = ["LatencyMonitor", "get_latency_monitor"]
