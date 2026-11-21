"""Low-overhead, bounded diagnostics for the Dashboard ASGI process."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
import traceback
from collections import deque
from typing import Any

from fastapi import APIRouter

backend_diagnostics_router = APIRouter(
    prefix="/api/diagnostics/backend",
    tags=["backend-diagnostics"],
)


class BackendLatencyMonitor:
    """Passive loop/executor/request attribution with constant memory."""

    def __init__(
        self,
        *,
        interval_s: float = 5.0,
        recent_limit: int = 100,
        blocking_threshold_s: float = 1.0,
        watchdog_interval_s: float = 0.5,
    ) -> None:
        self.interval_s = max(1.0, float(interval_s))
        self.blocking_threshold_s = max(0.5, float(blocking_threshold_s))
        self.watchdog_interval_s = max(0.25, float(watchdog_interval_s))
        self.started_at = time.time()
        self.inflight = 0
        self.max_inflight = 0
        self.last_loop_lag_ms = 0.0
        self.last_executor_queue_depth = 0
        self.loop_lag_ms: deque[float] = deque(maxlen=720)
        self.executor_queue_depth: deque[int] = deque(maxlen=720)
        self.recent_slow: deque[dict[str, Any]] = deque(maxlen=max(10, recent_limit))
        self.blocking_stacks: deque[dict[str, Any]] = deque(maxlen=20)
        self._executor: Any = None
        self._task: asyncio.Task[None] | None = None
        self._request_seq = 0
        self._loop_thread_id = 0
        self._heartbeat_perf = time.perf_counter()
        self._last_captured_heartbeat = 0.0
        self._watchdog_stop = threading.Event()
        self._watchdog_thread: threading.Thread | None = None

    @staticmethod
    def _queue_depth(executor: Any) -> int:
        try:
            return max(0, int(executor._work_queue.qsize()))  # noqa: SLF001
        except Exception:
            return 0

    def begin_request(self) -> dict[str, Any]:
        self._request_seq += 1
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        return {
            "request_id": self._request_seq,
            "started_perf": time.perf_counter(),
            "inflight_start": self.inflight,
            "executor_queue_start": self._queue_depth(self._executor),
            "loop_lag_start_ms": self.last_loop_lag_ms,
        }

    def finish_request(
        self,
        token: dict[str, Any],
        *,
        status: int,
        method: str,
        path: str,
        handler: str,
        instance: str = "",
        session: str = "",
        slow_seconds: float = 2.0,
        designed_wait: bool = False,
    ) -> dict[str, Any]:
        duration_ms = max(
            0.0,
            (time.perf_counter() - float(token["started_perf"])) * 1000.0,
        )
        self.inflight = max(0, self.inflight - 1)
        record = {
            "request_id": token["request_id"],
            "occurred_at": time.time(),
            "duration_ms": round(duration_ms, 3),
            "status": int(status),
            "method": method,
            "path": path,
            "handler": handler or "unresolved",
            "instance": instance[:64],
            "session": session[:64],
            "inflight_start": token["inflight_start"],
            "inflight_end": self.inflight,
            "executor_queue_start": token["executor_queue_start"],
            "executor_queue_end": self._queue_depth(self._executor),
            "loop_lag_start_ms": round(float(token["loop_lag_start_ms"]), 3),
            "loop_lag_end_ms": round(self.last_loop_lag_ms, 3),
            "designed_wait": designed_wait,
        }
        if status >= 500 or (
            not designed_wait and duration_ms >= slow_seconds * 1000.0
        ):
            self.recent_slow.append(record)
        return record

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        self._loop_thread_id = threading.get_ident()
        self._heartbeat_perf = time.perf_counter()
        target = loop.time() + self.watchdog_interval_s
        next_aggregate = loop.time() + self.interval_s
        while True:
            await asyncio.sleep(max(0.0, target - loop.time()))
            observed = loop.time()
            self.last_loop_lag_ms = max(0.0, (observed - target) * 1000.0)
            self._heartbeat_perf = time.perf_counter()
            if observed >= next_aggregate:
                self.last_executor_queue_depth = self._queue_depth(self._executor)
                self.loop_lag_ms.append(self.last_loop_lag_ms)
                self.executor_queue_depth.append(self.last_executor_queue_depth)
                next_aggregate = observed + self.interval_s
            target = observed + self.watchdog_interval_s

    def _watchdog_sample(self) -> bool:
        heartbeat = self._heartbeat_perf
        stale_s = time.perf_counter() - heartbeat
        if (
            not self._loop_thread_id
            or stale_s < self.blocking_threshold_s
            or heartbeat == self._last_captured_heartbeat
        ):
            return False
        frame = sys._current_frames().get(self._loop_thread_id)
        if frame is None:
            return False
        extracted = traceback.extract_stack(frame, limit=40)
        self.blocking_stacks.append({
            "captured_at": time.time(),
            "stale_ms": round(stale_s * 1000.0, 3),
            "stack": [
                {
                    "file": item.filename,
                    "line": item.lineno,
                    "function": item.name,
                    "code": item.line or "",
                }
                for item in extracted[-24:]
            ],
        })
        self._last_captured_heartbeat = heartbeat
        return True

    def _watchdog_run(self) -> None:
        while not self._watchdog_stop.wait(self.watchdog_interval_s):
            self._watchdog_sample()

    def start(self, executor: Any) -> asyncio.Task[None]:
        self._executor = executor
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="dashboard-latency-monitor")
        if self._watchdog_thread is None or not self._watchdog_thread.is_alive():
            self._watchdog_stop.clear()
            self._watchdog_thread = threading.Thread(
                target=self._watchdog_run,
                name="dashboard-loop-watchdog",
                daemon=True,
            )
            self._watchdog_thread.start()
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
        self._watchdog_stop.set()
        thread = self._watchdog_thread
        if thread is not None:
            thread.join(timeout=self.watchdog_interval_s + 0.25)
        self._watchdog_thread = None

    def snapshot(self) -> dict[str, Any]:
        loop_values = sorted(self.loop_lag_ms)
        queue_values = sorted(self.executor_queue_depth)

        def p95(values: list[float] | list[int]) -> float | int | None:
            if not values:
                return None
            return values[max(0, int(len(values) * 0.95) - 1)]

        return {
            "schema": "omni.dashboard-latency.v1",
            "sampled_at": time.time(),
            "started_at": self.started_at,
            "policy": {
                "mode": "passive",
                "active_probes": False,
                "interval_s": self.interval_s,
                "watchdog_interval_s": self.watchdog_interval_s,
                "blocking_threshold_s": self.blocking_threshold_s,
                "retention": "bounded_process_memory",
                "sample_limit": self.loop_lag_ms.maxlen,
                "slow_request_limit": self.recent_slow.maxlen,
                "blocking_stack_limit": self.blocking_stacks.maxlen,
                "filesystem_writes": False,
                "process_scans": False,
            },
            "current": {
                "inflight": self.inflight,
                "max_inflight": self.max_inflight,
                "event_loop_lag_ms": round(self.last_loop_lag_ms, 3),
                "executor_queue_depth": self.last_executor_queue_depth,
            },
            "aggregate": {
                "event_loop_lag_p95_ms": p95(loop_values),
                "event_loop_lag_max_ms": max(loop_values) if loop_values else None,
                "executor_queue_depth_p95": p95(queue_values),
                "executor_queue_depth_max": max(queue_values) if queue_values else None,
            },
            "recent_slow": list(self.recent_slow),
            "blocking_stacks": list(self.blocking_stacks),
        }


_MONITOR = BackendLatencyMonitor()


def get_backend_latency_monitor() -> BackendLatencyMonitor:
    return _MONITOR


@backend_diagnostics_router.get("")
async def backend_diagnostics() -> dict[str, Any]:
    return _MONITOR.snapshot()
